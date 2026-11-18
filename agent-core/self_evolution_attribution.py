"""Read-only attribution of changes between trusted stable and sandbox roots.

This module produces evidence only. It never invokes Git, writes files, merges,
or grants authority to adopt a change.
"""

from __future__ import annotations

import hashlib
import contextlib
import hmac
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_PATHS = 1000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
_SEAL_KEY = secrets.token_bytes(32)
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


class AttributionError(ValueError):
    """Input or snapshot could not be handled without weakening the safety boundary."""


@dataclass(frozen=True)
class _FileState:
    status: str
    device: int | None = None
    inode: int | None = None
    size: int | None = None
    sha256: str | None = None
    detail: str | None = None
    parent_chain: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class _Record:
    path: str
    stable: _FileState
    sandbox: _FileState
    initially_diverged: bool


@dataclass(frozen=True)
class Baseline:
    """Opaque, process-sealed snapshot returned by :func:`capture_baseline`."""

    stable_root: str
    sandbox_root: str
    paths: tuple[str, ...]
    records: tuple[_Record, ...]
    stable_root_identity: tuple[int, int]
    sandbox_root_identity: tuple[int, int]
    _seal: str


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _state_payload(state: _FileState) -> dict[str, Any]:
    return {
        "status": state.status,
        "device": state.device,
        "inode": state.inode,
        "size": state.size,
        "sha256": state.sha256,
        "detail": state.detail,
        "parent_chain": state.parent_chain,
    }


def _seal_payload(
    stable_root: str,
    sandbox_root: str,
    paths: tuple[str, ...],
    records: tuple[_Record, ...],
    stable_identity: tuple[int, int],
    sandbox_identity: tuple[int, int],
) -> bytes:
    return _canonical({
        "stable_root": stable_root,
        "sandbox_root": sandbox_root,
        "paths": paths,
        "records": [
            {"path": record.path, "stable": _state_payload(record.stable),
             "sandbox": _state_payload(record.sandbox), "initially_diverged": record.initially_diverged}
            for record in records
        ],
        "stable_root_identity": stable_identity,
        "sandbox_root_identity": sandbox_identity,
    })


def _root(path: os.PathLike[str] | str) -> tuple[Path, tuple[int, int]]:
    root = Path(path).expanduser()
    if not root.is_absolute():
        raise AttributionError("roots_must_be_absolute")
    if os.name == "nt":
        from self_evolution_manifest import _windows_open_trusted_root, ManifestError
        try:
            with _windows_open_trusted_root(root) as (_, _, identity, _):
                return root, (identity[0], int.from_bytes(identity[1], "little"))
        except ManifestError as exc:
            raise AttributionError("root_not_a_safe_directory") from exc
    current = Path(root.anchor)
    try:
        for component in root.parts[1:]:
            current = current / component
            info = current.lstat()
            if _is_link_or_reparse(info):
                raise AttributionError("root_link_or_reparse_point")
        info = root.lstat()
    except AttributionError:
        raise
    except OSError as exc:
        raise AttributionError("root_unavailable") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise AttributionError("root_not_a_safe_directory")
    # Do not resolve through an ancestor after checking it: preserve the exact
    # spelling whose components were inspected, then bind its final identity.
    return root, (int(info.st_dev), int(info.st_ino))


def _is_link_or_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _validate_paths(paths: Any) -> tuple[str, ...]:
    if isinstance(paths, (str, bytes)):
        raise AttributionError("paths_must_be_a_sequence")
    try:
        values = list(paths)
    except TypeError as exc:
        raise AttributionError("paths_must_be_a_sequence") from exc
    if not values or len(values) > MAX_PATHS:
        raise AttributionError("path_count_out_of_range")
    normalized: list[str] = []
    for raw in values:
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise AttributionError("invalid_relative_path")
        if "\\" in raw or raw.startswith("/") or _DRIVE_PREFIX.match(raw):
            raise AttributionError("invalid_relative_path")
        parts = raw.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise AttributionError("invalid_relative_path")
        if any(_is_windows_path_alias(part) for part in parts):
            raise AttributionError("windows_path_alias_forbidden")
        if any(part.casefold() == ".git" for part in parts):
            raise AttributionError("git_metadata_path_forbidden")
        normalized.append(raw)
    if len(set(normalized)) != len(normalized):
        raise AttributionError("duplicate_paths")
    # Candidate attribution is deliberately portable across Windows/macOS/Linux;
    # case-fold aliases must not split ownership evidence for one logical path.
    folded = [value.casefold() for value in normalized]
    if len(set(folded)) != len(folded):
        raise AttributionError("path_aliases")
    return tuple(sorted(normalized))



def _is_windows_path_alias(part: str) -> bool:
    if ":" in part or part.endswith((".", " ")):
        return True
    stem = part.split(".", 1)[0].casefold()
    return stem in {"con", "prn", "aux", "nul"} or bool(re.fullmatch(r"(?:com|lpt)[1-9]", stem))

