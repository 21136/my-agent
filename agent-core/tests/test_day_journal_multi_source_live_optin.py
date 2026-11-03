from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from llm_client import LLMClient, load_config
from llm_models import get_registry
from paths import AgentPaths

_LIVE_OPT_IN = "MY_AGENT_LIVE_DAY_JOURNAL_MULTI_SOURCE_OPTIN"
_MODEL_ID = "0x567-flash"
_EXPECTED_VENDOR = "0x567"
_EXPECTED_PROVIDER_MODEL = "gpt-6-luna"
_PROJECT_ID = "synthetic-project"
_REQUIREMENTS = (
    "合成需求甲：虚构组件云杉的校验甲已通过，尚未发布。内容完全虚构。",
    "合成需求乙：虚构组件白鹭的校验乙待复核，不代表已经完成。内容完全虚构。",
)


@pytest.mark.skipif(
    os.environ.get(_LIVE_OPT_IN) != "1",
    reason=f"set {_LIVE_OPT_IN}=1 to permit one synthetic-only 0x567 Luna request",
)
def test_live_multi_source_project_day_journal_summary_is_appended_unverified(tmp_path):
    """Use a temporary synthetic session/day and require both sources in the real request."""
    from day_journal import DayJournal
    from server import WsSessionHandler
    from work_records import WorkRecordStore

    test_dir = str(Path(__file__).resolve().parent)
    if test_dir not in sys.path:
        sys.path.insert(0, test_dir)
    from test_day_journal_summary_server_integration import _Bridge, _seed_session_boundary, _synthetic_session

    provider_paths = AgentPaths.discover(start=Path(__file__).resolve())
    entry = get_registry(provider_paths).get(_MODEL_ID)
    assert entry is not None, "required registry ID is unavailable"
    assert (entry.id, entry.vendor, entry.provider_model) == (
        _MODEL_ID, _EXPECTED_VENDOR, _EXPECTED_PROVIDER_MODEL,
    ), "configured registry entry is not the required 0x567 GPT-6 Luna"
    assert urlparse(entry.base_url).hostname == "api-cdn.0x567.com", "refusing non-canonical provider endpoint"
    # Resolve only for readiness and leak checks; never print, persist, or serialize the credential.
    api_key = entry.resolve_api_key(provider_paths)
    if not api_key:
        pytest.skip("0x567 credential unavailable; no provider request made")

    synthetic_paths, session = _synthetic_session(tmp_path)
    assert session.meta.project_id == _PROJECT_ID
    assert session.conversation_id == "synthetic-session"
    instant = datetime.now(timezone(timedelta(hours=8), "Asia/Shanghai"))
    journal = DayJournal(tmp_path / "isolated-synthetic-journal")
    journal.save_day(instant, None, [_PROJECT_ID])
    _seed_session_boundary(journal, instant, session_id=session.conversation_id, project_id=_PROJECT_ID)
    store = WorkRecordStore(session)
    expected_sources: list[tuple[str, str]] = []
    for index, requirement_text in enumerate(_REQUIREMENTS, start=1):
        item = store.create_item(
            title=f"完全合成的多来源事项${index}",
            scope={"kind": "project", "project_id": _PROJECT_ID},
        )
        event_id = f"synthetic-multi-source-requirement-{index}"
        store.add_requirement(item["item_id"], requirement_text, event_id=event_id)
        expected_sources.append((f"work-record:{item['item_id']}:{event_id}", requirement_text))

    request_evidence: dict[str, object] = {
        "count": 0, "model": None, "tools": None, "format": None, "elapsed_ms": None,
        "source_ids": [], "synthetic_texts_present": False,
    }

    class GuardedRealClient:
        """Forward one call only after checking that every prompt source is synthetic."""
        def __init__(self, delegate: LLMClient) -> None:
            self._delegate = delegate
            self.paths = provider_paths

        def chat(self, messages, **kwargs):
            request_evidence["count"] = int(request_evidence["count"]) + 1
            request_evidence["model"] = kwargs.get("model")
            request_evidence["tools"] = kwargs.get("tools")
            request_evidence["format"] = kwargs.get("response_format")
            serialized = json.dumps(messages, ensure_ascii=False)
            request_payload = json.loads(messages[-1]["content"])
            sources = request_payload.get("sources")
            expected_ids = [source_id for source_id, _ in expected_sources]
            if (request_evidence["count"] != 1
                    or not isinstance(sources, list)
                    or {source.get("source_id") for source in sources} != set(expected_ids)
                    or any(text not in serialized for _, text in expected_sources)
                    or str(provider_paths.agent_root) in serialized
                    or str(synthetic_paths.agent_root) in serialized
                    or api_key in serialized):
                raise AssertionError("refusing unexpected, non-synthetic, or secret-bearing provider request")
            request_evidence["source_ids"] = [source["source_id"] for source in sources]
            request_evidence["synthetic_texts_present"] = True
            started = time.perf_counter()
            try:
                return self._delegate.chat(messages, **kwargs)
            finally:
                request_evidence["elapsed_ms"] = (time.perf_counter() - started) * 1000

    def service_factory(*, client, confirm, current_context_token):
        assert isinstance(client, LLMClient)
        from day_journal_summary import DayJournalSummaryService
        return DayJournalSummaryService(
            client=GuardedRealClient(client), confirm=confirm,
            current_context_token=current_context_token,
        )

    bridge = _Bridge(choices=("y", "y"))
    handler = WsSessionHandler.__new__(WsSessionHandler)
    handler.paths = provider_paths
    handler.project_boundary_journal = journal
    handler.day_journal_summary_service_factory = service_factory
    before = journal.read_day(instant.date().isoformat())
    assert before is not None
    original_segments = list(before["segments"])

    asyncio.run(handler._handle_day_journal_summary(
        "总结今天的项目日记", SimpleNamespace(session=session), bridge,
    ))

    assert request_evidence["count"] == 1, "must issue exactly one real model request"
    assert request_evidence["model"] == _MODEL_ID
    assert request_evidence["tools"] == []
    assert request_evidence["format"] == {"type": "json_object"}
    assert set(request_evidence["source_ids"]) == {source_id for source_id, _ in expected_sources}
    assert request_evidence["synthetic_texts_present"] is True
    assert len(bridge.previews) == 2, "outbound and save authorization must be separately simulated"
    assert all(all(source_id in preview for source_id, _ in expected_sources) for preview in bridge.previews)
    assert all(text in bridge.previews[0] for _, text in expected_sources), "only the outbound preview should include full source text"
    assert all(text not in bridge.previews[1] for _, text in expected_sources), "save preview must not persist source text"
    assert all(str(provider_paths.agent_root) not in preview for preview in bridge.previews)
    assert all(str(synthetic_paths.agent_root) not in preview for preview in bridge.previews)
    assert all(api_key not in preview for preview in bridge.previews), "secret leaked into confirmation preview"

    saved = journal.read_day(instant.date().isoformat())
    assert saved is not None
    assert saved["segments"][:len(original_segments)] == original_segments, "summary must append, not replace"
    assert len(saved["segments"]) == len(original_segments) + 1
    summary_segment = saved["segments"][-1]
    assert summary_segment.get("summary_metadata", {}).get("kind") == "model_summary_candidate"
    assert isinstance(summary_segment.get("summary"), str) and summary_segment["summary"].strip()
    assert summary_segment["summary_metadata"]["source_ids"] == [
        source_id for source_id, _ in expected_sources
    ]
    assert summary_segment["summary_metadata"]["verified"] is False



