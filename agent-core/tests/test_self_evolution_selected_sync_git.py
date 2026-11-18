from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from self_evolution_selected_sync_git import capture, publish, SyncError


class SelectedSyncGitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.stable = Path(self.tmp.name) / "repo"
        self.stable.mkdir()
        self._git(self.stable, "init", "-q", "-b", "main")
        (self.stable / "agent-core").mkdir()
        (self.stable / "agent-core" / "module.py").write_bytes(b"one\ntwo\nthree\n")
        (self.stable / "agent-core" / "unselected.py").write_bytes(b"stable\n")
        self._git(self.stable, "add", ".")
        self._commit(self.stable, "base")
        self.base_head = self._git(self.stable, "rev-parse", "HEAD").strip()
        self._git(self.stable, "worktree", "add", "-qb", "experiment/sandbox", str(self.stable / ".worktrees" / "sandbox"), self.base_head)
        self.sandbox = self.stable / ".worktrees" / "sandbox"
        (self.stable / "agent-core" / "module.py").write_bytes(b"ONE\ntwo\nthree\n")
        self._git(self.stable, "add", "agent-core/module.py")
        self._commit(self.stable, "stable edit")
        self.source_head = self._git(self.stable, "rev-parse", "HEAD").strip()
        (self.sandbox / "agent-core" / "module.py").write_bytes(b"one\ntwo\nTHREE\n")
        (self.sandbox / "agent-core" / "unselected.py").write_bytes(b"sandbox dirty\n")

    def tearDown(self) -> None:
        self._git(self.stable, "worktree", "remove", "--force", str(self.sandbox))
        self.tmp.cleanup()

    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=cwd, check=True, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

    def _commit(self, cwd: Path, message: str) -> None:
        self._git(cwd, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", message)

    def test_three_way_publish_preserves_unselected_and_no_refs_change(self) -> None:
        snap = capture(self.stable, "main", self.source_head, ["agent-core/module.py"], ["agent-core/unselected.py"])
        self.assertEqual(snap.target_head, self.base_head)
        self.assertEqual(snap.source_head, self.source_head)
        self.assertEqual(snap.files["agent-core/module.py"].target, b"one\ntwo\nTHREE\n")
        self.assertEqual(snap.verification, {"agent-core/unselected.py": b"sandbox dirty\n"})
        self.assertFalse(snap.files["agent-core/module.py"].has_conflicts)
        self.assertEqual(snap.files["agent-core/module.py"].merged, b"ONE\ntwo\nTHREE\n")
        refs_before = self._git(self.stable, "show-ref").splitlines()
        result = publish(snap, {"agent-core/module.py": snap.files["agent-core/module.py"].merged}, lambda: False)
        self.assertTrue(result.published)
        self.assertEqual((self.sandbox / "agent-core" / "module.py").read_bytes(), b"ONE\ntwo\nTHREE\n")
        self.assertEqual((self.sandbox / "agent-core" / "unselected.py").read_bytes(), b"sandbox dirty\n")
        self.assertEqual(self._git(self.stable, "show-ref").splitlines(), refs_before)

    def test_diff3_conflict_draft_has_controlled_labels_and_crlf(self) -> None:
        # Stable source and sandbox both edit the same line; preserve sandbox CRLF.
        (self.stable / "agent-core" / "module.py").write_bytes(b"one\ntwo\nstable\n")
        self._git(self.stable, "add", "agent-core/module.py")
        self._commit(self.stable, "stable conflicting edit")
        source = self._git(self.stable, "rev-parse", "HEAD").strip()
        (self.sandbox / "agent-core" / "module.py").write_bytes(b"one\r\ntwo\r\nsandbox\r\n")
        snap = capture(self.stable, "main", source, ["agent-core/module.py"])
        draft = snap.files["agent-core/module.py"].merged
        self.assertTrue(snap.files["agent-core/module.py"].has_conflicts)
        self.assertIsInstance(draft, bytes)
        self.assertIn(b"<<<<<<<<<<<<<<<< SYNC_TARGET", draft)
        self.assertIn(b"|||||||||||||||| SYNC_BASE", draft)
        self.assertIn(b"================", draft)
        self.assertIn(b">>>>>>>>>>>>>>>> SYNC_SOURCE", draft)
        self.assertIn(b"\r\n", draft)
        self.assertNotIn(b"\n", draft.replace(b"\r\n", b""))

    def test_publish_serializes_on_repository_lock_and_keeps_stale_lock(self) -> None:
        snap = capture(self.stable, "main", self.source_head, ["agent-core/module.py"])
        common = Path(self._git(self.stable, "rev-parse", "--git-common-dir").strip())
        if not common.is_absolute():
            common = self.stable / common
        lock = common / "selected-sync-publish.lock"
        lock.write_bytes(b"another process")
        try:
            with self.assertRaises(SyncError):
                publish(snap, {"agent-core/module.py": snap.files["agent-core/module.py"].merged}, lambda: False)
            self.assertEqual(lock.read_bytes(), b"another process")
            self.assertEqual((self.sandbox / "agent-core" / "module.py").read_bytes(), b"one\ntwo\nTHREE\n")
        finally:
            lock.unlink()

    def test_publish_refuses_stale_target_bytes(self) -> None:
        snap = capture(self.stable, "main", self.source_head, ["agent-core/module.py"])
        path = self.sandbox / "agent-core" / "module.py"
        path.write_bytes(b"raced\n")
        with self.assertRaises(SyncError):
            publish(snap, {"agent-core/module.py": b"merged\n"}, lambda: False)
        self.assertEqual(path.read_bytes(), b"raced\n")

    def test_uses_literal_parent_and_does_not_import_older_unrelated_hunk(self) -> None:
        path = self.stable / "agent-core" / "module.py"
        path.write_bytes(b"stable-old\ntwo\nthree\n")
        self._git(self.stable, "add", "agent-core/module.py")
        self._commit(self.stable, "older independent change")
        path.write_bytes(b"stable-old\ntwo\nSTABLE-SELECTED\n")
        self._git(self.stable, "add", "agent-core/module.py")
        self._commit(self.stable, "selected change")
        commit = self._git(self.stable, "rev-parse", "HEAD").strip()
        # Sandbox remains at the original base and contributes its own last-line edit.
        (self.sandbox / "agent-core" / "module.py").write_bytes(b"one\ntwo\nthree\nSANDBOX\n")
        snap = capture(self.stable, "refs/heads/main", commit, ["agent-core/module.py"])
        self.assertEqual(snap.files["agent-core/module.py"].base, b"stable-old\ntwo\nthree\n")
        self.assertNotIn(b"stable-old", snap.files["agent-core/module.py"].merged)

    def test_publish_accepts_subset_and_noop_does_not_rewrite(self) -> None:
        snap = capture(self.stable, "refs/heads/main", self.source_head, ["agent-core/module.py"])
        untouched = self.sandbox / "agent-core" / "unselected.py"
        before = untouched.stat().st_mtime_ns
        result = publish(snap, {}, lambda: False)
        self.assertFalse(result.published)
        self.assertEqual(untouched.stat().st_mtime_ns, before)
        result = publish(snap, {"agent-core/module.py": snap.files["agent-core/module.py"].merged}, lambda: False)
        self.assertTrue(result.published)

    def test_publish_preserves_index_and_only_updates_selected_subset(self) -> None:
        path = "agent-core/module.py"
        self._git(self.sandbox, "add", path)
        staged_before = self._git(self.sandbox, "show", f":{path}")
        snap = capture(self.stable, "refs/heads/main", self.source_head, [path])
        result = publish(snap, {path: snap.files[path].merged}, lambda: False)
        self.assertTrue(result.published)
        self.assertEqual(self._git(self.sandbox, "show", f":{path}"), staged_before)
        self.assertEqual(Path(snap.index_path).read_bytes(), snap.index_bytes)

    def test_conflict_resolution_can_replace_a_block_but_not_context(self) -> None:
        path = "agent-core/module.py"
        stable_path = self.stable / path
        stable_path.write_bytes(b"one\ntwo\nstable\n")
        self._git(self.stable, "add", path)
        self._commit(self.stable, "conflicting stable edit")
        commit = self._git(self.stable, "rev-parse", "HEAD").strip()
        (self.sandbox / path).write_bytes(b"one\ntwo\ntarget\n")
        snap = capture(self.stable, "main", commit, [path])
        draft = snap.files[path].merged
        start = draft.index(b"<<<<<<<<<<<<<<<< SYNC_TARGET")
        end = draft.index(b">>>>>>>>>>>>>>>> SYNC_SOURCE")
        end = draft.index(b"\n", end) + 1
        resolved = draft[:start] + b"resolved\n" + draft[end:]
        self.assertTrue(publish(snap, {path: resolved}, lambda: False).published)
        snap = capture(self.stable, "main", commit, [path])
        with self.assertRaises(SyncError):
            publish(snap, {path: b"unrelated rewrite\n"}, lambda: False)

    def test_publish_refuses_changed_selected_paths_not_in_updates(self) -> None:
        snap = capture(self.stable, "main", self.source_head, ["agent-core/module.py"])
        path = self.sandbox / "agent-core" / "module.py"
        path.write_bytes(b"concurrent user edit\n")
        with self.assertRaises(SyncError):
            publish(snap, {}, lambda: False)
        self.assertEqual(path.read_bytes(), b"concurrent user edit\n")

    def test_accepts_more_than_one_merge_file_conflict(self) -> None:
        path = self.stable / "agent-core" / "module.py"
        path.write_bytes(b"stable-1\ntwo\nstable-3\n")
        self._git(self.stable, "add", "agent-core/module.py")
        self._commit(self.stable, "two conflicts")
        commit = self._git(self.stable, "rev-parse", "HEAD").strip()
        (self.sandbox / "agent-core" / "module.py").write_bytes(b"target-1\ntwo\ntarget-3\n")
        snap = capture(self.stable, "main", commit, ["agent-core/module.py"])
        self.assertTrue(snap.files["agent-core/module.py"].has_conflicts)
        self.assertEqual(snap.files["agent-core/module.py"].merged.count(b"<<<<<<<<<<<<<<<< SYNC_TARGET"), 2)

    def test_rejects_root_and_merge_source_commits(self) -> None:
        with self.assertRaises(SyncError):
            capture(self.stable, "main", self.base_head, ["agent-core/module.py"])
        self._git(self.stable, "branch", "merge-side", self.base_head)
        side = self.stable.parent / "merge-side-worktree"
        self._git(self.stable, "worktree", "add", "-q", str(side), "merge-side")
        try:
            (side / "agent-core" / "unselected.py").write_bytes(b"side\n")
            self._git(side, "add", "agent-core/unselected.py")
            self._commit(side, "side commit")
            self._git(self.stable, "merge", "--no-ff", "merge-side", "-m", "merge commit")
            merge = self._git(self.stable, "rev-parse", "HEAD").strip()
            with self.assertRaises(SyncError):
                capture(self.stable, "main", merge, ["agent-core/module.py"])
        finally:
            self._git(self.stable, "worktree", "remove", "--force", str(side))

    def test_rejects_non_first_parent_commit_and_unsafe_paths(self) -> None:
        for path in ("../escape", ".git/config", "Agent-core/module.py"):
            with self.subTest(path=path), self.assertRaises(SyncError):
                capture(self.stable, "main", self.source_head, [path])


if __name__ == "__main__":
    unittest.main()
