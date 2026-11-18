"""Strict, side-effect-free projection of trusted self-evolution records for the UI.

Authentication, event retrieval, and preview-handle issuance belong to the host.
This module accepts those host-authenticated values explicitly and never resolves
paths, URLs, config, or unreviewed worker text.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from self_evolution_types import EvolutionEvent, EvolutionIdentity, EvolutionSnapshot, VerificationEvidence

_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$")
_OPAQUE_HANDLE = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_MAX_SAFE_INTEGER = 2**53 - 1
_MAX_PUBLIC_FINDINGS = 100
_MAX_PUBLIC_FINDING_CHARS = 500
_MAX_PUBLIC_FINDINGS_CHARS = 10_000
_MAX_EVENTS = 256
_UNSAFE_PUBLIC_FINDING = re.compile(
    r"(?i)(?:\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret|"
    r"private[_-]?key|credential)\b\s*[:=]|\bbearer\s+\S+|\b[a-z][a-z0-9+.-]*://)"
)
_PHASES = frozenset({
    "preparing", "observing", "awaiting_design_approval", "editing", "checking",
    "awaiting_review", "blocked", "cancelling", "cancelled", "failed", "unknown", "merged",
})
_KINDS = frozenset({"task_started", "phase_changed", "verification_completed", "task_blocked", "task_terminal"})
_FACTS = frozenset({
    "task_accepted", "observation_recorded", "edit_started", "check_started", "check_passed", "check_failed",
    "blocked", "cancelled", "failed", "unknown", "awaiting_review", "merged",
})
_FACTS_NEEDING_REFS = frozenset({
    "observation_recorded", "edit_started", "check_started", "check_passed", "check_failed", "merged",
})
_TERMINAL_STATUS = {
    "blocked": "blocked", "cancelling": "cancelling", "cancelled": "cancelled",
    "failed": "failed", "unknown": "unknown", "awaiting_review": "completed", "merged": "merged",
}


def _token(value: object) -> bool:
    return isinstance(value, str) and _TOKEN.fullmatch(value) is not None


def _counter(value: object, minimum: int) -> bool:
    return type(value) is int and minimum <= value <= _MAX_SAFE_INTEGER


def _identity_payload(identity: object) -> dict[str, Any]:
    if type(identity) is not EvolutionIdentity:
        raise ValueError("identity must be a trusted EvolutionIdentity")
    values = {
        "session_id": getattr(identity, "session_id", None),
        "task_id": getattr(identity, "task_id", None),
        "attempt_id": getattr(identity, "attempt_id", None),
        "run_id": getattr(identity, "run_id", None),
        "turn_epoch": getattr(identity, "turn_epoch", None),
    }
    if any(not _token(values[name]) for name in ("session_id", "task_id", "attempt_id", "run_id")):
        raise ValueError("invalid projection identity")
    if not _counter(values["turn_epoch"], 0):
        raise ValueError("invalid projection identity")
    return values


def _trusted_evidence(snapshot: EvolutionSnapshot) -> tuple[tuple[VerificationEvidence, ...], set[str]]:
    if type(snapshot.evidence) is not tuple or len(snapshot.evidence) > 100:
        raise ValueError("invalid verification evidence collection")
    current: list[VerificationEvidence] = []
    refs: set[str] = set()
    for item in snapshot.evidence:
        if type(item) is not VerificationEvidence:
            raise ValueError("invalid verification evidence")
        if (not _token(item.check_id) or not _token(item.candidate_digest)
                or item.status not in {"passed", "failed", "not_run", "unknown"}
                or item.process_tree_state not in {"not_started", "stopped", "unknown"}
                or (item.exit_code is not None and type(item.exit_code) is not int)):
            raise ValueError("invalid verification evidence")
        # Evidence about an older (or absent) candidate is intentionally discarded.
        if snapshot.candidate_digest is None or item.candidate_digest != snapshot.candidate_digest:
            continue
        current.append(item)
        if _OPAQUE_HANDLE.fullmatch(item.evidence_ref):
            refs.add(item.evidence_ref)
    return tuple(current), refs


def _event_payload(
    event: EvolutionEvent,
    expected_identity: dict[str, Any],
    minimum_revision: int,
    minimum_sequence: int,
    watermark: int,
    trusted_evidence: tuple[VerificationEvidence, ...],
    trusted_refs: set[str],
    candidate_digest: str | None,
) -> dict[str, Any] | None:
    if type(event) is not EvolutionEvent:
        raise ValueError("events must be trusted EvolutionEvent records")
    identity = _identity_payload(event.identity)
    if identity != expected_identity:
        raise ValueError("event identity does not match snapshot")
    if not _counter(event.sequence, 1) or not _counter(event.snapshot_revision, 1):
        raise ValueError("invalid event sequence or revision")
    # The snapshot watermark explicitly covers these events; filter them instead
    # of replaying them. Identity is still checked above to prevent cross-session input.
    if event.sequence <= watermark:
        return None
    if event.sequence <= minimum_sequence or event.snapshot_revision <= minimum_revision:
        raise ValueError("event sequence or revision is stale")
    if type(event.fact_codes) is not tuple or len(event.fact_codes) > 20:
        raise ValueError("invalid event facts")
    if type(event.evidence_refs) is not tuple or len(event.evidence_refs) > 100:
        raise ValueError("invalid event evidence references")

    raw_facts = list(event.fact_codes)
    refs = [ref for ref in event.evidence_refs if isinstance(ref, str) and _OPAQUE_HANDLE.fullmatch(ref)]
    uncertain = (
        event.phase not in _PHASES
        or not _token(event.kind)
        or event.kind not in _KINDS
        or any(not _token(code) or code not in _FACTS for code in raw_facts)
    )
    if not uncertain:
        for fact in raw_facts:
            if fact in _FACTS_NEEDING_REFS and not refs:
                uncertain = True
                break
            if fact in {"check_passed", "check_failed"}:
                expected_status = "passed" if fact == "check_passed" else "failed"
                matching_refs = {
                    item.evidence_ref for item in trusted_evidence
                    if item.candidate_digest == candidate_digest and item.status == expected_status
                    and _OPAQUE_HANDLE.fullmatch(item.evidence_ref)
                }
                bound_refs = [ref for ref in refs if ref in matching_refs]
                if not bound_refs:
                    uncertain = True
                    break
                refs = bound_refs
    if uncertain:
        phase, kind, facts, evidence_refs = "unknown", "unknown", ["unknown"], []
    else:
        phase = event.phase
        kind = event.kind
        facts = raw_facts
        evidence_refs = refs
    return {
        "type": "self_evolution.update", **identity,
        "sequence": event.sequence, "phase": phase, "kind": kind,
        "fact_codes": facts, "snapshot_revision": event.snapshot_revision,
        "evidence_refs": evidence_refs,
    }


def project_evolution(
    snapshot: EvolutionSnapshot,
    *,
    authorized_session_id: str,
    last_sequence: int,
    events: tuple[EvolutionEvent, ...] = (),
    preview_handle: str | None = None,
    public_findings: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build a snapshot envelope and sanitized update envelopes for the UI.

    ``authorized_session_id`` and ``preview_handle`` must be supplied by the
    authenticated host, never request or model data. ``last_sequence`` is the
    explicit sequence watermark covered by this snapshot. Events at/below it are
    filtered; the remaining events must be strictly increasing in sequence and
    revision. ``public_findings`` must be a host-reviewed tuple; validation limits
    accidental sensitive content but cannot establish that review occurred. The
    snapshot envelope carries the same flattened whitelist fields plus its
    discriminating ``type``; no server or UI adaptation is performed here.
    """
    if type(snapshot) is not EvolutionSnapshot:
        raise ValueError("snapshot must be a trusted EvolutionSnapshot")
    identity = _identity_payload(snapshot.identity)
    if not _token(authorized_session_id) or identity["session_id"] != authorized_session_id:
        raise ValueError("snapshot is not authorized for this session")
    if not _counter(snapshot.snapshot_revision, 1) or not _counter(last_sequence, 0):
        raise ValueError("invalid snapshot revision or sequence watermark")
    if snapshot.phase not in _PHASES:
        raise ValueError("invalid snapshot phase")
    if preview_handle is not None and not _OPAQUE_HANDLE.fullmatch(preview_handle):
        raise ValueError("preview_handle must be a host-issued opaque token")
    candidate_digest = snapshot.candidate_digest
    if candidate_digest is not None and not _token(candidate_digest):
        raise ValueError("invalid candidate digest")

    if type(public_findings) is not tuple or len(public_findings) > _MAX_PUBLIC_FINDINGS:
        raise ValueError("public_findings must be a bounded host-reviewed tuple")
    if any(type(text) is not str or not text or text != text.strip()
           or len(text) > _MAX_PUBLIC_FINDING_CHARS
           or any(ord(char) < 32 or ord(char) == 127 or char in "<>/\\" for char in text)
           or _UNSAFE_PUBLIC_FINDING.search(text)
           for text in public_findings):
        raise ValueError("public finding failed privacy or display safety checks")
    if sum(len(text) for text in public_findings) > _MAX_PUBLIC_FINDINGS_CHARS:
        raise ValueError("public findings exceed the total text limit")

    if type(events) is not tuple or len(events) > _MAX_EVENTS:
        raise ValueError("events must be a bounded host-provided tuple")

    evidence, trusted_refs = _trusted_evidence(snapshot)
    projected_verification: list[dict[str, Any]] = []
    for item in evidence:
        ref_is_safe = bool(_OPAQUE_HANDLE.fullmatch(item.evidence_ref))
        result = item.status
        evidence_ref = item.evidence_ref if result in {"passed", "failed"} and ref_is_safe else None
        if result in {"passed", "failed"} and evidence_ref is None:
            # Never claim a result whose evidence handle cannot safely be exposed.
            result = "unknown"
        projected_verification.append({
            "label": f"检查 {len(projected_verification) + 1}",
            "result": result,
            "evidence_ref": evidence_ref,
        })

    projected_events: list[dict[str, Any]] = []
    sequence = last_sequence
    revision = snapshot.snapshot_revision
    for event in events:
        payload = _event_payload(event, identity, revision, sequence, last_sequence, evidence, trusted_refs, candidate_digest)
        if payload is None:
            continue
        projected_events.append(payload)
        sequence = event.sequence
        revision = event.snapshot_revision

    phase = snapshot.phase
    safe_snapshot = {
        **identity,
        "phase": phase,
        "status": _TERMINAL_STATUS.get(phase, "active"),
        "snapshot_revision": snapshot.snapshot_revision,
        "sequence": last_sequence,
        "findings": list(public_findings),
        "verification": projected_verification,
        "preview_handle": preview_handle,
    }
    return {
        "snapshot": {"type": "self_evolution.snapshot", **safe_snapshot},
        "events": projected_events,
    }
