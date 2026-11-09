"""Synthetic WS server for multi-project schedule workbench GUI harness.

Never starts an Agent, worker, or live model. Mirrors confirm-gated overview reads,
schedule_preview, dispatch acknowledgement, and post-dispatch in_progress projection.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from websockets.asyncio.server import serve

SESSION = "schedule-workbench-multi-session"
PROJECTS = ("synthetic-a", "synthetic-b", "synthetic-c")
SOURCE_SESSIONS = {
    "synthetic-a": "source-session-a",
    "synthetic-b": "source-session-b",
    "synthetic-c": "source-session-c",
}


def _task(project_id: str) -> str:
    return f"Synthetic queued requirement for {project_id}; harness must not execute"


def _candidate(project_id: str, index: int) -> dict:
    return {
        "project_id": project_id,
        "item_id": f"item-{index}",
        "requirement_id": f"req-{index}",
        "priority": 4 - min(index, 3),
        "task": _task(project_id),
        "suggested_read_targets": ["PROJECT.md"],
        "suggested_write_targets": ["NOTES.md"],
    }


def _group(project_id: str, *, status: str, index: int, source_session: str | None = None) -> dict:
    requirement_status = status if status in {"queued", "in_progress"} else "queued"
    group = {
        "kind": "project",
        "id": project_id,
        "visibility": "visible",
        "status": status,
        "status_counts": {status: 1},
        "item_count": 1,
        "requirement_count": 1,
        "items_page": None,
        "items": [{
            "item_id": f"item-{index}",
            "title": f"Harness item {project_id}",
            "status": status,
            "status_counts": {status: 1},
            "priority": 4 - min(index, 3),
            "current_results": [],
            "requirements": [{
                "requirement_id": f"req-{index}",
                "text": _task(project_id),
                "status": requirement_status,
            }],
        }],
    }
    if source_session:
        group["source_session_id"] = source_session
        group["source_revision"] = 3
    return group


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


def _overview(
    request_id: str,
    project_ids: list[str],
    *,
    mode: str,
) -> dict:
    candidates = [_candidate(project_id, index + 1) for index, project_id in enumerate(project_ids)]
    if mode == "in_progress":
        groups = [
            _group(project_id, status="in_progress", index=index + 1, source_session=SOURCE_SESSIONS[project_id])
            for index, project_id in enumerate(project_ids)
        ]
        schedule_preview = {
            "state": "visible",
            "candidates": [],
            "blocked": [{"project_id": project_id, "item_id": f"item-{index + 1}",
                         "requirement_id": f"req-{index + 1}", "priority": 3,
                         "reasons": ["要求正在执行；请先在概况中请求停止 worker"]}
                        for index, project_id in enumerate(project_ids)],
            "limitations": ["进行中的要求不会出现在可启动候选中"],
            "omissions": [],
        }
    else:
        groups = [_group(project_id, status="queued", index=index + 1, source_session=SOURCE_SESSIONS[project_id])
                  for index, project_id in enumerate(project_ids)]
        schedule_preview = {
            "state": "visible",
            "candidates": candidates,
            "blocked": [],
            "limitations": [],
            "omissions": [],
        }
    return {
        "type": "work-records.overview.result",
        "session_id": SESSION,
        "request_id": request_id,
        "state": "visible",
        "aggregation_scope": "selected_project_sessions",
        "selected_project_ids": project_ids,
        "work_record_revision": 1,
        "schedule_preview": schedule_preview,
        "groups": groups,
        "unread_important_results": [],
        "unread_status": "unknown",
        "visibility_limitations": ["mixed_current_and_confirmed_project_snapshots"],
        "visibility_counts": {
            "visible_groups": len(groups),
            "visible_items": len(groups),
            "collapsed_groups": 0,
            "collapsed_items": 0,
        },
        "snapshot_id": f"synthetic-{mode}-{len(project_ids)}",
        "page": _page(len(groups), len(groups)),
    }


def _local_overview(request_id: str) -> dict:
    """Session-local probe: keep the work entry visible while mapped sources exist."""
    return {
        "type": "work-records.overview.result",
        "session_id": SESSION,
        "request_id": request_id,
        "state": "no_records",
        "groups": [],
        "unread_important_results": [],
        "unread_status": "known",
        "visibility_limitations": ["mapped_project_sources_available_for_schedule"],
        "visibility_counts": {"visible_groups": 0, "visible_items": 0, "collapsed_groups": 0, "collapsed_items": 0},
        "snapshot_id": None,
        "page": _page(0, 0),
    }


def _confirm_preview(project_ids: list[str]) -> str:
    lines = [
        "确认只读读取下列项目 Session 的工作记录概况：",
        *(f"- 项目 {project_id} → Session {SOURCE_SESSIONS[project_id]}" for project_id in project_ids),
        "批准后仅聚合所选来源内的 project scope 记录；不会启动 worker。",
    ]
    return "\n".join(lines)


async def handle(socket) -> None:
  await socket.send(json.dumps({"type": "session.banner", "session_id": SESSION}))
  await socket.send(json.dumps({
      "type": "project.list",
      "projects": [{
          "id": project_id,
          "session_id": SOURCE_SESSIONS[project_id],
          "is_current": False,
          "external_adopted": True,
          "external_healthy": True,
      } for project_id in PROJECTS],
  }))

  dispatched_project_ids: list[str] = []
  pending_overview: dict[str, list[str]] = {}
  pending_confirm_to_overview: dict[str, str] = {}

  async for raw in socket:
      event = json.loads(raw)
      kind = event.get("type")

      if kind == "work-records.overview.query":
          request_id = event["request_id"]
          project_ids = event.get("project_ids")
          if not project_ids:
              await socket.send(json.dumps(_local_overview(request_id)))
              continue
          project_ids = list(dict.fromkeys(project_ids))
          if dispatched_project_ids and sorted(project_ids) == sorted(dispatched_project_ids):
              await socket.send(json.dumps(_overview(request_id, project_ids, mode="in_progress")))
              continue
          pending_overview[request_id] = project_ids
          confirm_id = str(uuid.uuid4())
          pending_confirm_to_overview[confirm_id] = request_id
          await socket.send(json.dumps({
              "type": "confirm.request",
              "session_id": SESSION,
              "run_id": "harness-run",
              "request_id": confirm_id,
              "preview": _confirm_preview(project_ids),
              "allow_approve_all": False,
          }))
          continue

      if kind == "confirm.response":
          confirm_id = event.get("request_id")
          overview_request = pending_confirm_to_overview.pop(confirm_id, None)
          if event.get("choice") != "y" or not overview_request:
              continue
          project_ids = pending_overview.pop(overview_request, None)
          if not project_ids:
              continue
          await socket.send(json.dumps(_overview(overview_request, project_ids, mode="queued")))
          continue

      if kind == "work-records.schedule.dispatch":
          project_ids = list(dict.fromkeys(event.get("project_ids") or []))
          dispatched_project_ids = project_ids
          await socket.send(json.dumps({
              "type": "work-records.schedule.dispatch.done",
              "session_id": event["session_id"],
              "request_id": event["request_id"],
              "status": "applied",
              "message": "合成夹具记录调度请求；未启动真实 worker。",
              "tool_ok": True,
              "workers": [{"project_id": project_id} for project_id in project_ids],
          }))
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
              "message": "合成夹具记录取消协调；未触碰真实 host。",
          }))


async def main() -> None:
    async with serve(handle, "127.0.0.1", 0) as server:
        print(json.dumps({"port": server.sockets[0].getsockname()[1]}), flush=True)
        await asyncio.to_thread(input)


if __name__ == "__main__":
    asyncio.run(main())
