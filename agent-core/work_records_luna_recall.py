"""Explicitly injected, read-only GPT-6 Luna recall over one complete session set.

Constructing and calling this recognizer is an explicit external-processing opt-in:
the caller must knowingly provide a question and candidate summaries that may be
sent to 0x567. Never pass real conversation content without separately approved
privacy handling. This module has no session discovery, persistence, or tools.
"""
from __future__ import annotations

import inspect
import json
import re
from typing import Any, Sequence

from llm_models import get_registry
from work_records_semantic import SearchResult, WorkRecordCandidate

MODEL_ID = "0x567-flash"
EXPECTED_VENDOR = "0x567"
EXPECTED_PROVIDER_MODEL = "gpt-6-luna"
# Operational UTF-8 byte cap; not a tokenizer-derived token count. Provider config remains a hard ceiling.
DEFAULT_INPUT_TOKEN_BUDGET = 256_000
DEFAULT_OUTPUT_RESERVE = 2_048
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SYSTEM = (
    "You are a read-only semantic recognizer. Compare the question directly against EVERY candidate supplied; "
    "the list is complete and must not be searched, reordered, shortened, or prefiltered. Return exactly one JSON "
    "object with exactly keys status and candidate_ids. Use match with exactly one candidate ID, ambiguous with "
    "two or more plausible IDs, or not_found with an empty list. Copy IDs verbatim from the supplied candidates. "
    "Do not invent IDs or add explanations. Never call tools, alter records, or infer/access any other session."
)


