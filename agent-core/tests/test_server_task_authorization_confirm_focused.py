import threading
import time

from server import WsBridge
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


def _running_bridge(paths, session, *, timeout=2.0):
    events = []
    bridge = WsBridge(emit=events.append, paths=paths, task_authorization_confirm_timeout=timeout)
    bridge._session_provider = lambda: session
    bridge.lifecycle_conversation_id = session.conversation_id
    bridge.lifecycle.start_or_activate("ordinary")
    return bridge, events


def _wait_for_event(events, event_type, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        match = next((e for e in events if e.get("type") == event_type), None)
        if match is not None:
            return match
        time.sleep(0.005)
    raise AssertionError(f"{event_type} was not emitted")


def _session(paths):
    return create_new(paths, conversation_id="20261106-aaaa0001")


def test_task_authorization_uses_server_bound_batch_confirmation_without_changing_ordinary_timeout():
    with temporary_agent_paths() as paths:
        session = _session(paths)
        bridge, events = _running_bridge(paths, session, timeout=2.0)
        assert bridge.confirm_timeout == 90.0
        result = []
        worker = threading.Thread(target=lambda: result.append(bridge.confirm_task_authorization('PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n{"tasks":[]}')))
        worker.start()
        request = _wait_for_event(events, "confirm.request")
        assert request["preview"].startswith("PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n")
        assert request["run_id"] == bridge.lifecycle.snapshot().run_id
        assert request["session_id"] == session.conversation_id
        assert bridge.deliver_confirm(request["request_id"], "y")
        worker.join(1)
        assert not worker.is_alive()
        assert result == ["y"]

        ordinary_result = []
        ordinary_worker = threading.Thread(target=lambda: ordinary_result.append(bridge.confirm_fn("ordinary", False)))
        ordinary_worker.start()
        deadline = time.monotonic() + 1
        while bridge._pending_confirm_id is None and time.monotonic() < deadline:
            time.sleep(0.005)
        ordinary_id = bridge._pending_confirm_id
        assert ordinary_id is not None
        assert not bridge.deliver_confirm(request["request_id"], "y")
        assert bridge.deliver_confirm(ordinary_id, "y")
        ordinary_worker.join(1)
        assert not ordinary_worker.is_alive()
        assert ordinary_result == ["y"]


def test_task_authorization_rejects_session_change_and_late_response():
    with temporary_agent_paths() as paths:
        session = _session(paths)
        bridge, events = _running_bridge(paths, session, timeout=2.0)
        result = []
        worker = threading.Thread(target=lambda: result.append(bridge.confirm_task_authorization("PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n{}")))
        worker.start()
        request = _wait_for_event(events, "confirm.request")
        bridge._session_provider = lambda: object()
        worker.join(1)
        assert not worker.is_alive()
        assert result == ["n"]
        assert not bridge.deliver_confirm(request["request_id"], "y")


def test_task_authorization_cancel_fails_closed():
    with temporary_agent_paths() as paths:
        session = _session(paths)
        bridge, events = _running_bridge(paths, session, timeout=2.0)
        result = []
        worker = threading.Thread(target=lambda: result.append(bridge.confirm_task_authorization("PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n{}")))
        worker.start()
        request = _wait_for_event(events, "confirm.request")
        bridge.cancel_event.set()
        worker.join(1)
        assert not worker.is_alive()
        assert result == ["n"]
        assert not bridge.deliver_confirm(request["request_id"], "y")


def test_task_authorization_rejects_run_change_and_expired_response():
    with temporary_agent_paths() as paths:
        session = _session(paths)
        bridge, events = _running_bridge(paths, session, timeout=0.15)
        result = []
        worker = threading.Thread(target=lambda: result.append(bridge.confirm_task_authorization("PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n{}")))
        worker.start()
        request = _wait_for_event(events, "confirm.request")
        bridge.lifecycle.finish("completed", ok=True)
        worker.join(1)
        assert not worker.is_alive()
        assert result == ["n"]
        assert not bridge.deliver_confirm(request["request_id"], "y")


def test_task_authorization_deadline_is_finite_and_does_not_extend_other_confirms(monkeypatch):
    from server import task_authorization_confirm_timeout_sec

    monkeypatch.setenv("TASK_AUTHORIZATION_CONFIRM_TIMEOUT_SEC", "99999")
    assert task_authorization_confirm_timeout_sec() == 1800.0
    monkeypatch.setenv("TASK_AUTHORIZATION_CONFIRM_TIMEOUT_SEC", "30")
    assert task_authorization_confirm_timeout_sec() == 120.0
    monkeypatch.setenv("CONFIRM_TIMEOUT_SEC", "3")
    with temporary_agent_paths() as paths:
        assert WsBridge(emit=lambda _event: None, paths=paths).confirm_timeout == 3.0


def test_task_authorization_disconnect_fails_closed_and_rejects_late_response():
    with temporary_agent_paths() as paths:
        session = _session(paths)
        bridge, events = _running_bridge(paths, session, timeout=2.0)
        result = []
        worker = threading.Thread(target=lambda: result.append(bridge.confirm_task_authorization(
            "PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n{}")))
        worker.start()
        request = _wait_for_event(events, "confirm.request")
        bridge.mark_connection_closed()
        worker.join(1)
        assert not worker.is_alive()
        assert result == ["n"]
        assert not bridge.deliver_confirm(request["request_id"], "y")



def test_completed_task_authorizations_do_not_exhaust_bridge():
    with temporary_agent_paths() as paths:
        session = _session(paths)
        bridge, events = _running_bridge(paths, session, timeout=2.0)
        original_emit = bridge.emit

        def approve_immediately(event):
            original_emit(event)
            if event.get("type") == "confirm.request":
                assert bridge.deliver_confirm(event["request_id"], "y")

        bridge.emit = approve_immediately
        for _ in range(4097):
            assert bridge.confirm_task_authorization(
                "PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n{}") == "y"
        assert len([event for event in events if event.get("type") == "confirm.request"]) == 4097
