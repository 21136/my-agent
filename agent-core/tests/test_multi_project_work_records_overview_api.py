from __future__ import annotations

import json
import sys
from pathlib import Path
from threading import Event

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from multi_project_work_record_authorization import authorize_project_record_sources
from multi_project_work_record_bindings import AuthorizedProjectRecordSource
from multi_project_work_records_overview_api import query_authorized_project_records_overview
from paths import AgentPaths
from project_switch import record_project_session
from session import Session, SessionMeta, create_new
from work_records import WorkRecordStore


def _fixture(root: Path):
    paths = AgentPaths(root, root / "workspace", root / "data", root / "evolve")
    paths.workspace.mkdir(parents=True, exist_ok=True)
    paths.evolve.mkdir(parents=True, exist_ok=True)
    (paths.evolve / "_index.core.toml").write_text("", encoding="utf-8")
    parent = create_new(paths, conversation_id="20261107-11111111")
    projects = []
    for index, project_id in enumerate(("project-alpha", "project-beta"), start=2):
        (paths.workspace / project_id).mkdir(parents=True)
        (paths.workspace / project_id / "PROJECT.md").write_text(f"# {project_id}\n", encoding="utf-8")
        source = Session(
            f"20261107-{index:08x}", paths.data / "sessions" / f"20261107-{index:08x}", "synthetic",
            SessionMeta(project_id=project_id), [], paths,
        )
        source.session_dir.mkdir(parents=True)
        source.save()
        record_project_session(paths, project_id, source.conversation_id)
        store = WorkRecordStore(source)
        item = store.create_item(title=f"item {project_id}", scope={"kind": "project", "project_id": project_id})
        requirement = store.add_requirement(item["item_id"], f"change {project_id}", event_id=f"req-{project_id}")
        store.begin_requirement_run(item["item_id"], requirement["requirement_id"],
                                    run_id=f"run-{project_id}", expected_current_run_id=None)
        store.record_result_if_current_run(
            item["item_id"], requirement["requirement_id"], run_id=f"run-{project_id}",
            result_id=f"result-{project_id}", status="unknown", summary="host result not verified",
            verification={"status": "not_verified"},
        )
        projects.append((project_id, source, item, requirement))
    return parent, projects


def test_structured_multi_project_overview_requires_host_receipts_and_filters_exact_scope(tmp_path):
    parent, projects = _fixture(tmp_path)
    confirmations = []
    sources = authorize_project_record_sources(
        parent, [row[0] for row in projects],
        confirm_fn=lambda preview, allow_all: confirmations.append((preview, allow_all)) or "y",
        cancel_event=Event(),
    )
    source_snapshots_before = {
        project_id: sources[project_id].store.snapshot() for project_id, *_rest in projects
    }
    result = query_authorized_project_records_overview(
        parent, [row[0] for row in projects], sources, cancel_event=Event(),
    )
    assert result["state"] == "visible"
    assert [group["id"] for group in result["groups"]] == ["project-alpha", "project-beta"]
    assert result["visibility_counts"]["visible_items"] == 2
    assert result["unread_status"] == "unknown"
    assert "project-alpha" in confirmations[0][0] and confirmations[0][1] is False
    assert {
        project_id: sources[project_id].store.snapshot() for project_id, *_rest in projects
    } == source_snapshots_before
    for group, (project_id, source, _item, requirement) in zip(result["groups"], projects):
        assert group["source_session_id"] == source.conversation_id
        assert group["source_revision"] > 0
        assert group["items"][0]["requirements"][0]["requirement_id"] == requirement["requirement_id"]
        assert group["items"][0]["current_results"][0]["read_status"] == "unknown"
        assert group["items"][0]["requirements"][0]["text"] == f"change {project_id}"


