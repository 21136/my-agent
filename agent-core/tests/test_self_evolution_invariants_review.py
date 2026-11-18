from __future__ import annotations

import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from self_evolution_store import EvolutionStore
from self_evolution_types import EvolutionEvent, EvolutionIdentity, EvolutionSnapshot


def _isolated_test_store(tmp_path: Path) -> EvolutionStore:
    """Exercise persistence in this test's temporary root, not Windows ACL security."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    root = tmp_path.resolve(strict=True)

    def temporary_root_only(path: Path) -> bool:
        return path.resolve(strict=True).is_relative_to(root)

    # The verifier is only a test double; production without one still fails closed.
    return EvolutionStore(state_dir, trusted_acl_verifier=temporary_root_only)


def test_orphan_reconciliation_persists_unknown_transition_event(tmp_path):
    identity = EvolutionIdentity("session-A", "task-A", "attempt-A", "run-A", 1)
    store = _isolated_test_store(tmp_path)
    store.create_task(identity, EvolutionSnapshot(identity, "preparing", 1))
    store.append_event(EvolutionEvent(identity, 1, "preparing", "phase_changed", ("task_accepted",), 1))
    store.claim_writer("owner-A", "task-A", "attempt-A")

    assert store.mark_orphaned_unknown("owner-A") == ("task-A",)

    snapshot = store.read_task("session-A", "task-A")
    events = store.read_events("session-A", "task-A", "attempt-A")
    assert snapshot.phase == "unknown"
    assert events[-1]["phase"] == snapshot.phase
    assert events[-1]["snapshot_revision"] == snapshot.snapshot_revision
    assert events[-1]["sequence"] == 2


def test_candidate_digest_change_invalidates_candidate_summary(tmp_path):
    identity = EvolutionIdentity("session-A", "task-A", "attempt-A", "run-A", 1)
    store = _isolated_test_store(tmp_path)
    store.create_task(identity, EvolutionSnapshot(
        identity, "preparing", 1, candidate_digest="digest-A", change_summary="summary for A"))

    updated = store.compare_and_swap("task-A", 1, EvolutionSnapshot(
        identity, "observing", 2, candidate_digest="digest-B", change_summary="summary for A"))

    assert updated.change_summary is None
