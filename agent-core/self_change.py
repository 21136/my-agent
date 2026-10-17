"""Safe self-modification transaction state and evidence model."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from enum import StrEnum
from typing import Any, Mapping


class SelfChangeError(ValueError):
    """Invalid self-change transaction or unsafe transition."""


class SelfChangeState(StrEnum):
    REQUESTED = "requested"
    SCOPED = "scoped"
    SNAPSHOTTED = "snapshotted"
    BRANCHED = "branched"
    EDITING = "editing"
    LOCAL_VERIFYING = "local_verifying"
    CANDIDATE_READY = "candidate_ready"
    RESTART_PENDING = "restart_pending"
    HEALTH_CHECKING = "health_checking"
    ACCEPTED = "accepted"
    COMMITTED = "committed"
    PUSHED = "pushed"
    ROLLED_BACK = "rolled_back"
    ABORTED = "aborted"
    BLOCKED = "blocked"


_ALLOWED_TRANSITIONS: dict[SelfChangeState, frozenset[SelfChangeState]] = {
    SelfChangeState.REQUESTED: frozenset({SelfChangeState.SCOPED, SelfChangeState.ABORTED, SelfChangeState.BLOCKED}),
    SelfChangeState.SCOPED: frozenset({SelfChangeState.SNAPSHOTTED, SelfChangeState.ROLLED_BACK, SelfChangeState.ABORTED, SelfChangeState.BLOCKED}),
    SelfChangeState.SNAPSHOTTED: frozenset({SelfChangeState.BRANCHED, SelfChangeState.ROLLED_BACK, SelfChangeState.ABORTED, SelfChangeState.BLOCKED}),
    SelfChangeState.BRANCHED: frozenset({SelfChangeState.EDITING, SelfChangeState.ROLLED_BACK, SelfChangeState.ABORTED, SelfChangeState.BLOCKED}),
    SelfChangeState.EDITING: frozenset({SelfChangeState.LOCAL_VERIFYING, SelfChangeState.ROLLED_BACK, SelfChangeState.ABORTED, SelfChangeState.BLOCKED}),
    SelfChangeState.LOCAL_VERIFYING: frozenset({SelfChangeState.CANDIDATE_READY, SelfChangeState.ROLLED_BACK, SelfChangeState.ABORTED, SelfChangeState.BLOCKED}),
    SelfChangeState.CANDIDATE_READY: frozenset({SelfChangeState.RESTART_PENDING, SelfChangeState.ACCEPTED, SelfChangeState.ROLLED_BACK, SelfChangeState.ABORTED, SelfChangeState.BLOCKED}),
    SelfChangeState.RESTART_PENDING: frozenset({SelfChangeState.HEALTH_CHECKING, SelfChangeState.ROLLED_BACK, SelfChangeState.ABORTED, SelfChangeState.BLOCKED}),
    SelfChangeState.HEALTH_CHECKING: frozenset({SelfChangeState.ACCEPTED, SelfChangeState.ROLLED_BACK, SelfChangeState.ABORTED, SelfChangeState.BLOCKED}),
    SelfChangeState.ACCEPTED: frozenset({SelfChangeState.COMMITTED, SelfChangeState.PUSHED, SelfChangeState.ROLLED_BACK, SelfChangeState.BLOCKED}),
    SelfChangeState.COMMITTED: frozenset({SelfChangeState.PUSHED, SelfChangeState.ROLLED_BACK, SelfChangeState.BLOCKED}),
    SelfChangeState.PUSHED: frozenset({SelfChangeState.ROLLED_BACK, SelfChangeState.BLOCKED}),
    SelfChangeState.ROLLED_BACK: frozenset(),
    SelfChangeState.ABORTED: frozenset(),
    SelfChangeState.BLOCKED: frozenset({SelfChangeState.SCOPED, SelfChangeState.ROLLED_BACK, SelfChangeState.ABORTED}),
}



@dataclass(frozen=True, slots=True)
class BaselineSnapshot:
    root: str
    base_ref: str
    owned_hashes: dict[str, str]
    pre_existing_dirty: tuple[str, ...]
    untracked_paths: tuple[str, ...]


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise SelfChangeError(
            f"git command failed ({completed.returncode}): git {' '.join(args)}\n"
            f"{(completed.stderr or completed.stdout or '').strip()[:500]}"
        )
    return completed.stdout or ""


def _sha256(path: Path) -> str:
    if not path.is_file():
        return "deleted"
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _status_paths(root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    output = _git(root, "status", "--porcelain", "--untracked-files=all")
    dirty: list[str] = []
    untracked: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        raw_path = line[3:].strip()
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1]
        normalized = raw_path.replace("\\", "/")
        if normalized not in dirty:
            dirty.append(normalized)
        if line.startswith("??") and normalized not in untracked:
            untracked.append(normalized)
    return tuple(dirty), tuple(untracked)


def capture_baseline(
    *,
    root: Path,
    owned_paths: tuple[str, ...] | list[str],
    base_ref: str = "HEAD",
) -> BaselineSnapshot:
    root = Path(root).resolve()
    _git(root, "rev-parse", "--show-toplevel")
    normalized = _normalize_paths(tuple(owned_paths))
    dirty, untracked = _status_paths(root)
    owned_set = set(normalized)
    hashes = {path: _sha256(root / path) for path in normalized}
    pre_existing = tuple(path for path in dirty if path not in owned_set)
    return BaselineSnapshot(
        root=str(root),
        base_ref=_safe_text(base_ref, field="base_ref"),
        owned_hashes=hashes,
        pre_existing_dirty=pre_existing,
        untracked_paths=untracked,
    )






@dataclass(frozen=True, slots=True)
class CommandEvidence:
    command: tuple[str, ...]
    cwd: str
    exit_code: int
    stdout_tail: str
    stderr_tail: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True, slots=True)
class VerificationSummary:
    status: str
    results: tuple[CommandEvidence, ...]

    @property
    def ok(self) -> bool:
        return self.status == "passed" and all(item.ok for item in self.results)


@dataclass(frozen=True, slots=True)
class HealthCheck:
    ok: bool
    command: tuple[str, ...]
    cwd: str
    exit_code: int | None
    timed_out: bool
    stdout_tail: str
    stderr_tail: str


def _redact(text: str) -> str:
    return re.sub(
        r"(?i)(token|secret|password|api[_-]?key)\s*[:=]\s*[^\s]+",
        r"\1=<redacted>",
        text or "",
    )[-4000:]


def run_local_verification(
    tx: SelfChangeTransaction,
    *,
    commands: tuple[tuple[str, ...], ...] | list[tuple[str, ...]],
) -> VerificationSummary:
    if tx.state != SelfChangeState.LOCAL_VERIFYING:
        raise SelfChangeError("local verification requires local_verifying state")
    results: list[CommandEvidence] = []
    for command in commands:
        argv = tuple(str(item) for item in command if str(item))
        if not argv:
            raise SelfChangeError("verification command must not be empty")
        completed = subprocess.run(
            list(argv),
            cwd=tx.target_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        results.append(
            CommandEvidence(
                command=argv,
                cwd=tx.target_root,
                exit_code=completed.returncode,
                stdout_tail=_redact(completed.stdout),
                stderr_tail=_redact(completed.stderr),
            )
        )
    status = "passed" if results and all(item.ok for item in results) else "failed"
    return VerificationSummary(status=status, results=tuple(results))


def check_candidate_health(
    *,
    command: tuple[str, ...] | list[str],
    cwd: Path,
    timeout_sec: float,
) -> HealthCheck:
    argv = tuple(str(item) for item in command if str(item))
    if not argv:
        raise SelfChangeError("health command must not be empty")
    try:
        completed = subprocess.run(
            list(argv),
            cwd=Path(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(0.1, float(timeout_sec)),
        )
    except subprocess.TimeoutExpired as exc:
        return HealthCheck(
            ok=False,
            command=argv,
            cwd=str(Path(cwd)),
            exit_code=None,
            timed_out=True,
            stdout_tail=_redact(str(exc.stdout or "")),
            stderr_tail=_redact(str(exc.stderr or "")),
        )
    return HealthCheck(
        ok=completed.returncode == 0,
        command=argv,
        cwd=str(Path(cwd)),
        exit_code=completed.returncode,
        timed_out=False,
        stdout_tail=_redact(completed.stdout),
        stderr_tail=_redact(completed.stderr),
    )

def prepare_branch(
    tx: SelfChangeTransaction,
    *,
    confirm: bool,
) -> SelfChangeTransaction:
    """Create the transaction branch only after explicit confirmation."""
    if tx.state != SelfChangeState.SNAPSHOTTED:
        raise SelfChangeError("branch preparation requires snapshotted state")
    if not confirm:
        return tx
    root = Path(tx.target_root).resolve()
    branches = _git(root, "branch", "--format=%(refname:short)").splitlines()
    if tx.work_branch in {item.strip() for item in branches}:
        raise SelfChangeError(f"transaction branch already exists: {tx.work_branch}")
    _git(root, "switch", "--create", tx.work_branch, tx.base_ref)
    return tx.advance(SelfChangeState.BRANCHED)


def verify_owned_paths(tx: SelfChangeTransaction) -> tuple[str, ...]:
    root = Path(tx.target_root).resolve()
    changed = tuple(
        path for path in tx.owned_paths
        if _sha256(root / path) != tx.baseline_hashes.get(path, "deleted")
    )
    return changed


def rollback_transaction(
    tx: SelfChangeTransaction,
    *,
    expected_hashes: Mapping[str, str],
    confirm: bool,
) -> SelfChangeTransaction:
    """Restore only owned paths when the current hashes still match expectation."""
    if tx.state not in {
        SelfChangeState.CANDIDATE_READY,
        SelfChangeState.RESTART_PENDING,
        SelfChangeState.HEALTH_CHECKING,
        SelfChangeState.ACCEPTED,
        SelfChangeState.COMMITTED,
        SelfChangeState.PUSHED,
    }:
        raise SelfChangeError(f"rollback is not allowed from {tx.state.value}")
    current = {path: _sha256(Path(tx.target_root) / path) for path in tx.owned_paths}
    for path in tx.owned_paths:
        expected = expected_hashes.get(path)
        if expected is None or current[path] != expected:
            raise SelfChangeError(f"rollback expected hash mismatch: {path}")
    if not confirm:
        return tx
    _git(
        Path(tx.target_root),
        "restore",
        "--source",
        tx.rollback_target,
        "--worktree",
        "--staged",
        "--",
        *tx.owned_paths,
    )
    return tx.advance(
        SelfChangeState.ROLLED_BACK,
        changed_paths=(),
        failure_reason="explicit self-change rollback",
    )

def _transaction_dir(paths: object) -> Path:
    data_root = Path(getattr(paths, "data"))
    directory = data_root / "self_change"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def save_transaction(paths: object, tx: SelfChangeTransaction) -> Path:
    destination = _transaction_dir(paths) / f"{tx.transaction_id}.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(tx.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination


def load_transaction(paths: object, transaction_id: str) -> SelfChangeTransaction:
    safe_id = _safe_text(transaction_id, field="transaction_id").strip()
    if not safe_id or "/" in safe_id or "\\" in safe_id or safe_id in {".", ".."}:
        raise SelfChangeError("invalid transaction_id")
    path = _transaction_dir(paths) / f"{safe_id}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SelfChangeError(f"transaction not found: {safe_id}") from exc
    except json.JSONDecodeError as exc:
        raise SelfChangeError(f"invalid transaction JSON: {safe_id}") from exc
    return SelfChangeTransaction.from_dict(payload)

_SECRET_LIKE_RE = re.compile(
    r"(?:ghp_[A-Za-z0-9_\-]{8,}|github_pat_[A-Za-z0-9_\-]{8,}|sk-[A-Za-z0-9_\-]{8,}|(?:token|secret|password|api[_-]?key)\s*[:=])",
    re.IGNORECASE,
)


def _safe_text(value: str, *, field: str) -> str:
    if not isinstance(value, str):
        raise SelfChangeError(f"{field} must be a string")
    if _SECRET_LIKE_RE.search(value):
        raise SelfChangeError(f"{field} looks like secret material")
    return value


def _normalize_paths(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    result: list[str] = []
    for raw in values:
        value = _safe_text(str(raw), field="path").strip().replace("\\", "/")
        if not value:
            raise SelfChangeError("paths must not be empty")
        if value.startswith("/") or ":" in value[:3] or ".." in value.split("/"):
            raise SelfChangeError(f"path must be relative and contained: {value!r}")
        if value not in result:
            result.append(value)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class SelfChangeTransaction:
    transaction_id: str
    run_id: str
    source_root: str
    target_root: str
    base_ref: str
    work_branch: str
    owned_paths: tuple[str, ...]
    baseline_hashes: dict[str, str]
    changed_paths: tuple[str, ...]
    rollback_target: str
    state: SelfChangeState
    failure_reason: str = ""

    def __post_init__(self) -> None:
        for field_name in ("transaction_id", "run_id", "source_root", "target_root", "base_ref", "work_branch", "rollback_target", "failure_reason"):
            _safe_text(str(getattr(self, field_name)), field=field_name)
        object.__setattr__(self, "owned_paths", _normalize_paths(self.owned_paths))
        object.__setattr__(self, "changed_paths", _normalize_paths(self.changed_paths))
        if not isinstance(self.baseline_hashes, dict):
            raise SelfChangeError("baseline_hashes must be a dict")
        for path, digest in self.baseline_hashes.items():
            normalized = _normalize_paths((path,))[0]
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}|deleted", digest):
                raise SelfChangeError(f"invalid baseline hash for {normalized!r}")
        if not isinstance(self.state, SelfChangeState):
            object.__setattr__(self, "state", SelfChangeState(str(self.state)))

    def advance(self, state: SelfChangeState, **updates: Any) -> "SelfChangeTransaction":
        target = SelfChangeState(state)
        if target not in _ALLOWED_TRANSITIONS[self.state]:
            raise SelfChangeError(f"invalid transition {self.state.value} -> {target.value}")
        next_values: dict[str, Any] = {"state": target, **updates}
        if target in {SelfChangeState.BLOCKED, SelfChangeState.ABORTED, SelfChangeState.ROLLED_BACK}:
            reason = str(next_values.get("failure_reason", self.failure_reason) or "").strip()
            if not reason:
                raise SelfChangeError(f"{target.value} requires failure_reason")
            next_values["failure_reason"] = reason
        if target == SelfChangeState.ROLLED_BACK:
            rollback_target = str(next_values.get("rollback_target", self.rollback_target) or "").strip()
            if not rollback_target:
                raise SelfChangeError("rolled_back requires rollback_target")
            next_values["rollback_target"] = rollback_target
        return replace(self, **next_values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "run_id": self.run_id,
            "source_root": self.source_root,
            "target_root": self.target_root,
            "base_ref": self.base_ref,
            "work_branch": self.work_branch,
            "owned_paths": list(self.owned_paths),
            "baseline_hashes": dict(self.baseline_hashes),
            "changed_paths": list(self.changed_paths),
            "rollback_target": self.rollback_target,
            "state": self.state.value,
            "failure_reason": self.failure_reason,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SelfChangeTransaction":
        required = (
            "transaction_id", "run_id", "source_root", "target_root", "base_ref",
            "work_branch", "owned_paths", "baseline_hashes", "changed_paths",
            "rollback_target", "state",
        )
        missing = [key for key in required if key not in payload]
        if missing:
            raise SelfChangeError(f"missing transaction fields: {', '.join(missing)}")
        return cls(
            transaction_id=str(payload["transaction_id"]),
            run_id=str(payload["run_id"]),
            source_root=str(payload["source_root"]),
            target_root=str(payload["target_root"]),
            base_ref=str(payload["base_ref"]),
            work_branch=str(payload["work_branch"]),
            owned_paths=tuple(str(item) for item in payload["owned_paths"]),
            baseline_hashes={str(key): str(value) for key, value in dict(payload["baseline_hashes"]).items()},
            changed_paths=tuple(str(item) for item in payload["changed_paths"]),
            rollback_target=str(payload["rollback_target"]),
            state=SelfChangeState(str(payload["state"])),
            failure_reason=str(payload.get("failure_reason", "")),
        )
