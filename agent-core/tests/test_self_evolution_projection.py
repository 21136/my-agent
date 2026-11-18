from __future__ import annotations

import sys
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from self_evolution_projection import project_evolution
from self_evolution_types import EvolutionEvent, EvolutionIdentity, EvolutionSnapshot, VerificationEvidence


def identity(**changes):
    values = dict(session_id="session-A", task_id="task-A", attempt_id="attempt-A", run_id="run-A", turn_epoch=4)
    values.update(changes)
    return EvolutionIdentity(**values)


def snap(**changes):
    values = dict(identity=identity(), phase="checking", snapshot_revision=8,
                  candidate_digest="candidate-digest-current", discoveries=("private discovery",),
                  change_summary="unreviewed body", evidence=())
    values.update(changes)
    return EvolutionSnapshot(**values)


def test_snapshot_envelope_is_flattened_and_strictly_allowlisted():
    value = snap(evidence=(
        VerificationEvidence("unit", "candidate-digest-current", "passed", 0, "stopped", "ev_1"),
        VerificationEvidence("integration", "old-digest", "passed", 0, "stopped", "ev_old"),
        VerificationEvidence("lint", "candidate-digest-current", "not_run", None, "not_started", "ev_3"),
        VerificationEvidence("timeout", "candidate-digest-current", "unknown", None, "unknown", "ev_4"),
        VerificationEvidence("unsafe-ref", "candidate-digest-current", "passed", 0, "stopped", "C:/secret"),
    ))
    result = project_evolution(value, authorized_session_id="session-A", last_sequence=12,
                               preview_handle="preview_token_1")
    envelope = result["snapshot"]
    assert envelope["type"] == "self_evolution.snapshot"
    assert set(envelope) == {"type", "session_id", "task_id", "attempt_id", "run_id", "turn_epoch", "phase", "status",
                            "snapshot_revision", "sequence", "findings", "verification", "preview_handle"}
    row = envelope
    assert set(row) == {"type", "session_id", "task_id", "attempt_id", "run_id", "turn_epoch", "phase", "status",
                        "snapshot_revision", "sequence", "findings", "verification", "preview_handle"}
    assert row["status"] == "active"
    assert row["verification"] == [
        {"label": "检查 1", "result": "passed", "evidence_ref": "ev_1"},
        {"label": "检查 2", "result": "not_run", "evidence_ref": None},
        {"label": "检查 3", "result": "unknown", "evidence_ref": None},
        {"label": "检查 4", "result": "unknown", "evidence_ref": None},
    ]
    assert row["findings"] == []
    assert row["sequence"] == 12
    assert "private discovery" not in repr(result) and "unreviewed body" not in repr(result)
    assert "old-digest" not in repr(result) and "candidate-digest-current" not in repr(result)
    assert "C:/secret" not in repr(result)


def test_rejects_snapshot_from_a_different_authenticated_session():
    with pytest.raises(ValueError):
        project_evolution(snap(), authorized_session_id="session-B", last_sequence=0)


def test_preview_handle_accepts_only_safe_host_opaque_token_shape():
    for handle in ("C:/candidate", "../candidate", "preview/path", "https://example.test/preview", "file:///tmp/x", " "):
        with pytest.raises(ValueError):
            project_evolution(snap(), authorized_session_id="session-A", last_sequence=0,
                              preview_handle=handle)
    envelope = project_evolution(snap(), authorized_session_id="session-A", last_sequence=0)["snapshot"]
    assert envelope["preview_handle"] is None


def test_updates_follow_explicit_watermark_and_monotonic_identity_revision():
    first = EvolutionEvent(identity(), 13, "editing", "phase_changed", ("task_accepted",), 9)
    second = EvolutionEvent(identity(), 15, "checking", "verification_completed", ("unknown",), 10)
    covered = EvolutionEvent(identity(), 12, "editing", "phase_changed", ("task_accepted",), 8)
    result = project_evolution(snap(), authorized_session_id="session-A", last_sequence=12,
                               events=(covered, first, second))
    assert [event["sequence"] for event in result["events"]] == [13, 15]
    assert all(event["type"] == "self_evolution.update" for event in result["events"])
    assert set(result["events"][0]) == {"type", "session_id", "task_id", "attempt_id", "run_id", "turn_epoch",
                                        "sequence", "phase", "kind", "fact_codes", "snapshot_revision", "evidence_refs"}
    invalid_chains = (
        (EvolutionEvent(identity(task_id="task-B"), 13, "editing", "phase_changed", (), 9),),
        (EvolutionEvent(identity(), 13, "editing", "phase_changed", (), 8),),
        (EvolutionEvent(identity(), 13, "editing", "phase_changed", (), 9),
         EvolutionEvent(identity(), 13, "editing", "phase_changed", (), 10)),
        (EvolutionEvent(identity(), 13, "editing", "phase_changed", (), 10),
         EvolutionEvent(identity(), 14, "editing", "phase_changed", (), 9)),
    )
    for bad_chain in invalid_chains:
        with pytest.raises(ValueError):
            project_evolution(snap(), authorized_session_id="session-A", last_sequence=12, events=bad_chain)


def test_mismatched_session_event_is_rejected_even_when_at_or_before_watermark():
    event = EvolutionEvent(identity(session_id="other-session"), 12, "editing", "phase_changed", (), 8)
    with pytest.raises(ValueError):
        project_evolution(snap(), authorized_session_id="session-A", last_sequence=12, events=(event,))


