"""Host-only scoped review of a live, non-Git candidate against frozen bytes.

Capture *before* dispatching the worker. The caller must establish candidate
identity, lifetime, baseline provenance, and disclosure authorization outside
this module. A point-in-time manifest is not an atomic snapshot; these results
are not an authority to execute or merge. Full-tree coverage is reported only
when the sealed manifest traversed the entire candidate without exclusions.
Windows only, reusing the manifest's handle-relative traversal and stream checks.
Only bounded UTF-8 text is retained. Larger or non-text files are represented by
verified manifest hashes and identities, not content. A changed such file makes
content review incomplete, even if its path was approved for disclosure.
"""
from __future__ import annotations

import contextlib
import difflib
import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass, field
from typing import Any

import self_evolution_manifest as manifest

_MAX_DISCLOSURE_PATHS = 256
_MAX_TREE_FILES = 8192
_MAX_FILE_BYTES = 1024 * 1024
_MAX_TOTAL_BYTES = 32 * 1024 * 1024
_MAX_DIFF_BYTES = 256 * 1024
_KEY = secrets.token_bytes(32)


class ReviewError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class FrozenFile:
    path: str
    present: bool
    sha256: str | None
    file_identity: tuple[int, int] | None
    mode: int | None
    content: bytes | None = field(repr=False)
    content_status: str = "text"


@dataclass(frozen=True)
class FrozenBaseline:
    candidate_id: str
    root: str = field(repr=False)
    root_identity: tuple[int, int]
    baseline_digest: str
    files: tuple[FrozenFile, ...] = field(repr=False)
    full_tree: bool
    _seal: str = field(repr=False)


def _fail(code: str) -> None:
    raise ReviewError(code) from None


def _paths(values: Any, *, limit: int = _MAX_DISCLOSURE_PATHS) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or not values or len(values) > limit:
        _fail("invalid_review_paths")
    try:
        return manifest.validate_relative_paths(values)
    except (manifest.ManifestError, UnicodeError, ValueError, TypeError):
        _fail("invalid_review_paths")


def _capture(root: str):
    try:
        value = manifest.capture_tree_manifest(root)
        manifest._require_captured_manifest(value)
    except (manifest.ManifestError, OSError, ValueError):
        _fail("candidate_capture_failed")
    if (not value.sweep_coverage_complete or not value.full_candidate_coverage or
            value.excluded_metadata):
        _fail("candidate_capture_scope_unsupported")
    return value