def test_authorized_requirement_history_pages_are_bounded_and_revision_bound(tmp_path):
    parent, projects = _fixture(tmp_path)
    project_id, source_session, item, requirement = projects[0]
    store = WorkRecordStore(source_session)
    current_run = f"run-{project_id}"
    for index in range(12):
        next_run = f"history-run-{index:02d}"
        store.begin_requirement_run(item["item_id"], requirement["requirement_id"],
                                    run_id=next_run, expected_current_run_id=current_run)
        store.record_result_if_current_run(
            item["item_id"], requirement["requirement_id"], run_id=next_run,
            result_id=f"history-result-{index:02d}", status="unknown", summary=f"synthetic {index}",
            verification={"status": "not_verified"},
        )
        current_run = next_run

    def query(cursor=None):
        sources = authorize_project_record_sources(
            parent, [project_id], confirm_fn=lambda _preview, _all: "y", cancel_event=Event(),
        )
        return query_authorized_project_records_overview(
            parent, [project_id], sources, cancel_event=Event(), history_project_id=project_id,
            history_item_id=item["item_id"], history_requirement_id=requirement["requirement_id"],
            history_cursor=cursor, history_page_size=5,
        )

    first = query()
    assert first["state"] == "visible"
    assert len(first["history"]) == 5
    assert first["history_page"]["has_more"] is True
    second = query(first["history_page"]["next_cursor"])
    assert len(second["history"]) == 5
    assert set(row["result_id"] for row in first["history"]).isdisjoint(
        row["result_id"] for row in second["history"])
    assert second["source"]["revision"] == first["source"]["revision"]
    third = query(second["history_page"]["next_cursor"])
    assert len(third["history"]) == 3
    assert third["history_page"]["has_more"] is False
    all_results = [row["result_id"] for page in (first, second, third) for row in page["history"]]
    assert len(all_results) == 13
    assert len(set(all_results)) == 13

    # Cursor contents are visible, but changing its offset beyond the source
    # history must fail closed instead of returning a misleading empty page.
    import base64
    import json
    cursor_payload = json.loads(base64.urlsafe_b64decode(
        second["history_page"]["next_cursor"] + "=" * (-len(second["history_page"]["next_cursor"]) % 4)))
    cursor_payload["offset"] = 10_000
    tampered = base64.urlsafe_b64encode(json.dumps(
        cursor_payload, sort_keys=True, separators=(",", ":")).encode()).decode().rstrip("=")
    rejected = query(tampered)
    assert rejected["state"] == "error"
    assert rejected.get("history", []) == []

    store.create_item(title="revision changes invalidate history cursor",
                      scope={"kind": "project", "project_id": project_id})
    stale = query(second["history_page"]["next_cursor"])
    assert stale["state"] == "error"
    assert stale.get("history", []) == []


def test_websocket_selected_project_history_paging_requires_confirmation_and_forwards_cursor(tmp_path):
    import asyncio
    import uuid
    from types import SimpleNamespace
    from server import WsBridge, WsSessionHandler

    parent, projects = _fixture(tmp_path)
    project_id, source_session, item, requirement = projects[0]
    store = WorkRecordStore(source_session)
    current_run = f"run-{project_id}"
    for index in range(7):
        run_id = f"ws-history-{index}"
        store.begin_requirement_run(item["item_id"], requirement["requirement_id"],
                                   run_id=run_id, expected_current_run_id=current_run)
        store.record_result_if_current_run(item["item_id"], requirement["requirement_id"],
            run_id=run_id, result_id=f"ws-result-{index}", status="unknown", summary=f"fixture {index}",
            verification={"status": "not_verified"})
        current_run = run_id

    async def exercise():
        events = []
        confirmations = []
        bridge = WsBridge(emit=events.append, paths=parent.paths)
        bridge.confirm_with_receipt = lambda preview, allow_all: (
            confirmations.append((preview, allow_all)) or ("y", "synthetic-receipt"))
        handler = WsSessionHandler(parent.paths)
        repl = SimpleNamespace(session=parent, agent=SimpleNamespace(session=parent))

        async def ask(cursor=None):
            request = {"type": "work-records.overview.query", "session_id": parent.conversation_id,
                "request_id": str(uuid.uuid4()), "project_ids": [project_id],
                "history_project_id": project_id, "history_item_id": item["item_id"],
                "history_requirement_id": requirement["requirement_id"], "history_page_size": 3}
            if cursor is not None:
                request["history_cursor"] = cursor
            await handler._handle_incoming(request, repl, bridge)
            return next(event for event in reversed(events)
                        if event.get("request_id") == request["request_id"])

        first = await ask()
        second = await ask(first["history_page"]["next_cursor"])
        third = await ask(second["history_page"]["next_cursor"])
        assert len(first["history"]) == 3
        assert len(second["history"]) == 3
        assert len(third["history"]) == 2
        assert len({row["result_id"] for result in (first, second, third)
                    for row in result["history"]}) == 8
        assert len(confirmations) == 3
        assert all(allow_all is False for _preview, allow_all in confirmations)

    asyncio.run(exercise())


