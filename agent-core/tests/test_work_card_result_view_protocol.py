from __future__ import annotations

import asyncio
import sys
import threading
import pytest
import uuid
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import server
import work_record_visibility_clock
from server import WsBridge, WsSessionHandler
from paths import AgentPaths
from project_cli import bind_project_session
from project_mode import create_project
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations
from work_record_cards import build_project_work_record_cards
from work_record_visibility_clock import ElapsedClockSample
from work_records_overview import render_work_records_overview


def test_explicit_result_detail_ack_requires_exact_history_presentation_and_is_result_scoped(tmp_path, monkeypatch):
    async def run():
        paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace", data=tmp_path / "data", evolve=tmp_path / "evolve")
        paths.workspace.mkdir(parents=True)
        create_project(paths, "p")
        session_dir = paths.data / "sessions" / "20261030-aaaaaaaa"
        session_dir.mkdir(parents=True)
        session = Session("20261030-aaaaaaaa", session_dir, "synthetic", SessionMeta(), [], paths)
        bind_project_session(session, "p", plan_status="draft")
        session.save()
        store = WorkRecordStore(session)
        item = store.create_item(title="synthetic", scope={"kind": "project", "project_id": "p"})
        req1 = store.add_requirement(item["item_id"], "one", event_id="e1")
        req2 = store.add_requirement(item["item_id"], "two", event_id="e2")
        for req, run_id, result_id, summary in ((req1, "run-a", "result-a", "a"), (req2, "run-b", "result-b", "b")):
            store.begin_requirement_run(item["item_id"], req["requirement_id"], run_id=run_id, expected_current_run_id=None)
            store.record_result_if_current_run(item["item_id"], req["requirement_id"], run_id=run_id, result_id=result_id,
                status="completed", summary=summary, verification={"status": "not_verified"})
        clock = [ElapsedClockSample("boot-protocol", 9_000_000_000, "2026-10-30T00:00:00Z")]
        monkeypatch.setattr(work_record_visibility_clock, "sample_elapsed_clock", lambda: clock[0])
        ledger = WorkCardLedger(session, store)
        saved = ledger.save_snapshot(str(uuid.uuid4()), build_project_work_record_cards(store, "p"))
        presentation_id = str(uuid.uuid4())
        session.append_sent_work_card_reference(presentation_id, saved.card_id, run_id=str(uuid.uuid4()))
        presentations = WorkCardPresentations(session, store, ledger)
        assert presentations.create(presentation_id, saved.card_id).status == "created"
        events = []
        bridge = WsBridge(emit=events.append, paths=paths)
        repl = server._build_repl(session, paths, bridge)
        handler = WsSessionHandler(paths)
        request = {"type": "work-card.result.view", "session_id": session.conversation_id,
                   "presentation_id": presentation_id, "card_id": saved.card_id,
                   "result_id": "result-a", "requirement_id": req1["requirement_id"]}

        await handler._handle_incoming(request, repl, bridge)
        first = events[-1]
        assert first["type"] == "work-card.result.view.done" and first["status"] == "acknowledged"
        fact = presentations.get(presentation_id).fact
        assert [(v.result_id, v.requirement_id) for v in fact.result_views] == [("result-a", req1["requirement_id"])]
        assert fact.result_views[0].elapsed_evidence["ack_boot_id"] == "boot-protocol"
        assert fact.result_views[0].elapsed_evidence["ack_monotonic_ns"] == 9_000_000_000
        assert fact.acknowledged_at is None and ledger.get_card(saved.card_id)["viewed_at"] is None

        await handler._handle_incoming(request, repl, bridge)
        assert events[-1]["status"] == "already_acknowledged"
        assert presentations.get(presentation_id).fact == fact

        for altered in (
            {**request, "result_id": "result-b"},
            {**request, "result_id": "not-in-snapshot"},
            {**request, "requirement_id": req2["requirement_id"]},
            {**request, "session_id": "20261030-bbbbbbbb"},
            {**request, "presentation_id": str(uuid.uuid4())},
            {**request, "action": "reliable_reply"},
        ):
            await handler._handle_incoming(altered, repl, bridge)
            assert events[-1]["status"] != "acknowledged"
        assert presentations.get(presentation_id).fact == fact

        # An otherwise valid card identity is insufficient if its exact reference was never recorded in history.
        missing_history_id = str(uuid.uuid4())
        assert presentations.create(missing_history_id, saved.card_id).status == "created"
        await handler._handle_incoming({**request, "presentation_id": missing_history_id, "result_id": "result-b", "requirement_id": req2["requirement_id"]}, repl, bridge)
        assert events[-1]["status"] == "rejected"
        assert presentations.get(missing_history_id).fact.result_views == ()

    asyncio.run(run())



