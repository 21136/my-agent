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
from session import create_new
from work_records import WorkRecordStore
from multi_project_work_record_bindings import ConversationWorkRecordBindings
from server import WsBridge, _build_repl, _run_line
import llm_client


@pytest.mark.parametrize("choice", ["y", "n", "stale", "remap", "project_only"])
def test_agent_separate_source_session_copy_confirmation(monkeypatch, choice):
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
            store.add_requirement(item["item_id"], f"Extra requirement for {project}.", event_id=f"extra-{project}")
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
            seen = set(); copy_previews = []
            deadline = asyncio.get_running_loop().time() + 20
            while not task.done():
                pending = bridge._pending_confirm_id
                if pending and pending not in seen:
                    event = next((e for e in reversed(events) if e.get("type") == "confirm.request" and e.get("request_id") == pending), None)
                    if event:
                        seen.add(pending)
                        is_copy = "LINKED_PROJECT_SESSION_COPY_CONFIRM" in event["preview"]
                        if is_copy:
                            copy_previews.append(event["preview"])
                            if choice == "stale" and "project-alpha" in event["preview"]:
                                source_stores[0][0].add_requirement(source_stores[0][1], "synthetic changed", event_id="change")
                            if choice == "remap" and "project-alpha" in event["preview"]:
                                record_project_session(paths, "project-alpha", "20261106-00000001")
                        item_copy = "LINKED_WORK_ITEM_COPY_CONFIRM" in event["preview"]
                        answer = (("n" if choice == "n" or "project-beta" in event["preview"] else "y")
                                  if is_copy else "n" if item_copy and choice == "project_only" else "y")
                        assert bridge.deliver_confirm(pending, answer)
                if asyncio.get_running_loop().time() > deadline:
                    bridge.request_cancel()
                    raise AssertionError("synthetic agent turn timed out")
                await asyncio.sleep(.005)
            await task
            return copy_previews
        previews = asyncio.run(run())
        assert len(previews) == 2
        alpha = next(p for p in previews if "project-alpha" in p)
        assert "Extra requirement for project-alpha." in alpha
        assert "project-beta" not in alpha
        assert "并非整个项目的全部历史" in alpha
        rows = ConversationWorkRecordBindings(session).list_bindings()
        alpha_row = next(r for r in rows if r["project_id"] == "project-alpha")
        beta_row = next(r for r in rows if r["project_id"] == "project-beta")
        assert ("project_session_copy" in alpha_row) == (choice in {"y", "project_only"})
        assert "project_session_copy" not in beta_row
        assert ("card_copy" in alpha_row) == (choice != "project_only")
        if choice in {"y", "project_only"}:
            copy = alpha_row["project_session_copy"]
            assert copy["intent"]["state"] == "reference_queued"
            offered = [e for e in events if e.get("type") == "work-card.present" and e.get("card", {}).get("snapshot", {}).get("kind") == "linked_project_session_copy"]
            assert len(offered) == 1
            assert offered[0]["card"]["snapshot"]["source"]["project_id"] == "project-alpha"
            assert len(copy["snapshot"]["items"][0]["requirements"]) == 2
            assert copy["snapshot"]["worker_facts"][0]["operation_result"] == "unknown"
        assert sources[1].read_bytes() == before[1]
        if choice != "stale": assert sources[0].read_bytes() == before[0]

        if choice == "project_only":
            notices = [e.get("text", "") for e in events if e.get("type") == "turn.notice"]
            assert any("来源 Session" in text and "待发布" in text for text in notices)
            assert not any("未确认保存来源事项副本；工作卡未发布" in text for text in notices)
            assert any("事项副本" in text and "未创建事项卡片" in text for text in notices)

        if choice == "y":
            from work_card_ledger import WorkCardLedger
            old_cards = WorkCardLedger(session, WorkRecordStore(session)).list_cards()
            source_stores[0][0].add_requirement(source_stores[0][1], "new synthetic requirement", event_id="refresh-new")
            monkeypatch.setattr(repl.agent.llm, "chat", lambda *a, **k: (_ for _ in ()).throw(AssertionError("model called")))
            monkeypatch.setattr(repl.agent.project_worker_host, "run_many", lambda *a, **k: (_ for _ in ()).throw(AssertionError("worker called")))
            before_events = len(events)
            async def refresh():
                task = asyncio.create_task(_run_line(repl, bridge, "把项目 project-alpha 的工作卡重新发一遍", paths))
                seen = set(); confirmations = []
                while not task.done():
                    pending = bridge._pending_confirm_id
                    if pending and pending not in seen:
                        event = next((e for e in reversed(events) if e.get("type") == "confirm.request" and e.get("request_id") == pending), None)
                        if event:
                            seen.add(pending); confirmations.append(event["preview"])
                            assert bridge.deliver_confirm(pending, "y")
                    await asyncio.sleep(.005)
                await task
                return confirmations
            confirmations = asyncio.run(asyncio.wait_for(refresh(), timeout=20))
            assert len(confirmations) == 2
            assert "project-alpha" in confirmations[0]
            assert "new synthetic requirement" in confirmations[1]
            cards = WorkCardLedger(session, WorkRecordStore(session)).list_cards()
            assert len(cards) == len(old_cards) + 1 and cards[:len(old_cards)] == old_cards
            offers = [e for e in events[before_events:] if e.get("type") == "work-card.present"]
            assert len(offers) == 1 and offers[0]["card_id"] == cards[-1]["card_id"]
            assert cards[-1]["snapshot"]["source"]["mapping_claim"] == "not_asserted"
            assert cards[-1]["snapshot"]["source"]["worker_facts"][0]["verification"] == "not_verified"

            async def rejected_or_changed(mode):
                task = asyncio.create_task(_run_line(repl, bridge, "看看项目 project-alpha 最新工作卡", paths))
                seen = set(); confirmations = []
                while not task.done():
                    pending = bridge._pending_confirm_id
                    if pending and pending not in seen:
                        event = next((e for e in reversed(events) if e.get("type") == "confirm.request" and e.get("request_id") == pending), None)
                        if event:
                            seen.add(pending); confirmations.append(event["preview"])
                            is_snapshot = "LINKED_PROJECT_SESSION_REFRESH_CONFIRM" in event["preview"]
                            if is_snapshot and mode == "changed":
                                source_stores[0][0].add_requirement(source_stores[0][1], "during confirm", event_id="changed-refresh")
                            assert bridge.deliver_confirm(pending, "n" if is_snapshot and mode == "deny" else "y")
                    await asyncio.sleep(.005)
                await task
                return confirmations
            for mode in ("deny", "changed"):
                before = len(events)
                prompts = asyncio.run(asyncio.wait_for(rejected_or_changed(mode), timeout=20))
                assert len(prompts) == 2
                assert not [e for e in events[before:] if e.get("type") == "work-card.present"]
                assert WorkCardLedger(session, WorkRecordStore(session)).list_cards() == cards
