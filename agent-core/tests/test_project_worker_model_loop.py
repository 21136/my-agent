"""Tool-loop contract tests use the real LLMClient plus isolated real writes."""
from __future__ import annotations

import json
from dataclasses import replace
import sys
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
_REAL_HTTPX_CLIENT = httpx.Client
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

import llm_client
import project_worker_execution as worker_execution
from llm_client import LLMClient, LLMConfig
from project_mode import create_project
from project_worker_execution import PROJECT_WORKER_COORDINATOR, ProjectWorkerSpec
from project_worker_model_loop import run_project_worker_model_loop
from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry


def _tool_call(name: str, arguments: dict[str, Any], call_id: str = "call-1") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _completion(message: dict[str, Any], finish: str) -> dict[str, Any]:
    return {
        "model": "gpt-6-luna",
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": {"prompt_tokens": 9, "completion_tokens": 4},
    }


class _ApproveExternalRequests:
    def __init__(self, callback=None):
        self.previews: list[str] = []
        self.callback = callback

    def __call__(self, preview: str) -> str:
        self.previews.append(preview)
        return self.callback(preview, len(self.previews)) if self.callback else "y"


def _harness(paths, monkeypatch, responses, *, after_response=None, confirm=None):
    requests: list[dict[str, Any]] = []
    request_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        requests.append(body)
        request_urls.append(str(request.url))
        response_payload = responses[len(requests) - 1]
        response = _completion(*response_payload)
        if after_response is not None:
            after_response(len(requests))
        return httpx.Response(200, json=response, request=request)

    monkeypatch.setenv("OX567_API_KEY", "test-only-never-print")
    monkeypatch.setenv("OX567_BASE_URL", "https://api-cdn.0x567.com")
    monkeypatch.setenv("OX567_MODEL_FLASH", "gpt-6-luna")
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        llm_client.httpx,
        "Client",
        lambda **kwargs: _REAL_HTTPX_CLIENT(transport=transport, **kwargs),
    )
    config = LLMConfig(
        api_key=None, base_url="https://api-cdn.0x567.com", model="0x567-flash",
        model_coding="0x567-pro", timeout_sec=3, context_limit_override=None,
    )
    client = LLMClient(config, paths=paths)
    create_project(paths, "worker-a", project_template="light")
    executor = ToolExecutor(
        registry=ToolRegistry.load(paths),
        session=ExecutorSession(
            allowed_evolved={"write_text", "patch_file"},
            project_root="workspace/worker-a", project_id="worker-a",
            active_shell="project", project_plan_status="confirmed",
            project_workflow_stage="implementation", project_entry="direct",
        ),
        confirm_fn=confirm or (lambda _preview, _all: "n"),
    )
    cancel = threading.Event()
    external_confirm = _ApproveExternalRequests()
    spec = ProjectWorkerSpec(
        conversation_id="parent-chat-1", project_id="worker-a",
        project_root=paths.workspace / "worker-a", write_targets=("result.txt",),
        executor=executor, host_binding_check=lambda: True,
        read_targets=("README.md",), cancel_event=cancel,
        outbound_request_confirm_fn=external_confirm,
    )
    return client, spec, cancel, requests, request_urls


