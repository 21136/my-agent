from __future__ import annotations

import asyncio
from collections import Counter
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from types import SimpleNamespace

from server import WsBridge, WsSessionHandler
from test_work_records_overview_api import _session
from test_work_records_overview_server_protocol import _request, _response


def test_real_handler_traverses_all_project_and_independent_detail_pages(tmp_path):
    async def run():
        session, store = _session(tmp_path)
        expected = {}
        counts = {}
        for project in ("project-api", "project-beta"):
            expected[project] = {}
            counts[project] = Counter()
            for i in range(8):
                digest = f"{i + 1:064x}"
                if project == "project-api":
                    item = store.create_item(title=f"{project}-{i}", scope={"kind": "project", "project_id": project})
                else:
                    item = store.create_item_with_requirement(
                        title="多项目任务批次", scope={"kind": "independent"}, requirement=f"original-{i}",
                        event_id=f"worker-batch-original-{digest}", idempotency_key=f"worker-batch-original:{digest}")
                expected[project][item["item_id"]] = set()
                for n in range(6):
                    kwargs = {"event_id": f"req-{project}-{i}-{n}"}
                    if project == "project-beta":
                        kwargs = {"event_id": f"worker-batch-task-{digest}-{n}",
                                  "idempotency_key": f"worker-batch-task:{digest}:{n}",
                                  "source_ref": {"kind": "project", "id": project}}
                    req = store.add_requirement(item["item_id"], f"requirement-{project}-{i}-{n}", **kwargs)
                    expected[project][item["item_id"]].add(req["requirement_id"])
                    status = "pending"
                    if i == 0 and n in (0, 1):
                        run_id = f"run-{project}-{n}"
                        store.begin_requirement_run(item["item_id"], req["requirement_id"], run_id=run_id, expected_current_run_id=None)
                        if n == 0:
                            store.record_result_if_current_run(item["item_id"], req["requirement_id"], run_id=run_id,
                                result_id=f"result-{project}", status="blocked", summary="blocked", verification={"status": "not_verified"})
                            status = "blocked"
                        else:
                            status = "in_progress"
                    counts[project][status] += 1
        independent = store.create_item(title="independent", scope={"kind": "independent"})
        independent_req = store.add_requirement(independent["item_id"], "independent requirement", event_id="independent")
        expected[("independent", independent["item_id"])] = {independent["item_id"]: {independent_req["requirement_id"]}}
        events = []
        bridge = WsBridge(emit=events.append, paths=session.paths)
        handler = WsSessionHandler(session.paths)
        repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))

        async def ask(*, cursor=None, detail=True, current=session, current_repl=repl):
            options = {"page_size": 1}
            if detail is not None:
                options["detail_paging"] = detail
            request = _request(current, cursor=cursor, **options)
            await handler._handle_incoming(request, current_repl, bridge)
            result = _response(events, request["request_id"])
            assert result["session_id"] == current.conversation_id
            return result

        first = await ask()
        assert first["state"] == "visible" and first["page"]["total_groups"] == 3
        snapshot = first["snapshot_id"]
        groups = []
        group_page = first
        while True:
            assert group_page["snapshot_id"] == snapshot
            groups.extend(group_page["groups"])
            cursor = group_page["page"]["next_cursor"]
            if not cursor:
                break
            group_page = await ask(cursor=cursor)
        assert len(groups) == 3 and len({(g["kind"], g["id"]) for g in groups}) == 3
        seen = {}
        sample_cursor = None
        for group in groups:
            key = group["id"] if group["kind"] == "project" else ("independent", group["id"])
            if group["kind"] == "project":
                assert group["status_counts"] == dict(counts[key])
                assert group["status"] == "blocked" and group["item_count"] == 8 and group["requirement_count"] == 48
                assert group["items"][0]["status"] == "blocked"
                assert group["items"][0]["requirements"][0]["status"] == "blocked"
                assert group["items"][0]["current_results"][0]["status"] == "blocked"
            items = list(group["items"])
            cursor = group["items_page"]["next_cursor"]
            if cursor and sample_cursor is None:
                sample_cursor = cursor
            while cursor:
                page = await ask(cursor=cursor)
                assert page["snapshot_id"] == snapshot and page["detail_page"]["kind"] == "items"
                assert (page["groups"][0]["kind"], page["groups"][0]["id"]) == (group["kind"], group["id"])
                items.extend(page["groups"][0]["items"])
                cursor = page["detail_page"]["next_cursor"]
            assert len(items) == len({i["item_id"] for i in items})
            seen[key] = {}
            for item in items:
                requirements = list(item["requirements"])
                cursor = item["requirements_page"]["next_cursor"]
                while cursor:
                    page = await ask(cursor=cursor)
                    assert page["snapshot_id"] == snapshot and page["detail_page"]["kind"] == "requirements"
                    assert page["detail_page"]["item_id"] == item["item_id"]
                    requirements.extend(page["groups"][0]["items"][0]["requirements"])
                    cursor = page["detail_page"]["next_cursor"]
                ids = [r["requirement_id"] for r in requirements]
                assert len(ids) == len(set(ids))
                seen[key][item["item_id"]] = set(ids)
        assert seen == expected
        assert sample_cursor
        other, _ = _session(tmp_path, "20261030-a1abcd02")
        other_repl = SimpleNamespace(session=other, agent=SimpleNamespace(session=other))
        cross = await ask(cursor=sample_cursor, current=other, current_repl=other_repl)
        assert cross["state"] == "expired" and cross["groups"] == []
        store.add_requirement(independent["item_id"], "new requirement", event_id="changed")
        expired = await ask(cursor=sample_cursor)
        assert expired["state"] == "expired" and expired["groups"] == []
        legacy = await ask(detail=None)
        legacy_groups = []
        while True:
            legacy_groups.extend(legacy["groups"])
            cursor = legacy["page"]["next_cursor"]
            if not cursor:
                break
            legacy = await ask(cursor=cursor, detail=None)
        projects = {group["id"]: group for group in legacy_groups if group["kind"] == "project"}
        assert set(projects) == {"project-api", "project-beta"}
        for project, group in projects.items():
            assert len(group["items"]) == 8 and "items_page" not in group
            assert all(len(item["requirements"]) == 6 and "requirements_page" not in item for item in group["items"])
            assert {item["item_id"]: {req["requirement_id"] for req in item["requirements"]}
                    for item in group["items"]} == expected[project]

    asyncio.run(run())

