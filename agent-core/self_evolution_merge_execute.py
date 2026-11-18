"""Controlled file-level Self-Evolution apply with explicit production limits.

The executor never stages/commits or mutates Git metadata. It consumes a Host
callback bound to an exact file manifest, snapshots and rechecks the complete
observed tree, and applies files individually. It cannot authenticate callback
provenance itself; callers must keep production integration gated until the
Electron/Host one-shot approval bridge is wired and platform race protection is
accepted.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import secrets
import tempfile
from types import MappingProxyType
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import self_evolution_manifest as tree_manifest

_HASH = re.compile(r"^[a-f0-9]{64}$")
_ALLOWED_ROOTS = ("agent-core", "desktop", "docs", "protocol", "scripts", "evolve/tools", "evolve/skills", "evolve/tool-catalog")
_MAX_FILES = 20_000
_MAX_TOTAL_BYTES = 64 * 1024 * 1024


class MergeSafetyError(RuntimeError):
    """A selected merge precondition failed or execution is not safely wired."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class WindowsReplaceUncertain(MergeSafetyError):
    """ReplaceFileW was called; files may have changed and require review."""

    def __init__(self, code: str, recovery_paths: tuple[str, ...], native_error_code: int | None = None):
        super().__init__(code, "Windows replacement or postcondition failed; inspect retained recovery files")
        self.recovery_paths = recovery_paths
        self.native_error_code = native_error_code


@dataclass(frozen=True)
class SelectedFileBinding:
    path: str
    action: str
    before_sha256: str | None
    after_sha256: str | None
    before_mode: str | None
    after_mode: str | None
    before_size: int | None
    after_size: int | None
    before_oid: str | None
    after_oid: str | None
    content_ref: str | None


@dataclass(frozen=True)
class SelectedMergeState:
    root: str
    root_identity: tuple[int, int]
    head: str
    git_common_dir: str
    index_sha256: str
    index_identity: tuple[int, int] | None
    status_sha256: str
    worktree_manifest_sha256: str
    unselected_manifest_sha256: str
    selected_files: tuple[SelectedFileBinding, ...]
    selected_file_identities: tuple[tuple[str, int, int, int], ...]
    # The existing manifest explicitly does not claim atomic/full-candidate coverage.
    coverage_complete: bool = False


@dataclass(frozen=True)
class AppliedFileResult:
    status: str
    operation_id: str
    operation_digest: str
    written_paths: tuple[str, ...]
    pending_paths: tuple[str, ...]
    error_code: str | None
    global_atomic: bool = False
    production_ready: bool = False
    limitations: tuple[str, ...] = ()
    manual_review_paths: tuple[str, ...] = ()
    recovery_paths: tuple[str, ...] = ()
    native_error_code: int | None = None


def _git(root: Path, *args: str) -> bytes:
    # Git status may otherwise refresh and replace the real index as an optional
    # side effect. Keep the full index bytes AND identity as drift evidence.
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), *args], capture_output=True, check=False, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MergeSafetyError("git_state_unavailable", "Git state could not be read safely") from exc
    if result.returncode:
        raise MergeSafetyError("git_state_unavailable", "Git state command failed")
    return result.stdout


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_root(root: os.PathLike[str] | str) -> Path:
    try:
        supplied = Path(root).absolute()
        canonical = supplied.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise MergeSafetyError("repository_root_invalid", "Repository root is unavailable") from exc
    if not canonical.is_dir() or supplied != canonical:
        raise MergeSafetyError("repository_root_invalid", "Repository root must be a canonical, non-aliased directory")
    top = Path(os.fsdecode(_git(canonical, "rev-parse", "--show-toplevel")).strip()).resolve(strict=True)
    if top != canonical:
        raise MergeSafetyError("repository_root_mismatch", "Provided root is not the exact Git worktree root")
    return canonical


