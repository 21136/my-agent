"""Fail-closed Git object broker for isolated self-evolution experiments.

The broker never checks out or mutates a candidate. It builds trees from tracked
blobs in a temporary index and publishes only to refs/heads/experiment-auto.
"""
from __future__ import annotations

import io
import os
import re
import stat
import subprocess
import tarfile
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Mapping


TARGET_NAME = "my-agent-auto-target"
BASE_REF = "refs/heads/experiment-base"
AUTO_REF = "refs/heads/experiment-auto"
MAX_BLOB_BYTES = 1 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_PATH_BYTES = 1024


class GitExperimentError(RuntimeError):
    """An unsafe, stale, or invalid experiment operation was rejected."""


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _canonical_no_links(path: Path, *, label: str) -> Path:
    candidate = Path(os.path.abspath(os.fspath(path)))
    # Inspect every existing component, including ancestors, before resolve()
    # so a junction/symlink cannot silently redirect an authorized path.
    probe = Path(candidate.anchor)
    for component in candidate.parts[1:]:
        probe = probe / component
        try:
            info = probe.lstat()
        except OSError as exc:
            raise GitExperimentError(f"{label} does not exist or cannot be inspected") from exc
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise GitExperimentError(f"{label} contains a link or reparse point")
    resolved = candidate.resolve(strict=True)
    if os.path.normcase(os.path.normpath(os.fspath(candidate))) != os.path.normcase(os.path.normpath(os.fspath(resolved))):
        raise GitExperimentError(f"{label} is not canonical")
    return resolved