def test_rejects_invalid_watermark_not_exact_integer():
    for value in (True, -1, 2**53):
        with pytest.raises(ValueError):
            project_evolution(snap(), authorized_session_id="session-A", last_sequence=value)


def test_unbound_old_candidate_evidence_is_filtered_without_success_claim():
    old = VerificationEvidence("check", "old-digest", "passed", 0, "stopped", "ev_old")
    not_run = VerificationEvidence("check-not-run", "candidate-digest-current", "not_run", None, "not_started", "ev_n")
    unknown = VerificationEvidence("check-unknown", "candidate-digest-current", "unknown", None, "unknown", "ev_u")
    data = project_evolution(snap(evidence=(old, not_run, unknown)), authorized_session_id="session-A", last_sequence=0)["snapshot"]
    assert data["verification"] == [
        {"label": "检查 1", "result": "not_run", "evidence_ref": None},
        {"label": "检查 2", "result": "unknown", "evidence_ref": None},
    ]


def test_unbound_or_wrong_result_event_cannot_claim_check_passed():
    event = EvolutionEvent(identity(), 1, "checking", "verification_completed", ("check_passed",), 9, ("ev_old",))
    result = project_evolution(snap(), authorized_session_id="session-A", last_sequence=0, events=(event,))
    projected = result["events"][0]
    assert projected["phase"] == "unknown"
    assert projected["kind"] == "unknown"
    assert projected["fact_codes"] == ["unknown"]
    assert projected["evidence_refs"] == []

    failed = VerificationEvidence("check", "candidate-digest-current", "failed", 1, "stopped", "ev_current")
    result = project_evolution(snap(evidence=(failed,)), authorized_session_id="session-A", last_sequence=0,
                               events=(event.__class__(identity(), 1, "checking", "verification_completed", ("check_passed",), 9, ("ev_current",)),))
    assert result["events"][0]["fact_codes"] == ["unknown"]


def test_check_passed_event_requires_current_digest_passed_evidence():
    passed = VerificationEvidence("check", "candidate-digest-current", "passed", 0, "stopped", "ev_current")
    event = EvolutionEvent(identity(), 1, "checking", "verification_completed", ("check_passed",), 9, ("ev_current",))
    result = project_evolution(snap(evidence=(passed,)), authorized_session_id="session-A", last_sequence=0,
                               events=(event,))
    assert result["events"][0]["fact_codes"] == ["check_passed"]
    assert result["events"][0]["evidence_refs"] == ["ev_current"]



def test_nonverification_facts_keep_only_safe_opaque_host_evidence_refs():
    event = EvolutionEvent(identity(), 1, "editing", "phase_changed", ("edit_started",), 9, ("host-ref_1", "C:/private"))
    result = project_evolution(snap(), authorized_session_id="session-A", last_sequence=0, events=(event,))
    assert result["events"][0]["fact_codes"] == ["edit_started"]
    assert result["events"][0]["evidence_refs"] == ["host-ref_1"]


def test_only_explicit_host_reviewed_findings_are_projected():
    result = project_evolution(
        snap(discoveries=("model-only private observation",), change_summary="unreviewed candidate prose"),
        authorized_session_id="session-A", last_sequence=0,
        public_findings=("Reviewed: check is bound to the current candidate digest.", "Reviewed: phase comes from the trusted host snapshot."),
    )
    assert result["snapshot"]["findings"] == [
        "Reviewed: check is bound to the current candidate digest.", "Reviewed: phase comes from the trusted host snapshot.",
    ]
    assert "model-only private observation" not in repr(result)
    assert "unreviewed candidate prose" not in repr(result)


def test_findings_reject_markup_paths_urls_secrets_and_invalid_lengths():
    rejected = (
        "<script>alert(1)</script>", "candidate\\private.py", "/home/user/private.txt",
        "https://private.example/item", "api_key=do-not-leak", "Bearer token-value", "line\ncontinued",
        "x" * 501,
    )
    for finding in rejected:
        with pytest.raises(ValueError):
            project_evolution(snap(), authorized_session_id="session-A", last_sequence=0,
                              public_findings=(finding,))


def test_findings_collection_is_bounded_and_defaults_to_empty():
    default = project_evolution(snap(), authorized_session_id="session-A", last_sequence=0)
    assert default["snapshot"]["findings"] == []
    with pytest.raises(ValueError):
        project_evolution(snap(), authorized_session_id="session-A", last_sequence=0,
                          public_findings=tuple("Reviewed finding" for _ in range(101)))
    with pytest.raises(ValueError):
        project_evolution(snap(), authorized_session_id="session-A", last_sequence=0,
                          public_findings=("x" * 500,) * 21)


def test_event_collection_is_bounded_and_requires_host_tuple():
    event = EvolutionEvent(identity(), 1, "editing", "phase_changed", (), 9)
    with pytest.raises(ValueError):
        project_evolution(snap(), authorized_session_id="session-A", last_sequence=0, events=[event])
    with pytest.raises(ValueError):
        project_evolution(snap(), authorized_session_id="session-A", last_sequence=0,
                          events=(event,) * 257)


def test_projection_rejects_subclass_records_instead_of_trusting_overridden_fields():
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class ForgedEvent(EvolutionEvent):
        pass

    current = snap()
    event = ForgedEvent(current.identity, 9, "editing", "phase_changed", (), 9)
    with pytest.raises(ValueError):
        project_evolution(current, authorized_session_id=current.identity.session_id, last_sequence=8, events=(event,))
