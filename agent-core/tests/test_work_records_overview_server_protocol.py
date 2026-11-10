from __future__ import annotations

import asyncio
import json
import sys
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import server
import work_records_overview_api
from paths import AgentPaths
from server import WsBridge, WsSessionHandler
from session import Session, SessionMeta
from work_records import WorkRecordStore


def _session(tmp_path: Path, session_id: str) -> tuple[Session, WorkRecordStore]:
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    session_dir = paths.data / "sessions" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    session = Session(session_id, session_dir, "synthetic overview", SessionMeta(), [], paths)
    return session, WorkRecordStore(session)


def _request(session: Session, *, cursor=None, **extra):
    request = {
        "type": "work-records.overview.query",
        "session_id": session.conversation_id,
        "request_id": str(uuid.uuid4()),
    }
    if cursor is not None:
        request["cursor"] = cursor
    request.update(extra)
    return request


def _response(events, request_id):
    return next(event for event in reversed(events)
                if event.get("type") == "work-records.overview.result"
                and event.get("request_id") == request_id)


def test_ws_overview_query_correlates_pages_and_reads_only_exact_current_session(tmp_path, monkeypatch):
    async def run():
        session, store = _session(tmp_path, "20261101-a1a1a1a1")
        foreign, foreign_store = _session(tmp_path, "20261101-b2b2b2b2")
        for index in range(8):
            row = store.create_item(title=f"current-{index}", scope={"kind": "independent"})
            store.add_requirement(row["item_id"], f"current requirement {index}", event_id=f"cur-{index}")
        secret = foreign_store.create_item(title="FOREIGN_SECRET", scope={"kind": "independent"})
        foreign_store.add_requirement(secret["item_id"], "foreign requirement", event_id="foreign-secret")
        events = []
        bridge = WsBridge(emit=events.append, paths=session.paths)
        handler = WsSessionHandler(session.paths)
        repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
        calls = []
        real_query = work_records_overview_api.query_work_records_overview
        def observe(bound_session, **kwargs):
            calls.append(bound_session)
            return real_query(bound_session, **kwargs)
        monkeypatch.setattr(work_records_overview_api, "query_work_records_overview", observe)

        first_request = _request(session)
        await handler._handle_incoming(first_request, repl, bridge)
        first = _response(events, first_request["request_id"])
        assert first["session_id"] == session.conversation_id
        assert first["state"] == "visible"
        assert first["visibility_recheck_after_ms"] == 10 * 60 * 1000
        assert len(first["groups"]) == 6
        assert first["page"]["page_size"] == 6
        assert first["page"]["total_visible_groups"] == 8
        assert first["page"]["total_visible_items"] == 8
        counts = first["visibility_counts"]
        assert counts["visible_groups"] == 8 and counts["visible_items"] == 8
        assert counts["attention_visible_items"] >= 0
        assert counts["attention_visible_groups"] >= 0
        assert counts["attention_visible_items"] <= counts["visible_items"]
        assert first["page"]["has_more"] is True
        assert "FOREIGN_SECRET" not in json.dumps(first)

        second_request = _request(session, cursor=first["page"]["next_cursor"])
        await handler._handle_incoming(second_request, repl, bridge)
        second = _response(events, second_request["request_id"])
        assert second["state"] == "visible"
        assert len(second["groups"]) == 2
        assert second["page"]["offset"] == 6

        store.create_item(title="change invalidates cursor", scope={"kind": "independent"})
        expired_request = _request(session, cursor=first["page"]["next_cursor"])
        await handler._handle_incoming(expired_request, repl, bridge)
        expired = _response(events, expired_request["request_id"])
        assert expired["state"] == "expired"
        assert expired["groups"] == []
        assert expired["page"]["next_cursor"] is None
        assert calls and all(bound is session for bound in calls)

    asyncio.run(run())


