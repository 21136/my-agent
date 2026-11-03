"""Opt-in live 0x567 worker -> one isolated pytest -> durable self-report integration."""
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
from llm_client import LLMClient, load_config
from llm_models import ModelRegistry
from paths import AgentPaths
from project_mode import create_project
from project_worker_host import ProjectWorkerHost, ProjectWorkerHostRequest
from project_worker_work_record_settlement import (
    ProjectWorkerWorkRecordBinding,
    _requirement_matches_bound_scope,
    settle_project_worker_work_record,
)
from server import WsBridge
from session import create_new
from work_record_settlement import BoundExecution
from work_records import WorkRecordStore
from tests.isolation_helpers import temporary_agent_paths

OPT_IN = "MY_AGENT_PROJECT_WORKER_REQUIREMENT_LIVE_OPTIN"
MODEL_ID = "0x567-flash"
PROVIDER_MODEL = "gpt-6-luna"
ENDPOINT = "https://api-cdn.0x567.com/v1/chat/completions"
PROJECT_ID = "synthetic-requirement-live-901"
TASK_ID = "synthetic-requirement-live-task"
RESULT_TEXT = "SYNTHETIC_REQUIREMENT_RESULT_901\n"
TASK = (
    "Use the write_project_file tool exactly once to create only result.txt "
    f"in this synthetic project, with entire content exactly {RESULT_TEXT!r}. "
    "Do not call another tool or modify another file."
)
TARGET = "test_feature.py::test_target"


