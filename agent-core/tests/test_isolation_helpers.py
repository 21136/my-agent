from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.isolation_helpers import _build_temp_root, _cleanup_temp_root


class IsolationHelperCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="isolation-helper-test-")
        self.fixture = Path(self.temp.name) / "agent-root"
        (self.fixture / "evolve").mkdir(parents=True)
        (self.fixture / "evolve" / "_index.core.toml").write_text("[[topic]]\nid = 'test'\n", encoding="utf-8")
        self.source_core = self.fixture / "agent-core"
        self.source_core.mkdir()
        self.sentinel = self.source_core / "user-data-sentinel.txt"
        self.sentinel.write_text("must remain untouched", encoding="utf-8")
        self.code_root = patch("tests.isolation_helpers._CODE_ROOT", self.fixture)
        self.code_root.start()
        self.addCleanup(self.code_root.stop)
        self.addCleanup(self.temp.cleanup)

    def test_build_uses_explicit_code_root_without_discovery_or_data_access(self) -> None:
        forbidden_data = self.fixture / "data"
        original_is_file = Path.is_file
        original_open = Path.open

        def reject_data_path(path: Path) -> None:
            try:
                path.absolute().relative_to(forbidden_data.absolute())
            except ValueError:
                return
            self.fail("helper attempted filesystem access under the code root data directory")

        def guarded_is_file(path: Path) -> bool:
            reject_data_path(path)
            return original_is_file(path)

        def guarded_open(path: Path, *args, **kwargs):
            reject_data_path(path)
            return original_open(path, *args, **kwargs)

        with (
            patch("tests.isolation_helpers.AgentPaths.discover", side_effect=AssertionError("discovery forbidden")),
            patch.object(Path, "is_file", guarded_is_file),
            patch.object(Path, "open", guarded_open),
        ):
            root = _build_temp_root()
        self.addCleanup(_cleanup_temp_root, root)
        self.assertEqual((root / "agent-core").resolve(), self.source_core.resolve())

    def test_cleanup_removes_only_registered_helper_root_and_preserves_core_target(self) -> None:
        root = _build_temp_root()
        self.addCleanup(_cleanup_temp_root, root)
        self.assertTrue((root / "agent-core").exists())

        _cleanup_temp_root(root)

        self.assertFalse(root.exists())
        self.assertEqual(self.sentinel.read_text(encoding="utf-8"), "must remain untouched")

    def test_cleanup_refuses_unregistered_directory(self) -> None:
        candidate = Path(self.temp.name) / "not-created-by-isolation-helper"
        candidate.mkdir()
        (candidate / "keep.txt").write_text("keep", encoding="utf-8")

        _cleanup_temp_root(candidate)

        self.assertEqual((candidate / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_cleanup_refuses_root_with_external_junction_or_symlink(self) -> None:
        root = _build_temp_root()
        external = Path(self.temp.name) / "external-target"
        external.mkdir()
        (external / "keep.txt").write_text("external data", encoding="utf-8")
        link = root / "unexpected-link"
        if os.name == "nt":
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(external)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode:
                _cleanup_temp_root(root)
                self.skipTest("Windows junction creation is unavailable in this test environment")
        else:
            try:
                link.symlink_to(external, target_is_directory=True)
            except OSError as exc:
                _cleanup_temp_root(root)
                self.skipTest(f"symlink creation is unavailable in this test environment: {exc}")

        try:
            _cleanup_temp_root(root)
            self.assertTrue(root.is_dir())
            self.assertEqual((external / "keep.txt").read_text(encoding="utf-8"), "external data")
        finally:
            if hasattr(link, "is_junction") and link.is_junction():
                link.rmdir()
            elif link.is_symlink():
                link.unlink()
            _cleanup_temp_root(root)
        self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
