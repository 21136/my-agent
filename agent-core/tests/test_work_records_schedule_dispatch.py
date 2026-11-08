from __future__ import annotations

import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import pytest
from threading import Event
from project_worker_host import ProjectWorkerHost, ProjectWorkerHostRequest
from work_records_schedule_dispatch import (
    dispatch_prepared_schedule,
    prepare_schedule_dispatch as _prepare_schedule_dispatch,
)


class Session:
    conversation_id = "synthetic-session"


def prepare_schedule_dispatch(candidate, **kwargs):
    kwargs.setdefault("authorized_projection", [{
        "kind": "project", "id": "synthetic-project", "items": [{
            "item_id": "synthetic-item", "priority": 3, "requirements": [{
                "requirement_id": "synthetic-requirement", "status": "queued", "depends_on": [],
                "schedule_claim": None,
            }],
        }],
    }])
    kwargs.setdefault("live_session", Session())
    kwargs.setdefault("live_binding_check", lambda session, run_id, epoch: (
        isinstance(session, Session) and run_id == "synthetic-parent-run" and epoch > 0
    ))
    return _prepare_schedule_dispatch(candidate, **kwargs)


CANDIDATE = {
    "project_id": "synthetic-project",
    "item_id": "synthetic-item",
    "requirement_id": "synthetic-requirement",
    "priority": 3,
}


def test_prepares_explicit_user_request_bound_to_exact_current_turn_without_running():
    prepared = prepare_schedule_dispatch(
        CANDIDATE,
        task="Write a small synthetic output",
        read_targets=["input.txt"],
        write_targets=["output.txt"],
        session_id="synthetic-session",
        parent_run_id="synthetic-parent-run",
        turn_epoch=12,
    )

    assert prepared.request == ProjectWorkerHostRequest(
        "synthetic-project", "synthetic-requirement", "Write a small synthetic output",
        ("input.txt",), ("output.txt",),
    )
    assert (prepared.session_id, prepared.parent_run_id, prepared.turn_epoch) == (
        "synthetic-session", "synthetic-parent-run", 12,
    )
    # This is a data object only; execution must be performed through host.run/run_many.
    assert not hasattr(prepared, "run")


@pytest.mark.parametrize("overrides", [
    {"task": "  "},
    {"write_targets": []},
    {"write_targets": ["out.txt", "OUT.TXT"]},
    {"turn_epoch": 0},
    {"turn_epoch": True},
    {"parent_run_id": ""},
])
def test_rejects_incomplete_or_ambiguous_dispatch_inputs(overrides):
    values = {
        "task": "user supplied task",
        "read_targets": [],
        "write_targets": ["output.txt"],
        "session_id": "synthetic-session",
        "parent_run_id": "synthetic-run",
        "turn_epoch": 4,
    }
    values.update(overrides)
    with pytest.raises(ValueError):
        prepare_schedule_dispatch(CANDIDATE, **values)

@pytest.mark.parametrize("path", [
    "../outside.txt", "folder/../outside.txt", "/absolute.txt", "C:/outside.txt",
    "folder\\\\outside.txt", "CON.txt", "folder/NUL", "trailing.", "trailing ",
    "wild*.txt", "./output.txt",
])
def test_rejects_targets_outside_safe_project_relative_path_grammar(path):
    with pytest.raises(ValueError):
        prepare_schedule_dispatch(
            CANDIDATE, task="human task", read_targets=[], write_targets=[path],
            session_id="synthetic-session", parent_run_id="synthetic-run", turn_epoch=1,
        )


def test_candidate_is_only_an_identity_hint_and_never_supplies_execution_scope():
    prepared = prepare_schedule_dispatch(
        CANDIDATE, task="human supplied task", read_targets=[], write_targets=["out/result.txt"],
        session_id="synthetic-session", parent_run_id="synthetic-parent-run", turn_epoch=8,
    )

    assert prepared.request.task_id == "synthetic-requirement"
    assert prepared.request.task == "human supplied task"
    assert prepared.request.read_targets == ()
    assert prepared.request.write_targets == ("out/result.txt",)
    assert prepared.turn_epoch == 8


def test_rejects_candidate_when_fresh_authorized_projection_changed():
    projection = [{
        "kind": "project", "id": "synthetic-project", "items": [{
            "item_id": "synthetic-item", "priority": 3, "requirements": [{
                "requirement_id": "synthetic-requirement", "status": "completed", "depends_on": [],
            }],
        }],
    }]
    with pytest.raises(ValueError, match="stale, blocked, or absent"):
        prepare_schedule_dispatch(
            CANDIDATE, task="human task", read_targets=[], write_targets=["out.txt"],
            session_id="synthetic-session", parent_run_id="synthetic-parent-run", turn_epoch=1,
            authorized_projection=projection,
        )


