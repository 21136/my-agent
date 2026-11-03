"""Opt-in direct GPT-6 Luna recognition over the complete synthetic 999-record corpus.

Default tests are offline/fake. The live test is explicitly opt-in, makes at most
three requests, sends no real-session metadata, and never invokes tools or writes
record data. A local UTF-8 byte upper bound prevents submitting an over-budget
request; this benchmark intentionally does not silently truncate or prefilter.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pytest

_TESTS = Path(__file__).resolve().parent
_CORE = _TESTS.parent
for _path in (_CORE, _TESTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from test_work_records_999_benchmark import BenchmarkCase, CANDIDATES, _case

MODEL_ID = "0x567-flash"
EXPECTED_VENDOR = "0x567"
EXPECTED_PROVIDER_MODEL = "gpt-6-luna"
EXPECTED_MAX_INPUT_TOKENS = 1_000_000
LIVE_OPT_IN = "MY_AGENT_LIVE_WORK_RECORD_LUNA_999"
MAX_LIVE_CALLS = 3
OUTPUT_RESERVE_TOKENS = 16_384


def luna_no_overlap_case() -> BenchmarkCase:
    """Use a unique temporal paraphrase without shared CJK bigrams with record 999."""
    return BenchmarkCase(
        label="luna_synonym_no_overlap_unique",
        category="near_synonym_without_bigram_overlap_unique_time",
        question="那支团队交谈原定星期三，后来日期顺延为下星期一，具体几点？",
        expected_ids=("synthetic-0999",),
    )


@dataclass(frozen=True)
class Decision:
    status: str
    candidate_ids: tuple[str, ...] = ()


@dataclass
class Metrics:
    request_count: int = 0
    request_body_bytes: list[int] | None = None
    response_bytes: list[int] | None = None
    latency_ms: list[float] | None = None
    prompt_tokens: list[int | None] | None = None
    error_types: list[str] | None = None
    budget_refusals: int = 0

    def __post_init__(self) -> None:
        self.request_body_bytes = [] if self.request_body_bytes is None else self.request_body_bytes
        self.response_bytes = [] if self.response_bytes is None else self.response_bytes
        self.latency_ms = [] if self.latency_ms is None else self.latency_ms
        self.prompt_tokens = [] if self.prompt_tokens is None else self.prompt_tokens
        self.error_types = [] if self.error_types is None else self.error_types


def validate_model_entry(entry: Any) -> None:
    """Fail closed if the registry profile is missing or has any override."""
    actual = (
        getattr(entry, "id", None),
        getattr(entry, "vendor", None),
        getattr(entry, "provider_model", None),
        getattr(entry, "max_input_tokens", None),
    )
    expected = (MODEL_ID, EXPECTED_VENDOR, EXPECTED_PROVIDER_MODEL, EXPECTED_MAX_INPUT_TOKENS)
    if actual != expected:
        raise RuntimeError("0x567-flash registry profile mismatch; refusing live benchmark")


def parse_decision(content: str, allowed_ids: set[str]) -> Decision:
    """Strictly accept only a state and IDs from the current complete candidate set."""
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid JSON decision") from exc
    if not isinstance(payload, dict) or set(payload) != {"status", "candidate_ids"}:
        raise ValueError("decision has unexpected shape")
    status, ids = payload["status"], payload["candidate_ids"]
    if status not in {"match", "uncertain", "ambiguous"} or not isinstance(ids, list):
        raise ValueError("invalid decision status or IDs")
    if any(not isinstance(item, str) for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("candidate IDs must be unique strings")
    if not set(ids).issubset(allowed_ids):
        raise ValueError("decision contains an ID outside the current candidates")
    if (status == "match" and len(ids) != 1) or (status == "uncertain" and ids) or (status == "ambiguous" and len(ids) < 2):
        raise ValueError("decision status does not match candidate ID count")
    return Decision(status, tuple(ids))


def _messages(question: str, candidates: Iterable[Any]) -> list[dict[str, str]]:
    rows = [{"id": row.record_id, "title": row.title, "excerpt": row.excerpt} for row in candidates]
    system = (
        "You are an isolated semantic recognition benchmark. Compare the query directly with EVERY supplied candidate; "
        "there is no retrieval or prefilter. Return exactly one JSON object with exactly keys status and candidate_ids. "
        "status is match with one ID, ambiguous with two or more equally plausible IDs, or uncertain with an empty list. "
        "IDs must be copied verbatim from this candidate set. Never infer or invent IDs. No explanation, tools, actions, "
        "record edits, or access to conversations outside this supplied synthetic data."
    )
    user = json.dumps({"query": question, "candidates": rows}, ensure_ascii=False, separators=(",", ":"))
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def recognize_all(question: str, candidates: Iterable[Any], client: Any, metrics: Metrics,
                  *, max_input_tokens: int = EXPECTED_MAX_INPUT_TOKENS) -> Decision:
    """One call over the whole supplied set, or fail-closed uncertain without a call."""
    rows = tuple(candidates)
    if not question.strip() or not rows:
        return Decision("uncertain")
    allowed = {row.record_id for row in rows}
    if len(allowed) != len(rows):
        return Decision("uncertain")

    messages = _messages(question, rows)
    request_body = {
        "model": EXPECTED_PROVIDER_MODEL,
        "messages": messages,
        "temperature": 0.0,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    body_bytes = len(json.dumps(request_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    # For UTF-8 byte-level tokenizers, input tokens cannot exceed input bytes;
    # reserve response capacity too. Never truncate or submit a partial corpus.
    if body_bytes + OUTPUT_RESERVE_TOKENS > max_input_tokens:
        metrics.budget_refusals += 1
        return Decision("uncertain")

    started = time.perf_counter()
    metrics.request_count += 1
    metrics.request_body_bytes.append(body_bytes)
    try:
        response = client.chat(
            messages,
            model=MODEL_ID,
            tools=[],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        tool_calls = [] if isinstance(response, str) else getattr(response, "tool_calls", [])
        if tool_calls:
            raise ValueError("tool call response rejected")
        content = response if isinstance(response, str) else getattr(response, "content", None)
        if not isinstance(content, str):
            raise ValueError("empty model response")
        metrics.response_bytes.append(len(content.encode("utf-8")))
        usage = getattr(response, "usage", None)
        prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        metrics.prompt_tokens.append(prompt_tokens if isinstance(prompt_tokens, int) else None)
        return parse_decision(content, allowed)
    except Exception as exc:  # model/API/format errors are non-actionable uncertainty
        metrics.error_types.append(type(exc).__name__)
        if len(metrics.response_bytes) < metrics.request_count:
            metrics.response_bytes.append(0)
        if len(metrics.prompt_tokens) < metrics.request_count:
            metrics.prompt_tokens.append(None)
        return Decision("uncertain")
    finally:
        metrics.latency_ms.append((time.perf_counter() - started) * 1000)


def test_parser_accepts_only_current_candidate_ids_and_recognition_states():
    allowed = {candidate.record_id for candidate in CANDIDATES}
    assert parse_decision('{"status":"match","candidate_ids":["synthetic-0999"]}', allowed).candidate_ids == ("synthetic-0999",)
    assert parse_decision('{"status":"uncertain","candidate_ids":[]}', allowed).status == "uncertain"
    assert parse_decision('{"status":"ambiguous","candidate_ids":["synthetic-0001","synthetic-0498"]}', allowed).status == "ambiguous"
    for invalid in (
        '{"status":"match","candidate_ids":["outside"]}',
        '{"status":"match","candidate_ids":["synthetic-0001"],"reason":"extra"}',
        '{"status":"match","candidate_ids":[]}',
        '{"status":"ambiguous","candidate_ids":["synthetic-0001"]}',
        '```json\n{"status":"uncertain","candidate_ids":[]}\n```',
    ):
        with pytest.raises(ValueError):
            parse_decision(invalid, allowed)



def test_luna_no_overlap_fixture_uniquely_targets_999_offline():
    case = luna_no_overlap_case()
    target = next(row for row in CANDIDATES if row.record_id == "synthetic-0999")
    query_bigrams = {case.question[i:i + 2] for i in range(len(case.question) - 1)}
    target_text = target.title + target.excerpt
    target_bigrams = {target_text[i:i + 2] for i in range(len(target_text) - 1)}
    assert query_bigrams.isdisjoint(target_bigrams)
    assert case.expected_ids == ("synthetic-0999",)
    time_cue_ids = {
        row.record_id for row in CANDIDATES
        if "周三" in row.title + row.excerpt and "下周一" in row.title + row.excerpt
    }
    assert time_cue_ids == {"synthetic-0999"}


def test_fake_recognizer_sends_all_999_candidates_and_only_synthetic_fields():
    from types import SimpleNamespace

    seen = []
    target = luna_no_overlap_case()
    def fake_client_chat(messages, **kwargs):
        seen.append((messages, kwargs))
        body = json.loads(messages[1]["content"])
        return SimpleNamespace(content=json.dumps({"status": "match", "candidate_ids": [target.expected_ids[0]]}), usage={"prompt_tokens": 123})

    metrics = Metrics()
    decision = recognize_all(target.question, CANDIDATES, SimpleNamespace(chat=fake_client_chat), metrics)
    assert decision == Decision("match", target.expected_ids)
    assert metrics.request_count == 1 and metrics.prompt_tokens == [123]
    messages, kwargs = seen[0]
    payload = json.loads(messages[1]["content"])
    assert len(payload["candidates"]) == 999
    assert {row["id"] for row in payload["candidates"]} == {row.record_id for row in CANDIDATES}
    assert all(set(row) == {"id", "title", "excerpt"} for row in payload["candidates"])
    assert "session_id" not in messages[1]["content"]
    assert kwargs == {"model": MODEL_ID, "tools": [], "temperature": 0.0, "response_format": {"type": "json_object"}}
    assert metrics.request_body_bytes[0] > 0 and metrics.response_bytes[0] > 0


def test_empty_context_and_model_errors_fail_closed_without_leaking_error_text():
    class BrokenClient:
        def chat(self, *_args, **_kwargs):
            raise RuntimeError("synthetic secret-like provider detail")

    metrics = Metrics()
    assert recognize_all("query", (), BrokenClient(), metrics) == Decision("uncertain")
    assert metrics.request_count == 0
    result = recognize_all("query", CANDIDATES, BrokenClient(), metrics)
    assert result == Decision("uncertain")
    assert metrics.request_count == 1 and metrics.error_types == ["RuntimeError"]
    assert "secret-like" not in repr(metrics)


def test_budget_refusal_never_sends_a_partial_candidate_set():
    class ForbiddenClient:
        def chat(self, *_args, **_kwargs):
            raise AssertionError("over-budget request must not be sent")

    metrics = Metrics()
    result = recognize_all("query", CANDIDATES, ForbiddenClient(), metrics, max_input_tokens=20_000)
    assert result == Decision("uncertain")
    assert metrics.request_count == 0 and metrics.budget_refusals == 1


def test_registry_profile_rejects_model_identity_and_context_overrides():
    from types import SimpleNamespace

    valid = SimpleNamespace(id=MODEL_ID, vendor=EXPECTED_VENDOR, provider_model=EXPECTED_PROVIDER_MODEL,
                            max_input_tokens=EXPECTED_MAX_INPUT_TOKENS)
    validate_model_entry(valid)
    for field, value in (("id", "other"), ("vendor", "other"), ("provider_model", "other"),
                         ("max_input_tokens", 999_999)):
        altered = vars(valid).copy()
        altered[field] = value
        with pytest.raises(RuntimeError, match="registry profile mismatch"):
            validate_model_entry(SimpleNamespace(**altered))


def _live_client() -> Any:
    from llm_client import LLMClient, load_config
    from llm_models import get_registry
    from paths import AgentPaths

    paths = AgentPaths.discover(start=Path(__file__).resolve())
    entry = get_registry(paths).get(MODEL_ID)
    if entry is None:
        pytest.fail("required 0x567-flash model is missing from the active registry")
    validate_model_entry(entry)
    # Check only presence; never print, persist, or assert on the credential value.
    if not entry.resolve_api_key(paths):
        pytest.skip("0x567 credential unavailable; no live request made")
    return LLMClient(load_config(), paths=paths)


@pytest.mark.skipif(os.environ.get(LIVE_OPT_IN) != "1", reason=f"set {LIVE_OPT_IN}=1 to allow up to 3 live synthetic-only requests")
def test_opt_in_luna_recognizes_entire_999_candidate_set_for_three_cases():
    client = _live_client()
    cases = (
        luna_no_overlap_case(),
        _case("ambiguous"),
        _case("not_found"),
    )
    metrics = Metrics()
    started = time.perf_counter()
    outcomes: list[tuple[Any, Decision, int, float]] = []
    for case in cases:
        calls_before = metrics.request_count
        decision = recognize_all(case.question, CANDIDATES, client, metrics)
        if metrics.request_count > calls_before:
            request_bytes = metrics.request_body_bytes[-1]
            elapsed_ms = metrics.latency_ms[-1]
        else:
            request_bytes = 0
            elapsed_ms = 0.0
        outcomes.append((case, decision, request_bytes, elapsed_ms))
    total_ms = (time.perf_counter() - started) * 1000

    for case, decision, request_bytes, elapsed_ms in outcomes:
        print(
            f"case={case.label} status={decision.status} ids={list(decision.candidate_ids)} "
            f"expected={list(case.expected_ids)} request_bytes={request_bytes} elapsed_ms={elapsed_ms:.1f}"
        )
    print(
        f"benchmark=direct_full_corpus candidates={len(CANDIDATES)} requests={metrics.request_count}/3 "
        f"request_bytes_total={sum(metrics.request_body_bytes)} response_bytes_total={sum(metrics.response_bytes)} "
        f"prompt_tokens={metrics.prompt_tokens} total_ms={total_ms:.1f} errors={metrics.error_types} "
        "synthetic_only=true; no prefilter, tools, writes, or real-session access"
    )
    assert metrics.request_count == len(cases) <= MAX_LIVE_CALLS
    assert metrics.budget_refusals == 0, "benchmark incomplete: a full 999-candidate request exceeded the token budget"
    assert len(metrics.latency_ms) == len(cases)
    assert all(size + OUTPUT_RESERVE_TOKENS <= EXPECTED_MAX_INPUT_TOKENS for size in metrics.request_body_bytes)
    # Decisions are benchmark observations, not gating product-quality assertions.