def test_real_llmclient_protocol_reads_then_writes_only_declared_temp_project(monkeypatch, caplog):
    caplog.set_level("INFO", logger="my_agent.sidecar")
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        model_responses = [
            ({"role": "assistant", "content": None,
              "tool_calls": [_tool_call("read_project_file", {"path": "README.md"})]}, "tool_calls"),
            ({"role": "assistant", "content": None,
              "tool_calls": [_tool_call("write_project_file", {
                  "path": "result.txt", "content": "synthetic worker output\n"})]}, "tool_calls"),
            ({"role": "assistant", "content": "Created and read back result.txt.",
              "tool_calls": []}, "stop"),
        ]
        client, spec, _cancel, requests, request_urls = _harness(paths, monkeypatch, model_responses)
        (paths.workspace / "worker-a" / "README.md").write_text("synthetic project source\n", encoding="utf-8")
        result = run_project_worker_model_loop(
            spec, "Create result.txt based on the source README.", client=client,
        )
        timing = [record.getMessage() for record in caplog.records
                  if "event=project_worker_timing" in record.getMessage()]
        assert any("phase=outbound_confirmation" in row and "duration_ms=" in row for row in timing)
        assert any("phase=model_request_cycle" in row and "duration_ms=" in row for row in timing)
        assert any("phase=write_tool" in row and "duration_ms=" in row for row in timing)
        assert all("synthetic worker output" not in row and "README.md" not in row for row in timing)
        assert result.status == "model_turn_finished"
        assert result.project_id == "worker-a"
        assert result.model_id == "0x567-flash"
        assert result.requests == 3
        assert result.writes[0].path == "result.txt"
        assert result.writes[0].verification == "content_match"
        assert (paths.workspace / "worker-a" / "result.txt").read_text(encoding="utf-8") == "synthetic worker output\n"
        assert all(body["model"] == "gpt-6-luna" for body in requests)
        assert all(url == "https://api-cdn.0x567.com/v1/chat/completions" for url in request_urls)
        assert all(set(tool["function"]["name"] for tool in body["tools"])
                   == {"read_project_file", "write_project_file", "patch_project_file"} for body in requests)
        assert requests[0]["messages"][0]["role"] == "system"
        assert "worker-a" in requests[0]["messages"][0]["content"]
        assert len(requests[0]["messages"]) == 2
        assert requests[0]["messages"][1]["content"] == "Create result.txt based on the source README."
        approval_docs = [json.loads(item) for item in spec.outbound_request_confirm_fn.previews]
        assert len(approval_docs) == len(requests) == 3
        assert all(doc["method"] == "POST" for doc in approval_docs)
        assert all(doc["headers"]["Authorization"] == "[redacted]" for doc in approval_docs)
        assert all(doc["provider_payload"] == requests[index]
                   for index, doc in enumerate(approval_docs))
        assert approval_docs[1]["scope"]["read_targets"] == ["README.md"]
        read_result = next(message for message in approval_docs[1]["provider_payload"]["messages"]
                           if message["role"] == "tool")
        assert json.loads(read_result["content"])["content"].replace("\r\n", "\n") == "synthetic project source\n"
        assert "test-only-never-print" not in json.dumps(approval_docs, ensure_ascii=False)


