"""Opt-in semantic matching over a bounded, caller-selected session candidate set.

No provider is wired by default; this module never persists data or executes tools.
Free-text excerpts are not claimed to be redacted. Callers must not send real
production records without an explicit privacy-approved integration.
"""
from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Callable, Literal, Sequence


@dataclass(frozen=True)
class WorkRecordCandidate:
    record_id: str
    title: str
    excerpt: str
    session_id: str


@dataclass(frozen=True)
class SearchResult:
    status: Literal["match", "ambiguous", "uncertain", "not_found", "needs_narrowing", "incomplete"]
    candidate_id: str | None = None
    scope: Literal["current_session"] | None = None
    candidate_ids: tuple[str, ...] = ()
    evidence: str | None = None


class WorkRecordSemanticRecognizer:
    """Synchronous selector using an injected text callable or sync LLMClient."""

    def __init__(self, llm: Callable[[str], Any], *, max_candidates: int = 20, max_input_chars: int = 12_000, max_calls: int = 8) -> None:
        if not callable(llm):
            raise TypeError("llm must be callable")
        if max_candidates < 1 or max_input_chars < 1 or max_calls < 2:
            raise ValueError("limits must be positive; max_calls must reserve a final arbitration call")
        self._llm = llm
        self._max_candidates = max_candidates
        self._max_input_chars = max_input_chars
        self._max_calls = max_calls

    @classmethod
    def from_sync_llm_client(
        cls, client: Any, *, model: str | None = None,
        max_candidates: int = 20, max_input_chars: int = 12_000, max_calls: int = 8,
    ) -> "WorkRecordSemanticRecognizer":
        """Adapt the repository's synchronous LLMClient.chat with tools disabled."""
        chat = getattr(client, "chat", None)
        if not callable(chat):
            raise TypeError("client must expose synchronous chat(messages, ...) ")

        def invoke(prompt: str) -> str | None:
            response = chat(
                [{"role": "user", "content": prompt}], model=model, tools=[],
                temperature=0.0, response_format={"type": "json_object"},
            )
            if inspect.isawaitable(response):
                _close_awaitable(response)
                raise TypeError("LLMClient.chat must be synchronous")
            if getattr(response, "tool_calls", None):
                raise TypeError("tool calls are not accepted")
            return getattr(response, "content", None)

        return cls(invoke, max_candidates=max_candidates, max_input_chars=max_input_chars, max_calls=max_calls)

    def match(
        self,
        question: str,
        candidates: Sequence[WorkRecordCandidate],
        *,
        annotate_current_session: bool = False,
    ) -> SearchResult:
        scope = "current_session" if annotate_current_session else None
        if not isinstance(question, str) or not question.strip():
            return SearchResult("uncertain", scope=scope)
        if not candidates:
            return SearchResult("not_found", scope=scope)
        try:
            ids: list[str] = []
            sessions: set[str] = set()
            records: list[dict[str, str]] = []
            for candidate in candidates:
                if not isinstance(candidate, WorkRecordCandidate):
                    return SearchResult("uncertain", scope=scope)
                values = (candidate.record_id, candidate.title, candidate.excerpt, candidate.session_id)
                if any(not isinstance(value, str) for value in values) or not candidate.record_id or not candidate.session_id:
                    return SearchResult("uncertain", scope=scope)
                ids.append(candidate.record_id)
                sessions.add(candidate.session_id)
                # Session identifiers are validated locally but never transmitted.
                records.append({"id": candidate.record_id, "title": candidate.title, "excerpt": candidate.excerpt})
            if len(sessions) != 1 or len(set(ids)) != len(ids):
                return SearchResult("uncertain", scope=scope)
            batches = [records[i:i + self._max_candidates] for i in range(0, len(records), self._max_candidates)]
            # Reserve one call for an independent arbiter; reject the whole request rather than silently search a prefix.
            if len(batches) + 1 > self._max_calls:
                return SearchResult("incomplete", scope=scope)
            proposed: list[str] = []
            proposal_evidence: dict[str, str] = {}
            for index, batch in enumerate(batches, 1):
                payload = json.dumps({"question": question, "batch": index, "candidates": batch}, ensure_ascii=False)
                prompt = (
                    "RECALL BATCH: inspect every candidate, including the last candidate; do not stop early. Match semantic "
                    "equivalents and synonyms, and resolve pronouns/coreferences from the question and candidate text. "
                    "Zero, one, or many may qualify. Do not decide global uniqueness. Return exactly JSON with status candidates|none and candidate_ids "
                    "(all plausible supplied IDs, possibly multiple) and evidence (object mapping each proposed ID to a "
                    "short reason). Do not invent IDs.\n" + payload
                )
                if len(prompt) > self._max_input_chars:
                    return SearchResult("incomplete", scope=scope)
                response = self._invoke_json(prompt)
                if response is None:
                    return SearchResult("incomplete", scope=scope)
                if set(response) != {"status", "candidate_ids", "evidence"}:
                    return SearchResult("incomplete", scope=scope)
                batch_ids = {r["id"] for r in batch}
                selected, evidence = response["candidate_ids"], response["evidence"]
                if (response["status"] not in ("candidates", "none") or not isinstance(selected, list)
                        or any(not isinstance(item, str) or item not in batch_ids for item in selected)
                        or len(set(selected)) != len(selected) or not isinstance(evidence, dict)
                        or set(evidence) != set(selected)
                        or any(not isinstance(v, str) or not v.strip() for v in evidence.values())
                        or (response["status"] == "none" and selected)
                        or (response["status"] == "candidates" and not selected)):
                    return SearchResult("incomplete", scope=scope)
                proposed.extend(selected)
                proposal_evidence.update({key: evidence.get(key, "") for key in selected})
            # A model's per-batch "none" is not a completeness proof. Never turn it into
            # a negative record claim; callers can retry with a narrower query/candidate set.
            if not proposed:
                return SearchResult("incomplete", scope=scope)
            arbitration_payload = json.dumps({
                "question": question,
                "proposals": [{"id": rid, "title": records[ids.index(rid)]["title"],
                               "excerpt": records[ids.index(rid)]["excerpt"], "batch_evidence": proposal_evidence[rid]}
                              for rid in proposed],
            }, ensure_ascii=False)
            prompt = (
                "FINAL ARBITRATION: independently compare all proposals; do not assume a unique match. Return exactly "
                "JSON {status: match|ambiguous|not_found, candidate_id: string|null, candidate_ids: string[], "
                "evidence: concise user-facing explanation}. match requires one proposed ID and candidate_ids [same ID]; "
                "ambiguous requires at least two proposed IDs; not_found requires no IDs.\n" + arbitration_payload
            )
            if len(prompt) > self._max_input_chars:
                return SearchResult("incomplete", scope=scope)
            result = self._invoke_json(prompt)
            if result is None or set(result) != {"status", "candidate_id", "candidate_ids", "evidence"}:
                return SearchResult("incomplete", scope=scope)
            status, chosen, chosen_ids, evidence = (result[k] for k in ("status", "candidate_id", "candidate_ids", "evidence"))
            if (not isinstance(chosen_ids, list) or any(not isinstance(i, str) or i not in proposed for i in chosen_ids)
                    or len(set(chosen_ids)) != len(chosen_ids) or not isinstance(evidence, str) or not evidence.strip()):
                return SearchResult("incomplete", scope=scope)
            if status == "match" and isinstance(chosen, str) and chosen in proposed and chosen_ids == [chosen]:
                return SearchResult("match", candidate_id=chosen, candidate_ids=(chosen,), evidence=evidence, scope=scope)
            if status == "ambiguous" and chosen is None and len(chosen_ids) >= 2:
                return SearchResult("ambiguous", candidate_ids=tuple(chosen_ids), evidence=evidence, scope=scope)
            # The arbiter only sees proposals, not a verifiably complete semantic index.
            # It may not certify global absence, even when it returns not_found.
            if status == "not_found" and chosen is None and not chosen_ids:
                return SearchResult("incomplete", scope=scope)
            return SearchResult("incomplete", scope=scope)
        except Exception:
            return SearchResult("incomplete", scope=scope)

    def _invoke_json(self, prompt: str) -> dict[str, Any] | None:
        try:
            response = self._llm(prompt)
            if inspect.isawaitable(response):
                _close_awaitable(response)
                return None
            if not isinstance(response, str):
                return None
            parsed = json.loads(response, object_pairs_hook=_unique_object)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None


def _close_awaitable(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


