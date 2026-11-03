from __future__ import annotations

import asyncio
import sys
import uuid
from dataclasses import replace
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import agent as agent_module
import llm_client
import server
import work_record_cards
from paths import AgentPaths
from project_cli import bind_project_session
from project_mode import create_project
from server import WsBridge, WsSessionHandler
from session import Session, SessionMeta
from work_card_ledger import WorkCardLedger
from work_records import WorkRecordStore


class NoCallModel:
    def __init__(self, *, paths):
        self.paths = paths
        self.calls = []
        self.cancel_event = None

    def set_cancel_event(self, event):
        self.cancel_event = event

    def chat(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("work-card.list must not call a model")


def make_paths(root: Path) -> AgentPaths:
    root.mkdir(parents=True, exist_ok=True)
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    paths.workspace.mkdir(parents=True, exist_ok=True)
    return paths


def make_session(paths: AgentPaths, conversation_id: str, project_id: str | None) -> Session:
    session_dir = paths.data / "sessions" / conversation_id
    session_dir.mkdir(parents=True, exist_ok=True)
    session = Session(
        conversation_id=conversation_id,
        session_dir=session_dir,
        goal="synthetic work card listing",
        meta=SessionMeta(),
        messages=[],
        paths=paths,
    )
    if project_id is not None:
        bind_project_session(session, project_id, plan_status="draft")
    session.save()
    return session


def install_no_call_models(monkeypatch):
    created = []

    def factory(*, paths):
        model = NoCallModel(paths=paths)
        created.append(model)
        return model

    monkeypatch.setattr(agent_module, "LLMClient", factory)
    monkeypatch.setattr(llm_client, "LLMClient", factory)
    return created


def latest(events, event_type):
    return next((event for event in reversed(events) if event.get("type") == event_type), None)


def list_request(request_id: str | None = None, **fields):
    return {"type": "work-card.list", "request_id": request_id or str(uuid.uuid4()), **fields}


def test_list_paginates_mixed_cards_stably_without_full_snapshots_or_ack(tmp_path, monkeypatch):
    async def _run():
        paths = make_paths(tmp_path / "listing-root")
        project_id = "listing-project"
        create_project(paths, project_id)
        session = make_session(paths, "20261103-aaaaaaaa", project_id)
        store = WorkRecordStore(session)
        project_item = store.create_item(
            title="Synthetic project item", scope={"kind": "project", "project_id": project_id},
        )
        store.add_requirement(project_item["item_id"], "Synthetic requirement", event_id="listing-req")
        independent_items = [
            store.create_item(title=f"Independent summary {index:02d}", scope={"kind": "independent"})
            for index in range(25)
        ]
        ledger = WorkCardLedger(session, store)
        project_saved = ledger.save_snapshot(
            str(uuid.uuid4()), work_record_cards.build_project_work_record_cards(store, project_id),
        )
        assert project_saved.status == "saved"
        for item in independent_items:
            saved = ledger.save_snapshot(
                str(uuid.uuid4()), work_record_cards.build_independent_work_record_card(store, item["item_id"]),
            )
            assert saved.status == "saved"
        original = ledger.list_cards()
        assert len(original) == 26

        events = []
        models = install_no_call_models(monkeypatch)
        bridge = WsBridge(emit=events.append, paths=paths)
        handler = WsSessionHandler(paths)
        repl = server._build_repl(session, paths, bridge)

        first_request_id = str(uuid.uuid4())
        await handler._handle_incoming(list_request(first_request_id), repl, bridge)
        first = latest(events, "work-card.list.done")
        assert first is not None and first["status"] == "ok"
        assert first["request_id"] == first_request_id
        assert first["source_scope"] == {"session_id": session.conversation_id}
        assert len(first["items"]) == 20
        assert first["has_more"] is True
        assert first["next_cursor"] == first["items"][-1]["card_id"]
        assert all(set(item) == {"card_id", "scope", "saved_at", "viewed_at", "title"} for item in first["items"])
        assert all("snapshot" not in item and "card" not in item for item in first["items"])
        assert all(item["viewed_at"] is None for item in first["items"])
        assert all(item["scope"]["kind"] == "independent" for item in first["items"])
        assert [item["card_id"] for item in first["items"]] == [row["card_id"] for row in original[-20:]][::-1]
        assert all(0 < len(item["title"]) <= 120 for item in first["items"])

        # Adding a newer card after page one must not shift the before-cursor page.
        late_item = store.create_item(title="Added after first page", scope={"kind": "independent"})
        late_saved = ledger.save_snapshot(
            str(uuid.uuid4()), work_record_cards.build_independent_work_record_card(store, late_item["item_id"]),
        )
        assert late_saved.status == "saved"
        second_request_id = str(uuid.uuid4())
        await handler._handle_incoming(list_request(
            second_request_id, limit=20, before_card_id=first["next_cursor"],
        ), repl, bridge)
        second = latest(events, "work-card.list.done")
        assert second["status"] == "ok"
        assert second["request_id"] == second_request_id
        assert second["source_scope"] == {"session_id": session.conversation_id}
        assert [entry["card_id"] for entry in second["items"]] == [row["card_id"] for row in original[:6]][::-1]
        assert second["has_more"] is False and second["next_cursor"] is None
        assert all(entry["scope"]["kind"] == "project" for entry in second["items"][-1:])
        assert all(entry["card_id"] != late_saved.card_id for entry in second["items"])

        # Explicit small page size, cursor must exist in this exact ledger.
        small_request_id = str(uuid.uuid4())
        await handler._handle_incoming(list_request(small_request_id, limit=1), repl, bridge)
        small = latest(events, "work-card.list.done")
        assert small["request_id"] == small_request_id
        assert len(small["items"]) == 1 and small["has_more"] is True
        assert small["next_cursor"] == small["items"][0]["card_id"]

        before_bad_requests = len(events)
        invalid_messages = (
            list_request(limit=True),
            list_request(limit=21),
            list_request(before_card_id="not-a-card"),
            list_request(session_id=session.conversation_id),
            list_request(project_id=project_id),
            list_request(path="outside.json"),
            list_request(body="do not accept client content"),
        )
        for bad in invalid_messages:
            await handler._handle_incoming(bad, repl, bridge)
        assert len(events) == before_bad_requests + len(invalid_messages)
        invalid_responses = events[-len(invalid_messages):]
        assert all(event.get("type") == "work-card.list.done" and event.get("status") == "invalid_request"
                   for event in invalid_responses)
        assert [event.get("request_id") for event in invalid_responses] == [
            message["request_id"] for message in invalid_messages
        ]
        assert all("outside.json" not in repr(event) and "do not accept client content" not in repr(event)
                   for event in invalid_responses)
        bad_id_messages = (
            {"type": "work-card.list"},
            {"type": "work-card.list", "request_id": "not-a-uuid", "path": "private/path"},
        )
        for bad in bad_id_messages:
            await handler._handle_incoming(bad, repl, bridge)
            rejected = latest(events, "work-card.list.done")
            assert rejected["status"] == "invalid_request"
            assert "request_id" not in rejected
            assert "not-a-uuid" not in repr(rejected)
            assert "private/path" not in repr(rejected)
        cursor_request_id = str(uuid.uuid4())
        await handler._handle_incoming(list_request(
            cursor_request_id, before_card_id="wc-" + "f" * 64,
        ), repl, bridge)
        bad_cursor = latest(events, "work-card.list.done")
        assert bad_cursor["status"] == "invalid_cursor"
        assert bad_cursor["request_id"] == cursor_request_id

        # Listing never mutates viewed acknowledgements or card contents.
        current = ledger.list_cards()
        assert len(current) == 27
        assert all(before["snapshot"] == after["snapshot"] for before, after in zip(original, current))
        assert all(record["viewed_at"] is None for record in current)
        assert all(model.calls == [] for model in models)

    asyncio.run(_run())


def test_list_is_current_session_only_and_readonly_profiles_reject_it(tmp_path, monkeypatch):
    async def _run():
        paths = make_paths(tmp_path / "listing-scope-root")
        project_id = "scope-project"
        create_project(paths, project_id)
        current = make_session(paths, "20261103-bbbbbbbb", project_id)
        current_store = WorkRecordStore(current)
        current_independent = current_store.create_item(title="Current card", scope={"kind": "independent"})
        current_ledger = WorkCardLedger(current, current_store)
        current_saved = current_ledger.save_snapshot(
            str(uuid.uuid4()), work_record_cards.build_independent_work_record_card(current_store, current_independent["item_id"]),
        )
        assert current_saved.status == "saved"

        foreign = make_session(paths, "20261103-cccccccc", None)
        foreign_store = WorkRecordStore(foreign)
        foreign_item = foreign_store.create_item(title="Foreign card", scope={"kind": "independent"})
        foreign_ledger = WorkCardLedger(foreign, foreign_store)
        foreign_saved = foreign_ledger.save_snapshot(
            str(uuid.uuid4()), work_record_cards.build_independent_work_record_card(foreign_store, foreign_item["item_id"]),
        )
        assert foreign_saved.status == "saved"

        events = []
        models = install_no_call_models(monkeypatch)
        bridge = WsBridge(emit=events.append, paths=paths)
        handler = WsSessionHandler(paths)
        repl = server._build_repl(current, paths, bridge)
        foreign_cursor_request_id = str(uuid.uuid4())
        await handler._handle_incoming(list_request(
            foreign_cursor_request_id, before_card_id=foreign_saved.card_id,
        ), repl, bridge)
        response = latest(events, "work-card.list.done")
        assert response is not None and response["status"] == "invalid_cursor"
        assert response["request_id"] == foreign_cursor_request_id
        assert current_ledger.list_cards()[0]["card_id"] == current_saved.card_id
        assert foreign_ledger.list_cards()[0]["card_id"] == foreign_saved.card_id

        # Listing is also available within its own unbound Session and remains local.
        foreign_events = []
        foreign_bridge = WsBridge(emit=foreign_events.append, paths=paths)
        foreign_repl = server._build_repl(foreign, paths, foreign_bridge)
        foreign_request_id = str(uuid.uuid4())
        await handler._handle_incoming(list_request(foreign_request_id), foreign_repl, foreign_bridge)
        foreign_listing = latest(foreign_events, "work-card.list.done")
        assert foreign_listing["status"] == "ok"
        assert foreign_listing["request_id"] == foreign_request_id
        assert foreign_listing["source_scope"] == {"session_id": foreign.conversation_id}
        assert [entry["card_id"] for entry in foreign_listing["items"]] == [foreign_saved.card_id]
        assert foreign_listing["items"][0]["scope"] == {
            "kind": "independent", "item_id": foreign_item["item_id"],
        }

        # A Session rebind observed during the ledger read invalidates the response.
        original_list = WorkCardLedger.list_cards
        def switch_during_list(instance):
            result = original_list(instance)
            repl.session = foreign
            return result
        monkeypatch.setattr(WorkCardLedger, "list_cards", switch_during_list)
        rebind_request_id = str(uuid.uuid4())
        await handler._handle_incoming(list_request(rebind_request_id), repl, bridge)
        rebind_response = latest(events, "work-card.list.done")
        assert rebind_response["status"] == "session_changed"
        assert rebind_response["request_id"] == rebind_request_id
        monkeypatch.setattr(WorkCardLedger, "list_cards", original_list)
        repl.session = current

        managed_events = []
        managed = WsSessionHandler(paths, managed_readonly_context=object())
        managed_bridge = WsBridge(emit=managed_events.append, paths=paths)
        await managed._dispatch({"type": "work-card.list"}, repl, managed_bridge)
        assert managed_events[-1]["type"] == "error"

        fixed_paths = replace(paths, _fixed_code_root=paths.agent_root / "fixed-control-root")
        fixed_events = []
        fixed = WsSessionHandler(fixed_paths)
        fixed_bridge = WsBridge(emit=fixed_events.append, paths=fixed_paths)
        await fixed._dispatch({"type": "work-card.list"}, repl, fixed_bridge)
        assert fixed_events[-1]["type"] == "error"

        # Corrupting only this temporary ledger fails closed without returning entries.
        current_ledger._ledger_store._path.write_text("{broken", encoding="utf-8")
        corrupt_request_id = str(uuid.uuid4())
        await handler._handle_incoming(list_request(corrupt_request_id), repl, bridge)
        corrupt_response = latest(events, "work-card.list.done")
        assert corrupt_response["status"] == "unavailable"
        assert corrupt_response["request_id"] == corrupt_request_id
        assert all(model.calls == [] for model in models)

    asyncio.run(_run())
