from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import companion_world_context
import llm_client
import project_api
import server
import work_records_service
from llm_client import LLMClient, load_config
from llm_models import get_registry
from paths import AgentPaths
from session import Session, SessionMeta
from server import WsBridge, WsSessionHandler
from work_records import WorkRecordStore
from work_records_service import WorkRecordCaptureService
from work_record_settlement import BoundExecution, ExecutionReceipt, WorkRecordSettlementAdapter
from work_records_luna_intent import EXPECTED_PROVIDER_MODEL, EXPECTED_VENDOR, MODEL_ID


LIVE_OPT_IN = "MY_AGENT_LIVE_CAPTURE_RUN_CAS_OPTIN"
SYNTHETIC_TEXT = "\u8bf7\u8bb0\u5f55\u4e00\u4e2a\u72ec\u7acb\u4e8b\u9879\uff1a\u5408\u6210\u5929\u6587\u7ad9 Kestrel-741 \u9700\u4ea4\u4ed8\u4e00\u4efd\u5305\u542b\u4e24\u6761\u865a\u6784\u89c2\u6d4b\u7684\u62a5\u544a\uff0c\u9a8c\u6536\u6807\u51c6\u662f\u62a5\u544a\u4e2d\u5305\u542b\u4e24\u4e2a\u89c2\u5bdf\u6761\u76ee\u3002\u672c\u6d88\u606f\u4ec5\u7528\u4e8e\u5408\u6210\u6d4b\u8bd5\uff0c\u5c1a\u672a\u6267\u884c\u6216\u9a8c\u8bc1\u3002"


def _safe_provider_response_summary(response: Any) -> dict[str, Any]:
    """Summarize classifier response shape only; never retain content or field values."""
    content = response if isinstance(response, str) else getattr(response, "content", None)
    raw_calls = getattr(response, "tool_calls", None)
    tool_call_count = len(raw_calls) if isinstance(raw_calls, list) else (0 if raw_calls is None else -1)
    safe: dict[str, Any] = {
        "response_type": type(response).__name__,
        "content_present": isinstance(content, str) and bool(content),
        "tool_call_count": tool_call_count,
        "content_format": "missing" if not isinstance(content, str) else "invalid_json",
        "schema": "unavailable",
        "action": "unavailable",
        "kind": "unavailable",
        "scope": "unavailable",
        "title_present": False,
        "requirement_present": False,
        "item_id_shape": "unavailable",
    }
    if not isinstance(content, str):
        return safe
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return safe
    if not isinstance(payload, dict):
        safe["content_format"] = "json_non_object"
        return safe
    safe["content_format"] = "json_object"
    keys = set(payload)
    legacy = {"action", "kind", "title", "requirement", "scope"}
    current = legacy | {"item_id"}
    safe["schema"] = "legacy" if keys == legacy else "current" if keys == current else "unexpected"
    raw_action = payload.get("action")
    safe["action"] = raw_action if isinstance(raw_action, str) and raw_action in {"record", "none", "recall", "uncertain"} else "other"
    raw_kind = payload.get("kind")
    safe["kind"] = raw_kind if isinstance(raw_kind, str) and raw_kind in {"requirement", "qa", "recall", "ambiguous", "execution"} else "other"
    raw_scope = payload.get("scope")
    safe["scope"] = raw_scope if raw_scope is None or (isinstance(raw_scope, str) and raw_scope in {"independent", "current_project"}) else "other"
    safe["title_present"] = isinstance(payload.get("title"), str) and bool(payload["title"].strip())
    safe["requirement_present"] = isinstance(payload.get("requirement"), str) and bool(payload["requirement"].strip())
    raw_item_id = payload.get("item_id", "missing")
    safe["item_id_shape"] = "missing" if raw_item_id == "missing" else "null" if raw_item_id is None else "string" if isinstance(raw_item_id, str) else "other"
    return safe


