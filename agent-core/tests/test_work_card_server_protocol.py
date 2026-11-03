from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import agent as agent_module
import llm_client
import multi_project_work_records as multi_project_reader
import project_api
import server
import work_record_cards
from paths import AgentPaths
from project_cli import bind_project_session
from project_mode import create_project
from server import WsBridge, WsSessionHandler
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_card_ledger import WorkCardLedger


class NoCallModel:
    def __init__(self, *, paths):
        self.paths = paths
        self.cancel_event = None
        self.calls = []

    def set_cancel_event(self, event):
        self.cancel_event = event

    def chat(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("card protocol must not call an LLM")


def make_paths(root: Path) -> AgentPaths:
    root.mkdir(parents=True, exist_ok=True)
    paths = AgentPaths(
        agent_root=root,
        workspace=root / "workspace",
        data=root / "data",
        evolve=root / "evolve",
    )
    paths.workspace.mkdir(parents=True, exist_ok=True)
    return paths


def make_project_session(paths: AgentPaths, project_id: str, conversation_id: str) -> Session:
    create_project(paths, project_id)
    session_dir = paths.data / "sessions" / conversation_id
    session_dir.mkdir(parents=True, exist_ok=True)
    session = Session(
        conversation_id=conversation_id,
        session_dir=session_dir,
        goal=f"synthetic {project_id}",
        meta=SessionMeta(),
        messages=[],
        paths=paths,
    )
    bind_project_session(session, project_id, plan_status="draft")
    session.save()
    store = WorkRecordStore(session)
    item = store.create_item(
        title=f"Synthetic {project_id} work item",
        scope={"kind": "project", "project_id": project_id},
    )
    store.add_requirement(
        item["item_id"], f"Synthetic requirement for {project_id}",
        event_id=f"{project_id}-requirement",
    )
    return session


def install_no_call_models(monkeypatch):
    main_models = []
    recall_clients = []

    def main_factory(*, paths):
        value = NoCallModel(paths=paths)
        main_models.append(value)
        return value

    def recall_factory(*, paths):
        value = NoCallModel(paths=paths)
        recall_clients.append(value)
        return value

    monkeypatch.setattr(agent_module, "LLMClient", main_factory)
    monkeypatch.setattr(llm_client, "LLMClient", recall_factory)
    return main_models, recall_clients


def response(events, event_type):
    matches = [event for event in events if event.get("type") == event_type]
    return matches[-1] if matches else None


def test_card_protocol_create_replay_get_seen_and_project_switch_are_session_scoped(tmp_path, monkeypatch):
    async def _run():
        paths = make_paths(tmp_path / "synthetic-agent-root")
        project_a = make_project_session(paths, "synthetic-a", "20261101-aaaaaaaa")
        project_b = make_project_session(paths, "synthetic-b", "20261101-bbbbbbbb")
        events = []
        drop_first_create_response = {"value": True}

        def emit(event):
            if event.get("type") == "work-card.create.done" and drop_first_create_response["value"]:
                drop_first_create_response["value"] = False
                raise OSError("synthetic response transport failure")
            events.append(event)

        bridge = WsBridge(emit=emit, paths=paths, confirm_timeout=3)
        main_models, recall_clients = install_no_call_models(monkeypatch)
        handler = WsSessionHandler(paths)
        repl = server._build_repl(project_a, paths, bridge)

        build_calls = []
        real_build = work_record_cards.build_project_work_record_cards
        def observe_build(store, project_id):
            build_calls.append((store._session.conversation_id, project_id))
            return real_build(store, project_id)
        monkeypatch.setattr(work_record_cards, "build_project_work_record_cards", observe_build)

        request_id = "fd4b7206-1315-4ce6-8c08-c4e053992aa1"
        create_message = {"type": "work-card.create", "request_id": request_id}
        await asyncio.wait_for(handler._handle_incoming(create_message, repl, bridge), timeout=5)
        assert response(events, "work-card.create.done") is None  # socket/event send failed after durable commit
        assert len(build_calls) == 1

        store_a = WorkRecordStore(project_a)
        item_a = store_a.list_items()[0]
        store_a.add_requirement(
            item_a["item_id"], "added after saved card", event_id="late-sidecar-change",
        )
        original_ledger = WorkCardLedger(project_a, store_a)
        stored_card = original_ledger.get_by_request_id(request_id)
        assert stored_card is not None
        original_snapshot = stored_card["snapshot"]

        # Same request ID resolves the immutable saved snapshot before rebuilding from changed source.
        await asyncio.wait_for(handler._handle_incoming(create_message, repl, bridge), timeout=5)
        replay = response(events, "work-card.create.done")
        assert replay["status"] == "already_saved"
        assert replay["request_id"] == request_id
        assert replay["card_id"] == stored_card["card_id"]
        assert replay["card"]["snapshot"] == original_snapshot
        assert replay["source_scope"] == {
            "session_id": project_a.conversation_id, "project_id": "synthetic-a",
        }
        assert replay["card"]["viewed_at"] is None
        assert "delivered" not in replay and "seen" not in replay
        assert len(build_calls) == 1

        await asyncio.wait_for(handler._handle_incoming({
            "type": "work-card.get", "card_id": stored_card["card_id"],
        }, repl, bridge), timeout=5)
        get_result = response(events, "work-card.get.done")
        assert get_result["status"] == "found"
        assert get_result["card"]["snapshot"] == original_snapshot

        seen_message = {"type": "work-card.seen", "card_id": stored_card["card_id"]}
        await asyncio.wait_for(handler._handle_incoming(seen_message, repl, bridge), timeout=5)
        seen_first = response(events, "work-card.seen.done")
        assert seen_first["status"] == "acknowledged"
        assert seen_first["viewed_at"]
        await asyncio.wait_for(handler._handle_incoming(seen_message, repl, bridge), timeout=5)
        seen_again = response(events, "work-card.seen.done")
        assert seen_again["status"] == "already_acknowledged"
        assert seen_again["viewed_at"] == seen_first["viewed_at"]

        # Project switching is serialized by TURN_LOCK; the same card cannot be read/ACKed in Session B.
        await asyncio.wait_for(handler._handle_incoming({
            "type": "project.switch", "project_id": "synthetic-b", "confirm": True,
            "request_id": "df75cfcb-f97c-4db5-9f5c-984af86ef110",
        }, repl, bridge), timeout=5)
        assert repl.session.conversation_id == project_b.conversation_id
        assert repl.agent.session is repl.session
        await asyncio.wait_for(handler._handle_incoming({
            "type": "work-card.get", "card_id": stored_card["card_id"],
        }, repl, bridge), timeout=5)
        assert response(events, "work-card.get.done")["status"] == "not_found"
        await asyncio.wait_for(handler._handle_incoming(seen_message, repl, bridge), timeout=5)
        assert response(events, "work-card.seen.done")["status"] == "rejected"

        assert all(model.calls == [] for model in main_models + recall_clients)


    asyncio.run(_run())
def test_card_protocol_rejects_extra_client_scope_and_readonly_host_modes(tmp_path, monkeypatch):
    async def _run():
        paths = make_paths(tmp_path / "strict-protocol-root")
        session = make_project_session(paths, "synthetic-only", "20261101-cccccccc")
        events = []
        bridge = WsBridge(emit=events.append, paths=paths)
        main_models, recall_clients = install_no_call_models(monkeypatch)
        handler = WsSessionHandler(paths)
        repl = server._build_repl(session, paths, bridge)
        build_calls = []
        monkeypatch.setattr(multi_project_reader, "render_selected_projects_overview",
                            lambda *_args: build_calls.append("overview"))

        await handler._handle_incoming({
            "type": "work-card.create", "request_id": "8b6dcab0-a122-4a5a-9d43-3bb8b1f56105",
            "project_id": "attacker-project", "snapshot": {"cards": []}, "path": "outside.json",
        }, repl, bridge)
        rejected = response(events, "work-card.create.done")
        assert rejected is not None and rejected["status"] == "invalid_request"
        assert build_calls == []
        assert WorkCardLedger(session, WorkRecordStore(session)).list_cards() == []

        # Managed-readonly uses a separate message whitelist; direct dispatch also fails closed.
        managed_events = []
        managed_handler = WsSessionHandler(paths, managed_readonly_context=SimpleNamespace())
        managed_bridge = WsBridge(emit=managed_events.append, paths=paths)
        for message in (
            {"type": "work-card.create", "request_id": "8b6dcab0-a122-4a5a-9d43-3bb8b1f56105"},
            {"type": "work-card.get", "card_id": "wc-" + "0" * 64},
            {"type": "work-card.seen", "card_id": "wc-" + "0" * 64},
        ):
            await managed_handler._dispatch(message, repl, managed_bridge)
        assert len(managed_events) == 3
        assert all(event.get("type") == "error" for event in managed_events)
        assert WorkCardLedger(session, WorkRecordStore(session)).list_cards() == []

        # Fixed Host E0 also rejects all card protocol messages, including read and ACK.
        fixed_paths = replace(paths, _fixed_code_root=paths.agent_root / "fixed-control-root")
        fixed_handler = WsSessionHandler(fixed_paths)
        fixed_events = []
        fixed_bridge = WsBridge(emit=fixed_events.append, paths=fixed_paths)
        for message in (
            {"type": "work-card.create", "request_id": "8b6dcab0-a122-4a5a-9d43-3bb8b1f56105"},
            {"type": "work-card.get", "card_id": "wc-" + "0" * 64},
            {"type": "work-card.seen", "card_id": "wc-" + "0" * 64},
        ):
            await fixed_handler._dispatch(message, repl, fixed_bridge)
        assert len(fixed_events) == 3
        assert all(event.get("type") == "error" for event in fixed_events)
        assert WorkCardLedger(session, WorkRecordStore(session)).list_cards() == []
        assert all(model.calls == [] for model in main_models + recall_clients)
    asyncio.run(_run())


def make_unbound_session(paths: AgentPaths, conversation_id: str) -> Session:
    session_dir = paths.data / "sessions" / conversation_id
    session_dir.mkdir(parents=True, exist_ok=True)
    session = Session(
        conversation_id=conversation_id,
        session_dir=session_dir,
        goal="synthetic unbound session",
        meta=SessionMeta(),
        messages=[],
        paths=paths,
    )
    session.save()
    return session


def test_independent_work_card_create_replay_get_seen_in_unbound_and_project_sessions(tmp_path, monkeypatch):
    async def _run():
        paths = make_paths(tmp_path / "independent-card-root")
        project_session = make_project_session(paths, "project-owner", "20261102-dddddddd")
        project_store = WorkRecordStore(project_session)
        independent = project_store.create_item(
            title="Synthetic independent matter", scope={"kind": "independent"},
        )
        unbound_session = make_unbound_session(paths, "20261102-eeeeeeee")
        unbound_store = WorkRecordStore(unbound_session)
        unbound_item = unbound_store.create_item(
            title="Synthetic unbound matter", scope={"kind": "independent"},
        )
        events = []
        bridge = WsBridge(emit=events.append, paths=paths)
        main_models, recall_clients = install_no_call_models(monkeypatch)
        handler = WsSessionHandler(paths)
        repl = server._build_repl(unbound_session, paths, bridge)

        calls = []
        real_build = work_record_cards.build_independent_work_record_card
        def observe_independent_build(store, item_id):
            calls.append((store._session, item_id))
            return real_build(store, item_id)
        monkeypatch.setattr(work_record_cards, "build_independent_work_record_card", observe_independent_build)

        req = "bdb141b2-4d03-4d26-9882-202693b58082"
        message = {"type": "work-card.create", "request_id": req, "item_id": unbound_item["item_id"]}
        await handler._handle_incoming(message, repl, bridge)
        created = response(events, "work-card.create.done")
        assert created is not None and created["status"] == "saved"
        assert created["card"]["snapshot"]["kind"] == "independent"
        assert created["card"]["snapshot"]["item_id"] == unbound_item["item_id"]
        assert created["source_scope"] == {"session_id": unbound_session.conversation_id, "kind": "independent"}
        assert "project_id" not in created["source_scope"]
        saved_snapshot = created["card"]["snapshot"]
        assert calls == [(unbound_session, unbound_item["item_id"])]

        # Same request and kind/item scope reuses the original immutable snapshot.
        unbound_store.create_item(title="later item", scope={"kind": "independent"})
        await handler._handle_incoming(message, repl, bridge)
        replay = response(events, "work-card.create.done")
        assert replay["status"] == "already_saved"
        assert replay["card"]["snapshot"] == saved_snapshot
        assert calls == [(unbound_session, unbound_item["item_id"])]

        card_id = created["card_id"]
        await handler._handle_incoming({"type": "work-card.get", "card_id": card_id}, repl, bridge)
        get_result = response(events, "work-card.get.done")
        assert get_result["card"]["snapshot"] == saved_snapshot
        assert get_result["source_scope"] == {"session_id": unbound_session.conversation_id, "kind": "independent"}
        await handler._handle_incoming({"type": "work-card.seen", "card_id": card_id}, repl, bridge)
        first_ack = response(events, "work-card.seen.done")
        assert first_ack["status"] == "acknowledged" and first_ack["viewed_at"]
        assert first_ack["source_scope"] == {"session_id": unbound_session.conversation_id, "kind": "independent"}
        await handler._handle_incoming({"type": "work-card.seen", "card_id": card_id}, repl, bridge)
        repeated_ack = response(events, "work-card.seen.done")
        assert repeated_ack["status"] == "already_acknowledged"
        assert repeated_ack["viewed_at"] == first_ack["viewed_at"]

        # A current unbound Session cannot view another Session's card or make a project card.
        project_ledger = WorkCardLedger(project_session, project_store)
        project_snapshot = work_record_cards.build_project_work_record_cards(project_store, "project-owner")
        project_saved = project_ledger.save_snapshot("2a9d7c7a-e38c-459a-9f9e-530aa9449b2c", project_snapshot)
        assert project_saved.status == "saved"
        await handler._handle_incoming({"type": "work-card.get", "card_id": project_saved.card_id}, repl, bridge)
        assert response(events, "work-card.get.done")["status"] == "not_found"
        await handler._handle_incoming({"type": "work-card.seen", "card_id": project_saved.card_id}, repl, bridge)
        assert response(events, "work-card.seen.done")["status"] == "rejected"

        no_item_req = "1640aa96-503d-4012-a725-6474ccf7d2d4"
        await handler._handle_incoming({"type": "work-card.create", "request_id": no_item_req}, repl, bridge)
        assert response(events, "work-card.create.done")["status"] == "project_unbound"
        assert WorkCardLedger(unbound_session, WorkRecordStore(unbound_session)).get_by_request_id(no_item_req) is None
        assert all(model.calls == [] for model in main_models + recall_clients)

    asyncio.run(_run())


def test_work_card_request_id_conflicts_across_kind_and_independent_item_scope(tmp_path, monkeypatch):
    async def _run():
        paths = make_paths(tmp_path / "cross-kind-conflict-root")
        session = make_project_session(paths, "conflict-project", "20261102-ffffffff")
        store = WorkRecordStore(session)
        independent_a = store.create_item(title="Independent A", scope={"kind": "independent"})
        independent_b = store.create_item(title="Independent B", scope={"kind": "independent"})
        events = []
        bridge = WsBridge(emit=events.append, paths=paths)
        main_models, recall_clients = install_no_call_models(monkeypatch)
        handler = WsSessionHandler(paths)
        repl = server._build_repl(session, paths, bridge)
        req = "0a1aa364-c81d-4ac7-8278-7d95b6642861"

        await handler._handle_incoming({"type": "work-card.create", "request_id": req}, repl, bridge)
        project_card = response(events, "work-card.create.done")["card"]
        assert project_card["snapshot"].get("kind") is None

        await handler._handle_incoming({"type": "work-card.create", "request_id": req, "item_id": independent_a["item_id"]}, repl, bridge)
        assert response(events, "work-card.create.done")["status"] == "request_conflict"

        independent_req = "dbb231e4-51eb-4c6f-ae83-cc1008e6b6a3"
        await handler._handle_incoming({"type": "work-card.create", "request_id": independent_req, "item_id": independent_a["item_id"]}, repl, bridge)
        independent_card = response(events, "work-card.create.done")["card"]
        assert independent_card["snapshot"]["kind"] == "independent"
        assert independent_card["snapshot"]["item_id"] == independent_a["item_id"]
        assert response(events, "work-card.create.done")["source_scope"] == {
            "session_id": session.conversation_id, "kind": "independent",
        }

        await handler._handle_incoming({"type": "work-card.create", "request_id": independent_req, "item_id": independent_b["item_id"]}, repl, bridge)
        assert response(events, "work-card.create.done")["status"] == "request_conflict"
        await handler._handle_incoming({"type": "work-card.create", "request_id": independent_req}, repl, bridge)
        assert response(events, "work-card.create.done")["status"] == "request_conflict"

        # Invalid card identifiers and extra client-supplied content/scope are rejected.
        before = len(WorkCardLedger(session, store).list_cards())
        for bad in (
            {"type": "work-card.get", "card_id": "not-a-card"},
            {"type": "work-card.seen", "card_id": independent_card["card_id"], "session_id": session.conversation_id},
            {"type": "work-card.create", "request_id": "4bd5e06d-e3dd-4fcc-802f-98ba0834abac", "item_id": independent_a["item_id"], "title": "client text"},
        ):
            await handler._handle_incoming(bad, repl, bridge)
        assert len(WorkCardLedger(session, store).list_cards()) == before
        assert all(model.calls == [] for model in main_models + recall_clients)

    asyncio.run(_run())