def test_result_view_uses_session_lease_while_turn_is_busy_and_serializes_session_switch(tmp_path, monkeypatch):
    async def run():
        paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace", data=tmp_path / "data", evolve=tmp_path / "evolve")
        paths.workspace.mkdir(parents=True)
        create_project(paths, "p")
        session_dir = paths.data / "sessions" / "20261030-bbbbbbbb"
        session_dir.mkdir(parents=True)
        session = Session("20261030-bbbbbbbb", session_dir, "synthetic", SessionMeta(), [], paths)
        bind_project_session(session, "p", plan_status="draft")
        session.save()
        store = WorkRecordStore(session)
        item = store.create_item(title="synthetic", scope={"kind": "project", "project_id": "p"})
        req = store.add_requirement(item["item_id"], "one", event_id="e1")
        store.begin_requirement_run(item["item_id"], req["requirement_id"], run_id="run", expected_current_run_id=None)
        store.record_result_if_current_run(item["item_id"], req["requirement_id"], run_id="run", result_id="result", status="completed", summary="done", verification={"status": "not_verified"})
        ledger = WorkCardLedger(session, store)
        saved = ledger.save_snapshot(str(uuid.uuid4()), build_project_work_record_cards(store, "p"))
        presentation_id = str(uuid.uuid4())
        session.append_sent_work_card_reference(presentation_id, saved.card_id, run_id=str(uuid.uuid4()))
        presentations = WorkCardPresentations(session, store, ledger)
        assert presentations.create(presentation_id, saved.card_id).status == "created"

        other_dir = paths.data / "sessions" / "20261030-cccccccc"
        other_dir.mkdir(parents=True)
        other = Session("20261030-cccccccc", other_dir, "synthetic", SessionMeta(), [], paths)
        events = []
        bridge = WsBridge(emit=events.append, paths=paths)
        bridge._turn_busy.set()
        repl = server._build_repl(session, paths, bridge)
        handler = WsSessionHandler(paths)
        turn_lock = asyncio.Lock()
        await turn_lock.acquire()
        monkeypatch.setattr(server, "TURN_LOCK", turn_lock)

        entered_ack = threading.Event()
        release_ack = threading.Event()
        original_ack = WorkCardPresentations.acknowledge_result
        def blocked_ack(self, *args, **kwargs):
            entered_ack.set()
            if not release_ack.wait(timeout=3):
                raise TimeoutError("test ACK release timed out")
            return original_ack(self, *args, **kwargs)
        monkeypatch.setattr(WorkCardPresentations, "acknowledge_result", blocked_ack)

        switched = asyncio.Event()
        original_dispatch = handler._dispatch
        async def fake_dispatch(message, bound_repl, _bridge, **kwargs):
            if message.get("type") == "session.open":
                bound_repl.session = other
                bound_repl.agent.session = other
                switched.set()
                return
            await original_dispatch(message, bound_repl, _bridge, **kwargs)
        monkeypatch.setattr(handler, "_dispatch", fake_dispatch)
        request = {"type": "work-card.result.view", "session_id": session.conversation_id,
                   "presentation_id": presentation_id, "card_id": saved.card_id,
                   "result_id": "result", "requirement_id": req["requirement_id"]}
        view_task = asyncio.create_task(handler._handle_incoming(request, repl, bridge))
        switch_task = None
        try:
            assert await asyncio.to_thread(entered_ack.wait, 1.5), "result.view waited behind the active TURN_LOCK"
            switch_task = asyncio.create_task(handler._handle_incoming({"type": "session.open"}, repl, bridge))
            for _ in range(100):
                if bridge._session_binding_transitions:
                    break
                await asyncio.sleep(0.01)
            assert bridge._session_binding_transitions == 1
            await handler._handle_incoming(request, repl, bridge)
            assert events[-1]["status"] == "session_unavailable"
            assert presentations.get(presentation_id).fact.result_views == ()
            await asyncio.sleep(0.05)
            assert repl.session is session and not switched.is_set()
            release_ack.set()
            await asyncio.wait_for(view_task, timeout=2)
            assert events[-1]["status"] == "acknowledged"
            assert not switch_task.done(), "switch must continue waiting for the original active turn"
            turn_lock.release()
            await asyncio.wait_for(switch_task, timeout=2)
            assert switched.is_set() and repl.session is other
            fact = presentations.get(presentation_id).fact
            assert [(v.result_id, v.requirement_id) for v in fact.result_views] == [("result", req["requirement_id"])]
        finally:
            release_ack.set()
            if turn_lock.locked():
                turn_lock.release()
            if not view_task.done():
                await asyncio.gather(view_task, return_exceptions=True)
            if switch_task is not None and not switch_task.done():
                await asyncio.gather(switch_task, return_exceptions=True)

    asyncio.run(run())