def test_unsupported_tool_is_rejected_without_any_write_or_retry(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        responses = [({"role": "assistant", "content": None,
                       "tool_calls": [_tool_call("run_command", {"command": "echo escape"})]}, "tool_calls")]
        client, spec, _cancel, requests, _urls = _harness(paths, monkeypatch, responses)
        result = run_project_worker_model_loop(spec, "Do safe work.", client=client)
        assert result.status == "failed"
        assert result.error_code == "tool_not_allowed"
        assert result.requests == 1
        assert not (paths.workspace / "worker-a" / "result.txt").exists()
        assert len(requests) == 1


def test_undeclared_read_is_not_attempted_and_worker_can_correct_itself(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        responses = [
            ({"role": "assistant", "content": None,
              "tool_calls": [_tool_call("read_project_file", {"path": "secret.md"}, "bad-read")]}, "tool_calls"),
            ({"role": "assistant", "content": None,
              "tool_calls": [_tool_call("read_project_file", {"path": "README.md"}, "good-read")]}, "tool_calls"),
            ({"role": "assistant", "content": None,
              "tool_calls": [_tool_call("write_project_file", {
                  "path": "result.txt", "content": "completed from allowed input"}, "write-result")]}, "tool_calls"),
            ({"role": "assistant", "content": "Completed using the authorized input.",
              "tool_calls": []}, "stop"),
        ]
        client, spec, _cancel, requests, _urls = _harness(paths, monkeypatch, responses)
        (paths.workspace / "worker-a" / "README.md").write_text("authorized source\n", encoding="utf-8")
        attempted_snapshot_paths: list[str] = []
        original_read_snapshot = worker_execution.ScopedProjectTools.read_snapshot

        def record_snapshot_attempt(scoped, relative_path):
            attempted_snapshot_paths.append(relative_path)
            return original_read_snapshot(scoped, relative_path)

        monkeypatch.setattr(worker_execution.ScopedProjectTools, "read_snapshot", record_snapshot_attempt)
        result = run_project_worker_model_loop(
            spec, "Read an authorized source and create the result.", client=client, max_requests=4,
        )

        assert result.status == "model_turn_finished"
        assert result.requests == 4
        assert len(spec.outbound_request_confirm_fn.previews) == 4
        assert result.writes[0].path == "result.txt"
        assert "README.md" in attempted_snapshot_paths
        assert "secret.md" not in attempted_snapshot_paths
        correction = next(message for message in requests[1]["messages"]
                          if message["role"] == "tool" and message["tool_call_id"] == "bad-read")
        correction_payload = json.loads(correction["content"])
        assert correction_payload["ok"] is False
        assert correction_payload["error"] == "read_target_not_declared"
        assert correction_payload["operation"] == "read_project_file"
        assert correction_payload["requested_path"] == "secret.md"
        assert correction_payload["allowed_targets"] == ["README.md"]
        assert correction_payload["declared_read_targets"] == ["README.md"]
        assert correction_payload["declared_write_targets"] == ["result.txt"]
        assert correction_payload["match_result"] == "not_declared"
        assert correction_payload["hint"]


def test_repeated_undeclared_reads_fail_without_reading_or_extra_requests(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        responses = [
            ({"role": "assistant", "content": None,
              "tool_calls": [_tool_call("read_project_file", {"path": "secret-a.md"}, "bad-read-1")]}, "tool_calls"),
            ({"role": "assistant", "content": None,
              "tool_calls": [_tool_call("read_project_file", {"path": "secret-b.md"}, "bad-read-2")]}, "tool_calls"),
        ]
        client, spec, _cancel, requests, _urls = _harness(paths, monkeypatch, responses)
        attempted_snapshot_paths: list[str] = []
        original_read_snapshot = worker_execution.ScopedProjectTools.read_snapshot

        def record_snapshot_attempt(scoped, relative_path):
            attempted_snapshot_paths.append(relative_path)
            return original_read_snapshot(scoped, relative_path)

        monkeypatch.setattr(worker_execution.ScopedProjectTools, "read_snapshot", record_snapshot_attempt)
        result = run_project_worker_model_loop(
            spec, "Read an authorized source.", client=client, max_requests=4,
        )

        assert result.status == "failed"
        assert result.error_code == "read_target_not_declared"
        assert result.requests == 2
        assert result.tool_calls == 2
        assert len(requests) == 2
        assert attempted_snapshot_paths == []
        assert result.read_attempts == ()
        assert result.scope_error_detail is not None
        assert result.scope_error_detail["requested_path"] == "secret-b.md"
        assert result.scope_error_detail["allowed_targets"] == ["README.md"]
        assert result.scope_error_detail["declared_write_targets"] == ["result.txt"]
        first_correction = next(message for message in requests[1]["messages"]
                                if message["role"] == "tool" and message["tool_call_id"] == "bad-read-1")
        first_payload = json.loads(first_correction["content"])
        assert first_payload["requested_path"] == "secret-a.md"
        assert first_payload["allowed_targets"] == ["README.md"]


def test_denied_existing_executor_confirmation_is_not_retried(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        responses = [({"role": "assistant", "content": None,
                       "tool_calls": [_tool_call("write_project_file", {
                           "path": "PROJECT.md", "content": "should not persist"})]}, "tool_calls")]
        prompts: list[str] = []
        client, spec, _cancel, requests, _urls = _harness(
            paths, monkeypatch, responses, confirm=lambda preview, _all: prompts.append(preview) or "n"
        )
        spec = ProjectWorkerSpec(
            conversation_id=spec.conversation_id, project_id=spec.project_id,
            project_root=spec.project_root, write_targets=("PROJECT.md",),
            executor=spec.executor, host_binding_check=spec.host_binding_check,
            read_targets=("PROJECT.md",), cancel_event=spec.cancel_event,
            outbound_request_confirm_fn=spec.outbound_request_confirm_fn,
        )
        original = (paths.workspace / "worker-a" / "PROJECT.md").read_text(encoding="utf-8")
        result = run_project_worker_model_loop(spec, "Modify the project charter.", client=client)
        assert result.status == "blocked"
        assert result.error_code == "confirm_rejected"
        assert result.writes[0].status == "not_written"
        assert result.verification_level == "not_verified"
        assert result.requests == 1
        assert len(prompts) == 1 and "PROJECT.md" in prompts[0]
        assert (paths.workspace / "worker-a" / "PROJECT.md").read_text(encoding="utf-8") == original
        assert len(requests) == 1


def test_duplicate_write_attempt_for_same_target_stops_after_first_write(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        responses = [
            ({"role": "assistant", "content": None,
              "tool_calls": [_tool_call("write_project_file", {
                  "path": "result.txt", "content": "first write\n"})]}, "tool_calls"),
            ({"role": "assistant", "content": None,
              "tool_calls": [_tool_call("write_project_file", {
                  "path": "result.txt", "content": "retry must not run\n"})]}, "tool_calls"),
        ]
        client, spec, _cancel, requests, _urls = _harness(paths, monkeypatch, responses)
        result = run_project_worker_model_loop(spec, "Write once only.", client=client)
        assert result.status == "failed"
        assert result.error_code == "write_already_attempted"
        assert result.requests == 2
        assert (paths.workspace / "worker-a" / "result.txt").read_text(encoding="utf-8") == "first write\n"
        assert len(requests) == 2


def test_cancel_after_model_response_stops_tool_dispatch(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        responses = [({"role": "assistant", "content": None,
                       "tool_calls": [_tool_call("write_project_file", {
                           "path": "result.txt", "content": "cancelled"})]}, "tool_calls")]
        cancel_ref: list[threading.Event] = []

        def cancel_after_first(count: int):
            if count == 1:
                cancel_ref[0].set()

        client, spec, cancel, requests, _urls = _harness(
            paths, monkeypatch, responses, after_response=cancel_after_first
        )
        cancel_ref.append(cancel)
        result = run_project_worker_model_loop(spec, "Write and be cancellable.", client=client)
        assert result.status == "cancelled"
        assert result.requests == 1
        assert not (paths.workspace / "worker-a" / "result.txt").exists()
        assert len(requests) == 1


def test_request_budget_stops_without_another_provider_call(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        responses = [({"role": "assistant", "content": None,
                       "tool_calls": [_tool_call("read_project_file", {"path": "README.md"})]}, "tool_calls")]
        client, spec, _cancel, requests, _urls = _harness(paths, monkeypatch, responses)
        (paths.workspace / "worker-a" / "README.md").write_text("synthetic\n", encoding="utf-8")
        result = run_project_worker_model_loop(
            spec, "Read then stop.", client=client, max_requests=1
        )
        assert result.status == "limit_reached"
        assert result.error_code == "request_limit"
        assert result.requests == 1
        assert len(requests) == 1


def test_wrong_0x567_model_profile_fails_before_network(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        responses = []
        client, spec, _cancel, requests, _urls = _harness(paths, monkeypatch, responses)
        monkeypatch.setenv("OX567_MODEL_FLASH", "gpt-6-sol")
        result = run_project_worker_model_loop(spec, "Do not call a different model.", client=client)
        assert result.status == "failed"
        assert result.error_code == "model_profile_mismatch"
        assert result.requests == 0
        assert requests == []





def test_malformed_extra_tool_call_in_raw_protocol_is_rejected_before_write(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        responses = [({"role": "assistant", "content": None,
                       "tool_calls": [
                           _tool_call("write_project_file", {
                               "path": "result.txt", "content": "must not write"}),
                           "malformed-extra-call",
                       ]}, "tool_calls")]
        client, spec, _cancel, requests, _urls = _harness(paths, monkeypatch, responses)
        result = run_project_worker_model_loop(spec, "Only one exact tool is allowed.", client=client)
        assert result.status == "failed"
        assert result.error_code == "multiple_or_malformed_tool_calls"
        assert result.requests == 1
        assert not (paths.workspace / "worker-a" / "result.txt").exists()
        assert len(requests) == 1



def test_cancel_while_queued_for_project_lease_never_calls_model(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        responses = []
        client, spec, cancel, requests, _urls = _harness(paths, monkeypatch, responses)
        holder = PROJECT_WORKER_COORDINATOR.write_lease(
            "separate-holder", "worker-a", spec.project_root, ("holder.txt",)
        )
        holder.__enter__()
        outcome: list[Any] = []
        thread = threading.Thread(target=lambda: outcome.append(
            run_project_worker_model_loop(spec, "Wait and then be cancelled.", client=client)
        ))
        thread.start()
        try:
            with PROJECT_WORKER_COORDINATOR._condition:
                assert PROJECT_WORKER_COORDINATOR._condition.wait_for(
                    lambda: any(item.conversation_id == spec.conversation_id
                                 for item in PROJECT_WORKER_COORDINATOR._waiting), timeout=1
                )
            cancel.set()
            thread.join(2)
            assert not thread.is_alive(), "cancelled admission remained blocked"
            assert outcome[0].status == "cancelled"
            assert outcome[0].requests == 0
            assert requests == []
        finally:
            if thread.is_alive():
                cancel.set()
                thread.join(2)
            holder.__exit__(None, None, None)



def test_model_can_patch_an_existing_declared_file_through_scoped_executor(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file")) as paths:
        responses = [
            ({"role": "assistant", "content": None,
              "tool_calls": [_tool_call("read_project_file", {"path": "README.md"})]}, "tool_calls"),
            ({"role": "assistant", "content": None,
              "tool_calls": [_tool_call("patch_project_file", {
                  "path": "README.md", "find": "source sentence", "replacement": "revised sentence"})]}, "tool_calls"),
            ({"role": "assistant", "content": "Updated the authorized README sentence.",
              "tool_calls": []}, "stop"),
        ]
        client, spec, _cancel, requests, _urls = _harness(paths, monkeypatch, responses)
        root = paths.workspace / "worker-a"
        (root / "README.md").write_text("A source sentence.\n", encoding="utf-8")
        spec = ProjectWorkerSpec(
            conversation_id=spec.conversation_id, project_id=spec.project_id,
            project_root=spec.project_root, write_targets=("README.md",),
            executor=spec.executor, host_binding_check=spec.host_binding_check,
            read_targets=("README.md",), cancel_event=spec.cancel_event,
            outbound_request_confirm_fn=spec.outbound_request_confirm_fn,
        )
        result = run_project_worker_model_loop(spec, "Revise the sentence.", client=client)
        assert result.status == "model_turn_finished"
        assert result.writes[0].verification == "content_match"
        assert (root / "README.md").read_text(encoding="utf-8") == "A revised sentence.\n"
        assert len(requests) == 3



def test_patch_with_non_unique_anchor_is_rejected_without_write(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file")) as paths:
        responses = [
            ({"role": "assistant", "content": None,
              "tool_calls": [_tool_call("read_project_file", {"path": "README.md"})]}, "tool_calls"),
            ({"role": "assistant", "content": None,
              "tool_calls": [_tool_call("patch_project_file", {
                  "path": "README.md", "find": "repeat", "replacement": "once"})]}, "tool_calls"),
        ]
        client, spec, _cancel, requests, _urls = _harness(paths, monkeypatch, responses)
        root = paths.workspace / "worker-a"
        original = "repeat repeat\n"
        (root / "README.md").write_text(original, encoding="utf-8")
        spec = ProjectWorkerSpec(
            conversation_id=spec.conversation_id, project_id=spec.project_id,
            project_root=spec.project_root, write_targets=("README.md",),
            executor=spec.executor, host_binding_check=spec.host_binding_check,
            read_targets=("README.md",), cancel_event=spec.cancel_event,
            outbound_request_confirm_fn=spec.outbound_request_confirm_fn,
        )
        result = run_project_worker_model_loop(spec, "Do not guess an ambiguous patch.", client=client)
        assert result.status == "failed"
        assert result.error_code == "patch_anchor_not_unique"
        assert result.requests == 2
        assert (root / "README.md").read_text(encoding="utf-8") == original
        assert len(requests) == 2


def test_missing_external_request_confirmation_fails_closed_before_post(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        client, spec, _cancel, requests, _urls = _harness(paths, monkeypatch, [])
        spec = replace(spec, outbound_request_confirm_fn=None)
        result = run_project_worker_model_loop(spec, "Synthetic task must not leave without approval.", client=client)
        assert result.status == "blocked"
        assert result.error_code == "outbound_confirmation_required"
        assert result.requests == 0
        assert requests == []



def test_executor_error_after_side_effect_is_unknown_and_readback_is_labeled(monkeypatch):
    from tools.schema import tool_fail

    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        responses = [({"role": "assistant", "content": None,
                       "tool_calls": [_tool_call("write_project_file", {
                           "path": "result.txt", "content": "possibly applied"})]}, "tool_calls")]
        client, spec, _cancel, requests, _urls = _harness(paths, monkeypatch, responses)
        target = paths.workspace / "worker-a" / "result.txt"

        def write_then_return_error(_name, _arguments):
            target.write_text("possibly applied", encoding="utf-8")
            return tool_fail("write_text", "execution_error", "simulated post-write executor failure")

        spec.executor.run = write_then_return_error
        result = run_project_worker_model_loop(spec, "Write once, never retry.", client=client)
        assert result.status == "failed"
        assert result.error_code == "write_outcome_unknown"
        assert result.requests == 1
        assert len(requests) == 1
        assert target.read_text(encoding="utf-8") == "possibly applied"
        assert result.writes[0].status == "outcome_unknown"
        assert result.writes[0].readback_succeeded is True
        assert result.writes[0].verification == "readback_after_error"
        assert result.writes[0].error_code == "execution_error"
        assert result.verification_level == "file_readback_only"


def test_model_stop_without_tools_is_only_turn_finished_not_task_completion(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        responses = [({"role": "assistant", "content": "Done; all tests passed.",
                       "tool_calls": []}, "stop")]
        client, spec, _cancel, requests, _urls = _harness(paths, monkeypatch, responses)
        result = run_project_worker_model_loop(spec, "Make a change and run tests.", client=client)
        assert result.status == "model_turn_finished"
        assert result.final_text == "Done; all tests passed."
        assert result.writes == ()
        assert result.tests_run == ()
        assert result.verification_level == "no_file_changes"
        assert len(requests) == 1


def test_invalid_none_spec_returns_safe_failure_without_attribute_error():
    result = run_project_worker_model_loop(None, "task", client=None)
    assert result.status == "failed"
    assert result.project_id == ""
    assert result.error_code == "invalid_worker_request"
    assert result.requests == 0



def test_external_request_denial_posts_zero_even_if_write_confirm_would_allow(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        responses = [({"role": "assistant", "content": "must not be received", "tool_calls": []}, "stop")]
        client, spec, _cancel, requests, _urls = _harness(paths, monkeypatch, responses)
        spec.executor.confirm_fn = lambda _preview, _all: "y"
        previews: list[str] = []
        spec = replace(spec, outbound_request_confirm_fn=lambda preview: previews.append(preview) or "n")
        result = run_project_worker_model_loop(spec, "Synthetic task; external send denied.", client=client)
        assert result.status == "blocked"
        assert result.error_code == "outbound_request_denied"
        assert result.requests == 0
        assert len(previews) == 1
        assert requests == []


@pytest.mark.parametrize("mutation", ["content", "identity"])
def test_project_source_change_after_approval_blocks_post_and_requires_fresh_approval(monkeypatch, mutation):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        responses = [
            ({"role": "assistant", "content": None,
              "tool_calls": [_tool_call("read_project_file", {"path": "README.md"})]}, "tool_calls"),
            ({"role": "assistant", "content": "finished", "tool_calls": []}, "stop"),
        ]
        client, spec, _cancel, requests, _urls = _harness(paths, monkeypatch, responses)
        source = paths.workspace / "worker-a" / "README.md"
        original = "approved source snapshot\\n"
        source.write_text(original, encoding="utf-8")
        previews: list[str] = []

        def confirm_then_mutate(preview: str) -> str:
            previews.append(preview)
            if len(previews) == 2:
                if mutation == "content":
                    source.write_text("changed after approval\\n", encoding="utf-8")
                else:
                    old = source.with_name("README.old")
                    source.rename(old)
                    source.write_text(original, encoding="utf-8")
            return "y"

        spec = replace(spec, outbound_request_confirm_fn=confirm_then_mutate)
        result = run_project_worker_model_loop(spec, "Read README, then finish.", client=client)
        assert result.status == "blocked"
        assert result.error_code == "outbound_source_changed"
        assert result.requests == 1
        assert len(previews) == 2
        assert len(requests) == 1
        preview_json = json.loads(previews[1])
        tool_result = next(message for message in preview_json["provider_payload"]["messages"]
                           if message["role"] == "tool")
        assert json.loads(tool_result["content"])["content"] == original





def test_executor_exception_after_write_attempt_is_unknown_and_never_retried(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        responses = [({"role": "assistant", "content": None,
                       "tool_calls": [_tool_call("write_project_file", {
                           "path": "result.txt", "content": "side effect before exception"})]}, "tool_calls")]
        client, spec, _cancel, requests, _urls = _harness(paths, monkeypatch, responses)
        target = paths.workspace / "worker-a" / "result.txt"
        calls = []

        def write_then_raise(_name, _arguments):
            calls.append(1)
            target.write_text("side effect before exception", encoding="utf-8")
            raise OSError("post-write executor exception")

        spec.executor.run = write_then_raise
        result = run_project_worker_model_loop(spec, "Write once; error means do not retry.", client=client)
        assert result.status == "failed"
        assert result.error_code == "write_outcome_unknown"
        assert result.writes[0].status == "outcome_unknown"
        assert result.writes[0].readback_succeeded is True
        assert result.verification_level == "file_readback_only"
        assert calls == [1]
        assert len(requests) == 1
        assert target.read_text(encoding="utf-8") == "side effect before exception"





def test_read_project_file_retries_without_model_retry_and_returns_attempt_evidence(monkeypatch):
    from project_worker_read import ProjectReadTransientError
    import project_worker_execution as execution
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        responses = [
            ({"role": "assistant", "content": None,
              "tool_calls": [_tool_call("read_project_file", {"path": "README.md"})]}, "tool_calls"),
            ({"role": "assistant", "content": "Read completed.", "tool_calls": []}, "stop"),
        ]
        client, spec, _cancel, requests, _urls = _harness(paths, monkeypatch, responses)
        (paths.workspace / "worker-a" / "README.md").write_text("source body", encoding="utf-8")
        calls = []
        class RetryReader:
            def __init__(self, _root): self._root_identity = (1, 2, 3)
            def read_texts(self, names):
                calls.append(tuple(names))
                if len(calls) == 1:
                    raise ProjectReadTransientError("sharing_violation")
                return {"README.md": "source body"}
        monkeypatch.setattr(execution, "ProjectTextReader", RetryReader)
        result = run_project_worker_model_loop(spec, "Read the authorized source.", client=client)
        assert result.status == "model_turn_finished"
        assert result.requests == len(requests) == 2
        assert len(calls) == len(result.read_attempts) == 4
        assert [(entry.read_id, entry.path, entry.attempt, entry.result, entry.error_code)
                for entry in result.read_attempts[:2]] == [
                    (1, "README.md", 1, "retrying", "sharing_violation"),
                    (1, "README.md", 2, "success", ""),
                ]
        assert all(entry.result == "success" for entry in result.read_attempts[2:])
        assert "source body" not in repr(result.read_attempts)


def test_cancellation_raised_after_write_attempt_retains_unknown_receipt(monkeypatch):
    from project_worker_execution import ProjectWorkerCancelled
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        responses = [({"role": "assistant", "content": None,
                       "tool_calls": [_tool_call("write_project_file", {
                           "path": "result.txt", "content": "possibly written"})]}, "tool_calls")]
        client, spec, cancel, requests, _urls = _harness(paths, monkeypatch, responses)
        target = paths.workspace / "worker-a" / "result.txt"
        calls = []
        def write_then_cancel(*_args):
            calls.append(1)
            target.write_text("possibly written", encoding="utf-8")
            cancel.set()
            raise ProjectWorkerCancelled("synthetic post-write cancellation")
        spec.executor.run = write_then_cancel
        result = run_project_worker_model_loop(spec, "Write once.", client=client)
        assert result.status == "cancelled"
        assert result.error_code == "write_outcome_unknown"
        assert len(calls) == len(requests) == 1
        assert target.read_text(encoding="utf-8") == "possibly written"
        assert len(result.writes) == 1
        assert result.writes[0].status == "outcome_unknown"
        assert result.writes[0].verification == "readback_failed"
        assert result.writes[0].readback_succeeded is False
        assert result.verification_level == "not_verified"
