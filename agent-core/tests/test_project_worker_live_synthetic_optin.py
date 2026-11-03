"""Explicit live 0x567 worker probe against one temporary synthetic project only."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pytest

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

import httpx
import llm_client
from llm_client import LLMClient, load_config
from llm_models import ModelRegistry
from paths import AgentPaths
from project_mode import create_project
from project_worker_host import ProjectWorkerHost, ProjectWorkerHostRequest
from session import create_new
from tests.isolation_helpers import temporary_agent_paths

OPT_IN = "MY_AGENT_PROJECT_WORKER_LIVE_SYNTHETIC_OPTIN"
MODEL_ID = "0x567-flash"
PROVIDER_MODEL = "gpt-6-luna"
ENDPOINT = "https://api-cdn.0x567.com/v1/chat/completions"
OUTPUT_TEXT = "SYNTHETIC_WORKER_RESULT_741: one harmless synthetic sentence.\n"
TASK = (
    "Use the write_project_file tool exactly once to create only result.txt "
    "in this synthetic project. "
    f"Set its entire content exactly to: {OUTPUT_TEXT!r} "
    "Do not call any other tool or modify another file."
)


@pytest.mark.skipif(os.getenv(OPT_IN) != "1", reason="explicit synthetic-only live model opt-in")
def test_live_project_worker_host_model_toolchain_writes_one_synthetic_file(monkeypatch):
    # Resolve the configured provider credential only in process memory. It is moved to
    # the provider's env slot so the worker client can remain bound to temporary AgentPaths.
    provider_paths = AgentPaths.discover(start=CORE)
    configured_provider = ModelRegistry.load(provider_paths).get(MODEL_ID)
    if configured_provider is None or (configured_provider.id, configured_provider.vendor,
                                       configured_provider.provider_model) != (
        MODEL_ID, "0x567", PROVIDER_MODEL
    ):
        pytest.fail("configured 0x567-flash profile mismatch; no request was sent", pytrace=False)
    configured_endpoint = configured_provider.chat_completions_url()
    configured_url = urlsplit(configured_endpoint)
    if (configured_endpoint != ENDPOINT or configured_url.scheme != "https"
            or configured_url.hostname != "api-cdn.0x567.com" or configured_url.username
            or configured_url.password or configured_url.query or configured_url.fragment
            or configured_url.port not in (None, 443)):
        pytest.fail("configured endpoint outside exact allowlist; no request was sent", pytrace=False)
    try:
        credential = configured_provider.resolve_api_key(provider_paths)
    except Exception:
        pytest.fail("configured provider credential could not be resolved; no request was sent", pytrace=False)
    if not credential:
        pytest.skip("configured provider credential unavailable; no request was sent")
    monkeypatch.setenv("OX567_API_KEY", credential)
    del credential

    # All writable content lives below this per-test temporary root.
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",), prefix="synthetic-live-worker-") as paths:
        project_id = "synthetic-live-741"
        create_project(paths, project_id, project_template="light")
        project_root = paths.workspace / project_id
        original_files = {p.relative_to(project_root).as_posix(): p.read_bytes()
                          for p in project_root.rglob("*") if p.is_file()}
        parent = create_new(paths, conversation_id="20261101-c0ffee71")
        parent._ordinary_work_intent = "execute"
        parent.meta.turn_mode = "agent"
        parent.meta.harness = "desktop"
        parent.messages.append({"role": "user", "content": "PARENT_PRIVATE_SENTINEL_NOT_FOR_MODEL"})
        parent.save()

        configured = ModelRegistry.load(paths).get(MODEL_ID)
        if configured is None or (configured.id, configured.vendor, configured.provider_model) != (
            MODEL_ID, "0x567", PROVIDER_MODEL
        ):
            pytest.fail("isolated worker model profile mismatch; no request was sent", pytrace=False)
        endpoint = configured.chat_completions_url()
        parsed = urlsplit(endpoint)
        if (endpoint != ENDPOINT or parsed.scheme != "https" or parsed.hostname != "api-cdn.0x567.com"
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.port not in (None, 443)):
            pytest.fail("isolated endpoint outside exact allowlist; no request was sent", pytrace=False)
        client = LLMClient(load_config(), paths=paths)
        host = ProjectWorkerHost(lambda: client)
        scope_approvals: list[str] = []
        outbound_approvals: list[dict] = []
        write_approvals: list[str] = []
        state = {"scope_approved": False, "posts": 0}
        response_statuses: list[int] = []

        def approve_scope(preview: str) -> str:
            # Programmatic simulation of a user choosing Yes; this is not UI acceptance.
            try:
                doc = json.loads(preview)
            except Exception:
                return "n"
            if (scope_approvals or doc.get("project_id") != project_id
                    or doc.get("task_id") != "synthetic-write-1"
                    or doc.get("read_targets") != [] or doc.get("write_targets") != ["result.txt"]
                    or "PARENT_PRIVATE_SENTINEL_NOT_FOR_MODEL" in preview):
                return "n"
            scope_approvals.append("synthetic scope approved")
            state["scope_approved"] = True
            return "y"

        def approve_outbound(preview: str) -> str:
            if not state["scope_approved"] or state["posts"] >= 1:
                return "n"
            try:
                doc = json.loads(preview)
                payload = doc["provider_payload"]
            except Exception:
                return "n"
            messages = payload.get("messages", [])
            if (doc.get("model_id") != MODEL_ID or doc.get("vendor") != "0x567"
                    or doc.get("endpoint") != ENDPOINT or payload.get("model") != PROVIDER_MODEL
                    or len(messages) < 2 or not any(row.get("role") == "user" and row.get("content") == TASK for row in messages)
                    or "PARENT_PRIVATE_SENTINEL_NOT_FOR_MODEL" in json.dumps(messages)
                    or doc.get("headers", {}).get("Authorization") != "[redacted]"):
                return "n"
            outbound_approvals.append(doc)
            return "y"

        def approve_write(preview: str, allow_all: bool) -> str:
            if (allow_all is not False or len(write_approvals) >= 1
                    or "result.txt" not in preview or project_id not in preview):
                return "n"
            write_approvals.append("synthetic write approved")
            return "y"

        original_post = httpx.Client.post

        def guarded_post(http_client, url, *args, **kwargs):
            content = kwargs.get("content")
            headers = kwargs.get("headers") or {}
            try:
                payload = json.loads(content.decode("utf-8")) if isinstance(content, bytes) else None
            except Exception:
                payload = None
            if (not state["scope_approved"] or state["posts"] >= 1
                    or str(url) != ENDPOINT or kwargs.get("follow_redirects") is not False
                    or http_client.follow_redirects
                    or type(payload) is not dict or payload.get("model") != PROVIDER_MODEL
                    or headers.get("Content-Type") != "application/json"
                    or not str(headers.get("Authorization", "")).startswith("Bearer ")):
                raise RuntimeError("live synthetic request guard rejected outbound request")
            message_rows = payload.get("messages", [])
            encoded_messages = json.dumps(message_rows, ensure_ascii=False)
            if (not message_rows
                    or not any(row.get("role") == "user" and row.get("content") == TASK for row in message_rows)
                    or "PARENT_PRIVATE_SENTINEL_NOT_FOR_MODEL" in encoded_messages
                    or any(secret_marker in encoded_messages for secret_marker in ("C:\\Users\\", "F:\\my-agent-main"))):
                raise RuntimeError("live synthetic request guard rejected non-synthetic prompt")
            if len(outbound_approvals) != state["posts"] + 1:
                raise RuntimeError("request had no corresponding programmatic outbound approval")
            approved_payload = outbound_approvals[-1].get("provider_payload")
            approved_body = json.dumps(approved_payload, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":"), allow_nan=False).encode("utf-8")
            if content != approved_body or approved_payload != payload:
                raise RuntimeError("approved request payload differs from actual request")
            state["posts"] += 1
            response = original_post(http_client, url, *args, **kwargs)
            response_statuses.append(response.status_code)
            return response

        monkeypatch.setattr(httpx.Client, "post", guarded_post)
        request = ProjectWorkerHostRequest(
            project_id=project_id, task_id="synthetic-write-1", task=TASK,
            read_targets=(), write_targets=("result.txt",),
        )
        try:
            outcome = host.run(
                parent, request,
                scope_confirm_fn=approve_scope,
                outbound_request_confirm_fn=approve_outbound,
                write_confirm_fn=approve_write,
                cancel_event=threading.Event(),
                # One request is enough to return the write tool call; the loop then
                # stops at its budget after host-side write + read-back verification.
                max_requests=1,
                run_id="synthetic-live-parent-run-1", turn_epoch=1,
                live_binding_check=lambda bound, run_id, epoch: (
                    bound is parent and run_id == "synthetic-live-parent-run-1" and epoch == 1
                ),
            )
        except Exception:
            pytest.fail("live provider/worker call failed; private response and exception withheld", pytrace=False)

        actual_files = {p.relative_to(project_root).as_posix(): p.read_bytes()
                        for p in project_root.rglob("*") if p.is_file()}
        result_path = project_root / "result.txt"
        assert outcome.status == "limit_reached", (
            f"worker status={outcome.status}, code={outcome.error_code}, "
            f"posts={state['posts']}, http_statuses={response_statuses}"
        )
        assert outcome.acceptance_status == "not_run"
        assert outcome.model_turn is not None and outcome.model_turn.status == "limit_reached"
        assert outcome.model_turn.error_code == "request_limit"
        assert outcome.model_turn.model_id == MODEL_ID
        assert outcome.model_turn.requests == state["posts"] == 1
        assert outcome.model_turn.tool_calls == 1
        assert len(scope_approvals) == 1 and len(outbound_approvals) == state["posts"]
        # A new in-project text file is policy-classified as safe and may not
        # prompt a second write confirmation; if it does, only this exact target is approved.
        assert len(write_approvals) <= 1
        assert len(outcome.model_turn.writes) == 1
        assert outcome.model_turn.writes[0].status == "written"
        assert outcome.model_turn.writes[0].verification == "content_match"
        assert outcome.model_turn.writes[0].readback_succeeded is True
        assert result_path.read_text(encoding="utf-8") == OUTPUT_TEXT
        assert set(actual_files) == set(original_files) | {"result.txt"}
        assert all(actual_files[name] == content for name, content in original_files.items())
        assert not (paths.workspace / "synthetic-live-741-neighbor").exists()