def _temp_session(root: Path, session_id: str) -> Session:
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    session_dir = paths.data / "sessions" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    paths.workspace.mkdir(parents=True, exist_ok=True)
    paths.evolve.mkdir(parents=True, exist_ok=True)
    session = Session(session_id, session_dir, "", SessionMeta(), [], paths)
    session.save()
    return session


class _OneCallClient:
    """Observe one real LLMClient call without retaining/printing secrets."""

    def __init__(self, delegate: LLMClient) -> None:
        self._delegate = delegate
        self.paths = delegate.paths
        self.calls = 0
        self.models: list[str | None] = []
        self.diagnostics: dict[str, Any] = {
            "call_entered": False, "provider_returned": False,
            "request_model_matches": False, "tools_empty": False,
            "synthetic_marker_present": False, "secret_absent": False,
            "response": None, "exception_type": None, "http_status": None,
        }

    def set_cancel_event(self, event) -> None:
        self._delegate.set_cancel_event(event)

    def chat(self, messages, **kwargs):
        self.calls += 1
        self.models.append(kwargs.get("model"))
        serialized = json.dumps(messages, ensure_ascii=False)
        entry = get_registry(self.paths).get(MODEL_ID)
        secret = entry.resolve_api_key(self.paths) if entry is not None else None
        self.diagnostics.update({
            "call_entered": True,
            "request_model_matches": kwargs.get("model") == MODEL_ID,
            "tools_empty": kwargs.get("tools") == [],
            "synthetic_marker_present": "Kestrel-741" in serialized,
            "secret_absent": not secret or secret not in serialized,
        })
        if not self.diagnostics["request_model_matches"] or not self.diagnostics["tools_empty"]:
            raise AssertionError("unexpected classifier request envelope")
        try:
            response = self._delegate.chat(messages, **kwargs)
        except Exception as exc:
            self.diagnostics["exception_type"] = type(exc).__name__
            status_code = getattr(exc, "status_code", None)
            self.diagnostics["http_status"] = status_code if isinstance(status_code, int) else None
            raise
        self.diagnostics["provider_returned"] = True
        self.diagnostics["response"] = _safe_provider_response_summary(response)
        return response


