"""Policy-gated publication of verified two-lane incident-repair postimages.

The helper only publishes modifications to existing regular files. It never
creates commits, updates a remote, or resets a worktree. Callers must supply
independently verified HEADs and per-lane SHA-256 digests for every old file.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping


@dataclass(frozen=True)
class IncidentRepairPolicy:
    """Explicit publication gate and the independently registered sandbox root."""

    publish_enabled: bool
    registered_sandbox_root: Path | None


@dataclass
class LaneOutcome:
    status: str
    reason: str = ""


@dataclass
class IncidentRepairResult:
    lanes: dict[str, LaneOutcome]
    # Original bytes are included only when safe automatic recovery was impossible.
    recovery_material: dict[str, dict[str, bytes]] = field(default_factory=dict)


class _Refused(Exception):
    pass


_LANES = ("stable", "sandbox")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9._/-]+$")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _git(root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        completed = subprocess.run(
            ["git", "--literal-pathspecs", "-C", str(root), *args], check=True, text=True, timeout=12,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _Refused(f"git check failed: {getattr(exc, 'stderr', '') or exc}") from exc
    return completed.stdout.strip()


def _canonical_root(value: Path | str) -> Path:
    root = Path(value).expanduser().absolute()
    for component in (root, *root.parents):
        try:
            st = component.lstat()
        except OSError as exc:
            raise _Refused(f"repository root is unavailable: {component}") from exc
        if stat.S_ISLNK(st.st_mode) or (getattr(st, "st_file_attributes", 0) & _REPARSE):
            raise _Refused(f"repository root traverses a link/reparse point: {component}")
    if not root.is_dir():
        raise _Refused(f"repository root is not a directory: {root}")
    return root.resolve(strict=True)


def _normalize_allowed(paths: set[str] | frozenset[str]) -> set[str]:
    if not paths:
        raise _Refused("allowed_paths must be non-empty")
    normalized: set[str] = set()
    for raw in paths:
        if not isinstance(raw, str) or not _SAFE_PATH.fullmatch(raw):
            raise _Refused(f"invalid permitted path: {raw!r}")
        path = raw.replace("\\", "/")
        parts = path.split("/")
        if path.startswith("/") or any(p in ("", ".", "..") or p.lower() == ".git" for p in parts) or ":" in path:
            raise _Refused(f"unsafe permitted path: {raw!r}")
        normalized.add(path)
    return normalized


def _safe_target(root: Path, relative: str) -> Path:
    target = root
    for part in relative.split("/"):
        target = target / part
        try:
            st = target.lstat()
        except OSError as exc:
            raise _Refused(f"target path component is missing: {relative}") from exc
        if stat.S_ISLNK(st.st_mode) or (getattr(st, "st_file_attributes", 0) & _REPARSE):
            raise _Refused(f"target traverses a link/reparse point: {relative}")
        if target != root / relative and not stat.S_ISDIR(st.st_mode):
            raise _Refused(f"target parent is not a directory: {relative}")
    if not stat.S_ISREG(target.lstat().st_mode):
        raise _Refused(f"target is not a regular file: {relative}")
    try:
        target.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise _Refused(f"target escapes repository root: {relative}") from exc
    return target


def _status_targets(root: Path, paths: list[str]) -> None:
    for rel in paths:
        tracked = _git(root, "ls-files", "-v", "--error-unmatch", "--", rel)
        if tracked != "H " + rel:
            raise _Refused(f"patch target is not tracked at the verified HEAD: {rel}")
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all", "--", *paths)
    if status:
        raise _Refused(f"patch target has staged or unstaged changes: {status.splitlines()[0]}")


def _validate_lane(
    lane: str,
    root_value: Path | str,
    expected_head: str,
    expected_hashes: Mapping[str, str],
    snapshots: Mapping[str, bytes],
    changes: Mapping[str, bytes],
    paths: list[str],
    common_dir: str | None = None,
) -> tuple[Path, dict[str, bytes], dict[str, bytes], dict[str, int], str]:
    root = _canonical_root(root_value)
    top = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    if top != root:
        raise _Refused(f"{lane} root is not the Git worktree root")
    head = _git(root, "rev-parse", "HEAD")
    if not expected_head or head != expected_head:
        raise _Refused(f"{lane} HEAD differs from independently verified expected HEAD")
    common_value = Path(_git(root, "rev-parse", "--git-common-dir"))
    common = str((common_value if common_value.is_absolute() else root / common_value).resolve())
    if common_dir is not None and os.path.normcase(common) != os.path.normcase(common_dir):
        raise _Refused("sandbox is not registered in the stable repository's Git worktree set")
    if set(expected_hashes) != set(paths) or set(snapshots) != set(paths) or set(changes) != set(paths):
        raise _Refused(f"{lane} snapshot, digest, and postimage maps must exactly match permitted paths")
    _status_targets(root, paths)
    old: dict[str, bytes] = {}
    new: dict[str, bytes] = {}
    modes: dict[str, int] = {}
    for rel in paths:
        if not isinstance(snapshots[rel], bytes) or not isinstance(changes[rel], bytes):
            raise _Refused(f"{lane} snapshots and postimages must be bytes: {rel}")
        target = _safe_target(root, rel)
        snapshot = snapshots[rel]
        if hashlib.sha256(snapshot).hexdigest().lower() != str(expected_hashes[rel]).lower():
            raise _Refused(f"{lane} supplied original snapshot digest mismatch: {rel}")
        if target.read_bytes() != snapshot:
            raise _Refused(f"{lane} current file differs from supplied original snapshot: {rel}")
        if snapshot == changes[rel]:
            raise _Refused(f"{lane} postimage does not change target: {rel}")
        old[rel] = snapshot
        new[rel] = changes[rel]
        modes[rel] = stat.S_IMODE(target.stat().st_mode)
    return root, old, new, modes, common


def _atomic_write(target: Path, content: bytes, mode: int) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.incident-repair-", dir=target.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, mode)
        os.replace(temp, target)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def publish_incident_repair(
    stable_root: Path | str,
    sandbox_root: Path | str | None,
    *,
    allowed_paths: set[str] | frozenset[str],
    expected_heads: Mapping[Literal["stable", "sandbox"], str],
    expected_old_sha256: Mapping[Literal["stable", "sandbox"], Mapping[str, str]],
    snapshots_by_lane: Mapping[Literal["stable", "sandbox"], Mapping[str, bytes]],
    changes_by_lane: Mapping[Literal["stable", "sandbox"], Mapping[str, bytes]],
    policy: IncidentRepairPolicy,
) -> IncidentRepairResult:
    """Publish verified lane-specific postimages to stable and registered sandbox.

    The producer must capture both lanes' original bytes before independent
    revision checks and construct each postimage from exact-anchor replacements.
    The helper verifies the supplied snapshots and hashes against both current
    trees, then publishes only the exact allowlisted paths. No patch parser,
    commit, remote update, reset, or broad rollback is involved.
    """
    candidate = lambda why: IncidentRepairResult({lane: LaneOutcome("candidate", why) for lane in _LANES})
    if not isinstance(policy, IncidentRepairPolicy) or policy.publish_enabled is not True:
        return candidate("publication policy is not enabled")
    if sandbox_root is None or policy.registered_sandbox_root is None:
        return candidate("sandbox lane is not configured/registered")
    try:
        stable_canonical = _canonical_root(stable_root)
        sandbox_canonical = _canonical_root(sandbox_root)
        registered = _canonical_root(policy.registered_sandbox_root)
        if sandbox_canonical != registered:
            return candidate("sandbox root does not match registered sandbox root")
        if set(expected_heads) != set(_LANES) or set(expected_old_sha256) != set(_LANES):
            raise _Refused("expected HEAD and digest maps must contain exactly stable and sandbox")
        if set(snapshots_by_lane) != set(_LANES) or set(changes_by_lane) != set(_LANES):
            raise _Refused("both lane snapshots and postimages must be supplied before validation")
        allowlist = _normalize_allowed(allowed_paths)
        lane_paths = {lane: set(snapshots_by_lane[lane]) for lane in _LANES}
        if not lane_paths["stable"] or lane_paths["stable"] != lane_paths["sandbox"]:
            raise _Refused("both lanes must have the same non-empty set of target paths")
        paths = sorted(lane_paths["stable"])
        if not set(paths) <= allowlist:
            raise _Refused("snapshot/postimage contains a path outside the exact allowlist")
        if any(set(changes_by_lane[lane]) != set(paths) for lane in _LANES):
            raise _Refused("each lane postimage map must exactly match snapshot paths")
        registered_marker = False
        for marker in ("evolve/_index.toml", "evolve/_index.core.toml"):
            try:
                _safe_target(sandbox_canonical, marker)
                registered_marker = True
                break
            except _Refused:
                continue
        if not registered_marker:
            return candidate("sandbox agent-root registration markers are absent or unsafe")
        stable_common_value = Path(_git(stable_canonical, "rev-parse", "--git-common-dir"))
        stable_common = str((stable_common_value if stable_common_value.is_absolute() else stable_canonical / stable_common_value).resolve())
        registered_worktrees = _git(stable_canonical, "worktree", "list", "--porcelain")
        registered_paths = {
            str(Path(line[9:]).resolve())
            for line in registered_worktrees.splitlines()
            if line.startswith("worktree ")
        }
        if os.path.normcase(str(sandbox_canonical)) not in {os.path.normcase(p) for p in registered_paths}:
            return candidate("sandbox is not listed as a registered Git worktree")
        lanes: dict[str, tuple[Path, dict[str, bytes], dict[str, bytes], dict[str, int], str]] = {}
        # Both original snapshots are now present; perform independent lane checks.
        for lane, root in (("stable", stable_canonical), ("sandbox", sandbox_canonical)):
            lanes[lane] = _validate_lane(
                lane, root, expected_heads[lane], expected_old_sha256[lane],
                snapshots_by_lane[lane], changes_by_lane[lane], paths,
                stable_common if lane == "sandbox" else None,
            )
    except (_Refused, TypeError, ValueError, OSError) as exc:
        return IncidentRepairResult({lane: LaneOutcome("refused", str(exc)) for lane in _LANES})

    # Do not run concurrent publication from the other lane's sidecar. A stale
    # lock after process death is deliberately fail-closed, not auto-deleted.
    lock = Path(stable_common) / "incident-repair-publish.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError:
        return candidate("publication lock is unavailable")
    identity = None
    try:
        identity = os.fstat(fd)
        return _publish_locked(lanes, expected_heads, expected_old_sha256,
                               snapshots_by_lane, changes_by_lane, paths, stable_common)
    finally:
        os.close(fd)
        try:
            if identity is not None and os.path.samestat(identity, lock.lstat()):
                lock.unlink()
        except OSError:
            pass


def _publish_locked(lanes, expected_heads, expected_old_sha256,
                    snapshots_by_lane, changes_by_lane, paths, stable_common):
    # Revalidate all lane snapshots, HEADs, target cleanliness and containment before writes.
    try:
        for lane in _LANES:
            root, _, _, _, _ = lanes[lane]
            _validate_lane(
                lane, root, expected_heads[lane], expected_old_sha256[lane],
                snapshots_by_lane[lane], changes_by_lane[lane], paths,
                stable_common if lane == "sandbox" else None,
            )
    except _Refused as exc:
        return IncidentRepairResult({lane: LaneOutcome("refused", f"revalidation failed: {exc}") for lane in _LANES})

    applied: list[tuple[str, str]] = []
    try:
        for lane in _LANES:
            root, old, new, modes, _ = lanes[lane]
            if _git(root, "rev-parse", "HEAD") != expected_heads[lane]:
                raise _Refused(f"{lane} HEAD changed during publication")
            _status_targets(root, paths)
            for rel in paths:
                target = _safe_target(root, rel)
                if target.read_bytes() != old[rel]:
                    raise _Refused(f"{lane} target changed after revalidation: {rel}")
                _status_targets(root, [rel])
                _atomic_write(target, new[rel], modes[rel])
                applied.append((lane, rel))
        for lane in _LANES:
            root, _, new, _, _ = lanes[lane]
            if (_git(root, "rev-parse", "HEAD") != expected_heads[lane]
                    or any(_safe_target(root, rel).read_bytes() != new[rel] for rel in paths)):
                raise _Refused(f"{lane} HEAD or repair postimage changed during publication")
    except Exception as exc:
        recovery: dict[str, dict[str, bytes]] = {}
        restore_ok: dict[str, bool] = {lane: True for lane in _LANES}
        for lane, rel in reversed(applied):
            root, old, new, modes, _ = lanes[lane]
            target = root / rel
            try:
                target = _safe_target(root, rel)
                if (_git(root, "rev-parse", "HEAD") != expected_heads[lane]
                        or _git(root, "diff", "--cached", "--name-only", "--", rel)
                        or target.read_bytes() != new[rel]):
                    raise _Refused("repair target is no longer owned by this write")
                _atomic_write(target, old[rel], modes[rel])
            except (OSError, _Refused):
                restore_ok[lane] = False
                recovery.setdefault(lane, {})[rel] = old[rel]
        reason = f"write failed; compare-before-restore recovery attempted: {exc}"
        outcomes = {lane: LaneOutcome("recovered" if restore_ok[lane] else "partial", reason) for lane in _LANES}
        for lane in _LANES:
            if not any(item_lane == lane for item_lane, _ in applied):
                outcomes[lane] = LaneOutcome("refused", reason)
        return IncidentRepairResult(outcomes, recovery)
    return IncidentRepairResult({lane: LaneOutcome("published") for lane in _LANES})
