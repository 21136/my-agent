from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_TESTS = Path(__file__).resolve().parent
_CORE = _TESTS.parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import work_records_luna_recall as luna
from work_records_semantic import SearchResult, WorkRecordCandidate


def candidate(record_id: str, *, session: str = "session-a", title: str | None = None, excerpt: str = "摘要"):
    return WorkRecordCandidate(record_id, title or f"标题 {record_id}", excerpt, session)


class FakeRegistry:
    def __init__(self, entry=None):
        self.entry = entry or SimpleNamespace(id="0x567-flash", vendor="0x567", provider_model="gpt-6-luna", max_input_tokens=1_000_000)

    def get(self, model_id):
        return self.entry if model_id == "0x567-flash" else None


def install_registry(monkeypatch, entry=None):
    monkeypatch.setattr(luna, "get_registry", lambda *_args: FakeRegistry(entry))


def response(status, ids):
    return SimpleNamespace(content=json.dumps({"status": status, "candidate_ids": ids}), tool_calls=[])


def test_directly_sends_complete_999_synthetic_candidates_in_one_read_only_call(monkeypatch):
    install_registry(monkeypatch)
    rows = [candidate(f"synthetic-{i:04d}", title=f"T{i}", excerpt=f"E{i}") for i in range(1, 1000)]
    calls = []

    class Client:
        def chat(self, messages, **kwargs):
            calls.append((messages, kwargs))
            payload = json.loads(messages[1]["content"])
            assert len(payload["candidates"]) == 999
            assert payload["candidates"][-1]["id"] == "synthetic-0999"
            return response("match", ["synthetic-0999"])

    result = luna.WorkRecordsLunaRecall(Client()).match("哪一条？", rows)
    assert isinstance(result, SearchResult)
    assert result.status == "match" and result.candidate_id == "synthetic-0999"
    assert result.candidate_ids == ("synthetic-0999",)
    messages, kwargs = calls[0]
    sent = json.loads(messages[1]["content"])
    assert set(sent) == {"question", "candidates"}
    assert all(set(row) == {"id", "title", "excerpt"} for row in sent["candidates"])
    assert "session_id" not in messages[1]["content"]
    assert kwargs == {"model": "0x567-flash", "tools": [], "temperature": 0.0, "response_format": {"type": "json_object"}}
    assert "every candidate" in messages[0]["content"].lower()


def test_nonempty_candidate_set_not_found_is_not_reported_as_a_negative(monkeypatch):
    install_registry(monkeypatch)
    client = SimpleNamespace(chat=lambda *_a, **_kw: response("not_found", []))
    result = luna.WorkRecordsLunaRecall(client).match("question", [candidate("r1")])
    assert result.status in {"uncertain", "incomplete"}
    assert result.candidate_id is None and result.candidate_ids == ()


def test_ambiguous_result_preserves_model_candidate_order(monkeypatch):
    install_registry(monkeypatch)
    client = SimpleNamespace(chat=lambda *_a, **_kw: response("ambiguous", ["r2", "r1"]))
    result = luna.WorkRecordsLunaRecall(client).match("question", [candidate("r1"), candidate("r2")])
    assert result.status == "ambiguous" and result.candidate_ids == ("r2", "r1")


def test_rejects_mixed_sessions_invalid_ids_and_duplicate_ids_without_sending(monkeypatch):
    install_registry(monkeypatch)
    calls = []
    client = SimpleNamespace(chat=lambda *a, **kw: calls.append((a, kw)))
    recognizer = luna.WorkRecordsLunaRecall(client)
    cases = (
        [candidate("r1"), candidate("r2", session="session-b")],
        [candidate("bad id")],
        [candidate("r1", session="bad session")],
        [candidate("r1"), candidate("r1")],
    )
    for rows in cases:
        assert recognizer.match("question", rows).status == "incomplete"
    assert calls == []


def test_over_budget_refuses_entire_set_without_truncation(monkeypatch):
    install_registry(monkeypatch)
    calls = []
    client = SimpleNamespace(chat=lambda *a, **kw: calls.append((a, kw)))
    recognizer = luna.WorkRecordsLunaRecall(client, max_input_tokens=2_000)
    rows = [candidate(f"r{i}", excerpt="x" * 500) for i in range(8)]
    result = recognizer.match("question", rows)
    assert result.status == "incomplete" and calls == []


def test_model_exception_and_invalid_json_fail_closed_without_error_leak(monkeypatch):
    install_registry(monkeypatch)
    class Broken:
        def chat(self, *_a, **_kw):
            raise RuntimeError("private provider diagnostic")
    result = luna.WorkRecordsLunaRecall(Broken()).match("question", [candidate("r1")])
    assert result.status == "error" and result.candidate_id is None
    assert "private provider diagnostic" not in repr(result)

    malformed = SimpleNamespace(chat=lambda *_a, **_kw: SimpleNamespace(content="not json", tool_calls=[]))
    result = luna.WorkRecordsLunaRecall(malformed).match("question", [candidate("r1")])
    assert result.status == "error" and result.candidate_ids == ()


def test_model_output_ids_must_be_unique_and_belong_to_input(monkeypatch):
    install_registry(monkeypatch)
    rows = [candidate("r1"), candidate("r2")]
    for answer in (response("match", ["invented"]), response("match", ["r1", "r1"]), response("match", []), response("ambiguous", ["r1"])):
        client = SimpleNamespace(chat=lambda *_a, _answer=answer, **_kw: _answer)
        result = luna.WorkRecordsLunaRecall(client).match("question", rows)
        assert result.status == "error" and result.candidate_id is None