def _parse_bindings(files: Sequence[Mapping[str, Any]]) -> tuple[SelectedFileBinding, ...]:
    if isinstance(files, (str, bytes)) or not isinstance(files, Sequence) or not 1 <= len(files) <= _MAX_FILES:
        raise MergeSafetyError("selected_manifest_invalid", "Selected manifest is empty or oversized")
    parsed: list[SelectedFileBinding] = []
    folded: set[str] = set()
    prior = ""
    total_bytes = 0
    exact_keys = {"path", "action", "beforeSha256", "afterSha256", "beforeMode", "afterMode", "beforeSize", "afterSize", "beforeOid", "afterOid", "contentRef"}
    for item in files:
        if not isinstance(item, Mapping) or set(item) != exact_keys:
            raise MergeSafetyError("selected_manifest_invalid", "Manifest entry has an unexpected field set")
        path = item["path"]
        try:
            tree_manifest.validate_relative_path(path)
        except (TypeError, ValueError) as exc:
            raise MergeSafetyError("selected_path_invalid", "Manifest contains an unsafe relative path") from exc
        if not any(path.startswith(prefix + "/") for prefix in _ALLOWED_ROOTS):
            raise MergeSafetyError("selected_path_invalid", "Selected path is outside the allowed source roots")
        if path <= prior or path.casefold() in folded or (prior and path.startswith(prior + "/")):
            raise MergeSafetyError("selected_manifest_invalid", "Manifest paths must be sorted, unique, and non-overlapping")
        prior = path
        folded.add(path.casefold())
        action = item["action"]
        before, after = item["beforeSha256"], item["afterSha256"]
        before_mode, after_mode = item["beforeMode"], item["afterMode"]
        before_size, after_size = item["beforeSize"], item["afterSize"]
        before_oid, after_oid = item["beforeOid"], item["afterOid"]
        content_ref = item["contentRef"]
        if action not in {"add", "modify", "delete"}:
            raise MergeSafetyError("selected_manifest_invalid", "Manifest action is invalid")
        for digest in (before, after):
            if digest is not None and (not isinstance(digest, str) or not _HASH.fullmatch(digest)):
                raise MergeSafetyError("selected_manifest_invalid", "Manifest content digest is invalid")
        for mode in (before_mode, after_mode):
            if mode is not None and mode not in {"100644", "100755"}:
                raise MergeSafetyError("selected_manifest_invalid", "Manifest file mode is invalid")
        if any(size is not None and (type(size) is not int or size < 0 or size > 16 * 1024 * 1024) for size in (before_size, after_size)):
            raise MergeSafetyError("selected_manifest_invalid", "Manifest file size is invalid")
        valid_oid = lambda oid: oid is None or (isinstance(oid, str) and re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", oid))
        if not valid_oid(before_oid) or not valid_oid(after_oid) or (content_ref is not None and (not isinstance(content_ref, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", content_ref))):
            raise MergeSafetyError("selected_manifest_invalid", "Manifest Git OID or content reference is invalid")
        if action == "add" and not (before is None and before_mode is None and before_size is None and before_oid is None and after and after_mode and after_size is not None and after_oid and content_ref):
            raise MergeSafetyError("selected_manifest_invalid", "Added file requires only a bound after-image")
        if action == "modify" and not all((before, after, before_mode, after_mode, before_size is not None, after_size is not None, before_oid, after_oid, content_ref)):
            raise MergeSafetyError("selected_manifest_invalid", "Modified file requires before and after images")
        if action == "delete" and not (before and before_mode and before_size is not None and before_oid and after is None and after_mode is None and after_size is None and after_oid is None and content_ref is None):
            raise MergeSafetyError("selected_manifest_invalid", "Deleted file requires only a bound before-image")
        total_bytes += after_size or 0
        if total_bytes > _MAX_TOTAL_BYTES:
            raise MergeSafetyError("selected_manifest_invalid", "Selected output exceeds the aggregate byte limit")
        parsed.append(SelectedFileBinding(path, action, before, after, before_mode, after_mode, before_size, after_size, before_oid, after_oid, content_ref))
    return tuple(parsed)


def _absolute_selected_path(root: Path, relative: str) -> Path:
    current = root
    for part in relative.split("/"):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise MergeSafetyError("selected_path_unreadable", "Selected path could not be inspected") from exc
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise MergeSafetyError("selected_path_reparse_or_symlink", "Selected path traverses a link or reparse point")
        if current != root / relative and not stat.S_ISDIR(info.st_mode):
            raise MergeSafetyError("selected_path_parent_not_directory", "A selected path parent is not a directory")
    return current


def _validate_live_selected_files(root: Path, files: tuple[SelectedFileBinding, ...]) -> None:
    # diff-files reports stat-cache mismatches as changes without comparing
    # content, so a harmless mtime touch would be rejected when index refresh
    # is disabled. Porcelain diff verifies content without writing the index.
    changed = set(os.fsdecode(_git(root, "diff", "--no-ext-diff", "--name-only", "-z")).split("\0"))
    staged = set(os.fsdecode(_git(root, "diff", "--cached", "--name-only", "-z")).split("\0"))
    untracked = set(os.fsdecode(_git(root, "ls-files", "--others", "--exclude-standard", "-z")).split("\0"))
    dirty = {name.replace("\\", "/") for name in changed | staged | untracked if name}
    dirty_folded = {name.casefold() for name in dirty}
    tracked = set(os.fsdecode(_git(root, "ls-files", "-z")).split("\0"))
    tracked.discard("")
    tracked_folded = {name.casefold() for name in tracked}
    for item in files:
        path = item.path
        absolute = _absolute_selected_path(root, path)
        exists = absolute.exists()
        if path in dirty or path.casefold() in dirty_folded:
            raise MergeSafetyError("selected_path_has_existing_change", "A selected path already has a staged, unstaged, or untracked change")
        if item.action == "add":
            if not absolute.parent.is_dir():
                raise MergeSafetyError("selected_parent_missing", "Selected add requires an existing ordinary parent directory")
            if exists or path in tracked or path.casefold() in tracked_folded:
                raise MergeSafetyError("selected_add_path_exists", "Selected added path already exists or aliases a tracked path")
            continue
        if not exists or not absolute.is_file():
            raise MergeSafetyError("selected_before_image_mismatch", "Selected before-image is missing or not a regular file")
        content = absolute.read_bytes()
        info = absolute.stat()
        mode = "100755" if info.st_mode & 0o111 else "100644"
        if _sha(content) != item.before_sha256 or mode != item.before_mode or len(content) != item.before_size:
            raise MergeSafetyError("selected_path_before_image_mismatch", "Live selected file differs from the approved before-image")
        if path not in tracked:
            raise MergeSafetyError("selected_path_not_tracked", "Modified or deleted selected path is not tracked by Git")
        committed = _git(root, "show", f"HEAD:{path}")
        head_oid = os.fsdecode(_git(root, "rev-parse", f"HEAD:{path}")).strip()
        if _sha(committed) != item.before_sha256 or head_oid != item.before_oid:
            raise MergeSafetyError("selected_path_head_mismatch", "Selected before-image does not match the current HEAD")


def capture_selected_merge_state(
    repository_root: os.PathLike[str] | str,
    selected_files: Sequence[Mapping[str, Any]],
) -> SelectedMergeState:
    """Capture read-only Git/worktree state and the manifest's exact selected paths."""
    root = _canonical_root(repository_root)
    bindings = _parse_bindings(selected_files)
    _validate_live_selected_files(root, bindings)
    manifest = tree_manifest.capture_tree_manifest(root)
    if manifest.root != str(root):
        raise MergeSafetyError("repository_root_identity_changed", "Manifest root differs from the validated worktree root")
    git_dir = Path(os.fsdecode(_git(root, "rev-parse", "--git-common-dir")).strip())
    if not git_dir.is_absolute():
        git_dir = (root / git_dir).resolve(strict=True)
    else:
        git_dir = git_dir.resolve(strict=True)
    status_bytes = _git(root, "status", "--porcelain=v2", "-z", "--untracked-files=all")
    index_path = Path(os.fsdecode(_git(root, "rev-parse", "--git-path", "index")).strip())
    if not index_path.is_absolute():
        index_path = (root / index_path).resolve(strict=True)
    try:
        index_stat = index_path.lstat()
        if not stat.S_ISREG(index_stat.st_mode) or stat.S_ISLNK(index_stat.st_mode) or getattr(index_stat, "st_file_attributes", 0) & 0x400:
            raise MergeSafetyError("git_index_path_unsafe", "Git index is not a plain regular file")
        index_bytes = index_path.read_bytes()
        index_identity = (int(index_stat.st_dev), int(index_stat.st_ino))
    except FileNotFoundError:
        index_bytes = b""
        index_identity = None
    ignored = {item.path for item in bindings}
    manifest_entries = {entry.path: entry for entry in manifest.entries}
    selected_identities = []
    for item in bindings:
        entry = manifest_entries.get(item.path)
        if entry is None:
            continue
        if entry.kind != "file" or entry.link_count != 1:
            raise MergeSafetyError("selected_file_identity_unsafe", "Selected path must be a regular, singly linked file")
        selected_identities.append((item.path, entry.device, entry.file_id, entry.link_count))
    return SelectedMergeState(
        root=str(root), root_identity=manifest.root_identity,
        head=os.fsdecode(_git(root, "rev-parse", "--verify", "HEAD")).strip(),
        git_common_dir=str(git_dir), index_sha256=_sha(index_bytes), index_identity=index_identity,
        status_sha256=_sha(status_bytes),
        worktree_manifest_sha256=manifest.digest,
        unselected_manifest_sha256=_selected_independent_digest(manifest, ignored), selected_files=bindings,
        selected_file_identities=tuple(selected_identities),
    )


def revalidate_selected_merge_state(expected: SelectedMergeState) -> bool:
    """Return whether HEAD, index, all observed worktree content, and paths still match."""
    if not isinstance(expected, SelectedMergeState):
        raise MergeSafetyError("merge_snapshot_invalid", "Expected a captured merge state")
    try:
        actual = capture_selected_merge_state(expected.root, [
            {"path": item.path, "action": item.action, "beforeSha256": item.before_sha256,
             "afterSha256": item.after_sha256, "beforeMode": item.before_mode, "afterMode": item.after_mode,
             "beforeSize": item.before_size, "afterSize": item.after_size,
             "beforeOid": item.before_oid, "afterOid": item.after_oid, "contentRef": item.content_ref}
            for item in expected.selected_files
        ])
    except MergeSafetyError:
        return False
    return actual == expected


def _runtime_limitations(*extra: str) -> tuple[str, ...]:
    values = ["per_file_only_not_globally_atomic", "external_race_window", "production_host_bridge_not_connected", *extra]
    if os.name == "nt":
        values.append("windows_external_race_protection_unavailable")
        values.append("windows_acl_restore_race_window")
    return tuple(dict.fromkeys(values))


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _git_input(root: Path, data: bytes, *args: str) -> bytes:
    try:
        result = subprocess.run(["git", "--no-optional-locks", "-C", str(root), *args], input=data,
                                capture_output=True, check=False, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MergeSafetyError("git_state_unavailable", "Git object identity could not be read safely") from exc
    if result.returncode:
        raise MergeSafetyError("git_state_unavailable", "Git object identity command failed")
    return result.stdout


def _current_index(root: Path) -> tuple[str, tuple[int, int] | None]:
    index_path = Path(os.fsdecode(_git(root, "rev-parse", "--git-path", "index")).strip())
    if not index_path.is_absolute():
        index_path = root / index_path
    try:
        info = index_path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise MergeSafetyError("git_index_path_unsafe", "Git index is not a plain regular file")
        return _sha(index_path.read_bytes()), (int(info.st_dev), int(info.st_ino))
    except FileNotFoundError:
        return _sha(b""), None


def _selected_independent_digest(manifest: tree_manifest.TreeManifest, selected: set[str]) -> str:
    payload = []
    for entry in manifest.entries:
        if entry.path in selected:
            continue
        value = entry.payload()
        # Directory byte length can change due to an exact selected add/delete;
        # retain directory identity/mode while making this digest content-focused.
        if entry.kind == "directory":
            value["size"] = 0
        payload.append(value)
    return _sha(_canonical(payload))


def _expected_file_state(root: Path, binding: SelectedFileBinding, *, after: bool) -> tuple[bool, str | None, str | None, int | None]:
    path = _absolute_selected_path(root, binding.path)
    if after and binding.action == "delete":
        return False, None, None, None
    should_exist = after or binding.action != "add"
    if not should_exist:
        return False, None, None, None
    if not path.exists() or not path.is_file():
        raise MergeSafetyError("selected_file_state_changed", "Selected file disappeared or is no longer a regular file")
    data = path.read_bytes()
    info = path.stat()
    mode = "100755" if info.st_mode & 0o111 else "100644"
    return True, _sha(data), mode, len(data)


def _assert_application_boundary(state: SelectedMergeState, bindings: tuple[SelectedFileBinding, ...],
                                 written: set[str]) -> None:
    root = Path(state.root)
    if os.fsdecode(_git(root, "rev-parse", "--verify", "HEAD")).strip() != state.head:
        raise MergeSafetyError("external_head_change", "HEAD changed after approval")
    if _current_index(root) != (state.index_sha256, state.index_identity):
        raise MergeSafetyError("external_index_change", "Git index changed after approval")
    common = Path(os.fsdecode(_git(root, "rev-parse", "--git-common-dir")).strip())
    if not common.is_absolute():
        common = (root / common).resolve(strict=True)
    else:
        common = common.resolve(strict=True)
    if str(common) != state.git_common_dir:
        raise MergeSafetyError("git_identity_changed", "Git common directory changed after approval")
    current = tree_manifest.capture_tree_manifest(root)
    if current.root_identity != state.root_identity:
        raise MergeSafetyError("repository_root_identity_changed", "Repository root identity changed after approval")
    if _selected_independent_digest(current, {item.path for item in bindings}) != state.unselected_manifest_sha256:
        raise MergeSafetyError("external_worktree_change", "An unselected worktree path changed after approval")
    identity_map = {path: (device, file_id, links) for path, device, file_id, links in state.selected_file_identities}
    for item in bindings:
        exists, digest, mode, size = _expected_file_state(root, item, after=item.path in written)
        if item.path in written:
            if item.action == "delete":
                if exists:
                    raise MergeSafetyError("applied_file_changed", "A previously applied deletion was externally recreated")
            elif digest != item.after_sha256 or mode != item.after_mode or size != item.after_size:
                raise MergeSafetyError("applied_file_changed", "A previously applied file changed after application")
        elif item.action == "add":
            if exists:
                raise MergeSafetyError("selected_add_path_exists", "Selected add path appeared after approval")
        elif digest != item.before_sha256 or mode != item.before_mode or size != item.before_size:
            raise MergeSafetyError("selected_file_state_changed", "A pending selected path changed after approval")
        elif exists and item.path in identity_map:
            info = _absolute_selected_path(root, item.path).lstat()
            if (int(info.st_dev), int(info.st_ino), int(info.st_nlink)) != identity_map[item.path]:
                raise MergeSafetyError("selected_file_identity_changed", "Selected file identity changed after approval")


def _write_temp(parent: Path, content: bytes, mode: str) -> Path:
    fd, name = tempfile.mkstemp(prefix=".self-evolution-apply-", suffix=".tmp", dir=parent)
    temp_path = Path(name)
    try:
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_path, 0o755 if mode == "100755" else 0o644)
        if temp_path.read_bytes() != content:
            raise MergeSafetyError("temporary_file_verification_failed", "Prepared file did not verify byte-for-byte")
        return temp_path
    except BaseException:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _windows_security_descriptor(path: Path) -> bytes:
    """Read owner, group and DACL as a self-relative descriptor; fail closed."""
    from ctypes import wintypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    get_security = advapi.GetFileSecurityW
    get_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p,
                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    get_security.restype = wintypes.BOOL
    info = 0x1 | 0x2 | 0x4  # owner, group, DACL; not SACL (privileged)
    needed = wintypes.DWORD()
    if get_security(str(path), info, None, 0, ctypes.byref(needed)) or ctypes.get_last_error() != 122 or not 0 < needed.value <= 1024 * 1024:
        raise MergeSafetyError("windows_acl_read_failed", "Cannot size the file security descriptor")
    descriptor = ctypes.create_string_buffer(needed.value)
    if not get_security(str(path), info, descriptor, needed.value, ctypes.byref(needed)):
        raise MergeSafetyError("windows_acl_read_failed", "Cannot read the file security descriptor")
    return descriptor.raw[:needed.value]


def _windows_restore_dacl(path: Path, descriptor: bytes) -> None:
    """Restore the captured DACL only; owner and group are separately verified."""
    from ctypes import wintypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    set_security = advapi.SetFileSecurityW
    set_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    set_security.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(descriptor)
    if not set_security(str(path), 0x4, buffer):
        raise MergeSafetyError("windows_acl_restore_failed", "Cannot restore the captured DACL")


def _windows_replace_file(target: Path, replacement: Path, backup: Path) -> None:
    """Call ReplaceFileW with no ignore flags and same-directory backup."""
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    replace = kernel.ReplaceFileW
    replace.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                        wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    replace.restype = wintypes.BOOL
    if not replace(str(target), str(replacement), str(backup), 0, None, None):
        raise OSError(ctypes.get_last_error(), "ReplaceFileW failed")


def _apply_one(root: Path, binding: SelectedFileBinding, content: bytes | None) -> None:
    target = _absolute_selected_path(root, binding.path)
    if binding.action == "delete":
        exists, digest, mode, size = _expected_file_state(root, binding, after=False)
        if not exists or digest != binding.before_sha256 or mode != binding.before_mode or size != binding.before_size:
            raise MergeSafetyError("selected_file_state_changed", "Delete target changed immediately before unlink")
        target.unlink()
        return
    if content is None:
        raise MergeSafetyError("candidate_content_unavailable", "Candidate after-image bytes are unavailable")
    if binding.action == "modify":
        exists, digest, mode, size = _expected_file_state(root, binding, after=False)
        if not exists or digest != binding.before_sha256 or mode != binding.before_mode or size != binding.before_size:
            raise MergeSafetyError("selected_file_state_changed", "Modify target changed immediately before replacement")
    elif target.exists():
        raise MergeSafetyError("selected_add_path_exists", "Add target appeared immediately before publication")
    original_security = _windows_security_descriptor(target) if os.name == "nt" and binding.action == "modify" else None
    temp_path = _write_temp(target.parent, content, binding.after_mode or "100644")
    retain_temp = False
    backup_path = temp_path.with_name(temp_path.name + ".backup")
    try:
        # Add uses an exclusive hard-link publication; modify uses same-directory replace.
        if binding.action == "add":
            os.link(temp_path, target)
            temp_path.unlink()
        else:
            exists, digest, mode, size = _expected_file_state(root, binding, after=False)
            if not exists or digest != binding.before_sha256 or mode != binding.before_mode or size != binding.before_size:
                raise MergeSafetyError("selected_file_state_changed", "Modify target changed at the publication boundary")
            if os.name == "nt":
                if _windows_security_descriptor(target) != original_security:
                    raise MergeSafetyError("windows_acl_changed_before_replace", "Target ACL changed before replacement")
                if backup_path.exists():
                    raise MergeSafetyError("windows_backup_path_exists", "Recovery backup path is already occupied")
                # Once entered, failure may still rename or remove either file.
                # Retain every remaining recovery artifact until all checks pass.
                retain_temp = True
                try:
                    _windows_replace_file(target, temp_path, backup_path)
                    if _windows_security_descriptor(target) != original_security:
                        # ReplaceFileW can materialize inherited ACEs as explicit
                        # entries. Never claim exact preservation without restoring
                        # the captured DACL and comparing the descriptor again.
                        if _windows_security_descriptor(backup_path) != original_security:
                            raise MergeSafetyError("windows_backup_acl_mismatch", "Backup ACL differs from the captured target ACL")
                        _windows_restore_dacl(target, original_security)
                        if _windows_security_descriptor(target) != original_security:
                            raise MergeSafetyError("windows_acl_verification_failed", "Replacement DACL or owner/group changed")
                    exists, digest, mode, size = _expected_file_state(root, binding, after=True)
                    if not exists or digest != binding.after_sha256 or mode != binding.after_mode or size != binding.after_size:
                        raise MergeSafetyError("written_file_verification_failed", "Published file differs from approved content")
                    backup_path.unlink()
                except Exception as exc:
                    code = exc.code if isinstance(exc, MergeSafetyError) else "windows_replace_result_uncertain"
                    artifacts = tuple(str(path) for path in (temp_path, backup_path) if path.exists())
                    native_error = getattr(exc, "winerror", None) or (exc.errno if isinstance(exc, OSError) else None)
                    raise WindowsReplaceUncertain(code, artifacts, native_error) from exc
                retain_temp = False
            else:
                os.replace(temp_path, target)
    finally:
        if not retain_temp:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
    exists, digest, mode, size = _expected_file_state(root, binding, after=True)
    if not exists or digest != binding.after_sha256 or mode != binding.after_mode or size != binding.after_size:
        raise MergeSafetyError("written_file_verification_failed", "Published file did not match the approved after-image")


def _file_matches_after(root: Path, binding: SelectedFileBinding) -> bool:
    try:
        exists, digest, mode, size = _expected_file_state(root, binding, after=True)
    except (OSError, MergeSafetyError):
        return False
    if binding.action == "delete":
        return not exists
    return exists and digest == binding.after_sha256 and mode == binding.after_mode and size == binding.after_size


def execute_selected_merge(
    repository_root: os.PathLike[str] | str,
    selected_files: Sequence[Mapping[str, Any]],
    *,
    content_resolver,
    trusted_host_approval_consumer=None,
    approval_receipt=None,
    trusted_execution_guard=None,
) -> AppliedFileResult:
    """Apply exact files after an injected Host consumes a bound one-shot approval.

    This is an executable controlled-fixture path, not a production authorization
    claim. The callback's provenance and platform-level exclusion of arbitrary
    concurrent writers are not enforceable here; production remains gated.
    """
    trusted_host_approval_consumer = trusted_host_approval_consumer if trusted_host_approval_consumer is not None else None
    if approval_receipt is not None:
        raise MergeSafetyError("untrusted_approval_receipt", "Unverified approval payloads are not accepted")
    if trusted_execution_guard is not None and not callable(trusted_execution_guard):
        raise MergeSafetyError("trusted_execution_guard_invalid", "Execution guard must be callable")
    if not callable(content_resolver) or not callable(trusted_host_approval_consumer):
        raise MergeSafetyError("trusted_host_identity_unavailable", "A trusted Host content resolver and one-shot approval consumer are required")
    root = _canonical_root(repository_root)
    bindings = _parse_bindings(selected_files)
    resolved: dict[str, bytes | None] = {}
    for item in bindings:
        if item.action == "delete":
            resolved[item.path] = None
            continue
        try:
            content = content_resolver(item.content_ref)
        except Exception as exc:
            raise MergeSafetyError("candidate_content_unavailable", "Candidate after-image could not be resolved") from exc
        if not isinstance(content, bytes) or len(content) != item.after_size or _sha(content) != item.after_sha256:
            raise MergeSafetyError("candidate_content_mismatch", "Candidate bytes do not match the complete approved after-image binding")
        oid = os.fsdecode(_git_input(root, content, "hash-object", "--stdin")).strip()
        if oid != item.after_oid:
            raise MergeSafetyError("candidate_content_mismatch", "Candidate Git blob identity differs from the approved manifest")
        resolved[item.path] = content

    state = capture_selected_merge_state(root, selected_files)
    operation_id = secrets.token_hex(16)
    files_payload = tuple(MappingProxyType({
        "path": item.path, "action": item.action,
        "beforeSha256": item.before_sha256, "afterSha256": item.after_sha256,
        "beforeMode": item.before_mode, "afterMode": item.after_mode,
        "beforeSize": item.before_size, "afterSize": item.after_size,
    }) for item in bindings)
    payload = {
        "operationId": operation_id, "root": state.root, "head": state.head,
        "indexSha256": state.index_sha256, "worktreeManifestSha256": state.worktree_manifest_sha256,
        "files": [dict(item) for item in files_payload],
    }
    operation_digest = _sha(_canonical(payload))
    request = MappingProxyType({
        "mode": "self_evolution_file_apply_confirmation_v1",
        "operationId": operation_id, "operationDigest": operation_digest,
        "root": state.root, "head": state.head,
        "selectedPaths": tuple(item.path for item in bindings), "files": files_payload,
    })
    try:
        approved = trusted_host_approval_consumer(request)
    except Exception as exc:
        raise MergeSafetyError("trusted_host_approval_failed", "Trusted Host did not confirm this exact operation") from exc
    if approved is not True:
        return AppliedFileResult("approval_rejected", operation_id, operation_digest, (),
                                 tuple(item.path for item in bindings), "approval_rejected",
                                 limitations=("production_host_bridge_not_connected",))

    # Approval can be displayed asynchronously; re-read the complete observed tree before first write.
    if not revalidate_selected_merge_state(state):
        return AppliedFileResult("stale_after_approval", operation_id, operation_digest, (),
                                 tuple(item.path for item in bindings), "approved_state_changed",
                                 limitations=("production_host_bridge_not_connected",))

    written: set[str] = set()
    ordered_paths = tuple(item.path for item in bindings)
    for item in bindings:
        try:
            if trusted_execution_guard is not None and trusted_execution_guard() is not True:
                raise MergeSafetyError("trusted_execution_guard_rejected", "Host or candidate binding changed during application")
            _assert_application_boundary(state, bindings, written)
            _apply_one(root, item, resolved[item.path])
            written.add(item.path)
        except Exception as exc:
            code = exc.code if isinstance(exc, MergeSafetyError) else "file_apply_failed"
            if _file_matches_after(root, item):
                written.add(item.path)
            pending = tuple(path for path in ordered_paths if path not in written)
            uncertain = isinstance(exc, WindowsReplaceUncertain)
            return AppliedFileResult(
                "partial_failure" if written or uncertain else "failed", operation_id, operation_digest,
                tuple(path for path in ordered_paths if path in written), pending, code,
                limitations=_runtime_limitations(),
                manual_review_paths=tuple(dict.fromkeys((*pending, *((item.path,) if uncertain else ())))),
                recovery_paths=exc.recovery_paths if uncertain else (),
                native_error_code=exc.native_error_code if uncertain else None,
            )
    try:
        if trusted_execution_guard is not None and trusted_execution_guard() is not True:
            raise MergeSafetyError("trusted_execution_guard_rejected", "Host or candidate binding changed after application")
        _assert_application_boundary(state, bindings, written)
    except Exception as exc:
        code = exc.code if isinstance(exc, MergeSafetyError) else "post_apply_verification_failed"
        return AppliedFileResult("partial_failure", operation_id, operation_digest, ordered_paths, (), code,
                                 limitations=_runtime_limitations(),
                                 manual_review_paths=ordered_paths)
    return AppliedFileResult("applied", operation_id, operation_digest, ordered_paths, (), None,
                             limitations=_runtime_limitations())