def test_ws_overview_no_records_error_and_invalid_session_are_explicit(tmp_path, monkeypatch):
    async def run():
        session, store = _session(tmp_path, "20261101-c3c3c3c3")
        foreign, _ = _session(tmp_path, "20261101-d4d4d4d4")
        events = []
        bridge = WsBridge(emit=events.append, paths=session.paths)
        handler = WsSessionHandler(session.paths)
        repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
        real_query = work_records_overview_api.query_work_records_overview
        calls = []
        def observe(bound_session, **kwargs):
            calls.append(bound_session)
            return real_query(bound_session, **kwargs)
        monkeypatch.setattr(work_records_overview_api, "query_work_records_overview", observe)

        empty_request = _request(session)
        await handler._handle_incoming(empty_request, repl, bridge)
        empty = _response(events, empty_request["request_id"])
        assert empty["state"] == "no_records"
        assert empty["visibility_recheck_after_ms"] == 10 * 60 * 1000
        assert empty["groups"] == []

        mismatch = _request(session)
        mismatch["session_id"] = foreign.conversation_id
        await handler._handle_incoming(mismatch, repl, bridge)
        rejected = _response(events, mismatch["request_id"])
        assert rejected["state"] == "error"
        assert rejected["reason"] == "session_mismatch"
        assert calls == [session]

        row = store.create_item(title="corrupt source", scope={"kind": "independent"})
        store._path.write_text("{broken", encoding="utf-8")
        corrupt_request = _request(session)
        await handler._handle_incoming(corrupt_request, repl, bridge)
        corrupt = _response(events, corrupt_request["request_id"])
        assert corrupt["state"] == "error"
        assert corrupt["reason"] == "source_invalid"
        assert corrupt["groups"] == []

        bad_request = _request(session, page_size=True)
        await handler._handle_incoming(bad_request, repl, bridge)
        invalid = _response(events, bad_request["request_id"])
        assert invalid["state"] == "error"
        assert invalid["reason"] == "invalid_request"

    asyncio.run(run())


def test_ws_overview_cancel_suppresses_partial_result_and_keeps_request_correlation(tmp_path, monkeypatch):
    async def run():
        session, _store = _session(tmp_path, "20261101-e5e5e5e5")
        events = []
        bridge = WsBridge(emit=events.append, paths=session.paths)
        handler = WsSessionHandler(session.paths)
        repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
        started = threading.Event()
        release = threading.Event()
        def slow_query(_session, **_kwargs):
            started.set()
            release.wait(timeout=3)
            return {"state": "visible", "groups": [{"id": "must-not-escape"}],
                    "unread_status": "known", "unread_important_results": [],
                    "visibility_limitations": [], "snapshot_id": "s", "page": {}}
        monkeypatch.setattr(work_records_overview_api, "query_work_records_overview", slow_query)
        request = _request(session)
        bridge._turn_busy.set()
        task = asyncio.create_task(handler._handle_incoming(request, repl, bridge))
        assert await asyncio.to_thread(started.wait, 2)
        await handler._handle_incoming({"type": "turn.cancel"}, repl, bridge)
        release.set()
        await asyncio.wait_for(task, timeout=3)
        result = _response(events, request["request_id"])
        assert result["session_id"] == session.conversation_id
        assert result["state"] == "cancelled"
        assert result["groups"] == []
        assert result["reason"] == "cancelled"
        assert result["visibility_counts"]["visible_groups"] == 0
        assert bridge.cancel_event.is_set()

    asyncio.run(run())


