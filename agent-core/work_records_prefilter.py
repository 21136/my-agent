"""Local, bounded candidate reduction for work-record semantic recognition.

This module is an opt-in prefilter only. Its lexical coverage is never evidence
that an unselected record is semantically irrelevant or that the corpus has no
matching event. It does not persist records or call an agent/model.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace
from typing import Callable, Literal, Sequence

from work_records_semantic import SearchResult, WorkRecordCandidate

PrefilterCandidate = WorkRecordCandidate


@dataclass(frozen=True)
class PrefilterStats:
    input_count: int
    candidate_count: int
    budget: int
    elapsed_ms: float
    truncated: bool


@dataclass(frozen=True)
class PrefilterResult:
    candidates: tuple[WorkRecordCandidate, ...]
    candidate_ids: tuple[str, ...]
    coverage: Literal["lexical_only"]
    incomplete: bool
    uncertain: bool
    reason: str | None
    stats: PrefilterStats


class LocalHybridRetriever:
    """Standard-library lexical matcher with transparent, small synonym rules.

    The rules are recall hints, not a language model or a reliable synonym
    ontology. Ranking combines exact normalized terms and CJK character n-grams.
    """

    _synonyms: dict[str, tuple[str, ...]] = {
        "买": ("购买", "购置", "采购"),
        "购买": ("买", "购置", "采购"),
        "出差": ("差旅", "商务旅行", "商务出行"),
        "商务旅行": ("出差", "差旅", "商务出行"),
        "会议": ("开会", "讨论"),
        "开会": ("会议", "讨论"),
        "修复": ("解决", "处理"),
        "报销": ("费用", "票据", "发票"),
    }
    _word_re = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]+", re.IGNORECASE)

    def __call__(self, question: str, candidates: Sequence[WorkRecordCandidate], limit: int) -> Sequence[str]:
        query_terms = self._terms(question)
        # Expand only when a known phrase is literally present; expose no claim
        # that this small table covers paraphrases or pronoun/coreference.
        for phrase, alternatives in self._synonyms.items():
            if phrase in question:
                query_terms.update(self._terms(" ".join(alternatives)))
        if not query_terms:
            return ()
        ranked: list[tuple[int, int, str]] = []
        for ordinal, candidate in enumerate(candidates):
            text_terms = self._terms(f"{candidate.title} {candidate.excerpt}")
            score = sum(3 if term in text_terms else 1 for term in query_terms if term in text_terms)
            if score:
                ranked.append((score, -ordinal, candidate.record_id))
        ranked.sort(reverse=True)
        return tuple(record_id for _, _, record_id in ranked[:limit])

    @classmethod
    def _terms(cls, text: str) -> set[str]:
        terms: set[str] = set()
        for part in cls._word_re.findall(text.casefold()):
            if part.isascii():
                terms.add(part)
            else:
                # Bigrams make Chinese matching possible without a downloaded
                # segmenter. Trigrams add a little phrase discrimination.
                terms.update(part[i:i + 2] for i in range(len(part) - 1))
                terms.update(part[i:i + 3] for i in range(len(part) - 2))
                if len(part) == 1:
                    terms.add(part)
        return terms


Retriever = Callable[[str, Sequence[WorkRecordCandidate], int], Sequence[str]]


def prefilter_work_records(
    question: str,
    candidates: Sequence[WorkRecordCandidate],
    *,
    max_candidates: int = 20,
    retriever: Retriever | None = None,
) -> PrefilterResult:
    """Produce bounded candidates without converting lexical misses to absence.

    When the entire supplied set fits the budget but lexical retrieval finds no
    match, pass the full set downstream and mark it uncertain. If a reduction
    is required, invalid/failed retrieval yields no candidates and an explicit
    incomplete result rather than silently presenting a misleading prefix.
    """
    start = time.perf_counter()
    input_items = tuple(candidates) if isinstance(candidates, Sequence) else ()
    budget = max_candidates if isinstance(max_candidates, int) and not isinstance(max_candidates, bool) else 0

    def finish(items: tuple[WorkRecordCandidate, ...], incomplete: bool, uncertain: bool,
               reason: str | None, *, truncated: bool = False) -> PrefilterResult:
        elapsed = (time.perf_counter() - start) * 1000
        return PrefilterResult(
            candidates=items,
            candidate_ids=tuple(item.record_id for item in items),
            coverage="lexical_only",
            incomplete=incomplete,
            uncertain=uncertain,
            reason=reason,
            stats=PrefilterStats(len(input_items), len(items), max(budget, 0), elapsed, truncated),
        )

    if budget < 1:
        raise ValueError("max_candidates must be a positive integer")
    if not isinstance(question, str) or not question.strip():
        if len(input_items) <= budget and input_items and _valid_candidates(input_items):
            return finish(input_items, False, True, "empty or invalid query; forwarded full bounded set")
        return finish((), True, True, "empty or invalid query", truncated=len(input_items) > budget)
    if not _valid_candidates(input_items):
        return finish((), True, True, "invalid candidate records, duplicate IDs, or mixed sessions", truncated=len(input_items) > budget)
    if not input_items:
        return finish((), False, True, "empty supplied candidate set")

    selected_retriever = retriever if retriever is not None else LocalHybridRetriever()
    try:
        selected_ids = selected_retriever(question, input_items, budget)
        if not isinstance(selected_ids, Sequence) or isinstance(selected_ids, (str, bytes)):
            raise ValueError("retriever must return a sequence of record IDs")
        by_id = {item.record_id: item for item in input_items}
        selected = tuple(selected_ids)
        if (len(selected) > budget or len(set(selected)) != len(selected)
                or any(not isinstance(item, str) or item not in by_id for item in selected)):
            raise ValueError("retriever returned duplicate, unknown, or over-budget IDs")
        reduced = tuple(by_id[record_id] for record_id in selected)
    except Exception as exc:
        return finish((), True, True, f"retriever rejected: {type(exc).__name__}", truncated=len(input_items) > budget)

    if not reduced:
        if len(input_items) <= budget:
            return finish(input_items, False, True, "no lexical candidates; full bounded set forwarded")
        return finish((), True, True, "no lexical candidates within oversized corpus", truncated=True)
    if len(input_items) > budget and len(reduced) >= budget:
        # Cannot know whether the retriever found more than it returned.
        return finish(reduced, True, True, "candidate budget reached; additional matches may be omitted", truncated=True)
    dropped = len(reduced) < len(input_items)
    synonym_hint = retriever is None and any(phrase in question for phrase in LocalHybridRetriever._synonyms)
    uncertain = dropped or synonym_hint
    reason = ("lexical filter omitted records; semantic recall is not guaranteed" if dropped else
              "explicit synonym expansion used; recall remains uncertain" if synonym_hint else None)
    return finish(reduced, dropped, uncertain, reason, truncated=dropped)



def guard_semantic_result(prefilter: PrefilterResult, semantic_result: SearchResult) -> SearchResult:
    """Prevent a model result over a reduced/uncertain set from becoming final.

    A clean pass means every caller-supplied candidate reached the recognizer;
    it still says nothing about records outside that supplied set. Hints from an
    incomplete pass are retained only when they refer to an actually supplied
    prefilter candidate, and never carry a selected candidate_id.
    """
    if not isinstance(prefilter, PrefilterResult) or not isinstance(semantic_result, SearchResult):
        return SearchResult("incomplete", evidence="预筛或语义结果无效；本次识别未完成。")

    supplied_ids = tuple(item.record_id for item in prefilter.candidates
                         if isinstance(item, WorkRecordCandidate) and isinstance(item.record_id, str))
    allowed = set(supplied_ids)
    model_ids: list[str] = []
    raw_ids = semantic_result.candidate_ids
    if isinstance(raw_ids, (tuple, list)):
        model_ids.extend(item for item in raw_ids if isinstance(item, str) and item in allowed)
    if isinstance(semantic_result.candidate_id, str) and semantic_result.candidate_id in allowed:
        if semantic_result.candidate_id not in model_ids:
            model_ids.append(semantic_result.candidate_id)
    hints = tuple(dict.fromkeys(model_ids))

    complete_pass = (
        prefilter.coverage == "lexical_only"
        and not prefilter.incomplete
        and not prefilter.uncertain
        and not prefilter.stats.truncated
        and prefilter.stats.input_count == len(prefilter.candidates)
        and prefilter.stats.candidate_count == len(prefilter.candidates)
        and len(prefilter.candidate_ids) == len(prefilter.candidates)
        and prefilter.candidate_ids == supplied_ids
        and _valid_candidates(prefilter.candidates)
    )
    # If semantic output names anything outside the actual candidate set, it is
    # malformed rather than a valid result, even when the prefilter was complete.
    reported_ids = list(raw_ids) if isinstance(raw_ids, (tuple, list)) else []
    if isinstance(semantic_result.candidate_id, str):
        reported_ids.append(semantic_result.candidate_id)
    if any(not isinstance(item, str) or item not in allowed for item in reported_ids):
        complete_pass = False

    if not complete_pass:
        detail = semantic_result.evidence.strip() if isinstance(semantic_result.evidence, str) else ""
        evidence = "候选预筛未完成或存在不确定性；模型输出不能作为全库唯一匹配或全库无此事的结论。"
        if detail:
            evidence += f" 有限候选提示：{detail}"
        return SearchResult("incomplete", candidate_ids=hints, evidence=evidence, scope=semantic_result.scope)

    if semantic_result.status == "not_found":
        # Do not relay the model's negative evidence: it may assert unsupported
        # global absence even though it only examined the supplied candidate set.
        evidence = f"仅表示在本次提供的候选集合（{len(supplied_ids)} 条）中未找到；不代表全库无此事。"
        return replace(semantic_result, candidate_id=None, candidate_ids=(), evidence=evidence)
    if semantic_result.status == "match":
        detail = semantic_result.evidence.strip() if isinstance(semantic_result.evidence, str) else ""
        evidence = f"仅对本次提供的候选集合（{len(supplied_ids)} 条）成立，不代表全库唯一匹配。"
        if detail:
            evidence += f" 模型说明：{detail}"
        return replace(semantic_result, evidence=evidence)
    return semantic_result


def _valid_candidates(candidates: Sequence[WorkRecordCandidate]) -> bool:
    seen: set[str] = set()
    sessions: set[str] = set()
    for item in candidates:
        if not isinstance(item, WorkRecordCandidate):
            return False
        fields = (item.record_id, item.title, item.excerpt, item.session_id)
        if any(not isinstance(value, str) for value in fields) or not item.record_id or not item.session_id:
            return False
        if item.record_id in seen:
            return False
        seen.add(item.record_id)
        sessions.add(item.session_id)
    return len(sessions) == 1

