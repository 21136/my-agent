"""Local synthetic WS endpoint for schedule-launch browser E2E; never creates an Agent or worker."""
import asyncio
import json
from websockets.asyncio.server import serve

SESSION = "schedule-launch-browser-session"
TASK = "Synthetic queued requirement; must not execute"

async def handle(socket):
    await socket.send(json.dumps({"type": "session.banner", "session_id": SESSION}))
    await socket.send(json.dumps({"type": "project.list", "projects": [
        {"id": "synthetic-project", "session_id": "synthetic-source-session", "is_current": False,
          "external_adopted": True, "external_healthy": True}
    ]}))
    async for raw in socket:
        event = json.loads(raw)
        if event.get("type") == "work-records.overview.query":
            request_id = event["request_id"]
            response = {
                "type": "work-records.overview.result", "session_id": SESSION, "request_id": request_id,
                "state": "visible", "aggregation_scope": "selected_project_sessions",
                "selected_project_ids": ["synthetic-project"], "work_record_revision": 1,
                "schedule_preview": {"state": "visible", "candidates": [{"project_id": "synthetic-project",
                    "item_id": "synthetic-item", "requirement_id": "synthetic-req", "priority": 3,
                    "task": TASK, "suggested_read_targets": ["PROJECT.md"], "suggested_write_targets": ["NOTES.md"]}],
                    "blocked": [], "limitations": [], "omissions": []},
                "groups": [{"kind": "project", "id": "synthetic-project", "visibility": "visible", "status": "queued",
                    "status_counts": {"queued": 1}, "item_count": 1, "requirement_count": 1,
                    "items_page": None, "items": [{"item_id": "synthetic-item", "title": "Synthetic item",
                        "status": "queued", "status_counts": {"queued": 1}, "priority": 3,
                        "current_results": [],
                        "requirements": [{"requirement_id": "synthetic-req", "text": TASK, "status": "queued"}]}]}],
                "unread_important_results": [], "unread_status": "known", "visibility_limitations": [],
                "visibility_counts": {"visible_groups": 1, "visible_items": 1, "collapsed_groups": 0, "collapsed_items": 0},
                "snapshot_id": "synthetic-snapshot",
                "page": {"page_size": 6, "offset": 0, "total_groups": 1, "total_visible_groups": 1,
                    "total_visible_items": 1, "total_collapsed_groups": 0, "total_collapsed_items": 0,
                    "has_more": False, "next_cursor": None},
            }
            await socket.send(json.dumps(response))
        elif event.get("type") == "work-records.schedule.dispatch":
            await socket.send(json.dumps({
                "type": "work-records.schedule.dispatch.done",
                "session_id": event["session_id"],
                "request_id": event["request_id"],
                "status": "rejected",
                "message": "合成夹具未启动 worker（仅记录 dispatch 帧）。",
            }))

async def main():
    async with serve(handle, "127.0.0.1", 0) as server:
        print(json.dumps({"port": server.sockets[0].getsockname()[1]}), flush=True)
        await asyncio.to_thread(input)

asyncio.run(main())
