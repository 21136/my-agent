"""Phase 26 M1 — port_status/kill_port + git_commit (IT-83 / IT-84)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
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


class PortGovernanceTests(unittest.TestCase):
    def test_it83_port_status_and_kill(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_service",)) as paths:
            main_py = paths.evolve / "tools" / "common" / "run_service" / "main.py"
            mod = _load_mod(main_py, "run_service_m1")
            mod._agent_root = lambda: paths.agent_root  # type: ignore[method-assign]

            # Separate process so kill_port does not kill the test runner.
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    (
                        "import socket,sys,time\n"
                        "s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(1)\n"
                        "print(s.getsockname()[1], flush=True)\n"
                        "time.sleep(90)\n"
                    ),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert child.stdout is not None
            port_line = child.stdout.readline().strip()
            port = int(port_line)
            try:
                time.sleep(0.3)
                status = mod.run_service({"action": "port_status", "port": port})
                self.assertTrue(status.get("ok"), status)
                self.assertTrue(status.get("pids"), status)
                self.assertIn(child.pid, status.get("pids") or [])

                registry = ToolRegistry.load(paths)
                confirms: list[str] = []

                def reject(preview: str, allow_approve_all: bool = False) -> str:
                    confirms.append("kill")
                    return "n"

                executor = ToolExecutor(
                    registry=registry,
                    session=ExecutorSession(allowed_evolved={"run_service"}),
                    confirm_fn=reject,
                )
                st = executor.run(
                    "run_evolved",
                    {
                        "tool_name": "run_service",
                        "arguments": {"action": "port_status", "port": port},
                    },
                )
                self.assertTrue(st.ok, st)
                self.assertEqual(confirms, [])

                denied = executor.run(
                    "run_evolved",
                    {
                        "tool_name": "run_service",
                        "arguments": {"action": "kill_port", "port": port},
                    },
                )
                self.assertFalse(denied.ok)
                self.assertIn("kill", confirms)

                killed = mod.run_service({"action": "kill_port", "port": port})
                self.assertTrue(killed.get("ok"), killed)
                time.sleep(0.5)
                after = mod.run_service({"action": "port_status", "port": port})
                self.assertFalse(after.get("pids"), after)
                self.assertIsNotNone(child.poll())
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)


class GitCommitTests(unittest.TestCase):
    def test_it84_dry_run_commit_and_confirm(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("coding/git_commit",)) as paths:
            main_py = paths.evolve / "tools" / "coding" / "git_commit" / "main.py"
            mod = _load_mod(main_py, "git_commit_m1")
            mod._agent_root = lambda: paths.agent_root  # type: ignore[method-assign]

            repo = paths.workspace / "demo-repo"
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
            (repo / "a.txt").write_text("one\n", encoding="utf-8")
            subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            (repo / "a.txt").write_text("two\n", encoding="utf-8")

            dry = mod.git_commit(
                {
                    "working_dir": "workspace/demo-repo",
                    "message": "update a",
                    "dry_run": True,
                }
            )
            self.assertTrue(dry.get("ok"), dry)
            self.assertTrue(dry.get("dry_run"))
            self.assertIn("a.txt", dry.get("would_stage") or [])

            # confirm: dry_run skips; real commit confirms then reject
            registry = ToolRegistry.load(paths)
            confirms: list[str] = []

            def confirm_fn(preview: str, allow_approve_all: bool = False) -> str:
                confirms.append(preview)
                return "y"

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(allowed_evolved={"git_commit"}),
                confirm_fn=confirm_fn,
            )
            dry_ex = executor.run(
                "run_evolved",
                {
                    "tool_name": "git_commit",
                    "arguments": {
                        "working_dir": "workspace/demo-repo",
                        "message": "update a",
                        "dry_run": True,
                    },
                },
            )
            self.assertTrue(dry_ex.ok, dry_ex)
            self.assertEqual(confirms, [])

            live = executor.run(
                "run_evolved",
                {
                    "tool_name": "git_commit",
                    "arguments": {
                        "working_dir": "workspace/demo-repo",
                        "message": "update a",
                    },
                },
            )
            self.assertTrue(live.ok, live)
            self.assertTrue(confirms)
            data = live.data if isinstance(live.data, dict) else {}
            self.assertTrue(data.get("commit") or data.get("ok") is not False)

            # Direct call also works
            (repo / "a.txt").write_text("three\n", encoding="utf-8")
            again = mod.git_commit(
                {"working_dir": "workspace/demo-repo", "message": "update a again"}
            )
            self.assertTrue(again.get("ok"), again)
            self.assertTrue(again.get("commit"))

            bad = mod.git_commit(
                {"working_dir": "workspace/demo-repo", "message": "x --amend y"}
            )
            self.assertFalse(bad.get("ok"))


if __name__ == "__main__":
    unittest.main()