class WorkRecordsLunaRecall:
    """Synchronous adapter for explicit controller injection.

    The default conservative request budget is 256k UTF-8 bytes. Callers may explicitly raise
    ``max_input_tokens`` for an approved synthetic full-corpus trial; each request
    remains capped by the configured provider limit and checked with the same
    UTF-8-byte request estimate used by the confirmation preview.
    """

    def __init__(
        self,
        client: Any,
        *,
        max_input_tokens: int = DEFAULT_INPUT_TOKEN_BUDGET,
        output_reserve_tokens: int = DEFAULT_OUTPUT_RESERVE,
    ) -> None:
        if not callable(getattr(client, "chat", None)):
            raise TypeError("client must expose synchronous chat")
        if not isinstance(max_input_tokens, int) or max_input_tokens < 1:
            raise ValueError("max_input_tokens must be positive")
        if not isinstance(output_reserve_tokens, int) or output_reserve_tokens < 0:
            raise ValueError("output_reserve_tokens must be nonnegative")
        self._client = client
        self._input_budget = max_input_tokens
        self._output_reserve = output_reserve_tokens

    def match(self, question: str, candidates: Sequence[WorkRecordCandidate]) -> SearchResult:
        if not isinstance(question, str) or not question.strip():
            return SearchResult("uncertain", scope="current_session")
        try:
            rows = tuple(candidates)
        except Exception:
            return SearchResult("incomplete", scope="current_session")
        if not rows:
            return SearchResult("not_found", scope="current_session")
        validated = self._validate_candidates(rows)
        if validated is None:
            return SearchResult("incomplete", scope="current_session")

        try:
            registry = get_registry(getattr(self._client, "paths", None)) if getattr(self._client, "paths", None) is not None else get_registry()
            entry = registry.get(MODEL_ID)
            if (entry is None or getattr(entry, "id", None) != MODEL_ID
                    or getattr(entry, "vendor", None) != EXPECTED_VENDOR
                    or getattr(entry, "provider_model", None) != EXPECTED_PROVIDER_MODEL):
                return SearchResult("error", scope="current_session")
            provider_limit = getattr(entry, "max_input_tokens", None)
            if not isinstance(provider_limit, int) or provider_limit <= 0:
                return SearchResult("error", scope="current_session")
            budget = min(self._input_budget, provider_limit)
            messages = self._messages(question, validated)
            # Byte count is deliberately a conservative upper bound; reserve output and
            # framing space, and refuse the complete request instead of sending a prefix.
            if not request_fits_budget(
                messages, input_budget=self._input_budget, provider_limit=provider_limit,
                output_reserve_tokens=self._output_reserve,
            ):
                return SearchResult("incomplete", scope="current_session")
            response = self._client.chat(
                messages,
                model=MODEL_ID,
                tools=[],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            if inspect.isawaitable(response):
                close = getattr(response, "close", None)
                if callable(close):
                    close()
                return SearchResult("error", scope="current_session")
            if isinstance(response, str):
                content = response
            else:
                if getattr(response, "tool_calls", None):
                    return SearchResult("error", scope="current_session")
                content = getattr(response, "content", None)
            if not isinstance(content, str):
                return SearchResult("error", scope="current_session")
            return self._parse(content, {row["id"] for row in validated})
        except Exception:
            # Do not return/log provider details; they can contain sensitive request data.
            return SearchResult("error", scope="current_session")

    @staticmethod
    def _validate_candidates(candidates: tuple[Any, ...]) -> list[dict[str, str]] | None:
        ids: set[str] = set()
        sessions: set[str] = set()
        serialized: list[dict[str, str]] = []
        for candidate in candidates:
            if not isinstance(candidate, WorkRecordCandidate):
                return None
            record_id, title, excerpt, session_id = (
                candidate.record_id, candidate.title, candidate.excerpt, candidate.session_id
            )
            if (not isinstance(record_id, str) or not _ID_RE.fullmatch(record_id)
                    or not isinstance(title, str) or not isinstance(excerpt, str)
                    or not isinstance(session_id, str) or not _ID_RE.fullmatch(session_id)):
                return None
            if record_id in ids:
                return None
            ids.add(record_id)
            sessions.add(session_id)
            # Session identity is checked locally and is never serialized to the provider.
            serialized.append({"id": record_id, "title": title, "excerpt": excerpt})
        if len(sessions) != 1:
            return None
        return serialized

    @staticmethod
    def _messages(question: str, candidates: list[dict[str, str]]) -> list[dict[str, str]]:
        payload = json.dumps(
            {"question": question, "candidates": candidates},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": payload},
        ]

    @staticmethod
    def _parse(content: str, allowed_ids: set[str]) -> SearchResult:
        try:
            payload = json.loads(content, object_pairs_hook=_unique_object)
            if not isinstance(payload, dict) or set(payload) != {"status", "candidate_ids"}:
                raise ValueError("unexpected result shape")
            status, ids = payload["status"], payload["candidate_ids"]
            if (status not in {"match", "ambiguous", "not_found"} or not isinstance(ids, list)
                    or any(not isinstance(record_id, str) for record_id in ids)
                    or len(ids) != len(set(ids)) or not set(ids).issubset(allowed_ids)):
                raise ValueError("invalid result values")
            if ((status == "match" and len(ids) != 1)
                    or (status == "ambiguous" and len(ids) < 2)
                    or (status == "not_found" and ids)):
                raise ValueError("status and candidate IDs disagree")
            # A model's negative result is not reliable enough to assert absence
            # from a nonempty corpus; retain an explicit uncertainty for the caller.
            if status == "not_found":
                return SearchResult("uncertain", scope="current_session")
            chosen = ids[0] if status == "match" else None
            return SearchResult(status, candidate_id=chosen, candidate_ids=tuple(ids), scope="current_session")
        except Exception:
            return SearchResult("error", scope="current_session")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result






def request_fits_budget(
    messages: list[dict[str, str]], *, input_budget: int, provider_limit: int,
    output_reserve_tokens: int = DEFAULT_OUTPUT_RESERVE,
) -> bool:
    """Conservatively preflight the exact serialized messages used by preview and chat.

    UTF-8 bytes are an upper-bound proxy, not a provider tokenizer measurement.
    The fixed 512-byte framing allowance and output reserve are intentionally retained.
    """
    if (not isinstance(input_budget, int) or input_budget < 1
            or not isinstance(provider_limit, int) or provider_limit < 1
            or not isinstance(output_reserve_tokens, int) or output_reserve_tokens < 0):
        return False
    body_bytes = len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return body_bytes + output_reserve_tokens + 512 <= min(input_budget, provider_limit)