def test_structured_multi_project_overview_rejects_forged_or_partial_source_receipts(tmp_path):
    parent, projects = _fixture(tmp_path)
    sources = authorize_project_record_sources(
        parent, [row[0] for row in projects], confirm_fn=lambda _preview, _all: "y", cancel_event=Event(),
    )
    partial = query_authorized_project_records_overview(parent, [row[0] for row in projects],
                                                        {projects[0][0]: sources[projects[0][0]]},
                                                        cancel_event=Event())
    forged_sources = dict(sources)
    trusted = sources[projects[0][0]]
    forged_sources[projects[0][0]] = AuthorizedProjectRecordSource(
        trusted.authorization_id, trusted.conversation_id, trusted.project_id, trusted.store,
    )
    forged = query_authorized_project_records_overview(parent, [row[0] for row in projects],
                                                        forged_sources, cancel_event=Event())
    assert partial["state"] == "error" and partial["groups"] == []
    assert forged["state"] == "error" and forged["groups"] == []


def test_structured_multi_project_overview_discards_on_mapping_change(tmp_path):
    parent, projects = _fixture(tmp_path)
    sources = authorize_project_record_sources(
        parent, [row[0] for row in projects], confirm_fn=lambda _preview, _all: "y", cancel_event=Event(),
    )
    # Rebind one mapping after the receipt was issued; the overview must not merge
    # rows from different project sessions under the same displayed project ID.
    record_project_session(parent.paths, projects[0][0], projects[1][1].conversation_id)
    result = query_authorized_project_records_overview(
        parent, [row[0] for row in projects], sources, cancel_event=Event(),
    )
    assert result["state"] == "error"
    assert result["groups"] == []


def test_selected_projects_history_is_one_merged_revision_bound_page(tmp_path):
    parent, projects = _fixture(tmp_path)
    project_ids = [row[0] for row in projects]

    def query(cursor=None):
        sources = authorize_project_record_sources(
            parent, project_ids, confirm_fn=lambda _preview, _all: "y", cancel_event=Event(),
        )
        return query_authorized_project_records_overview(
            parent, project_ids, sources, cancel_event=Event(), history_scope="selected_projects_history",
            history_cursor=cursor, history_page_size=1,
        )

    before = {project_id: WorkRecordStore(source).with_locked_data(lambda data: data)
              for project_id, source, *_ in projects}
    first = query()
    assert first["state"] == "visible"
    assert first["history_scope"] == "selected_projects_history"
    assert first["history_page"]["scope"] == "selected_projects_history"
    assert len(first["history"]) == 1
    assert first["history_page"]["has_more"]
    assert {row["project_id"] for row in first["history_sources"]} == set(project_ids)
    assert first["history_sources"] == [
        {"project_id": pid, "session_id": source.conversation_id,
         "revision": before[pid]["revision"]}
        for pid, source, *_ in projects
    ]
    second = query(first["history_page"]["next_cursor"])
    assert len(second["history"]) == 1
    assert second["history"][0]["project_id"] != first["history"][0]["project_id"]
    assert second["history_page"]["has_more"] is False
    after = {pid: WorkRecordStore(source).with_locked_data(lambda data: data)
             for pid, source, *_ in projects}
    assert after == before

    # Reuse the original receipts after rebinding. Reauthorization itself must
    # fail closed; the API must also reject a cursor against changed mappings.
    original_sources = authorize_project_record_sources(
        parent, project_ids, confirm_fn=lambda _preview, _all: "y", cancel_event=Event(),
    )
    record_project_session(parent.paths, project_ids[0], projects[1][1].conversation_id)
    stale = query_authorized_project_records_overview(
        parent, project_ids, original_sources, cancel_event=Event(),
        history_scope="selected_projects_history", history_cursor=first["history_page"]["next_cursor"],
        history_page_size=1,
    )
    assert stale["state"] == "error" and stale.get("history", []) == []