def _windows_read_state(root: Path, relative: str, budget: list[int],
                        expected_root: tuple[int, int]) -> _FileState:
    """Read using held relative handles; evidence is still point-in-time only."""
    from self_evolution_manifest import (
        ManifestError, _windows_open_trusted_root, _windows_open_relative,
        _windows_handle_info, _windows_default_streams, _windows_digest_handle,
    )
    chain: list[tuple[int, int]] = []
    try:
        with _windows_open_trusted_root(root) as (_, root_handle, root_id, _):
            if (root_id[0], int.from_bytes(root_id[1], "little")) != expected_root:
                return _FileState("unsafe", detail="root_identity_changed")
            with contextlib.ExitStack() as stack:
                parent = root_handle
                parts = relative.split("/")
                for index, part in enumerate(parts):
                    try:
                        child = stack.enter_context(_windows_open_relative(
                            parent, part, directory=None, share_access=0 if index == len(parts) - 1 else 7))
                    except ManifestError as exc:
                        if str(exc) == "windows_relative_open_failed_0xc0000034":
                            return _FileState("missing", parent_chain=tuple(chain))
                        if str(exc) == "link_or_reparse_point_detected":
                            return _FileState("unsafe", detail="link_or_reparse_point", parent_chain=tuple(chain))
                        return _FileState("unsafe", detail="relative_open_failed", parent_chain=tuple(chain))
                    identity, info = _windows_handle_info(child)
                    portable_id = (identity[0], int.from_bytes(identity[1], "little"))
                    if info["delete_pending"]:
                        return _FileState("unsafe", detail="entry_delete_pending", parent_chain=tuple(chain))
                    if index < len(parts) - 1:
                        if not info["directory"]:
                            return _FileState("unsafe", detail="parent_not_directory", parent_chain=tuple(chain))
                        chain.append(portable_id)
                        parent = child
                        continue
                    if info["directory"] or int(info["attributes"]) & 0x40:
                        return _FileState("unsafe", detail="not_regular_file", parent_chain=tuple(chain))
                    if info["links"] != 1:
                        return _FileState("unsafe", detail="hard_link_alias_detected", parent_chain=tuple(chain))
                    if int(info["size"]) < 0 or int(info["size"]) > MAX_FILE_BYTES or budget[0] + int(info["size"]) > MAX_TOTAL_BYTES:
                        return _FileState("unsafe", detail="size_limit_exceeded", parent_chain=tuple(chain))
                    streams = _windows_default_streams(child, directory=False)
                    size, digest, after = _windows_digest_handle(
                        child, info, budget, max_file_bytes=MAX_FILE_BYTES, max_total_bytes=MAX_TOTAL_BYTES)
                    if _windows_default_streams(child, directory=False) != streams:
                        return _FileState("unsafe", detail="file_changed_during_read", parent_chain=tuple(chain))
                    return _FileState("present", portable_id[0], portable_id[1], size, digest,
                                      parent_chain=tuple(chain))
    except ManifestError:
        return _FileState("unsafe", detail="handle_traversal_failed", parent_chain=tuple(chain))
    return _FileState("unsafe", detail="handle_traversal_failed", parent_chain=tuple(chain))


def _read_state(root: Path, relative: str, budget: list[int],
                expected_root: tuple[int, int]) -> _FileState:
    if os.name == "nt":
        return _windows_read_state(root, relative, budget, expected_root)
    # Name-based lstat/open/recheck cannot detect a swapped-back parent.
    # No equivalent audited POSIX handle traversal is implemented here.
    return _FileState("unsafe", detail="safe_relative_handle_traversal_unavailable")


def capture_baseline(
    stable_root: os.PathLike[str] | str,
    sandbox_root: os.PathLike[str] | str,
    paths: Any,
) -> Baseline:
    """Capture bounded read-only baseline evidence for explicit repository-relative paths."""
    relative_paths = _validate_paths(paths)
    stable, stable_identity = _root(stable_root)
    sandbox, sandbox_identity = _root(sandbox_root)
    if stable == sandbox or stable in sandbox.parents or sandbox in stable.parents:
        raise AttributionError("stable_and_sandbox_roots_must_not_overlap")
    budget = [0]
    records: list[_Record] = []
    for relative in relative_paths:
        stable_state = _read_state(stable, relative, budget, stable_identity)
        sandbox_state = _read_state(sandbox, relative, budget, sandbox_identity)
        diverged = (stable_state.status, stable_state.sha256) != (sandbox_state.status, sandbox_state.sha256)
        records.append(_Record(relative, stable_state, sandbox_state, diverged))
    record_tuple = tuple(records)
    payload = _seal_payload(str(stable), str(sandbox), relative_paths, record_tuple, stable_identity, sandbox_identity)
    seal = hmac.new(_SEAL_KEY, payload, hashlib.sha256).hexdigest()
    return Baseline(str(stable), str(sandbox), relative_paths, record_tuple, stable_identity, sandbox_identity, seal)


def _current_root_identity(path: str) -> tuple[int, int] | None:
    if os.name == "nt":
        try:
            return _root(path)[1]
        except AttributionError:
            return None
    try:
        info = Path(path).lstat()
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            return None
        return int(info.st_dev), int(info.st_ino)
    except OSError:
        return None