def _validate_repo_path(path: Path, allowed_parent: Path) -> tuple[Path, Path]:
    parent = _canonical_no_links(allowed_parent, label="allowed_parent")
    root = _canonical_no_links(path, label="experiment path")
    if root.parent != parent or root.name != TARGET_NAME:
        raise GitExperimentError(f"experiment must be the direct child named {TARGET_NAME!r} of allowed_parent")
    if root == Path(root.anchor) or root == parent:
        raise GitExperimentError("protected root paths cannot be experiment targets")
    protected = [os.environ.get("WINDIR"), os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramData")]
    for raw in protected:
        if raw:
            try:
                blocked = Path(os.path.abspath(raw)).resolve(strict=False)
                if root == blocked or blocked in root.parents:
                    raise GitExperimentError("protected system paths cannot be experiment targets")
            except OSError:
                continue
    return root, parent


def _validate_relpath(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise GitExperimentError("path must be a non-empty relative UTF-8 path")
    if "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise GitExperimentError("path traversal, drive paths, and Windows aliases are forbidden")
    try:
        raw = value.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise GitExperimentError("path is not valid UTF-8") from exc
    if unicodedata.normalize("NFC", value) != value:
        raise GitExperimentError("non-canonical Unicode path spellings are forbidden")
    if len(raw) > MAX_PATH_BYTES:
        raise GitExperimentError("path exceeds the configured bound")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise GitExperimentError("path traversal is forbidden")
    for part in parts:
        if part.endswith((".", " ")) or ":" in part:
            raise GitExperimentError("Windows path aliases and alternate streams are forbidden")
        if part.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            raise GitExperimentError("Windows device-name paths are forbidden")
        if part.casefold() == ".git" or part.casefold().startswith(".git"):
            raise GitExperimentError("Git administrative paths are forbidden")
    return value


def _is_test_path(path: str) -> bool:
    parts = path.casefold().split("/")
    name = parts[-1]
    return any(part in {"test", "tests", "__tests__"} for part in parts[:-1]) or name.startswith("test_") or name.endswith("_test.py")


class GitExperiment:
    """Object-level Git broker for one independent, dedicated experiment clone."""

    def __init__(self, root: Path, timeout: float):
        self.root = root
        self.timeout = timeout
        self._object_format = "sha1"

    @classmethod
    def open(cls, path: Path, *, allowed_parent: Path, timeout: float = 20) -> "GitExperiment":
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0 or timeout > 300:
            raise GitExperimentError("timeout must be a positive number no greater than 300 seconds")
        root, _parent = _validate_repo_path(Path(path), Path(allowed_parent))
        dotgit = root / ".git"
        try:
            info = dotgit.lstat()
        except OSError as exc:
            raise GitExperimentError("target must be an independent non-bare Git clone") from exc
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise GitExperimentError("linked worktrees and redirected Git metadata are forbidden")
        config = dotgit / "config"
        try:
            config_info = config.lstat()
        except OSError as exc:
            raise GitExperimentError("Git metadata is incomplete") from exc
        if not stat.S_ISREG(config_info.st_mode) or stat.S_ISLNK(config_info.st_mode) or _is_reparse(config_info):
            raise GitExperimentError("Git configuration must be a regular in-repository file")
        for admin_path, expect_dir in ((dotgit / "objects", True), (dotgit / "refs", True), (dotgit / "HEAD", False)):
            try:
                admin_info = admin_path.lstat()
            except OSError as exc:
                raise GitExperimentError("Git administrative metadata is incomplete") from exc
            if stat.S_ISLNK(admin_info.st_mode) or _is_reparse(admin_info) or (stat.S_ISDIR(admin_info.st_mode) != expect_dir):
                raise GitExperimentError("Git administrative paths must be in-repository regular paths")
        alternates = dotgit / "objects" / "info" / "alternates"
        try:
            alternates.lstat()
        except FileNotFoundError:
            pass
        else:
            raise GitExperimentError("object-alternate clones are forbidden")
        broker = cls(root, float(timeout))
        if broker._run("rev-parse", "--is-bare-repository").strip() != "false":
            raise GitExperimentError("bare repositories are forbidden")
        top = Path(broker._run("rev-parse", "--show-toplevel").strip()).resolve(strict=True)
        git_dir = Path(broker._run("rev-parse", "--absolute-git-dir").strip()).resolve(strict=True)
        common_dir_text = broker._run("rev-parse", "--git-common-dir").strip()
        common_dir = Path(common_dir_text)
        if not common_dir.is_absolute():
            common_dir = root / common_dir
        common_dir = common_dir.resolve(strict=True)
        if top != root or git_dir != dotgit.resolve(strict=True) or common_dir != git_dir:
            raise GitExperimentError("target must be a canonical independent clone, not a linked worktree")
        branch = broker._run("symbolic-ref", "-q", "HEAD").strip()
        if branch != BASE_REF:
            raise GitExperimentError("HEAD must be checked out on the dedicated experiment-base branch")
        broker._assert_control_refs_direct()
        broker._object_format = broker._run("rev-parse", "--show-object-format").strip()
        if broker._object_format not in {"sha1", "sha256"}:
            raise GitExperimentError("unsupported Git object format")
        broker._resolve_commit(broker._ref(BASE_REF))
        # Reject a malicious local config that changes the executable/index plumbing.
        if broker._run("config", "--bool", "--get", "core.bare", check=False).strip().lower() == "true":
            raise GitExperimentError("repository configuration marks the target bare")
        return broker

    def _env(self, *, index_file: str | None = None) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        env.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_AUTHOR_NAME": "My Agent Experiment",
            "GIT_AUTHOR_EMAIL": "my-agent-experiment@localhost",
            "GIT_COMMITTER_NAME": "My Agent Experiment",
            "GIT_COMMITTER_EMAIL": "my-agent-experiment@localhost",
        })
        if index_file:
            env["GIT_INDEX_FILE"] = index_file
        return env

    def _run(self, *args: str, input_data: bytes | None = None, check: bool = True, env: dict[str, str] | None = None) -> str:
        try:
            with tempfile.TemporaryDirectory(prefix="my-agent-auto-hooks-disabled-", dir=self.root / ".git") as hooks_disabled:
                result = subprocess.run(
                    [
                        "git", "--git-dir", str(self.root / ".git"), "--work-tree", str(self.root),
                        "--no-pager", "--no-replace-objects",
                        "-c", f"core.hooksPath={hooks_disabled}",
                        "-c", "core.fsmonitor=false", "-c", "commit.gpgSign=false",
                        "-c", "gc.auto=0", "-c", "maintenance.auto=false", *args,
                    ],
                    cwd=self.root, env=env or self._env(), input=input_data,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=self.timeout, check=False,
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitExperimentError(f"Git operation failed or exceeded {self.timeout:g}s timeout") from exc
        if check and result.returncode:
            detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
            message = detail[-1][:300] if detail else f"exit {result.returncode}"
            raise GitExperimentError(f"Git operation rejected ({message})")
        if not check and result.returncode:
            return ""
        try:
            return result.stdout.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise GitExperimentError("Git returned non-UTF-8 path or metadata output") from exc

    def _assert_control_refs_direct(self) -> None:
        for ref in (BASE_REF, AUTO_REF):
            target = self._run("symbolic-ref", "--quiet", ref, check=False).strip()
            if target:
                raise GitExperimentError(f"symbolic control ref is forbidden: {ref}")

    def _ref(self, ref: str) -> str:
        value = self._run("rev-parse", "--verify", "--quiet", ref, check=False).strip()
        return value

    def _resolve_commit(self, value: str) -> str:
        expected = 40 if self._object_format == "sha1" else 64
        if not isinstance(value, str) or len(value) != expected or not re.fullmatch(r"[0-9a-fA-F]+", value):
            raise GitExperimentError("base must be a full Git commit object ID")
        resolved = self._run("rev-parse", "--verify", "--quiet", f"{value}^{{commit}}", check=False).strip()
        if resolved.lower() != value.lower():
            raise GitExperimentError("base commit does not exist in this repository")
        return resolved

    def head(self) -> str:
        auto = self._ref(AUTO_REF)
        if auto:
            return self._resolve_commit(auto)
        return self._resolve_commit(self._ref(BASE_REF))

    def _tree_entries(self, treeish: str) -> dict[str, tuple[str, str]]:
        self._resolve_commit(treeish)
        output = self._run("ls-tree", "-r", "-z", "--full-tree", treeish)
        entries: dict[str, tuple[str, str]] = {}
        for record in output.split("\x00"):
            if not record:
                continue
            try:
                metadata, path = record.split("\t", 1)
                mode, kind, oid = metadata.split(" ", 2)
            except ValueError as exc:
                raise GitExperimentError("malformed Git tree entry") from exc
            entries[path] = (mode, oid) if kind == "blob" else (mode, "")
        return entries

    def _regular_blob(self, entries: Mapping[str, tuple[str, str]], path: str) -> str:
        entry = entries.get(path)
        if entry is None:
            raise GitExperimentError(f"path is not tracked at the requested base: {path}")
        mode, oid = entry
        if mode not in {"100644", "100755"} or not oid:
            raise GitExperimentError(f"only regular tracked files are permitted: {path}")
        return oid

    def _blob(self, oid: str) -> bytes:
        try:
            payload = subprocess.run(
                [
                    "git", "--git-dir", str(self.root / ".git"), "--work-tree", str(self.root),
                    "--no-pager", "--no-replace-objects",
                    "-c", "core.fsmonitor=false", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
                    "cat-file", "blob", oid,
                ],
                cwd=self.root, env=self._env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=self.timeout, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitExperimentError("could not read Git blob") from exc
        if payload.returncode:
            raise GitExperimentError("Git blob is unavailable")
        if len(payload.stdout) > MAX_BLOB_BYTES:
            raise GitExperimentError("tracked file exceeds the per-file size bound")
        return payload.stdout

    def _require_current_base(self, base: str) -> str:
        commit = self._resolve_commit(base)
        if self.head() != commit:
            raise GitExperimentError("stale base: supplied commit is not the current experiment tip")
        return commit

    def source(self, base: str, paths: tuple[str, ...]) -> dict[str, str]:
        commit = self._require_current_base(base)
        entries = self._tree_entries(commit)
        checked = [_validate_relpath(path) for path in paths]
        if len({path.casefold() for path in checked}) != len(checked):
            raise GitExperimentError("duplicate or Windows case-alias paths are forbidden")
        result: dict[str, str] = {}
        total = 0
        for path in checked:
            if not path.startswith("agent-core/"):
                raise GitExperimentError("source paths must be under agent-core/")
            blob = self._blob(self._regular_blob(entries, path))
            total += len(blob)
            if total > MAX_TOTAL_BYTES:
                raise GitExperimentError("requested source exceeds the total size bound")
            try:
                result[path] = blob.decode("utf-8", "strict")
            except UnicodeDecodeError as exc:
                raise GitExperimentError(f"tracked file is not UTF-8 text: {path}") from exc
        return result

    def prepare(self, base: str, edits: dict[str, str]) -> str:
        commit = self._require_current_base(base)
        if not isinstance(edits, dict) or not edits:
            raise GitExperimentError("edits must be a non-empty mapping")
        entries = self._tree_entries(commit)
        checked: list[tuple[str, bytes]] = []
        total = 0
        seen: set[str] = set()
        for path, text in edits.items():
            path = _validate_relpath(path)
            if not path.startswith("agent-core/"):
                raise GitExperimentError("editable paths must be under agent-core/")
            alias = path.casefold()
            if alias in seen:
                raise GitExperimentError("duplicate or Windows case-alias edit paths are forbidden")
            seen.add(alias)
            if _is_test_path(path):
                raise GitExperimentError("candidate edits may not modify tests")
            self._regular_blob(entries, path)
            if not isinstance(text, str):
                raise GitExperimentError("edited file content must be text")
            try:
                content = text.encode("utf-8", "strict")
            except UnicodeError as exc:
                raise GitExperimentError("edited content must be valid UTF-8") from exc
            if len(content) > MAX_BLOB_BYTES:
                raise GitExperimentError(f"edited file exceeds the per-file size bound: {path}")
            total += len(content)
            if total > MAX_TOTAL_BYTES:
                raise GitExperimentError("edited content exceeds the total size bound")
            checked.append((path, content))
        with tempfile.TemporaryDirectory(prefix="my-agent-auto-index-", dir=self.root / ".git") as scratch:
            index_path = os.path.join(scratch, "index")
            env = self._env(index_file=index_path)
            self._run("read-tree", commit, env=env)
            for path, content in checked:
                oid = self._run("hash-object", "-w", "--stdin", input_data=content).strip()
                mode, _old_oid = entries[path]
                self._run("update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}", env=env)
            tree = self._run("write-tree", env=env).strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", tree):
            raise GitExperimentError("Git returned an invalid candidate tree ID")
        self._run("cat-file", "-e", f"{tree}^{{tree}}")
        return tree

    def archive(self, tree: str, paths: tuple[str, ...]) -> bytes:
        if not isinstance(tree, str) or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", tree):
            raise GitExperimentError("tree must be a full Git tree object ID")
        self._run("cat-file", "-e", f"{tree}^{{tree}}")
        entries = self._tree_entries_from_tree(tree)
        checked = [_validate_relpath(path) for path in paths]
        if len({path.casefold() for path in checked}) != len(checked):
            raise GitExperimentError("duplicate or Windows case-alias archive paths are forbidden")
        if not checked:
            raise GitExperimentError("archive requires explicit paths")
        output = io.BytesIO()
        total = 0
        try:
            with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path in checked:
                    if not path.startswith("agent-core/") or not path.endswith(".py"):
                        raise GitExperimentError("runner archives accept explicit agent-core .py paths only")
                    content = self._blob(self._regular_blob(entries, path))
                    total += len(content)
                    if total > MAX_ARCHIVE_BYTES:
                        raise GitExperimentError("archive exceeds the total size bound")
                    info = tarfile.TarInfo(path)
                    info.size = len(content)
                    info.mode = 0o644
                    info.mtime = 0
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    archive.addfile(info, io.BytesIO(content))
        except (tarfile.TarError, OSError) as exc:
            raise GitExperimentError("could not build bounded runner archive") from exc
        payload = output.getvalue()
        if len(payload) > MAX_ARCHIVE_BYTES:
            raise GitExperimentError("archive exceeds the total size bound")
        return payload

    def _tree_entries_from_tree(self, tree: str) -> dict[str, tuple[str, str]]:
        output = self._run("ls-tree", "-r", "-z", "--full-tree", tree)
        entries: dict[str, tuple[str, str]] = {}
        for record in output.split("\x00"):
            if not record:
                continue
            try:
                metadata, path = record.split("\t", 1)
                mode, kind, oid = metadata.split(" ", 2)
            except ValueError as exc:
                raise GitExperimentError("malformed Git tree entry") from exc
            entries[path] = (mode, oid) if kind == "blob" else (mode, "")
        return entries

    def publish(self, base: str, tree: str, message: str) -> str:
        commit_base = self._resolve_commit(base)
        if not isinstance(tree, str) or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", tree):
            raise GitExperimentError("tree must be a full Git tree object ID")
        self._run("cat-file", "-e", f"{tree}^{{tree}}")
        base_entries = self._tree_entries(commit_base)
        candidate_entries = self._tree_entries_from_tree(tree)
        changed = {path for path in set(base_entries) | set(candidate_entries) if base_entries.get(path) != candidate_entries.get(path)}
        if not changed:
            raise GitExperimentError("candidate tree contains no changes")
        for path in changed:
            _validate_relpath(path)
            if not path.startswith("agent-core/"):
                raise GitExperimentError("candidate paths must be under agent-core/")
            if _is_test_path(path):
                raise GitExperimentError("candidate tree changes tests")
            self._regular_blob(base_entries, path)
            self._regular_blob(candidate_entries, path)
        self._assert_control_refs_direct()
        auto_now = self._ref(AUTO_REF)
        if auto_now and self._is_checked_out(AUTO_REF):
            raise GitExperimentError("experiment-auto is checked out in a worktree")
        if auto_now and auto_now != commit_base:
            raise GitExperimentError("stale base: experiment-auto no longer equals the supplied base")
        if not auto_now and self._ref(BASE_REF) != commit_base:
            raise GitExperimentError("stale base: experiment-base no longer equals the supplied base")
        if not isinstance(message, str) or not message.strip() or "\x00" in message or len(message.encode("utf-8", "strict")) > 2000:
            raise GitExperimentError("commit message must be non-empty UTF-8 text no longer than 2000 bytes")
        timestamp = str(int(time.time())) + " +0000"
        commit = self._run("-c", "commit.gpgSign=false", "commit-tree", tree, "-p", commit_base, "-m", message,
                           env={**self._env(), "GIT_AUTHOR_DATE": timestamp, "GIT_COMMITTER_DATE": timestamp}).strip()
        self._resolve_commit(commit)
        if self._ref(AUTO_REF):
            # update <ref> <new> <old> is itself an atomic compare-and-swap.
            transaction = f"start\nupdate {AUTO_REF} {commit} {commit_base}\nprepare\ncommit\n"
        else:
            transaction = f"start\nverify {BASE_REF} {commit_base}\ncreate {AUTO_REF} {commit}\nprepare\ncommit\n"
        self._run("update-ref", "--stdin", input_data=transaction.encode("ascii"))
        if self._ref(AUTO_REF) != commit:
            raise GitExperimentError("publish compare-and-swap did not install the candidate commit")
        return commit

    def _is_checked_out(self, ref: str) -> bool:
        branch = "refs/heads/" + ref.removeprefix("refs/heads/")
        output = self._run("worktree", "list", "--porcelain")
        return any(line.strip() == f"branch {branch}" for line in output.splitlines())