def test_managed_readonly_ws_rejects_overview_without_calling_api(tmp_path, monkeypatch):
    class Context:
        installation_id = "install"
        release_id = "release"
        binding_digest = "binding"
        runtime_digest = "runtime"
        nonce = "nonce"
        def revalidate(self):
            return None

    class WebSocket:
        def __init__(self, messages):
            self.messages = iter(messages)
            self.sent = []
        def __aiter__(self):
            return self
        async def __anext__(self):
            try:
                return next(self.messages)
            except StopIteration:
                raise StopAsyncIteration
        async def send(self, payload):
            self.sent.append(json.loads(payload))

    async def run():
        paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
        handler = WsSessionHandler(paths, managed_readonly_context=Context())
        called = []
        monkeypatch.setattr(work_records_overview_api, "query_work_records_overview",
                            lambda *_args, **_kwargs: called.append(True))
        request_id = str(uuid.uuid4())
        ws = WebSocket([json.dumps({
            "type": "work-records.overview.query", "session_id": "managed", "request_id": request_id,
        })])
        await handler._handle_managed_readonly(ws)
        assert called == []
        assert ws.sent[0]["type"] == "work-records.overview.result"
        assert ws.sent[0]["state"] == "error"
        assert ws.sent[0]["reason"] == "managed_readonly_rejected"
        assert ws.sent[0]["request_id"] == request_id

    asyncio.run(run())


def test_ws_overview_query_returns_while_turn_lock_is_held(tmp_path, monkeypatch):
    async def run():
        session, _store = _session(tmp_path, "20261101-f6f6f6f6")
        events = []
        bridge = WsBridge(emit=events.append, paths=session.paths)
        handler = WsSessionHandler(session.paths)
        repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
        lock = asyncio.Lock()
        monkeypatch.setattr(server, "TURN_LOCK", lock)
        turn_started = asyncio.Event()
        release_turn = asyncio.Event()
        async def long_turn_holding_dispatch_lock():
            async with lock:
                turn_started.set()
                await release_turn.wait()
        turn_task = asyncio.create_task(long_turn_holding_dispatch_lock())
        await turn_started.wait()
        monkeypatch.setattr(work_records_overview_api, "query_work_records_overview", lambda *_args, **_kwargs: {
            "state": "no_records", "groups": [], "unread_important_results": [],
            "unread_status": "known", "visibility_limitations": [], "visibility_counts": {
                "visible_groups": 0, "visible_items": 0, "collapsed_groups": 0, "collapsed_items": 0,
                "attention_visible_items": 0, "attention_visible_groups": 0,
            },
            "snapshot_id": None, "page": {"page_size": 6, "offset": 0,
                                               "total_visible_groups": 0, "total_visible_items": 0,
                                               "has_more": False, "next_cursor": None},
        })
        request = _request(session)
        await asyncio.wait_for(handler._handle_incoming(request, repl, bridge), timeout=0.25)
        assert not turn_task.done()  # the active turn still owns TURN_LOCK
        result = _response(events, request["request_id"])
        assert result["state"] == "no_records"
        assert result["session_id"] == session.conversation_id
        release_turn.set()
        await asyncio.wait_for(turn_task, timeout=1)

    asyncio.run(run())


def test_session_switch_cancels_inflight_overview_without_cross_session_response(tmp_path, monkeypatch):
    async def run():
        session, _store = _session(tmp_path, "20261101-f6f6f6f6")
        other, _other_store = _session(tmp_path, "20261101-07070707")
        events = []
        bridge = WsBridge(emit=events.append, paths=session.paths)
        handler = WsSessionHandler(session.paths)
        repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
        started = threading.Event()
        release = threading.Event()
        def slow_query(bound_session, **_kwargs):
            assert bound_session is session
            started.set()
            release.wait(timeout=3)
            return {"state": "visible", "groups": [{"id": "FOREIGN_MUST_NOT_ESCAPE"}],
                    "unread_status": "known", "unread_important_results": [],
                    "visibility_limitations": [], "visibility_counts": {
                        "visible_groups": 0, "visible_items": 0, "collapsed_groups": 0, "collapsed_items": 0,
                        "attention_visible_items": 0, "attention_visible_groups": 0,
                    }, "snapshot_id": "s",
                    "page": {"page_size": 6, "offset": 0, "total_visible_groups": 1,
                             "total_visible_items": 1, "has_more": False, "next_cursor": None}}
        monkeypatch.setattr(work_records_overview_api, "query_work_records_overview", slow_query)
        switched = []
        original_dispatch = handler._dispatch
        async def fake_dispatch(message, bound_repl, bound_bridge):
            if message.get("type") == "session.open":
                switched.append(True)
                bound_repl.session = other
                bound_repl.agent.session = other
                return
            await original_dispatch(message, bound_repl, bound_bridge)
        monkeypatch.setattr(handler, "_dispatch", fake_dispatch)
        request = _request(session)
        query_task = asyncio.create_task(handler._handle_incoming(request, repl, bridge))
        assert await asyncio.to_thread(started.wait, 2)
        switch_task = asyncio.create_task(handler._handle_incoming({"type": "session.open"}, repl, bridge))
        try:
            await asyncio.wait_for(switch_task, timeout=0.25)
            assert switched == [True]
        finally:
            release.set()
            await asyncio.wait_for(query_task, timeout=3)
        result = _response(events, request["request_id"])
        assert result["session_id"] == session.conversation_id
        assert result["state"] == "cancelled"
        assert result["groups"] == []
        assert "FOREIGN_MUST_NOT_ESCAPE" not in json.dumps(result)
        assert repl.session is other

    asyncio.run(run())