def _same_content(left: _FileState, right: _FileState) -> bool:
    return left.status == right.status and left.sha256 == right.sha256


def check_changes(baseline: Baseline) -> dict[str, Any]:
    """Report only captured paths at this instant; never whole-candidate safety or merge authority."""
    if not isinstance(baseline, Baseline):
        raise AttributionError("baseline_must_be_captured_in_this_process")
    payload = _seal_payload(
        baseline.stable_root, baseline.sandbox_root, baseline.paths, baseline.records,
        baseline.stable_root_identity, baseline.sandbox_root_identity,
    )
    expected = hmac.new(_SEAL_KEY, payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, baseline._seal):
        raise AttributionError("baseline_integrity_check_failed")

    stable_root_ok = _current_root_identity(baseline.stable_root) == baseline.stable_root_identity
    sandbox_root_ok = _current_root_identity(baseline.sandbox_root) == baseline.sandbox_root_identity
    root_reasons: list[str] = []
    if not stable_root_ok:
        root_reasons.append("stable_root_identity_changed")
    if not sandbox_root_ok:
        root_reasons.append("sandbox_root_identity_changed")

    budget = [0]
    changed: list[str] = []
    preexisting: list[str] = []
    blocked: list[str] = []
    reasons: dict[str, list[str]] = {}
    entries: list[dict[str, Any]] = []
    for record in baseline.records:
        current_stable = (
            _read_state(Path(baseline.stable_root), record.path, budget, baseline.stable_root_identity)
            if stable_root_ok else _FileState("unsafe", detail="root_identity_changed")
        )
        current_sandbox = (
            _read_state(Path(baseline.sandbox_root), record.path, budget, baseline.sandbox_root_identity)
            if sandbox_root_ok else _FileState("unsafe", detail="root_identity_changed")
        )
        why: list[str] = list(root_reasons)
        if current_stable.status == "unsafe":
            why.append("stable_path_unsafe")
            if current_stable.detail:
                why.append("stable_" + current_stable.detail)
        if current_sandbox.status == "unsafe":
            why.append("sandbox_path_unsafe")
            if current_sandbox.detail:
                why.append("sandbox_" + current_sandbox.detail)
        if current_stable.status == "missing" and record.stable.status == "present":
            why.append("stable_missing_after_baseline")
        if current_sandbox.status == "missing" and record.sandbox.status == "present":
            why.append("sandbox_missing_after_baseline")
        stable_changed = not _same_content(record.stable, current_stable)
        if record.stable.parent_chain != current_stable.parent_chain:
            why.append("stable_parent_path_identity_changed")
        if stable_changed:
            why.append("stable_changed")
        if record.stable.status == "present" and current_stable.status == "present" and (record.stable.device, record.stable.inode) != (current_stable.device, current_stable.inode):
            why.append("stable_file_identity_changed")
        sandbox_changed = not _same_content(record.sandbox, current_sandbox)
        if record.sandbox.parent_chain != current_sandbox.parent_chain:
            why.append("sandbox_parent_path_identity_changed")
        if record.sandbox.status == "present" and current_sandbox.status == "present" and (record.sandbox.device, record.sandbox.inode) != (current_sandbox.device, current_sandbox.inode):
            sandbox_changed = True
            why.append("sandbox_file_identity_changed")
        if record.initially_diverged:
            preexisting.append(record.path)
            why.append("sandbox_already_diverged_at_baseline")
        if sandbox_changed:
            changed.append(record.path)
        if record.initially_diverged and sandbox_changed:
            why.append("preexisting_and_new_changes_mixed_file_blocked")
        if why:
            blocked.append(record.path)
            reasons[record.path] = sorted(set(why))
        entries.append({
            "path": record.path,
            "baseline": {"stable": _state_payload(record.stable), "sandbox": _state_payload(record.sandbox)},
            "current": {"stable": _state_payload(current_stable), "sandbox": _state_payload(current_sandbox)},
            "initially_diverged": record.initially_diverged,
            "sandbox_changed_since_baseline": sandbox_changed,
            "stable_changed_since_baseline": stable_changed,
            "reasons": sorted(set(why)),
        })

    body: dict[str, Any] = {
        "schema": "self_evolution_attribution_v1",
        "evidence_scope": "declared_paths_point_in_time_only",
        "full_candidate_coverage": False,
        "safe_to_consider": not blocked,
        "merge_authorized": False,
        "changed_paths": sorted(changed),
        "preexisting_paths": sorted(preexisting),
        "blocked_paths": sorted(set(blocked)),
        "reasons": {key: reasons[key] for key in sorted(reasons)},
        "entries": entries,
        "limits": {"max_paths": MAX_PATHS, "max_file_bytes": MAX_FILE_BYTES, "max_total_bytes": MAX_TOTAL_BYTES},
    }
    body["summary_sha256"] = hashlib.sha256(_canonical(body)).hexdigest()
    return body
