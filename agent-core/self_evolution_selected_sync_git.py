"""Selected stable-to-sandbox snapshot and CAS publication.

No branch, ref, index, remote, or global Git configuration is changed. Stale
publication locks/journals are intentionally never auto-reclaimed; inspect the
recorded PID and recovery.json, then recover manually before retrying.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

SANDBOX_BRANCH = "experiment/sandbox"
SANDBOX_RELATIVE = Path(".worktrees") / "sandbox"
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_INDEX_BYTES = 8 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
MAX_PATH_COUNT = 256
MAX_PROCESS_OUTPUT = 32 * 1024 * 1024
PROCESS_TIMEOUT_SECONDS = 20
MARKER_SIZE = 16


class SyncError(RuntimeError):
    """The requested snapshot or publication cannot be performed safely."""


class SyncRecoveryRequired(SyncError):
    """Some worktree writes could not safely be rolled back; journal retained."""


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    target: bytes | None
    base: bytes | None
    source: bytes
    divergence: bytes | None
    merged: bytes | None
    has_conflicts: bool
    target_mode: int | None
    source_mode: int


@dataclass(frozen=True)
class Snapshot:
    target_head: str
    source_head: str
    digest: str
    files: dict[str, FileSnapshot]
    verification: dict[str, bytes]
    stable_root: str
    target_root: str
    source_ref: str
    source_tip: str
    index_path: str
    index_bytes: bytes
    selected_paths: tuple[str, ...]
    verification_paths: tuple[str, ...]
    _state_payload: bytes


@dataclass(frozen=True)
class PublishResult:
    published: bool
    paths: tuple[str, ...]
    digest: str


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run(args: list[str], *, cwd: Path | None = None, input: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        proc = subprocess.run(args, cwd=cwd, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=False, env=_env(), timeout=PROCESS_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise SyncError(f"process timed out: {args[0]}") from exc
    if len(proc.stdout) > MAX_PROCESS_OUTPUT or len(proc.stderr) > MAX_PROCESS_OUTPUT:
        raise SyncError(f"process output exceeded limit: {args[0]}")
    return proc


def _git(cwd: Path, *args: str, check: bool = True) -> bytes:
    proc = _run(["git", "-C", str(cwd), *args])
    if check and proc.returncode:
        raise SyncError(proc.stderr.decode("utf-8", "replace").strip() or "git command failed")
    return proc.stdout


def _text(data: bytes) -> str:
    return data.decode("utf-8", "strict").strip()


def _canonical_root(root: Path) -> Path:
    try:
        resolved = root.resolve(strict=True)
        top = Path(_text(_git(resolved, "rev-parse", "--show-toplevel"))).resolve()
    except (OSError, SyncError) as exc:
        raise SyncError(f"stable_root is not an available Git root: {root}") from exc
    if not resolved.is_dir() or top != resolved:
        raise SyncError("stable_root must be the Git worktree root")
    return resolved


def _safe_rel(raw: str) -> str:
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        raise SyncError(f"unsafe repository path: {raw!r}")
    parts = raw.split("/")
    if raw.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise SyncError(f"unsafe repository path: {raw!r}")
    if parts[0].casefold() == ".git":
        raise SyncError("Git metadata paths are not selectable")
    return "/".join(parts)


def _safe_path(root: Path, rel: str, *, allow_missing: bool) -> Path:
    current = root
    parts = Path(rel).parts
    for i, part in enumerate(parts):
        try:
            aliases = [p.name for p in current.iterdir() if p.name.casefold() == part.casefold()]
        except FileNotFoundError:
            if allow_missing:
                return root.joinpath(*parts)
            raise SyncError(f"path parent disappeared: {rel}")
        if aliases and (len(aliases) != 1 or aliases[0] != part):
            raise SyncError(f"case alias in path: {rel}")
        current /= part
        try:
            st = current.lstat()
        except FileNotFoundError:
            if allow_missing:
                return root.joinpath(*parts)
            raise SyncError(f"required path is missing: {rel}")
        if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
            raise SyncError(f"link/reparse path rejected: {rel}")
        if i < len(parts) - 1 and not stat.S_ISDIR(st.st_mode):
            raise SyncError(f"non-directory path component: {rel}")
        if i == len(parts) - 1 and (not stat.S_ISREG(st.st_mode) or getattr(st, "st_nlink", 1) > 1):
            raise SyncError(f"non-regular or hard-linked file: {rel}")
    return current


def _read_working(root: Path, rel: str) -> bytes | None:
    path = _safe_path(root, rel, allow_missing=True)
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(data) > MAX_FILE_BYTES:
        raise SyncError(f"file exceeds {MAX_FILE_BYTES} bytes: {rel}")
    return data


def _tree_blob(root: Path, commit: str, rel: str) -> tuple[bytes | None, int | None]:
    raw = _git(root, "ls-tree", "-z", commit, "--", rel)
    if not raw:
        return None, None
    record = raw.rstrip(b"\0").split(b"\t", 1)
    if len(record) != 2 or record[1].decode("utf-8", "surrogateescape") != rel:
        raise SyncError(f"ambiguous Git tree path: {rel}")
    meta = record[0].decode("ascii").split()
    if len(meta) != 3 or meta[0] not in ("100644", "100755") or meta[1] != "blob":
        raise SyncError(f"selected source is not a regular tracked file: {rel}")
    data = _git(root, "cat-file", "blob", meta[2])
    if len(data) > MAX_FILE_BYTES:
        raise SyncError(f"source file exceeds {MAX_FILE_BYTES} bytes: {rel}")
    return data, int(meta[0], 8)


def _merge_bytes(target: bytes, base: bytes | None, source: bytes) -> tuple[bytes, bool] | None:
    values = [target, base or b"", source]
    if any(b"\0" in value for value in values):
        return None
    try:
        for value in values:
            value.decode("utf-8", "strict")
    except UnicodeDecodeError:
        return None
    newline = b"\r\n" if b"\r\n" in target else (b"\r" if b"\r" in target and b"\n" not in target else b"\n")
    normalized = [value.replace(b"\r\n", b"\n").replace(b"\r", b"\n") for value in values]
    with tempfile.TemporaryDirectory(prefix="selected-sync-merge-") as temp:
        names = [Path(temp) / name for name in ("target", "base", "source")]
        for path, data in zip(names, normalized):
            path.write_bytes(data)
        proc = _run(["git", "merge-file", "--stdout", "-p", "--diff3", f"--marker-size={MARKER_SIZE}",
                     "-L", "SYNC_TARGET", "-L", "SYNC_BASE", "-L", "SYNC_SOURCE",
                     *(str(path) for path in names)])
    if proc.returncode < 0 or proc.returncode > 127:
        raise SyncError("git merge-file failed: " + proc.stderr.decode("utf-8", "replace"))
    output = proc.stdout
    try:
        output.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise SyncError("git merge-file returned non-UTF-8 text") from exc
    start = re.compile(rb"(?m)^<{16} SYNC_TARGET(?:\r?\n|$)")
    end = re.compile(rb"(?m)^>{16} SYNC_SOURCE(?:\r?\n|$)")
    count = len(start.findall(output))
    if count != proc.returncode or len(end.findall(output)) != count:
        raise SyncError("git merge-file returned an invalid conflict draft")
    if proc.returncode and (b"|||||||||||||||| SYNC_BASE" not in output or b"================" not in output):
        raise SyncError("git merge-file conflict markers are incomplete")
    if newline != b"\n":
        output = output.replace(b"\n", newline)
    return output, proc.returncode > 0


def _index_path(target: Path) -> Path:
    path = Path(_text(_git(target, "rev-parse", "--git-path", "index")))
    return path if path.is_absolute() else target / path


def _snapshot_state(stable: Path, target: Path, source_ref: str, source_tip: str, target_head: str,
                    index_path: Path, selected: tuple[str, ...], verification: tuple[str, ...]
                    ) -> tuple[bytes, dict[str, bytes | None], dict[str, bytes], bytes]:
    try:
        with index_path.open("rb") as stream:
            index = stream.read(MAX_INDEX_BYTES + 1)
    except OSError as exc:
        raise SyncError("cannot read target Git index") from exc
    if len(index) > MAX_INDEX_BYTES:
        raise SyncError("target Git index exceeds configured limit")
    selected_bytes = {rel: _read_working(target, rel) for rel in selected}
    total = sum(len(value) for value in selected_bytes.values() if value is not None)
    if total > MAX_SNAPSHOT_BYTES:
        raise SyncError("selected target snapshot exceeds configured aggregate limit")
    verification_bytes: dict[str, bytes] = {}
    for rel in verification:
        data = _read_working(target, rel)
        if data is None:
            raise SyncError(f"verification dependency missing: {rel}")
        verification_bytes[rel] = data
        total += len(data)
        if total > MAX_SNAPSHOT_BYTES:
            raise SyncError("snapshot and verification data exceed configured aggregate limit")
    payload = {
        "stable": str(stable).casefold(), "target": str(target).casefold(),
        "source_ref": source_ref, "source_tip": source_tip, "target_head": target_head,
        "index_sha256": hashlib.sha256(index).hexdigest(),
        "selected": {p: None if b is None else hashlib.sha256(b).hexdigest() for p, b in selected_bytes.items()},
        "verification": {p: hashlib.sha256(b).hexdigest() for p, b in verification_bytes.items()},
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), selected_bytes, verification_bytes, index


def _source_ref(stable: Path, requested: str) -> tuple[str, str]:
    branch_ref = _text(_git(stable, "symbolic-ref", "HEAD"))
    branch_name = branch_ref.removeprefix("refs/heads/")
    if requested == branch_name or requested == branch_ref:
        return branch_ref, branch_ref
    raise SyncError("source_ref must exactly name the checked-out stable branch (short name or full refs/heads/ name)")


def _selected_deltas(stable: Path, parent: str, commit: str) -> dict[str, str]:
    raw = _git(stable, "diff", "--raw", "-z", "--no-renames", parent, commit)
    parts = raw.split(b"\0")
    changes: dict[str, str] = {}
    i = 0
    while i < len(parts) and parts[i]:
        meta = parts[i].decode("ascii").split()
        if len(meta) != 5 or not meta[0].startswith(":"):
            raise SyncError("unable to parse exact source commit diff")
        if i + 1 >= len(parts):
            raise SyncError("truncated source commit diff")
        path = parts[i + 1].decode("utf-8", "strict")
        status = meta[4]
        if status not in ("A", "M", "D"):
            raise SyncError(f"unsupported source change type: {status}")
        if status == "M" and (meta[0][1:] != meta[1] or meta[2] == meta[3]):
            raise SyncError(f"mode-only or empty modification is not selectable: {path}")
        if status == "D":
            changes[path] = "D"
        else:
            changes[path] = status
        i += 2
    return changes


def capture(stable_root: str | os.PathLike[str], source_ref: str, commit: str,
            selected_paths: list[str] | tuple[str, ...],
            verification_paths: list[str] | tuple[str, ...] = ()) -> Snapshot:
    stable = _canonical_root(Path(stable_root))
    target = stable / SANDBOX_RELATIVE
    try:
        target_real = target.resolve(strict=True)
    except OSError as exc:
        raise SyncError("registered sandbox worktree is missing") from exc
    if target_real != target.absolute() or not target_real.is_dir():
        raise SyncError("registered sandbox root is not a canonical directory")
    listed = _git(stable, "worktree", "list", "--porcelain").decode("utf-8", "replace").splitlines()
    registrations = []
    for line in listed:
        if line.startswith("worktree "):
            try:
                registrations.append(Path(line[9:]).resolve())
            except OSError:
                pass
    if target_real not in registrations:
        raise SyncError("sandbox is not registered in the stable repository common dir")
    if Path(_text(_git(target, "rev-parse", "--show-toplevel"))).resolve() != target_real:
        raise SyncError("sandbox Git root mismatch")
    if _text(_git(target, "symbolic-ref", "--short", "HEAD")) != SANDBOX_BRANCH:
        raise SyncError("sandbox must be on experiment/sandbox")
    canonical_ref, _ = _source_ref(stable, source_ref)
    source_tip = _text(_git(stable, "rev-parse", "--verify", f"{canonical_ref}^{{commit}}"))
    if _text(_git(stable, "rev-parse", "HEAD")) != source_tip:
        raise SyncError("stable branch tip and checked-out HEAD differ")
    resolved = _text(_git(stable, "rev-parse", "--verify", f"{commit}^{{commit}}"))
    if resolved != commit or len(commit) < 40:
        raise SyncError("commit must be an explicit full commit SHA")
    parents = _text(_git(stable, "rev-list", "--parents", "-n", "1", commit)).split()
    if len(parents) != 2 or parents[0] != commit:
        raise SyncError("selected commit must have exactly one parent")
    parent = parents[1]
    if commit not in _git(stable, "rev-list", "--first-parent", source_tip).decode().splitlines():
        raise SyncError("selected commit is not in stable first-parent history")
    selected = tuple(_safe_rel(p) for p in selected_paths)
    verification = tuple(_safe_rel(p) for p in verification_paths)
    if not selected or len(selected) > MAX_PATH_COUNT or len({p.casefold() for p in selected}) != len(selected):
        raise SyncError("selected paths must be non-empty, bounded, and unique")
    if len(verification) > MAX_PATH_COUNT or len({p.casefold() for p in verification}) != len(verification) or {p.casefold() for p in selected} & {p.casefold() for p in verification}:
        raise SyncError("verification paths must be unique and disjoint from selected paths")
    deltas = _selected_deltas(stable, parent, commit)
    # Detect rename similarity across the full commit even though exact raw deltas use --no-renames.
    rename_tokens = _git(stable, "diff", "--name-status", "-z", "--find-renames", parent, commit).split(b"\0")
    i = 0
    while i < len(rename_tokens) and rename_tokens[i]:
        status = rename_tokens[i]
        count = 2 if status.startswith((b"R", b"C")) else 1
        paths = {rename_tokens[i + offset].decode("utf-8", "strict") for offset in range(1, count + 1)}
        if status.startswith(b"R") and paths & set(selected):
            raise SyncError("selected path participates in a source rename")
        i += count + 1
    for rel in selected:
        if deltas.get(rel) not in ("A", "M"):
            raise SyncError(f"selected path must be an exact source addition/modification: {rel}")
    target_head = _text(_git(target, "rev-parse", "HEAD"))
    index_path = _index_path(target)
    before, target_bytes, verification_bytes, index_bytes = _snapshot_state(
        stable, target, canonical_ref, source_tip, target_head, index_path, selected, verification)
    files: dict[str, FileSnapshot] = {}
    source_total = 0
    for rel in selected:
        source_bytes, source_mode = _tree_blob(stable, commit, rel)
        base_bytes, base_mode = _tree_blob(stable, parent, rel)
        if source_bytes is None or source_mode is None:
            raise SyncError(f"selected path is not tracked in source commit: {rel}")
        if deltas[rel] == "A" and base_bytes is not None or deltas[rel] == "M" and base_bytes is None:
            raise SyncError(f"source delta disagrees with parent tree: {rel}")
        source_total += len(source_bytes) + (len(base_bytes) if base_bytes is not None else 0)
        if source_total > MAX_SNAPSHOT_BYTES:
            raise SyncError("selected source/base snapshots exceed configured aggregate limit")
        target_data = target_bytes[rel]
        target_mode = _tree_blob(stable, target_head, rel)[1]
        if target_data is None and target_mode is not None:
            raise SyncError(f"selected tracked target path is missing: {rel}")
        merged_pair = _merge_bytes(target_data or b"", base_bytes, source_bytes)
        merged, has_conflicts = merged_pair if merged_pair is not None else (None, False)
        if merged is not None:
            source_total += len(merged)
            if source_total > MAX_SNAPSHOT_BYTES:
                raise SyncError("merged drafts exceed configured aggregate limit")
        files[rel] = FileSnapshot(rel, target_data, base_bytes, source_bytes,
                                  None if target_data == base_bytes else target_data,
                                  merged, has_conflicts, target_mode, source_mode)
    after, after_target, after_verification, _ = _snapshot_state(
        stable, target, canonical_ref, source_tip, _text(_git(target, "rev-parse", "HEAD")),
        index_path, selected, verification)
    if before != after or target_bytes != after_target or verification_bytes != after_verification:
        raise SyncError("worktree changed during snapshot capture")
    if _text(_git(stable, "rev-parse", "HEAD")) != source_tip:
        raise SyncError("stable branch changed during snapshot capture")
    digest = hashlib.sha256(before).hexdigest()
    return Snapshot(target_head, commit, digest, files, verification_bytes, str(stable), str(target),
                    canonical_ref, source_tip, str(index_path), index_bytes, selected, verification, before)


def _controls(snapshot: Snapshot) -> tuple[str, bytes, dict[str, bytes]]:
    stable, target = Path(snapshot.stable_root), Path(snapshot.target_root)
    expected_target = (stable / SANDBOX_RELATIVE).resolve(strict=True)
    if target.resolve(strict=True) != expected_target:
        raise SyncError("registered sandbox root changed")
    if _text(_git(target, "symbolic-ref", "HEAD")) != f"refs/heads/{SANDBOX_BRANCH}":
        raise SyncError("sandbox branch changed since capture")
    registered = _git(stable, "worktree", "list", "--porcelain").decode("utf-8", "replace").splitlines()
    if target not in [Path(line[9:]).resolve() for line in registered if line.startswith("worktree ")]:
        raise SyncError("sandbox registration changed since capture")
    if _text(_git(stable, "symbolic-ref", "HEAD")) != snapshot.source_ref:
        raise SyncError("stable branch changed since capture")
    source_tip = _text(_git(stable, "rev-parse", "--verify", f"{snapshot.source_ref}^{{commit}}"))
    target_head = _text(_git(target, "rev-parse", "HEAD"))
    try:
        with Path(snapshot.index_path).open("rb") as stream:
            index = stream.read(MAX_INDEX_BYTES + 1)
    except OSError as exc:
        raise SyncError("cannot revalidate target Git index") from exc
    if len(index) > MAX_INDEX_BYTES:
        raise SyncError("target Git index exceeds configured limit")
    verification = {}
    for rel in snapshot.verification_paths:
        data = _read_working(target, rel)
        if data is None:
            raise SyncError(f"verification dependency missing: {rel}")
        verification[rel] = data
    if (source_tip != snapshot.source_tip or target_head != snapshot.target_head or
            index != snapshot.index_bytes or verification != snapshot.verification):
        raise SyncError("snapshot CAS validation failed: branch, HEAD, index, or verification dependency changed")
    return target_head, index, verification


def _acquire_lock(stable: Path) -> tuple[Path, bytes]:
    common = Path(_text(_git(stable, "rev-parse", "--git-common-dir")))
    if not common.is_absolute():
        common = stable / common
    lock = common / "selected-sync-publish.lock"
    token = json.dumps({"pid": os.getpid(), "nonce": uuid.uuid4().hex}).encode()
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise SyncError("publication lock exists; inspect owner and recovery journal, never auto-remove a stale lock") from exc
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(token)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        try:
            if lock.read_bytes() == token:
                lock.unlink()
        except OSError:
            pass
        raise SyncError("could not persist publication lock") from exc
    return lock, token


def _conflict_update_allowed(file: FileSnapshot, postimage: bytes) -> bool:
    if file.merged is None:
        return False
    if not file.has_conflicts:
        return postimage == file.merged
    start_re = re.compile(rb"(?m)^<{16} SYNC_TARGET(?:\r?\n|$)")
    base_re = re.compile(rb"(?m)^\|{16} SYNC_BASE(?:\r?\n|$)")
    mid_re = re.compile(rb"(?m)^={16,}(?:\r?\n|$)")
    end_re = re.compile(rb"(?m)^>{16} SYNC_SOURCE(?:\r?\n|$)")
    fixed: list[bytes] = []
    pos = 0
    while True:
        start = start_re.search(file.merged, pos)
        if not start:
            fixed.append(file.merged[pos:])
            break
        base = base_re.search(file.merged, start.end())
        mid = mid_re.search(file.merged, base.end() if base else start.end())
        end = end_re.search(file.merged, mid.end() if mid else start.end())
        if not base or not mid or not end:
            return False
        fixed.append(file.merged[pos:start.start()])
        pos = end.end()
    cursor = 0
    for segment in fixed:
        found = postimage.find(segment, cursor)
        if found < 0:
            return False
        cursor = found + len(segment)
    return True


def publish(snapshot: Snapshot, postimages: Mapping[str, bytes], cancelled: object) -> PublishResult:
    if not isinstance(snapshot, Snapshot) or not isinstance(postimages, Mapping):
        raise SyncError("invalid snapshot or postimages")
    if not set(postimages).issubset(snapshot.selected_paths) or any(not isinstance(v, bytes) for v in postimages.values()):
        raise SyncError("postimages must be a subset of selected paths mapped to bytes")
    if callable(cancelled):
        is_cancelled = lambda: bool(cancelled())
    else:
        is_set = getattr(cancelled, "is_set", None)
        is_cancelled = lambda: bool(is_set()) if callable(is_set) else bool(cancelled)
    stable, target = Path(snapshot.stable_root), Path(snapshot.target_root)
    # Full initial CAS includes selected working bytes, verification bytes, and exact index bytes.
    state, selected_now, verify_now, index_now = _snapshot_state(
        stable, target, snapshot.source_ref, snapshot.source_tip, snapshot.target_head,
        Path(snapshot.index_path), snapshot.selected_paths, snapshot.verification_paths)
    if (hashlib.sha256(state).hexdigest() != snapshot.digest or state != snapshot._state_payload or
            selected_now != {p: snapshot.files[p].target for p in snapshot.selected_paths} or
            verify_now != snapshot.verification or index_now != snapshot.index_bytes):
        raise SyncError("snapshot CAS validation failed")
    if is_cancelled():
        raise SyncError("publication cancelled")
    updates = {p: b for p, b in postimages.items() if b != selected_now[p]}
    if not updates:
        return PublishResult(False, (), snapshot.digest)
    for rel, data in updates.items():
        if len(data) > MAX_FILE_BYTES:
            raise SyncError(f"postimage exceeds {MAX_FILE_BYTES} bytes: {rel}")
        try:
            data.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise SyncError(f"postimage must be UTF-8 text: {rel}") from exc
        if not _conflict_update_allowed(snapshot.files[rel], data):
            raise SyncError(f"postimage changes text outside the merge draft/conflict blocks: {rel}")

    lock, lock_token = _acquire_lock(stable)
    journal = Path(snapshot.target_root).parent / f".selected-sync-journal-{uuid.uuid4().hex}"
    backups = {rel: selected_now[rel] for rel in updates}
    backup_modes: dict[str, int | None] = {}
    staged: dict[str, Path] = {}
    completed: list[str] = []
    retain_lock = False
    try:
        journal.mkdir()
        for rel, data in updates.items():
            current_path = _safe_path(target, rel, allow_missing=True)
            if backups[rel] is None:
                backup_modes[rel] = None
                mode = snapshot.files[rel].source_mode
            else:
                backup_modes[rel] = stat.S_IMODE(current_path.stat().st_mode)
                mode = backup_modes[rel]
            staged[rel] = journal / (hashlib.sha256(rel.encode()).hexdigest() + ".new")
            staged[rel].write_bytes(data)
            staged[rel].chmod(mode)
        manifest = {"state": "prepared", "pid": os.getpid(), "target_head": snapshot.target_head,
                    "digest": snapshot.digest,
                    "files": {p: {"before": None if b is None else base64.b64encode(b).decode("ascii"),
                                  "after": base64.b64encode(updates[p]).decode("ascii"),
                                  "mode": backup_modes[p]} for p, b in backups.items()}}
        manifest_path = journal / "recovery.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        with manifest_path.open("r+b") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        _controls(snapshot)
        if is_cancelled():
            raise SyncError("publication cancelled")
        for rel in updates:
            _controls(snapshot)
            for done in completed:
                if _read_working(target, done) != updates[done]:
                    raise SyncError(f"published path changed concurrently: {done}")
            if is_cancelled():
                raise SyncError("publication cancelled")
            if _read_working(target, rel) != backups[rel]:
                raise SyncError(f"target changed before replace: {rel}")
            dest = _safe_path(target, rel, allow_missing=True)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest = _safe_path(target, rel, allow_missing=True)
            if _read_working(target, rel) != backups[rel]:
                raise SyncError(f"target changed immediately before replace: {rel}")
            os.replace(staged[rel], dest)
            completed.append(rel)
            if _read_working(target, rel) != updates[rel]:
                raise SyncError(f"published bytes failed verification: {rel}")
        _controls(snapshot)
        for rel, expected in updates.items():
            if _read_working(target, rel) != expected:
                raise SyncError(f"final hash/byte verification failed: {rel}")
        manifest_path.write_text(json.dumps({**manifest, "state": "published"}, sort_keys=True), encoding="utf-8")
        shutil.rmtree(journal)
        return PublishResult(True, tuple(updates), snapshot.digest)
    except BaseException as exc:
        safe_to_rollback = True
        if completed:
            try:
                _controls(snapshot)
            except (OSError, SyncError):
                safe_to_rollback = False
        rollback_errors = False
        if safe_to_rollback:
            for rel in reversed(completed):
                try:
                    dest = _safe_path(target, rel, allow_missing=True)
                    if _read_working(target, rel) != updates[rel]:
                        rollback_errors = True
                        continue
                    previous = backups[rel]
                    if previous is None:
                        dest.unlink()
                    else:
                        restore = journal / (hashlib.sha256((rel + ".restore").encode()).hexdigest())
                        restore.write_bytes(previous)
                        if backup_modes[rel] is not None:
                            restore.chmod(backup_modes[rel])
                        os.replace(restore, dest)
                except (OSError, SyncError):
                    rollback_errors = True
        else:
            rollback_errors = True
        if not rollback_errors and journal.exists():
            shutil.rmtree(journal)
        elif rollback_errors:
            # Keep the exclusive lock beside the recovery journal; never auto-retry over it.
            retain_lock = True
            raise SyncRecoveryRequired("worktree write recovery requires reconciliation") from exc
        raise
    finally:
        try:
            if not retain_lock and lock.read_bytes() == lock_token:
                lock.unlink()
        except OSError:
            pass
