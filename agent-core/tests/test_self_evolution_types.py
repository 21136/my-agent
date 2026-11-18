from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from self_evolution_types import (
    CandidateBinding,
    EvolutionIdentity,
    EvolutionEvent,
    EvolutionSnapshot,
    Phase,
    VerificationEvidence,
)


def identity(**changes):
    values = dict(session_id="session-A", task_id="task-A", attempt_id="attempt-A", run_id="run-A", turn_epoch=4)
    values.update(changes)
    return EvolutionIdentity(**values)


def test_identity_requires_all_nonempty_trusted_ids_and_nonnegative_epoch():
    with pytest.raises(ValueError):
        identity(session_id=" ")
    with pytest.raises(ValueError):
        identity(turn_epoch=True)
    with pytest.raises(ValueError):
        identity(turn_epoch=-1)


def test_contract_records_are_immutable_and_validate_enums_and_revisions():
    bound = CandidateBinding("candidate-A", "a" * 40, "common-A", "stable-A", "candidate-wt-A", "b" * 64)
    ev = VerificationEvidence("unit", "c" * 64, "not_run", None, "not_started", "evidence-A")
    snap = EvolutionSnapshot(identity(), "preparing", 1, candidate=bound, evidence=(ev,))
    assert snap.phase == "preparing"
    assert snap.snapshot_revision == 1
    with pytest.raises((FrozenInstanceError, AttributeError)):
        snap.phase = "failed"
    with pytest.raises(ValueError):
        EvolutionSnapshot(identity(), "invented", 1)
    with pytest.raises(ValueError):
        EvolutionSnapshot(identity(), "preparing", 0)


def test_literals_match_the_cross_module_status_contract():
    assert set(Phase.__args__) == {
        "preparing", "observing", "awaiting_design_approval", "editing", "checking",
        "awaiting_review", "blocked", "cancelling", "cancelled", "failed", "unknown", "merged",
    }


def test_event_contract_rejects_unbounded_or_invalid_identity_fields():
    with pytest.raises(ValueError):
        EvolutionEvent(identity(), 0, "preparing", "phase_changed", (), 1)
    with pytest.raises(ValueError):
        EvolutionEvent(identity(), 1, "preparing", " ", (), 1)


def test_change_summary_without_digest_remains_accepted_for_api_compatibility():
    snapshot = EvolutionSnapshot(identity(), "preparing", 1, change_summary="legacy summary")
    assert snapshot.change_summary == "legacy summary"
