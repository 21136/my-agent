"""Companion home (小魔王空间): trash → recreate → worker auth → failed batch chat deferral.

Synthetic WS/confirm wiring without GUI; extends #40–#43 regression harness.
"""
from __future__ import annotations

import asyncio
import secrets
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from current_dialogue_project_card_chat import candidate_defers_chat_surface
from project_mode import create_project, project_dir
from project_switch import lookup_project_session, record_project_session
from server import WsBridge, _schedule_current_dialogue_project_work_cards
from session import Session, create_new
from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry


def _wait_for_confirm(
    events: list[dict],
    bridge: WsBridge,
    *,
    timeout: float = 2.0,
    after: int = 0,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending_id = bridge._pending_confirm_id
        if pending_id:
            match = next(
                (
                    event
                    for event in events[after:]
                    if event.get("type") == "confirm.request" and event.get("request_id") == pending_id
                ),
                None,
            )
            if match is not None:
                return match
        time.sleep(0.005)
    raise AssertionError("confirm.request was not emitted with active pending id")


def _companion_session(paths, name: str) -> Session:
    session = create_new(paths, conversation_id=name)
    session.meta.active_shell = "grow"
    session.meta.harness = "desktop"
    session.meta.turn_mode = "agent"
    session.save()
    return session


def test_companion_trash_confirm_delivers_via_bridge_without_stale_rejection():
    """Trash soft-delete confirm must accept deliver_confirm on the active request (not stale-card)."""
    with temporary_agent_paths() as paths:
        token = secrets.token_hex(2)
        project_ids = [f"random-number-{label}-{token}" for label in ("alpha", "beta", "gamma")]
        for project_id in project_ids:
            create_project(paths, project_id)

        session = _companion_session(paths, f"companion-trash-{secrets.token_hex(4)}")
        events: list[dict] = []
        bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=5.0)
        bridge._session_provider = lambda: session
        bridge.lifecycle_conversation_id = session.conversation_id
        bridge.lifecycle.start_or_activate("ordinary")

        registry = ToolRegistry.load(paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession.load(session.session_dir),
            confirm_fn=bridge.confirm_fn,
        )

        results: list = []

        def run_trash() -> None:
            results.append(
                executor.run("trash_workspace_projects", {"glob_pattern": "random-number*"}),
            )

        worker = threading.Thread(target=run_trash, daemon=True)
        worker.start()
        request = _wait_for_confirm(events, bridge)
        assert "软删除" in (request.get("preview") or "")
        if "session_id" in request:
            assert request["session_id"] == session.conversation_id
        assert bridge.deliver_confirm(request["request_id"], "y")
        worker.join(3)
        assert not worker.is_alive()
        assert len(results) == 1 and results[0].ok, results[0].error if results else None
        for project_id in project_ids:
            assert not project_dir(paths, project_id).exists()
        reloaded = Session.load(paths, session.conversation_id)
        assert not (reloaded.meta.project_id or "").strip()
        assert reloaded.meta.active_shell == "grow"
        done = [event for event in events if event.get("type") == "confirm.done"]
        assert any(event.get("choice") == "y" for event in done)
        assert not any(event.get("choice") == "stale" for event in done)


def test_companion_recreates_three_projects_register_only_stays_unbound():
    with temporary_agent_paths() as paths:
        token = secrets.token_hex(2)
        project_ids = [f"random-number-{label}-{token}" for label in ("one", "two", "three")]

        session = _companion_session(paths, f"companion-open-{secrets.token_hex(4)}")
        events: list[dict] = []
        bridge = WsBridge(emit=events.append, paths=paths)
        registry = ToolRegistry.load(paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession.load(session.session_dir),
            confirm_fn=bridge.confirm_fn,
        )

        for project_id in project_ids:
            create_project(paths, project_id)
            opened = executor.run(
                "propose_context_switch",
                {"action": "project.open", "target": project_id},
            )
            assert opened.ok, opened.error
            assert lookup_project_session(paths, project_id) == session.conversation_id

        loaded = Session.load(paths, session.conversation_id)
        assert not (loaded.meta.project_id or "").strip()
        assert loaded.meta.active_shell == "grow"
        for project_id in project_ids:
            assert project_dir(paths, project_id).is_dir()


def test_companion_worker_batch_authorization_confirm_surfaces_on_parent_ws():
    with temporary_agent_paths() as paths:
        session = _companion_session(paths, f"companion-batch-{secrets.token_hex(4)}")
        events: list[dict] = []
        bridge = WsBridge(emit=events.append, paths=paths, task_authorization_confirm_timeout=3.0)
        bridge._session_provider = lambda: session
        bridge.lifecycle_conversation_id = session.conversation_id
        bridge.lifecycle.start_or_activate("ordinary")

        outcomes: list[str] = []

        def authorize() -> None:
            outcomes.append(
                bridge.confirm_task_authorization(
                    'PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n{"schema_version":1,"tasks":[]}',
                ),
            )

        worker = threading.Thread(target=authorize, daemon=True)
        worker.start()
        request = _wait_for_confirm(events, bridge, timeout=1.5)
        assert (request.get("preview") or "").startswith("PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n")
        assert request["session_id"] == session.conversation_id
        assert not any(
            event.get("type") == "turn.notice" and "小魔王空间" in (event.get("text") or "")
            for event in events
        )
        assert bridge.deliver_confirm(request["request_id"], "y")
        worker.join(2)
        assert outcomes == ["y"]
        assert bridge._pending_confirm_id is None


