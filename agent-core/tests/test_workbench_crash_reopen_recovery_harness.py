"""Automated workbench reopen recovery: foreign-owner runs → unknown, overview stays honest.

Exercises session.open recovery, overview projection, and reachable controls after a
simulated process restart. No live model or Electron GUI.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import server
from paths import AgentPaths
from server import WsBridge, WsSessionHandler, _recover_interrupted_work_records
from session import Session, SessionMeta, load_session_for_harness
from work_records import WorkRecordStore, _WORK_RECORD_PROCESS_INSTANCE_ID


def _session(tmp_path: Path, conversation_id: str) -> Session:
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    paths.workspace.mkdir(parents=True, exist_ok=True)
    session_dir = paths.data / "sessions" / conversation_id
    session_dir.mkdir(parents=True, exist_ok=True)
    meta = SessionMeta(active_shell="grow", harness="desktop", turn_mode="agent")
    session = Session(conversation_id, session_dir, "", meta, [], paths)
    session.save()
    return session


def _overview_request(session: Session) -> dict:
    return {
        "type": "work-records.overview.query",
        "session_id": session.conversation_id,
        "request_id": str(uuid.uuid4()),
    }


def _overview_response(events: list[dict], request_id: str) -> dict:
    return next(
        event
        for event in reversed(events)
        if event.get("type") == "work-records.overview.result"
        and event.get("request_id") == request_id
    )


def _requirement_statuses(result: dict) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for group in result.get("groups", []):
        for item in group.get("items", []):
            for req in item.get("requirements", []):
                text = req.get("text", "")
                if text:
                    statuses[text] = req.get("status", "")
    return statuses


def _stamp_foreign_execution_owner(
    store: WorkRecordStore, item_id: str, requirement_id: str, owner_id: str,
) -> None:
    def change(data):
        changed = 0
        for item in data["items"]:
            if item.get("item_id") != item_id:
                continue
            for req in item.get("requirements", []):
                if req.get("requirement_id") == requirement_id:
                    req["execution_owner_id"] = owner_id
                    changed += 1
        return changed, changed > 0

    store._mutate(change)


def _minimal_repl(session: Session) -> SimpleNamespace:
    executor = SimpleNamespace(session=SimpleNamespace(refresh_bound_project_meta=lambda: None))
    agent = SimpleNamespace(session=session, executor=executor, project_worker_host=None)
    repl = SimpleNamespace(session=session, agent=agent)

    def _rebind_agent() -> None:
        agent.session = repl.session

    repl._rebind_agent = _rebind_agent
    return repl


def test_session_open_recovery_overview_and_controls_after_simulated_crash(tmp_path, monkeypatch):
    """session.open marks foreign in_progress unknown; overview + stop/cancel stay honest."""

    async def run() -> None:
        conversation_id = "20261109-crash-reopen"
        session = _session(tmp_path, conversation_id)
        store = WorkRecordStore(session)

        crashed_item = store.create_item(title="crashed worker task", scope={"kind": "independent"})
        crashed_req = store.add_requirement(
            crashed_item["item_id"], "stale in-flight requirement", event_id="stale-req",
        )
        store.begin_requirement_run(
            crashed_item["item_id"], crashed_req["requirement_id"],
            run_id="stale-run", expected_current_run_id=None,
        )

        live_item = store.create_item(title="same-process live task", scope={"kind": "independent"})
        live_req = store.add_requirement(
            live_item["item_id"], "live in-flight requirement", event_id="live-req",
        )
        store.begin_requirement_run(
            live_item["item_id"], live_req["requirement_id"],
            run_id="live-run", expected_current_run_id=None,
        )

        queued_item = store.create_item(title="queued follow-up", scope={"kind": "independent"})
        queued_req = store.add_requirement(
            queued_item["item_id"], "queued stop target", event_id="queued-req",
        )
        store.set_requirement_control(
            queued_item["item_id"], queued_req["requirement_id"],
            action="queue", expected_status="pending",
            event_id="queue-1", idempotency_key="queue-1",
        )

        session.meta.project_id = "project-alpha"
        session.save()
        store = WorkRecordStore(session)
        worker_item = store.create_item(
            title="project worker task",
            scope={"kind": "project", "project_id": "project-alpha"},
        )
        worker_req = store.add_requirement(
            worker_item["item_id"], "cancel still reachable", event_id="worker-req",
        )
        store.begin_requirement_run(
            worker_item["item_id"], worker_req["requirement_id"],
            run_id="worker-live-run", expected_current_run_id=None,
        )

        _stamp_foreign_execution_owner(
            store, crashed_item["item_id"], crashed_req["requirement_id"], "crashed-sidecar-process",
        )
        session.save()

        reopened = load_session_for_harness(session.paths, conversation_id, expected="desktop")
        repl = _minimal_repl(reopened)
        events: list[dict] = []
        bridge = WsBridge(emit=events.append, paths=session.paths)
        handler = WsSessionHandler(session.paths)
        monkeypatch.setattr(server, "emit_corruption_notices", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(server, "_emit_pending_work_card_references", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(bridge, "emit_session_state", lambda *_session: None)

        await handler._handle_incoming(
            {"type": "session.open", "session_id": conversation_id}, repl, bridge,
        )

        warn = next(
            (event for event in events if event.get("type") == "notice" and event.get("level") == "warn"),
            None,
        )
        assert warn is not None
        assert "未自动恢复" in warn.get("text", "")

        overview_id = str(uuid.uuid4())
        overview_msg = _overview_request(reopened)
        overview_msg["request_id"] = overview_id
        await handler._handle_incoming(overview_msg, repl, bridge)
        overview = _overview_response(events, overview_id)

        assert overview["state"] == "visible"
        statuses = _requirement_statuses(overview)
        assert statuses["stale in-flight requirement"] == "unknown"
        assert statuses["live in-flight requirement"] == "in_progress"
        assert statuses["queued stop target"] == "queued"
        assert statuses["cancel still reachable"] == "in_progress"
        assert "in_progress" not in statuses.get("stale in-flight requirement", "in_progress")

        stop_request_id = str(uuid.uuid4())
        stop_msg = {
            "type": "work-record.control",
            "session_id": conversation_id,
            "request_id": stop_request_id,
            "item_id": queued_item["item_id"],
            "requirement_id": queued_req["requirement_id"],
            "action": "stop",
            "expected_status": "queued",
            "expected_revision": WorkRecordStore(reopened).with_locked_data(lambda data: data["revision"]),
            "event_id": str(uuid.uuid4()),
            "idempotency_key": stop_request_id,
        }
        await handler._handle_incoming(stop_msg, repl, bridge)
        stop_done = next(
            event for event in reversed(events)
            if event.get("type") == "work-record.control.done"
            and event.get("request_id") == stop_request_id
        )
        assert stop_done["status"] == "applied"

        cancel_signals: list[tuple] = []
        host = SimpleNamespace(
            request_cancel_for_record=lambda bound_session, **identity: (
                cancel_signals.append((bound_session.conversation_id, identity)) or "signal_set"
            ),
        )
        repl.agent.project_worker_host = host
        reopened.meta.project_id = "project-alpha"
        cancel_request_id = str(uuid.uuid4())
        cancel_msg = {
            "type": "work-record.control",
            "session_id": conversation_id,
            "request_id": cancel_request_id,
            "item_id": worker_item["item_id"],
            "requirement_id": worker_req["requirement_id"],
            "action": "cancel_execution",
            "expected_status": "in_progress",
            "expected_revision": WorkRecordStore(reopened).with_locked_data(lambda data: data["revision"]),
            "event_id": str(uuid.uuid4()),
            "idempotency_key": cancel_request_id,
        }
        await handler._handle_incoming(cancel_msg, repl, bridge)
        cancel_done = next(
            event for event in reversed(events)
            if event.get("type") == "work-record.control.done"
            and event.get("request_id") == cancel_request_id
        )
        assert cancel_done["status"] == "applied"
        assert cancel_signals

        persisted = WorkRecordStore(reopened).get_item(crashed_item["item_id"])
        assert persisted["requirements"][0]["status"] == "unknown"
        assert any(event["kind"] == "requirement_interrupted" for event in persisted["history"])

    asyncio.run(run())


_RECOVERY_SUBPROCESS = r'''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
root = Path(sys.argv[2])
conversation_id = sys.argv[3]
paths = AgentPaths(root, root / "workspace", root / "data", root / "evolve")
session_dir = paths.data / "sessions" / conversation_id
meta = SessionMeta.from_dict(__import__("json").loads((session_dir / "meta.json").read_text(encoding="utf-8")))
session = Session(conversation_id, session_dir, "", meta, [], paths)
store = WorkRecordStore(session)
changed = store.mark_interrupted_runs_unknown(event_id_prefix="subprocess-reopen")
print(changed)
'''


def test_subprocess_restart_then_session_open_matches_outer_process_recovery(tmp_path):
    """A new interpreter marks the stale owner unknown; session.open stays consistent."""
    conversation_id = "20261109-outer-process"
    session = _session(tmp_path, conversation_id)
    store = WorkRecordStore(session)
    item = store.create_item(title="outer crash", scope={"kind": "independent"})
    requirement = store.add_requirement(item["item_id"], "outer stale requirement", event_id="outer-req")
    store.begin_requirement_run(
        item["item_id"], requirement["requirement_id"],
        run_id="outer-run", expected_current_run_id=None,
    )
    _stamp_foreign_execution_owner(store, item["item_id"], requirement["requirement_id"], "previous-sidecar-instance")
    session.save()

    recovered = subprocess.run(
        [sys.executable, "-c", _RECOVERY_SUBPROCESS, str(_CORE), str(tmp_path), conversation_id],
        cwd=_CORE,
        env={**os.environ, "PYTHONPATH": str(_CORE), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert recovered.returncode == 0, recovered.stderr
    assert recovered.stdout.strip().splitlines()[-1] == "1"

    reopened = load_session_for_harness(session.paths, conversation_id, expected="desktop")
    assert _recover_interrupted_work_records(reopened) is True
    req = WorkRecordStore(reopened).get_item(item["item_id"])["requirements"][0]
    assert req["status"] == "unknown"
    assert req.get("execution_owner_id") != "previous-sidecar-instance"
    assert req.get("execution_owner_id") is None


def test_schedule_claim_unknown_surfaces_claim_recovery_in_overview(tmp_path):
    session = _session(tmp_path, "20261109-claim-unknown")
    session.meta.project_id = "project-beta"
    session.save()
    store = WorkRecordStore(session)
    item = store.create_item(
        title="claimed worker",
        scope={"kind": "project", "project_id": "project-beta"},
    )
    requirement = store.add_requirement(
        item["item_id"], "claim needs human inspection", event_id="claim-req",
    )
    store.set_requirement_control(
        item["item_id"], requirement["requirement_id"],
        action="queue", expected_status="pending",
        event_id="queue-claim", idempotency_key="queue-claim",
    )
    revision = store.with_locked_data(lambda data: data["revision"])
    store.claim_scheduled_requirement(
        "project-beta", item["item_id"], requirement["requirement_id"],
        expected_revision=revision, owner_id="dead-host", reservation_id="res-1",
        parent_run_id="parent-1",
    )
    store.begin_claimed_schedule_run(
        "project-beta", item["item_id"], requirement["requirement_id"],
        run_id="worker-dead", owner_id="dead-host", reservation_id="res-1",
        parent_run_id="parent-1", expected_requirement_text="claim needs human inspection",
    )
    _stamp_foreign_execution_owner(store, item["item_id"], requirement["requirement_id"], "dead-host")
    store.mark_interrupted_runs_unknown(
        event_id_prefix="claim-recovery",
        current_owner_id=_WORK_RECORD_PROCESS_INSTANCE_ID,
    )
    store.mark_schedule_claim_unknown(
        item["item_id"], requirement["requirement_id"],
        owner_id="dead-host", reservation_id="res-1", parent_run_id="parent-1",
    )

    async def run() -> None:
        events: list[dict] = []
        bridge = WsBridge(emit=events.append, paths=session.paths)
        handler = WsSessionHandler(session.paths)
        repl = _minimal_repl(session)
        request = _overview_request(session)
        await handler._handle_incoming(request, repl, bridge)
        result = _overview_response(events, request["request_id"])
        claim_flags = [
            req.get("claim_recovery_needed")
            for group in result["groups"]
            for item_row in group["items"]
            for req in item_row["requirements"]
            if req.get("text") == "claim needs human inspection"
        ]
        assert claim_flags == [True]

    asyncio.run(run())