def test_cancelled_result_view_keeps_lease_until_thread_finishes_before_command_rebind(tmp_path, monkeypatch):
    async def run():
        paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace", data=tmp_path / "data", evolve=tmp_path / "evolve")
        paths.workspace.mkdir(parents=True)
        create_project(paths, "p")
        session_dir = paths.data / "sessions" / "20261030-dddddddd"
        session_dir.mkdir(parents=True)
        session = Session("20261030-dddddddd", session_dir, "synthetic", SessionMeta(), [], paths)
        bind_project_session(session, "p", plan_status="draft")
        session.save()
        store = WorkRecordStore(session)
        item = store.create_item(title="synthetic", scope={"kind": "project", "project_id": "p"})
        req = store.add_requirement(item["item_id"], "one", event_id="e1")
        store.begin_requirement_run(item["item_id"], req["requirement_id"], run_id="run", expected_current_run_id=None)
        store.record_result_if_current_run(item["item_id"], req["requirement_id"], run_id="run", result_id="result", status="completed", summary="done", verification={"status": "not_verified"})
        ledger = WorkCardLedger(session, store)
        saved = ledger.save_snapshot(str(uuid.uuid4()), build_project_work_record_cards(store, "p"))
        presentation_id = str(uuid.uuid4())
        session.append_sent_work_card_reference(presentation_id, saved.card_id, run_id=str(uuid.uuid4()))
        presentations = WorkCardPresentations(session, store, ledger)
        assert presentations.create(presentation_id, saved.card_id).status == "created"
        other_dir = paths.data / "sessions" / "20261030-eeeeeeee"
        other_dir.mkdir(parents=True)
        other = Session("20261030-eeeeeeee", other_dir, "synthetic", SessionMeta(), [], paths)
        bridge = WsBridge(emit=lambda _event: None, paths=paths)
        repl = server._build_repl(session, paths, bridge)
        handler = WsSessionHandler(paths)
        turn_lock = asyncio.Lock()
        await turn_lock.acquire()
        monkeypatch.setattr(server, "TURN_LOCK", turn_lock)

        entered_io = threading.Event()
        release_io = threading.Event()
        original_ack = WorkCardPresentations.acknowledge_result
        def slow_ack(self, *args, **kwargs):
            entered_io.set()
            if not release_io.wait(timeout=4):
                raise TimeoutError("slow ACK test timed out")
            return original_ack(self, *args, **kwargs)
        monkeypatch.setattr(WorkCardPresentations, "acknowledge_result", slow_ack)

        rebound = asyncio.Event()
        original_dispatch = handler._dispatch
        async def fake_dispatch(message, bound_repl, _bridge, **kwargs):
            if message.get("type") == "command":
                bound_repl.session = other
                bound_repl.agent.session = other
                rebound.set()
                return
            await original_dispatch(message, bound_repl, _bridge, **kwargs)
        monkeypatch.setattr(handler, "_dispatch", fake_dispatch)
        request = {"type": "work-card.result.view", "session_id": session.conversation_id,
                   "presentation_id": presentation_id, "card_id": saved.card_id,
                   "result_id": "result", "requirement_id": req["requirement_id"]}
        view_task = asyncio.create_task(handler._handle_incoming(request, repl, bridge))
        command_task = None
        try:
            assert await asyncio.to_thread(entered_io.wait, 1.5)
            view_task.cancel()
            await asyncio.sleep(0.05)
            assert not view_task.done(), "cancelled handler released its lease while worker I/O was still running"
            command_task = asyncio.create_task(handler._handle_incoming(
                {"type": "command", "name": "new"}, repl, bridge,
            ))
            turn_lock.release()
            await asyncio.sleep(0.1)
            assert not rebound.is_set(), "command-path rebind bypassed the result-view lease"
            assert repl.session is session
            release_io.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(view_task, timeout=2)
            await asyncio.wait_for(command_task, timeout=2)
            assert rebound.is_set() and repl.session is other
            fact = presentations.get(presentation_id).fact
            assert [(v.result_id, v.requirement_id) for v in fact.result_views] == [("result", req["requirement_id"])]
        finally:
            release_io.set()
            if turn_lock.locked():
                turn_lock.release()
            for task in (view_task, command_task):
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())



