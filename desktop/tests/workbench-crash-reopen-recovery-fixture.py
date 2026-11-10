"""Synthetic WS: late connect + crash/reopen recovery honest 当前工作 (no live model).

Mirrors agent-core/tests/test_workbench_crash_reopen_recovery_harness.py projection:
foreign-owner stale → unknown labels; same-process in_progress → cancel reachable;
session reopen emits conservative warn notice (not silent 执行中).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from websockets.asyncio.server import serve

SESSION = "20261109-crash-reopen"
SOURCE_SESSION = "recovery-source-alpha"
WORK_RECORD_REVISION = 1
SOURCE_REVISION = 3
LATE_WS_MS = float(os.environ.get("WORKBENCH_RECOVERY_LATE_WS_MS", "350"))
RECOVERY_WARN = (
    "检测到上次异常退出：进行中事项已保守标记为待确认，未自动续跑 worker。"
    "请在当前工作中逐项停止或检查。"
)


def _page(visible_groups: int, visible_items: int) -> dict:
    return {
        "page_size": 6,
        "offset": 0,
        "total_groups": visible_groups,
        "total_visible_groups": visible_groups,
        "total_visible_items": visible_items,
        "total_collapsed_groups": 0,
        "total_collapsed_items": 0,
        "has_more": False,
        "next_cursor": None,
    }


def _recovery_overview(request_id: str) -> dict:
    return {
        "type": "work-records.overview.result",
        "session_id": SESSION,
        "request_id": request_id,
        "state": "visible",
        "work_record_revision": WORK_RECORD_REVISION,
        "unread_status": "unknown",
        "visibility_limitations": [],
        "visibility_counts": {
            "visible_groups": 2,
            "visible_items": 3,
            "collapsed_groups": 0,
            "collapsed_items": 0,
            "attention_visible_items": 3,
            "attention_visible_groups": 2,
        },
        "snapshot_id": "recovery-synthetic",
        "unread_important_results": [],
        "page": _page(2, 3),
        "groups": [
            {
                "kind": "independent",
                "id": "current",
                "visibility": "visible",
                "status": "unknown",
                "status_counts": {"unknown": 1, "in_progress": 1, "queued": 1},
                "item_count": 2,
                "requirement_count": 3,
                "items": [
                    {
                        "item_id": "crashed",
                        "title": "crashed worker task",
                        "status": "unknown",
                        "status_counts": {"unknown": 1},
                        "requirements": [{
                            "requirement_id": "stale",
                            "text": "stale in-flight requirement",
                            "status": "unknown",
                        }],
                        "current_results": [],
                    },
                    {
                        "item_id": "live",
                        "title": "same-process live task",
                        "status": "in_progress",
                        "status_counts": {"in_progress": 1, "queued": 1},
                        "requirements": [
                            {
                                "requirement_id": "live",
                                "text": "live in-flight requirement",
                                "status": "in_progress",
                            },
                            {
                                "requirement_id": "queued",
                                "text": "queued stop target",
                                "status": "queued",
                            },
                        ],
                        "current_results": [],
                    },
                ],
            },
            {
                "kind": "project",
                "id": "project-alpha",
                "source_session_id": SOURCE_SESSION,
                "source_revision": SOURCE_REVISION,
                "visibility": "visible",
                "status": "in_progress",
                "status_counts": {"in_progress": 1},
                "item_count": 1,
                "requirement_count": 1,
                "items_page": None,
                "items": [{
                    "item_id": "worker",
                    "title": "project worker task",
                    "status": "in_progress",
                    "status_counts": {"in_progress": 1},
                    "requirements": [{
                        "requirement_id": "w",
                        "text": "cancel still reachable",
                        "status": "in_progress",
                    }],
                    "current_results": [],
                }],
            },
        ],
    }


def _claim_overview(request_id: str) -> dict:
    return {
        "type": "work-records.overview.result",
        "session_id": SESSION,
        "request_id": request_id,
        "state": "visible",
        "work_record_revision": WORK_RECORD_REVISION,
        "unread_status": "unknown",
        "visibility_limitations": [],
        "visibility_counts": {
            "visible_groups": 1,
            "visible_items": 1,
            "collapsed_groups": 0,
            "collapsed_items": 0,
            "attention_visible_items": 1,
            "attention_visible_groups": 1,
        },
        "snapshot_id": "claim-recovery",
        "unread_important_results": [],
        "page": _page(1, 1),
        "groups": [{
            "kind": "project",
            "id": "project-beta",
            "source_session_id": SESSION,
            "source_revision": 3,
            "visibility": "visible",
            "status": "unknown",
            "status_counts": {"unknown": 1},
            "item_count": 1,
            "requirement_count": 1,
            "items": [{
                "item_id": "claim-item",
                "title": "claimed worker",
                "status": "unknown",
                "status_counts": {"unknown": 1},
                "requirements": [{
                    "requirement_id": "claim-req",
                    "text": "claim needs human inspection",
                    "status": "unknown",
                    "claim_recovery_needed": True,
                }],
                "current_results": [],
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
            "title": "Recovery harness session",
            "preview": "crash/reopen synthetic",
            "updated_at": "2026-11-09T14:00:00.000Z",
            "message_count": 0,
        }],
    }


_CONNECTION_SEQ = 0


async def _send_session_bootstrap(socket) -> None:
    await socket.send(json.dumps({
        "type": "notice",
        "level": "warn",
        "text": RECOVERY_WARN,
    }))
    await socket.send(json.dumps(_session_banner()))
    await socket.send(json.dumps(_session_list()))


async def handle(socket) -> None:
    global _CONNECTION_SEQ
    _CONNECTION_SEQ += 1
    conn_seq = _CONNECTION_SEQ
    bootstrapped = False

    async def bootstrap_once() -> None:
        nonlocal bootstrapped
        if bootstrapped:
            return
        bootstrapped = True
        await _send_session_bootstrap(socket)

    async def connection_bootstrap() -> None:
        if conn_seq == 1:
            await asyncio.sleep(LATE_WS_MS / 1000.0)
        await bootstrap_once()

    bootstrap = asyncio.create_task(connection_bootstrap())
    try:
        async for raw in socket:
            event = json.loads(raw)
            kind = event.get("type")

            if kind == "terminal.list":
                if conn_seq > 1:
                    await bootstrap_once()
                continue

            if kind == "session.list":
                await socket.send(json.dumps(_session_list()))
                continue

            if kind == "session.open":
                await _send_session_bootstrap(socket)
                continue

            if kind == "work-records.overview.query":
                request_id = event["request_id"]
                if event.get("claim_recovery_probe"):
                    await socket.send(json.dumps(_claim_overview(request_id)))
                else:
                    await socket.send(json.dumps(_recovery_overview(request_id)))
                continue

            if kind == "work-record.control":
                await socket.send(json.dumps({
                    "type": "work-record.control.done",
                    "session_id": event["session_id"],
                    "request_id": event["request_id"],
                    "item_id": event["item_id"],
                    "project_id": event.get("project_id", ""),
                    "requirement_id": event.get("requirement_id", ""),
                    "action": event["action"],
                    "status": "applied",
                    "message": "合成夹具记录协调；未触碰真实 host。",
                }))
                continue
    finally:
        bootstrap.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await bootstrap


async def main() -> None:
    async with serve(handle, "127.0.0.1", 0) as server:
        print(json.dumps({"port": server.sockets[0].getsockname()[1]}), flush=True)
        await asyncio.to_thread(input)


if __name__ == "__main__":
    asyncio.run(main())
