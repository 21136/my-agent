from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

import self_evolution_materializer as materializer_module  # noqa: E402
from self_evolution_materializer import MaterializationError, SafeSnapshotMaterializer  # noqa: E402


class _LifecycleTestMaterializer(SafeSnapshotMaterializer):
    """Exercise native lifecycle in fixtures without opening the production gate."""

    @staticmethod
    def _cleanup_is_race_safe() -> bool:
        return True


class MaterializerLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="isolated-materializer-lifecycle-")
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "--quiet", str(self.repo))
        self.git("-C", str(self.repo), "config", "user.name", "Fixture")
        self.git("-C", str(self.repo), "config", "user.email", "fixture@example.invalid")
        (self.repo / "tracked.txt").write_bytes(b"fixture payload\n")
        self.git("-C", str(self.repo), "add", ".")
        self.git("-C", str(self.repo), "commit", "--quiet", "-m", "fixture")
        self.commit = self.git("-C", str(self.repo), "rev-parse", "HEAD")
        object_dir = self.git("-C", str(self.repo), "rev-parse", "--git-path", "objects")
        self.objects = Path(object_dir)
        if not self.objects.is_absolute():
            self.objects = self.repo / self.objects
        self.git_executable = shutil.which("git")
        self.assertTrue(self.git_executable)
        self.materializer = _LifecycleTestMaterializer(self.git_executable)
        self.production_materializer = SafeSnapshotMaterializer(self.git_executable)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def git(*args: str) -> str:
        result = subprocess.run(["git", *args], capture_output=True, check=False)
        if result.returncode:
            raise AssertionError(result.stderr.decode("utf-8", "replace"))
        return result.stdout.decode().strip()

    def test_production_gate_stays_closed_without_host_bound_root_identity(self) -> None:
        destination = self.root / "candidate"
        self.assertFalse(self.production_materializer._cleanup_is_race_safe())
        with mock.patch.object(materializer_module, "_create_temporary_tree",
                               side_effect=AssertionError("closed gate must not create scratch")):
            with self.assertRaises(MaterializationError) as caught:
                self.production_materializer.materialize(self.objects, self.commit, destination)
        self.assertEqual(caught.exception.code, "unsafe_cleanup_unavailable")
        self.assertFalse(destination.exists())

    def test_production_lifecycle_creates_and_atomically_publishes_snapshot(self) -> None:
        destination = self.root / "candidate"
        result = self.materializer.materialize(self.objects, self.commit, destination)
        self.assertEqual((destination / "tracked.txt").read_bytes(), b"fixture payload\n")
        self.assertEqual(result.destination, str(destination))
        self.assertFalse(list(self.root.glob(".self-evolution-materializer-*")))

    def test_failed_publish_cleans_only_private_stage_and_preserves_destination(self) -> None:
        destination = self.root / "candidate"
        destination.mkdir()
        sentinel = destination / "owned.txt"
        sentinel.write_text("do not replace", encoding="utf-8")
        with self.assertRaises(MaterializationError) as caught:
            self.materializer.materialize(self.objects, self.commit, destination)
        self.assertEqual(caught.exception.code, "destination_exists")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "do not replace")
        self.assertFalse(list(self.root.glob(".self-evolution-materializer-*")))

    def test_creation_rejects_replaced_authorized_parent_identity(self) -> None:
        destination = self.root / "candidate"
        expected = self.materializer._directory_identity(self.root)
        with mock.patch.object(materializer_module, "_create_temporary_tree",
                               side_effect=MaterializationError("parent changed", code="parent_identity_changed")):
            with self.assertRaises(MaterializationError) as caught:
                self.materializer.materialize(self.objects, self.commit, destination)
        self.assertEqual(caught.exception.code, "parent_identity_changed")
        self.assertFalse(destination.exists())

    @unittest.skipUnless(os.name == "nt", "Windows handle-relative escape refusal")
    def test_windows_cleanup_rejects_escape_name_and_preserves_external_sentinel(self) -> None:
        sentinel = self.root / "outside.txt"
        sentinel.write_text("must survive", encoding="utf-8")
        parent_id = self.materializer._directory_identity(self.root)
        scratch = materializer_module._create_temporary_tree(self.root, expected_parent_identity=parent_id)
        try:
            with mock.patch.object(materializer_module._manifest, "_windows_enumerate_directory",
                                   return_value=("..\\outside.txt",)):
                with self.assertRaises(MaterializationError):
                    materializer_module._cleanup_temporary_tree(
                        scratch.path, expected_parent_identity=scratch.parent_identity,
                        expected_root_identity=scratch.root_identity,
                        parent_handle=scratch.parent_handle, root_handle=scratch.root_handle,
                    )
            self.assertTrue(scratch.path.exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "must survive")
        finally:
            scratch.close()

    @unittest.skipUnless(os.name == "nt", "Windows pinned-parent race behavior")
    def test_windows_pinned_authorized_parent_blocks_rename_and_preserves_sibling(self) -> None:
        sibling = self.root / "outside.txt"
        sibling.write_text("preserve", encoding="utf-8")
        parent_id = self.materializer._directory_identity(self.root)
        scratch = materializer_module._create_temporary_tree(self.root, expected_parent_identity=parent_id)
        moved = self.root.with_name(self.root.name + "-moved")
        try:
            with self.assertRaises(OSError):
                self.root.rename(moved)
            replaced = scratch.path.with_name(scratch.path.name + "-replacement")
            with self.assertRaises(OSError):
                scratch.path.rename(replaced)
            materializer_module._cleanup_temporary_tree(
                scratch.path, expected_parent_identity=scratch.parent_identity,
                expected_root_identity=scratch.root_identity,
                parent_handle=scratch.parent_handle, root_handle=scratch.root_handle,
            )
            self.assertTrue(sibling.exists())
            self.assertEqual(sibling.read_text(encoding="utf-8"), "preserve")
        finally:
            scratch.close()

    def test_cleanup_failure_preserves_unpublished_scratch_instead_of_claiming_success(self) -> None:
        destination = self.root / "candidate"
        with mock.patch.object(self.materializer, "_read_commit_tree",
                               side_effect=MaterializationError("injected object failure", code="invalid_commit")):
            with mock.patch.object(materializer_module, "_cleanup_temporary_tree",
                                   side_effect=MaterializationError("injected cleanup refusal", code="temporary_cleanup_failed")):
                with self.assertRaises(MaterializationError) as caught:
                    self.materializer.materialize(self.objects, self.commit, destination)
        self.assertEqual(caught.exception.code, "temporary_cleanup_failed")
        self.assertFalse(destination.exists())
        # A refused cleanup is intentionally preserved for host recovery.
        self.assertEqual(len(list(self.root.glob(".self-evolution-materializer-*"))), 1)


if __name__ == "__main__":
    unittest.main()
