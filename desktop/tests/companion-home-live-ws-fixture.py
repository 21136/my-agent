"""Companion home: real WsSessionHandler + disposable projects; stdin-driven confirm ops for Playwright."""
from __future__ import annotations

import asyncio
import json
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any

core = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(core), str(core / "tests")]

import server  # noqa: E402
from unittest.mock import patch  # noqa: E402
from project_mode import create_project, project_dir  # noqa: E402
from server import WsBridge  # noqa: E402
from session import create_new  # noqa: E402
from tests.isolation_helpers import temporary_agent_paths  # noqa: E402
from tools.executor import ExecutorSession, ToolExecutor  # noqa: E402
from tools.registry import ToolRegistry  # noqa: E402
from websockets.asyncio.server import serve  # noqa: E402

_ACTIVE: dict[str, Any] = {
    "bridge": None,
    "session": None,
    "events": [],
    "paths": None,
    "token": "",
    "project_ids": [],
    "workers": [],
}
_ORIGINAL_WS_BRIDGE_INIT = WsBridge.__init__


def _patched_ws_bridge_init(self, emit, paths, **kwargs):
    events: list[dict[str, Any]] = []

    def tracked(event: dict[str, Any]) -> None:
        events.append(event)
        emit(event)

    _ORIGINAL_WS_BRIDGE_INIT(self, tracked, paths, **kwargs)
    _ACTIVE["bridge"] = self
    _ACTIVE["events"] = events


def _companion_session(paths, name: str):
    session = create_new(paths, conversation_id=name)
    session.meta.active_shell = "grow"
    session.meta.harness = "desktop"
    session.meta.turn_mode = "agent"
    session.save()
    return session


def _require_bridge() -> WsBridge:
    bridge = _ACTIVE.get("bridge")
    if bridge is None:
        raise RuntimeError("no_active_websocket_bridge")
    return bridge


def _run_trash(glob_pattern: str) -> dict[str, Any]:
    bridge = _require_bridge()
    paths = _ACTIVE["paths"]
    session = _ACTIVE["session"]
    bridge._session_provider = lambda: session
    bridge.lifecycle_conversation_id = session.conversation_id
    bridge.lifecycle.start_or_activate("ordinary")
    registry = ToolRegistry.load(paths)
    executor = ToolExecutor(
        registry=registry,
        session=ExecutorSession.load(session.session_dir),
        confirm_fn=bridge.confirm_fn,
    )
    result = executor.run("trash_workspace_projects", {"glob_pattern": glob_pattern})
    return {"ok": result.ok, "error": result.error if not result.ok else None}


def _run_batch_auth(preview: str) -> dict[str, Any]:
    bridge = _require_bridge()
    session = _ACTIVE["session"]
    bridge._session_provider = lambda: session
    bridge.lifecycle_conversation_id = session.conversation_id
    bridge.lifecycle.start_or_activate("ordinary")
    choice = bridge.confirm_task_authorization(preview)
    return {"choice": choice}


def _verify_disk() -> dict[str, Any]:
    paths = _ACTIVE["paths"]
    rows = []
    for project_id in _ACTIVE["project_ids"]:
        root = project_dir(paths, project_id)
        rows.append({"project_id": project_id, "exists": root.exists()})
    return {"projects": rows}


def _wait_for_confirm_request(before: int, timeout_sec: float) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if any(
            event.get("type") == "confirm.request"
            for event in _ACTIVE["events"][before:]
        ):
            return True
        time.sleep(0.02)
    return False


def _confirm_status() -> dict[str, Any]:
    bridge = _ACTIVE.get("bridge")
    pending = getattr(bridge, "_pending_confirm_id", None) if bridge is not None else None
    done = [event for event in _ACTIVE.get("events", []) if event.get("type") == "confirm.done"]
    requests = [event for event in _ACTIVE.get("events", []) if event.get("type") == "confirm.request"]
    return {
        "pending_confirm_id": pending,
        "confirm_done_count": len(done),
        "confirm_request_count": len(requests),
        "last_confirm_done": done[-1] if done else None,
    }


