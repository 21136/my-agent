from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
from pathlib import Path, PureWindowsPath
from typing import Literal

Phase = Literal[
    "preparing", "observing", "awaiting_design_approval", "editing", "checking",
    "awaiting_review", "blocked", "cancelling", "cancelled", "failed", "unknown", "merged",
]
VerificationState = Literal["passed", "failed", "not_run", "unknown"]
ProcessTreeState = Literal["not_started", "stopped", "unknown", "not_applicable"]
_PHASES = frozenset(Phase.__args__)
_VERIFICATION_STATES = frozenset(VerificationState.__args__)
_PROCESS_STATES = frozenset(ProcessTreeState.__args__)


def _required_id(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 256:
        raise ValueError(f"{name} must be a non-empty canonical string of at most 256 characters")


@dataclass(frozen=True, slots=True)
class EvolutionIdentity:
    session_id: str
    task_id: str
    attempt_id: str
    run_id: str
    turn_epoch: int

    def __post_init__(self) -> None:
        for name in ("session_id", "task_id", "attempt_id", "run_id"):
            _required_id(getattr(self, name), name)
        if type(self.turn_epoch) is not int or self.turn_epoch < 0:
            raise ValueError("turn_epoch must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class CandidateBinding:
    """Persisted candidate identity, discriminated without inventing Git IDs.

    The original six positional fields remain in place for old callers and old
    database payloads. New snapshot bindings set all three Git-only identities
    to None and provide the authenticated plain-directory identity instead.
    """

    candidate_id: str
    stable_commit: str
    git_common_id: str | None
    stable_worktree_id: str | None
    candidate_worktree_id: str | None
    baseline_manifest_digest: str
    candidate_kind: Literal["git_worktree", "materialized_snapshot"] = "git_worktree"
    snapshot_root: str | None = None
    snapshot_tree_oid: str | None = None
    snapshot_binding_digest: str | None = None

    def __post_init__(self) -> None:
        for name in ("candidate_id", "stable_commit", "baseline_manifest_digest"):
            _required_id(getattr(self, name), name)
        if self.candidate_kind == "git_worktree":
            for name in ("git_common_id", "stable_worktree_id", "candidate_worktree_id"):
                _required_id(getattr(self, name), name)
            if any(value is not None for value in
                   (self.snapshot_root, self.snapshot_tree_oid, self.snapshot_binding_digest)):
                raise ValueError("Git worktree binding cannot contain snapshot identity fields")
            return
        if self.candidate_kind != "materialized_snapshot":
            raise ValueError("unsupported candidate kind")
        if any(value is not None for value in
               (self.git_common_id, self.stable_worktree_id, self.candidate_worktree_id)):
            raise ValueError("materialized snapshot cannot claim Git worktree identities")
        if (type(self.snapshot_root) is not str or not self.snapshot_root or "\x00" in self.snapshot_root
                or not (Path(self.snapshot_root).is_absolute() or PureWindowsPath(self.snapshot_root).is_absolute())):
            raise ValueError("snapshot_root must be an absolute host path")
        if type(self.snapshot_tree_oid) is not str or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", self.snapshot_tree_oid):
            raise ValueError("snapshot_tree_oid must be a Git tree object ID")
        if type(self.snapshot_binding_digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", self.snapshot_binding_digest):
            raise ValueError("snapshot_binding_digest must be a SHA-256 digest")


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
    check_id: str
    candidate_digest: str
    status: VerificationState
    exit_code: int | None
    process_tree_state: ProcessTreeState
    evidence_ref: str
    verification_type: Literal["execution", "static_ast"] = "execution"
    baseline_digest: str | None = None
    before_candidate_digest: str | None = None
    after_candidate_digest: str | None = None

    def __post_init__(self) -> None:
        _required_id(self.check_id, "check_id")
        _required_id(self.candidate_digest, "candidate_digest")
        _required_id(self.evidence_ref, "evidence_ref")
        if self.status not in _VERIFICATION_STATES or self.process_tree_state not in _PROCESS_STATES:
            raise ValueError("invalid verification or process-tree state")
        if self.exit_code is not None and type(self.exit_code) is not int:
            raise ValueError("exit_code must be an integer or None")
        if self.verification_type not in {"execution", "static_ast"}:
            raise ValueError("invalid verification type")
        static_digests = (self.baseline_digest, self.before_candidate_digest, self.after_candidate_digest)
        if self.verification_type == "static_ast":
            if (self.process_tree_state != "not_applicable" or self.exit_code is not None
                    or any(type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None
                           for value in static_digests)
                    or self.before_candidate_digest != self.candidate_digest
                    or self.after_candidate_digest != self.candidate_digest):
                raise ValueError("static AST evidence must be digest-bound and non-executing")
        elif (self.process_tree_state == "not_applicable"
              or any(value is not None for value in static_digests)):
            raise ValueError("execution evidence cannot claim non-applicable process state or static AST digests")


@dataclass(frozen=True, slots=True)
class EvolutionSnapshot:
    identity: EvolutionIdentity
    phase: Phase
    snapshot_revision: int
    candidate: CandidateBinding | None = None
    candidate_digest: str | None = None
    discoveries: tuple[str, ...] = ()
    # Existing consumers may omit candidate_digest; the store clears this summary
    # whenever a persisted snapshot changes to a different candidate digest.
    change_summary: str | None = None
    evidence: tuple[VerificationEvidence, ...] = ()
    blocker_code: str | None = None
    error_code: str | None = None
    allowed_next_actions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, EvolutionIdentity):
            raise ValueError("identity must be EvolutionIdentity")
        if self.phase not in _PHASES:
            raise ValueError("invalid phase")
        if type(self.snapshot_revision) is not int or self.snapshot_revision < 1:
            raise ValueError("snapshot_revision must be a positive integer")
        for name in ("discoveries", "evidence", "allowed_next_actions"):
            if not isinstance(getattr(self, name), tuple):
                raise ValueError(f"{name} must be a tuple")
        if self.candidate_digest is not None:
            _required_id(self.candidate_digest, "candidate_digest")
        for name in ("change_summary", "blocker_code", "error_code"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string or None")
        if len(self.discoveries) > 64 or len(self.allowed_next_actions) > 64:
            raise ValueError("snapshot text collections exceed bounded item count")
        for text in self.discoveries + self.allowed_next_actions:
            if not isinstance(text, str) or len(text) > 2048:
                raise ValueError("snapshot text items must be strings of at most 2048 characters")
        if self.change_summary is not None and len(self.change_summary) > 8192:
            raise ValueError("change_summary exceeds 8192 characters")
        if any(not isinstance(item, VerificationEvidence) for item in self.evidence):
            raise ValueError("evidence entries must be VerificationEvidence")


@dataclass(frozen=True, slots=True)
class EvolutionRequest:
    user_goal: str
    acceptance_points: tuple[str, ...] = ()
    context_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.user_goal, str) or not self.user_goal.strip():
            raise ValueError("user_goal is required")
        if len(self.user_goal) > 20_000:
            raise ValueError("user_goal exceeds the contract limit")
        for name in ("acceptance_points", "context_refs"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or len(values) > 128 or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError(f"{name} must be a bounded tuple of non-empty strings")


@dataclass(frozen=True, slots=True)
class EvolutionEvent:
    identity: EvolutionIdentity
    sequence: int
    phase: Phase
    kind: str
    fact_codes: tuple[str, ...]
    snapshot_revision: int
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("sequence must be a positive integer")
        if self.phase not in _PHASES:
            raise ValueError("invalid phase")
        _required_id(self.kind, "kind")
        if type(self.snapshot_revision) is not int or self.snapshot_revision < 1:
            raise ValueError("snapshot_revision must be positive")
        for name in ("fact_codes", "evidence_refs"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or len(values) > 64:
                raise ValueError(f"{name} must be a bounded tuple")
            for value in values:
                _required_id(value, name)


@dataclass(frozen=True, slots=True)
class CancelReceipt:
    task_id: str
    attempt_id: str
    disposition: Literal["accepted", "already_finished", "not_found", "unknown"]

    def __post_init__(self) -> None:
        _required_id(self.task_id, "task_id")
        _required_id(self.attempt_id, "attempt_id")
        if self.disposition not in {"accepted", "already_finished", "not_found", "unknown"}:
            raise ValueError("invalid cancel disposition")


@dataclass(frozen=True, slots=True)
class PromotionPreview:
    task_id: str
    candidate_id: str
    candidate_digest: str
    stable_state_digest: str
    selected_paths: tuple[str, ...]
    verification_refs: tuple[str, ...]
    preview_digest: str
    blocking_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("task_id", "candidate_id", "candidate_digest", "stable_state_digest", "preview_digest"):
            _required_id(getattr(self, name), name)
        for name in ("selected_paths", "verification_refs", "blocking_reasons"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or len(values) > 2048 or any(not isinstance(v, str) or not v for v in values):
                raise ValueError(f"{name} must be a bounded tuple of strings")
