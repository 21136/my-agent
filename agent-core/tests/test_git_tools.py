"""T-6203 / IT-6203: isolated Git snapshot and restore contracts."""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tests.isolation_helpers import temporary_agent_paths
from tools.builtin.run_evolved import run
from tools.registry import ToolRegistry


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _restore_runner(paths):
    script = paths.evolve / "tools" / "coding" / "git_restore" / "main.py"
    spec = importlib.util.spec_from_file_location("git_restore_under_test", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_restore


def _init_repo(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Git tool test")


class GitToolsTests(unittest.TestCase):
    def test_restore_defaults_to_preview_through_evolved_runner(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("coding/git_restore",)) as paths:
            _init_repo(paths.agent_root)
            target = paths.workspace / "default-preview.txt"
            target.write_text("base\n", encoding="utf-8")
            _git(paths.agent_root, "add", "workspace/default-preview.txt")
            _git(paths.agent_root, "commit", "-m", "base")
            target.write_text("changed\n", encoding="utf-8")
            current_hash = _sha256(target)

            manifest = paths.evolve / "tools" / "coding" / "git_restore" / "tool.toml"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace('status = "experimental"', 'status = "active"'),
                encoding="utf-8",
            )
            result = run(
                {
                    "tool_name": "git_restore",
                    "arguments": {
                        "path": "workspace/default-preview.txt",
                        "expected_hash": current_hash,
                    },
                },
                registry=ToolRegistry.load(paths),
            )
            self.assertTrue(result.ok, result.error)
            self.assertEqual(result.data["state"], "would_restore")
            self.assertEqual(target.read_text(encoding="utf-8"), "changed\n")

    def test_snapshot_returns_worktree_staged_and_untracked_details(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("coding/git_snapshot",)) as paths:
            _init_repo(paths.agent_root)
            tracked = paths.workspace / "tracked.txt"
            staged = paths.workspace / "staged.txt"
            untracked = paths.workspace / "new.txt"
            tracked.write_text("base\n", encoding="utf-8")
            staged.write_text("base staged\n", encoding="utf-8")
            _git(paths.agent_root, "add", "workspace/tracked.txt", "workspace/staged.txt")
            _git(paths.agent_root, "commit", "-m", "base")

            tracked.write_text("worktree changed\n", encoding="utf-8")
            staged.write_text("index changed\n", encoding="utf-8")
            _git(paths.agent_root, "add", "workspace/staged.txt")
            untracked.write_text("not tracked\n", encoding="utf-8")

            registry = ToolRegistry.load(paths)
            result = run(
                {
                    "tool_name": "git_snapshot",
                    "arguments": {"include_diff": True},
                },
                registry=registry,
            )

            self.assertTrue(result.ok, result.error)
            data = result.data
            self.assertIn("workspace/new.txt", data["untracked_paths"])
            self.assertIn("worktree changed", data["diff"])
            self.assertIn("index changed", data["staged_diff"])
            self.assertTrue(any("workspace/tracked.txt" in line for line in data["status_lines"]))

    def test_snapshot_can_limit_full_diff_to_a_path(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("coding/git_snapshot",)) as paths:
            _init_repo(paths.agent_root)
            first = paths.workspace / "first.txt"
            second = paths.workspace / "second.txt"
            first.write_text("one\n", encoding="utf-8")
            second.write_text("two\n", encoding="utf-8")
            _git(paths.agent_root, "add", ".")
            _git(paths.agent_root, "commit", "-m", "base")
            first.write_text("first changed\n", encoding="utf-8")
            second.write_text("second changed\n", encoding="utf-8")

            result = run(
                {
                    "tool_name": "git_snapshot",
                    "arguments": {
                        "paths": ["workspace/first.txt"],
                        "include_diff": True,
                    },
                },
                registry=ToolRegistry.load(paths),
            )

            self.assertTrue(result.ok, result.error)
            self.assertIn("first changed", result.data["diff"])
            self.assertNotIn("second changed", result.data["diff"])

    def test_snapshot_from_nested_working_dir_captures_entire_git_repository(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("coding/git_snapshot",)) as paths:
            repo_root = paths.workspace / "music"
            nested_dir = repo_root / "api"
            nested_dir.mkdir(parents=True)
            _init_repo(repo_root)
            root_file = repo_root / "README.md"
            nested_file = nested_dir / "service.py"
            root_file.write_text("base root\n", encoding="utf-8")
            nested_file.write_text("base api\n", encoding="utf-8")
            _git(repo_root, "add", ".")
            _git(repo_root, "commit", "-m", "base")
            root_file.write_text("changed root\n", encoding="utf-8")
            nested_file.write_text("changed api\n", encoding="utf-8")

            manifest_path = paths.evolve / "tools" / "coding" / "git_snapshot" / "tool.toml"
            manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
            inputs = manifest["schema"]["input"]["properties"]
            self.assertIn("working_dir", inputs)
            self.assertIn("cwd", inputs)

            result = run(
                {
                    "tool_name": "git_snapshot",
                    "arguments": {
                        "working_dir": "workspace/music/api",
                        "include_diff": True,
                    },
                },
                registry=ToolRegistry.load(paths),
            )

            self.assertTrue(result.ok, result.error)
            self.assertEqual(result.data["repo_root"], "workspace/music")
            self.assertIn("changed root", result.data["diff"])
            self.assertIn("changed api", result.data["diff"])

    def test_snapshot_nested_path_filters_are_relative_to_repository_root(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("coding/git_snapshot",)) as paths:
            repo_root = paths.workspace / "music"
            nested_dir = repo_root / "api"
            nested_dir.mkdir(parents=True)
            _init_repo(repo_root)
            root_file = repo_root / "README.md"
            nested_file = nested_dir / "service.py"
            root_file.write_text("base root\n", encoding="utf-8")
            nested_file.write_text("base api\n", encoding="utf-8")
            _git(repo_root, "add", ".")
            _git(repo_root, "commit", "-m", "base")
            root_file.write_text("selected root change\n", encoding="utf-8")
            nested_file.write_text("omitted api change\n", encoding="utf-8")

            result = run(
                {
                    "tool_name": "git_snapshot",
                    "arguments": {
                        "working_dir": "workspace/music/api",
                        "paths": ["README.md"],
                        "include_diff": True,
                    },
                },
                registry=ToolRegistry.load(paths),
            )

            self.assertTrue(result.ok, result.error)
            self.assertIn("selected root change", result.data["diff"])
            self.assertNotIn("omitted api change", result.data["diff"])
    def test_restore_dry_run_and_worktree_restore_require_matching_hash(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("coding/git_restore",)) as paths:
            _init_repo(paths.agent_root)
            target = paths.workspace / "restore.txt"
            target.write_text("base\n", encoding="utf-8")
            _git(paths.agent_root, "add", "workspace/restore.txt")
            _git(paths.agent_root, "commit", "-m", "base")
            target.write_text("agent change\n", encoding="utf-8")
            current_hash = _sha256(target)
            restore = _restore_runner(paths)

            preview = restore(
                {
                    "path": "workspace/restore.txt",
                    "expected_hash": current_hash,
                    "dry_run": True,
                }
            )
            self.assertTrue(preview["ok"], preview)
            self.assertEqual(target.read_text(encoding="utf-8"), "agent change\n")
            self.assertEqual(preview["state"], "would_restore")

            mismatch = restore(
                {
                    "path": "workspace/restore.txt",
                    "expected_hash": "0" * 64,
                }
            )
            self.assertFalse(mismatch["ok"])
            self.assertIn("expected_hash mismatch", mismatch["error"])
            self.assertEqual(target.read_text(encoding="utf-8"), "agent change\n")

            restored = restore(
                {
                    "path": "workspace/restore.txt",
                    "expected_hash": current_hash,
                    "dry_run": False,
                }
            )
            self.assertTrue(restored["ok"], restored)
            self.assertEqual(restored["state"], "restored")
            self.assertEqual(target.read_text(encoding="utf-8"), "base\n")

    def test_restore_staged_only_changes_index_and_rejects_untracked(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("coding/git_restore",)) as paths:
            _init_repo(paths.agent_root)
            target = paths.workspace / "staged.txt"
            target.write_text("base\n", encoding="utf-8")
            _git(paths.agent_root, "add", "workspace/staged.txt")
            _git(paths.agent_root, "commit", "-m", "base")
            target.write_text("staged change\n", encoding="utf-8")
            _git(paths.agent_root, "add", "workspace/staged.txt")
            current_hash = _sha256(target)
            restore = _restore_runner(paths)

            result = restore(
                {
                    "path": "workspace/staged.txt",
                    "scope": "staged",
                    "expected_hash": current_hash,
                    "dry_run": False,
                }
            )

            self.assertTrue(result["ok"], result)
            self.assertEqual(target.read_text(encoding="utf-8"), "staged change\n")
            self.assertEqual(_git(paths.agent_root, "diff", "--cached", "--", "workspace/staged.txt"), "")

            untracked = paths.workspace / "untracked.txt"
            untracked.write_text("new\n", encoding="utf-8")
            rejected = restore(
                {
                    "path": "workspace/untracked.txt",
                    "expected_hash": _sha256(untracked),
                }
            )
            self.assertFalse(rejected["ok"])
            self.assertIn("tracked file", rejected["error"])

    def test_restore_deleted_tracked_file_uses_deleted_state(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("coding/git_restore",)) as paths:
            _init_repo(paths.agent_root)
            target = paths.workspace / "deleted.txt"
            target.write_text("base\n", encoding="utf-8")
            _git(paths.agent_root, "add", "workspace/deleted.txt")
            _git(paths.agent_root, "commit", "-m", "base")
            target.unlink()
            restore = _restore_runner(paths)

            preview = restore(
                {
                    "path": "workspace/deleted.txt",
                    "expected_hash": "deleted",
                }
            )
            self.assertTrue(preview["ok"], preview)
            self.assertEqual(preview["state"], "would_restore")
            self.assertFalse(target.exists())

            restored = restore(
                {
                    "path": "workspace/deleted.txt",
                    "expected_hash": "deleted",
                    "dry_run": False,
                }
            )
            self.assertTrue(restored["ok"], restored)
            self.assertEqual(target.read_text(encoding="utf-8"), "base\n")


if __name__ == "__main__":
    unittest.main()