def test_overview_started_during_session_binding_transition_is_rejected_without_read(tmp_path, monkeypatch):
    async def run():
        session, _store = _session(tmp_path, "20261101-12121212")
        other, _other_store = _session(tmp_path, "20261101-13131313")
        events = []
        bridge = WsBridge(emit=events.append, paths=session.paths)
        handler = WsSessionHandler(session.paths)
        repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
        switch_started = asyncio.Event()
        finish_switch = asyncio.Event()
        calls = []
        original_dispatch = handler._dispatch
        async def fake_dispatch(message, bound_repl, bound_bridge):
            if message.get("type") == "session.open":
                switch_started.set()
                await finish_switch.wait()
                bound_repl.session = other
                bound_repl.agent.session = other
                return
            await original_dispatch(message, bound_repl, bound_bridge)
        monkeypatch.setattr(handler, "_dispatch", fake_dispatch)
        monkeypatch.setattr(work_records_overview_api, "query_work_records_overview",
                            lambda *_args, **_kwargs: calls.append("read"))
        switch_task = asyncio.create_task(handler._handle_incoming({"type": "session.open"}, repl, bridge))
        await switch_started.wait()
        request = _request(session)
        await handler._handle_incoming(request, repl, bridge)
        result = _response(events, request["request_id"])
        assert result["state"] == "error" and result["reason"] == "query_busy"
        assert result["session_id"] == session.conversation_id
        assert calls == []
        finish_switch.set()
        await switch_task
        assert repl.session is other

    asyncio.run(run())


