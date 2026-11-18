"""Fail-closed promotion of a pinned worker commit into a checked-out stable repo."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import uuid
from typing import Callable

from self_evolution_auto_runner import DockerVerifier


@dataclass(frozen=True, slots=True)
class PromotionResult:
    status: str
    detail: str
    commit: str | None = None


class _Blocked(RuntimeError):
    pass


class _Drift(RuntimeError):
    pass


class _Cancelled(RuntimeError):
    pass


class StablePromoter:
    """Project only a one-parent, policy-scoped worker delta onto stable HEAD."""

    _MAX_ATTEMPTS = 3
    _MAX_FILE_BYTES = 1 * 1024 * 1024
    _MAX_ARCHIVE_BYTES = 8 * 1024 * 1024

    def __init__(self, stable_root: Path, policy, *, resolve=None):
        if resolve is not None and not callable(resolve):
            raise TypeError("resolve must be callable or None")
        self.stable_root = Path(stable_root)
        self.policy = policy
        self.resolve = resolve

    def promote(self, repo, commit: str, *, cancelled: Callable[[], bool]) -> PromotionResult:
        try:
            root = self._root()
            delta = self._worker_delta(repo, commit)
            if not delta:
                return PromotionResult("no_change", "worker commit contains no scoped changes")
            verifier = DockerVerifier(
                self.policy.test_image, self.policy.tests,
                timeout=self.policy.test_timeout_sec,
                allowed_paths=self.policy.verification_files,
            )
            verifier.preflight()
            _, expected_branch, _ = self._stable_state(root)
            for attempt in range(self._MAX_ATTEMPTS):
                self._check_cancelled(cancelled)
                head, branch, git_dir = self._stable_state(root)
                if branch != expected_branch:
                    return PromotionResult("blocked", "stable checkout switched branches; no promotion attempted")
                if any(git_dir.glob("stable-promote-recovery-*/journal.json")):
                    return PromotionResult("blocked", "an earlier stable promotion has unresolved recovery material")
                projected = self._project(root, head, delta)
                if not projected:
                    return PromotionResult("no_change", "worker delta is already present on stable")
                snapshot = self._snapshot(root, git_dir, head, branch, tuple(sorted(projected)))
                dirty = self._working_projection(root, head, projected, snapshot["working"])
                baseline_archive = self._verification_archive(root, head, {})
                archive = self._verification_archive(root, head, projected)
                try:
                    baseline = verifier.run(baseline_archive, cancelled=cancelled)
                except Exception as exc:
                    return PromotionResult("failed", f"stable baseline verification failed: {self._safe_detail(exc)}")
                self._check_cancelled(cancelled)
                if not getattr(baseline, "passed", False):
                    return PromotionResult("failed", "latest stable baseline did not pass: " + self._safe_detail(getattr(baseline, "summary", "verification failed")))
                try:
                    verification = verifier.run(archive, cancelled=cancelled)
                except Exception as exc:
                    return PromotionResult("failed", f"candidate verification failed: {self._safe_detail(exc)}")
                self._check_cancelled(cancelled)
                if not getattr(verification, "passed", False):
                    return PromotionResult("failed", self._safe_detail(getattr(verification, "summary", "candidate verification failed")))
                try:
                    stable_commit = self._commit_if_current(
                        root, git_dir, head, branch, projected, dirty, snapshot, cancelled
                    )
                    return PromotionResult("promoted", "verified scoped delta promoted", stable_commit)
                except _Drift:
                    if attempt + 1 < self._MAX_ATTEMPTS:
                        continue
                    return PromotionResult("blocked", "stable repository kept changing during verification")
            return PromotionResult("blocked", "stable drift retry limit reached")
        except _Cancelled:
            return PromotionResult("blocked", "promotion cancelled")
        except _Blocked as exc:
            return PromotionResult("blocked", self._safe_detail(exc))
        except Exception as exc:
            return PromotionResult("failed", self._safe_detail(exc))

    @staticmethod
    def _safe_detail(value) -> str:
        text = str(value).replace("\x00", " ").strip()
        return text[:500] or "operation failed"

    @staticmethod
    def _check_cancelled(cancelled: Callable[[], bool]) -> None:
        if not callable(cancelled) or cancelled():
            raise _Cancelled()

    def _root(self) -> Path:
        try:
            root = self.stable_root.resolve(strict=True)
            if not root.is_dir() or root != self.stable_root.absolute():
                raise _Blocked("stable root must be an existing canonical directory")
        except OSError as exc:
            raise _Blocked("stable root cannot be inspected") from exc
        return root

    def _git(self, root: Path, *args: str, env: dict[str, str] | None = None, check: bool = True) -> bytes:
        safe_env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        safe_env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                         "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
                         "GIT_ATTR_NOSYSTEM": "1", "GIT_NO_REPLACE_OBJECTS": "1"})
        if env:
            safe_env.update(env)
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "--no-pager", "--no-replace-objects",
                 "-c", f"safe.directory={root.as_posix()}",
                 "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull,
                 "-c", "commit.gpgSign=false", "-c", "gc.auto=0", "-c", "maintenance.auto=false", *args],
                cwd=root, env=safe_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("bounded Git operation failed") from exc
        if check and result.returncode:
            message = result.stderr.decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError("Git rejected operation" + (": " + message[-1][:250] if message else ""))
        if not check and result.returncode:
            return b""
        return result.stdout

    def _stable_state(self, root: Path) -> tuple[str, str, Path]:
        if self._git(root, "rev-parse", "--is-bare-repository").strip() != b"false":
            raise _Blocked("stable target must be a non-bare checked-out repository")
        top = Path(os.fsdecode(self._git(root, "rev-parse", "--show-toplevel").strip())).resolve(strict=True)
        if top != root:
            raise _Blocked("stable root is not the repository top-level")
        branch = os.fsdecode(self._git(root, "symbolic-ref", "-q", "HEAD").strip())
        if not branch.startswith("refs/heads/") or branch == "refs/heads/" or branch.startswith("refs/heads/experiment/"):
            raise _Blocked("stable HEAD must be checked out on a local branch")
        head = os.fsdecode(self._git(root, "rev-parse", "--verify", "HEAD^{commit}").strip())
        git_dir = Path(os.fsdecode(self._git(root, "rev-parse", "--absolute-git-dir").strip())).resolve(strict=True)
        return head, branch, git_dir

    @staticmethod
    def _policy_paths(policy, field: str) -> tuple[str, ...]:
        paths = getattr(policy, field, None)
        if not isinstance(paths, tuple) or not paths:
            raise _Blocked(f"policy {field} must be a non-empty tuple")
        checked = []
        seen = set()
        for path in paths:
            if (not isinstance(path, str) or not path.startswith("agent-core/")
                    or "\\" in path or "\x00" in path or path.startswith("/")
                    or any(part in ("", ".", "..") for part in path.split("/"))
                    or not path.endswith(".py")):
                raise _Blocked(f"policy contains an unsafe path: {path!r}")
            key = path.casefold()
            if key in seen:
                raise _Blocked("policy contains duplicate or case-alias paths")
            seen.add(key)
            checked.append(path)
        return tuple(checked)

    def _worker_delta(self, repo, commit: str) -> dict[str, tuple[bytes, bytes, str]]:
        editable = set(self._policy_paths(self.policy, "editable_files"))
        expected = 40 if getattr(repo, "_object_format", "sha1") == "sha1" else 64
        if not isinstance(commit, str) or not re.fullmatch(rf"[0-9a-fA-F]{{{expected}}}", commit):
            raise _Blocked("worker commit must be a full object ID")
        commit = repo._resolve_commit(commit)
        parents = repo._run("rev-list", "--parents", "-n", "1", commit).strip().split()
        if len(parents) != 2:
            raise _Blocked("worker commit must have exactly one parent")
        parent = repo._resolve_commit(parents[1])
        before = repo._tree_entries(parent)
        after = repo._tree_entries(commit)
        changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
        if not changed:
            return {}
        if not changed <= editable:
            raise _Blocked("worker commit changes paths outside policy.editable_files")
        delta = {}
        for path in sorted(changed):
            old_entry, new_entry = before.get(path), after.get(path)
            if not old_entry or not new_entry or old_entry[0] not in {"100644", "100755"} or new_entry[0] not in {"100644", "100755"}:
                raise _Blocked("worker delta may modify existing regular files only")
            if old_entry[0] != new_entry[0]:
                raise _Blocked("worker delta may not change file modes")
            base, candidate = repo._blob(old_entry[1]), repo._blob(new_entry[1])
            if len(base) > self._MAX_FILE_BYTES or len(candidate) > self._MAX_FILE_BYTES:
                raise _Blocked("worker delta exceeds the per-file size bound")
            try:
                base.decode("utf-8", "strict")
                candidate.decode("utf-8", "strict")
            except UnicodeDecodeError as exc:
                raise _Blocked("worker delta must be UTF-8 text") from exc
            delta[path] = (base, candidate, new_entry[0])
        return delta

    def _tree_entry(self, root: Path, commit: str, path: str) -> tuple[str, str]:
        output = self._git(root, "ls-tree", "-z", commit, "--", path)
        records = output.split(b"\x00")
        if len(records) < 2 or not records[0]:
            raise _Blocked(f"stable commit does not track required regular file {path}")
        try:
            meta, found = records[0].split(b"\t", 1)
            mode, kind, oid = meta.decode("ascii").split(" ")
            found_path = found.decode("utf-8", "strict")
        except (ValueError, UnicodeDecodeError) as exc:
            raise _Blocked("stable tree contains malformed path metadata") from exc
        if found_path != path or kind != "blob" or mode not in {"100644", "100755"}:
            raise _Blocked(f"stable path is not a regular file: {path}")
        return mode, oid

    def _committed_blob(self, root: Path, commit: str, path: str) -> tuple[str, bytes]:
        mode, oid = self._tree_entry(root, commit, path)
        data = self._git(root, "cat-file", "blob", oid)
        if len(data) > self._MAX_FILE_BYTES:
            raise _Blocked(f"stable file exceeds the size bound: {path}")
        try:
            data.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise _Blocked(f"stable file is not UTF-8 text: {path}") from exc
        return mode, data

    def _merge(self, root: Path, path: str, base: bytes, ours: bytes, theirs: bytes, *, allow_resolve: bool = False) -> bytes:
        if ours == base:
            return theirs
        if theirs == base or ours == theirs:
            return ours
        crlf = b"\r\n" in ours and ours.replace(b"\r\n", b"").find(b"\n") < 0
        normalized = tuple(data.replace(b"\r\n", b"\n") for data in (ours, base, theirs))
        with tempfile.TemporaryDirectory(prefix="stable-promote-merge-") as scratch:
            names = ("ours", "base", "theirs")
            for name, data in zip(names, normalized):
                Path(scratch, name).write_bytes(data)
            result = subprocess.run(
                ["git", "-C", str(root), "-c", f"safe.directory={root.as_posix()}", "--no-pager",
                 "merge-file", "--diff3", "--marker-size=16", "-p",
                 "-L", "SYNC_TARGET", "-L", "SYNC_BASE", "-L", "SYNC_SOURCE",
                 *(str(Path(scratch, name)) for name in names)],
                cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False,
                env=self._git_env({}),
            )
            if not 0 <= result.returncode <= 127:
                raise _Blocked(f"three-way merge failed for {path}")
            if result.returncode:
                if not allow_resolve or self.resolve is None:
                    raise _Blocked(f"irreconcilable same-file changes in {path}")
                try:
                    resolved = self.resolve(path, result.stdout)
                except Exception as exc:
                    raise _Blocked(f"conflict resolver failed for {path}: {self._safe_detail(exc)}") from exc
                if resolved is None:
                    raise _Blocked(f"conflict resolver declined {path}")
                if not isinstance(resolved, bytes):
                    raise _Blocked("conflict resolver must return bytes or None")
                if len(resolved) > self._MAX_FILE_BYTES:
                    raise _Blocked(f"resolved file exceeds size bound: {path}")
                try:
                    resolved.decode("utf-8", "strict")
                except UnicodeDecodeError as exc:
                    raise _Blocked(f"resolved file is not UTF-8 text: {path}") from exc
                return resolved
            return result.stdout.replace(b"\n", b"\r\n") if crlf else result.stdout

    def _project(self, root: Path, head: str, delta: dict[str, tuple[bytes, bytes, str]]) -> dict[str, tuple[str, bytes]]:
        projected = {}
        for path, (base, worker, mode) in delta.items():
            stable_mode, stable = self._committed_blob(root, head, path)
            if stable_mode != mode:
                raise _Blocked(f"stable file mode drift conflicts with worker change: {path}")
            combined = self._merge(root, path, base, stable, worker, allow_resolve=True)
            if combined != stable:
                projected[path] = (stable_mode, combined)
        return projected

    def _working_projection(self, root: Path, head: str, projected: dict[str, tuple[str, bytes]],
                            working_snapshot: dict[str, bytes]) -> dict[str, bytes]:
        staged = set(os.fsdecode(x) for x in self._git(root, "diff", "--cached", "--name-only", "--no-renames", "-z").split(b"\x00") if x)
        if staged.intersection(projected):
            raise _Blocked("same-path staged stable changes cannot be promoted")
        result = {}
        for path, (_mode, committed) in projected.items():
            target = root.joinpath(*PurePosixPath(path).parts)
            try:
                info = target.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                    raise _Blocked(f"affected working path is not a regular file: {path}")
                working = working_snapshot[path]
            except FileNotFoundError as exc:
                raise _Blocked(f"affected working file is missing: {path}") from exc
            if len(working) > self._MAX_FILE_BYTES:
                raise _Blocked(f"affected working file exceeds the size bound: {path}")
            base = self._committed_blob(root, head, path)[1]
            result[path] = self._merge(root, path, base, working, committed)
        return result

    def _verification_archive(self, root: Path, head: str, projected: dict[str, tuple[str, bytes]]) -> bytes:
        allowed = self._policy_paths(self.policy, "verification_files")
        contents = {}
        total = 0
        for path in allowed:
            content = projected[path][1] if path in projected else self._committed_blob(root, head, path)[1]
            total += len(content)
            if total > self._MAX_ARCHIVE_BYTES:
                raise _Blocked("verification input exceeds archive size bound")
            contents[path] = content
        import io
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for path, content in contents.items():
                info = tarfile.TarInfo(path)
                info.size = len(content)
                info.mode = 0o644
                info.mtime = 0
                archive.addfile(info, io.BytesIO(content))
        return stream.getvalue()

    def _snapshot(self, root: Path, git_dir: Path, head: str, branch: str, paths: tuple[str, ...]):
        index = Path(os.fsdecode(self._git(root, "rev-parse", "--git-path", "index").strip()))
        if not index.is_absolute():
            index = root / index
        index = index.resolve(strict=True)
        if git_dir not in index.parents:
            raise _Blocked("Git index path is outside its administrative directory")
        index_bytes = index.read_bytes()
        working = {}
        working_identity = {}
        for path in paths:
            target = root.joinpath(*PurePosixPath(path).parts)
            info = target.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise _Drift("affected working file changed type")
            working[path] = target.read_bytes()
            working_identity[path] = self._identity(target)
        return {"head": head, "branch": branch, "index_path": index, "index": index_bytes,
                "index_hash": hashlib.sha256(index_bytes).digest(), "working": working,
                "working_identity": working_identity}

    @staticmethod
    def _identity(path: Path):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise _Drift("transaction path changed type")
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)

    @staticmethod
    def _file_matches(path: Path, content: bytes, identity) -> bool:
        try:
            return StablePromoter._identity(path) == identity and path.read_bytes() == content
        except (OSError, _Drift):
            return False

    @staticmethod
    def _write_fsynced(path: Path, content: bytes) -> None:
        with path.open("wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    def _persist_journal(self, journal_dir: Path, manifest: dict) -> None:
        import json
        temp = journal_dir / "journal.json.tmp"
        data = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self._write_fsynced(temp, data)
        os.replace(temp, journal_dir / "journal.json")
        try:
            fd = os.open(journal_dir, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass

    def _commit_if_current(self, root: Path, git_dir: Path, head: str, branch: str,
                           projected: dict[str, tuple[str, bytes]], dirty: dict[str, bytes], snapshot,
                           cancelled: Callable[[], bool]) -> str:
        paths = tuple(sorted(projected))
        self._check_cancelled(cancelled)
        if self._stable_state(root)[:2] != (head, branch):
            raise _Drift("stable HEAD changed during verification")
        if snapshot["index_hash"] != hashlib.sha256(snapshot["index_path"].read_bytes()).digest():
            raise _Drift("stable index changed during verification")
        for path, content in snapshot["working"].items():
            target = root.joinpath(*PurePosixPath(path).parts)
            if not self._file_matches(target, content, snapshot["working_identity"][path]):
                raise _Drift("affected stable working file changed during verification")

        scratch_dir = Path(tempfile.mkdtemp(prefix="stable-promote-", dir=git_dir))
        index_file = scratch_dir / "index"
        lock = Path(str(snapshot["index_path"]) + ".lock")
        recovery_ref = "refs/codex/stable-promote-recovery/" + uuid.uuid4().hex
        journal_dir = git_dir / ("stable-promote-recovery-" + uuid.uuid4().hex)
        ref_updated = False
        recovery_created = False
        lock_fd = None
        lock_identity = None
        journal_ready = False
        index_installed = False
        commit: str | None = None
        index_bytes: bytes | None = None
        manifest = {"version": 1, "phase": "preparing", "head_before": head,
                    "branch": branch, "stable_commit": None, "recovery_ref": recovery_ref,
                    "index_path": str(snapshot["index_path"]),
                    "index_before_sha256": hashlib.sha256(snapshot["index"]).hexdigest(),
                    "index_after_sha256": None, "index_installed_identity": None,
                    "working": []}
        try:
            env = {"GIT_INDEX_FILE": str(index_file), "GIT_AUTHOR_NAME": "My Agent Stable Promoter",
                   "GIT_AUTHOR_EMAIL": "my-agent-stable-promoter@localhost",
                   "GIT_COMMITTER_NAME": "My Agent Stable Promoter",
                   "GIT_COMMITTER_EMAIL": "my-agent-stable-promoter@localhost"}
            self._git(root, "read-tree", head, env=env)
            for path, (mode, content) in projected.items():
                blob = subprocess.run(["git", "-C", str(root), "-c", f"safe.directory={root.as_posix()}",
                                       "hash-object", "-w", "--stdin"], input=content,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self._git_env(env),
                                      timeout=15, check=False)
                if blob.returncode:
                    raise RuntimeError("could not write candidate blob")
                oid = blob.stdout.decode("ascii").strip()
                self._git(root, "update-index", "--cacheinfo", f"{mode},{oid},{path}", env=env)
            tree = os.fsdecode(self._git(root, "write-tree", env=env).strip())
            commit = os.fsdecode(self._git(root, "commit-tree", tree, "-p", head, "-m", "Promote verified experimental worker delta", env=env).strip())
            parents = self._git(root, "rev-list", "--parents", "-n", "1", commit).decode().strip().split()
            if len(parents) != 2 or parents[1] != head:
                raise RuntimeError("generated stable commit has an unexpected parent")
            changed = set(os.fsdecode(x) for x in self._git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", head, commit).split(b"\x00") if x)
            if changed != set(paths):
                raise RuntimeError("generated stable commit failed scoped diff audit")
            for path, (expected_mode, expected_content) in projected.items():
                actual_mode, actual_content = self._committed_blob(root, commit, path)
                if actual_mode != expected_mode or actual_content != expected_content:
                    raise RuntimeError("generated stable commit failed content audit")

            final_index = scratch_dir / "final-index"
            shutil.copyfile(snapshot["index_path"], final_index)
            final_env = {"GIT_INDEX_FILE": str(final_index)}
            for path in paths:
                new_mode, oid = self._tree_entry(root, commit, path)
                self._git(root, "update-index", "--cacheinfo", f"{new_mode},{oid},{path}", env=final_env)
            index_bytes = final_index.read_bytes()
            manifest["stable_commit"] = commit
            manifest["index_after_sha256"] = hashlib.sha256(index_bytes).hexdigest()

            # Durable original bytes and planned post-state exist before the first stable write.
            journal_dir.mkdir()
            self._write_fsynced(journal_dir / "index.before", snapshot["index"])
            self._write_fsynced(journal_dir / "index.after", index_bytes)
            for number, path in enumerate(paths):
                before_path = journal_dir / f"working-{number}.before"
                after_path = journal_dir / f"working-{number}.after"
                self._write_fsynced(before_path, snapshot["working"][path])
                self._write_fsynced(after_path, dirty[path])
                manifest["working"].append({
                    "path": path, "backup": before_path.name, "postimage": after_path.name,
                    "before_sha256": hashlib.sha256(snapshot["working"][path]).hexdigest(),
                    "after_sha256": hashlib.sha256(dirty[path]).hexdigest(),
                    "before_identity": list(snapshot["working_identity"][path]),
                    "installed_identity": None, "attempted": False, "restored": False,
                })
            self._persist_journal(journal_dir, manifest)
            journal_ready = True

            lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(lock_fd, "wb") as stream:
                lock_fd = None
                stream.write(index_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            lock_identity = (self._identity(lock), hashlib.sha256(lock.read_bytes()).digest())

            current_head, current_branch, _ = self._stable_state(root)
            if current_head != head or current_branch != branch or snapshot["index_path"].read_bytes() != snapshot["index"]:
                raise _Drift("stable HEAD or index changed at write boundary")
            for path, content in snapshot["working"].items():
                target = root.joinpath(*PurePosixPath(path).parts)
                if not self._file_matches(target, content, snapshot["working_identity"][path]):
                    raise _Drift("affected stable working file changed at write boundary")
            self._check_cancelled(cancelled)

            self._git(root, "update-ref", recovery_ref, head)
            recovery_created = True
            self._git(root, "update-ref", branch, commit, head)
            ref_updated = True
            manifest["phase"] = "head_cas_complete"
            self._persist_journal(journal_dir, manifest)

            def require_transaction_head():
                if (self._stable_state(root)[:2] != (commit, branch)
                        or snapshot["index_path"].read_bytes() != snapshot["index"]):
                    raise RuntimeError("stable HEAD or index changed after ref exchange")

            for number, path in enumerate(paths):
                target = root.joinpath(*PurePosixPath(path).parts)
                item = manifest["working"][number]
                temp = target.with_name(target.name + ".stable-promote-" + uuid.uuid4().hex)
                with temp.open("xb") as stream:
                    stream.write(dirty[path])
                    stream.flush()
                    os.fsync(stream.fileno())
                planned_identity = self._identity(temp)
                require_transaction_head()
                # A concurrent writer after HEAD CAS must not be silently overwritten.
                if not self._file_matches(target, snapshot["working"][path], snapshot["working_identity"][path]):
                    temp.unlink(missing_ok=True)
                    raise RuntimeError(f"affected file changed after HEAD update: {path}")
                item["attempted"] = True
                item["installed_identity"] = list(planned_identity)
                manifest["phase"] = f"installing_working_{number}"
                self._persist_journal(journal_dir, manifest)
                try:
                    os.replace(temp, target)
                finally:
                    temp.unlink(missing_ok=True)
                # On exFAT, rename can move the directory entry and change its
                # file ID. Pin the identity at the installed path for rollback.
                installed = self._identity(target)
                if ((installed[0], *installed[2:]) != (planned_identity[0], *planned_identity[2:])
                        or not self._file_matches(target, dirty[path], installed)):
                    raise RuntimeError(f"affected file changed during promotion write: {path}")
                item["installed_identity"] = list(installed)
                item["restored"] = False
                self._persist_journal(journal_dir, manifest)

            require_transaction_head()
            # Revalidate the owned lock itself before the atomic index exchange.
            if not self._file_matches(lock, index_bytes, lock_identity[0]) or hashlib.sha256(lock.read_bytes()).digest() != lock_identity[1]:
                raise RuntimeError("owned index lock changed before exchange")
            manifest["index_exchange_attempted"] = True
            manifest["index_installed_identity"] = list(lock_identity[0])
            manifest["phase"] = "exchanging_index"
            self._persist_journal(journal_dir, manifest)
            os.replace(lock, snapshot["index_path"])
            index_installed = True
            manifest["index_installed_identity"] = list(self._identity(snapshot["index_path"]))
            manifest["phase"] = "index_installed"
            self._persist_journal(journal_dir, manifest)
            self._git(root, "update-ref", "-d", recovery_ref, head)
            recovery_created = False
            ref_updated = False
            manifest["phase"] = "complete"
            self._persist_journal(journal_dir, manifest)
            shutil.rmtree(journal_dir)
            return commit
        except Exception as exc:
            if lock_fd is not None:
                os.close(lock_fd)
            uncertain = False
            rollback_notes = []
            try:
                current_ref = os.fsdecode(self._git(root, "rev-parse", "--verify", branch, check=False).strip())
                if ref_updated and commit is not None and current_ref == commit:
                    self._git(root, "update-ref", branch, head, commit)
                    ref_updated = False
                    manifest["phase"] = "head_rolled_back"
                    self._persist_journal(journal_dir, manifest)
                elif ref_updated and current_ref != head:
                    uncertain = True
                    rollback_notes.append("stable ref moved again; HEAD rollback skipped")
            except Exception:
                uncertain = True
                rollback_notes.append("stable ref could not be safely rolled back")

            # Undo only our own postimages, proven by both content and filesystem identity.
            attempted_items = {item["path"]: item for item in manifest["working"] if item["attempted"]}
            for path, item in attempted_items.items():
                target = root.joinpath(*PurePosixPath(path).parts)
                postimage = (journal_dir / item["postimage"]).read_bytes() if journal_ready else b""
                planned_identity = tuple(item["installed_identity"]) if item["installed_identity"] else None
                if planned_identity is None or not self._file_matches(target, postimage, planned_identity):
                    uncertain = True
                    rollback_notes.append(f"{path} changed after promotion write; preserved concurrent content")
                    continue
                try:
                    restore = target.with_name(target.name + ".stable-promote-restore-" + uuid.uuid4().hex)
                    self._write_fsynced(restore, (journal_dir / item["backup"]).read_bytes())
                    restore_identity = self._identity(restore)
                    if not self._file_matches(target, postimage, planned_identity):
                        restore.unlink(missing_ok=True)
                        uncertain = True
                        rollback_notes.append(f"{path} changed during rollback; preserved concurrent content")
                        continue
                    os.replace(restore, target)
                    item["restored"] = True
                    self._persist_journal(journal_dir, manifest)
                except Exception:
                    uncertain = True
                    rollback_notes.append(f"could not restore {path}")

            # Restore the index only when our exchange actually installed the prepared postimage.
            current_index = snapshot["index_path"]
            exchange_applied = index_installed or (
                index_bytes is not None and manifest.get("index_exchange_attempted", False)
                and self._file_matches(current_index, index_bytes, lock_identity[0] if lock_identity else None)
            )
            if exchange_applied:
                expected_after = (journal_dir / "index.after").read_bytes() if journal_ready else b""
                expected_identity = tuple(manifest["index_installed_identity"]) if manifest["index_installed_identity"] else None
                if expected_identity is None or not self._file_matches(current_index, expected_after, expected_identity):
                    uncertain = True
                    rollback_notes.append("index changed after promotion; preserved concurrent index")
                else:
                    rollback_lock_fd = None
                    rollback_lock_identity = None
                    try:
                        rollback_lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                        with os.fdopen(rollback_lock_fd, "wb") as stream:
                            rollback_lock_fd = None
                            stream.write(snapshot["index"])
                            stream.flush()
                            os.fsync(stream.fileno())
                        rollback_lock_identity = self._identity(lock)
                        if not self._file_matches(current_index, expected_after, expected_identity):
                            uncertain = True
                            rollback_notes.append("index changed during rollback; preserved concurrent index")
                        else:
                            os.replace(lock, current_index)
                    except Exception:
                        uncertain = True
                        rollback_notes.append("could not safely restore original index")
                    finally:
                        if rollback_lock_fd is not None:
                            os.close(rollback_lock_fd)
                        if rollback_lock_identity is not None:
                            self._unlink_owned_lock(lock, rollback_lock_identity, hashlib.sha256(snapshot["index"]).digest())

            if recovery_created:
                if uncertain:
                    rollback_notes.append(f"recovery ref retained: {recovery_ref}")
                else:
                    try:
                        self._git(root, "update-ref", "-d", recovery_ref, head)
                        recovery_created = False
                    except Exception:
                        uncertain = True
                        rollback_notes.append(f"recovery ref retained: {recovery_ref}")
            if journal_ready:
                manifest["phase"] = "rollback_uncertain" if uncertain else "rolled_back"
                manifest["rollback_notes"] = rollback_notes
                try:
                    self._persist_journal(journal_dir, manifest)
                except Exception:
                    uncertain = True
                if not uncertain:
                    shutil.rmtree(journal_dir, ignore_errors=True)
            detail = self._safe_detail(exc)
            if uncertain and journal_ready:
                detail += f"; durable recovery journal retained at {journal_dir}"
                if rollback_notes:
                    detail += "; " + "; ".join(rollback_notes)
            if not uncertain and isinstance(exc, (_Drift, _Cancelled)):
                raise
            raise RuntimeError(detail) from exc
        finally:
            if lock_fd is not None:
                os.close(lock_fd)
            if lock_identity is not None:
                self._unlink_owned_lock(lock, lock_identity[0], lock_identity[1])
            shutil.rmtree(scratch_dir, ignore_errors=True)

    def _unlink_owned_lock(self, lock: Path, identity, digest: bytes) -> None:
        try:
            if self._identity(lock) == identity and hashlib.sha256(lock.read_bytes()).digest() == digest:
                lock.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _git_env(self, extra: dict[str, str]) -> dict[str, str]:
        result = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        result.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                       "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1"})
        result.update(extra)
        return result