def _seal_payload(candidate_id: str, root: str, root_identity: tuple[int, int],
                  digest: str, files: tuple[FrozenFile, ...], full_tree: bool) -> bytes:
    return json.dumps({
        "schema": "self_evolution_frozen_baseline_v2",
        "candidate_id": candidate_id, "root": root, "root_identity": root_identity,
        "baseline_digest": digest, "full_tree": full_tree,
        "files": [{"path": item.path, "present": item.present, "sha256": item.sha256,
                   "content_sha256": (hashlib.sha256(item.content).hexdigest()
                                      if item.content is not None else None),
                   "content_status": item.content_status,
                   "file_identity": item.file_identity, "mode": item.mode,
                   "size": len(item.content) if item.content is not None else None} for item in files],
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _read_bound_file(tree: manifest.TreeManifest, entry: manifest.TreeEntry) -> bytes:
    """Read an exact captured file using held Windows relative handles.

    The existing manifest reader has a self-comparison timestamp check; this
    implementation independently compares before/after metadata and hashes.
    """
    if os.name != "nt":
        _fail("safe_handle_read_unavailable")
    if entry.kind != "file" or entry.size > _MAX_FILE_BYTES:
        _fail("file_size_or_type_unsupported")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    parts = manifest.validate_relative_path(entry.path).split("/")
    entries = {item.path: item for item in tree.entries}
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    duplicate = kernel.DuplicateHandle
    duplicate.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                          ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                          wintypes.BOOL, wintypes.DWORD]
    duplicate.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    try:
        with manifest._windows_open_trusted_root(tree.root) as (_, root_handle, root_id, _):
            if (root_id[0], int.from_bytes(root_id[1], "little")) != tree.root_identity:
                _fail("candidate_identity_changed")
            with contextlib.ExitStack() as stack:
                parent = root_handle
                prefix = []
                for part in parts[:-1]:
                    prefix.append(part)
                    parent = stack.enter_context(manifest._windows_open_relative(parent, part, directory=True))
                    identity, info = manifest._windows_handle_info(parent)
                    expected = entries.get("/".join(prefix))
                    if (expected is None or expected.kind != "directory" or info["delete_pending"] or
                            (identity[0], int.from_bytes(identity[1], "little")) != (expected.device, expected.file_id)):
                        _fail("candidate_identity_changed")
                    manifest._windows_default_streams(parent, directory=True)
                handle = stack.enter_context(manifest._windows_open_relative(
                    parent, parts[-1], directory=False, share_access=0))
                identity, before = manifest._windows_handle_info(handle)
                if (before["directory"] or before["delete_pending"] or before["links"] != 1 or
                        before["size"] != entry.size or
                        (identity[0], int.from_bytes(identity[1], "little")) != (entry.device, entry.file_id)):
                    _fail("candidate_identity_changed")
                if manifest._windows_default_streams(handle, directory=False) != ("::$DATA",):
                    _fail("file_stream_unsupported")
                process = kernel.GetCurrentProcess()
                copied = wintypes.HANDLE()
                if not duplicate(process, handle, process, ctypes.byref(copied), 0, False, 2):
                    _fail("file_read_failed")
                try:
                    fd = msvcrt.open_osfhandle(copied.value, os.O_RDONLY | getattr(os, "O_BINARY", 0))
                except OSError:
                    kernel.CloseHandle(copied)
                    _fail("file_read_failed")
                with os.fdopen(fd, "rb") as stream:
                    content = stream.read(_MAX_FILE_BYTES + 1)
                after_identity, after = manifest._windows_handle_info(handle)
                if (after_identity != identity or any(before[key] != after[key] for key in
                    ("size", "write_time", "change_time", "links", "attributes", "directory", "delete_pending")) or
                        manifest._windows_default_streams(handle, directory=False) != ("::$DATA",) or
                        len(content) != entry.size or entry.sha256 is None or
                        not hmac.compare_digest(hashlib.sha256(content).hexdigest(), entry.sha256)):
                    _fail("candidate_changed_during_read")
                return content
    except ReviewError:
        raise
    except (manifest.ManifestError, OSError, ValueError):
        _fail("file_read_failed")


def _collect(tree: manifest.TreeManifest, paths: tuple[str, ...], *,
             enforce_tree_limit: bool = False) -> tuple[FrozenFile, ...]:
    entries = {entry.path: entry for entry in tree.entries}
    folded = {path.casefold(): path for path in entries}
    files = []
    total = 0
    if enforce_tree_limit and sum(entry.kind == "file" for entry in tree.entries) > _MAX_TREE_FILES:
        _fail("review_file_count_exceeded")
    for path in paths:
        parts = path.split("/")
        for i in range(1, len(parts) + 1):
            prefix = "/".join(parts[:i])
            actual = folded.get(prefix.casefold())
            if actual is not None and actual != prefix:
                _fail("selected_path_alias")
        entry = entries.get(path)
        if entry is None:
            files.append(FrozenFile(path, False, None, None, None, b"", "absent"))
            continue
        if entry.kind != "file":
            _fail("file_size_or_type_unsupported")
        # The handle-based manifest has already verified the complete file's
        # digest and identity. Oversized files are never loaded into memory.
        if entry.size > _MAX_FILE_BYTES:
            data, status = None, "over_text_limit"
        else:
            data = _read_bound_file(tree, entry)
            if _text(data) is None:
                data, status = None, "binary_or_unsupported"
            else:
                status = "text"
                total += len(data)
                if total > _MAX_TOTAL_BYTES:
                    _fail("review_total_size_exceeded")
        files.append(FrozenFile(path, True, entry.sha256, (entry.device, entry.file_id),
                                entry.mode, data, status))
    return tuple(files)


def freeze_baseline(*, candidate_root: os.PathLike[str] | str, candidate_id: str,
                    paths: tuple[str, ...] | None = None) -> FrozenBaseline:
    """Freeze a bounded full tree (or legacy explicit paths) before worker writes.

    ``paths=None`` covers every file observed in the candidate tree. Explicit
    paths retain the earlier scoped behavior. Host must derive the candidate
    root and ID from its authenticated materialization binding. This sweep is
    not atomic; neither root nor ID may come from model/tool input. Per-file and
    aggregate text budgets are separate from the manifest's filesystem budgets.
    """
    if not isinstance(candidate_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", candidate_id):
        _fail("invalid_candidate_id")
    full_tree = paths is None
    before = _capture(os.fspath(candidate_root))
    if full_tree:
        if sum(entry.kind == "file" for entry in before.entries) > _MAX_TREE_FILES:
            _fail("review_file_count_exceeded")
        selected = tuple(entry.path for entry in before.entries if entry.kind == "file")
    else:
        selected = _paths(paths)
    files = _collect(before, selected, enforce_tree_limit=full_tree)
    after = _capture(before.root)
    if after.digest != before.digest or after.root_identity != before.root_identity:
        _fail("candidate_changed_during_freeze")
    seal = hmac.new(_KEY, _seal_payload(candidate_id, before.root, before.root_identity,
                                       before.digest, files, full_tree), hashlib.sha256).hexdigest()
    return FrozenBaseline(candidate_id, before.root, before.root_identity, before.digest, files, full_tree, seal)


def _validate_baseline(baseline: FrozenBaseline, candidate_id: str) -> None:
    if type(baseline) is not FrozenBaseline or baseline.candidate_id != candidate_id:
        _fail("baseline_binding_mismatch")
    try:
        expected = hmac.new(_KEY, _seal_payload(baseline.candidate_id, baseline.root,
                            baseline.root_identity, baseline.baseline_digest, baseline.files,
                            baseline.full_tree), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, baseline._seal):
            _fail("baseline_integrity_failed")
    except (ValueError, TypeError, AttributeError):
        _fail("baseline_integrity_failed")


def _text(content: bytes | None) -> str | None:
    if content is None or b"\0" in content:
        return None
    try:
        value = content.decode("utf-8", "strict")
    except UnicodeError:
        return None
    if any(ord(ch) < 32 and ch not in "\r\n\t" or 127 <= ord(ch) <= 159 for ch in value):
        return None
    return value


def review_candidate(baseline: FrozenBaseline, *, candidate_id: str,
                     disclose_paths: tuple[str, ...] = (),
                     max_diff_bytes: int = _MAX_DIFF_BYTES) -> dict[str, Any]:
    """Compare frozen bytes to current uncommitted files, with optional hunks.

    Disclosure paths must be supplied only by trusted Host after a separate
    human authorization decision. No content classifier can guarantee secrets
    are absent. Output, including relative paths and hashes, is sensitive.
    ``content_complete`` concerns changed files in the requested scope; it is
    false for undisclosed, truncated or hash-only changes, not a merge approval.
    Per-side content status is absent, text, binary_or_unsupported, or
    over_text_limit (which does not classify the oversized file as binary).
    """
    _validate_baseline(baseline, candidate_id)
    if type(max_diff_bytes) is not int or not 0 < max_diff_bytes <= _MAX_DIFF_BYTES:
        _fail("invalid_diff_limit")
    if not isinstance(disclose_paths, (list, tuple)):
        _fail("invalid_review_paths")
    approved = set(_paths(disclose_paths)) if disclose_paths else set()
    before = _capture(baseline.root)
    if before.root_identity != baseline.root_identity:
        _fail("candidate_identity_changed")
    baseline_paths = tuple(item.path for item in baseline.files)
    if baseline.full_tree:
        current_paths = tuple(entry.path for entry in before.entries if entry.kind == "file")
        if len(current_paths) > _MAX_TREE_FILES:
            _fail("review_file_count_exceeded")
        paths = tuple(sorted(set(baseline_paths).union(current_paths), key=lambda p: (p.casefold(), p)))
    else:
        paths = baseline_paths
    if not approved.issubset(set(paths)):
        _fail("disclosure_path_outside_baseline")
    current = _collect(before, paths, enforce_tree_limit=baseline.full_tree)
    after = _capture(baseline.root)
    if before.digest != after.digest or before.root_identity != after.root_identity:
        _fail("candidate_changed_during_review")
    rows = []
    remaining = max_diff_bytes
    truncated = False
    content_complete = True
    old_by_path = {item.path: item for item in baseline.files}
    for new in current:
        old = old_by_path.get(new.path, FrozenFile(new.path, False, None, None, None, b"", "absent"))
        if (old.present, old.sha256, old.file_identity, old.mode) == (
                new.present, new.sha256, new.file_identity, new.mode):
            continue
        old_text = _text(old.content)
        new_text = _text(new.content)
        replaced = old.present and new.present and old.file_identity != new.file_identity
        row = {"path": old.path,
               "change": ("added" if not old.present else "deleted" if not new.present else
                          "replaced" if replaced else "modified"),
               "content_changed": (old.sha256 != new.sha256) if old.present and new.present else None,
               "before_sha256": old.sha256, "after_sha256": new.sha256,
               "before_file_identity": old.file_identity, "after_file_identity": new.file_identity,
               "before_mode": old.mode, "after_mode": new.mode,
               "before_content_status": old.content_status, "after_content_status": new.content_status,
               "content_kind": "text" if old_text is not None and new_text is not None else "binary_or_unsupported"}
        if old.path in approved and old_text is not None and new_text is not None:
            raw = "".join(difflib.unified_diff(old_text.splitlines(keepends=True),
                           new_text.splitlines(keepends=True), fromfile="before", tofile="after")).encode("utf-8")
            if len(raw) > remaining:
                truncated = True
                raw = raw[:remaining]
            diff = raw.decode("utf-8", "ignore")
            remaining -= len(diff.encode("utf-8"))
            row["diff"] = diff
        else:
            content_complete = False
        rows.append(row)
    # Formatting can take time; discard all results if the observed tree
    # changed while diff text was being constructed. Still not an atomic view.
    final = _capture(baseline.root)
    if final.digest != before.digest or final.root_identity != before.root_identity:
        _fail("candidate_changed_during_review")
    full_coverage = bool(baseline.full_tree and before.full_candidate_coverage and
                         final.full_candidate_coverage and not before.excluded_metadata and
                         not final.excluded_metadata)
    return {"schema": "self_evolution_full_tree_snapshot_review_v1" if baseline.full_tree else
                         "self_evolution_scoped_snapshot_review_v1", "candidate_id": candidate_id,
            "baseline_digest": baseline.baseline_digest, "candidate_digest": before.digest,
            "files": rows, "truncated": truncated, "content_complete": content_complete and not truncated,
            "scope": "bounded_full_tree_point_in_time_not_atomic" if baseline.full_tree else
                     "explicit_paths_point_in_time_not_atomic",
            "full_candidate_coverage": full_coverage,
            "snapshot_coverage_complete": False, "host_coverage_claim_permitted": False,
            "merge_authorized": False, "content_safety_guaranteed": False}
