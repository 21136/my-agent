"""Trusted host wiring tests with real Sessions, ToolExecutors and MockTransport."""
from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

import llm_client
from llm_client import LLMClient, LLMConfig
from project_mode import create_project
from project_worker_execution import PROJECT_WORKER_COORDINATOR
from project_worker_host import ProjectWorkerHost, ProjectWorkerHostRequest
from session import create_new
from tests.isolation_helpers import temporary_agent_paths

_REAL_HTTPX_CLIENT = httpx.Client


def _tool_call(name: str, args: dict[str, Any], index: int) -> dict[str, Any]:
    return {"id": f"call-{index}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def _response(request: httpx.Request, message: dict[str, Any], finish: str) -> httpx.Response:
    return httpx.Response(200, request=request, json={
        "model": "gpt-6-luna",
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
    })


def _host(paths, monkeypatch, handler):
    monkeypatch.setenv("OX567_API_KEY", "host-test-secret-never-display")
    monkeypatch.setenv("OX567_BASE_URL", "https://api-cdn.0x567.com")
    monkeypatch.setenv("OX567_MODEL_FLASH", "gpt-6-luna")
    monkeypatch.setattr(
        llm_client.httpx, "Client",
        lambda **kwargs: _REAL_HTTPX_CLIENT(transport=httpx.MockTransport(handler), **kwargs),
    )
    config = LLMConfig(api_key=None, base_url="https://unused.example", model="0x567-flash",
                       model_coding="0x567-pro", timeout_sec=3, context_limit_override=None)
    created: list[LLMClient] = []

    def client_factory():
        client = LLMClient(config, paths=paths)
        created.append(client)
        return client

    return ProjectWorkerHost(client_factory), created


def _callbacks(scope_log: list[str] | None = None, outbound_log: list[str] | None = None,
               *, scope_choice="y", outbound_choice="y", write_choice="y"):
    scope_log = scope_log if scope_log is not None else []
    outbound_log = outbound_log if outbound_log is not None else []
    return (
        lambda preview: scope_log.append(preview) or scope_choice,
        lambda preview: outbound_log.append(preview) or outbound_choice,
        lambda _preview, _allow: write_choice,
    )


def _request(project_id: str, task_id: str, task: str,
             *, reads=("README.md",), writes=("result.txt",)):
    return ProjectWorkerHostRequest(project_id, task_id, task, reads, writes)




def _reservation_live(session, parent_run_id: str, turn_epoch: int):
    return lambda bound, run_id, epoch: (
        bound is session and run_id == parent_run_id and epoch == turn_epoch
    )

def _run(host, session, request, callbacks, *, cancel=None, max_requests=5,
         live_binding_check=None, run_id=None, turn_epoch=1):
    scope_confirm, outbound_confirm, write_confirm = callbacks
    run_id = run_id or f"host-run-{request.task_id}"
    live_binding_check = live_binding_check or (
        lambda bound, active_run, epoch: bound is session and active_run == run_id and epoch == turn_epoch
    )
    return host.run(
        session, request,
        scope_confirm_fn=scope_confirm,
        outbound_request_confirm_fn=outbound_confirm,
        write_confirm_fn=write_confirm,
        cancel_event=cancel or threading.Event(),
        run_id=run_id,
        turn_epoch=turn_epoch,
        live_binding_check=live_binding_check,
        max_requests=max_requests,
    )


def test_same_session_two_projects_run_parallel_without_parent_context_leak(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file")) as paths:
        for pid in ("project-alpha", "project-beta"):
            create_project(paths, pid, project_template="light")
            (paths.workspace / pid / "README.md").write_text(f"{pid} source\n", encoding="utf-8")
        session = create_new(paths, conversation_id="20261101-aaaa0001")
        session._ordinary_work_intent = "execute"
        session.meta.active_shell = "project"
        session.meta.project_id = "project-alpha"
        session.meta.project_root = "workspace/project-alpha"
        session.messages.append({"role": "user", "content": "PARENT_PRIVATE_SENTINEL"})
        session.save()

        first_posts = threading.Barrier(2, timeout=4)
        lock = threading.Lock()
        counts: dict[str, int] = {}
        wire: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8"))
            wire.append(body)
            system = body["messages"][0]["content"]
            pid = next(value for value in ("project-alpha", "project-beta")
                       if f"project_id is '{value}'" in system)
            task = body["messages"][1]["content"]
            marker = "ALPHA_TASK" if "ALPHA_TASK" in task else "BETA_TASK"
            with lock:
                round_no = counts.get(marker, 0)
                counts[marker] = round_no + 1
            if round_no == 0:
                first_posts.wait()
                return _response(request, {"role": "assistant", "content": None,
                    "tool_calls": [_tool_call("read_project_file", {"path": "README.md"}, round_no)]}, "tool_calls")
            if round_no == 1:
                return _response(request, {"role": "assistant", "content": None,
                    "tool_calls": [_tool_call("write_project_file", {
                        "path": "result.txt", "content": f"{pid}:{marker}\n"}, round_no)]}, "tool_calls")
            return _response(request, {"role": "assistant", "content": f"finished {marker}",
                                       "tool_calls": []}, "stop")

        host, clients = _host(paths, monkeypatch, handler)
        scope_log: list[str] = []
        outbound_log: list[str] = []
        callbacks = _callbacks(scope_log, outbound_log)
        results: dict[str, Any] = {}
        failures: list[BaseException] = []

        def run(pid: str, marker: str):
            try:
                results[pid] = _run(host, session,
                    _request(pid, f"work-{marker.lower()}", f"Create output for {marker}."), callbacks)
            except BaseException as exc:
                failures.append(exc)

        threads = [threading.Thread(target=run, args=("project-alpha", "ALPHA_TASK")),
                   threading.Thread(target=run, args=("project-beta", "BETA_TASK"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert not any(thread.is_alive() for thread in threads), "project workers deadlocked"
        assert failures == []
        assert set(results) == {"project-alpha", "project-beta"}
        assert all(row.status == "model_turn_finished" for row in results.values())
        assert all(row.acceptance_status == "not_run" for row in results.values())
        assert len({row.worker_id for row in results.values()}) == 2
        assert len({row.worker_run_id for row in results.values()}) == 2
        assert all(row.worker_run_id != row.parent_run_id for row in results.values())
        assert all(row.run_id == row.parent_run_id for row in results.values())
        assert len({row.reservation_id for row in results.values()}) == 2
        assert (paths.workspace / "project-alpha" / "result.txt").read_text(encoding="utf-8").strip() == "project-alpha:ALPHA_TASK"
        assert (paths.workspace / "project-beta" / "result.txt").read_text(encoding="utf-8").strip() == "project-beta:BETA_TASK"
        assert len(clients) == 2 and clients[0] is not clients[1]
        assert len(scope_log) == 2 and len(outbound_log) == 6
        assert all("PARENT_PRIVATE_SENTINEL" not in json.dumps(body) for body in wire)
        assert session.meta.project_id == "project-alpha"
        scope_docs = [json.loads(preview) for preview in scope_log]
        assert {doc["project_id"] for doc in scope_docs} == {"project-alpha", "project-beta"}


def test_same_project_host_runs_queue_in_same_conversation(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "project-alpha", project_template="light")
        session = create_new(paths, conversation_id="20261101-bbbb0002")
        session._ordinary_work_intent = "execute"
        first_post = threading.Event()
        release_first = threading.Event()
        count_lock = threading.Lock()
        post_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal post_count
            with count_lock:
                post_count += 1
                index = post_count
            if index == 1:
                first_post.set()
                assert release_first.wait(4)
            return _response(request, {"role": "assistant", "content": "turn ended", "tool_calls": []}, "stop")

        host, _clients = _host(paths, monkeypatch, handler)
        scope_calls: list[str] = []
        outbound_calls: list[str] = []
        callbacks = _callbacks(scope_calls, outbound_calls)
        results: list[Any] = []
        args = dict(scope_confirm_fn=callbacks[0], outbound_request_confirm_fn=callbacks[1],
                    write_confirm_fn=callbacks[2], max_requests=1)
        first = threading.Thread(target=lambda: results.append(host.run(
            session, _request("project-alpha", "w1", "first" , reads=(), writes=("one.txt",)),
            cancel_event=threading.Event(), run_id="run-w1", turn_epoch=1,
            live_binding_check=lambda bound, rid, epoch: bound is session and rid == "run-w1" and epoch == 1,
            **args)) )
        second = threading.Thread(target=lambda: results.append(host.run(
            session, _request("project-alpha", "w2", "second", reads=(), writes=("two.txt",)),
            cancel_event=threading.Event(), run_id="run-w2", turn_epoch=1,
            live_binding_check=lambda bound, rid, epoch: bound is session and rid == "run-w2" and epoch == 1,
            **args)) )
        first.start()
        assert first_post.wait(2)
        second.start()
        with PROJECT_WORKER_COORDINATOR._condition:
            assert PROJECT_WORKER_COORDINATOR._condition.wait_for(
                lambda: any(item.conversation_id == session.conversation_id
                            and item.project_id == "project-alpha"
                            for item in PROJECT_WORKER_COORDINATOR._waiting), timeout=3,
            )
        assert post_count == 1
        release_first.set()
        first.join(6); second.join(6)
        assert not first.is_alive() and not second.is_alive()
        assert post_count == 2
        assert len(results) == 2
        assert all(row.status == "model_turn_finished" for row in results)


def test_denial_cancel_and_unregistered_directory_fail_closed(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "project-alpha", project_template="light")
        session = create_new(paths, conversation_id="20261101-cccc0003")
        session._ordinary_work_intent = "execute"
        called: list[str] = []

        def factory():
            called.append("client")
            return LLMClient(LLMConfig(None, "https://api-cdn.0x567.com", "0x567-flash",
                "0x567-pro", 2, None), paths=paths)

        host = ProjectWorkerHost(factory)
        denied = _run(host, session, _request("project-alpha", "w-deny", "task"),
                      _callbacks(scope_choice="n"))
        assert denied.status == "blocked" and denied.error_code == "scope_denied"
        assert called == []

        session._ordinary_work_intent = "read_only"
        read_only = _run(host, session, _request("project-alpha", "w-read-only", "task"),
                         _callbacks())
        assert read_only.status == "blocked"
        assert read_only.error_code == "current_turn_not_write_authorized"
        assert called == []
        session._ordinary_work_intent = "execute"

        canceled = threading.Event(); canceled.set()
        canceled_result = _run(host, session, _request("project-alpha", "w-cancel", "task"),
                               _callbacks(), cancel=canceled)
        assert canceled_result.status == "cancelled"
        assert called == []

        missing = _run(host, session, _request("unknown-project", "w-missing", "task"),
                        _callbacks())
        assert missing.status == "blocked"
        assert missing.error_code == "project_not_found"
        assert called == []

        traversal = _run(host, session,
            _request("project-alpha", "w-traversal", "task", writes=("../project-alpha/PROJECT.md",)),
            _callbacks())
        assert traversal.status == "blocked"
        assert traversal.error_code == "invalid_target_scope"
        assert called == []



def test_executor_write_confirmation_is_separate_from_scope_and_outbound_approval(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "project-alpha", project_template="light")
        session = create_new(paths, conversation_id="20261101-dddd0004")
        session._ordinary_work_intent = "execute"
        charter = paths.workspace / "project-alpha" / "PROJECT.md"
        original = charter.read_text(encoding="utf-8")
        requests: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8"))
            requests.append(body)
            if len(requests) == 1:
                return _response(request, {"role": "assistant", "content": None,
                    "tool_calls": [_tool_call("read_project_file", {"path": "PROJECT.md"}, 1)]}, "tool_calls")
            return _response(request, {"role": "assistant", "content": None,
                "tool_calls": [_tool_call("write_project_file", {
                    "path": "PROJECT.md", "content": "must be refused"}, 2)]}, "tool_calls")

        host, _clients = _host(paths, monkeypatch, handler)
        write_previews: list[str] = []
        result = _run(
            host, session,
            _request("project-alpha", "task-charter", "Change the project charter.",
                     reads=("PROJECT.md",), writes=("PROJECT.md",)),
            (lambda _preview: "y", lambda _preview: "y",
             lambda preview, _all: write_previews.append(preview) or "n"),
        )
        assert result.status == "blocked"
        assert result.error_code == "confirm_rejected"
        assert result.model_turn is not None and result.model_turn.writes[0].status == "not_written"
        assert len(write_previews) == 1
        assert len(requests) == 2
        assert charter.read_text(encoding="utf-8") == original
        assert result.acceptance_status == "not_run"


def test_two_different_sessions_do_not_share_parent_messages_or_result_identity(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "project-alpha", project_template="light")
        create_project(paths, "project-beta", project_template="light")
        session_a = create_new(paths, conversation_id="20261101-eeee0005")
        session_b = create_new(paths, conversation_id="20261101-ffff0006")
        session_a._ordinary_work_intent = "execute"
        session_b._ordinary_work_intent = "execute"
        session_a.messages.append({"role": "user", "content": "SESSION_A_PRIVATE"})
        session_b.messages.append({"role": "user", "content": "SESSION_B_PRIVATE"})
        wire: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8"))
            wire.append(body)
            marker = body["messages"][1]["content"]
            return _response(request, {"role": "assistant", "content": marker, "tool_calls": []}, "stop")

        host, clients = _host(paths, monkeypatch, handler)
        outcomes = [
            _run(host, session_a, _request("project-alpha", "task-a", "SESSION_A_TASK", reads=(), writes=("a.txt",)), _callbacks()),
            _run(host, session_b, _request("project-beta", "task-b", "SESSION_B_TASK", reads=(), writes=("b.txt",)), _callbacks()),
        ]
        assert [item.conversation_id for item in outcomes] == [session_a.conversation_id, session_b.conversation_id]
        assert [item.project_id for item in outcomes] == ["project-alpha", "project-beta"]
        assert all(item.status == "model_turn_finished" for item in outcomes)
        assert len(clients) == 2 and clients[0] is not clients[1]
        encoded = json.dumps(wire, ensure_ascii=False)
        assert "SESSION_A_PRIVATE" not in encoded and "SESSION_B_PRIVATE" not in encoded
        assert "SESSION_A_TASK" in encoded and "SESSION_B_TASK" in encoded
        assert outcomes[0].acceptance_status == outcomes[1].acceptance_status == "not_run"


def test_host_rejects_reused_llm_client_instance(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "project-alpha", project_template="light")
        session = create_new(paths, conversation_id="20261101-gggg0007")
        session._ordinary_work_intent = "execute"
        requests: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content.decode("utf-8")))
            return _response(request, {"role": "assistant", "content": "one turn", "tool_calls": []}, "stop")

        host, _clients = _host(paths, monkeypatch, handler)
        # A factory retaining and returning one client across worker invocations is not isolation.
        shared = LLMClient(LLMConfig(None, "https://unused.example", "0x567-flash",
            "0x567-pro", 3, None), paths=paths)
        reused_host = ProjectWorkerHost(lambda: shared)
        args = dict(scope_confirm_fn=lambda _preview: "y",
                    outbound_request_confirm_fn=lambda _preview: "y",
                    write_confirm_fn=lambda _preview, _all: "y",
                    cancel_event=threading.Event(), max_requests=1)
        first = reused_host.run(session, _request("project-alpha", "reuse-1", "first", reads=(), writes=("one.txt",)),
                                run_id="reuse-run-1", turn_epoch=1,
                                live_binding_check=lambda bound, rid, epoch: bound is session and rid == "reuse-run-1" and epoch == 1,
                                **args)
        second = reused_host.run(session, _request("project-alpha", "reuse-2", "second", reads=(), writes=("two.txt",)),
                                 run_id="reuse-run-2", turn_epoch=1,
                                 live_binding_check=lambda bound, rid, epoch: bound is session and rid == "reuse-run-2" and epoch == 1,
                                 **args)
        assert first.status == "model_turn_finished"
        assert second.status == "blocked" and second.error_code == "shared_llm_client"
        assert len(requests) == 1


def test_live_binding_switch_after_scope_confirmation_blocks_before_post(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "project-alpha", project_template="light")
        original = create_new(paths, conversation_id="20261101-hhhh0008")
        replacement = create_new(paths, conversation_id="20261101-iiii0009")
        original._ordinary_work_intent = "execute"
        replacement._ordinary_work_intent = "execute"
        current = {"session": original, "run_id": "run-scope", "epoch": 7, "authorized": True}
        posts: list[httpx.Request] = []
        host, clients = _host(paths, monkeypatch, lambda request: (posts.append(request), pytest.fail("must not POST"))[1])

        def live_check(bound_session, run_id, epoch):
            return (current["session"] is bound_session and current["run_id"] == run_id
                    and current["epoch"] == epoch and current["authorized"] is True)

        def scope_confirm(_preview):
            current["session"] = replacement
            return "y"

        outcome = host.run(
            original, _request("project-alpha", "run-scope-task", "explicit task"),
            scope_confirm_fn=scope_confirm,
            outbound_request_confirm_fn=lambda _preview: "y",
            write_confirm_fn=lambda _preview, _all: "y",
            cancel_event=threading.Event(), max_requests=2,
            run_id="run-scope", turn_epoch=7, live_binding_check=live_check,
        )
        assert outcome.status == "blocked"
        assert outcome.error_code == "live_binding_changed"
        assert posts == []


def test_session_switch_after_model_response_prevents_any_worker_write(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "project-alpha", project_template="light")
        original = create_new(paths, conversation_id="20261101-jjjj0010")
        replacement = create_new(paths, conversation_id="20261101-kkkk0011")
        original._ordinary_work_intent = "execute"
        replacement._ordinary_work_intent = "execute"
        current = {"session": original, "run_id": "run-model", "epoch": 3, "authorized": True}
        calls = 0

        def handler(request: httpx.Request):
            nonlocal calls
            calls += 1
            current["session"] = replacement
            return _response(request, {"role": "assistant", "content": None,
                "tool_calls": [_tool_call("write_project_file", {
                    "path": "result.txt", "content": "stale run must not write"}, 1)]}, "tool_calls")

        host, _clients = _host(paths, monkeypatch, handler)

        def live_check(bound_session, run_id, epoch):
            return (current["session"] is bound_session and current["run_id"] == run_id
                    and current["epoch"] == epoch and current["authorized"] is True)

        result = host.run(
            original, _request("project-alpha", "run-model-task", "write result"),
            scope_confirm_fn=lambda _preview: "y",
            outbound_request_confirm_fn=lambda _preview: "y",
            write_confirm_fn=lambda _preview, _all: "y",
            cancel_event=threading.Event(), max_requests=2,
            run_id="run-model", turn_epoch=3, live_binding_check=live_check,
        )
        assert result.status == "blocked"
        assert result.error_code == "live_binding_changed"
        assert calls == 1
        assert not (paths.workspace / "project-alpha" / "result.txt").exists()



def test_live_binding_switch_after_outbound_approval_posts_zero(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "project-alpha", project_template="light")
        original = create_new(paths, conversation_id="20261101-llll0012")
        replacement = create_new(paths, conversation_id="20261101-mmmm0013")
        original._ordinary_work_intent = "execute"
        replacement._ordinary_work_intent = "execute"
        current = {"session": original, "run_id": "run-outbound", "epoch": 8, "authorized": True}
        posts: list[httpx.Request] = []
        host, clients = _host(paths, monkeypatch,
                              lambda request: (posts.append(request), pytest.fail("stale run must not POST"))[1])

        def live_check(bound_session, run_id, epoch):
            return (current["session"] is bound_session and current["run_id"] == run_id
                    and current["epoch"] == epoch and current["authorized"] is True)

        def approve_then_switch(_preview):
            current["session"] = replacement
            return "y"

        outcome = host.run(
            original, _request("project-alpha", "run-outbound-task", "explicit task", reads=(), writes=("result.txt",)),
            scope_confirm_fn=lambda _preview: "y",
            outbound_request_confirm_fn=approve_then_switch,
            write_confirm_fn=lambda _preview, _all: "y",
            cancel_event=threading.Event(), max_requests=2,
            run_id="run-outbound", turn_epoch=8, live_binding_check=live_check,
        )
        assert outcome.status == "blocked"
        assert outcome.error_code == "live_binding_changed"
        assert posts == []



def test_host_requires_live_run_binding_before_scope_confirmation_or_client(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "project-alpha", project_template="light")
        session = create_new(paths, conversation_id="20261101-nnnn0014")
        session._ordinary_work_intent = "execute"
        calls: list[str] = []
        host, _clients = _host(paths, monkeypatch, lambda request: pytest.fail("must not POST"))
        outcome = host.run(
            session, _request("project-alpha", "missing-binding", "explicit task"),
            scope_confirm_fn=lambda _preview: calls.append("scope") or "y",
            outbound_request_confirm_fn=lambda _preview: "y",
            write_confirm_fn=lambda _preview, _all: "y",
            cancel_event=threading.Event(),
            # No run_id, epoch, or live binding verifier.
        )
        assert outcome.status == "blocked"
        assert outcome.error_code == "live_binding_required"
        assert calls == []


def test_session_switch_during_write_confirmation_prevents_write(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "project-alpha", project_template="light")
        original = create_new(paths, conversation_id="20261101-oooo0015")
        replacement = create_new(paths, conversation_id="20261101-pppp0016")
        original._ordinary_work_intent = "execute"
        replacement._ordinary_work_intent = "execute"
        original.meta.active_shell = "project"
        original.meta.project_id = "project-alpha"
        original.meta.project_root = "workspace/project-alpha"
        target = paths.workspace / "project-alpha" / "PROJECT.md"
        before = target.read_text(encoding="utf-8")
        current = {"session": original, "run_id": "run-write", "epoch": 4, "authorized": True}
        post_count = 0

        def handler(request: httpx.Request):
            nonlocal post_count
            post_count += 1
            if post_count == 1:
                return _response(request, {"role": "assistant", "content": None,
                    "tool_calls": [_tool_call("read_project_file", {"path": "PROJECT.md"}, 1)]}, "tool_calls")
            return _response(request, {"role": "assistant", "content": None,
                "tool_calls": [_tool_call("write_project_file", {
                    "path": "PROJECT.md", "content": "stale session must not write"}, 2)]}, "tool_calls")

        host, _clients = _host(paths, monkeypatch, handler)

        def live_check(bound_session, run_id, epoch):
            return (current["session"] is bound_session and current["run_id"] == run_id
                    and current["epoch"] == epoch and current["authorized"] is True)

        def write_confirm(_preview, _allow_all):
            current["session"] = replacement
            return "y"

        outcome = host.run(
            original,
            _request("project-alpha", "write-on-charter", "Update charter.",
                     reads=("PROJECT.md",), writes=("PROJECT.md",)),
            scope_confirm_fn=lambda _preview: "y",
            outbound_request_confirm_fn=lambda _preview: "y",
            write_confirm_fn=write_confirm,
            cancel_event=threading.Event(), max_requests=3,
            run_id="run-write", turn_epoch=4, live_binding_check=live_check,
        )
        assert outcome.status == "blocked"
        assert outcome.error_code == "live_binding_changed"
        assert outcome.model_turn is not None
        assert outcome.model_turn.writes[0].status == "not_written"
        assert post_count == 2
        assert target.read_text(encoding="utf-8") == before


def test_session_switch_immediately_after_executor_write_returns_unknown_receipt(monkeypatch):
    from tools.executor import ToolExecutor

    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "project-alpha", project_template="light")
        (paths.workspace / "project-alpha" / "README.md").write_text("source\n", encoding="utf-8")
        original = create_new(paths, conversation_id="20261101-qqqq0017")
        replacement = create_new(paths, conversation_id="20261101-rrrr0018")
        original._ordinary_work_intent = "execute"
        replacement._ordinary_work_intent = "execute"
        current = {"session": original, "run_id": "run-after-write", "epoch": 5, "authorized": True}
        responses = [
            ({"role": "assistant", "content": None,
              "tool_calls": [_tool_call("read_project_file", {"path": "README.md"}, 1)]}, "tool_calls"),
            ({"role": "assistant", "content": None,
              "tool_calls": [_tool_call("write_project_file", {
                  "path": "result.txt", "content": "written before session switch"}, 2)]}, "tool_calls"),
        ]
        count = 0

        def handler(request: httpx.Request):
            nonlocal count
            body = json.loads(request.content.decode("utf-8"))
            payload = responses[count]
            count += 1
            return _response(request, *payload)

        host, _clients = _host(paths, monkeypatch, handler)
        real_run = ToolExecutor.run

        def run_then_switch(executor, tool_name, arguments=None):
            result = real_run(executor, tool_name, arguments)
            if tool_name == "run_evolved" and (arguments or {}).get("tool_name") == "write_text":
                current["session"] = replacement
            return result

        monkeypatch.setattr(ToolExecutor, "run", run_then_switch)

        def live_check(bound_session, run_id, epoch):
            return (current["session"] is bound_session and current["run_id"] == run_id
                    and current["epoch"] == epoch and current["authorized"] is True)

        result = host.run(
            original, _request("project-alpha", "write-before-switch", "Write once.",
                               reads=("README.md",), writes=("result.txt",)),
            scope_confirm_fn=lambda _preview: "y",
            outbound_request_confirm_fn=lambda _preview: "y",
            write_confirm_fn=lambda _preview, _all: "y",
            cancel_event=threading.Event(), max_requests=4,
            run_id="run-after-write", turn_epoch=5, live_binding_check=live_check,
        )
        print(result)
        assert result.status == "blocked" and result.error_code == "live_binding_changed"
        assert result.model_turn is not None
        assert result.model_turn.error_code == "write_outcome_unknown"
        assert result.model_turn.writes[0].status == "outcome_unknown"
        assert result.model_turn.writes[0].readback_succeeded is False
        assert count == 2
        assert (paths.workspace / "project-alpha" / "result.txt").read_text(encoding="utf-8") == "written before session switch"



def test_turn_epoch_change_after_scope_confirmation_invalidates_worker(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "project-alpha", project_template="light")
        session = create_new(paths, conversation_id="20261101-ssss0019")
        session._ordinary_work_intent = "execute"
        current = {"run_id": "epoch-run", "epoch": 10, "authorized": True}
        host, clients = _host(paths, monkeypatch, lambda request: pytest.fail("must not POST"))

        def live_check(bound_session, run_id, epoch):
            return (bound_session is session and current["run_id"] == run_id
                    and current["epoch"] == epoch and current["authorized"] is True)

        def confirm_scope(_preview):
            current["epoch"] += 1
            return "y"

        result = host.run(
            session, _request("project-alpha", "epoch-task", "explicit task"),
            scope_confirm_fn=confirm_scope,
            outbound_request_confirm_fn=lambda _preview: "y",
            write_confirm_fn=lambda _preview, _all: "y",
            cancel_event=threading.Event(), max_requests=2,
            run_id="epoch-run", turn_epoch=10, live_binding_check=live_check,
        )
        assert result.status == "blocked"
        assert result.error_code == "live_binding_changed"
        assert clients == []


def test_parent_write_scope_for_project_a_does_not_silently_authorize_project_b(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "project-alpha", project_template="light")
        create_project(paths, "project-beta", project_template="light")
        session = create_new(paths, conversation_id="20261101-ssss0020")
        session._ordinary_work_intent = "execute"
        session.meta.active_shell = "project"
        session.meta.project_id = "project-alpha"
        session.meta.project_root = "workspace/project-alpha"
        session.meta.write_scope_root = "workspace/project-alpha"
        client_creations: list[bool] = []

        def client_factory():
            client_creations.append(True)
            raise AssertionError("a parent write fence must fail before model creation")

        host = ProjectWorkerHost(client_factory)
        scope_requests: list[str] = []
        result = host.run(
            session, _request("project-beta", "task-beta", "Modify beta only."),
            scope_confirm_fn=lambda preview: scope_requests.append(preview) or "y",
            outbound_request_confirm_fn=lambda _preview: "y",
            write_confirm_fn=lambda _preview, _all: "y",
            cancel_event=threading.Event(), run_id="scope-test", turn_epoch=1,
            live_binding_check=lambda bound, run_id, epoch: bound is session and run_id == "scope-test" and epoch == 1,
        )
        assert result.status == "blocked"
        assert result.error_code == "parent_write_scope_excludes_project"
        assert scope_requests == []
        assert client_creations == []
        assert not (paths.workspace / "project-beta" / "result.txt").exists()


def test_worker_reservations_are_parent_epoch_bound_single_use_and_echoed_on_denial(monkeypatch):
    with temporary_agent_paths() as paths:
        create_project(paths, "project-alpha", project_template="light")
        session = create_new(paths, conversation_id="20261103-reserve0001")
        session._ordinary_work_intent = "execute"
        host, _clients = _host(paths, monkeypatch, lambda request: pytest.fail("denied scope must not POST"))
        request = _request("project-alpha", "task-reserved", "Explicit task")
        reservations = host.reserve_many(
            session, [request], parent_run_id="parent-run-1", turn_epoch=9,
            live_binding_check=_reservation_live(session, "parent-run-1", 9),
        )
        reserved = reservations[0]
        assert reserved.parent_run_id == "parent-run-1"
        assert reserved.turn_epoch == 9
        assert reserved.conversation_id == session.conversation_id
        assert reserved.project_id == request.project_id
        assert reserved.task_id == request.task_id
        assert reserved.worker_id and reserved.worker_run_id
        assert reserved.worker_run_id != reserved.parent_run_id
        with pytest.raises(ValueError, match="already reserved"):
            host.reserve_many(
                session, [request], parent_run_id="parent-run-1", turn_epoch=9,
                live_binding_check=_reservation_live(session, "parent-run-1", 9),
            )
        later_run = host.reserve_many(
            session, [request], parent_run_id="parent-run-1-next", turn_epoch=10,
            live_binding_check=_reservation_live(session, "parent-run-1-next", 10),
        )[0]
        assert later_run.task_id == reserved.task_id
        assert later_run.parent_run_id != reserved.parent_run_id
        assert later_run.worker_run_id != reserved.worker_run_id

        callbacks = _callbacks(scope_choice="n")
        wrong_epoch = host.run_many(
            session, [request], scope_confirm_fn=callbacks[0],
            outbound_request_confirm_fn=callbacks[1], write_confirm_fn=callbacks[2],
            cancel_event=threading.Event(), parent_run_id="parent-run-1", turn_epoch=10,
            live_binding_check=lambda *_args: True, reservations=(reserved,),
        )[0]
        assert wrong_epoch.status == "blocked"
        assert wrong_epoch.error_code == "worker_reservation_mismatch"
        outcome = host.run_many(
            session, [request], scope_confirm_fn=callbacks[0],
            outbound_request_confirm_fn=callbacks[1], write_confirm_fn=callbacks[2],
            cancel_event=threading.Event(), parent_run_id="parent-run-1", turn_epoch=9,
            live_binding_check=lambda bound, parent, epoch: (
                bound is session and parent == "parent-run-1" and epoch == 9
            ), reservations=reservations,
        )[0]
        assert outcome.status == "blocked"
        assert outcome.error_code == "scope_denied"
        assert outcome.parent_run_id == outcome.run_id == reserved.parent_run_id
        assert outcome.turn_epoch == reserved.turn_epoch
        assert outcome.worker_id == reserved.worker_id
        assert outcome.worker_run_id == reserved.worker_run_id
        assert outcome.reservation_id == reserved.reservation_id

        replay = host.run_many(
            session, [request], scope_confirm_fn=callbacks[0],
            outbound_request_confirm_fn=callbacks[1], write_confirm_fn=callbacks[2],
            cancel_event=threading.Event(), parent_run_id="parent-run-1", turn_epoch=9,
            live_binding_check=lambda *_args: True, reservations=reservations,
            before_worker_start=lambda *_args: pytest.fail("pre-cancelled queued workers must not start"),
        )[0]
        assert replay.status == "blocked"
        assert replay.error_code == "worker_reservation_replayed"
        assert (replay.worker_id, replay.worker_run_id, replay.reservation_id) == (
            reserved.worker_id, reserved.worker_run_id, reserved.reservation_id,
        )


def test_worker_reservations_are_distinct_per_task_and_parent_identity_is_preserved(monkeypatch):
    with temporary_agent_paths() as paths:
        for project_id in ("project-alpha", "project-beta"):
            create_project(paths, project_id, project_template="light")
        session = create_new(paths, conversation_id="20261103-reserve0002")
        session._ordinary_work_intent = "execute"
        host, _clients = _host(paths, monkeypatch, lambda request: pytest.fail("denied scope must not POST"))
        requests = [
            _request("project-alpha", "task-a", "Task A"),
            _request("project-beta", "task-b", "Task B"),
        ]
        reservations = host.reserve_many(
            session, requests, parent_run_id="parent-run-2", turn_epoch=10,
            live_binding_check=_reservation_live(session, "parent-run-2", 10),
        )
        assert len({row.worker_id for row in reservations}) == 2
        assert len({row.worker_run_id for row in reservations}) == 2
        assert all(row.parent_run_id == "parent-run-2" and row.turn_epoch == 10 for row in reservations)

        outcomes = host.run_many(
            session, requests, scope_confirm_fn=lambda _preview: "n",
            outbound_request_confirm_fn=lambda _preview: "n", write_confirm_fn=lambda *_args: "n",
            cancel_event=threading.Event(), run_id="parent-run-2", turn_epoch=10,
            live_binding_check=lambda *_args: True, reservations=reservations,
            before_worker_start=lambda *_args: pytest.fail("pre-cancelled queued workers must not start"),
        )
        assert len(outcomes) == 2
        assert [(row.task_id, row.worker_id, row.worker_run_id) for row in outcomes] == [
            (request.task_id, reservation.worker_id, reservation.worker_run_id)
            for request, reservation in zip(requests, reservations)
        ]
        assert all(row.run_id == row.parent_run_id == "parent-run-2" for row in outcomes)


def test_pre_cancelled_batch_returns_each_reserved_identity(monkeypatch):
    with temporary_agent_paths() as paths:
        for project_id in ("project-alpha", "project-beta"):
            create_project(paths, project_id, project_template="light")
        session = create_new(paths, conversation_id="20261103-reserve0003")
        session._ordinary_work_intent = "execute"
        host, _clients = _host(paths, monkeypatch, lambda request: pytest.fail("cancelled batch must not POST"))
        requests = [_request("project-alpha", "cancel-a", "Task A"),
                    _request("project-beta", "cancel-b", "Task B")]
        reservations = host.reserve_many(
            session, requests, parent_run_id="parent-run-cancel", turn_epoch=11,
            live_binding_check=_reservation_live(session, "parent-run-cancel", 11),
        )
        cancel = threading.Event()
        cancel.set()
        outcomes = host.run_many(
            session, requests, scope_confirm_fn=lambda _preview: "y",
            outbound_request_confirm_fn=lambda _preview: "y", write_confirm_fn=lambda *_args: "y",
            cancel_event=cancel, parent_run_id="parent-run-cancel", turn_epoch=11,
            live_binding_check=lambda *_args: True, reservations=reservations,
            before_worker_start=lambda *_args: pytest.fail("pre-cancelled queued workers must not start"),
        )
        assert len(outcomes) == 2
        assert [row.status for row in outcomes] == ["cancelled", "cancelled"]
        assert [(row.task_id, row.worker_id, row.worker_run_id, row.reservation_id)
                for row in outcomes] == [
            (request.task_id, reservation.worker_id, reservation.worker_run_id, reservation.reservation_id)
            for request, reservation in zip(requests, reservations)
        ]
        assert all(row.parent_run_id == row.run_id == "parent-run-cancel" for row in outcomes)


def test_release_parent_run_frees_only_its_reservations_and_old_tokens_stay_rejected(monkeypatch):
    with temporary_agent_paths() as paths:
        for project_id in ("project-alpha", "project-beta"):
            create_project(paths, project_id, project_template="light")
        session = create_new(paths, conversation_id="20261103-release0001")
        session._ordinary_work_intent = "execute"
        host, _clients = _host(paths, monkeypatch, lambda request: pytest.fail("denied scope must not POST"))
        request_a = _request("project-alpha", "repeatable-task", "Task A")
        request_b = _request("project-beta", "active-task", "Task B")
        reservation_a = host.reserve_many(
            session, [request_a], parent_run_id="parent-release-a", turn_epoch=21,
            live_binding_check=_reservation_live(session, "parent-release-a", 21),
        )[0]
        reservation_b = host.reserve_many(
            session, [request_b], parent_run_id="parent-release-b", turn_epoch=22,
            live_binding_check=_reservation_live(session, "parent-release-b", 22),
        )[0]

        assert host.release_parent_run(
            conversation_id=session.conversation_id,
            parent_run_id="parent-release-a", turn_epoch=21,
        ) == 1
        assert reservation_a.reservation_id not in host._reservations
        assert reservation_b.reservation_id in host._reservations
        assert (session.conversation_id, "parent-release-b", 22, "project-beta", "active-task") \
            in host._reserved_task_scopes

        old = host.run_many(
            session, [request_a], scope_confirm_fn=lambda _preview: "y",
            outbound_request_confirm_fn=lambda _preview: "y", write_confirm_fn=lambda *_args: "y",
            cancel_event=threading.Event(), parent_run_id="parent-release-a", turn_epoch=21,
            live_binding_check=lambda *_args: True, reservations=(reservation_a,),
        )[0]
        assert old.status == "blocked"
        assert old.error_code == "worker_reservation_unrecognized"
        assert old.reservation_id == reservation_a.reservation_id
        with pytest.raises(PermissionError, match="not currently live"):
            host.reserve_many(
                session, [request_a], parent_run_id="parent-release-a", turn_epoch=21,
                live_binding_check=lambda *_args: False,
            )

        still_live = host.run_many(
            session, [request_b], scope_confirm_fn=lambda _preview: "n",
            outbound_request_confirm_fn=lambda _preview: "n", write_confirm_fn=lambda *_args: "n",
            cancel_event=threading.Event(), parent_run_id="parent-release-b", turn_epoch=22,
            live_binding_check=lambda *_args: True, reservations=(reservation_b,),
        )[0]
        assert still_live.status == "blocked" and still_live.error_code == "scope_denied"
        assert (still_live.worker_id, still_live.worker_run_id) == (
            reservation_b.worker_id, reservation_b.worker_run_id,
        )
        assert host.release_parent_run(
            conversation_id=session.conversation_id,
            parent_run_id="parent-release-b", turn_epoch=22,
        ) == 1
        assert host._reservations == {}
        assert host._reservations_by_parent == {}
        assert host._reserved_task_scopes == set()


def test_repeated_terminal_release_cycles_do_not_accumulate_capacity(monkeypatch):
    with temporary_agent_paths() as paths:
        create_project(paths, "project-alpha", project_template="light")
        session = create_new(paths, conversation_id="20261103-release0002")
        session._ordinary_work_intent = "execute"
        host, _clients = _host(paths, monkeypatch, lambda request: pytest.fail("reservation cycle must not POST"))
        request = _request("project-alpha", "same-task", "Same explicit task")
        issued_reservation_ids = set()
        for epoch in range(1, 1_201):
            parent_run_id = f"parent-cycle-{epoch}"
            reservation = host.reserve_many(
                session, [request], parent_run_id=parent_run_id, turn_epoch=epoch,
                live_binding_check=_reservation_live(session, parent_run_id, epoch),
            )[0]
            issued_reservation_ids.add(reservation.reservation_id)
            assert host.release_parent_run(
                conversation_id=session.conversation_id,
                parent_run_id=parent_run_id, turn_epoch=epoch,
            ) == 1
        assert len(issued_reservation_ids) == 1_200
        assert host._reservations == {}
        assert host._reserved_task_scopes == set()
        final = host.reserve_many(
            session, [request], parent_run_id="parent-cycle-final", turn_epoch=1_201,
            live_binding_check=_reservation_live(session, "parent-cycle-final", 1_201),
        )[0]
        assert final.worker_run_id


def test_concurrent_reservation_race_issues_one_identity_for_same_parent_task(monkeypatch):
    with temporary_agent_paths() as paths:
        create_project(paths, "project-alpha", project_template="light")
        session = create_new(paths, conversation_id="20261103-release0003")
        session._ordinary_work_intent = "execute"
        host, _clients = _host(paths, monkeypatch, lambda request: pytest.fail("reservation race must not POST"))
        request = _request("project-alpha", "racing-task", "Task")
        barrier = threading.Barrier(2)
        accepted = []
        rejected = []

        def reserve():
            barrier.wait(timeout=2)
            try:
                accepted.extend(host.reserve_many(
                    session, [request], parent_run_id="parent-race", turn_epoch=31,
                    live_binding_check=_reservation_live(session, "parent-race", 31),
                ))
            except ValueError as exc:
                rejected.append(str(exc))

        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        assert all(not thread.is_alive() for thread in threads)
        assert len(accepted) == 1
        assert len(rejected) == 1
        assert "already reserved" in rejected[0]


def test_fifteen_task_batch_schedules_five_projects_three_each_with_bounded_parallelism(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=()) as paths:
        project_ids = [f"project-{letter}" for letter in "abcde"]
        for project_id in project_ids:
            create_project(paths, project_id, project_template="light")
        session = create_new(paths, conversation_id="20261104-batch-fifteen001")
        session._ordinary_work_intent = "execute"

        lock = threading.Lock()
        first_wave = threading.Event()
        active = 0
        max_active = 0
        active_by_project: dict[str, int] = {}
        max_by_project: dict[str, int] = {}
        scopes: list[dict[str, Any]] = []
        posts: list[tuple[str, str]] = []
        requests: list[ProjectWorkerHostRequest] = []

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal active, max_active
            body = json.loads(request.content.decode("utf-8"))
            system = body["messages"][0]["content"]
            project_id = next(pid for pid in project_ids if f"project_id is '{pid}'" in system)
            task = body["messages"][1]["content"]
            task_id = next(item.task_id for item in requests if item.project_id == project_id
                           and item.task in task)
            with lock:
                active += 1
                max_active = max(max_active, active)
                active_by_project[project_id] = active_by_project.get(project_id, 0) + 1
                max_by_project[project_id] = max(
                    max_by_project.get(project_id, 0), active_by_project[project_id],
                )
                posts.append((project_id, task_id))
                if len(posts) >= 2:
                    first_wave.set()
                in_first_wave = len(posts) <= 2
            if in_first_wave:
                assert first_wave.wait(3), "two project workers were not dispatched together"
            response = _response(request, {"role": "assistant", "content": f"done {task_id}",
                                           "tool_calls": []}, "stop")
            with lock:
                active -= 1
                active_by_project[project_id] -= 1
            return response

        host, clients = _host(paths, monkeypatch, handler)
        requests.extend(
            _request(project_id, f"task-{project_id}-{ordinal}",
                     f"Handle requirement {ordinal} for {project_id}.", reads=(),
                     writes=(f"result-{ordinal}.txt",))
            for project_id in project_ids for ordinal in (1, 2, 3)
        )
        reservations = host.reserve_many(
            session, requests, parent_run_id="parent-ten", turn_epoch=1,
            live_binding_check=_reservation_live(session, "parent-ten", 1),
        )
        outcomes = host.run_many(
            session, requests,
            scope_confirm_fn=lambda preview: scopes.append(json.loads(preview)) or "y",
            outbound_request_confirm_fn=lambda _preview: "y",
            write_confirm_fn=lambda *_args: "y",
            cancel_event=threading.Event(), parent_run_id="parent-ten", turn_epoch=1,
            live_binding_check=_reservation_live(session, "parent-ten", 1),
            reservations=reservations, max_requests=1,
        )

        assert len(outcomes) == 15
        assert all(row.status == "model_turn_finished" for row in outcomes)
        assert [(row.project_id, row.task_id) for row in outcomes] == [
            (item.project_id, item.task_id) for item in requests
        ]
        assert len({row.worker_id for row in outcomes}) == 15
        assert len({row.worker_run_id for row in outcomes}) == 15
        assert len({row.reservation_id for row in outcomes}) == 15
        assert all(row.parent_run_id == row.run_id == "parent-ten" for row in outcomes)
        assert all(row.worker_run_id != row.parent_run_id for row in outcomes)
        assert max_active == 2
        assert max(max_by_project.values()) == 1
        assert len(clients) == 15
        assert {(doc["project_id"], doc["task_id"]) for doc in scopes} == {
            (item.project_id, item.task_id) for item in requests
        }
        assert len(posts) == 15



def test_partial_pool_submit_failure_keeps_started_real_outcomes_and_marks_unstarted_blocked(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor
    import project_worker_host as host_module

    with temporary_agent_paths(copy_tool_dirs=()) as paths:
        project_ids=("project-alpha","project-beta","project-gamma")
        for project_id in project_ids:create_project(paths,project_id,project_template="light")
        session=create_new(paths,conversation_id="20261106-partialsubmit1");session._ordinary_work_intent="execute"
        requests=[_request(pid,f"task-{pid}",f"Synthetic task for {pid}.",reads=(),writes=("out.txt",)) for pid in project_ids]
        lock=threading.Lock();pair_entered=threading.Event();release=threading.Event()
        active=0;max_active=0;posted=[];started=[];submit_count=0

        def handler(request):
            nonlocal active,max_active
            body=json.loads(request.content.decode("utf-8"));system=body["messages"][0]["content"]
            project_id=next(pid for pid in project_ids if f"project_id is '{pid}'" in system)
            with lock:
                active+=1;max_active=max(max_active,active);posted.append(project_id)
                if active==2:pair_entered.set()
            try:
                assert release.wait(5),"test did not release first two worker calls"
                return _response(request,{"role":"assistant","content":"finished","tool_calls":[]},"stop")
            finally:
                with lock:active-=1

        class FailThirdSubmit:
            def __init__(self,**kwargs):self.executor=RealThreadPoolExecutor(**kwargs)
            def __enter__(self):self.executor.__enter__();return self
            def __exit__(self,*args):return self.executor.__exit__(*args)
            def submit(self,*args,**kwargs):
                nonlocal submit_count
                submit_count+=1
                if submit_count==3:raise RuntimeError("synthetic pool submit failure")
                return self.executor.submit(*args,**kwargs)
        monkeypatch.setattr(host_module,"ThreadPoolExecutor",FailThirdSubmit)
        host,clients=_host(paths,monkeypatch,handler)
        reservations=host.reserve_many(session,requests,parent_run_id="parent-partial-submit",turn_epoch=51,
            live_binding_check=_reservation_live(session,"parent-partial-submit",51))
        cancel=threading.Event();outcomes=[];errors=[]
        def start(request,reservation):
            with lock:started.append((request.project_id,reservation.reservation_id))
            return True
        def run():
            try:
                outcomes.extend(host.run_many(session,requests,scope_confirm_fn=lambda _preview:"y",
                    outbound_request_confirm_fn=lambda _preview:"y",write_confirm_fn=lambda *_args:"y",
                    cancel_event=cancel,parent_run_id="parent-partial-submit",turn_epoch=51,
                    live_binding_check=_reservation_live(session,"parent-partial-submit",51),
                    reservations=reservations,before_worker_start=start))
            except Exception as exc:errors.append(exc)
        thread=threading.Thread(target=run,daemon=True);thread.start()
        try:
            assert pair_entered.wait(5),"two workers did not start before third submit"
            release.set()
        finally:
            release.set();thread.join(8)
        assert not thread.is_alive() and not errors
        assert submit_count==3 and max_active==2
        assert set(posted)==set(project_ids[:2])
        assert {project for project,_reservation in started}==set(project_ids[:2])
        by_task={outcome.task_id:outcome for outcome in outcomes}
        assert len(by_task)==3
        for request,reservation in zip(requests[:2],reservations[:2]):
            outcome=by_task[request.task_id]
            assert outcome.status=="model_turn_finished" and outcome.model_turn is not None
            assert outcome.reservation_id==reservation.reservation_id
        queued=by_task[requests[2].task_id]
        assert queued.status=="blocked" and queued.model_turn is None
        assert queued.error_code=="worker_dispatch_submit_failed"
        assert queued.reservation_id==reservations[2].reservation_id
        assert len(clients)==2

def test_scope_confirmation_rejection_never_records_a_worker_start(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=()) as paths:
        create_project(paths, "project-alpha", project_template="light")
        session = create_new(paths, conversation_id="20261106-startdeny0001")
        session._ordinary_work_intent = "execute"
        host, clients = _host(paths, monkeypatch, lambda _request: pytest.fail("denied scope must not enter worker loop"))
        request = _request("project-alpha", "scope-denied", "Task denied before start", reads=(), writes=("out.txt",))
        reservations = host.reserve_many(
            session, [request], parent_run_id="parent-start-deny", turn_epoch=43,
            live_binding_check=_reservation_live(session, "parent-start-deny", 43),
        )
        starts = []
        outcomes = host.run_many(
            session, [request], scope_confirm_fn=lambda _preview: "n",
            outbound_request_confirm_fn=lambda _preview: "y", write_confirm_fn=lambda *_args: "y",
            cancel_event=threading.Event(), parent_run_id="parent-start-deny", turn_epoch=43,
            live_binding_check=_reservation_live(session, "parent-start-deny", 43), reservations=reservations,
            before_worker_start=lambda *args: starts.append(args) or True,
        )
        assert len(outcomes) == 1 and outcomes[0].error_code == "scope_denied"
        assert starts == []
        assert clients == []

def test_worker_start_hook_waits_for_slot_and_cancelled_same_project_and_third_project_stay_queued(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=()) as paths:
        project_ids = ("project-alpha", "project-beta", "project-gamma")
        for project_id in project_ids:
            create_project(paths, project_id, project_template="light")
        session = create_new(paths, conversation_id="20261106-startslot0001")
        session._ordinary_work_intent = "execute"
        requests = [
            _request("project-alpha", "alpha-running", "Alpha running", reads=(), writes=("alpha.txt",)),
            _request("project-alpha", "alpha-queued", "Alpha queued", reads=(), writes=("queued.txt",)),
            _request("project-beta", "beta-running", "Beta running", reads=(), writes=("beta.txt",)),
            _request("project-gamma", "gamma-queued", "Gamma queued", reads=(), writes=("gamma.txt",)),
        ]
        lock = threading.Lock()
        both_in_provider = threading.Event()
        release_provider = threading.Event()
        active_by_project: dict[str, int] = {}
        max_by_project: dict[str, int] = {}
        started: list[tuple[str, str, str]] = []
        permission_confirmed: set[str] = set()
        posts: list[tuple[str, str]] = []

        def handler(request):
            body = json.loads(request.content.decode("utf-8"))
            system, user = body["messages"][0]["content"], body["messages"][1]["content"]
            project_id = next(pid for pid in project_ids if f"project_id is '{pid}'" in system)
            match = next(item for item in requests if item.project_id == project_id and item.task in user)
            with lock:
                active_by_project[project_id] = active_by_project.get(project_id, 0) + 1
                max_by_project[project_id] = max(max_by_project.get(project_id, 0), active_by_project[project_id])
                posts.append((project_id, match.task_id))
                if sum(active_by_project.values()) == 2:
                    both_in_provider.set()
            assert release_provider.wait(5), "test did not release the controlled worker provider"
            with lock:
                active_by_project[project_id] -= 1
            return _response(request, {"role": "assistant", "content": "done", "tool_calls": []}, "stop")

        host, clients = _host(paths, monkeypatch, handler)
        reservations = host.reserve_many(
            session, requests, parent_run_id="parent-startslot", turn_epoch=41,
            live_binding_check=_reservation_live(session, "parent-startslot", 41),
        )
        cancel = threading.Event()
        outcomes: list[tuple] = []
        thread_errors: list[BaseException] = []

        def confirm_scope(preview):
            row = json.loads(preview)
            with lock:
                permission_confirmed.add(row["task_id"])
            return "y"

        def before_worker_start(request, reservation):
            assert not cancel.is_set()
            assert _reservation_live(session, "parent-startslot", 41)(session, "parent-startslot", 41)
            assert request.task_id in permission_confirmed, "start record must follow scope authorization"
            with lock:
                started.append((request.project_id, request.task_id, reservation.worker_run_id))
            return True

        def dispatch():
            try:
                outcomes.extend(host.run_many(
                    session, requests, scope_confirm_fn=confirm_scope,
                    outbound_request_confirm_fn=lambda _preview: "y",
                    write_confirm_fn=lambda *_args: "y", cancel_event=cancel,
                    parent_run_id="parent-startslot", turn_epoch=41,
                    live_binding_check=_reservation_live(session, "parent-startslot", 41),
                    reservations=reservations, before_worker_start=before_worker_start,
                ))
            except BaseException as exc:
                thread_errors.append(exc)

        thread = threading.Thread(target=dispatch, daemon=True)
        thread.start()
        try:
            assert both_in_provider.wait(5), "two eligible projects did not acquire worker slots"
            with lock:
                assert {(project, task) for project, task, _run in started} == {
                    ("project-alpha", "alpha-running"), ("project-beta", "beta-running"),
                }
                assert len(started) == 2
                assert max(max_by_project.values()) == 1
            cancel.set()
        finally:
            release_provider.set()
            thread.join(8)
        assert not thread.is_alive()
        assert not thread_errors
        assert len(outcomes) == len(requests)
        by_task = {outcome.task_id: outcome for outcome in outcomes}
        assert by_task["alpha-queued"].status == "cancelled"
        assert by_task["gamma-queued"].status == "cancelled"
        assert {(project, task) for project, task, _run in started} == {
            ("project-alpha", "alpha-running"), ("project-beta", "beta-running"),
        }
        reservation_by_task = {row.task_id: row for row in reservations}
        assert all(by_task[task].reservation_id == reservation_by_task[task].reservation_id for task in by_task)
        assert len(posts) == 2
        assert len(clients) == 2


@pytest.mark.parametrize("start_record", [False, "raise"])
def test_start_record_failure_fails_closed_before_model_client_or_worker_loop(monkeypatch, start_record):
    with temporary_agent_paths(copy_tool_dirs=()) as paths:
        create_project(paths, "project-alpha", project_template="light")
        session = create_new(paths, conversation_id="20261106-startfail0001")
        session._ordinary_work_intent = "execute"
        host, clients = _host(paths, monkeypatch, lambda request: pytest.fail("worker model loop must not start"))
        request = _request("project-alpha", "record-fails", "Record before start", reads=(), writes=("out.txt",))
        reservation = host.reserve_many(
            session, [request], parent_run_id="parent-start-fail", turn_epoch=42,
            live_binding_check=_reservation_live(session, "parent-start-fail", 42),
        )
        calls = []

        def before_worker_start(bound_request, bound_reservation):
            calls.append((bound_request.task_id, bound_reservation.worker_run_id))
            if start_record == "raise":
                raise OSError("synthetic durable start record failure")
            return start_record

        outcomes = host.run_many(
            session, [request], scope_confirm_fn=lambda _preview: "y",
            outbound_request_confirm_fn=lambda _preview: "y", write_confirm_fn=lambda *_args: "y",
            cancel_event=threading.Event(), parent_run_id="parent-start-fail", turn_epoch=42,
            live_binding_check=_reservation_live(session, "parent-start-fail", 42), reservations=reservation,
            before_worker_start=before_worker_start,
        )
        assert len(calls) == 1
        assert outcomes[0].status == "blocked"
        assert outcomes[0].error_code == "worker_run_start_record_failed"
        assert outcomes[0].reservation_id == reservation[0].reservation_id
        assert clients == [], "failed start persistence must not construct/start a worker client"

def test_twenty_six_task_batch_is_rejected_before_reservation_or_model_dispatch(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=()) as paths:
        create_project(paths, "project-alpha", project_template="light")
        session = create_new(paths, conversation_id="20261104-batch-over002")
        session._ordinary_work_intent = "execute"
        host, clients = _host(paths, monkeypatch, lambda _request: pytest.fail("overload must not dispatch"))
        requests = [_request("project-alpha", f"task-{index}", f"Task {index}", reads=(),
                             writes=(f"out-{index}.txt",)) for index in range(26)]

        with pytest.raises(ValueError, match="bounded explicit requests"):
            host.reserve_many(
                session, requests, parent_run_id="parent-overload", turn_epoch=1,
                live_binding_check=_reservation_live(session, "parent-overload", 1),
            )
        outcomes = host.run_many(
            session, requests,
            scope_confirm_fn=lambda _preview: "y",
            outbound_request_confirm_fn=lambda _preview: "y",
            write_confirm_fn=lambda *_args: "y",
            cancel_event=threading.Event(), parent_run_id="parent-overload", turn_epoch=1,
            live_binding_check=_reservation_live(session, "parent-overload", 1),
        )
        assert len(outcomes) == 1
        assert outcomes[0].status == "blocked"
        assert outcomes[0].error_code == "worker_batch_limit_or_shape_invalid"
        assert host._reservations == {}
        assert clients == []