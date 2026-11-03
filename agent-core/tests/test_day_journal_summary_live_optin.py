from __future__ import annotations

import json
import os
import sys
import time
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path
from typing import Any

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from day_journal_summary import DayJournalSummaryService, SummaryError
from llm_client import LLMClient, load_config
from llm_models import get_registry
from paths import AgentPaths


_LIVE_OPT_IN = "MY_AGENT_LIVE_DAY_JOURNAL_SUMMARY_OPTIN"
_MODEL_ID = "0x567-flash"
_EXPECTED_VENDOR = "0x567"
_EXPECTED_PROVIDER_MODEL = "gpt-6-luna"
_SYNTHETIC_SOURCE_ID = "synthetic:journal-summary:sample-1"
_SYNTHETIC_EXCERPT = (
    "合成测试记录：样本-7在虚构环境完成校验A，结果为通过；校验B仍待复核。"
    "内容完全虚构，不对应真实项目、用户或会话。"
)


class _TimedLLMClient:
    """Measure one LLMClient call while retaining only synthetic request/response evidence."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client
        self.paths = client.paths
        self.call_count = 0
        self.elapsed_ms: float | None = None
        self.response_text: str | None = None
        self.request_model: str | None = None
        self.request_tools: Any = None
        self.request_response_format: Any = None

    def chat(self, messages, **kwargs):
        self.call_count += 1
        self.request_model = kwargs.get("model")
        self.request_tools = kwargs.get("tools")
        self.request_response_format = kwargs.get("response_format")
        started = time.perf_counter()
        try:
            response = self._client.chat(messages, **kwargs)
            self.response_text = getattr(response, "content", None)
            return response
        finally:
            self.elapsed_ms = (time.perf_counter() - started) * 1000


@pytest.mark.skipif(
    os.environ.get(_LIVE_OPT_IN) != "1",
    reason=f"set {_LIVE_OPT_IN}=1 to permit one synthetic-only 0x567 Luna request",
)
def test_live_day_journal_summary_one_synthetic_request():
    paths = AgentPaths.discover(start=Path(__file__).resolve())
    entry = get_registry(paths).get(_MODEL_ID)
    assert entry is not None, "required registry ID 0x567-flash is unavailable"
    assert (entry.id, entry.vendor, entry.provider_model) == (
        _MODEL_ID, _EXPECTED_VENDOR, _EXPECTED_PROVIDER_MODEL,
    ), "registry provider does not match required 0x567/gpt-6-luna"
    # Resolve only to check presence; never print, store, or serialize the credential.
    if not entry.resolve_api_key(paths):
        pytest.skip("0x567 credential unavailable; no provider request made")

    client = _TimedLLMClient(LLMClient(load_config(), paths=paths))
    previews: list[dict[str, Any]] = []

    def confirm(preview: dict[str, Any]) -> bool:
        # This is the user-authorized synthetic test confirmation: validate the exact
        # provider identity and request locally; never print or log secret material.
        assert preview["registry_id"] == _MODEL_ID
        assert preview["vendor"] == _EXPECTED_VENDOR
        assert preview["provider_model"] == _EXPECTED_PROVIDER_MODEL
        assert preview["tools"] == []
        assert preview["response_format"] == {"type": "json_object"}
        serialized = json.dumps(preview["messages"], ensure_ascii=False)
        assert _SYNTHETIC_SOURCE_ID in serialized
        assert _SYNTHETIC_EXCERPT in serialized
        assert str(paths.agent_root) not in serialized
        previews.append(preview)
        return True

    service = DayJournalSummaryService(
        client=client,
        confirm=confirm,
        current_context_token=lambda: "synthetic-context-revision-1",
    )
    result: dict[str, Any] | None = None
    failure: str | None = None
    try:
        result = service.generate(
            sources=[{"source_id": _SYNTHETIC_SOURCE_ID, "text": _SYNTHETIC_EXCERPT}],
            context_token="synthetic-context-revision-1",
        )
    except SummaryError as exc:
        # SummaryError contains only safe, fixed diagnostics from this service.
        failure = str(exc)
    finally:
        evidence = {
            "provider": f"{_EXPECTED_VENDOR}/{_EXPECTED_PROVIDER_MODEL}",
            "registry_id": _MODEL_ID,
            "requests": client.call_count,
            "elapsed_ms": round(client.elapsed_ms, 1) if client.elapsed_ms is not None else None,
            "response_format": client.request_response_format,
            "tools": client.request_tools,
            "summary_result": result,
            "raw_synthetic_response": client.response_text,
            "safe_failure": failure,
            "synthetic_only": True,
        }
        print("DAY_JOURNAL_LIVE_RESULT=" + json.dumps(evidence, ensure_ascii=False, separators=(",", ":")))

    assert client.call_count == 1, "live test must make exactly one provider request"
    assert len(previews) == 1, "exact request must be confirmed once"
    assert client.request_model == _MODEL_ID
    assert client.request_tools == []
    assert client.request_response_format == {"type": "json_object"}
    assert result is not None, f"summary generation failed: {failure or 'unknown safe failure'}"
    assert result["status"] == "candidate", "model did not return a qualified summary candidate"
    assert result["sources"] == [_SYNTHETIC_SOURCE_ID]
    summary = result["summary"]
    assert "样本-7" in summary and "校验A" in summary and "通过" in summary
    assert "校验B" in summary and "待复核" in summary
    assert result["verified"] is False


_LIVE_E2E_OPT_IN = "MY_AGENT_LIVE_DAY_JOURNAL_SUMMARY_E2E_OPTIN"
_LIVE_E2E_REQUIREMENT = (
    "合成端到端样本：虚构模块雪松的校验甲已通过，校验乙仍待复核；"
    "内容完全虚构，不对应真实用户、项目、session 或工作历史。"
)


@pytest.mark.skipif(
    os.environ.get(_LIVE_E2E_OPT_IN) != "1",
    reason=f"set {_LIVE_E2E_OPT_IN}=1 to permit exactly one synthetic-only 0x567 Luna E2E request",
)
def test_live_day_journal_summary_real_single_session_end_to_end(tmp_path):
    """Exercise the actual server path with only a temporary synthetic Session and journal."""
    from urllib.parse import urlparse
    import asyncio

    from day_journal import DayJournal
    from day_journal_summary import DayJournalSummaryService
    from server import WsSessionHandler
    test_dir = str(Path(__file__).resolve().parent)
    if test_dir not in sys.path:
        sys.path.insert(0, test_dir)
    from test_day_journal_summary_server_integration import (
        _Bridge, _seed_session_boundary, _synthetic_session,
    )
    from work_records import WorkRecordStore

    provider_paths = AgentPaths.discover(start=Path(__file__).resolve())
    entry = get_registry(provider_paths).get(_MODEL_ID)
    assert entry is not None, "required registry ID is unavailable"
    assert (entry.id, entry.vendor, entry.provider_model) == (
        _MODEL_ID, _EXPECTED_VENDOR, _EXPECTED_PROVIDER_MODEL,
    ), "configured registry entry is not the required 0x567 GPT-6 Luna"
    assert urlparse(entry.base_url).hostname == "api-cdn.0x567.com", "refusing non-canonical 0x567 endpoint"
    # Resolve credentials only to establish readiness; never print, persist, or put the value in a preview.
    if not entry.resolve_api_key(provider_paths):
        pytest.skip("0x567 credential unavailable; no provider request made")

    synthetic_paths, session = _synthetic_session(tmp_path)
    instant = datetime.now(timezone(timedelta(hours=8)))
    journal = DayJournal(tmp_path / "isolated-synthetic-journal")
    journal.save_day(instant, None, ["synthetic-project"])
    _seed_session_boundary(journal, instant)
    store = WorkRecordStore(session)
    item = store.create_item(
        title="完全合成的端到端日记事项",
        scope={"kind": "project", "project_id": "synthetic-project"},
    )
    store.add_requirement(item["item_id"], _LIVE_E2E_REQUIREMENT, event_id="synthetic-e2e-requirement-event")

    call_evidence: dict[str, Any] = {"count": 0, "model": None, "tools": None, "format": None,
                                     "elapsed_ms": None}

    class GuardedRealClient:
        """Allow one exact real call only after proving its entire prompt is synthetic."""
        def __init__(self, delegate):
            self._delegate = delegate
            self.paths = provider_paths

        def chat(self, messages, **kwargs):
            call_evidence["count"] += 1
            call_evidence["model"] = kwargs.get("model")
            call_evidence["tools"] = kwargs.get("tools")
            call_evidence["format"] = kwargs.get("response_format")
            serialized = json.dumps(messages, ensure_ascii=False)
            expected_source_id = f"work-record:{item['item_id']}:synthetic-e2e-requirement-event"
            if (call_evidence["count"] != 1
                    or expected_source_id not in serialized
                    or _LIVE_E2E_REQUIREMENT not in serialized
                    or str(provider_paths.agent_root) in serialized
                    or str(synthetic_paths.agent_root) in serialized):
                raise AssertionError("refusing an unexpected or non-synthetic provider request")
            started = time.perf_counter()
            try:
                return self._delegate.chat(messages, **kwargs)
            finally:
                call_evidence["elapsed_ms"] = (time.perf_counter() - started) * 1000

    def service_factory(*, client, confirm, current_context_token):
        assert isinstance(client, LLMClient)
        service = DayJournalSummaryService(
            client=GuardedRealClient(client), confirm=confirm,
            current_context_token=current_context_token,
        )
        return service

    bridge = _Bridge(choices=("y", "y"))
    handler = WsSessionHandler.__new__(WsSessionHandler)
    # Real provider configuration is used only by LLMClient. All session, store, and journal data is temporary.
    handler.paths = provider_paths
    handler.project_boundary_journal = journal
    handler.day_journal_summary_service_factory = service_factory
    repl = SimpleNamespace(session=session)

    asyncio.run(handler._handle_day_journal_summary("总结今天的项目日记", repl, bridge))

    assert call_evidence["count"] == 1, "must issue exactly one real model request"
    assert call_evidence["model"] == _MODEL_ID
    assert call_evidence["tools"] == []
    assert call_evidence["format"] == {"type": "json_object"}
    assert len(bridge.previews) == 2, "outbound and save authorization must be separately confirmed"
    assert all(_LIVE_E2E_REQUIREMENT in preview for preview in bridge.previews[:1])
    assert all(str(provider_paths.agent_root) not in preview for preview in bridge.previews)
    assert all(str(synthetic_paths.agent_root) not in preview for preview in bridge.previews)
    def preview_contains_secret(preview: str) -> bool:
        resolved = entry.resolve_api_key(provider_paths)
        return bool(resolved and resolved in preview)
    assert not any(preview_contains_secret(preview) for preview in bridge.previews), "secret leaked into a confirmation card"

    saved = journal.read_day(instant.date().isoformat())
    assert saved is not None
    summary_segment = next(
        segment for segment in saved["segments"]
        if segment.get("summary_metadata", {}).get("kind") == "model_summary_candidate"
    )
    assert isinstance(summary_segment["summary"], str) and summary_segment["summary"].strip()
    assert summary_segment["summary_metadata"]["verified"] is False
    assert summary_segment["summary_metadata"]["source_ids"] == [
        f"work-record:{item['item_id']}:synthetic-e2e-requirement-event"
    ]
    assert "未经核实" in bridge.responses[0]
    assert _LIVE_E2E_REQUIREMENT in bridge.responses[0]
