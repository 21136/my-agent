"""IT-140 / IT-141 · Phase 32 Track E — git_branch + git_push."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry


def _load_mod(main_py: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, main_py)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    # Default branch name for older git
    subprocess.run(
        ["git", "checkout", "-b", "main"],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


class GitBranchIT140Tests(unittest.TestCase):
    def test_list_create_switch_and_confirm_gate(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("coding/git_branch",)) as paths:
            main_py = paths.evolve / "tools" / "coding" / "git_branch" / "main.py"
            mod = _load_mod(main_py, "git_branch_it140")
            mod._agent_root = lambda: paths.agent_root  # type: ignore[method-assign]

            repo = paths.workspace / "demo-repo"
            _init_repo(repo)
            wd = "workspace/demo-repo"

            listed = mod.git_branch({"action": "list", "working_dir": wd})
            self.assertTrue(listed.get("ok"), listed)
            self.assertIn("main", listed.get("branches") or [])

            dry = mod.git_branch(
                {
                    "action": "create",
                    "name": "feat-x",
                    "switch": True,
                    "working_dir": wd,
                    "dry_run": True,
                }
            )
            self.assertTrue(dry.get("ok"), dry)
            self.assertTrue(dry.get("dry_run"))

            created = mod.git_branch(
                {
                    "action": "create",
                    "name": "feat-x",
                    "switch": True,
                    "working_dir": wd,
                }
            )
            self.assertTrue(created.get("ok"), created)
            self.assertEqual(created.get("current"), "feat-x")

            switched = mod.git_branch(
                {"action": "switch", "name": "main", "working_dir": wd}
            )
            self.assertTrue(switched.get("ok"), switched)
            self.assertEqual(switched.get("current"), "main")

            # Conflicting dirty tree blocks switch without --force
            subprocess.run(
                ["git", "checkout", "feat-x"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            (repo / "a.txt").write_text("on-feat\n", encoding="utf-8")
            subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "feat change"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "checkout", "main"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            (repo / "a.txt").write_text("local-conflict\n", encoding="utf-8")
            blocked = mod.git_branch(
                {"action": "switch", "name": "feat-x", "working_dir": wd}
            )
            self.assertFalse(blocked.get("ok"), blocked)
            dry_switch = mod.git_branch(
                {
                    "action": "switch",
                    "name": "feat-x",
                    "working_dir": wd,
                    "dry_run": True,
                }
            )
            self.assertNotIn("-f", dry_switch.get("command") or [])
            self.assertNotIn("--force", dry_switch.get("command") or [])


            registry = ToolRegistry.load(paths)
            confirms: list[str] = []

            def confirm_fn(preview: str, allow_approve_all: bool = False) -> str:
                confirms.append(preview)
                return "y"

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(allowed_evolved={"git_branch"}),
                confirm_fn=confirm_fn,
            )
            list_ex = executor.run(
                "run_evolved",
                {
                    "tool_name": "git_branch",
                    "arguments": {"action": "list", "working_dir": wd},
                },
            )
            self.assertTrue(list_ex.ok, list_ex)
            self.assertEqual(confirms, [])

            create_ex = executor.run(
                "run_evolved",
                {
                    "tool_name": "git_branch",
                    "arguments": {
                        "action": "create",
                        "name": "feat-y",
                        "working_dir": wd,
                    },
                },
            )
            self.assertTrue(create_ex.ok, create_ex)
            self.assertEqual(len(confirms), 1)


class GitPushIT141Tests(unittest.TestCase):
    def test_push_to_bare_remote_and_forbid_force(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("coding/git_push",)) as paths:
            main_py = paths.evolve / "tools" / "coding" / "git_push" / "main.py"
            mod = _load_mod(main_py, "git_push_it141")
            mod._agent_root = lambda: paths.agent_root  # type: ignore[method-assign]

            repo = paths.workspace / "demo-repo"
            bare = paths.workspace / "remote.git"
            _init_repo(repo)
            subprocess.run(
                ["git", "init", "--bare", str(bare)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "remote", "add", "origin", str(bare)],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            wd = "workspace/demo-repo"

            forbidden = mod.git_push(
                {"working_dir": wd, "force": True}
            )
            self.assertFalse(forbidden.get("ok"), forbidden)
            self.assertIn("forbidden", (forbidden.get("error") or "").lower())

            dry = mod.git_push({"working_dir": wd, "dry_run": True, "set_upstream": True})
            self.assertTrue(dry.get("ok"), dry)
            self.assertTrue(dry.get("dry_run"))
            self.assertEqual(dry.get("branch"), "main")

            live = mod.git_push({"working_dir": wd, "set_upstream": True})
            self.assertTrue(live.get("ok"), live)
            self.assertEqual(live.get("branch"), "main")
            cmd = live.get("command") or []
            self.assertNotIn("--force", cmd)
            self.assertNotIn("--force-with-lease", cmd)

            registry = ToolRegistry.load(paths)
            confirms: list[str] = []

            def confirm_fn(preview: str, allow_approve_all: bool = False) -> str:
                confirms.append(preview)
                return "n"

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(allowed_evolved={"git_push"}),
                confirm_fn=confirm_fn,
            )
            dry_ex = executor.run(
                "run_evolved",
                {
                    "tool_name": "git_push",
                    "arguments": {"working_dir": wd, "dry_run": True},
                },
            )
            self.assertTrue(dry_ex.ok, dry_ex)
            self.assertEqual(confirms, [])

            rejected = executor.run(
                "run_evolved",
                {
                    "tool_name": "git_push",
                    "arguments": {"working_dir": wd},
                },
            )
            self.assertFalse(rejected.ok)
            self.assertEqual(len(confirms), 1)


if __name__ == "__main__":
    unittest.main()