def test_session_rebinding_chat_commands_are_classified_for_binding_transition():
    assert server._line_may_rebind_session("new")
    assert server._line_may_rebind_session("\u65b0\u4f1a\u8bdd")
    assert server._line_may_rebind_session("\u9879\u76ee \u5207\u6362 demo")
    assert server._line_may_rebind_session("project open demo")
    assert not server._line_may_rebind_session("project list")
    assert not server._line_may_rebind_session("换主题")

def test_internal_result_view_helper_requires_host_binding_for_reliable_reply(tmp_path):
    from work_card_reply_binding import HostReplyBindingAuthority

    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace", data=tmp_path / "data", evolve=tmp_path / "evolve")
    paths.workspace.mkdir(parents=True)
    create_project(paths, "p")
    session_dir = paths.data / "sessions" / "20261030-fffffff1"
    session_dir.mkdir(parents=True)
    session = Session("20261030-fffffff1", session_dir, "synthetic", SessionMeta(), [], paths)
    bind_project_session(session, "p", plan_status="draft")
    session.save()
    store = WorkRecordStore(session)
    item = store.create_item(title="synthetic", scope={"kind": "project", "project_id": "p"})
    requirement = store.add_requirement(item["item_id"], "one", event_id="helper-e1")
    store.begin_requirement_run(item["item_id"], requirement["requirement_id"], run_id="helper-run", expected_current_run_id=None)
    store.record_result_if_current_run(item["item_id"], requirement["requirement_id"], run_id="helper-run",
        result_id="helper-result", status="completed", summary="reply target", verification={"status": "not_verified"})
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(str(uuid.uuid4()), build_project_work_record_cards(store, "p"))
    presentation_id = str(uuid.uuid4())
    session.append_sent_work_card_reference(presentation_id, saved.card_id, run_id=str(uuid.uuid4()))
    presentations = WorkCardPresentations(session, store, ledger)
    assert presentations.create(presentation_id, saved.card_id).status == "created"

    target = {"session_id": session.conversation_id, "presentation_id": presentation_id, "card_id": saved.card_id,
              "result_id": "helper-result", "requirement_id": requirement["requirement_id"]}
    text = "What does this persisted result mean?"
    authority = HostReplyBindingAuthority(session, lambda: session)
    ingress = authority.prepare_user_message_binding(text, target)
    assert ingress is not None
    message_index = len(session.messages)
    session.append_message({"role": "user", "content": text})
    capability = authority.bind_persisted_user_message(ingress, message_index)
    assert capability is not None
    message_count = len(session.messages)
    assert server._record_work_card_result_view(
        session=session, store=store, ledger=ledger, presentations=presentations, **target,
        action="reliable_reply",
    ) == "rejected"

    assert server._record_work_card_result_view(
        session=session, store=store, ledger=ledger, presentations=presentations, **target,
        action="reliable_reply", reply_binding_authority=authority,
        reply_binding=capability, reply_text=text,
    ) == "acknowledged"
    assert len(session.messages) == message_count
    fact = presentations.get(presentation_id).fact
    assert [(view.result_id, view.requirement_id, view.action) for view in fact.result_views] == [
        ("helper-result", requirement["requirement_id"], "reliable_reply"),
    ]
    assert server._record_work_card_result_view(
        session=session, store=store, ledger=ledger, presentations=presentations, **target,
        action="reliable_reply",
    ) == "rejected"




