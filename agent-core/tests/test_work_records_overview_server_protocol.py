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
        assert first["visibility_counts"] == {
            "visible_groups": 8, "visible_items": 8, "collapsed_groups": 0, "collapsed_items": 0,
        }
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
            "unread_status": "known", "visibility_limitations": [], "visibility_counts": {},
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
                    "visibility_limitations": [], "visibility_counts": {}, "snapshot_id": "s",
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