def test_ws_cross_item_dependency_requires_confirmation_persists_and_blocks_start(tmp_path):
    async def run():
        session, store = _session(tmp_path, "20261108-cross-dependency")
        prerequisite_item = store.create_item(title="Synthetic prerequisite", scope={"kind": "independent"})
        prerequisite = store.add_requirement(prerequisite_item["item_id"], "Synthetic prerequisite task", event_id="dependency-prerequisite")
        dependent_item = store.create_item(title="Synthetic dependent", scope={"kind": "independent"})
        dependent = store.add_requirement(dependent_item["item_id"], "Synthetic dependent task", event_id="dependency-dependent")
        events, previews = [], []
        bridge = WsBridge(emit=events.append, paths=session.paths)
        handler = WsSessionHandler(session.paths)
        repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))

        def request(choice):
            request_id = str(uuid.uuid4())
            bridge.confirm_with_receipt = lambda preview, _allow: (previews.append(preview) or (choice, "synthetic-receipt"))
            return {"type": "work-record.control", "session_id": session.conversation_id,
                    "request_id": request_id, "event_id": str(uuid.uuid4()), "idempotency_key": request_id,
                    "expected_revision": store.with_locked_data(lambda data: data["revision"]),
                    "item_id": dependent_item["item_id"], "requirement_id": dependent["requirement_id"],
                    "expected_status": "pending", "action": "set_dependencies", "depends_on": [],
                    "depends_on_refs": [{"item_id": prerequisite_item["item_id"],
                                         "requirement_id": prerequisite["requirement_id"]}]}

        await handler._handle_incoming(request("n"), repl, bridge)
        assert events[-1]["type"] == "work-record.control.done"
        assert events[-1]["status"] == "rejected"
        assert "Synthetic prerequisite" in previews[-1] and "Synthetic prerequisite task" in previews[-1]
        assert WorkRecordStore(session).get_item(dependent_item["item_id"])["requirements"][0].get("depends_on_refs", []) == []

        await handler._handle_incoming(request("y"), repl, bridge)
        assert events[-1]["status"] == "applied", events[-1].get("message")
        persisted = WorkRecordStore(session).get_item(dependent_item["item_id"])
        assert persisted["requirements"][0]["depends_on_refs"] == [{
            "item_id": prerequisite_item["item_id"], "requirement_id": prerequisite["requirement_id"]}]
        from work_records import WorkRecordConflict
        with pytest.raises(WorkRecordConflict, match="depend"):
            store.begin_requirement_run(dependent_item["item_id"], dependent["requirement_id"],
                                       run_id="synthetic-blocked-run", expected_current_run_id=None)
        assert WorkRecordStore(session).get_item(dependent_item["item_id"])["requirements"][0]["status"] == "pending"

    asyncio.run(run())


def _control_request(session, store, item_id, requirement_id, action, **extra):
    request_id = str(uuid.uuid4())
    request = {
        "type": "work-record.control", "session_id": session.conversation_id,
        "request_id": request_id, "item_id": item_id, "requirement_id": requirement_id,
        "action": action, "expected_revision": store.with_locked_data(lambda data: data["revision"]),
        "event_id": str(uuid.uuid4()), "idempotency_key": request_id,
    }
    if action != "copy_requirement_to_new_item":
        request["expected_status"] = "pending"
    request.update(extra)
    return request


def _control_done(events, request):
    return next(event for event in reversed(events)
                if event.get("type") == "work-record.control.done"
                and event.get("request_id") == request["request_id"])


def test_ws_cancel_execution_signals_only_current_local_worker_and_does_not_claim_terminal(tmp_path):
    async def run():
        session, _unused_store = _session(tmp_path, "20261108-cancel-ws")
        session.meta.project_id = "project-alpha"
        store = WorkRecordStore(session)
        item = store.create_item(title="Synthetic running task", scope={"kind": "project", "project_id": "project-alpha"})
        requirement = store.add_requirement(item["item_id"], "Synthetic running requirement", event_id="cancel-req")
        store.begin_requirement_run(item["item_id"], requirement["requirement_id"],
                                    run_id="synthetic-worker-run", expected_current_run_id=None)
        signals = []
        host = SimpleNamespace(request_cancel_for_record=lambda bound_session, **identity:
                               signals.append((bound_session, identity)) or "signal_set")
        repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session, project_worker_host=host))
        events = []
        bridge = WsBridge(emit=events.append, paths=session.paths)
        handler = WsSessionHandler(session.paths)
        request = _control_request(session, store, item["item_id"], requirement["requirement_id"],
                                  "cancel_execution", expected_status="in_progress")

        await handler._handle_incoming(request, repl, bridge)

        done = _control_done(events, request)
        assert done["status"] == "applied"
        assert "不代表它已停止" in done["message"]
        assert signals == [(session, {"item_id": item["item_id"],
                                      "requirement_id": requirement["requirement_id"]})]
        assert WorkRecordStore(session).get_item(item["item_id"])["requirements"][0]["status"] == "in_progress"

        remote_request = {**request, "request_id": str(uuid.uuid4()), "idempotency_key": str(uuid.uuid4()),
                          "event_id": str(uuid.uuid4()), "project_id": "project-beta",
                          "expected_source_revision": store.with_locked_data(lambda data: data["revision"])}
        await handler._handle_incoming(remote_request, repl, bridge)
        assert _control_done(events, remote_request)["status"] == "rejected"
        assert len(signals) == 1, "remote cancellation must never reach the local host"

    asyncio.run(run())


