from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path
import httpx
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_agent_project_worker_tool import _ParentModel, _REAL_HTTPX_CLIENT
from tests.isolation_helpers import temporary_agent_paths
from project_mode import create_project
from project_switch import record_project_session
from session import create_new, _valid_work_card_ref
from work_records import WorkRecordStore
from multi_project_work_record_bindings import ConversationWorkRecordBindings
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations
from server import WsBridge, _build_repl, _run_line
import llm_client


@pytest.mark.parametrize("choice", ["y", "n", "stale"])
def test_agent_explicit_copy_confirmation_and_delivery_lifecycle(monkeypatch, choice):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file")) as paths:
        projects = ("project-alpha", "project-beta")
        sources = []
        source_stores = []
        for i, project in enumerate(projects):
            create_project(paths, project, project_template="light")
            source = create_new(paths, conversation_id=f"20261106-{i:08x}")
            source.meta.project_id = project
            source.meta.project_root = f"workspace/{project}"
            source.save(); record_project_session(paths, project, source.conversation_id)
            store = WorkRecordStore(source)
            item = store.create_item(title=f"Synthetic item {project}", scope={"kind": "project", "project_id": project})
            store.add_requirement(item["item_id"], f"Synthetic task for {project}.", event_id=f"req-{project}")
            sources.append(store._path)
            source_stores.append((store, item["item_id"]))
            (paths.workspace / project / "README.md").write_text("synthetic", encoding="utf-8")
        before = [p.read_bytes() for p in sources]
        session = create_new(paths, conversation_id="20261106-00000020")
        session.meta.turn_mode = "agent"; session.meta.harness = "desktop"
        session.meta.active_shell = "project"; session.meta.project_id = "project-alpha"
        session.meta.project_root = "workspace/project-alpha"; session._ordinary_work_intent = "execute"
        workers = [{"project_id": p, "task": f"Synthetic task for {p}.", "read_targets": ["README.md"], "write_targets": ["PROJECT.md"]} for p in projects]
        events = []
        bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=5)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
        model = _ParentModel(workers); repl.agent.llm = model; model.set_cancel_event(repl.agent.cancel_event)
        real_turn = repl.agent.run_turn
        repl.agent.run_turn = lambda text, **_kwargs: real_turn(text, spawn_explore=False)
        monkeypatch.setattr("project_api.after_turn_project_hooks", lambda *_args: None)
        monkeypatch.setattr("server.session_memory_event", lambda *_args: {"type": "session.memory"})
        monkeypatch.setattr("server._proposals_payload", lambda *_args: {"type": "evolve.proposals", "items": []})
        monkeypatch.setattr("server._memories_payload", lambda *_args: {"type": "evolve.memories", "items": []})
        monkeypatch.setenv("OX567_API_KEY", "synthetic-only")
        monkeypatch.setenv("OX567_BASE_URL", "https://example.invalid")
        monkeypatch.setenv("OX567_MODEL_FLASH", "gpt-6-luna")
        def provider(request):
            return httpx.Response(200, request=request, json={"model": "gpt-6-luna", "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "synthetic turn", "tool_calls": []}}]})
        monkeypatch.setattr(llm_client.httpx, "Client", lambda **kw: _REAL_HTTPX_CLIENT(transport=httpx.MockTransport(provider), **kw))
        queued = bridge.lifecycle.enqueue("ordinary"); bridge.lifecycle_conversation_id = session.conversation_id; bridge._turn_busy.set()
        async def run():
            task = asyncio.create_task(_run_line(repl, bridge, "Execute both synthetic projects and track in this conversation.", paths, lock_held=True, prestarted_run_id=queued.run_id))
            seen = set(); copy_previews = []; project_previews = []
            deadline = asyncio.get_running_loop().time() + 20
            while not task.done():
                pending = bridge._pending_confirm_id
                if pending and pending not in seen:
                    event = next((e for e in reversed(events) if e.get("type") == "confirm.request" and e.get("request_id") == pending), None)
                    if event:
                        seen.add(pending)
                        is_copy = "LINKED_WORK_ITEM_COPY_CONFIRM" in event["preview"]
                        if is_copy:
                            copy_previews.append(event["preview"])
                            if choice == "stale":
                                source_stores[0][0].add_requirement(source_stores[0][1], "synthetic changed", event_id="change")
                        is_project_copy = "LINKED_PROJECT_SESSION_COPY_CONFIRM" in event["preview"]
                        assert not (is_copy and is_project_copy)
                        if is_project_copy:
                            project_previews.append(event["preview"])
                        answer = ("n" if is_project_copy else
                                  ("n" if choice == "n" else "y") if is_copy else "y")
                        assert bridge.deliver_confirm(pending, answer)
                if asyncio.get_running_loop().time() > deadline:
                    bridge.request_cancel()
                    raise AssertionError("synthetic agent turn timed out")
                await asyncio.sleep(.005)
            await task
            return copy_previews, project_previews
        previews, project_previews = asyncio.run(run())
        assert len(previews) == 1 and all(p in previews[0] for p in projects)
        preview = previews[0]
        assert all(f"Synthetic task for {p}." in preview for p in projects)
        assert "\u4e0d\u662f\u5b8c\u6574\u9879\u76ee\u5feb\u7167" in preview
        assert "\u4e0d\u6388\u6743\u5199\u5165\u6765\u6e90\u6216\u672a\u6765\u91cd\u8bfb\u6765\u6e90" in preview
        # Each independently requested project copy is explicitly refused;
        # stale alpha cannot be proposed, but beta must still be considered.
        assert len(project_previews) == (1 if choice == "stale" else 2)
        assert any("project-beta" in preview for preview in project_previews)
        assert (any("project-alpha" in preview for preview in project_previews)) == (choice != "stale")
        rows = ConversationWorkRecordBindings(session).list_bindings()
        assert len(rows) == 2
        assert all("project_session_copy" not in row for row in rows)
        assert all(("card_copy" in row) == (choice == "y") for row in rows)
        present_events = [event for event in events if event.get("type") == "work-card.present"]
        if choice == "y":
            # The tool result reports the copy as pending at its own commit point.
            # Turn-finalization then persists cards/references and advances the intent.
            copy_states = [row["card_copy"]["intent"]["state"] for row in rows]
            assert copy_states == ["reference_queued"] * len(rows), copy_states
            store = WorkRecordStore(session)
            ledger = WorkCardLedger(session, store)
            cards = ledger.list_cards()
            assert len(cards) == len(rows)
            assert all(card["snapshot"]["kind"] == "linked_item_copy" for card in cards)
            assert {event["card_id"] for event in present_events} == {card["card_id"] for card in cards}
            tool_end_positions = [index for index, event in enumerate(events)
                                  if event.get("type") == "tool.end" and event.get("tool") == "run_project_workers"]
            present_positions = [index for index, event in enumerate(events)
                                 if event.get("type") == "work-card.present"]
            assert len(tool_end_positions) == 1 and present_positions
            assert tool_end_positions[0] < min(present_positions), "reference publication follows the tool's pending result"
            references = [message for message in session.messages if _valid_work_card_ref(message)]
            assert len(references) == len(rows)
            assert {message["card_id"] for message in references} == {card["card_id"] for card in cards}
            presentations = WorkCardPresentations(session, store, ledger)
            facts = [presentations.get(message["presentation_id"]).fact for message in references]
            assert all(fact is not None and fact.receipt_state != "received"
                       and fact.received_at is None and fact.acknowledged_at is None for fact in facts)
            assert not any(event.get("type") == "work-card.received" for event in events)
        else:
            assert not present_events
            assert not [message for message in session.messages if _valid_work_card_ref(message)]
            store = WorkRecordStore(session)
            assert not [card for card in WorkCardLedger(session, store).list_cards()
                        if card["snapshot"].get("kind") == "linked_item_copy"]
        tool_rows = [json.loads(row["content"]) for row in session.messages if row.get("role") == "tool"]
        result = next(row for row in tool_rows if row.get("tool") == "run_project_workers")
        expected = {"y": "saved_pending_not_published", "n": "not_authorized", "stale": "not_confirmed_unavailable_or_stale"}
        assert result["data"]["linked_item_copy_status"] == expected[choice]
        assert sources[1].read_bytes() == before[1]
        if choice != "stale": assert sources[0].read_bytes() == before[0]