def test_registry_must_resolve_to_expected_vendor_and_provider_model(monkeypatch):
    bad_entries = (
        SimpleNamespace(id="other", vendor="0x567", provider_model="gpt-6-luna", max_input_tokens=1_000_000),
        SimpleNamespace(id="0x567-flash", vendor="elsewhere", provider_model="gpt-6-luna", max_input_tokens=1_000_000),
        SimpleNamespace(id="0x567-flash", vendor="0x567", provider_model="different", max_input_tokens=1_000_000),
    )
    for entry in bad_entries:
        install_registry(monkeypatch, entry)
        calls = []
        result = luna.WorkRecordsLunaRecall(SimpleNamespace(chat=lambda *a, **kw: calls.append((a, kw)))).match("question", [candidate("r1")])
        assert result.status == "error" and calls == []


def test_default_budget_allows_complete_999_candidate_request(monkeypatch):
    install_registry(monkeypatch)
    calls = []
    client = SimpleNamespace(chat=lambda *a, **kw: calls.append((a, kw)) or response("match", ["synthetic-0999"]))
    recognizer = luna.WorkRecordsLunaRecall(client)
    rows = [candidate(f"synthetic-{i:04d}", title=f"T{i}", excerpt=f"E{i}" + "x" * 90) for i in range(1, 1000)]
    result = recognizer.match("question", rows)
    assert result.status == "match" and result.candidate_id == "synthetic-0999"
    assert len(calls) == 1
    assert len(json.loads(calls[0][0][0][1]["content"])["candidates"]) == 999






def test_explicit_200k_budget_still_allows_complete_999_candidate_request(monkeypatch):
    install_registry(monkeypatch)
    rows = [candidate(f"synthetic-{i:04d}", title=f"T{i}", excerpt=(f"E{i}" + "x" * 90)) for i in range(1, 1000)]
    calls = []
    client = SimpleNamespace(chat=lambda messages, **kwargs: calls.append((messages, kwargs)) or response("match", ["synthetic-0999"]))

    configured = luna.WorkRecordsLunaRecall(client, max_input_tokens=200_000).match("question", rows)
    assert configured.status == "match" and configured.candidate_id == "synthetic-0999"
    assert len(calls) == 1
    payload = json.loads(calls[0][0][1]["content"])
    assert len(payload["candidates"]) == 999
    assert len(json.dumps(calls[0][0], ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 96_000


def test_explicit_budget_is_still_capped_by_verified_provider_context(monkeypatch):
    limited = SimpleNamespace(id="0x567-flash", vendor="0x567", provider_model="gpt-6-luna", max_input_tokens=120_000)
    install_registry(monkeypatch, limited)
    rows = [candidate(f"synthetic-{i:04d}", excerpt="x" * 90) for i in range(1, 1000)]
    calls = []
    client = SimpleNamespace(chat=lambda *a, **kw: calls.append((a, kw)) or response("match", ["synthetic-0999"]))
    result = luna.WorkRecordsLunaRecall(client, max_input_tokens=200_000).match("question", rows)
    assert result.status == "incomplete" and calls == []


def test_empty_candidate_set_is_distinct_from_model_not_found(monkeypatch):
    install_registry(monkeypatch)
    calls = []
    client = SimpleNamespace(chat=lambda *a, **kw: calls.append((a, kw)))
    result = luna.WorkRecordsLunaRecall(client).match("question", [])
    assert result.status == "not_found" and result.candidate_ids == ()
    assert calls == []


LIVE_RECALL_OPT_IN = "MY_AGENT_LIVE_WORK_RECORD_LUNA_RECALL_999"


@pytest.mark.skipif(
    os.environ.get(LIVE_RECALL_OPT_IN) != "1",
    reason=f"set {LIVE_RECALL_OPT_IN}=1 to send one synthetic-only request to 0x567 GPT-6 Luna",
)
def test_opt_in_live_luna_recall_matches_full_999_synthetic_candidates():
    """One explicit live call; query and all candidates are fixed synthetic fixtures."""
    from llm_models import get_registry
    from test_work_records_luna_999 import (
        CANDIDATES,
        EXPECTED_PROVIDER_MODEL,
        MODEL_ID,
        _live_client,
        luna_no_overlap_case,
        validate_model_entry,
    )

    client = _live_client()
    entry = get_registry(client.paths).get(MODEL_ID)
    validate_model_entry(entry)
    assert entry.provider_model == "gpt-6-luna" == EXPECTED_PROVIDER_MODEL
    case = luna_no_overlap_case()
    calls = []

    class RecordingClient:
        paths = client.paths

        def chat(self, messages, **kwargs):
            calls.append((messages, kwargs))
            return client.chat(messages, **kwargs)

    recognizer = luna.WorkRecordsLunaRecall(RecordingClient(), max_input_tokens=200_000)
    started = time.perf_counter()
    result = recognizer.match(case.question, CANDIDATES)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert len(calls) == 1
    messages, kwargs = calls[0]
    assert kwargs["model"] == "0x567-flash" == MODEL_ID
    assert kwargs["tools"] == []
    sent = json.loads(messages[1]["content"])
    assert sent["question"] == case.question
    assert len(sent["candidates"]) == 999
    assert {row["id"] for row in sent["candidates"]} == {row.record_id for row in CANDIDATES}
    assert all(set(row) == {"id", "title", "excerpt"} for row in sent["candidates"])
    assert "session_id" not in messages[1]["content"]
    assert result.status == "match" and result.candidate_id == case.expected_ids[0]

    request_bytes = len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    print(
        f"live_luna_recall provider={entry.provider_model} model_id={MODEL_ID} candidates=999 "
        f"request_bytes={request_bytes} elapsed_ms={elapsed_ms:.1f} result={result.status}:{result.candidate_id} "
        "synthetic_only=true tools=[] requests=1"
    )
