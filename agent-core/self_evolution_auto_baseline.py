"""Bounded, pre-edit follower for committed stable-tree drift.

Stable is read-only. Refresh commits are separate parents after the current
experiment tip, and their anchor ref is advanced in the same Git ref transaction.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

from self_evolution_auto_git import AUTO_REF, BASE_REF, GitExperiment, GitExperimentError, _validate_relpath

ANCHOR_REF = "refs/agent/stable-baseline"
SCOPE_TREE_REF = "refs/codex/stable-baseline-scope-tree"
_MAX_FILE = 1 * 1024 * 1024
_MAX_TOTAL = 8 * 1024 * 1024


class StableBaselineError(RuntimeError):
    """Stable drift could not be followed without violating the safe boundary."""


class _StableBaselineStale(StableBaselineError):
    """A fresh snapshot may be retried without relaxing any check."""


def _clean_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0"})
    return env


def _git_bytes(root: Path, *args: str, timeout: float = 20, check: bool = True) -> bytes:
    try:
        result = subprocess.run(["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root), "--no-pager", *args],
                                cwd=root, env=_clean_env(), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StableBaselineError("stable Git inspection failed or timed out") from exc
    if result.returncode and check:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise StableBaselineError("stable Git inspection rejected" + (f": {detail[-1][:240]}" if detail else ""))
    return result.stdout


def _git(root: Path, *args: str, timeout: float = 20, check: bool = True) -> str:
    try:
        return _git_bytes(root, *args, timeout=timeout, check=check).decode("utf-8", "strict").strip()
    except UnicodeDecodeError as exc:
        raise StableBaselineError("Git returned invalid UTF-8 metadata") from exc


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    try:
        result = subprocess.run(["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root), "--no-pager", "merge-base", "--is-ancestor", ancestor, descendant],
                                cwd=root, env=_clean_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StableBaselineError("stable ancestry check failed or timed out") from exc
    if result.returncode not in (0, 1):
        raise StableBaselineError("stable ancestry check could not be completed")
    return result.returncode == 0

def _commit(value: str) -> bool:
    return bool(re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value))


def _scoped_paths(policy: Any) -> tuple[str, ...]:
    verification = getattr(policy, "verification_files", None)
    editable = getattr(policy, "editable_files", None)
    tests = getattr(policy, "tests", None)
    if (not isinstance(verification, tuple) or not verification
            or not isinstance(editable, tuple) or not editable
            or not isinstance(tests, tuple) or not tests):
        raise StableBaselineError("policy does not expose bounded editable, verification, and fixed-test scopes")
    try:
        scope = {_validate_relpath(path) for path in verification}
        required = {_validate_relpath(path) for path in (*editable, *tests)}
    except GitExperimentError as exc:
        raise StableBaselineError("policy scope is invalid") from exc
    if not required <= scope or any(not path.startswith("agent-core/") or not path.endswith(".py") for path in scope):
        raise StableBaselineError("policy scope is invalid")
    return tuple(sorted(scope))


def _tree_entries(repo: GitExperiment, commit: str) -> dict[str, tuple[str, str]]:
    return repo._tree_entries(commit)

def _stable_tree_entries(root: Path, commit: str) -> dict[str, tuple[str, str]]:
    raw = _git_bytes(root, "ls-tree", "-r", "-z", "--full-tree", commit)
    entries: dict[str, tuple[str, str]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path_bytes = record.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split(" ", 2)
            path = path_bytes.decode("utf-8", "strict")
        except (ValueError, UnicodeDecodeError) as exc:
            raise StableBaselineError("stable commit contains an invalid tree entry") from exc
        if kind == "blob":
            entries[path] = (mode, oid)
    return entries

def _stable_content(root: Path, entries: Mapping[str, tuple[str, str]], path: str) -> tuple[str, bytes] | None:
    entry = entries.get(path)
    if entry is None:
        return None
    mode, oid = entry
    if mode not in {"100644", "100755"}:
        raise StableBaselineError(f"scoped stable path is not a regular tracked file: {path}")
    data = _git_bytes(root, "cat-file", "blob", oid)
    if len(data) > _MAX_FILE:
        raise StableBaselineError(f"scoped stable file exceeds the configured bound: {path}")
    try:
        data.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise StableBaselineError(f"scoped stable file is not UTF-8 text: {path}") from exc
    return mode, data


def _content(repo: GitExperiment, entries: Mapping[str, tuple[str, str]], path: str) -> tuple[str, bytes] | None:
    entry = entries.get(path)
    if entry is None:
        return None
    mode, oid = entry
    if mode not in {"100644", "100755"} or not oid:
        raise StableBaselineError(f"scoped stable path is not a regular tracked file: {path}")
    try:
        value = repo._blob(oid).decode("utf-8", "strict")
    except (UnicodeDecodeError, GitExperimentError) as exc:
        raise StableBaselineError(f"scoped stable path is not bounded UTF-8 text: {path}") from exc
    return mode, value.encode("utf-8")


def _merge_text(repo: GitExperiment, path: str, base: bytes, ours: bytes, theirs: bytes,
                resolve: Callable[[str, bytes], bytes | None] | None = None) -> bytes:
    # Normalize Windows checkout line endings for the in-memory merge only.
    base, ours, theirs = (value.replace(b"\r\n", b"\n") for value in (base, ours, theirs))
    with tempfile.TemporaryDirectory(prefix="stable-baseline-merge-") as scratch:
        root = Path(scratch)
        current, ancestor, incoming = (root / name for name in ("ours", "base", "theirs"))
        current.write_bytes(ours)
        ancestor.write_bytes(base)
        incoming.write_bytes(theirs)
        try:
            result = subprocess.run(
                ["git", "--git-dir", str(repo.root / ".git"), "--work-tree", str(repo.root),
                 "--no-pager", "--no-replace-objects", "merge-file", "--diff3", "--marker-size=16", "-p",
                 "-L", "SYNC_TARGET", "-L", "SYNC_BASE", "-L", "SYNC_SOURCE", str(current), str(ancestor), str(incoming)],
                cwd=repo.root, env=repo._env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=repo.timeout, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StableBaselineError(f"three-way merge could not be completed: {path}") from exc
        if not 0 <= result.returncode <= 127:
            raise StableBaselineError(f"three-way merge failed for scoped file: {path}")
        merged = result.stdout
        if result.returncode:
            if resolve is None:
                raise StableBaselineError(f"scoped stable drift conflicts with experiment edits: {path}")
            try:
                merged = resolve(path, merged)
            except Exception as exc:
                raise StableBaselineError(f"scoped conflict resolver failed for editable file: {path}") from exc
            if merged is None:
                raise StableBaselineError(f"scoped stable drift conflicts with experiment edits: {path}")
            if type(merged) is not bytes:
                raise StableBaselineError("scoped conflict resolver must return bytes or None")
        if len(merged) > _MAX_FILE:
            raise StableBaselineError(f"merged file exceeds the configured bound: {path}")
        try:
            merged.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise StableBaselineError(f"merged file is not UTF-8 text: {path}") from exc
        return merged


def _make_tree(repo: GitExperiment, current: str, changes: Mapping[str, tuple[str, bytes] | None]) -> str:
    with tempfile.TemporaryDirectory(prefix="stable-baseline-index-", dir=repo.root / ".git") as scratch:
        env = repo._env(index_file=os.path.join(scratch, "index"))
        repo._run("read-tree", current, env=env)
        total = 0
        for path, value in changes.items():
            if value is None:
                repo._run("update-index", "--force-remove", "--", path, env=env)
                continue
            mode, data = value
            total += len(data)
            if len(data) > _MAX_FILE or total > _MAX_TOTAL:
                raise StableBaselineError("scoped refresh exceeds the configured content bound")
            oid = repo._run("hash-object", "-w", "--stdin", input_data=data).strip()
            repo._run("update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}", env=env)
        tree = repo._run("write-tree", env=env).strip()
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", tree):
        raise StableBaselineError("Git returned an invalid refreshed tree")
    return tree


class StableBaseline:
    """Follow only committed, policy-scoped stable drift before model editing."""

    def __init__(self, stable_root: Path, policy: Any,
                 resolve: Callable[[str, bytes], bytes | None] | None = None) -> None:
        self.stable_root = Path(stable_root)
        self.policy = policy
        self.scope = _scoped_paths(policy)
        self.editable = frozenset(policy.editable_files)
        self.fixed_tests = frozenset(policy.tests)
        if resolve is not None and not callable(resolve):
            raise StableBaselineError("resolve must be callable when supplied")
        self.resolve = resolve

    def _stable_head(self) -> str:
        root = self.stable_root.resolve(strict=True)
        if not root.is_dir() or Path(_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True) != root:
            raise StableBaselineError("stable_root must be the canonical root of a Git working tree")
        head = _git(root, "rev-parse", "--verify", "HEAD^{commit}")
        if not _commit(head):
            raise StableBaselineError("stable HEAD is not a full commit ID")
        return head

    def _anchor(self, repo: GitExperiment, stable_root: Path, stable_head: str) -> tuple[str, dict[str, tuple[str, str]]]:
        if (repo._run("symbolic-ref", "--quiet", ANCHOR_REF, check=False).strip()
                or repo._run("symbolic-ref", "--quiet", SCOPE_TREE_REF, check=False).strip()):
            raise StableBaselineError("stable baseline and scope tree must be direct refs")
        marker = repo._ref(ANCHOR_REF)
        if marker:
            try:
                fields = repo._blob(marker).decode("ascii", "strict").strip().split()
            except (GitExperimentError, UnicodeDecodeError) as exc:
                raise StableBaselineError("stable baseline marker is invalid") from exc
            if len(fields) != 3 or fields[0] != "stable-baseline-v1" or not _commit(fields[1]) or not _commit(fields[2]):
                raise StableBaselineError("stable baseline marker does not contain a valid commit and scope tree")
            applied_commit, scoped_tree = fields[1], fields[2]
            pinned_tree = repo._ref(SCOPE_TREE_REF)
            if repo._run("symbolic-ref", "--quiet", SCOPE_TREE_REF, check=False).strip():
                raise StableBaselineError("stable scope tree must be pinned by a direct ref")
            if pinned_tree != scoped_tree:
                raise StableBaselineError("stable scope snapshot is missing or does not match its pinned ref")
            try:
                repo._run("cat-file", "-e", f"{pinned_tree}^{{tree}}")
            except GitExperimentError as exc:
                raise StableBaselineError("stable scope snapshot tree is unavailable") from exc
            if not _is_ancestor(stable_root, applied_commit, stable_head):
                raise StableBaselineError("stable history no longer contains the applied baseline anchor")
            entries = repo._tree_entries_from_tree(scoped_tree)
            if not set(entries) <= set(self.scope):
                raise StableBaselineError("stable baseline snapshot exceeds the configured verification scope")
            return applied_commit, entries
        base = repo._resolve_commit(repo._ref(BASE_REF))
        merge_base = _git(stable_root, "merge-base", stable_head, base)
        if not merge_base:
            raise StableBaselineError("no shared stable/experiment ancestry is available locally")
        try:
            merge_base = repo._resolve_commit(merge_base)
        except GitExperimentError as exc:
            raise StableBaselineError("shared stable anchor commit is absent from the experiment clone") from exc
        return merge_base, _tree_entries(repo, merge_base)

    def _can_resolve_source_conflict(self, path: str) -> bool:
        parts = path.casefold().split("/")
        filename = parts[-1]
        is_test_path = (any(part in {"test", "tests", "__tests__"} for part in parts[:-1])
                        or filename.startswith("test_") or filename.endswith("_test.py"))
        return path in self.editable and path not in self.fixed_tests and not is_test_path

    def _snapshot_tree(self, repo: GitExperiment, stable_root: Path,
                       entries: Mapping[str, tuple[str, str]]) -> str:
        with tempfile.TemporaryDirectory(prefix="stable-baseline-snapshot-", dir=repo.root / ".git") as scratch:
            env = repo._env(index_file=os.path.join(scratch, "index"))
            repo._run("read-tree", "--empty", env=env)
            total = 0
            for path in self.scope:
                value = _stable_content(stable_root, entries, path)
                if value is None:
                    continue
                mode, data = value
                total += len(data)
                if total > _MAX_TOTAL:
                    raise StableBaselineError("stable scope snapshot exceeds the configured content bound")
                oid = repo._run("hash-object", "-w", "--stdin", input_data=data).strip()
                repo._run("update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}", env=env)
            tree = repo._run("write-tree", env=env).strip()
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", tree):
            raise StableBaselineError("Git returned an invalid stable scope snapshot")
        return tree

    def refresh(self, repo: GitExperiment, verifier: Any, *, cancelled: Callable[[], bool]) -> str:
        if not callable(cancelled) or not callable(getattr(verifier, "run", None)):
            raise StableBaselineError("refresh requires a verifier and cancellation callback")
        for attempt in range(2):
            try:
                return self._refresh_once(repo, verifier, cancelled=cancelled)
            except _StableBaselineStale:
                if attempt or cancelled():
                    raise
        raise AssertionError("unreachable")

    def _refresh_once(self, repo: GitExperiment, verifier: Any, *, cancelled: Callable[[], bool]) -> str:
        if cancelled():
            raise StableBaselineError("stable refresh cancelled")
        stable_root = self.stable_root.resolve(strict=True)
        initial_stable = self._stable_head()
        anchor, entries_anchor = self._anchor(repo, stable_root, initial_stable)
        experiment_head = repo.head()
        auto_ref = repo._ref(AUTO_REF)
        old_anchor = repo._ref(ANCHOR_REF)
        base_ref = repo._ref(BASE_REF)
        entries_stable = _stable_tree_entries(stable_root, initial_stable)
        changed_raw = _git_bytes(stable_root, "diff", "--name-only", "-z", anchor, initial_stable, "--")
        try:
            changed_names = {name.decode("utf-8", "strict") for name in changed_raw.split(b"\0") if name}
        except UnicodeDecodeError as exc:
            raise StableBaselineError("stable change contains a non-UTF-8 path") from exc
        affected = sorted(changed_names & set(self.scope))
        if not affected:
            if self._stable_head() != initial_stable or repo.head() != experiment_head:
                raise _StableBaselineStale("stable or experiment ref changed before editing; refresh is stale")
            return experiment_head
        if cancelled():
            raise StableBaselineError("stable refresh cancelled")

        entries_experiment = _tree_entries(repo, experiment_head)
        updates: dict[str, tuple[str, bytes] | None] = {}
        for path in affected:
            base_value = _content(repo, entries_anchor, path)
            local_value = _content(repo, entries_experiment, path)
            stable_value = _stable_content(stable_root, entries_stable, path)
            if stable_value == base_value:
                continue
            if local_value == base_value:
                updates[path] = stable_value
            elif local_value == stable_value:
                continue
            elif base_value is None:
                raise StableBaselineError(f"concurrent additions conflict in scoped file: {path}")
            elif local_value is None or stable_value is None:
                raise StableBaselineError(f"delete/modify conflict in scoped file: {path}")
            else:
                mode = local_value[0] if local_value[0] == stable_value[0] else None
                if mode is None:
                    raise StableBaselineError(f"file mode conflict in scoped file: {path}")
                resolver = self.resolve if self._can_resolve_source_conflict(path) else None
                merged = _merge_text(repo, path, base_value[1], local_value[1], stable_value[1], resolver)
                updates[path] = (mode, merged)

        if cancelled():
            raise StableBaselineError("stable refresh cancelled")
        current_tree = repo._run("rev-parse", f"{experiment_head}^{{tree}}").strip()
        tree = _make_tree(repo, experiment_head, updates) if updates else current_tree
        verification = verifier.run(repo.archive(tree, tuple(self.policy.verification_files)), cancelled=cancelled)
        if cancelled():
            raise StableBaselineError("stable refresh cancelled")
        if not bool(getattr(verification, "passed", False)):
            raise StableBaselineError("fixed Docker verification failed for the scoped stable refresh")

        # Both dimensions are rechecked after potentially slow Docker execution.
        if self._stable_head() != initial_stable or repo.head() != experiment_head or repo._ref(BASE_REF) != base_ref:
            raise _StableBaselineStale("stable or experiment ref changed during scoped verification; refresh is stale")
        if cancelled():
            raise StableBaselineError("stable refresh cancelled")

        snapshot_tree = self._snapshot_tree(repo, stable_root, entries_stable)
        if (self._stable_head() != initial_stable or repo.head() != experiment_head
                or repo._ref(AUTO_REF) != auto_ref or repo._ref(BASE_REF) != base_ref
                or repo._ref(ANCHOR_REF) != old_anchor):
            raise _StableBaselineStale("stable or experiment refs changed before refresh publication")
        changes_tree = tree != current_tree
        if changes_tree:
            if repo._ref(AUTO_REF) and repo._is_checked_out(AUTO_REF):
                raise StableBaselineError("experiment-auto is checked out; refusing refresh publication")
            import time
            timestamp = str(int(time.time())) + " +0000"
            commit = repo._run(
                "commit-tree", tree, "-p", experiment_head, "-m", "Refresh scoped stable baseline",
                env={**repo._env(), "GIT_AUTHOR_DATE": timestamp, "GIT_COMMITTER_DATE": timestamp},
            ).strip()
            repo._resolve_commit(commit)
        else:
            commit = experiment_head

        marker_oid = repo._run("hash-object", "-w", "--stdin",
                               input_data=f"stable-baseline-v1 {initial_stable} {snapshot_tree}\n".encode("ascii")).strip()
        lines = ["start"]
        if changes_tree:
            if auto_ref:
                lines.append(f"update {AUTO_REF} {commit} {auto_ref}")
            else:
                lines.extend((f"verify {BASE_REF} {base_ref}", f"create {AUTO_REF} {commit}"))
        else:
            lines.append(f"verify {AUTO_REF if auto_ref else BASE_REF} {experiment_head if auto_ref else base_ref}")
        if old_anchor:
            lines.append(f"update {ANCHOR_REF} {marker_oid} {old_anchor}")
        else:
            lines.append(f"create {ANCHOR_REF} {marker_oid}")
        old_scope_tree = repo._ref(SCOPE_TREE_REF)
        if old_scope_tree:
            lines.append(f"update {SCOPE_TREE_REF} {snapshot_tree} {old_scope_tree}")
        else:
            lines.append(f"create {SCOPE_TREE_REF} {snapshot_tree}")
        if (self._stable_head() != initial_stable or repo.head() != experiment_head
                or repo._ref(AUTO_REF) != auto_ref or repo._ref(BASE_REF) != base_ref
                or repo._ref(ANCHOR_REF) != old_anchor):
            raise _StableBaselineStale("stable or experiment refs changed before atomic publication")
        lines.extend(("prepare", "commit", ""))
        repo._run("update-ref", "--stdin", input_data="\n".join(lines).encode("ascii"))
        if changes_tree and repo._ref(AUTO_REF) != commit:
            raise StableBaselineError("atomic experiment refresh transaction did not install its commit")
        if repo._ref(ANCHOR_REF) != marker_oid or repo._ref(SCOPE_TREE_REF) != snapshot_tree:
            raise StableBaselineError("atomic stable anchor transaction did not install the new anchor and pinned scope tree")
        return commit