def test_ws_revise_requirement_confirms_text_and_preserves_existing_history(tmp_path):
    async def run():
        session, store = _session(tmp_path, "20261108-revise-confirm")
        item = store.create_item(title="Synthetic revision", scope={"kind": "independent"})
        requirement = store.add_requirement(item["item_id"], "Old synthetic text", event_id="old-requirement")
        run_id = "synthetic-completed-run"
        store.begin_requirement_run(item["item_id"], requirement["requirement_id"],
                                   run_id=run_id, expected_current_run_id=None)
        store.record_result_if_current_run(
            item["item_id"], requirement["requirement_id"], run_id=run_id,
            result_id="synthetic-result", status="completed", summary="Synthetic evidence summary",
            verification={"status": "passed", "evidence_refs": ["synthetic://evidence"]},
            sources=[{"kind": "result", "id": "synthetic-source-1"}],
        )
        before = store.get_item(item["item_id"])
        events, previews = [], []
        bridge = WsBridge(emit=events.append, paths=session.paths)
        bridge.confirm_with_receipt = lambda preview, _allow: (previews.append(preview) or "y", "receipt")
        handler = WsSessionHandler(session.paths)
        repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
        request = _control_request(session, store, item["item_id"], requirement["requirement_id"],
                                   "revise_requirement", expected_status="completed",
                                   new_text="New synthetic text")

        await handler._handle_incoming(request, repl, bridge)

        assert _control_done(events, request)["status"] == "applied"
        assert "原文：Old synthetic text" in previews[0]
        assert "新文：New synthetic text" in previews[0]
        after = WorkRecordStore(session).get_item(item["item_id"])
        assert after["requirements"][0]["text"] == "New synthetic text"
        assert after["runs"] == before["runs"]
        assert after["runs"][0]["verification"]["evidence_refs"] == ["synthetic://evidence"]
        assert after["runs"][0]["sources"] == [{"kind": "result", "id": "synthetic-source-1"}]
        assert after["history"][:-1] == before["history"]
        assert after["history"][-1]["kind"] == "requirement_revised"

    asyncio.run(run())


@pytest.mark.parametrize("confirmation_outcome", ["deny", "disconnect", "session_switch", "stale_revision"])
def test_ws_revise_requirement_confirmation_failures_leave_record_unchanged(
        tmp_path, confirmation_outcome):
    async def run():
        session, store = _session(tmp_path, f"20261108-revise-{confirmation_outcome}")
        other, _ = _session(tmp_path, f"20261108-other-{confirmation_outcome}")
        item = store.create_item(title="Synthetic revision", scope={"kind": "independent"})
        requirement = store.add_requirement(item["item_id"], "Old synthetic text", event_id="old-requirement")
        before = store.with_locked_data(lambda data: data)
        events = []
        bridge = WsBridge(emit=events.append, paths=session.paths)
        repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
        def confirm(_preview, _allow):
            if confirmation_outcome == "disconnect":
                bridge._connection_closed.set()
            elif confirmation_outcome == "session_switch":
                repl.session = other
            elif confirmation_outcome == "stale_revision":
                store.create_item(title="Concurrent synthetic item", scope={"kind": "independent"})
            return ("n" if confirmation_outcome == "deny" else "y", "receipt")
        bridge.confirm_with_receipt = confirm
        handler = WsSessionHandler(session.paths)
        request = _control_request(session, store, item["item_id"], requirement["requirement_id"],
                                   "revise_requirement", new_text="New synthetic text")

        await handler._handle_incoming(request, repl, bridge)

        assert _control_done(events, request)["status"] == "rejected"
        current = store.with_locked_data(lambda data: data)
        if confirmation_outcome == "stale_revision":
            assert current["revision"] == before["revision"] + 1
            assert store.get_item(item["item_id"])["requirements"][0]["text"] == "Old synthetic text"
        else:
            assert current == before

    asyncio.run(run())