@pytest.mark.skipif(os.getenv(OPT_IN) != "1", reason="explicit live 0x567 integration opt-in")
def test_live_worker_single_requirement_pytest_persists_self_report(monkeypatch):
    # Resolve only the configured provider secret; never include it in diagnostics.
    provider_paths = AgentPaths.discover(start=CORE)
    entry = ModelRegistry.load(provider_paths).get(MODEL_ID)
    if entry is None or (entry.id, entry.vendor, entry.provider_model) != (MODEL_ID, "0x567", PROVIDER_MODEL):
        pytest.fail("configured 0x567-flash profile mismatch; no request was sent", pytrace=False)
    configured_endpoint = entry.chat_completions_url()
    parsed = urlsplit(configured_endpoint)
    if (configured_endpoint != ENDPOINT or parsed.scheme != "https" or parsed.hostname != "api-cdn.0x567.com"
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.port not in (None, 443)):
        pytest.fail("configured provider endpoint outside exact allowlist; no request was sent", pytrace=False)
    try:
        credential = entry.resolve_api_key(provider_paths)
    except Exception:
        pytest.fail("provider credential unavailable; no request was sent", pytrace=False)
    if not credential:
        pytest.skip("provider credential unavailable; no request was sent")
    monkeypatch.setenv("OX567_API_KEY", credential)
    del credential
    monkeypatch.setenv("OX567_BASE_URL", "https://api-cdn.0x567.com")
    monkeypatch.setenv("OX567_MODEL_FLASH", PROVIDER_MODEL)

    posts: list[int] = []
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",), prefix="worker-requirement-live-") as paths:
        create_project(paths, PROJECT_ID, project_template="light")
        project_root = paths.workspace / PROJECT_ID
        (project_root / "test_feature.py").write_text(
            "from pathlib import Path\n"
            "def test_target():\n"
            f"    assert Path('result.txt').read_text(encoding='utf-8') == {RESULT_TEXT!r}\n"
            "    Path('pytest-ran.txt').write_text('ran', encoding='utf-8')\n",
            encoding="utf-8",
        )
        original_project_files = {p.relative_to(project_root).as_posix() for p in project_root.rglob("*") if p.is_file()}

        session = create_new(paths, conversation_id="20261101-live901")
        session.meta.turn_mode = "agent"
        session.meta.harness = "desktop"
        session.meta.project_id = PROJECT_ID
        session.meta.project_root = f"workspace/{PROJECT_ID}"
        session._ordinary_work_intent = "execute"
        session.messages.append({"role": "user", "content": "PARENT_PRIVATE_SENTINEL_NOT_FOR_WORKER"})
        session.save()

        store = WorkRecordStore(session)
        item = store.create_item(title="Synthetic single requirement", scope={"kind": "project", "project_id": PROJECT_ID})
        requirement = store.add_requirement(item["item_id"], TASK, event_id="live901-requirement")
        events: list[dict] = []
        bridge: WsBridge

        def emit(event):
            events.append(event)
            if (event.get("type") == "confirm.request"
                    and event.get("confirm_subtype") == "project_worker_requirement_verification"):
                bridge.deliver_confirm(event["request_id"], "y")

        bridge = WsBridge(emit=emit, paths=paths)
        bridge._session_provider = lambda: session
        assert bridge.begin_turn(mode="ordinary")
        snapshot = bridge.lifecycle.snapshot()
        bridge.lifecycle_conversation_id = session.conversation_id
        bridge._turn_busy.set()
        store.begin_requirement_run(item["item_id"], requirement["requirement_id"],
                                   run_id=snapshot.run_id, expected_current_run_id=None)
        execution = BoundExecution(session.conversation_id, item["item_id"], requirement["requirement_id"], snapshot.run_id)
        request = ProjectWorkerHostRequest(PROJECT_ID, TASK_ID, TASK, (), ("result.txt",))
        live = lambda bound, run_id, epoch: (
            bound is session and run_id == snapshot.run_id and epoch == snapshot.sequence
            and not bridge.cancel_event.is_set() and bridge.lifecycle.snapshot().state == "running"
        )

        client = LLMClient(load_config(), paths=paths)
        host = ProjectWorkerHost(lambda: client)
        reservation, = host.reserve_many(
            session, [request], parent_run_id=snapshot.run_id,
            turn_epoch=snapshot.sequence, live_binding_check=live,
        )
        binding = ProjectWorkerWorkRecordBinding(execution, request, reservation)
        assert _requirement_matches_bound_scope(store, store.get_item(item["item_id"]), binding)
        try:
            scope_approvals: list[str] = []
            outbound_approvals: list[str] = []
            write_approvals: list[str] = []

            def approve_scope(preview: str) -> str:
                doc = json.loads(preview)
                if (scope_approvals or doc.get("project_id") != PROJECT_ID
                        or doc.get("task_id") != TASK_ID or doc.get("write_targets") != ["result.txt"]
                        or doc.get("read_targets") != []):
                    return "n"
                scope_approvals.append("approved")
                return "y"

            def approve_outbound(preview: str) -> str:
                doc = json.loads(preview)
                payload = doc.get("provider_payload", {})
                messages = payload.get("messages", [])
                if (len(outbound_approvals) >= 2 or doc.get("model_id") != MODEL_ID or doc.get("vendor") != "0x567"
                        or doc.get("endpoint") != ENDPOINT or payload.get("model") != PROVIDER_MODEL
                        or not any(row.get("role") == "user" and row.get("content") == TASK for row in messages)
                        or "PARENT_PRIVATE_SENTINEL_NOT_FOR_WORKER" in json.dumps(messages, ensure_ascii=False)):
                    return "n"
                outbound_approvals.append("approved")
                return "y"

            def approve_write(preview: str, allow_all: bool) -> str:
                if allow_all or write_approvals or "result.txt" not in preview or PROJECT_ID not in preview:
                    return "n"
                write_approvals.append("approved")
                return "y"

            real_post = httpx.Client.post

            def guarded_post(http_client, url, *args, **kwargs):
                if len(posts) >= 2:
                    raise RuntimeError("live worker request budget exceeded")
                content = kwargs.get("content")
                try:
                    payload = json.loads(content.decode("utf-8")) if isinstance(content, bytes) else None
                except Exception:
                    payload = None
                headers = kwargs.get("headers") or {}
                messages = payload.get("messages", []) if type(payload) is dict else []
                encoded_messages = json.dumps(messages, ensure_ascii=False)
                if (len(outbound_approvals) != len(posts) + 1
                        or str(url) != ENDPOINT or kwargs.get("follow_redirects") is not False
                        or http_client.follow_redirects or type(payload) is not dict
                        or payload.get("model") != PROVIDER_MODEL
                        or headers.get("Content-Type") != "application/json"
                        or not str(headers.get("Authorization", "")).startswith("Bearer ")
                        or not any(row.get("role") == "user" and row.get("content") == TASK for row in messages)
                        or "PARENT_PRIVATE_SENTINEL_NOT_FOR_WORKER" in encoded_messages
                        or "F:\\my-agent-main" in encoded_messages or "C:\\Users\\" in encoded_messages):
                    raise RuntimeError("outbound live worker request rejected by test guard")
                posts.append(1)
                response = real_post(http_client, url, *args, **kwargs)
                if response.status_code < 200 or response.status_code >= 300:
                    raise RuntimeError("provider returned a non-success status")
                return response

            monkeypatch.setattr(httpx.Client, "post", guarded_post)
            try:
                outcomes = host.run_many(
                    session, [request], scope_confirm_fn=approve_scope,
                    outbound_request_confirm_fn=approve_outbound, write_confirm_fn=approve_write,
                    cancel_event=threading.Event(), run_id=snapshot.run_id,
                    turn_epoch=snapshot.sequence, live_binding_check=live,
                    max_requests=2, reservations=(reservation,),
                )
            except Exception:
                pytest.fail("live worker request failed; private provider data withheld", pytrace=False)
            assert len(outcomes) == 1
            outcome = outcomes[0]
            assert outcome.status == "model_turn_finished", f"worker status={outcome.status}, code={outcome.error_code}"
            assert outcome.model_turn is not None and outcome.model_turn.model_id == MODEL_ID
            assert outcome.model_turn.requests == len(posts) and 1 <= len(posts) <= 2
            assert outcome.model_turn.tool_calls >= 1
            assert len(scope_approvals) == 1
            assert len(outbound_approvals) == len(posts) == 2
            assert len(write_approvals) <= 1
            assert (project_root / "result.txt").read_text(encoding="utf-8") == RESULT_TEXT

            receipt = bridge.verify_project_worker_requirement(
                host=host, session=session, store=store, binding=binding, outcome=outcome,
                target=TARGET, live_binding_check=live,
            )
            assert receipt is not None
            assert (project_root / "pytest-ran.txt").read_text(encoding="utf-8") == "ran"
            assert bridge.finish_execution("completed", ok=True, run_id=snapshot.run_id)
            settled = settle_project_worker_work_record(
                store, binding, outcome, sequence=bridge.lifecycle.snapshot().sequence,
                verification_receipt=receipt, verification_host=host, verification_bridge=bridge,
            )
            assert settled.status == "recorded" and settled.reason == "project_self_reported_pass"
            row, = WorkRecordStore(session).get_item(item["item_id"])["runs"]
            assert row["status"] == "unknown"
            assert row["verification"]["status"] == "not_verified"
            assert row["verification"]["project_test"] == {
                "status": "passed", "source": "user_authorized_project_self_report",
                "pytest_target": TARGET, "exit_code": 0,
            }
            expected_new_files = {
                "result.txt",
                "pytest-ran.txt",
                ".pytest_cache/README.md",
                ".pytest_cache/.gitignore",
                ".pytest_cache/CACHEDIR.TAG",
                ".pytest_cache/v/cache/nodeids",
            }
            actual_files = {p.relative_to(project_root).as_posix() for p in project_root.rglob("*") if p.is_file()}
            assert actual_files == original_project_files | expected_new_files
            assert sum(e.get("confirm_subtype") == "project_worker_requirement_verification"
                       and e.get("type") == "confirm.request" for e in events) == 1
        finally:
            released = host.release_parent_run(
                conversation_id=session.conversation_id,
                parent_run_id=snapshot.run_id,
                turn_epoch=snapshot.sequence,
            )
        assert released == 1
        assert not host._reservations and not host._requirement_verifications


