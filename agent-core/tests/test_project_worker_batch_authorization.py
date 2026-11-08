from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

sys.path.insert(0, str(Path(__file__).parent))

from project_mode import create_project
from project_worker_host import ProjectWorkerHostRequest
from session import create_new
from tests.isolation_helpers import temporary_agent_paths
from test_project_worker_host import _host, _request, _reservation_live, _response


def _setup(monkeypatch):
    paths_ctx = temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file"))
    paths = paths_ctx.__enter__()
    for pid in ("project-alpha", "project-beta"):
        create_project(paths, pid, project_template="light")
        (paths.workspace / pid / "README.md").write_text(f"{pid} source\n", encoding="utf-8")
    session = create_new(paths, conversation_id="20261106-aaaa0001")
    session._ordinary_work_intent = "execute"
    session.meta.active_shell = "project"
    session.meta.project_id = "project-alpha"
    session.meta.project_root = "workspace/project-alpha"
    host, _clients = _host(paths, monkeypatch, lambda _request: pytest.fail("must not POST"))
    return paths_ctx, paths, session, host


def test_batch_authorization_preview_discloses_each_project_and_data_classes(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file")) as paths:
        for pid in ("project-alpha", "project-beta"):
            create_project(paths, pid, project_template="light")
        session = create_new(paths, conversation_id="20261106-aaaa0002")
        session._ordinary_work_intent = "execute"
        host, _clients = _host(paths, monkeypatch, lambda _request: pytest.fail("must not POST"))
        requests = [_request("project-alpha", "a", "Update alpha", reads=("README.md",), writes=("NOTES.md",)),
                    _request("project-beta", "b", "Update beta", reads=("PLAN.md",), writes=("RESULT.md",))]
        reservations = host.reserve_many(session, requests, parent_run_id="batch-run", turn_epoch=7,
                                         live_binding_check=_reservation_live(session, "batch-run", 7))
        previews = []
        grant = host.authorize_batch(session, requests, reservations, confirm_fn=lambda value: previews.append(value) or "y",
                                     cancel_event=threading.Event(), parent_run_id="batch-run", turn_epoch=7,
                                     live_binding_check=_reservation_live(session, "batch-run", 7))
        assert grant is not None
        assert previews[0].startswith("PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n")
        payload = json.loads(previews[0].split("\n", 1)[1])
        assert payload["projects"] == [
            {"project_id": "project-alpha", "task_id": "a", "task": "Update alpha",
             "project_root": "workspace/project-alpha", "read_targets": ["README.md"], "write_targets": ["NOTES.md"],
             "generated_content_write": "Model-generated content may be written only to write_targets through ToolExecutor/write_policy and existing lease/source-snapshot/readback checks."},
            {"project_id": "project-beta", "task_id": "b", "task": "Update beta",
             "project_root": "workspace/project-beta", "read_targets": ["PLAN.md"], "write_targets": ["RESULT.md"],
             "generated_content_write": "Model-generated content may be written only to write_targets through ToolExecutor/write_policy and existing lease/source-snapshot/readback checks."},
        ]
        assert payload["endpoint"] == {"provider": "0x567", "model_id": "0x567-flash", "model": "gpt-6-luna", "url": "https://api-cdn.0x567.com/v1/chat/completions"}
        assert "parent_conversation" in payload["not_disclosed"]
        assert payload["authorization"]["expires_in_seconds"] == 3600
        assert payload["not_disclosed"] == ["parent_conversation", "memory", "unlisted_project_data"]


def test_batch_authorization_rejects_replay_cross_session_scope_and_expiry(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file")) as paths:
        create_project(paths, "project-alpha", project_template="light")
        session = create_new(paths, conversation_id="20261106-aaaa0003")
        session._ordinary_work_intent = "execute"
        host, _clients = _host(paths, monkeypatch, lambda _request: pytest.fail("must not POST"))
        request = _request("project-alpha", "a", "Update alpha", writes=("NOTES.md",))
        reservations = host.reserve_many(session, [request], parent_run_id="batch-run", turn_epoch=8,
                                         live_binding_check=_reservation_live(session, "batch-run", 8))
        grant = host.authorize_batch(session, [request], reservations, confirm_fn=lambda _v: "y",
                                     cancel_event=threading.Event(), parent_run_id="batch-run", turn_epoch=8,
                                     live_binding_check=_reservation_live(session, "batch-run", 8))
        assert grant is not None
        assert not host.batch_authorization_allows(grant, session, request, reservations[0],
                    parent_run_id="another-run", turn_epoch=8, live_binding_check=lambda *_: True)
        assert not host.batch_authorization_allows(grant, session, _request("project-alpha", "a", "Changed task", writes=("NOTES.md",)), reservations[0],
                    parent_run_id="batch-run", turn_epoch=8, live_binding_check=_reservation_live(session, "batch-run", 8))
        import project_worker_host as host_module
        original_snapshot = host_module._worker_model_snapshot
        monkeypatch.setattr(host_module, "_worker_model_snapshot",
                            lambda _paths: ("0x567-flash", "0x567", "gpt-6-luna", "https://api-cdn.0x567.com/changed"))
        assert not host.batch_authorization_allows(grant, session, request, reservations[0],
                    parent_run_id="batch-run", turn_epoch=8, live_binding_check=lambda *_: True)
        monkeypatch.setattr(host_module, "_worker_model_snapshot", original_snapshot)
        host.revoke_batch_authorization(grant)
        assert not host.batch_authorization_allows(grant, session, request, reservations[0],
                    parent_run_id="batch-run", turn_epoch=8, live_binding_check=_reservation_live(session, "batch-run", 8))




def test_denied_cancelled_expired_and_cross_project_grants_fail_closed(monkeypatch):
    import project_worker_host as host_module

    with temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file")) as paths:
        for pid in ("project-alpha", "project-beta"):
            create_project(paths, pid, project_template="light")
        session = create_new(paths, conversation_id="20261106-aaaa0004")
        session._ordinary_work_intent = "execute"
        host, _clients = _host(paths, monkeypatch, lambda _request: pytest.fail("must not POST"))
        request = _request("project-alpha", "a", "Update alpha", writes=("NOTES.md",))
        sibling = _request("project-beta", "b", "Update beta", writes=("RESULT.md",))
        reservations = host.reserve_many(session, [request, sibling], parent_run_id="batch-run", turn_epoch=9,
                                         live_binding_check=_reservation_live(session, "batch-run", 9))
        rejected = host.authorize_batch(session, [request, sibling], reservations, confirm_fn=lambda _v: "n",
                    cancel_event=threading.Event(), parent_run_id="batch-run", turn_epoch=9,
                    live_binding_check=_reservation_live(session, "batch-run", 9))
        assert rejected is None
        cancelled = threading.Event()
        assert host.authorize_batch(session, [request, sibling], reservations,
                    confirm_fn=lambda _v: (cancelled.set() or "y"), cancel_event=cancelled,
                    parent_run_id="batch-run", turn_epoch=9,
                    live_binding_check=_reservation_live(session, "batch-run", 9)) is None
        grant = host.authorize_batch(session, [request, sibling], reservations, confirm_fn=lambda _v: "y",
                    cancel_event=threading.Event(), parent_run_id="batch-run", turn_epoch=9,
                    live_binding_check=_reservation_live(session, "batch-run", 9), expires_in_seconds=1)
        assert grant is not None
        assert not host.batch_authorization_allows(grant, session, sibling, reservations[0],
                    parent_run_id="batch-run", turn_epoch=9,
                    live_binding_check=_reservation_live(session, "batch-run", 9))
        monkeypatch.setattr(host_module.time, "monotonic", lambda: grant.expires_at + 0.1)
        assert not host.batch_authorization_allows(grant, session, request, reservations[0],
                    parent_run_id="batch-run", turn_epoch=9,
                    live_binding_check=_reservation_live(session, "batch-run", 9))


def test_batch_grant_rejects_path_replacement_and_another_session(monkeypatch):
    import shutil

    with temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file")) as paths:
        create_project(paths, "project-alpha", project_template="light")
        session = create_new(paths, conversation_id="20261106-aaaa0005")
        session._ordinary_work_intent = "execute"
        host, _clients = _host(paths, monkeypatch, lambda _request: pytest.fail("must not POST"))
        request = _request("project-alpha", "a", "Update alpha", writes=("NOTES.md",))
        reservation = host.reserve_many(session, [request], parent_run_id="bound-run", turn_epoch=10,
                    live_binding_check=_reservation_live(session, "bound-run", 10))[0]
        grant = host.authorize_batch(session, [request], (reservation,), confirm_fn=lambda _v: "y",
                    cancel_event=threading.Event(), parent_run_id="bound-run", turn_epoch=10,
                    live_binding_check=_reservation_live(session, "bound-run", 10))
        assert grant is not None
        other_session = create_new(paths, conversation_id="20261106-aaaa0006")
        assert not host.batch_authorization_allows(grant, other_session, request, reservation,
                    parent_run_id="bound-run", turn_epoch=10,
                    live_binding_check=_reservation_live(session, "bound-run", 10))
        root = paths.workspace / "project-alpha"
        shutil.move(str(root), str(paths.workspace / "project-alpha-old"))
        create_project(paths, "project-alpha", project_template="light")
        assert not host.batch_authorization_allows(grant, session, request, reservation,
                    parent_run_id="bound-run", turn_epoch=10,
                    live_binding_check=_reservation_live(session, "bound-run", 10))


def test_run_many_uses_one_batch_grant_for_two_workers_and_queues_third(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file")) as paths:
        project_ids = ("project-alpha", "project-beta", "project-gamma")
        for pid in project_ids:
            create_project(paths, pid, project_template="light")
            (paths.workspace / pid / "README.md").write_text(f"{pid} source\n", encoding="utf-8")
        session = create_new(paths, conversation_id="20261106-aaaa0007")
        session._ordinary_work_intent = "execute"
        session.meta.active_shell = "project"
        session.meta.project_id = "project-alpha"
        session.meta.project_root = "workspace/project-alpha"
        request_rows = [_request(pid, f"task-{idx}", f"Task {pid}", writes=("NOTES.md",))
                        for idx, pid in enumerate(project_ids)]
        lock = threading.Lock()
        active = 0
        max_active = 0
        posted = []

        def handler(http_request):
            nonlocal active, max_active
            body = json.loads(http_request.content.decode("utf-8"))
            with lock:
                active += 1
                max_active = max(max_active, active)
                posted.append(body)
            time.sleep(0.05)
            with lock:
                active -= 1
            return _response(http_request, {"role": "assistant", "content": "done"}, "stop")

        host, _clients = _host(paths, monkeypatch, handler)
        live = _reservation_live(session, "batch-three", 11)
        reservations = host.reserve_many(session, request_rows, parent_run_id="batch-three", turn_epoch=11,
                                         live_binding_check=live)
        confirms = []
        grant = host.authorize_batch(session, request_rows, reservations,
                    confirm_fn=lambda preview: confirms.append(preview) or "y",
                    cancel_event=threading.Event(), parent_run_id="batch-three", turn_epoch=11,
                    live_binding_check=live)
        assert grant is not None and len(confirms) == 1
        outcomes = host.run_many(session, request_rows,
                    scope_confirm_fn=lambda _preview: pytest.fail("scope was already batch-authorized"),
                    outbound_request_confirm_fn=lambda _preview: pytest.fail("outbound category was already disclosed"),
                    write_confirm_fn=lambda *_args: pytest.fail("write target was already disclosed"),
                    cancel_event=threading.Event(), parent_run_id="batch-three", turn_epoch=11,
                    live_binding_check=live, reservations=reservations, batch_authorization=grant)
        assert len(outcomes) == 3
        assert all(row.status == "model_turn_finished" for row in outcomes), [(row.status, row.error_code, row.model_turn.error_code if row.model_turn else None, row.model_turn.error_type if row.model_turn else None) for row in outcomes] + [(len(posted), str(paths.workspace))]
        assert len(posted) == 3
        assert max_active == 2
        host.revoke_batch_authorization(grant)
        assert grant.grant_id not in host._batch_authorizations






def test_outbound_grant_accepts_only_exact_worker_transcript_and_listed_read_sources(monkeypatch):
    from project_worker_model_loop import TOOL_DEFINITIONS, build_worker_system_prompt

    with temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file")) as paths:
        create_project(paths, "project-alpha", project_template="light")
        session = create_new(paths, conversation_id="20261106-aaaa0008")
        session._ordinary_work_intent = "execute"
        host, _clients = _host(paths, monkeypatch, lambda _request: pytest.fail("must not POST"))
        request = _request("project-alpha", "task-a", "Update the README", reads=("README.md",), writes=("NOTES.md",))
        reservation = host.reserve_many(session, [request], parent_run_id="source-run", turn_epoch=12,
                    live_binding_check=_reservation_live(session, "source-run", 12))[0]
        grant = host.authorize_batch(session, [request], (reservation,), confirm_fn=lambda _v: "y",
                    cancel_event=threading.Event(), parent_run_id="source-run", turn_epoch=12,
                    live_binding_check=_reservation_live(session, "source-run", 12))
        assert grant is not None
        system = build_worker_system_prompt("project-alpha", ("README.md",), ("NOTES.md",))
        scope = {"project_id": "project-alpha", "project_root": "workspace/project-alpha",
                 "read_targets": ["README.md"], "write_targets": ["NOTES.md"],
                 "run_id": "source-run", "turn_epoch": 12, "request_number": 2}
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "Update the README"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "read-call", "type": "function",
             "function": {"name": "read_project_file", "arguments": json.dumps({"path": "README.md"})}}]},
            {"role": "tool", "tool_call_id": "read-call",
             "content": json.dumps({"ok": True, "path": "README.md", "content": "listed project text", "truncated": False})},
        ]
        def preview_for(transcript):
            return json.dumps({
                "method": "POST", "model_id": grant.model_id, "vendor": grant.provider,
                "endpoint": grant.endpoint,
                "headers": {"Authorization": "[redacted]", "Content-Type": "application/json"},
                "session_id": grant.conversation_id, "scope": scope,
                "provider_payload": {"model": grant.provider_model, "messages": transcript,
                    "temperature": 0.0, "stream": False, "tools": TOOL_DEFINITIONS},
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert host._batch_outbound_preview_matches(preview_for(messages), grant, session, request, reservation)
        completed_write = [dict(row) for row in messages] + [
            {"role": "assistant", "content": None, "tool_calls": [{"id": "write-call", "type": "function",
             "function": {"name": "write_project_file", "arguments": json.dumps({"path": "NOTES.md", "content": "generated"})}}]},
            {"role": "tool", "tool_call_id": "write-call",
             "content": json.dumps({"ok": True, "path": "NOTES.md", "written": True, "readback_verified": True})},
        ]
        assert host._batch_outbound_preview_matches(preview_for(completed_write), grant, session, request, reservation)
        contaminated_task = [dict(row) for row in messages]
        contaminated_task[1] = {"role": "user", "content": "Update the README\nPARENT_PRIVATE_SENTINEL"}
        assert not host._batch_outbound_preview_matches(preview_for(contaminated_task), grant, session, request, reservation)
        unlisted_source = [dict(row) for row in messages]
        unlisted_source[2] = {"role": "assistant", "content": None, "tool_calls": [{"id": "read-call", "type": "function",
            "function": {"name": "read_project_file", "arguments": json.dumps({"path": "PRIVATE.md"})}}]}
        unlisted_source[3] = {"role": "tool", "tool_call_id": "read-call",
            "content": json.dumps({"ok": True, "path": "PRIVATE.md", "content": "not authorized", "truncated": False})}
        assert not host._batch_outbound_preview_matches(preview_for(unlisted_source), grant, session, request, reservation)