@pytest.mark.parametrize("confirmation_outcome", ["deny", "disconnect", "session_switch", "stale_revision"])
def test_ws_copy_requirement_confirmation_failures_do_not_copy_or_alter_source(
        tmp_path, confirmation_outcome):
    async def run():
        session, store = _session(tmp_path, f"20261108-copy-{confirmation_outcome}")
        other, _ = _session(tmp_path, f"20261108-copy-other-{confirmation_outcome}")
        item = store.create_item(title="Synthetic source", scope={"kind": "independent"})
        requirement = store.add_requirement(item["item_id"], "Synthetic source text", event_id="source-requirement")
        before_item = store.get_item(item["item_id"])
        before_revision = store.with_locked_data(lambda data: data["revision"])
        events = []
        bridge = WsBridge(emit=events.append, paths=session.paths)
        repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
        def confirm(_preview, _allow):
            if confirmation_outcome == "disconnect":
                bridge._connection_closed.set()
            elif confirmation_outcome == "session_switch":
                repl.session = other
                repl.agent.session = other
            elif confirmation_outcome == "stale_revision":
                store.create_item(title="Concurrent synthetic item", scope={"kind": "independent"})
            return ("n" if confirmation_outcome == "deny" else "y", "receipt")
        bridge.confirm_with_receipt = confirm
        handler = WsSessionHandler(session.paths)
        request = _control_request(session, store, item["item_id"], requirement["requirement_id"],
                                   "copy_requirement_to_new_item")

        await handler._handle_incoming(request, repl, bridge)

        assert _control_done(events, request)["status"] == "rejected"
        assert store.get_item(item["item_id"]) == before_item
        items = store.with_locked_data(lambda data: data["items"])
        assert len(items) == (2 if confirmation_outcome == "stale_revision" else 1)
        if confirmation_outcome == "stale_revision":
            assert store.with_locked_data(lambda data: data["revision"]) == before_revision + 1

    asyncio.run(run())


def test_ws_copy_requirement_approval_creates_independent_copy_and_preserves_source(tmp_path):
    async def run():
        session, store = _session(tmp_path, "20261108-copy-approved")
        item = store.create_item(title="Synthetic source", scope={"kind": "independent"})
        requirement = store.add_requirement(item["item_id"], "Synthetic source text", event_id="source-requirement")
        source_before = store.get_item(item["item_id"])
        previews, events = [], []
        bridge = WsBridge(emit=events.append, paths=session.paths)
        bridge.confirm_with_receipt = lambda preview, _allow: (previews.append(preview) or "y", "receipt")
        handler = WsSessionHandler(session.paths)
        repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
        request = _control_request(session, store, item["item_id"], requirement["requirement_id"],
                                   "copy_requirement_to_new_item")

        await handler._handle_incoming(request, repl, bridge)

        assert _control_done(events, request)["status"] == "applied"
        assert "Synthetic source text" in previews[0]
        assert store.get_item(item["item_id"]) == source_before
        copied = [row for row in store.with_locked_data(lambda data: data["items"])
                  if row["item_id"] != item["item_id"]]
        assert len(copied) == 1
        assert copied[0]["scope"] == {"kind": "independent", "object_ref": None}
        assert copied[0]["requirements"][0]["text"] == "Synthetic source text"
        assert copied[0]["requirements"][0]["status"] == "pending"
        copied_event = next(event for event in copied[0]["history"]
                            if event["kind"] == "requirement_copied_to_new_item")
        assert copied_event["payload"]["source_item_id"] == item["item_id"]
        assert copied_event["payload"]["source_requirement_id"] == requirement["requirement_id"]

    asyncio.run(run())


