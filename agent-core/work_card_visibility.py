"""Pure work-entry visibility decision; no UI, persistence, scheduling or I/O.

The host must authenticate and bind explicit view acknowledgements to immutable
result identities and attest trustworthy elapsed time, including across restarts.
A wall-clock timestamp alone cannot prove that the clock did not jump forward.
No system clock is read here and a decision does not perform a GUI action.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal


@dataclass(frozen=True)
class RequirementInput:
    requirement_id: str
    status: str


@dataclass(frozen=True)
class ImportantResult:
    """An immutable result identity; a new or changed fact needs a new ID."""
    requirement_id: str
    result_id: str


@dataclass(frozen=True)
class CardInput:
    session_id: str
    card_id: str
    kind: Literal["project", "independent"]
    requirements: tuple[RequirementInput, ...]
    important_results: tuple[ImportantResult, ...]


@dataclass(frozen=True)
class ViewAck:
    """Host-authenticated explicit view ACK, bound to one exact result identity.

    ``verified`` is the caller's trust assertion, not something this module can
    establish. Rendering, assistant.done, and result creation are not ACKs.
    """
    session_id: str
    card_id: str
    requirement_id: str
    result_id: str
    viewed_at: datetime
    verified: bool = False


@dataclass(frozen=True)
class ClockEvidence:
    """Caller-supplied current time and explicit external trust attestation.

    ``previous_now`` detects a known rollback if supplied, but cannot detect a
    forward jump. ``trusted=True`` is meaningful only if the host independently
    established that enough actual time passed, including after process restart.
    """
    now: datetime | None = None
    trusted: bool = False
    previous_now: datetime | None = None


@dataclass(frozen=True)
class VisibilityDecision:
    may_hide: bool
    reason: str


_KEEP = VisibilityDecision(False, "retain")
_HIDE = VisibilityDecision(True, "eligible_to_hide")
_WAIT = timedelta(minutes=30)


def _valid_id(value: object) -> bool:
    return (type(value) is str and 0 < len(value) <= 256 and value == value.strip()
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _utc(value: object) -> datetime | None:
    if type(value) is not datetime or value.tzinfo is None:
        return None
    try:
        if value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)
    except Exception:
        return None


def decide_visibility(card: CardInput, acknowledgements: tuple[ViewAck, ...],
                      clock: ClockEvidence | None = None) -> VisibilityDecision:
    """Return permission to hide only; never perform or record that action.

    Unknown inputs or absence of reliable evidence retain the entry. Every
    completed requirement must have at least one identified important result;
    every current important result needs exactly one bound, verified ACK that
    has aged at least 30 minutes according to caller-attested reliable time.
    """
    if (type(card) is not CardInput or type(clock) is not ClockEvidence
            or clock.trusted is not True or not _valid_id(card.session_id)
            or not _valid_id(card.card_id) or card.kind not in ("project", "independent")
            or type(card.requirements) is not tuple or not card.requirements
            or type(card.important_results) is not tuple or not card.important_results
            or type(acknowledgements) is not tuple):
        return _KEEP
    now = _utc(clock.now)
    previous = _utc(clock.previous_now) if clock.previous_now is not None else None
    if now is None or (clock.previous_now is not None and (previous is None or now < previous)):
        return _KEEP
    requirements: set[str] = set()
    for req in card.requirements:
        if (type(req) is not RequirementInput or not _valid_id(req.requirement_id)
                or type(req.status) is not str or req.status != "completed"
                or req.requirement_id in requirements):
            return _KEEP
        requirements.add(req.requirement_id)
    results: dict[str, str] = {}
    covered: set[str] = set()
    for result in card.important_results:
        if (type(result) is not ImportantResult or not _valid_id(result.requirement_id)
                or not _valid_id(result.result_id) or result.requirement_id not in requirements
                or result.result_id in results):
            return _KEEP
        results[result.result_id] = result.requirement_id
        covered.add(result.requirement_id)
    if covered != requirements:
        return _KEEP
    seen: set[str] = set()
    for ack in acknowledgements:
        if (type(ack) is not ViewAck or ack.verified is not True
                or ack.session_id != card.session_id or ack.card_id != card.card_id
                or not _valid_id(ack.requirement_id) or not _valid_id(ack.result_id)
                or results.get(ack.result_id) != ack.requirement_id or ack.result_id in seen):
            return _KEEP
        viewed = _utc(ack.viewed_at)
        if viewed is None or viewed > now or now - viewed < _WAIT:
            return _KEEP
        seen.add(ack.result_id)
    return _HIDE if seen == set(results) else _KEEP
