from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from server import WsBridge, _begin_work_record_run_binding, _settle_work_record_run
from work_records import WorkRecordStore


def make_unmanaged_handler(paths):
    """Use the ordinary-session dispatch contract without bypassing its mode flag."""
    from server import WsSessionHandler

    handler = object.__new__(WsSessionHandler)
    handler.paths = paths
    handler.managed_readonly_context = None
    handler._file_stage = SimpleNamespace(take=lambda *_args: [])
    return handler


def make_store(tmp_path: Path):
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace", data=tmp_path / "data", evolve=tmp_path / "evolve")
    session_dir = paths.data / "sessions" / "run-wire-session"
    session_dir.mkdir(parents=True)
    session = Session(
        conversation_id="run-wire-session", session_dir=session_dir, goal="",
        meta=SessionMeta(), messages=[], paths=paths,
    )
    store = WorkRecordStore(session)
    item = store.create_item(title="Synthetic task", scope={"kind": "independent"})
    requirement = store.add_requirement(item["item_id"], "Synthetic requirement", event_id="source-req")
    return session, store, item["item_id"], requirement["requirement_id"]


def test_exact_recorded_capture_binds_authoritative_run_and_settles_unknown(tmp_path):
    session, store, item_id, requirement_id = make_store(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    snapshot = bridge.lifecycle.snapshot()
    bridge.lifecycle_conversation_id = session.conversation_id
    bridge._turn_busy.set()
    capture = SimpleNamespace(status="recorded", item_id=item_id, requirement_id=requirement_id)

    binding = _begin_work_record_run_binding(
        session=session, capture=capture, run_id=snapshot.run_id,
        current_session=session, bridge=bridge, emit=events.append,
    )
    assert binding is not None
    run_store, bound = binding
    assert bound.run_id == snapshot.run_id

    assert bridge.finish_execution("completed", ok=True, run_id=snapshot.run_id)
    assert _settle_work_record_run(
        current_session=session, bridge=bridge, store=run_store, binding=bound, emit=events.append,
    )

    item = store.get_item(item_id)
    assert item["requirements"][0]["current_run_id"] == snapshot.run_id
    assert item["requirements"][0]["status"] == "unknown"
    assert item["runs"][0]["run_id"] == snapshot.run_id
    assert item["runs"][0]["status"] == "unknown"
    assert item["runs"][0]["verification"]["status"] == "not_verified"
    assert any(event.get("type") == "turn.notice" and event.get("level") == "info" for event in events)


@pytest.mark.parametrize(
    ("finish_reason", "ok", "expected_state", "expected_label"),
    [
        ("completed", True, "settled", "completed"),
        ("cancelled", False, "settled", "cancelled"),
        ("timeout", False, "settled", "timeout"),
        ("error", False, "failed", "error"),
        ("runaway_paused", False, "paused", "paused"),
        ("provider-private-reason", False, "failed", "unknown"),
        ("success", True, "settled", "unknown"),
    ],
)
def test_terminal_snapshot_persists_only_restricted_host_reason_and_keeps_requirement_unknown(
    tmp_path, finish_reason, ok, expected_state, expected_label,
):
    import json

    session, store, item_id, requirement_id = make_store(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    snapshot = bridge.lifecycle.snapshot()
    bridge.lifecycle_conversation_id = session.conversation_id
    binding = _begin_work_record_run_binding(
        session=session,
        capture=SimpleNamespace(status="recorded", item_id=item_id, requirement_id=requirement_id),
        run_id=snapshot.run_id, current_session=session, bridge=bridge, emit=events.append,
    )
    assert binding is not None
    run_store, bound = binding
    assert bridge.finish_execution(finish_reason, ok=ok, run_id=snapshot.run_id)
    terminal = bridge.lifecycle.snapshot()
    assert terminal.state == expected_state

    assert _settle_work_record_run(
        current_session=session, bridge=bridge, store=run_store, binding=bound, emit=events.append,
    )

    item = WorkRecordStore(session).get_item(item_id)
    requirement = next(row for row in item["requirements"] if row["requirement_id"] == requirement_id)
    run = next(row for row in item["runs"] if row["run_id"] == snapshot.run_id)
    assert requirement["status"] == run["status"] == "unknown"
    assert run["verification"] == {
        "status": "not_verified",
        "evidence_refs": [],
        "host_execution": {"sequence": terminal.sequence, "finish_reason": expected_label},
    }
    assert "outcome" not in run["verification"]["host_execution"]
    persisted = json.dumps(item, ensure_ascii=False)
    assert "provider-private-reason" not in persisted


def test_non_recorded_or_incomplete_capture_never_begins_cas(tmp_path):
    session, store, item_id, requirement_id = make_store(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    snapshot = bridge.lifecycle.snapshot()

    unrecorded = _begin_work_record_run_binding(
        session=session,
        capture=SimpleNamespace(status="not_authorized", item_id=item_id, requirement_id=requirement_id),
        run_id=snapshot.run_id, current_session=session, bridge=bridge, emit=events.append,
    )
    assert unrecorded is None  # ordinary chat remains eligible to continue
    for capture in (
        SimpleNamespace(status="recorded", item_id=item_id, requirement_id=None),
        SimpleNamespace(status="recorded", item_id=None, requirement_id=requirement_id),
    ):
        assert _begin_work_record_run_binding(
            session=session, capture=capture, run_id=snapshot.run_id,
            current_session=session, bridge=bridge, emit=events.append,
        ) is not None  # recorded-but-unbindable must fail closed

    item = store.get_item(item_id)
    assert "current_run_id" not in item["requirements"][0]
    assert item["runs"] == []


def test_wrong_session_and_nonterminal_run_do_not_settle(tmp_path):
    session, store, item_id, requirement_id = make_store(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    snapshot = bridge.lifecycle.snapshot()
    bridge.lifecycle_conversation_id = session.conversation_id
    binding = _begin_work_record_run_binding(
        session=session,
        capture=SimpleNamespace(status="recorded", item_id=item_id, requirement_id=requirement_id),
        run_id=snapshot.run_id, current_session=session, bridge=bridge, emit=events.append,
    )
    assert binding is not None

    run_store, bound = binding
    assert not _settle_work_record_run(
        current_session=SimpleNamespace(conversation_id="other-session"),
        bridge=bridge, store=run_store, binding=bound, emit=events.append,
    )
    assert store.get_item(item_id)["runs"] == []
    run_store, bound = binding
    assert not _settle_work_record_run(
        current_session=session, bridge=bridge, store=run_store, binding=bound, emit=events.append,
    )
    assert store.get_item(item_id)["runs"] == []



def test_same_message_capture_requests_tracking_and_wires_the_run_through_run_line(tmp_path, monkeypatch):
    import asyncio
    import companion_world_context
    import llm_client
    import project_api
    import server
    import work_records_service
    from server import WsSessionHandler

    session, store, item_id, requirement_id = make_store(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    captured_track_flags: list[bool] = []

    class FakeClient:
        def __init__(self, **_kwargs): pass
        def set_cancel_event(self, _event): pass

    class FakeCaptureService:
        def __init__(self, *, session, **_kwargs): self.session = session
        def capture(self, _text, *, request_id=None, track_run=False):
            captured_track_flags.append(track_run)
            item = WorkRecordStore(self.session).create_item(title="Bound task", scope={"kind": "independent"})
            requirement = WorkRecordStore(self.session).add_requirement(
                item["item_id"], "Bound requirement", event_id=request_id or "capture-req",
                    idempotency_key=f"test:{request_id}",
            )
            return SimpleNamespace(
                status="recorded", message="candidate stored",
                item_id=item["item_id"], requirement_id=requirement["requirement_id"],
            )

    monkeypatch.setattr(work_records_service, "is_explicit_record_request", lambda _text: True)
    monkeypatch.setattr(work_records_service, "WorkRecordCaptureService", FakeCaptureService)
    monkeypatch.setattr(llm_client, "LLMClient", FakeClient)
    monkeypatch.setattr(server, "session_memory_event", lambda *_args: {"type": "session.memory"})
    monkeypatch.setattr(server, "_proposals_payload", lambda *_args: {"type": "evolve.proposals", "items": []})
    monkeypatch.setattr(server, "_memories_payload", lambda *_args: {"type": "evolve.memories", "items": []})
    monkeypatch.setattr(project_api, "after_turn_project_hooks", lambda *_args: None)
    monkeypatch.setattr(companion_world_context, "is_companion_world_context_allowed", lambda **_kwargs: False)

    calls: list[str] = []
    repl = SimpleNamespace(
        session=session,
        last_turn_finish_reason=None,
        agent=SimpleNamespace(
            bind_host_run_context=None,
            executor=SimpleNamespace(session=SimpleNamespace(evolution_authorization_id="")),
        ),
        handle_line=lambda line: calls.append(line) or "continue",
    )
    handler = make_unmanaged_handler(session.paths)

    asyncio.run(handler._dispatch(
        {"type": "user.message", "text": "please record this", "request_id": "3f6c6e0c-7438-4fe1-ace1-3195c00af442"},
        repl, bridge,
    ))

    assert captured_track_flags == [True]
    assert len(calls) == 1
    assert bridge.lifecycle.snapshot().state == "settled"
    item = next(row for row in store.list_items() if row["title"] == "Bound task")
    assert item["requirements"][0]["current_run_id"] == bridge.lifecycle.snapshot().run_id
    assert item["requirements"][0]["status"] == "unknown"
    assert item["runs"][0]["run_id"] == bridge.lifecycle.snapshot().run_id
    assert item["runs"][0]["status"] == "unknown"
    assert item["runs"][0]["verification"] == {
        "status": "not_verified",
        "evidence_refs": [],
        "host_execution": {"sequence": bridge.lifecycle.snapshot().sequence, "finish_reason": "completed"},
    }
    assert any(event.get("type") == "turn.notice" and event.get("level") == "info" for event in events)


def test_same_request_replay_with_existing_run_is_blocked_before_agent_execution(tmp_path, monkeypatch):
    import asyncio
    import companion_world_context
    import llm_client
    import project_api
    import server
    import work_records_service
    from server import WsSessionHandler

    session, store, _item_id, _requirement_id = make_store(tmp_path)
    request_id = "ac2a5894-87dc-4c33-aebd-d351e86964ce"
    replay_item = store.create_item(title="Previously captured", scope={"kind": "independent"})
    requirement = store.add_requirement(
        replay_item["item_id"], "Same request requirement", event_id=request_id,
        idempotency_key=f"test:{request_id}",
    )
    store.begin_requirement_run(
        replay_item["item_id"], requirement["requirement_id"],
        run_id="prior-authoritative-run", expected_current_run_id=None,
    )

    class FakeClient:
        def __init__(self, **_kwargs): pass
        def set_cancel_event(self, _event): pass

    class FakeCaptureService:
        def __init__(self, **_kwargs): pass
        def capture(self, _text, *, request_id=None, track_run=False):
            assert request_id == request_id_expected
            assert track_run is True
            return SimpleNamespace(
                status="recorded", message="idempotent replay",
                item_id=replay_item["item_id"], requirement_id=requirement["requirement_id"],
            )

    request_id_expected = request_id
    monkeypatch.setattr(work_records_service, "is_explicit_record_request", lambda _text: True)
    monkeypatch.setattr(work_records_service, "WorkRecordCaptureService", FakeCaptureService)
    monkeypatch.setattr(llm_client, "LLMClient", FakeClient)
    monkeypatch.setattr(server, "session_memory_event", lambda *_args: {"type": "session.memory"})
    monkeypatch.setattr(server, "_proposals_payload", lambda *_args: {"type": "evolve.proposals", "items": []})
    monkeypatch.setattr(server, "_memories_payload", lambda *_args: {"type": "evolve.memories", "items": []})
    monkeypatch.setattr(project_api, "after_turn_project_hooks", lambda *_args: None)
    monkeypatch.setattr(companion_world_context, "is_companion_world_context_allowed", lambda **_kwargs: False)

    calls: list[str] = []
    events: list[dict] = []
    repl = SimpleNamespace(
        session=session,
        last_turn_finish_reason=None,
        agent=SimpleNamespace(
            bind_host_run_context=None,
            executor=SimpleNamespace(session=SimpleNamespace(evolution_authorization_id="")),
        ),
        handle_line=lambda line: calls.append(line) or "continue",
    )
    bridge = WsBridge(emit=events.append, paths=session.paths)
    handler = make_unmanaged_handler(session.paths)

    asyncio.run(handler._dispatch(
        {"type": "user.message", "text": "same original request", "request_id": request_id},
        repl, bridge,
    ))

    assert calls == []
    assert bridge.lifecycle.snapshot().state == "failed"
    prior = store.get_item(replay_item["item_id"])
    assert prior["requirements"][0]["current_run_id"] == "prior-authoritative-run"
    assert prior["requirements"][0]["status"] == "in_progress"
    assert prior["runs"] == []
    assert any(event.get("type") == "turn.notice" and event.get("level") == "warn" for event in events)


def test_same_conversation_id_replacement_session_is_rejected_before_cas(tmp_path):
    session, store, item_id, requirement_id = make_store(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    snapshot = bridge.lifecycle.snapshot()
    bridge.lifecycle_conversation_id = session.conversation_id
    binding_result = _begin_work_record_run_binding(
        session=session,
        capture=SimpleNamespace(status="recorded", item_id=item_id, requirement_id=requirement_id),
        run_id=snapshot.run_id, current_session=session, bridge=bridge, emit=events.append,
    )
    assert binding_result is not None
    run_store, binding = binding_result
    assert bridge.finish_execution("completed", ok=True, run_id=snapshot.run_id)

    replacement = Session(
        conversation_id=session.conversation_id,
        session_dir=session.session_dir,
        goal=session.goal,
        meta=SessionMeta(),
        messages=[],
        paths=session.paths,
    )
    # Mirror the current call-site bug: both arguments are the replacement object,
    # whose ID matches, while the CAS store is still bound to the original Session.
    assert not _settle_work_record_run(
        current_session=replacement, bridge=bridge,
        store=run_store, binding=binding, emit=events.append,
    )
    import json
    raw = json.loads((session.session_dir / "work-records.v1.json").read_text(encoding="utf-8"))
    bound_item = next(row for row in raw["items"] if row["item_id"] == item_id)
    assert bound_item["runs"] == []
    assert any(event.get("type") == "turn.notice" and event.get("level") == "warn" for event in events)


def test_changed_project_scope_on_original_session_refuses_settlement(tmp_path):
    session, store, item_id, requirement_id = make_store(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    snapshot = bridge.lifecycle.snapshot()
    bridge.lifecycle_conversation_id = session.conversation_id
    binding_result = _begin_work_record_run_binding(
        session=session,
        capture=SimpleNamespace(status="recorded", item_id=item_id, requirement_id=requirement_id),
        run_id=snapshot.run_id, current_session=session, bridge=bridge, emit=events.append,
    )
    assert binding_result is not None
    run_store, binding = binding_result
    assert bridge.finish_execution("completed", ok=True, run_id=snapshot.run_id)

    session.meta.project_id = "changed-project-scope"
    assert not _settle_work_record_run(
        current_session=session, bridge=bridge, store=run_store,
        binding=binding, emit=events.append,
    )
    import json
    raw = json.loads((session.session_dir / "work-records.v1.json").read_text(encoding="utf-8"))
    bound_item = next(row for row in raw["items"] if row["item_id"] == item_id)
    assert bound_item["runs"] == []
    assert any(event.get("type") == "turn.notice" and event.get("level") == "warn" for event in events)


def test_run_line_emit_failures_do_not_skip_terminal_or_unknown_settlement(tmp_path, monkeypatch):
    import asyncio
    import project_api
    import server
    from server import _run_line

    session, store, item_id, requirement_id = make_store(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    snapshot = bridge.lifecycle.snapshot()
    bridge.lifecycle_conversation_id = session.conversation_id
    bridge._turn_busy.set()
    binding = _begin_work_record_run_binding(
        session=session,
        capture=SimpleNamespace(status="recorded", item_id=item_id, requirement_id=requirement_id),
        run_id=snapshot.run_id, current_session=session, bridge=bridge, emit=events.append,
    )
    assert binding is not None
    calls: list[str] = []
    repl = SimpleNamespace(
        session=session,
        last_turn_finish_reason=None,
        agent=SimpleNamespace(bind_host_run_context=None),
        handle_line=lambda line: calls.append(line) or "continue",
    )
    monkeypatch.setattr(server, "session_memory_event", lambda *_args: {"type": "session.memory"})
    monkeypatch.setattr(server, "_proposals_payload", lambda *_args: {"type": "evolve.proposals", "items": []})
    monkeypatch.setattr(server, "_memories_payload", lambda *_args: {"type": "evolve.memories", "items": []})
    monkeypatch.setattr(project_api, "after_turn_project_hooks", lambda *_args: None)

    def failing_emit(event):
        if event.get("type") in {"session.memory", "evolve.proposals", "evolve.memories", "execution.state", "turn.end", "turn.notice"}:
            raise OSError("synthetic websocket send failure")
        events.append(event)

    bridge.emit = failing_emit
    asyncio.run(_run_line(
        repl, bridge, "synthetic turn", session.paths,
        prestarted_run_id=snapshot.run_id, work_record_binding=binding,
    ))

    assert calls == ["synthetic turn"]
    assert bridge._turn_busy.is_set() is False
    final = bridge.lifecycle.snapshot()
    assert final.run_id == snapshot.run_id
    assert final.state == "settled"
    raw = __import__("json").loads((session.session_dir / "work-records.v1.json").read_text(encoding="utf-8"))
    bound_item = next(row for row in raw["items"] if row["item_id"] == item_id)
    assert bound_item["runs"][0]["status"] == "unknown"


def test_recorded_capture_with_unavailable_cas_is_blocking_not_ordinary_chat(tmp_path, monkeypatch):
    import work_records
    from server import _begin_work_record_run_binding

    session, _store, item_id, requirement_id = make_store(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    snapshot = bridge.lifecycle.snapshot()
    bridge.lifecycle_conversation_id = session.conversation_id
    bridge._turn_busy.set()

    class LockUnavailableStore:
        def __init__(self, _session): pass
        def begin_requirement_run(self, *_args, **_kwargs):
            raise OSError("synthetic lock unavailable")

    monkeypatch.setattr(work_records, "WorkRecordStore", LockUnavailableStore)
    decision = _begin_work_record_run_binding(
        session=session,
        capture=SimpleNamespace(status="recorded", item_id=item_id, requirement_id=requirement_id),
        run_id=snapshot.run_id, current_session=session, bridge=bridge,
        emit=events.append, request_id="8cc69a20-d932-4c4d-a595-328f7cd7d351",
    )
    assert decision is not None
    assert not isinstance(decision, tuple)
    assert any(event.get("type") == "turn.notice" and event.get("level") == "warn" for event in events)


def test_recorded_capture_with_lock_failure_does_not_enter_agent_run(tmp_path, monkeypatch):
    import asyncio
    import companion_world_context
    import llm_client
    import project_api
    import server
    import work_records
    import work_records_service
    from server import WsSessionHandler

    session, actual_store, item_id, requirement_id = make_store(tmp_path)
    bridge_events: list[dict] = []
    bridge = WsBridge(emit=bridge_events.append, paths=session.paths)
    calls: list[str] = []

    class FakeClient:
        def __init__(self, **_kwargs): pass
        def set_cancel_event(self, _event): pass

    class FakeCaptureService:
        def __init__(self, **_kwargs): pass
        def capture(self, _text, *, request_id=None, track_run=False):
            assert track_run is True
            return SimpleNamespace(
                status="recorded", message="candidate stored",
                item_id=item_id, requirement_id=requirement_id,
            )

    class LockedStore:
        def __init__(self, _session): pass
        def begin_requirement_run(self, *_args, **_kwargs):
            raise OSError("synthetic lock unavailable")

    monkeypatch.setattr(work_records_service, "is_explicit_record_request", lambda _text: True)
    monkeypatch.setattr(work_records_service, "WorkRecordCaptureService", FakeCaptureService)
    monkeypatch.setattr(llm_client, "LLMClient", FakeClient)
    monkeypatch.setattr(work_records, "WorkRecordStore", LockedStore)
    monkeypatch.setattr(server, "session_memory_event", lambda *_args: {"type": "session.memory"})
    monkeypatch.setattr(server, "_proposals_payload", lambda *_args: {"type": "evolve.proposals", "items": []})
    monkeypatch.setattr(server, "_memories_payload", lambda *_args: {"type": "evolve.memories", "items": []})
    monkeypatch.setattr(project_api, "after_turn_project_hooks", lambda *_args: None)
    monkeypatch.setattr(companion_world_context, "is_companion_world_context_allowed", lambda **_kwargs: False)

    repl = SimpleNamespace(
        session=session,
        last_turn_finish_reason=None,
        agent=SimpleNamespace(
            bind_host_run_context=None,
            executor=SimpleNamespace(session=SimpleNamespace(evolution_authorization_id="")),
        ),
        handle_line=lambda line: calls.append(line) or "continue",
    )
    handler = make_unmanaged_handler(session.paths)

    asyncio.run(handler._dispatch(
        {"type": "user.message", "text": "approved task", "request_id": "d8b39473-3aed-49cb-b949-63814132e190"},
        repl, bridge,
    ))

    assert calls == []
    assert bridge.lifecycle.snapshot().state == "failed"
    assert bridge.lifecycle.snapshot().finish_reason == "error"
    assert any(event.get("type") == "turn.notice" and event.get("level") == "warn" for event in bridge_events)
    assert actual_store.get_item(item_id)["requirements"][0].get("current_run_id") is None


@pytest.mark.parametrize("status", ["already_recorded", "request_conflict", "stale_context", "storage_uncertain", "invalid_request", "unavailable", "uncertain"])
@pytest.mark.parametrize("has_prior_run", [False, True])
def test_replay_conflict_or_uncertain_capture_never_enters_agent_and_closes_turn(
    tmp_path, monkeypatch, status, has_prior_run,
):
    import asyncio
    import companion_world_context
    import llm_client
    import project_api
    import server
    import work_records_service
    from server import WsSessionHandler

    session, store, item_id, requirement_id = make_store(tmp_path)
    if has_prior_run:
        store.begin_requirement_run(
            item_id, requirement_id, run_id="prior-run", expected_current_run_id=None,
        )
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    calls: list[str] = []

    class FakeClient:
        def __init__(self, **_kwargs): pass
        def set_cancel_event(self, _event): pass

    class FakeCaptureService:
        def __init__(self, **_kwargs): pass
        def capture(self, _text, *, request_id=None, track_run=False):
            assert track_run is True
            return SimpleNamespace(
                status=status, message="synthetic capture state",
                item_id=item_id, requirement_id=requirement_id,
            )

    monkeypatch.setattr(work_records_service, "is_explicit_record_request", lambda _text: True)
    monkeypatch.setattr(work_records_service, "WorkRecordCaptureService", FakeCaptureService)
    monkeypatch.setattr(llm_client, "LLMClient", FakeClient)
    monkeypatch.setattr(server, "session_memory_event", lambda *_args: {"type": "session.memory"})
    monkeypatch.setattr(server, "_proposals_payload", lambda *_args: {"type": "evolve.proposals", "items": []})
    monkeypatch.setattr(server, "_memories_payload", lambda *_args: {"type": "evolve.memories", "items": []})
    monkeypatch.setattr(project_api, "after_turn_project_hooks", lambda *_args: None)
    monkeypatch.setattr(companion_world_context, "is_companion_world_context_allowed", lambda **_kwargs: False)

    repl = SimpleNamespace(
        session=session,
        last_turn_finish_reason=None,
        agent=SimpleNamespace(
            bind_host_run_context=None,
            executor=SimpleNamespace(session=SimpleNamespace(evolution_authorization_id="")),
        ),
        handle_line=lambda line: calls.append(line) or "continue",
    )
    handler = make_unmanaged_handler(session.paths)

    asyncio.run(handler._dispatch(
        {"type": "user.message", "text": "please record this", "request_id": "68f11fd0-dc0b-4f9f-bb71-05e0c980d90b"},
        repl, bridge,
    ))

    assert calls == []
    assert bridge._turn_busy.is_set() is False
    assert bridge.lifecycle.snapshot().state in {"failed", "settled"}
    requirement = store.get_item(item_id)["requirements"][0]
    if has_prior_run:
        assert requirement["current_run_id"] == "prior-run"
    else:
        assert "current_run_id" not in requirement
    notices = [event for event in events if event.get("type") == "turn.notice"]
    assert any(event.get("level") == "warn" and "核对" in event.get("text", "") for event in notices)
    assert not any("已新建" in event.get("text", "") or "新写入" in event.get("text", "") for event in notices)
    assert not any("已执行" in event.get("text", "") or "已写入" in event.get("text", "") for event in notices)
    if status == "invalid_request":
        assert any("请求编号" in event.get("text", "") for event in notices)
    if status == "unavailable":
        assert any("重放身份" in event.get("text", "") for event in notices)
    if status == "uncertain":
        assert any("分类或上下文" in event.get("text", "") for event in notices)


@pytest.mark.parametrize("status", ["storage_not_authorized", "not_recorded"])
def test_definite_nonwrite_capture_results_keep_ordinary_dialogue_path(tmp_path, monkeypatch, status):
    import asyncio
    import companion_world_context
    import llm_client
    import project_api
    import server
    import work_records_service
    from server import WsSessionHandler

    session, _store, item_id, requirement_id = make_store(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    calls: list[str] = []

    class FakeClient:
        def __init__(self, **_kwargs): pass
        def set_cancel_event(self, _event): pass

    class FakeCaptureService:
        def __init__(self, **_kwargs): pass
        def capture(self, *_args, **_kwargs):
            return SimpleNamespace(status=status, message="definitely no capture", item_id=item_id,
                                   requirement_id=requirement_id)

    monkeypatch.setattr(work_records_service, "is_explicit_record_request", lambda _text: True)
    monkeypatch.setattr(work_records_service, "WorkRecordCaptureService", FakeCaptureService)
    monkeypatch.setattr(llm_client, "LLMClient", FakeClient)
    monkeypatch.setattr(server, "session_memory_event", lambda *_args: {"type": "session.memory"})
    binding_attempts = []
    original_bind = server._begin_work_record_run_binding
    def observe_binding_attempt(**kwargs):
        binding_attempts.append(kwargs["capture"].status)
        return original_bind(**kwargs)
    monkeypatch.setattr(server, "_begin_work_record_run_binding", observe_binding_attempt)
    monkeypatch.setattr(server, "_proposals_payload", lambda *_args: {"type": "evolve.proposals", "items": []})
    monkeypatch.setattr(server, "_memories_payload", lambda *_args: {"type": "evolve.memories", "items": []})
    monkeypatch.setattr(project_api, "after_turn_project_hooks", lambda *_args: None)
    monkeypatch.setattr(companion_world_context, "is_companion_world_context_allowed", lambda **_kwargs: False)

    repl = SimpleNamespace(
        session=session,
        last_turn_finish_reason=None,
        agent=SimpleNamespace(bind_host_run_context=None,
            executor=SimpleNamespace(session=SimpleNamespace(evolution_authorization_id=""))),
        handle_line=lambda line: calls.append(line) or "continue",
    )
    handler = make_unmanaged_handler(session.paths)

    asyncio.run(handler._dispatch(
        {"type": "user.message", "text": "please record this", "request_id": "abf7ef20-2c41-47e1-9ba8-c87379df0c99"},
        repl, bridge,
    ))
    assert calls == ["please record this"]
    assert bridge.lifecycle.snapshot().state == "settled"
    assert binding_attempts == []
    assert not any("关联" in event.get("text", "") and "未确认" in event.get("text", "")
                   for event in events)



def test_refused_classification_stops_locally_and_preserves_attribution(tmp_path, monkeypatch):
    import asyncio
    import llm_client
    import server
    import work_records_service

    session, store, item_id, requirement_id = make_store(tmp_path)
    original_item = store.get_item(item_id)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    ordinary_calls: list[str] = []
    binding_attempts: list[object] = []

    class FakeClient:
        def __init__(self, **_kwargs): pass
        def set_cancel_event(self, _event): pass

    class RefusedCaptureService:
        def __init__(self, **_kwargs): pass
        def capture(self, *_args, **_kwargs):
            return SimpleNamespace(status="not_authorized", message="definitely no capture",
                                   item_id=item_id, requirement_id=requirement_id)

    monkeypatch.setattr(work_records_service, "is_explicit_record_request", lambda _text: True)
    monkeypatch.setattr(work_records_service, "WorkRecordCaptureService", RefusedCaptureService)
    monkeypatch.setattr(llm_client, "LLMClient", FakeClient)
    monkeypatch.setattr(server, "_begin_work_record_run_binding",
                        lambda **kwargs: binding_attempts.append(kwargs))
    repl = SimpleNamespace(
        session=session, last_turn_finish_reason=None,
        agent=SimpleNamespace(bind_host_run_context=None,
            executor=SimpleNamespace(session=SimpleNamespace(evolution_authorization_id=""))),
        handle_line=lambda line: ordinary_calls.append(line) or "continue",
    )
    handler = make_unmanaged_handler(session.paths)
    text = "please record this"
    asyncio.run(handler._dispatch(
        {"type": "user.message", "text": text, "request_id": "abf7ef20-2c41-47e1-9ba8-c87379df0c99"},
        repl, bridge,
    ))
    assert ordinary_calls == [] and binding_attempts == []
    assert store.get_item(item_id) == original_item
    assert bridge.lifecycle.snapshot().state == "settled"
    assert [(m.get("role"), m.get("content")) for m in session.messages if m.get("role") == "user"] == [("user", text)]
    notice = next(event["text"] for event in events if event.get("type") == "turn.notice"
                  and "未获分类外发授权" in event.get("text", ""))
    assert "未进入普通 Agent" in notice and "未写入工作记录" in notice
    assert "原文按用户消息保存在当前对话中" in notice and "后续对话可能发送" in notice
    assert session.messages[-1].get("role") == "assistant" and session.messages[-1].get("content") == notice
    import json
    persisted = [json.loads(line) for line in session.messages_path.read_text(encoding="utf-8").splitlines()]
    assert persisted[-2:] == [{"role": "user", "content": text}, {"role": "assistant", "content": notice}]


def test_standalone_already_recorded_capture_only_reports_and_never_starts_run(tmp_path, monkeypatch):
    import asyncio
    import llm_client
    import work_records_service
    from server import WsSessionHandler

    session, item_id, requirement_id = make_store(tmp_path)[0], None, None
    store = __import__("work_records").WorkRecordStore(session)
    item = store.create_item(title="Existing", scope={"kind": "independent"})
    requirement = store.add_requirement(item["item_id"], "Existing request", event_id="capture-request")
    item_id, requirement_id = item["item_id"], requirement["requirement_id"]
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)

    class FakeClient:
        def __init__(self, **_kwargs): pass
        def set_cancel_event(self, _event): pass

    class FakeCaptureService:
        def __init__(self, **_kwargs): pass
        def capture(self, *_args, **_kwargs):
            return SimpleNamespace(status="already_recorded", message="previously recorded",
                                   item_id=item_id, requirement_id=requirement_id)

    monkeypatch.setattr(llm_client, "LLMClient", FakeClient)
    monkeypatch.setattr(work_records_service, "WorkRecordCaptureService", FakeCaptureService)
    handler = make_unmanaged_handler(session.paths)
    asyncio.run(handler._dispatch({
        "type": "work-record.capture", "text": "record again", "session_id": session.conversation_id,
        "request_id": "ef72211a-23d4-4138-9c4a-f39b2f3f08ba",
    }, SimpleNamespace(session=session), bridge))

    done = next(event for event in events if event.get("type") == "work-record.capture.done")
    assert done["status"] == "already_recorded"
    assert done["item_id"] == item_id
    assert bridge.lifecycle.snapshot() is None
    assert store.get_item(item_id)["requirements"][0].get("current_run_id") is None


def test_cancelled_capture_with_unknown_write_reclaims_turn_without_execution(tmp_path, monkeypatch):
    import asyncio
    import companion_world_context
    import llm_client
    import project_api
    import server
    import threading
    import work_records_service
    from server import WsSessionHandler

    session, store, item_id, requirement_id = make_store(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    calls: list[str] = []
    capture_started = threading.Event()
    cancel_seen = threading.Event()

    class FakeClient:
        def __init__(self, **_kwargs): self.cancel_event = None
        def set_cancel_event(self, event): self.cancel_event = event

    class FakeCaptureService:
        def __init__(self, *, client, **_kwargs): self.client = client
        def capture(self, *_args, **_kwargs):
            capture_started.set()
            assert self.client.cancel_event.wait(3)
            cancel_seen.set()
            return SimpleNamespace(status="storage_uncertain", message="uncertain write",
                                   item_id=item_id, requirement_id=requirement_id)

    monkeypatch.setattr(work_records_service, "is_explicit_record_request", lambda _text: True)
    monkeypatch.setattr(work_records_service, "WorkRecordCaptureService", FakeCaptureService)
    monkeypatch.setattr(llm_client, "LLMClient", FakeClient)
    monkeypatch.setattr(server, "session_memory_event", lambda *_args: {"type": "session.memory"})
    monkeypatch.setattr(server, "_proposals_payload", lambda *_args: {"type": "evolve.proposals", "items": []})
    monkeypatch.setattr(server, "_memories_payload", lambda *_args: {"type": "evolve.memories", "items": []})
    monkeypatch.setattr(project_api, "after_turn_project_hooks", lambda *_args: None)
    monkeypatch.setattr(companion_world_context, "is_companion_world_context_allowed", lambda **_kwargs: False)

    repl = SimpleNamespace(
        session=session, last_turn_finish_reason=None,
        agent=SimpleNamespace(bind_host_run_context=None,
            executor=SimpleNamespace(session=SimpleNamespace(evolution_authorization_id=""))),
        handle_line=lambda line: calls.append(line) or "continue",
    )
    handler = make_unmanaged_handler(session.paths)

    async def cancel_dispatch():
        task = asyncio.create_task(handler._dispatch(
            {"type": "user.message", "text": "please record this", "request_id": "cdd14516-5392-4744-b3eb-2bf0e435fb48"},
            repl, bridge,
        ))
        assert await asyncio.to_thread(capture_started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_dispatch())
    assert cancel_seen.is_set()
    assert calls == []
    assert bridge._turn_busy.is_set() is False
    assert bridge.lifecycle.snapshot().state == "settled"
    assert bridge.lifecycle.snapshot().finish_reason == "cancelled"
    assert store.get_item(item_id)["runs"] == []
    assert "核对" in " ".join(event.get("text", "") for event in events)


@pytest.mark.parametrize("failure_point", ["capture", "commit_after_side_effect"])
def test_unexpected_capture_exception_after_run_start_blocks_agent_and_reclaims_turn(
    tmp_path, monkeypatch, failure_point,
):
    import asyncio
    import companion_world_context
    import llm_client
    import project_api
    import server
    import work_records_service
    from server import WsSessionHandler

    session, store, item_id, requirement_id = make_store(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    calls: list[str] = []
    uncertain_side_effect = []

    class FakeClient:
        def __init__(self, **_kwargs): pass
        def set_cancel_event(self, _event): pass

    class FakeCaptureService:
        def __init__(self, **_kwargs): pass
        def capture(self, *_args, **_kwargs):
            if failure_point == "commit_after_side_effect":
                uncertain_side_effect.append("may-have-committed")
            raise OSError("synthetic private storage failure")

    monkeypatch.setattr(work_records_service, "is_explicit_record_request", lambda _text: True)
    monkeypatch.setattr(work_records_service, "WorkRecordCaptureService", FakeCaptureService)
    monkeypatch.setattr(llm_client, "LLMClient", FakeClient)
    monkeypatch.setattr(server, "session_memory_event", lambda *_args: {"type": "session.memory"})
    monkeypatch.setattr(server, "_proposals_payload", lambda *_args: {"type": "evolve.proposals", "items": []})
    monkeypatch.setattr(server, "_memories_payload", lambda *_args: {"type": "evolve.memories", "items": []})
    monkeypatch.setattr(project_api, "after_turn_project_hooks", lambda *_args: None)
    monkeypatch.setattr(companion_world_context, "is_companion_world_context_allowed", lambda **_kwargs: False)

    repl = SimpleNamespace(
        session=session,
        last_turn_finish_reason=None,
        agent=SimpleNamespace(bind_host_run_context=None,
            executor=SimpleNamespace(session=SimpleNamespace(evolution_authorization_id=""))),
        handle_line=lambda line: calls.append(line) or "continue",
    )
    handler = make_unmanaged_handler(session.paths)

    asyncio.run(handler._dispatch(
        {"type": "user.message", "text": "please record this", "request_id": "3c67dc65-8218-47e0-bad4-bb304ddb1f84"},
        repl, bridge,
    ))

    assert uncertain_side_effect == ([] if failure_point == "capture" else ["may-have-committed"])
    assert calls == []
    assert bridge._turn_busy.is_set() is False
    assert bridge.lifecycle.snapshot().state == "failed"
    assert any(event.get("level") == "warn" and "\u4e0d\u81ea\u52a8\u7ee7\u7eed" in event.get("text", "")
               and "核对" in event.get("text", "") for event in events)
    warning_text = " ".join(event.get("text", "") for event in events)
    assert "synthetic private storage failure" not in warning_text
    assert "已执行" not in warning_text and "已写入" not in warning_text
    assert store.get_item(item_id)["runs"] == []


def test_local_record_gate_exception_before_prestart_keeps_ordinary_chat_fallback(tmp_path, monkeypatch):
    import asyncio
    import companion_world_context
    import llm_client
    import project_api
    import server
    import work_records_service
    from server import WsSessionHandler

    session, _store, _item_id, _requirement_id = make_store(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    calls: list[str] = []

    class FakeClient:
        def __init__(self, **_kwargs): pass
        def set_cancel_event(self, _event): pass

    def fail_local_gate(_text):
        raise RuntimeError("synthetic gate failure")

    monkeypatch.setattr(work_records_service, "is_explicit_record_request", fail_local_gate)
    monkeypatch.setattr(llm_client, "LLMClient", FakeClient)
    monkeypatch.setattr(server, "session_memory_event", lambda *_args: {"type": "session.memory"})
    monkeypatch.setattr(server, "_proposals_payload", lambda *_args: {"type": "evolve.proposals", "items": []})
    monkeypatch.setattr(server, "_memories_payload", lambda *_args: {"type": "evolve.memories", "items": []})
    monkeypatch.setattr(project_api, "after_turn_project_hooks", lambda *_args: None)
    monkeypatch.setattr(companion_world_context, "is_companion_world_context_allowed", lambda **_kwargs: False)

    repl = SimpleNamespace(
        session=session,
        last_turn_finish_reason=None,
        agent=SimpleNamespace(bind_host_run_context=None,
            executor=SimpleNamespace(session=SimpleNamespace(evolution_authorization_id=""))),
        handle_line=lambda line: calls.append(line) or "continue",
    )
    handler = make_unmanaged_handler(session.paths)

    asyncio.run(handler._dispatch(
        {"type": "user.message", "text": "ordinary request", "request_id": "5d5052bf-9fc6-4c67-af07-b8b076b3ae06"},
        repl, bridge,
    ))
    assert calls == ["ordinary request"]
    assert bridge.lifecycle.snapshot().state == "settled"
    assert any("普通对话仍会继续" in event.get("text", "") for event in events)