def test_ws_remote_cancel_execution_confirms_and_signals_parent_host(tmp_path):
    async def run():
        from project_switch import record_project_session
        from session import create_new

        paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
        paths.workspace.mkdir(parents=True, exist_ok=True)
        paths.evolve.mkdir(parents=True, exist_ok=True)
        (paths.evolve / "_index.core.toml").write_text("", encoding="utf-8")
        parent = create_new(paths, conversation_id="20261109-aaaaaaaa")
        project_id = "project-beta"
        (paths.workspace / project_id).mkdir(parents=True)
        (paths.workspace / project_id / "PROJECT.md").write_text("# beta\n", encoding="utf-8")
        source_session = Session(
            "20261109-bbbbbbbb", paths.data / "sessions" / "20261109-bbbbbbbb", "remote source",
            SessionMeta(project_id=project_id), [], paths,
        )
        source_session.session_dir.mkdir(parents=True, exist_ok=True)
        source_session.save()
        record_project_session(paths, project_id, source_session.conversation_id)
        source_store = WorkRecordStore(source_session)
        item = source_store.create_item(title="Remote run", scope={"kind": "project", "project_id": project_id})
        requirement = source_store.add_requirement(item["item_id"], "Remote task", event_id="remote-req")
        source_store.begin_requirement_run(
            item["item_id"], requirement["requirement_id"],
            run_id="remote-worker-run", expected_current_run_id=None,
        )
        from multi_project_work_record_authorization import authorize_project_record_sources
        from threading import Event

        authorized = authorize_project_record_sources(
            parent, [project_id], confirm_fn=lambda *_: "y", cancel_event=Event(),
        )
        import multi_project_work_record_authorization as authorization_module
        import project_worker_host as project_worker_host_module

        authorization_module.authorize_project_record_sources = lambda *_args, **_kwargs: authorized
        project_worker_host_module._resolve_bound_project = (
            lambda paths, bound_project_id: paths.workspace / bound_project_id
        )

        async def _inline_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        import server as server_module

        original_to_thread = asyncio.to_thread
        asyncio.to_thread = _inline_to_thread
        server_module.asyncio.to_thread = _inline_to_thread
        signals = []
        host = SimpleNamespace(request_cancel_for_record=lambda bound_session, **identity:
                               signals.append((bound_session, identity)) or "signal_set")
        events = []
        previews = []
        bridge = WsBridge(emit=events.append, paths=parent.paths)
        bridge.confirm_with_receipt = lambda preview, _allow: (previews.append(preview) or "y", "receipt")
        handler = WsSessionHandler(parent.paths)
        repl = SimpleNamespace(session=parent, agent=SimpleNamespace(session=parent, project_worker_host=host))
        request_id = str(uuid.uuid4())
        request = {
            "type": "work-record.control", "session_id": parent.conversation_id,
            "request_id": request_id, "item_id": item["item_id"],
            "requirement_id": requirement["requirement_id"], "project_id": project_id,
            "action": "cancel_execution", "expected_status": "in_progress",
            "expected_revision": 0,
            "expected_source_revision": source_store.with_locked_data(lambda data: data["revision"]),
            "event_id": str(uuid.uuid4()), "idempotency_key": request_id,
        }
        try:
            await handler._handle_incoming(request, repl, bridge)
        finally:
            asyncio.to_thread = original_to_thread
            server_module.asyncio.to_thread = original_to_thread
        done = next(event for event in reversed(events) if event.get("type") == "work-record.control.done")
        assert done["status"] == "applied"
        assert "远程 worker" in done["message"]
        assert previews and "来源 Session" in previews[0]
        assert signals == [(parent, {"item_id": item["item_id"], "requirement_id": requirement["requirement_id"]})]
        assert source_store.get_item(item["item_id"])["requirements"][0]["status"] == "in_progress"

    asyncio.run(run())