def test_companion_failed_worker_batch_turn_end_suppresses_chat_delivery(monkeypatch):
    """Server turn-end gate + scheduler must not push blocked batch cards into main chat (#41)."""
    blocked_rows = [
        {
            "project_id": "random-number-alpha",
            "status": "blocked",
            "model_turn_status": "blocked",
            "record_lifecycle_status": "blocked_recorded",
        },
        {
            "project_id": "random-number-beta",
            "status": "blocked",
            "model_turn_status": "blocked",
            "record_lifecycle_status": "blocked_recorded",
        },
    ]
    from current_dialogue_project_card_chat import worker_batch_should_defer_project_cards_to_chat

    assert worker_batch_should_defer_project_cards_to_chat(blocked_rows) is True
    candidates = [{
        "session_id": "companion-home",
        "parent_run_id": str(uuid.uuid4()),
        "turn_epoch": 1,
        "item_id": "orchestration-item",
        "project_ids": ("random-number-alpha", "random-number-beta"),
        "defer_chat_surface": worker_batch_should_defer_project_cards_to_chat(blocked_rows),
    }]
    chat_delivery = not any(candidate_defers_chat_surface(candidate) for candidate in candidates)
    assert chat_delivery is False

    with temporary_agent_paths() as paths:
        for project_id in candidates[0]["project_ids"]:
            create_project(paths, project_id, project_template="light")

        session = _companion_session(paths, f"companion-defer-{secrets.token_hex(4)}")
        events: list[dict] = []
        bridge = WsBridge(emit=events.append, paths=paths)
        repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
        monkeypatch.setattr("server.PROJECT_WORK_CARD_MERGE_SECONDS", 0.01)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def schedule_without_chat() -> int:
            scheduled = _schedule_current_dialogue_project_work_cards(
                repl=repl,
                bridge=bridge,
                session=session,
                project_ids=candidates[0]["project_ids"],
                chat_delivery=chat_delivery,
            )
            await asyncio.sleep(0.35)
            return scheduled

        try:
            scheduled = loop.run_until_complete(schedule_without_chat())
        finally:
            loop.close()
            asyncio.set_event_loop(None)

        assert scheduled == 0
        assert not any(event.get("type") == "work-card.present" for event in events)


def test_companion_trash_then_register_then_batch_auth_sequence():
    """End-to-end confirm sequence: trash → open three → batch authorization."""
    with temporary_agent_paths() as paths:
        token = secrets.token_hex(2)
        labels = ("a", "b", "c")
        project_ids = [f"random-number-{label}-{token}" for label in labels]
        for project_id in project_ids:
            create_project(paths, project_id)

        session = _companion_session(paths, f"companion-seq-{secrets.token_hex(4)}")
        events: list[dict] = []
        bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=5.0,
                         task_authorization_confirm_timeout=3.0)
        bridge._session_provider = lambda: session
        bridge.lifecycle_conversation_id = session.conversation_id
        bridge.lifecycle.start_or_activate("ordinary")

        registry = ToolRegistry.load(paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession.load(session.session_dir),
            confirm_fn=bridge.confirm_fn,
        )

        trash_results: list = []

        def trash() -> None:
            trash_results.append(
                executor.run("trash_workspace_projects", {"glob_pattern": f"random-number-*-{token}"}),
            )

        trash_thread = threading.Thread(target=trash, daemon=True)
        trash_thread.start()
        trash_request = _wait_for_confirm(events, bridge)
        assert bridge.deliver_confirm(trash_request["request_id"], "y")
        trash_thread.join(3)
        assert trash_results[0].ok

        for project_id in project_ids:
            create_project(paths, project_id)
            open_result = executor.run(
                "propose_context_switch",
                {"action": "project.open", "target": project_id},
            )
            assert open_result.ok

        auth_results: list[str] = []

        def batch_auth() -> None:
            auth_results.append(
                bridge.confirm_task_authorization(
                    'PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n'
                    '{"schema_version":1,"project_ids":["random-number-a-'
                    f'{token}","random-number-b-{token}","random-number-c-{token}"]}}',
                ),
            )

        after_trash_events = len(events)
        auth_thread = threading.Thread(target=batch_auth, daemon=True)
        auth_thread.start()
        auth_request = _wait_for_confirm(events, bridge, timeout=1.5, after=after_trash_events)
        assert auth_request["request_id"] != trash_request["request_id"]
        assert bridge.deliver_confirm(auth_request["request_id"], "y")
        auth_thread.join(2)
        assert auth_results == ["y"]

        confirm_ids = [
            event["request_id"]
            for event in events
            if event.get("type") == "confirm.request"
        ]
        assert len(confirm_ids) == 2
        assert len(set(confirm_ids)) == 2
        assert not (Session.load(paths, session.conversation_id).meta.project_id or "").strip()
