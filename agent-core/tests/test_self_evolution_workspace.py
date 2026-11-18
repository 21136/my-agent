from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path
from unittest import mock

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_evolution_materializer import MaterializationError, SafeSnapshotMaterializer  # noqa: E402


class _TestSnapshotMaterializer(SafeSnapshotMaterializer):
    def _cleanup_is_race_safe(self) -> bool:
        # Test-only gate substitute; does not establish production safety.
        return True


from self_evolution_workspace import (  # noqa: E402
    SelfEvolutionWorkspace,
    WorkspaceError,
)


class SelfEvolutionWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        # Historical linked-worktree regression cases remain executable against
        # disposable repos only. This test-only override never changes the host
        # default and is not evidence that production checkout is safe to enable.
        if self._testMethodName not in {
            "test_candidate_creation_fails_closed_before_running_git",
            "test_candidate_inspection_fails_closed_while_git_isolation_is_unavailable",
            "test_candidate_binding_is_not_claimed_for_unmaterialized_snapshot",
        }:
            gate = mock.patch("self_evolution_workspace._SAFE_CANDIDATE_CREATION_AVAILABLE", True)
            gate.start()
            self.addCleanup(gate.stop)
        self.tmp = tempfile.TemporaryDirectory(prefix="evolution-workspace-test-")
        self.base = Path(self.tmp.name)
        self.stable = self.base / "stable-repository"
        self.trust_root = self.base / "candidate-root"
        self.stable.mkdir()
        self.trust_root.mkdir()
        self.enrollment_root = self.base / "private-backend-storage"
        self.enrollment_root.mkdir()
        self.enrollment_path = self.enrollment_root / "trust-root-enrollment.json"
        self.git("init", "--quiet", str(self.stable))
        self.git("-C", str(self.stable), "config", "user.name", "Test User")
        self.git("-C", str(self.stable), "config", "user.email", "test@example.invalid")
        self.git("-C", str(self.stable), "config", "core.autocrlf", "false")
        (self.stable / "tracked.txt").write_text("committed source\n", encoding="utf-8")
        (self.stable / ".gitignore").write_text("ignored-dependency/\n", encoding="utf-8")
        self.git("-C", str(self.stable), "add", "tracked.txt", ".gitignore")
        self.git("-C", str(self.stable), "commit", "--quiet", "-m", "initial")
        self.workspace = SelfEvolutionWorkspace(self.stable, self.trust_root, b"test-only-key-32-bytes-minimum!!", enrollment_path=self.enrollment_path, allow_initial_enrollment=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if result.returncode:
            raise AssertionError(f"git fixture setup failed ({result.returncode})")
        return result.stdout.strip()

    def commit_change(self, name: str, content: str) -> None:
        (self.stable / name).write_text(content, encoding="utf-8")
        self.git("-C", str(self.stable), "add", name)
        self.git("-C", str(self.stable), "commit", "--quiet", "-m", name)

    def test_candidate_creation_fails_closed_before_running_git(self) -> None:
        sentinel = self.base / "filter-executed.txt"
        (self.stable / ".gitattributes").write_text("tracked.txt filter=hostile\n", encoding="utf-8")
        self.git("-C", str(self.stable), "config", "filter.hostile.smudge", f"cmd /c echo pwned > {sentinel}")
        self.git("-C", str(self.stable), "config", "core.hooksPath", str(self.base / "hostile-hooks"))
        with mock.patch.object(self.workspace, "_run_git", side_effect=AssertionError("Git must not run")):
            result = self.workspace.create()
        self.assertEqual(result["status"], "creation_failed")
        self.assertEqual(result["failure"]["code"], "safe_checkout_unavailable")
        self.assertFalse(result["scene_preserved"])
        self.assertFalse(sentinel.exists())

    def test_candidate_binding_is_not_claimed_for_unmaterialized_snapshot(self) -> None:
        with mock.patch.object(self.workspace, "_run_git", side_effect=AssertionError("Git must not run")):
            with self.assertRaises(WorkspaceError) as caught:
                self.workspace.create_binding()
        self.assertEqual(caught.exception.code, "safe_checkout_unavailable")
        self.assertEqual(list(self.trust_root.iterdir()), [])

    def test_candidate_inspection_fails_closed_while_git_isolation_is_unavailable(self) -> None:
        with self.assertRaises(WorkspaceError) as caught:
            self.workspace.inspect("12345678-1234-4234-8234-123456789abc")
        self.assertEqual(caught.exception.code, "safe_checkout_unavailable")

    def test_create_contains_only_committed_tracked_files_and_inspect_binds_identity(self) -> None:
        (self.stable / "untracked-user-data.txt").write_text("do not copy", encoding="utf-8")
        (self.stable / "ignored-dependency").mkdir()
        before_head = self.git("-C", str(self.stable), "rev-parse", "HEAD")

        created = self.workspace.create()
        candidate = Path(created["candidate_path"])

        self.assertEqual(created["stable_head"], before_head)
        self.assertEqual(created["object_store"], "shared_common_dir")
        self.assertNotEqual(created["candidate_repository"]["git_dir"], created["stable_repository"]["git_dir"])
        self.assertEqual(created["task_id"], candidate.name)
        self.assertEqual((candidate / "tracked.txt").read_text(encoding="utf-8"), "committed source\n")
        self.assertFalse((candidate / "untracked-user-data.txt").exists())
        self.assertFalse((candidate / "ignored-dependency").exists())
        self.assertTrue((candidate / ".git").is_file())
        self.assertEqual(created["candidate_repository"]["git_common_dir"], created["stable_repository"]["git_common_dir"])
        self.assertEqual(self.workspace.inspect(created["task_id"]), created)
        verification = self.workspace.verify(created["task_id"])
        self.assertEqual(verification["status"], "baseline_unchanged")
        self.assertTrue(verification["baseline_matches"])
        self.assertFalse(verification["merge_authorized"])

    @unittest.skipUnless(os.name == "nt", "production snapshot publication currently requires Windows handle-relative cleanup")
    def test_snapshot_candidate_is_created_through_workspace_with_persisted_binding(self) -> None:
        materializer = SafeSnapshotMaterializer(shutil.which("git"))
        created = self.workspace.create_snapshot_candidate(materializer)
        candidate = Path(created["candidate_path"])
        self.assertEqual(created["status"], "created")
        self.assertEqual(created["candidate_kind"], "materialized_snapshot")
        self.assertEqual(created["binding"]["candidate_id"], created["task_id"])
        self.assertEqual(created["binding"]["commit_oid"], created["stable_head"])
        self.assertFalse(created["os_sandbox"])
        self.assertFalse(created["merge_authorized"])
        self.assertFalse((candidate / ".git").exists())
        self.assertEqual(materializer.read_binding(candidate, expected_candidate_id=created["task_id"], integrity_key=self.workspace._integrity_key), created["binding"])
        self.assertEqual((candidate / "tracked.txt").read_text(encoding="utf-8"), "committed source\n")

    def test_snapshot_candidate_does_not_run_status_or_checkout_against_shared_git_config(self) -> None:
        self.enterContext(mock.patch.object(SafeSnapshotMaterializer, "_cleanup_is_race_safe", return_value=True))
        sentinel = self.base / "workspace-filter-executed.txt"
        filter_script = self.base / "workspace-smudge.cmd"
        filter_script.write_text(f"@echo pwned>{sentinel}\r\n@type\r\n", encoding="utf-8")
        (self.stable / ".gitattributes").write_text("tracked.txt filter=hostile\n", encoding="utf-8")
        (self.stable / "tracked.txt").write_text("committed source\n", encoding="utf-8")
        self.git("-C", str(self.stable), "add", ".gitattributes", "tracked.txt")
        self.git("-C", str(self.stable), "commit", "--quiet", "-m", "filter attributes")
        self.git("-C", str(self.stable), "config", "filter.hostile.smudge", str(filter_script))
        self.git("-C", str(self.stable), "config", "filter.hostile.required", "true")
        materializer = SafeSnapshotMaterializer(shutil.which("git"))

        with mock.patch.object(self.workspace, "_worktree_snapshot", side_effect=AssertionError("shared worktree status is unsafe")):
            created = self.workspace.create_snapshot_candidate(materializer)

        candidate = Path(created["candidate_path"])
        self.assertEqual((candidate / "tracked.txt").read_text(encoding="utf-8"), "committed source\n")
        self.assertEqual((candidate / ".gitattributes").read_text(encoding="utf-8"), "tracked.txt filter=hostile\n")
        self.assertFalse(sentinel.exists(), "shared Git smudge filter executed during materialization")
        self.assertFalse((candidate / ".git").exists())
        self.assertEqual(created["binding"]["commit_oid"], created["stable_head"])

    def test_snapshot_candidate_rejects_a_persisted_binding_for_another_commit(self) -> None:
        self.enterContext(mock.patch.object(SafeSnapshotMaterializer, "_cleanup_is_race_safe", return_value=True))
        original_read_binding = SafeSnapshotMaterializer.read_binding

        def wrong_commit(materializer, destination, *, expected_candidate_id, integrity_key, expected_parent_identity=None):
            binding = original_read_binding(
                materializer, destination,
                expected_candidate_id=expected_candidate_id, integrity_key=integrity_key,
                expected_parent_identity=expected_parent_identity,
            )
            binding["commit_oid"] = "0" * 40
            return binding

        with mock.patch.object(SafeSnapshotMaterializer, "read_binding", wrong_commit):
            with self.assertRaises(WorkspaceError) as caught:
                self.workspace.create_snapshot_candidate(SafeSnapshotMaterializer(shutil.which("git")))

        self.assertEqual(caught.exception.code, "snapshot_binding_mismatch")
        self.assertEqual(caught.exception.candidate_id, caught.exception.as_dict()["task_id"])
        self.assertEqual(caught.exception.publication_status, "published")
        candidates = [path for path in self.trust_root.iterdir() if path.is_dir()]
        self.assertEqual(len(candidates), 1)
        self.assertTrue((candidates[0] / ".self-evolution-binding.json").is_file())

    def test_final_stable_check_failure_returns_published_candidate_identity(self) -> None:
        original = self.workspace._repository_identity

        def fail_final(path, stage):
            if stage == "snapshot_final_identity":
                raise WorkspaceError("injected", code="git_failed", stage=stage, exit_code=17)
            return original(path, stage)

        with mock.patch.object(self.workspace, "_repository_identity", side_effect=fail_final):
            with self.assertRaises(WorkspaceError) as caught:
                self.workspace.create_snapshot_candidate(SafeSnapshotMaterializer(shutil.which("git")))

        self.assertEqual(caught.exception.code, "git_failed")
        self.assertEqual(caught.exception.publication_status, "published")
        self.assertIsNotNone(caught.exception.candidate_id)
        self.assertTrue((self.trust_root / caught.exception.candidate_id / "tracked.txt").is_file())

    def test_snapshot_cleanup_failure_reports_recoverable_identity_without_paths(self) -> None:
        self.enterContext(mock.patch.object(SafeSnapshotMaterializer, "_cleanup_is_race_safe", return_value=True))
        materializer = SafeSnapshotMaterializer(shutil.which("git"))
        with mock.patch("self_evolution_materializer._cleanup_temporary_tree", side_effect=MaterializationError("private path", code="cleanup_reparse")):
            with self.assertRaises(WorkspaceError) as caught:
                self.workspace.create_snapshot_candidate(materializer)
        failure = caught.exception.as_dict()
        self.assertEqual(failure["code"], "publication_unknown")
        self.assertEqual(failure["publication_status"], "unknown")
        candidate_id = failure["candidate_id"]
        self.assertEqual(failure["task_id"], candidate_id)
        self.assertNotIn(str(self.base), str(failure))
        candidate = self.trust_root / candidate_id
        self.assertEqual((candidate / "tracked.txt").read_text(encoding="utf-8"), "committed source\n")
        record = materializer.read_binding(candidate, expected_candidate_id=candidate_id,
                                           integrity_key=self.workspace._integrity_key)
        self.assertEqual(record["candidate_id"], candidate_id)
        self.assertTrue(list(self.trust_root.glob(".self-evolution-materializer-*")))

    def test_snapshot_scope_excludes_dirty_staged_untracked_and_ignored_data(self) -> None:
        self.enterContext(mock.patch.object(SafeSnapshotMaterializer, "_cleanup_is_race_safe", return_value=True))
        (self.stable / "tracked.txt").write_text("unstaged change\n", encoding="utf-8")
        (self.stable / "staged.txt").write_text("staged change\n", encoding="utf-8")
        self.git("-C", str(self.stable), "add", "staged.txt")
        (self.stable / "private.txt").write_text("private\n", encoding="utf-8")
        (self.stable / "ignored-dependency").mkdir()
        (self.stable / "ignored-dependency" / "private.txt").write_text("ignored\n", encoding="utf-8")
        before_status = self.git("-C", str(self.stable), "status", "--porcelain=v1", "--untracked-files=all")
        index = self.stable / self.git("-C", str(self.stable), "rev-parse", "--git-path", "index")
        before_index = index.read_bytes()
        result = self.workspace.create_snapshot_candidate(SafeSnapshotMaterializer(shutil.which("git")))
        candidate = Path(result["candidate_path"])
        self.assertEqual(result["snapshot_scope"], "committed_head_tree_only")
        self.assertEqual(result["binding"]["snapshot_scope"], "committed_tree_only")
        self.assertEqual((candidate / "tracked.txt").read_text(encoding="utf-8"), "committed source\n")
        for name in ("staged.txt", "private.txt", "ignored-dependency"):
            self.assertFalse((candidate / name).exists())
        self.assertEqual((self.stable / "tracked.txt").read_text(encoding="utf-8"), "unstaged change\n")
        self.assertEqual((self.stable / "staged.txt").read_text(encoding="utf-8"), "staged change\n")
        self.assertEqual((self.stable / "private.txt").read_text(encoding="utf-8"), "private\n")
        self.assertEqual((self.stable / "ignored-dependency" / "private.txt").read_text(encoding="utf-8"), "ignored\n")
        self.assertEqual(index.read_bytes(), before_index)
        self.assertEqual(self.git("-C", str(self.stable), "status", "--porcelain=v1", "--untracked-files=all"), before_status)

    def test_snapshot_candidate_rejects_subclass_that_opens_cleanup_gate(self) -> None:
        materializer = _TestSnapshotMaterializer(shutil.which("git"))
        with self.assertRaises(WorkspaceError) as caught:
            self.workspace.create_snapshot_candidate(materializer)
        self.assertEqual(caught.exception.code, "invalid_materializer")
        self.assertEqual(list(self.trust_root.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "production snapshot creation requires Windows handle-relative lifecycle")
    def test_snapshot_candidate_ignores_exact_instance_cleanup_gate_override(self) -> None:
        materializer = SafeSnapshotMaterializer(shutil.which("git"))
        materializer._cleanup_is_race_safe = lambda: False
        created = self.workspace.create_snapshot_candidate(materializer)
        self.assertEqual(created["status"], "created")
        self.assertEqual(created["binding"]["authorized_parent_identity"], list(self.workspace._trust_root_identity))

    def test_snapshot_candidate_path_obeys_production_materializer_gate(self) -> None:
        materializer = SafeSnapshotMaterializer(shutil.which("git"))
        before = sorted(path.name for path in self.trust_root.iterdir())
        if os.name == "nt":
            created = self.workspace.create_snapshot_candidate(materializer)
            self.assertEqual(created["status"], "created")
        else:
            with self.assertRaises(WorkspaceError) as caught:
                self.workspace.create_snapshot_candidate(materializer)
            self.assertEqual(caught.exception.code, "unsafe_cleanup_unavailable")
        if os.name != "nt":
            self.assertEqual(sorted(path.name for path in self.trust_root.iterdir()), before)

    def test_create_binding_exposes_host_verified_typed_candidate_contract(self) -> None:
        binding = self.workspace.create_binding()
        self.assertEqual(binding.candidate_id, self.workspace.inspect(binding.candidate_id)["task_id"])
        self.assertEqual(binding.stable_commit, self.git("-C", str(self.stable), "rev-parse", "HEAD"))
        self.assertEqual(binding.git_common_id, self.workspace.inspect(binding.candidate_id)["stable_repository"]["git_common_dir"])
        self.assertNotEqual(binding.stable_worktree_id, binding.candidate_worktree_id)
        self.assertEqual(len(binding.baseline_manifest_digest), 64)

    def test_dirty_stable_index_and_untracked_files_are_preserved_and_not_copied(self) -> None:
        (self.stable / "tracked.txt").write_text("unstaged edit\n", encoding="utf-8")
        (self.stable / "staged.txt").write_text("staged content\n", encoding="utf-8")
        self.git("-C", str(self.stable), "add", "staged.txt")
        (self.stable / "untracked-user-data.txt").write_text("private\n", encoding="utf-8")
        index = Path(self.git("-C", str(self.stable), "rev-parse", "--git-path", "index"))
        if not index.is_absolute():
            index = self.stable / index
        before_index = index.read_bytes()
        before_status = self.git("-C", str(self.stable), "status", "--porcelain=v1", "--untracked-files=all")
        before_head = self.git("-C", str(self.stable), "rev-parse", "HEAD")

        result = self.workspace.create()

        self.assertEqual(result["status"], "created")
        candidate = Path(result["candidate_path"])
        self.assertEqual(result["stable_head"], before_head)
        self.assertEqual((candidate / "tracked.txt").read_text(encoding="utf-8"), "committed source\n")
        self.assertFalse((candidate / "staged.txt").exists())
        self.assertFalse((candidate / "untracked-user-data.txt").exists())
        self.assertEqual(index.read_bytes(), before_index)
        self.assertEqual(self.git("-C", str(self.stable), "status", "--porcelain=v1", "--untracked-files=all"), before_status)

    def test_existing_dirty_sandbox_is_not_modified_by_attached_candidate(self) -> None:
        sandbox = self.base / "existing-sandbox"
        self.git("-C", str(self.stable), "worktree", "add", "--quiet", "--detach", str(sandbox), "HEAD")
        (sandbox / "tracked.txt").write_text("sandbox dirty\n", encoding="utf-8")
        (sandbox / "sandbox-only.txt").write_text("preserve\n", encoding="utf-8")
        before = self.git("-C", str(sandbox), "status", "--porcelain=v1", "--untracked-files=all")
        before_index = (sandbox / ".git").read_text(encoding="utf-8")

        result = self.workspace.create()

        self.assertEqual(result["status"], "created")
        self.assertEqual((sandbox / "tracked.txt").read_text(encoding="utf-8"), "sandbox dirty\n")
        self.assertEqual((sandbox / "sandbox-only.txt").read_text(encoding="utf-8"), "preserve\n")
        self.assertEqual(self.git("-C", str(sandbox), "status", "--porcelain=v1", "--untracked-files=all"), before)
        self.assertEqual((sandbox / ".git").read_text(encoding="utf-8"), before_index)
        self.assertNotEqual(Path(result["candidate_path"]), sandbox)
        self.assertEqual(result["stable_repository"]["git_common_dir"], result["candidate_repository"]["git_common_dir"])

    def test_repository_local_post_checkout_hook_never_executes(self) -> None:
        sentinel = self.base / "hook-ran.txt"
        hooks = self.base / "hostile-hooks"
        hooks.mkdir()
        hook = hooks / "post-checkout"
        hook.write_text(f"#!/bin/sh\nprintf pwned > '{sentinel.as_posix()}'\n", encoding="utf-8")
        hook.chmod(0o755)
        self.git("-C", str(self.stable), "config", "core.hooksPath", str(hooks))
        result = self.workspace.create()
        self.assertIn(result["status"], ("created", "creation_failed"))
        self.assertFalse(sentinel.exists(), "repository-local post-checkout hook executed")

    def test_repository_local_smudge_filter_never_executes(self) -> None:
        sentinel = self.base / "smudge-ran.txt"
        (self.stable / ".gitattributes").write_text("tracked.txt filter=evil\n", encoding="utf-8")
        self.git("-C", str(self.stable), "add", ".gitattributes")
        self.git("-C", str(self.stable), "commit", "--quiet", "-m", "attributes")
        filter_script = self.base / "smudge.sh"
        filter_script.write_text(f"#!/bin/sh\nprintf pwned > '{sentinel.as_posix()}'\ncat\n", encoding="utf-8")
        filter_script.chmod(0o755)
        self.git("-C", str(self.stable), "config", "filter.evil.smudge", f"sh '{filter_script.as_posix()}'")
        self.git("-C", str(self.stable), "config", "filter.evil.required", "true")
        result = self.workspace.create()
        self.assertEqual(result["status"], "creation_failed")
        self.assertEqual(result["failure"]["code"], "unsafe_git_configuration")
        self.assertFalse(sentinel.exists(), "repository-local smudge filter executed")

    def test_filter_and_committed_attributes_are_refused(self) -> None:
        sentinel = self.base / "late-smudge-ran.txt"
        (self.stable / ".gitattributes").write_text("tracked.txt filter=late\n", encoding="utf-8")
        self.git("-C", str(self.stable), "add", ".gitattributes")
        self.git("-C", str(self.stable), "commit", "--quiet", "-m", "attributes")
        self.git("-C", str(self.stable), "config", "filter.late.smudge", f"sh -c 'echo pwned > {sentinel.as_posix()}; cat'")
        result = self.workspace.create()
        self.assertEqual(result["status"], "creation_failed")
        self.assertFalse(sentinel.exists())

    def test_filter_added_after_initial_snapshot_is_refused_before_checkout(self) -> None:
        sentinel = self.base / "late-filter-ran.txt"
        original = self.workspace._worktree_snapshot
        def snapshot_then_change(path, stage):
            result = original(path, stage)
            if stage == "stable_snapshot":
                self.git("-C", str(self.stable), "config", "filter.evil.smudge", f"sh -c 'echo pwned > {sentinel.as_posix()}; cat'")
            return result
        with mock.patch.object(self.workspace, "_worktree_snapshot", side_effect=snapshot_then_change):
            result = self.workspace.create()
        self.assertEqual(result["status"], "creation_failed")
        self.assertEqual(result["failure"]["code"], "unsafe_git_configuration")
        self.assertFalse(Path(result["candidate_path"]).exists())
        self.assertFalse(sentinel.exists())

    def test_default_post_checkout_hook_never_executes(self) -> None:
        sentinel = self.base / "default-hook-ran.txt"
        hook = self.stable / ".git" / "hooks" / "post-checkout"
        hook.write_text(f"#!/bin/sh\nprintf pwned > '{sentinel.as_posix()}'\n", encoding="utf-8")
        hook.chmod(0o755)
        result = self.workspace.create()
        self.assertIn(result["status"], ("created", "creation_failed"))
        self.assertFalse(sentinel.exists())

    def test_repository_clean_filter_never_executes_during_status(self) -> None:
        sentinel = self.base / "clean-ran.txt"
        (self.stable / ".gitattributes").write_text("tracked.txt filter=evil\n", encoding="utf-8")
        self.git("-C", str(self.stable), "add", ".gitattributes")
        self.git("-C", str(self.stable), "commit", "--quiet", "-m", "attributes")
        script = self.base / "clean.sh"
        script.write_text(f"#!/bin/sh\nprintf pwned > '{sentinel.as_posix()}'\ncat\n", encoding="utf-8")
        script.chmod(0o755)
        self.git("-C", str(self.stable), "config", "filter.evil.clean", f"sh '{script.as_posix()}'")
        (self.stable / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        result = self.workspace.create()
        self.assertEqual(result["status"], "creation_failed")
        self.assertEqual((self.stable / "tracked.txt").read_text(encoding="utf-8"), "dirty\n")
        self.assertFalse(sentinel.exists())

    def test_configured_fsmonitor_hook_never_executes(self) -> None:
        sentinel = self.base / "fsmonitor-ran.txt"
        script = self.base / "monitor.sh"
        script.write_text(f"#!/bin/sh\nprintf pwned > '{sentinel.as_posix()}'\n", encoding="utf-8")
        script.chmod(0o755)
        self.git("-C", str(self.stable), "config", "core.fsmonitor", str(script))
        self.workspace.create()
        self.assertFalse(sentinel.exists())

    def test_info_attributes_are_refused_without_modification(self) -> None:
        attributes = self.stable / ".git" / "info" / "attributes"
        attributes.write_text("tracked.txt filter=evil\n", encoding="utf-8")
        result = self.workspace.create()
        self.assertEqual(result["status"], "creation_failed")
        self.assertEqual(result["failure"]["code"], "unsafe_git_configuration")
        self.assertEqual(attributes.read_text(encoding="utf-8"), "tracked.txt filter=evil\n")

    def test_committed_attributes_are_refused_without_filter_configuration(self) -> None:
        (self.stable / ".gitattributes").write_text("*.txt text\n", encoding="utf-8")
        self.git("-C", str(self.stable), "add", ".gitattributes")
        self.git("-C", str(self.stable), "commit", "--quiet", "-m", "attributes")
        result = self.workspace.create()
        self.assertEqual(result["status"], "creation_failed")
        self.assertEqual(result["failure"]["code"], "unsafe_git_configuration")

    def test_dirty_file_same_status_change_is_not_claimed_as_content_verified(self) -> None:
        tracked = self.stable / "tracked.txt"
        tracked.write_text("first dirty version\n", encoding="utf-8")
        before_status = self.git("-C", str(self.stable), "status", "--porcelain=v1")
        original = self.workspace._run_git
        def modify_during_add(*args, stage, want_output=False):
            if stage == "worktree_add":
                tracked.write_text("second dirty version\n", encoding="utf-8")
            return original(*args, stage=stage, want_output=want_output)
        with mock.patch.object(self.workspace, "_run_git", side_effect=modify_during_add):
            result = self.workspace.create()
        self.assertEqual(self.git("-C", str(self.stable), "status", "--porcelain=v1"), before_status)
        self.assertEqual(tracked.read_text(encoding="utf-8"), "second dirty version\n")
        self.assertEqual(result["status"], "created")
        self.assertFalse(result["stable_worktree_content_verified"])

    def test_non_git_stable_root_is_rejected(self) -> None:
        plain = self.base / "plain"
        plain.mkdir()
        result = SelfEvolutionWorkspace(plain, self.trust_root, b"test-only-key-32-bytes-minimum!!").create()
        self.assertEqual(result["failure"]["code"], "not_git_repository")
        self.assertFalse(result["scene_preserved"])

    def test_git_output_collection_uses_streamed_reader_not_capture_output(self) -> None:
        with mock.patch(
            "self_evolution_workspace.subprocess.run",
            side_effect=AssertionError("unbounded capture_output path was used"),
        ):
            head = self.workspace._git_output(self.stable, "rev-parse", "HEAD", stage="test_stream")
        self.assertEqual(head, self.git("-C", str(self.stable), "rev-parse", "HEAD"))

    def test_oversized_real_git_status_is_rejected_during_bounded_collection(self) -> None:
        created = self.workspace.create()
        candidate = Path(created["candidate_path"])
        for index in range(100):
            (candidate / f"untracked-{index:03}.txt").write_text("x", encoding="utf-8")
        with mock.patch("self_evolution_workspace._MAX_GIT_OUTPUT_BYTES", 512):
            result = self.workspace.verify(created["task_id"])
        self.assertEqual(result["status"], "verification_failed")
        self.assertEqual(result["failure"]["code"], "git_output_limit")
        self.assertFalse(result["merge_authorized"])

    def test_verify_reports_stable_head_drift_read_only(self) -> None:
        created = self.workspace.create()
        self.commit_change("next.txt", "new committed source\n")
        before = self.git("-C", str(self.stable), "rev-parse", "HEAD")

        result = self.workspace.verify(created["task_id"])

        self.assertEqual(result["status"], "stable_head_drift")
        self.assertFalse(result["baseline_matches"])
        self.assertFalse(result["merge_authorized"])
        self.assertEqual(result["recorded_stable_head"], created["stable_head"])
        self.assertEqual(result["current_stable_head"], before)
        self.assertEqual(self.git("-C", str(self.stable), "rev-parse", "HEAD"), before)
        self.assertTrue((Path(created["candidate_path"]) / "tracked.txt").exists())

    def test_verify_detects_candidate_worktree_changes_even_when_head_is_unchanged(self) -> None:
        created = self.workspace.create()
        (Path(created["candidate_path"]) / "tracked.txt").write_text("edited candidate\n", encoding="utf-8")
        result = self.workspace.verify(created["task_id"])
        self.assertEqual(result["status"], "baseline_unchanged")
        self.assertTrue(result["identity_matches"])
        self.assertTrue(result["baseline_matches"])
        self.assertFalse(result["merge_authorized"])
        self.assertTrue(any("tracked.txt" in change["path"] for change in result["candidate_worktree_changes"]["entries"]))

    def test_verify_reports_candidate_untracked_changes_without_blocking_baseline(self) -> None:
        created = self.workspace.create()
        (Path(created["candidate_path"]) / "worker-output.txt").write_text("new", encoding="utf-8")
        result = self.workspace.verify(created["task_id"])
        self.assertEqual(result["status"], "baseline_unchanged")
        self.assertTrue(any("worker-output.txt" in change["path"] for change in result["candidate_worktree_changes"]["entries"]))
        self.assertFalse(result["merge_authorized"])

    def test_verify_detects_repository_identity_mismatch(self) -> None:
        created = self.workspace.create()
        other = self.base / "other-stable"
        other.mkdir()
        self.git("init", "--quiet", str(other))
        alternate = SelfEvolutionWorkspace(other, self.trust_root, b"test-only-key-32-bytes-minimum!!")

        result = alternate.verify(created["task_id"])

        self.assertEqual(result["status"], "identity_mismatch")
        self.assertFalse(result["identity_matches"])
        self.assertFalse(result["merge_authorized"])

    def test_candidate_worktree_identity_replacement_is_rejected(self) -> None:
        created = self.workspace.create()
        candidate = Path(created["candidate_path"])
        other = self.base / "second-task-worktree"
        self.git("-C", str(self.stable), "worktree", "add", "--quiet", "--detach", str(other), created["stable_head"])
        other_git_dir = self.git("-C", str(other), "rev-parse", "--absolute-git-dir")
        git_pointer = candidate / ".git"
        git_pointer.unlink()
        git_pointer.write_text(f"gitdir: {other_git_dir}\n", encoding="utf-8")

        with self.assertRaises(WorkspaceError) as caught:
            self.workspace.inspect(created["task_id"])
        self.assertEqual(caught.exception.code, "identity_mismatch")
        result = self.workspace.verify(created["task_id"])

        self.assertEqual(result["status"], "identity_mismatch")
        self.assertFalse(result["identity_matches"])
        self.assertFalse(result["merge_authorized"])

    def test_task_id_is_validated_and_cannot_select_arbitrary_paths(self) -> None:
        for task_id in ("..", "../stable-repository", str(self.stable), "not-a-uuid"):
            with self.subTest(task_id=task_id):
                with self.assertRaises(WorkspaceError) as caught:
                    self.workspace.inspect(task_id)
                self.assertEqual(caught.exception.code, "invalid_task_id")

    def test_failed_worktree_identity_check_preserves_candidate_and_returns_bounded_diagnostic(self) -> None:
        original = self.workspace._run_git
        def fail_add(*args, stage, want_output=False):
            if stage == "verify_candidate":
                raise WorkspaceError("git failed", code="git_failed", stage=stage, exit_code=17)
            return original(*args, stage=stage, want_output=want_output)
        with mock.patch.object(self.workspace, "_run_git", side_effect=fail_add):
            result = self.workspace.create()
        self.assertEqual(result["status"], "creation_failed")
        self.assertEqual(result["failure"]["stage"], "verify_candidate")
        self.assertEqual(result["failure"]["exit_code"], 17)
        self.assertTrue(Path(result["candidate_path"]).exists())
        self.assertTrue(result["scene_preserved"])
        self.assertNotIn("password", str(result).lower())

    def test_manifest_tampering_is_detected(self) -> None:
        created = self.workspace.create()
        manifest = self.trust_root / f"{created['task_id']}.self-evolution-workspace.json"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace(created["stable_head"], "0" * 40), encoding="utf-8")
        with self.assertRaises(WorkspaceError) as caught:
            self.workspace.inspect(created["task_id"])
        self.assertEqual(caught.exception.code, "manifest_tampered")

    @unittest.skipUnless(os.name == "nt", "production snapshot creation currently requires Windows handle-relative lifecycle")
    def test_candidate_binding_rejects_candidate_moved_under_replacement_trust_root(self) -> None:
        created = self.workspace.create_snapshot_candidate(SafeSnapshotMaterializer(shutil.which("git")))
        task_id = created["task_id"]
        old_root = self.base / "original-authorized-root"
        self.trust_root.rename(old_root)
        self.trust_root.mkdir()
        relocated = self.trust_root / task_id
        (old_root / task_id).rename(relocated)

        replacement_workspace = SelfEvolutionWorkspace(
            self.stable, self.trust_root, self.workspace._integrity_key,
            enrollment_path=self.enrollment_path,
        )
        with self.assertRaises(MaterializationError) as caught:
            SafeSnapshotMaterializer(shutil.which("git")).read_binding(
                relocated, expected_candidate_id=task_id,
                integrity_key=self.workspace._integrity_key,
                expected_parent_identity=replacement_workspace._trust_root_identity,
            )

        self.assertEqual(caught.exception.code, "binding_identity_mismatch")
        self.assertTrue((relocated / "tracked.txt").is_file())

    @unittest.skipUnless(os.name == "nt", "persistent trust-root anchor uses Windows ACL and handle-relative proof")
    def test_restarted_workspace_rejects_replaced_root_using_persistent_hmac_anchor(self) -> None:
        created = self.workspace.create_snapshot_candidate(SafeSnapshotMaterializer(shutil.which("git")))
        anchor_files = list(self.base.glob(".self-evolution-root-anchor-*.json"))
        self.assertEqual(len(anchor_files), 1)
        anchor = anchor_files[0]
        anchor_before = anchor.read_bytes()
        old_root = self.base / "original-root-after-restart"
        replacement = self.base / "replacement-root-after-restart"
        self.trust_root.rename(old_root)
        replacement.mkdir()
        replacement.rename(self.trust_root)

        restarted = SelfEvolutionWorkspace(self.stable, self.trust_root, self.workspace._integrity_key, enrollment_path=self.enrollment_path)
        with self.assertRaises(WorkspaceError) as caught:
            restarted.create_snapshot_candidate(SafeSnapshotMaterializer(shutil.which("git")))

        self.assertEqual(caught.exception.code, "trust_root_identity_mismatch")
        self.assertEqual(anchor.read_bytes(), anchor_before)
        self.assertTrue((old_root / created["task_id"] / "tracked.txt").is_file())
        self.assertEqual(list(self.trust_root.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "persistent trust-root anchor uses Windows ACL and handle-relative proof")
    def test_corrupt_persistent_anchor_is_preserved_and_never_reinitialized(self) -> None:
        self.workspace.create_snapshot_candidate(SafeSnapshotMaterializer(shutil.which("git")))
        anchors = list(self.base.glob(".self-evolution-root-anchor-*.json"))
        self.assertEqual(len(anchors), 1)
        anchor = anchors[0]
        corrupted = b"{corrupt-anchor-do-not-replace"
        anchor.write_bytes(corrupted)

        restarted = SelfEvolutionWorkspace(self.stable, self.trust_root, self.workspace._integrity_key, enrollment_path=self.enrollment_path)
        with self.assertRaises(WorkspaceError) as caught:
            restarted.create_snapshot_candidate(SafeSnapshotMaterializer(shutil.which("git")))

        self.assertEqual(caught.exception.code, "trust_root_anchor_corrupt")
        self.assertEqual(anchor.read_bytes(), corrupted)
        self.assertEqual(len(list(self.base.glob(".self-evolution-root-anchor-*.json"))), 1)

    @unittest.skipUnless(os.name == "nt", "persistent trust-root anchor uses Windows ACL and handle-relative proof")
    def test_missing_anchor_with_existing_candidates_is_not_reinitialized(self) -> None:
        created = self.workspace.create_snapshot_candidate(SafeSnapshotMaterializer(shutil.which("git")))
        anchor = next(self.base.glob(".self-evolution-root-anchor-*.json"))
        anchor.unlink()
        restarted = SelfEvolutionWorkspace(self.stable, self.trust_root, self.workspace._integrity_key, enrollment_path=self.enrollment_path)

        with self.assertRaises(WorkspaceError) as caught:
            restarted.create_snapshot_candidate(SafeSnapshotMaterializer(shutil.which("git")))

        self.assertEqual(caught.exception.code, "trust_root_anchor_missing")
        self.assertFalse(anchor.exists())
        self.assertTrue((self.trust_root / created["task_id"] / "tracked.txt").is_file())

    @unittest.skipUnless(os.name == "nt", "persistent trust-root anchor uses Windows ACL and handle-relative proof")
    def test_persistent_anchor_rejects_wrong_host_integrity_key_without_rewriting(self) -> None:
        self.workspace.create_snapshot_candidate(SafeSnapshotMaterializer(shutil.which("git")))
        anchor = next(self.base.glob(".self-evolution-root-anchor-*.json"))
        before = anchor.read_bytes()
        restarted = SelfEvolutionWorkspace(
            self.stable, self.trust_root, b"wrong-host-integrity-key-32-bytes!!",
            enrollment_path=self.enrollment_path,
        )

        with self.assertRaises(WorkspaceError) as caught:
            restarted.create_snapshot_candidate(SafeSnapshotMaterializer(shutil.which("git")))

        self.assertEqual(caught.exception.code, "trust_root_enrollment_tampered")
        self.assertEqual(anchor.read_bytes(), before)

    @unittest.skipUnless(os.name == "nt", "persistent trust-root anchor uses Windows ACL and handle-relative proof")
    def test_simultaneous_first_creates_leave_one_authenticated_anchor_and_no_partial_state(self) -> None:
        workspaces = [
            SelfEvolutionWorkspace(self.stable, self.trust_root, self.workspace._integrity_key, enrollment_path=self.enrollment_path, allow_initial_enrollment=True)
            for _ in range(2)
        ]
        barrier = Barrier(2)

        def create(workspace):
            barrier.wait(timeout=10)
            return workspace.create_snapshot_candidate(SafeSnapshotMaterializer(shutil.which("git")))

        results = []
        errors = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create, workspace) for workspace in workspaces]
            for future in futures:
                try:
                    results.append(future.result(timeout=90))
                except WorkspaceError as exc:
                    errors.append(exc)

        self.assertTrue(results, "at least one concurrent host must initialize and create")
        self.assertTrue(all(result["status"] == "created" for result in results))
        self.assertTrue(all(error.code in {"trust_root_anchor_busy", "trust_root_enrollment_busy"} for error in errors))
        anchors = list(self.base.glob(".self-evolution-root-anchor-*.json"))
        self.assertEqual(len(anchors), 1)
        self.assertFalse(anchors[0].with_suffix(anchors[0].suffix + ".lock").exists())
        self.assertEqual(len({result["task_id"] for result in results}), len(results))
        self.assertEqual(len([path for path in self.trust_root.iterdir() if path.is_dir()]), len(results))

    def test_host_configured_trust_root_replacement_is_rejected_before_snapshot_creation(self) -> None:
        replacement = self.base / "replacement-root"
        replacement.mkdir()
        original_identity = self.workspace._trust_root_identity
        self.trust_root.rename(self.base / "original-root")
        replacement.rename(self.trust_root)

        with self.assertRaises(WorkspaceError) as caught:
            self.workspace.create_snapshot_candidate(SafeSnapshotMaterializer(shutil.which("git")))

        self.assertEqual(caught.exception.code, "trust_root_identity_mismatch")
        self.assertEqual(self.workspace._trust_root_identity, original_identity)
        self.assertEqual(list(self.trust_root.iterdir()), [])
        self.assertTrue((self.base / "original-root").is_dir())

    @unittest.skipUnless(os.name == "nt", "independent enrollment uses Windows ACL and handle-relative proof")
    def test_root_and_anchor_loss_cannot_reenroll_while_independent_evidence_remains(self) -> None:
        self.workspace.create_snapshot_candidate(SafeSnapshotMaterializer(shutil.which("git")))
        anchor = next(self.base.glob(".self-evolution-root-anchor-*.json"))
        anchor.unlink()
        enrollment_before = self.enrollment_path.read_bytes()
        shutil.rmtree(self.trust_root)
        self.trust_root.mkdir()

        restarted = SelfEvolutionWorkspace(
            self.stable, self.trust_root, self.workspace._integrity_key,
            enrollment_path=self.enrollment_path,
        )
        with self.assertRaises(WorkspaceError) as caught:
            restarted.create_snapshot_candidate(SafeSnapshotMaterializer(shutil.which("git")))

        self.assertEqual(caught.exception.code, "trust_root_identity_mismatch")
        self.assertEqual(self.enrollment_path.read_bytes(), enrollment_before)
        self.assertFalse(anchor.exists())
        self.assertEqual(list(self.trust_root.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "independent enrollment uses Windows ACL and handle-relative proof")
    def test_missing_independent_enrollment_after_root_and_anchor_loss_never_silently_reenrolls(self) -> None:
        self.workspace.create_snapshot_candidate(SafeSnapshotMaterializer(shutil.which("git")))
        anchor = next(self.base.glob(".self-evolution-root-anchor-*.json"))
        anchor.unlink()
        shutil.rmtree(self.trust_root)
        self.trust_root.mkdir()
        restarted = SelfEvolutionWorkspace(
            self.stable, self.trust_root, self.workspace._integrity_key,
            enrollment_path=self.enrollment_path,
        )
        enrollment = self.enrollment_path.read_bytes()
        self.enrollment_path.unlink()
        with self.assertRaises(WorkspaceError) as caught:
            restarted.create_snapshot_candidate(SafeSnapshotMaterializer(shutil.which("git")))
        self.assertEqual(caught.exception.code, "trust_root_enrollment_required")
        self.assertFalse(self.enrollment_path.exists())
        self.assertFalse(anchor.exists())
        self.assertEqual(list(self.trust_root.iterdir()), [])
        self.assertNotEqual(enrollment, b"")

    def test_linked_trust_root_is_rejected(self) -> None:
        linked_root = self.base / "linked-candidate-root"
        try:
            linked_root.symlink_to(self.trust_root, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlink unavailable: {type(exc).__name__}")
        result = SelfEvolutionWorkspace(self.stable, linked_root, b"test-only-key-32-bytes-minimum!!").create()
        self.assertEqual(result["failure"]["code"], "linked_path")
        self.assertEqual(list(self.trust_root.iterdir()), [])

    def test_unresolved_creation_lock_fails_closed(self) -> None:
        lock = self.trust_root / ".self-evolution-create.lock"
        lock.write_text("active", encoding="utf-8")
        result = self.workspace.create()
        self.assertEqual(result["failure"]["code"], "concurrent_state")
        self.assertEqual(lock.read_text(encoding="utf-8"), "active")

    def test_existing_candidate_target_is_never_overwritten(self) -> None:
        task_id = "12345678-1234-4234-8234-123456789abc"
        occupied = self.trust_root / task_id
        occupied.mkdir()
        (occupied / "keep.txt").write_text("untouched", encoding="utf-8")
        with mock.patch("self_evolution_workspace.uuid.uuid4", return_value=task_id):
            result = self.workspace.create()
        self.assertEqual(result["status"], "creation_failed")
        self.assertEqual(result["failure"]["code"], "target_exists")
        self.assertEqual((occupied / "keep.txt").read_text(encoding="utf-8"), "untouched")


if __name__ == "__main__":
    unittest.main()
