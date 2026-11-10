"""Synthetic companion WS: #110/#111 当前工作 quiet drawer + scoped composer confirms (no LLM)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path

from websockets.asyncio.server import serve

SESSION = "20261110-cccccccc"
RUN_ID = "20261110-run-quiet-001"

LIFECYCLE_SUMMARY = json.dumps({
    "kind": "project_worker_host_lifecycle",
    "result_status": "unknown",
    "host_status": "model_turn_finished",
    "task_id": "task-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "worker_id": "0a32959d-0000-4000-8000-000000000001",
    "write_receipts": [],
    "read_attempts_available": True,
    "read_attempts": [{
        "path": "PROJECT.md",
        "read_id": 1,
        "attempt": 1,
        "result": "success",
        "error_code": "",
    }],
    "acceptance_status": "not_run",
}, separators=(",", ":"))

UNREAD_SAMPLE = [
    {
        "group": ["independent", "current"],
        "item_id": "item-action",
        "requirement_id": "req-action",
        "result_id": f"res-passive-{index}",
    }
    for index in range(34)
]


def _page() -> dict:
    return {
        "page_size": 6,
        "offset": 0,
        "total_groups": 1,
        "total_visible_groups": 1,
        "total_visible_items": 1,
        "total_collapsed_groups": 0,
        "total_collapsed_items": 0,
        "has_more": False,
        "next_cursor": None,
    }


def _overview(request_id: str) -> dict:
    return {
        "type": "work-records.overview.result",
        "session_id": SESSION,
        "request_id": request_id,
        "state": "visible",
        "work_record_revision": 3,
        "unread_status": "known",
        "unread_important_results": UNREAD_SAMPLE,
        "unread_important_results_total": 34,
        "unread_important_results_truncated": False,
        "visibility_limitations": [],
        "visibility_counts": {
            "visible_groups": 1,
            "visible_items": 1,
            "collapsed_groups": 0,
            "collapsed_items": 0,
        },
        "snapshot_id": "quiet-drawer-synthetic",
        "page": _page(),
        "groups": [{
            "kind": "independent",
            "id": "current",
            "visibility": "visible",
            "status": "unknown",
            "status_counts": {"unknown": 1},
            "item_count": 1,
            "requirement_count": 1,
            "items": [{
                "item_id": "item-action",
                "title": "推进文档",
                "status": "unknown",
                "status_counts": {"unknown": 1},
                "requirements": [{
                    "requirement_id": "req-action",
                    "text": "核对 PROJECT.md 写入",
                    "status": "unknown",
                    "history": [{
                        "run_id": "run-history-1",
                        "result_id": "result-history-1",
                        "status": "unknown",
                        "summary": LIFECYCLE_SUMMARY,
                        "verification_status": "not_verified",
                        "recorded_at": 100,
                    }, {
                        "run_id": "run-history-2",
                        "result_id": "result-history-2",
                        "status": "unknown",
                        "summary": LIFECYCLE_SUMMARY,
                        "verification_status": "not_verified",
                        "recorded_at": 99,
                    }, {
                        "run_id": "run-history-3",
                        "result_id": "result-history-3",
                        "status": "unknown",
                        "summary": LIFECYCLE_SUMMARY,
                        "verification_status": "not_verified",
                        "recorded_at": 98,
                    }],
                    "history_page": {
                        "page_size": 10,
                        "offset": 0,
                        "has_more": False,
                        "next_cursor": None,
                        "scope": "this_requirement_only",
                    },
                }],
                "current_results": [{
                    "requirement_id": "req-action",
                    "result_id": "res-action-1",
                    "status": "unknown",
                    "read_status": "unread",
                }],
            }],
        }],
    }


def _session_banner() -> dict:
    return {
        "type": "session.banner",
        "session_id": SESSION,
        "topics": [],
        "turn_mode": "agent",
        "turn_mode_label": "动手",
        "phase": "S4",
        "active_shell": "grow",
    }


def _session_list() -> dict:
    return {
        "type": "session.list",
        "sessions": [{
            "session_id": SESSION,
            "title": "Quiet drawer harness",
            "preview": "companion synthetic",
            "updated_at": "2026-11-10T06:00:00.000Z",
            "message_count": 0,
        }],
    }


_pending_confirms: dict[str, dict] = {}


async def _push_confirm_scenario(socket) -> None:
    await socket.send(json.dumps({
        "type": "execution.state",
        "run_id": RUN_ID,
        "state": "running",
        "sequence": 1,
    }))
    ordinary_id = "confirm-tool-trash"
    _pending_confirms[ordinary_id] = {"kind": "ordinary"}
    await socket.send(json.dumps({
        "type": "confirm.request",
        "session_id": SESSION,
        "run_id": RUN_ID,
        "request_id": ordinary_id,
        "preview": "软删除到 _trash workspace 项目\nids=harness-demo",
        "allow_approve_all": False,
    }))
    await asyncio.sleep(0.05)
    post_turn_id = "confirm-post-turn-harness"
    _pending_confirms[post_turn_id] = {"kind": "work_record_post_turn"}
    await socket.send(json.dumps({
        "type": "confirm.request",
        "session_id": SESSION,
        "request_id": post_turn_id,
        "confirm_kind": "work_record_post_turn",
        "preview": "确认写入工作记录：推进文档结果",
        "allow_approve_all": False,
    }))
    verification_id = "confirm-verification-harness"
    _pending_confirms[verification_id] = {"kind": "project_worker_requirement_verification"}
    await socket.send(json.dumps({
        "type": "confirm.request",
        "session_id": SESSION,
        "run_id": RUN_ID,
        "request_id": verification_id,
        "confirm_subtype": "project_worker_requirement_verification",
        "preview": "运行单目标测试以记录自报告结果",
        "allow_approve_all": False,
        "project_worker_verification": {
            "session_id": SESSION,
            "project_id": "harness-project",
            "item_id": "item-action",
            "requirement_id": "req-action",
            "project_name": "Harness Project",
            "requirement_text": "核对 PROJECT.md 写入",
            "pytest_target": "tests/test_harness.py::test_target",
            "worker_run_id": "worker-run-harness",
            "reservation_id": "reservation-harness",
        },
    }))


async def handle(socket) -> None:
    bootstrapped = False
    confirms_sent = False

    async def bootstrap() -> None:
        nonlocal bootstrapped
        if bootstrapped:
            return
        bootstrapped = True
        await socket.send(json.dumps(_session_banner()))
        await socket.send(json.dumps(_session_list()))

    try:
        async for raw in socket:
            event = json.loads(raw)
            kind = event.get("type")

            if kind in {"terminal.list", "session.list"}:
                await bootstrap()
                if kind == "session.list":
                    await socket.send(json.dumps(_session_list()))
                continue

            if kind == "session.open":
                await bootstrap()
                continue

            if kind == "work-records.overview.query":
                await bootstrap()
                await socket.send(json.dumps(_overview(event["request_id"])))
                if not confirms_sent:
                    confirms_sent = True
                    await _push_confirm_scenario(socket)
                continue

            if kind == "confirm.response":
                request_id = event.get("request_id")
                meta = _pending_confirms.pop(request_id, None)
                if not meta:
                    continue
                subtype = {}
                if meta["kind"] == "work_record_post_turn":
                    subtype = {"confirm_kind": "work_record_post_turn"}
                elif meta["kind"] == "project_worker_requirement_verification":
                    subtype = {
                        "confirm_subtype": "project_worker_requirement_verification",
                    }
                await socket.send(json.dumps({
                    "type": "confirm.done",
                    "session_id": SESSION,
                    "run_id": RUN_ID,
                    "request_id": request_id,
                    "choice": event.get("choice", "n"),
                    **subtype,
                }))
                continue

            if kind == "work-record.control":
                await socket.send(json.dumps({
                    "type": "work-record.control.done",
                    "session_id": SESSION,
                    "request_id": event.get("request_id", ""),
                    "item_id": event.get("item_id", ""),
                    "requirement_id": event.get("requirement_id", ""),
                    "action": event.get("action", ""),
                    "status": "applied",
                    "message": "合成夹具：仅元数据更新。",
                }))
    finally:
        pass


async def main() -> None:
    async with serve(handle, "127.0.0.1", 0) as server:
        print(json.dumps({
            "port": server.sockets[0].getsockname()[1],
            "session_id": SESSION,
            "companion_unbound": True,
        }), flush=True)
        await asyncio.to_thread(sys.stdin.buffer.readline)


if __name__ == "__main__":
    asyncio.run(main())