async def main() -> None:
    token = secrets.token_hex(4)
    labels = ("alpha", "beta", "gamma")
    project_ids = [f"random-number-{label}-{token}" for label in labels]

    with temporary_agent_paths() as paths:
        for project_id in project_ids:
            create_project(paths, project_id)

        session = _companion_session(paths, f"companion-live-e2e-{secrets.token_hex(4)}")
        _ACTIVE["session"] = session
        _ACTIVE["paths"] = paths
        _ACTIVE["token"] = token
        _ACTIVE["project_ids"] = project_ids

        patches = [
            patch.object(WsBridge, "__init__", _patched_ws_bridge_init),
            patch.object(server, "resume_desktop_or_create", lambda _paths: session),
            patch.object(server, "emit_corruption_notices", lambda *_a, **_k: None),
            patch.object(server, "_emit_pending_work_card_references", lambda *_a, **_k: None),
        ]
        for item in patches:
            item.start()

        try:
            async with serve(server.WsSessionHandler(paths).handle, "127.0.0.1", 0) as ws_server:
                port = ws_server.sockets[0].getsockname()[1]
                if port in (5173, 5174, 8000, 8765):
                    raise RuntimeError("reserved port selected")
                print(
                    json.dumps(
                        {
                            "fixture": "companion-home-live-ws",
                            "port": port,
                            "session_id": session.conversation_id,
                            "token": token,
                            "project_ids": project_ids,
                            "agent_root": str(paths.agent_root),
                            "workspace": str(paths.workspace),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

                while True:
                    line = await asyncio.to_thread(sys.stdin.buffer.readline)
                    if not line:
                        break
                    raw = line.strip()
                    if raw == b"stop":
                        break
                    if not raw:
                        continue
                    try:
                        command = json.loads(raw)
                    except json.JSONDecodeError:
                        print(json.dumps({"error": "invalid_json_command"}), flush=True)
                        continue
                    op = command.get("op")
                    try:
                        if op == "trash":
                            glob_pattern = command.get("glob_pattern") or f"random-number-*-{token}"
                            before_events = len(_ACTIVE["events"])
                            error: dict[str, Any] | None = None
                            payload: dict[str, Any] | None = None

                            def trash_worker() -> None:
                                nonlocal error, payload
                                try:
                                    payload = _run_trash(glob_pattern)
                                except Exception as exc:  # pragma: no cover - surfaced to browser harness
                                    error = {"error": str(exc)}

                            thread = threading.Thread(target=trash_worker, daemon=True)
                            thread.start()
                            _ACTIVE["workers"].append(thread)
                            emitted = _wait_for_confirm_request(
                                before_events,
                                float(command.get("await_confirm_sec", 8)),
                            )
                            print(
                                json.dumps(
                                    {
                                        "op": "trash",
                                        "thread_alive": thread.is_alive(),
                                        "confirm_request_emitted": emitted,
                                        "status": _confirm_status(),
                                    },
                                    ensure_ascii=False,
                                ),
                                flush=True,
                            )
                        elif op == "trash_wait":
                            thread = next((item for item in _ACTIVE["workers"] if item.is_alive()), None)
                            if thread is not None:
                                thread.join(float(command.get("timeout_sec", 30)))
                            print(
                                json.dumps(
                                    {
                                        "op": "trash_wait",
                                        "thread_alive": bool(thread and thread.is_alive()),
                                        "status": _confirm_status(),
                                        "disk": _verify_disk(),
                                    },
                                    ensure_ascii=False,
                                ),
                                flush=True,
                            )
                        elif op == "batch_auth":
                            preview = command.get("preview")
                            if not isinstance(preview, str) or not preview.strip():
                                print(json.dumps({"error": "batch_auth_requires_preview"}), flush=True)
                                continue
                            before_events = len(_ACTIVE["events"])

                            def auth_worker() -> None:
                                _run_batch_auth(preview)

                            thread = threading.Thread(target=auth_worker, daemon=True)
                            thread.start()
                            _ACTIVE["workers"].append(thread)
                            emitted = _wait_for_confirm_request(
                                before_events,
                                float(command.get("await_confirm_sec", 8)),
                            )
                            print(
                                json.dumps(
                                    {
                                        "op": "batch_auth",
                                        "thread_alive": thread.is_alive(),
                                        "confirm_request_emitted": emitted,
                                        "status": _confirm_status(),
                                    },
                                    ensure_ascii=False,
                                ),
                                flush=True,
                            )
                        elif op == "verify_disk":
                            print(json.dumps({"op": "verify_disk", **_verify_disk()}), flush=True)
                        elif op == "confirm_status":
                            print(json.dumps({"op": "confirm_status", **_confirm_status()}), flush=True)
                        else:
                            print(json.dumps({"error": "unknown_op", "op": op}), flush=True)
                    except Exception as exc:
                        print(json.dumps({"error": "fixture_op_failed", "detail": str(exc)}), flush=True)
        finally:
            for item in reversed(patches):
                item.stop()


if __name__ == "__main__":
    asyncio.run(main())