@pytest.mark.skipif(
    os.environ.get(LIVE_OPT_IN) != "1",
    reason=f"set {LIVE_OPT_IN}=1 to permit one synthetic-only 0x567 capture classification call",
)
def test_live_capture_same_message_run_cas_persists_unknown_and_refusal_stays_unwritten(tmp_path, monkeypatch):
    provider_paths = AgentPaths.discover(start=_CORE)
    entry = get_registry(provider_paths).get(MODEL_ID)
    assert entry is not None
    assert (entry.id, entry.vendor, entry.provider_model) == (
        MODEL_ID, EXPECTED_VENDOR, EXPECTED_PROVIDER_MODEL,
    )
    api_key = entry.resolve_api_key(provider_paths)
    if not api_key:
        pytest.skip("0x567 credential unavailable; no request made")

    delegate = LLMClient(load_config(), paths=provider_paths)
    live_client = _OneCallClient(delegate)
    live_session = _temp_session(tmp_path / "accepted", "synthetic-live-capture-session")
    confirmation_stages: list[str] = []
    capture_results: list[Any] = []
    user_request_id = str(uuid.uuid4())

    def approve_both(preview: str, allow_approve_all: bool = False):
        assert allow_approve_all is False
        if preview.startswith("\u5de5\u4f5c\u8bb0\u5f55\u5355\u6b21\u5916\u53d1\u786e\u8ba4"):
            confirmation_stages.append("outbound")
        elif preview.startswith("\u5de5\u4f5c\u8bb0\u5f55\u672c\u5730\u843d\u76d8\u786e\u8ba4"):
            confirmation_stages.append("storage")
        else:
            confirmation_stages.append("unrecognized")
        return "yes", str(uuid.uuid4())

    bridge = WsBridge(emit=lambda _event: None, paths=provider_paths)
    monkeypatch.setattr(bridge, "confirm_with_receipt", approve_both)

    class CaptureProbe:
        def __init__(self, *, session, client, confirm):
            self._delegate = WorkRecordCaptureService(session=session, client=client, confirm=confirm)

        def capture(self, text, *, request_id=None, track_run=False):
            assert track_run is True
            result = self._delegate.capture(text, request_id=request_id, track_run=track_run)
            capture_results.append(result)
            return result

    monkeypatch.setattr(work_records_service, "WorkRecordCaptureService", CaptureProbe)
    monkeypatch.setattr(llm_client, "LLMClient", lambda **_kwargs: live_client)
    monkeypatch.setattr(server, "session_memory_event", lambda *_args: {"type": "session.memory"})
    monkeypatch.setattr(server, "_proposals_payload", lambda *_args: {"type": "evolve.proposals", "items": []})
    monkeypatch.setattr(server, "_memories_payload", lambda *_args: {"type": "evolve.memories", "items": []})
    monkeypatch.setattr(project_api, "after_turn_project_hooks", lambda *_args: None)
    monkeypatch.setattr(companion_world_context, "is_companion_world_context_allowed", lambda **_kwargs: False)

    executed: list[str] = []
    repl = SimpleNamespace(
        session=live_session,
        last_turn_finish_reason=None,
        agent=SimpleNamespace(
            bind_host_run_context=None,
            executor=SimpleNamespace(session=SimpleNamespace(evolution_authorization_id="")),
        ),
        handle_line=lambda line: executed.append(line) or "continue",
    )
    handler = object.__new__(WsSessionHandler)
    handler.paths = provider_paths
    handler._file_stage = SimpleNamespace(take=lambda *_args: [])

    asyncio.run(handler._dispatch(
        {"type": "user.message", "text": SYNTHETIC_TEXT, "request_id": user_request_id},
        repl,
        bridge,
    ))

    assert live_client.calls == 1
    assert live_client.models == [MODEL_ID]
    assert len(capture_results) == 1
    if capture_results[0].status != "recorded":
        safe_state = json.dumps({
            "capture_status": capture_results[0].status,
            "provider": live_client.diagnostics,
            "confirmation_stages": confirmation_stages,
        }, sort_keys=True)
        pytest.fail(f"capture did not reach recorded; sanitized state={safe_state}")
    assert live_client.diagnostics["provider_returned"] is True
    assert live_client.diagnostics["response"]["schema"] in {"legacy", "current"}
    assert live_client.diagnostics["response"]["action"] == "record"
    assert live_client.diagnostics["response"]["kind"] == "requirement"
    assert confirmation_stages == ["outbound", "storage"]
    assert len(capture_results) == 1
    capture = capture_results[0]
    assert capture.status == "recorded"
    assert isinstance(capture.item_id, str) and capture.item_id
    assert isinstance(capture.requirement_id, str) and capture.requirement_id
    assert executed and len(executed) == 1

    terminal = bridge.lifecycle.snapshot()
    assert terminal is not None and terminal.state == "settled"
    assert terminal.finish_reason == "completed"
    assert bridge.lifecycle_conversation_id == live_session.conversation_id

    reopened_session = Session(
        live_session.conversation_id, live_session.session_dir, "", SessionMeta(), [], live_session.paths,
    )
    reopened_store = WorkRecordStore(reopened_session)
    item = reopened_store.get_item(capture.item_id)
    assert item is not None
    requirement = next(row for row in item["requirements"] if row["requirement_id"] == capture.requirement_id)
    assert requirement["current_run_id"] == terminal.run_id
    assert requirement["status"] == "unknown"
    assert len(item["runs"]) == 1
    persisted_run = item["runs"][0]
    assert persisted_run["requirement_id"] == capture.requirement_id
    assert persisted_run["run_id"] == terminal.run_id
    assert persisted_run["status"] == "unknown"
    verification = persisted_run["verification"]
    assert verification["status"] == "not_verified"
    assert verification["evidence_refs"] == []
    assert set(verification) == {"status", "evidence_refs", "host_execution"}
    host_execution = verification["host_execution"]
    assert set(host_execution) == {"sequence", "finish_reason"}
    assert type(host_execution["sequence"]) is int and host_execution["sequence"] >= 1
    assert host_execution["finish_reason"] == "completed"
    assert requirement["status"] == "unknown"
    assert persisted_run["status"] == "unknown"
    assert any(
        event.get("event_id") == user_request_id
        and event.get("kind") == "requirement_added"
        and event.get("payload", {}).get("requirement_id") == capture.requirement_id
        for event in item["history"]
    )

    # A separately declined synthetic capture makes no provider call and persists no item.
    refused_session = _temp_session(tmp_path / "refused", "synthetic-refused-capture-session")

    class NeverCallClient:
        paths = provider_paths
        def chat(self, *_args, **_kwargs):
            raise AssertionError("refused capture must not call the provider")

    refused = WorkRecordCaptureService(
        session=refused_session,
        client=NeverCallClient(),
        confirm=lambda _preview: ("no", str(uuid.uuid4())),
    ).capture(SYNTHETIC_TEXT, request_id=str(uuid.uuid4()), track_run=True)
    assert refused.status == "not_authorized"
    assert refused.requirement_id is None
    assert WorkRecordStore(refused_session).list_items() == []

    # A failed synthetic execution receipt is also persisted as unknown, never completed.
    failed_session = _temp_session(tmp_path / "failed", "synthetic-failed-run-session")
    failed_store = WorkRecordStore(failed_session)
    failed_item = failed_store.create_item(title="Synthetic failed run", scope={"kind": "independent"})
    failed_requirement = failed_store.add_requirement(
        failed_item["item_id"], "Synthetic failing execution", event_id="synthetic-failed-requirement",
    )
    failed_run_id = "synthetic-failed-run"
    failed_store.begin_requirement_run(
        failed_item["item_id"], failed_requirement["requirement_id"],
        run_id=failed_run_id, expected_current_run_id=None,
    )
    failed_binding = BoundExecution(
        failed_session.conversation_id, failed_item["item_id"],
        failed_requirement["requirement_id"], failed_run_id,
    )
    failed_receipt = ExecutionReceipt(
        failed_session.conversation_id, failed_item["item_id"],
        failed_requirement["requirement_id"], failed_run_id, 1, "exception",
    )
    failed_settlement = WorkRecordSettlementAdapter(failed_store, failed_binding).settle(failed_receipt)
    assert failed_settlement.status == "recorded"
    failed_reopened = WorkRecordStore(Session(
        failed_session.conversation_id, failed_session.session_dir, "", SessionMeta(), [], failed_session.paths,
    )).get_item(failed_item["item_id"])
    assert failed_reopened["requirements"][0]["status"] == "unknown"
    assert failed_reopened["runs"][0]["status"] == "unknown"
    assert failed_reopened["runs"][0]["status"] != "completed"



def test_provider_diagnostic_summary_keeps_classification_shape_only():
    sentinel = "SYNTHETIC_PRIVATE_RESPONSE_MUST_NOT_APPEAR"
    fake = SimpleNamespace(
        content=json.dumps({
            "action": "uncertain", "kind": "ambiguous", "title": sentinel,
            "requirement": sentinel, "scope": None, "item_id": None,
        }),
        tool_calls=[],
    )

    safe = _safe_provider_response_summary(fake)

    assert safe["content_format"] == "json_object"
    assert safe["schema"] == "current"
    assert safe["action"] == "uncertain"
    assert safe["kind"] == "ambiguous"
    assert safe["title_present"] is True
    assert safe["requirement_present"] is True
    assert sentinel not in repr(safe)