def test_rechecks_exact_trusted_current_turn_after_candidate_validation():
    checks = []

    def binding_check(session, run_id, epoch):
        checks.append((session, run_id, epoch))
        return len(checks) == 1

    with pytest.raises(ValueError, match="turn changed"):
        prepare_schedule_dispatch(
            CANDIDATE, task="human task", read_targets=[], write_targets=["out.txt"],
            session_id="synthetic-session", parent_run_id="synthetic-parent-run", turn_epoch=1,
            live_binding_check=binding_check,
        )
    assert len(checks) == 2
    assert all(row[0].conversation_id == "synthetic-session" for row in checks)


class _DispatchHost(ProjectWorkerHost):
    def __init__(self, result="synthetic-success"):
        self.calls = []
        self.result = result

    def run(self, session, request, **kwargs):
        self.calls.append((session, request, kwargs))
        return self.result


def _prepared_for_dispatch(check):
    return prepare_schedule_dispatch(
        CANDIDATE, task="synthetic user task", read_targets=["input.txt"],
        write_targets=["output.txt"], session_id="synthetic-session",
        parent_run_id="synthetic-parent-run", turn_epoch=12,
        live_binding_check=check,
    )


def test_dispatch_success_forwards_exact_request_and_all_host_confirmation_gates():
    session = Session()
    check = lambda current, run, epoch: run == "synthetic-parent-run" and epoch == 12
    prepared = _prepared_for_dispatch(check)
    host = _DispatchHost()
    callbacks = [lambda *_: "y" for _ in range(3)]
    result = dispatch_prepared_schedule(
        prepared, live_session=session, host=host,
        scope_confirm_fn=callbacks[0], outbound_request_confirm_fn=callbacks[1],
        write_confirm_fn=callbacks[2], cancel_event=Event(), live_binding_check=check,
    )
    assert result == "synthetic-success"
    bound_session, request, kwargs = host.calls[0]
    assert bound_session is session and request == prepared.request
    assert kwargs["parent_run_id"] == "synthetic-parent-run"
    assert kwargs["turn_epoch"] == 12
    assert kwargs["scope_confirm_fn"] is callbacks[0]
    assert kwargs["outbound_request_confirm_fn"] is callbacks[1]
    assert kwargs["write_confirm_fn"] is callbacks[2]


def test_dispatch_rejects_expired_binding_before_host_reservation():
    session = Session()
    check = lambda *_: True
    prepared = _prepared_for_dispatch(check)
    host = _DispatchHost()
    with pytest.raises(PermissionError, match="expired"):
        dispatch_prepared_schedule(
            prepared, live_session=session, host=host,
            scope_confirm_fn=lambda *_: "y", outbound_request_confirm_fn=lambda *_: "y",
            write_confirm_fn=lambda *_: "y", cancel_event=Event(),
            live_binding_check=lambda *_: False,
        )
    assert host.calls == []


def test_dispatch_rejects_race_after_preparation_before_host_reservation():
    session = Session()
    checks = iter([True, True, False])
    check = lambda *_: next(checks)
    prepared = _prepared_for_dispatch(check)
    host = _DispatchHost()
    with pytest.raises(PermissionError, match="expired"):
        dispatch_prepared_schedule(
            prepared, live_session=session, host=host,
            scope_confirm_fn=lambda *_: "y", outbound_request_confirm_fn=lambda *_: "y",
            write_confirm_fn=lambda *_: "y", cancel_event=Event(),
            live_binding_check=check,
        )
    assert host.calls == []


def test_host_result_preserves_explicit_rejection():
    session = Session()
    check = lambda *_: True
    prepared = _prepared_for_dispatch(check)
    host = _DispatchHost(result="scope_denied")
    result = dispatch_prepared_schedule(
        prepared, live_session=session, host=host,
        scope_confirm_fn=lambda *_: "n", outbound_request_confirm_fn=lambda *_: "y",
        write_confirm_fn=lambda *_: "y", cancel_event=Event(), live_binding_check=check,
    )
    assert result == "scope_denied"
    assert len(host.calls) == 1


def test_rejects_candidate_expansion_or_missing_identity():
    candidate = {**CANDIDATE, "task": "model supplied task"}
    with pytest.raises(ValueError):
        prepare_schedule_dispatch(
            candidate, task="human task", read_targets=[], write_targets=["out.txt"],
            session_id="synthetic-session", parent_run_id="synthetic-run", turn_epoch=1,
        )
