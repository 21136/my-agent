"""999-record synthetic benchmark for local prefiltering and optional live recall.

All candidates/questions below are fictional and fixed. Ground-truth IDs are
independent annotations; lexical retrieval outcomes never define the labels.
Live mode is opt-in and intentionally small: it sends only reduced candidate
sets to 0x567-flash, never the full corpus, and is not run by default.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from work_records_semantic import WorkRecordCandidate, WorkRecordSemanticRecognizer
from work_records_prefilter import prefilter_work_records

LIVE_OPT_IN = "MY_AGENT_LIVE_WORK_RECORD_999_BENCHMARK"
MODEL_ID = "0x567-flash"


@dataclass(frozen=True)
class BenchmarkCase:
    label: str
    category: str
    question: str
    expected_ids: tuple[str, ...]


# Four planted relevant records are placed far apart in the deterministic corpus.
# These annotations are defined before / independently of any retriever output.
CASES = (
    BenchmarkCase("explicit", "keyword_overlap", "海棠组交流会改到周几？", ("synthetic-0001",)),
    BenchmarkCase("synonym_overlap", "near_synonym_with_overlap", "海棠组碰面改期到了哪天？", ("synthetic-0498",)),
    BenchmarkCase("synonym_no_overlap", "near_synonym_without_overlap", "那场碰头后来挪到什么时候？", ("synthetic-0999",)),
    BenchmarkCase("ambiguous", "ambiguous_multi_match", "海棠组那次交流会和碰面分别改到哪天了？", ("synthetic-0001", "synthetic-0498")),
    BenchmarkCase("not_found", "unrelated_not_found", "火星探测器的太阳能板有多大？", ()),
)


def _make_candidates() -> tuple[WorkRecordCandidate, ...]:
    """Return exactly 999 deterministic synthetic candidates, with dispersed targets."""
    planted = {
        1: WorkRecordCandidate("synthetic-0001", "海棠组交流会改期", "海棠组交流会从周二调整到周四下午。", "synthetic-999-benchmark"),
        498: WorkRecordCandidate("synthetic-0498", "海棠组碰面安排", "海棠组碰面原定周二，后来改到周五上午。", "synthetic-999-benchmark"),
        777: WorkRecordCandidate("synthetic-0777", "练习图标配色", "检查模拟图标的颜色组合。", "synthetic-999-benchmark"),
        999: WorkRecordCandidate("synthetic-0999", "模拟讨论日程调整", "一次小组讨论由周三改到下周一下午。", "synthetic-999-benchmark"),
    }
    candidates = []
    for index in range(1, 1000):
        candidates.append(planted.get(index) or WorkRecordCandidate(
            record_id=f"synthetic-{index:04d}",
            title=f"虚构事项 {index:04d}",
            excerpt=f"合成基准填充记录编号 {index:04d}；内容为无关的虚构练习事项。",
            session_id="synthetic-999-benchmark",
        ))
    return tuple(candidates)


CANDIDATES = _make_candidates()


def _case(label: str) -> BenchmarkCase:
    return next(case for case in CASES if case.label == label)


def _run_prefilter(case: BenchmarkCase, *, limit: int = 20):
    return prefilter_work_records(case.question, CANDIDATES, max_candidates=limit)


def _recall(case: BenchmarkCase, selected_ids: tuple[str, ...] | list[str]) -> float | None:
    """Recall against hand-labeled IDs; no relevant IDs means recall is undefined."""
    if not case.expected_ids:
        return None
    return len(set(case.expected_ids) & set(selected_ids)) / len(case.expected_ids)


def test_benchmark_fixture_has_999_unique_candidates_and_dispersed_ground_truth():
    assert len(CANDIDATES) == 999
    assert len({candidate.record_id for candidate in CANDIDATES}) == 999
    positions = {candidate.record_id: index for index, candidate in enumerate(CANDIDATES)}
    assert positions["synthetic-0001"] == 0
    assert positions["synthetic-0498"] == 497
    assert positions["synthetic-0999"] == 998


def test_case_labels_are_independent_and_cover_overlap_synonym_ambiguity_and_absence():
    assert {case.category for case in CASES} >= {
        "keyword_overlap", "near_synonym_with_overlap", "near_synonym_without_overlap",
        "ambiguous_multi_match", "unrelated_not_found",
    }
    by_id = {candidate.record_id: candidate for candidate in CANDIDATES}
    exact = _case("explicit")
    no_overlap = _case("synonym_no_overlap")
    assert exact.expected_ids == ("synthetic-0001",)
    assert no_overlap.expected_ids == ("synthetic-0999",)
    assert "交流会" in exact.question and "交流会" in by_id[exact.expected_ids[0]].title
    # Explicitly record this is a paraphrase with no shared CJK bigram between query and target.
    query = no_overlap.question
    target = by_id[no_overlap.expected_ids[0]].title + by_id[no_overlap.expected_ids[0]].excerpt
    query_bigrams = {query[i:i + 2] for i in range(len(query) - 1)}
    target_bigrams = {target[i:i + 2] for i in range(len(target) - 1)}
    assert query_bigrams.isdisjoint(target_bigrams)
    assert len(_case("ambiguous").expected_ids) == 2
    assert _case("not_found").expected_ids == ()


@pytest.mark.parametrize("label", ["explicit", "synonym_overlap", "synonym_no_overlap", "ambiguous", "not_found"])
def test_local_prefilter_reports_metrics_against_independent_labels(label: str):
    case = _case(label)
    started = time.perf_counter()
    result = _run_prefilter(case)
    measured_ms = (time.perf_counter() - started) * 1000
    selected_ids = tuple(result.candidate_ids)

    assert result.stats.input_count == 999
    assert result.stats.candidate_count == len(result.candidates) == len(selected_ids)
    assert result.stats.candidate_count <= result.stats.budget <= 20
    assert result.stats.elapsed_ms >= 0
    assert measured_ms >= 0
    assert len(selected_ids) == len(set(selected_ids))
    assert set(selected_ids) == {candidate.record_id for candidate in result.candidates}
    assert result.coverage == "lexical_only"
    # A subset is not evidence of complete recall or a global negative conclusion.
    assert result.incomplete and result.uncertain

    recall = _recall(case, selected_ids)
    false_positives = len(set(selected_ids) - set(case.expected_ids))
    print(
        f"case={case.label} category={case.category} input=999 candidates={len(selected_ids)} "
        f"recall={recall if recall is not None else 'n/a'} false_positives={false_positives} "
        f"calls=0 elapsed_ms={result.stats.elapsed_ms:.3f} measured_ms={measured_ms:.3f}"
    )
    # Keep explicit recall values visible in captured local test output when -s is used.
    if case.label in {"explicit", "synonym_overlap", "synonym_no_overlap", "ambiguous"}:
        assert recall is not None


def test_retriever_cannot_promote_missing_or_invalid_ids_to_complete_results():
    case = _case("synonym_no_overlap")
    candidate = next(item for item in CANDIDATES if item.record_id == case.expected_ids[0])

    def invalid_retriever(_question, _candidates, _limit):
        return [candidate.record_id, "not-in-the-corpus"]

    result = prefilter_work_records(case.question, CANDIDATES, max_candidates=20, retriever=invalid_retriever)
    assert result.candidates == ()
    assert result.candidate_ids == ()
    assert result.incomplete and result.uncertain


def _live_recognizer() -> tuple[WorkRecordSemanticRecognizer, list[dict[str, Any]]]:
    from llm_client import LLMClient, load_config
    from llm_models import get_registry
    from llm_secrets import get_llm_secret
    from paths import AgentPaths

    paths = AgentPaths.discover(start=Path(__file__).resolve())
    model = get_registry(paths).get(MODEL_ID)
    if model is None or model.id != MODEL_ID:
        pytest.fail(f"configured model registry does not contain {MODEL_ID}")
    if model.api_key_env and not get_llm_secret(model.api_key_env, paths):
        pytest.skip("0x567 credential unavailable; no live request made")
    client = LLMClient(load_config(), paths=paths)
    observations: list[dict[str, Any]] = []

    class ObservedClient:
        def chat(self, messages, **kwargs):
            started = time.perf_counter()
            try:
                return client.chat(messages, model=MODEL_ID, tools=[], temperature=0.0,
                                   response_format={"type": "json_object"})
            finally:
                observations.append({"elapsed_ms": (time.perf_counter() - started) * 1000})

    return WorkRecordSemanticRecognizer.from_sync_llm_client(ObservedClient(), model=MODEL_ID,
                                                               max_candidates=20, max_calls=4), observations


@pytest.mark.skipif(os.environ.get(LIVE_OPT_IN) != "1", reason=f"set {LIVE_OPT_IN}=1 for bounded live requests")
def test_opt_in_0x567_small_recall_benchmark_uses_synthetic_data_and_reduced_sets_only():
    # Three cases x at most 2 calls each. The 999-item input is only used by the
    # local retriever; no full-corpus payload is sent to the model.
    selected_cases = (_case("explicit"), _case("synonym_no_overlap"), _case("ambiguous"))
    recognizer, observations = _live_recognizer()
    report = []
    wall_started = time.perf_counter()

    for case in selected_cases:
        prefilter_started = time.perf_counter()
        reduced = _run_prefilter(case, limit=20)
        prefilter_ms = (time.perf_counter() - prefilter_started) * 1000
        selected_ids = tuple(reduced.candidate_ids)
        labeled_recall = _recall(case, selected_ids)
        before_recall = 1.0 if case.expected_ids else None
        false_positives = len(set(selected_ids) - set(case.expected_ids))
        if not reduced.candidates:
            report.append((case, reduced, before_recall, labeled_recall, false_positives, None, prefilter_ms, 0, 0.0))
            continue

        before_calls = len(observations)
        started = time.perf_counter()
        decision = recognizer.match(case.question, reduced.candidates)
        model_ms = (time.perf_counter() - started) * 1000
        call_count = len(observations) - before_calls
        report.append((case, reduced, before_recall, labeled_recall, false_positives, decision, prefilter_ms, call_count, model_ms))

    wall_ms = (time.perf_counter() - wall_started) * 1000
    for row in report:
        case, reduced, recall_before, recall_after, false_positives, decision, prefilter_ms, calls, model_ms = row
        result_recall = (
            float(decision.status == "match" and decision.candidate_id in case.expected_ids)
            if decision else (0.0 if case.expected_ids else None)
        )
        if case.category == "ambiguous_multi_match":
            wrong_decision = bool(decision and (
                decision.status != "ambiguous" or set(decision.candidate_ids) != set(case.expected_ids)
            ))
        else:
            wrong_decision = bool(decision and (
                decision.status != "match" or decision.candidate_id not in case.expected_ids
            ))
        print(
            f"case={case.label} recall_before={recall_before if recall_before is not None else 'n/a'} "
            f"labeled_prefilter_recall={recall_after if recall_after is not None else 'n/a'} "
            f"prefilter_false_positives={false_positives} reduced={len(reduced.candidates)}/999 "
            f"model_result={decision.status if decision else 'not_called'} end_to_end_recall={result_recall} "
            f"wrong_decision={wrong_decision} calls={calls} prefilter_ms={prefilter_ms:.3f} model_ms={model_ms:.3f}"
        )
    print(f"benchmark=small cases={len(report)} calls={len(observations)} wall_ms={wall_ms:.3f}; synthetic-only, not product acceptance")
    assert len(report) == 3
    assert len(observations) <= 6
    assert all(0 <= row[3] <= 1 for row in report if row[3] is not None)

