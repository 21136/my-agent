"""IT-100～102 · Phase 28 M0 run_command."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry


def _load_run_command(main_py: Path):
    spec = importlib.util.spec_from_file_location("run_command_under_test", main_py)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RunCommandIT100Tests(unittest.TestCase):
    """IT-100: success + cwd out of bounds."""

    def test_success_echo(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_command",)) as paths:
            main_py = paths.evolve / "tools" / "common" / "run_command" / "main.py"
            mod = _load_run_command(main_py)
            mod._agent_root = lambda: paths.agent_root  # type: ignore[method-assign]

            marker = "SHELL_CHANNEL_IT100"
            if sys.platform == "win32":
                cmd = f"Write-Output '{marker}'"
            else:
                cmd = f"echo {marker}"

            out = mod.run_command({"command": cmd, "working_dir": "workspace"})
            self.assertTrue(out.get("ok"), out)
            self.assertEqual(out.get("exit_code"), 0)
            self.assertIn(marker, out.get("stdout", ""))
            self.assertEqual(out.get("cwd"), "workspace")

    def test_cwd_out_of_bounds(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_command",)) as paths:
            main_py = paths.evolve / "tools" / "common" / "run_command" / "main.py"
            mod = _load_run_command(main_py)
            mod._agent_root = lambda: paths.agent_root  # type: ignore[method-assign]

            out = mod.run_command({"command": "echo hi", "working_dir": "../outside"})
            self.assertFalse(out.get("ok"), out)
            err = (out.get("error") or "").lower()
            self.assertTrue(
                "out of bounds" in err or "not found" in err or "invalid" in err,
                out,
            )

    def test_dry_run(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_command",)) as paths:
            main_py = paths.evolve / "tools" / "common" / "run_command" / "main.py"
            mod = _load_run_command(main_py)
            mod._agent_root = lambda: paths.agent_root  # type: ignore[method-assign]

            out = mod.run_command({"command": "echo hi", "dry_run": True})
            self.assertTrue(out.get("ok"), out)
            self.assertTrue(out.get("dry_run"))
            self.assertIn("command", out)

    def test_env_deny(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_command",)) as paths:
            main_py = paths.evolve / "tools" / "common" / "run_command" / "main.py"
            mod = _load_run_command(main_py)
            mod._agent_root = lambda: paths.agent_root  # type: ignore[method-assign]

            out = mod.run_command(
                {"command": "echo hi", "env": {"LLM_API_KEY": "secret"}, "dry_run": True}
            )
            self.assertFalse(out.get("ok"), out)
            self.assertIn("not allowed", (out.get("error") or "").lower())


class RunCommandIT101Tests(unittest.TestCase):
    """IT-101: confirm required; approve_all session still confirms."""

    def test_requires_confirm_even_when_session_approved(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_command",)) as paths:
            registry = ToolRegistry.load(paths)
            evolved = registry.get_evolved("run_command")
            self.assertIsNotNone(evolved)
            self.assertFalse(evolved.policy.allow_approve_all)

            confirms: list[str] = []

            def reject_fn(preview: str, allow_approve_all: bool = False) -> str:
                confirms.append(preview)
                return "n"

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(
                    allowed_evolved={"run_command"},
                    workspace_evolved_approved=True,
                ),
                confirm_fn=reject_fn,
            )
            result = executor.run(
                "run_evolved",
                {
                    "tool_name": "run_command",
                    "arguments": {"command": "echo should-not-run"},
                },
            )
            self.assertFalse(result.ok)
            self.assertEqual(len(confirms), 1)
            self.assertIn("run_service", confirms[0])

    def test_dry_run_skips_confirm(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_command",)) as paths:
            registry = ToolRegistry.load(paths)
            confirms: list[str] = []

            def confirm_fn(preview: str, allow_approve_all: bool = False) -> str:
                confirms.append(preview)
                return "y"

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(allowed_evolved={"run_command"}),
                confirm_fn=confirm_fn,
            )
            result = executor.run(
                "run_evolved",
                {
                    "tool_name": "run_command",
                    "arguments": {"command": "echo hi", "dry_run": True},
                },
            )
            self.assertTrue(result.ok, getattr(result, "error", None) or result)
            self.assertEqual(confirms, [])
            self.assertEqual(executor.session.turn_evidence, [])


class RunCommandIT102Tests(unittest.TestCase):
    """IT-102: timeout kills long command."""

    def test_timeout(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_command",)) as paths:
            main_py = paths.evolve / "tools" / "common" / "run_command" / "main.py"
            mod = _load_run_command(main_py)
            mod._agent_root = lambda: paths.agent_root  # type: ignore[method-assign]

            if sys.platform == "win32":
                cmd = "Start-Sleep -Seconds 30"
            else:
                cmd = "sleep 30"

            out = mod.run_command({"command": cmd, "timeout_sec": 1})
            self.assertFalse(out.get("ok"), out)
            self.assertIn("timed out", (out.get("error") or "").lower())
            self.assertIn("run_service", (out.get("hint") or ""))


class RunCommandHardConfirmEvenIfPolicyAllows(unittest.TestCase):
    """Hard gate: even if policy.allow_approve_all were true, executor forces confirm."""

    def test_forced_confirm_overrides_policy_flag(self) -> None:
        from dataclasses import replace

        with temporary_agent_paths(copy_tool_dirs=("common/run_command",)) as paths:
            registry = ToolRegistry.load(paths)
            evolved = registry.get_evolved("run_command")
            assert evolved is not None
            # Mutate in-memory tool to prove executor hard-gate (not only toml).
            patched = replace(
                evolved,
                policy=replace(evolved.policy, allow_approve_all=True),
            )
            registry._evolved[evolved.name] = patched  # type: ignore[attr-defined]

            confirms: list[str] = []

            def reject_fn(preview: str, allow_approve_all: bool = False) -> str:
                confirms.append("hit")
                return "n"

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(
                    allowed_evolved={"run_command"},
                    workspace_evolved_approved=True,
                ),
                confirm_fn=reject_fn,
            )
            result = executor.run(
                "run_evolved",
                {"tool_name": "run_command", "arguments": {"command": "echo x"}},
            )
            self.assertFalse(result.ok)
            self.assertEqual(confirms, ["hit"])


class RunCommandIT110Tests(unittest.TestCase):
    """IT-110: Phase 29 A2 layered confirm."""

    def test_build_test_skips_confirm_in_project(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_command",)) as paths:
            registry = ToolRegistry.load(paths)
            evolved = registry.get_evolved("run_command")
            builtin = registry.get_builtin("run_evolved")
            assert evolved is not None and builtin is not None
            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(
                    allowed_evolved={"run_command"},
                    project_root="workspace/demo",
                    project_id="demo",
                ),
            )
            needs = executor._needs_confirm(
                builtin,
                evolved,
                {
                    "tool_name": "run_command",
                    "arguments": {
                        "command": "mvn -q test",
                        "working_dir": "workspace/demo",
                    },
                },
                tool_name="run_evolved",
            )
            self.assertFalse(needs)

    def test_install_still_confirms_in_project(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_command",)) as paths:
            registry = ToolRegistry.load(paths)
            evolved = registry.get_evolved("run_command")
            builtin = registry.get_builtin("run_evolved")
            assert evolved is not None and builtin is not None
            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(
                    allowed_evolved={"run_command"},
                    project_root="workspace/demo",
                    project_id="demo",
                ),
            )
            needs = executor._needs_confirm(
                builtin,
                evolved,
                {
                    "tool_name": "run_command",
                    "arguments": {
                        "command": "npm install",
                        "working_dir": "workspace/demo",
                    },
                },
                tool_name="run_evolved",
            )
            self.assertTrue(needs)

    def test_outside_project_confirms_readonly(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_command",)) as paths:
            registry = ToolRegistry.load(paths)
            evolved = registry.get_evolved("run_command")
            builtin = registry.get_builtin("run_evolved")
            assert evolved is not None and builtin is not None
            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(
                    allowed_evolved={"run_command"},
                    project_root="workspace/demo",
                ),
            )
            needs = executor._needs_confirm(
                builtin,
                evolved,
                {
                    "tool_name": "run_command",
                    "arguments": {"command": "echo hi", "working_dir": "."},
                },
                tool_name="run_evolved",
            )
            self.assertTrue(needs)


class RunCommandIT103Tests(unittest.TestCase):
    """IT-103: archived mvn_exec/npm_exec not callable via run_evolved."""

    def test_archived_not_in_active_allowlist(self) -> None:
        with temporary_agent_paths(
            copy_tool_dirs=(
                "common/run_command",
                "common/mvn_exec",
                "common/npm_exec",
            )
        ) as paths:
            registry = ToolRegistry.load(paths)
            mvn = registry.get_evolved("mvn_exec")
            npm = registry.get_evolved("npm_exec")
            self.assertIsNotNone(mvn)
            self.assertIsNotNone(npm)
            self.assertEqual(mvn.status, "archived")
            self.assertEqual(npm.status, "archived")

            active = {t.name for t in registry.evolved() if t.status == "active"}
            self.assertIn("run_command", active)
            self.assertNotIn("mvn_exec", active)
            self.assertNotIn("npm_exec", active)

    def test_archived_rejected_even_if_forced_allowlist(self) -> None:
        with temporary_agent_paths(
            copy_tool_dirs=("common/mvn_exec", "common/npm_exec", "common/run_command")
        ) as paths:
            registry = ToolRegistry.load(paths)
            confirms: list[str] = []

            def confirm_fn(preview: str, allow_approve_all: bool = False) -> str:
                confirms.append(preview)
                return "y"

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(
                    allowed_evolved={"mvn_exec", "npm_exec", "run_command"},
                ),
                confirm_fn=confirm_fn,
            )
            for name in ("mvn_exec", "npm_exec"):
                result = executor.run(
                    "run_evolved",
                    {"tool_name": name, "arguments": {"args": ["-v"]}},
                )
                self.assertFalse(result.ok, name)
                msg = (result.error.message if result.error else "") or ""
                self.assertIn("不可执行", msg)
            self.assertEqual(confirms, [])

    def test_repl_archived_not_in_active_allowlist(self) -> None:
        """IT-437: archived repl not callable via run_evolved."""
        with temporary_agent_paths(copy_tool_dirs=("common/repl", "common/run_command")) as paths:
            registry = ToolRegistry.load(paths)
            repl = registry.get_evolved("repl")
            self.assertIsNotNone(repl)
            assert repl is not None
            self.assertEqual(repl.status, "archived")

            active = {t.name for t in registry.evolved() if t.status == "active"}
            self.assertIn("run_command", active)
            self.assertNotIn("repl", active)

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(allowed_evolved={"repl", "run_command"}),
                confirm_fn=lambda _p, _a: "y",
            )
            result = executor.run(
                "run_evolved",
                {"tool_name": "repl", "arguments": {"code": "print(1)"}},
            )
            self.assertFalse(result.ok)
            msg = (result.error.message if result.error else "") or ""
            self.assertIn("不可执行", msg)


class RunCommandLongTimeoutIT164Tests(unittest.TestCase):
    """IT-164: npm install / rmdir node_modules use long timeout tier."""

    def test_npm_install_gets_long_default(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_command",)) as paths:
            main_py = paths.evolve / "tools" / "common" / "run_command" / "main.py"
            mod = _load_run_command(main_py)
            mod._agent_root = lambda: paths.agent_root  # type: ignore[method-assign]
            out = mod.run_command(
                {
                    "command": "npm install",
                    "working_dir": ".",
                    "dry_run": True,
                }
            )
            self.assertTrue(out.get("ok"))
            self.assertTrue(out.get("long_timeout_tier"))
            self.assertGreaterEqual(int(out.get("timeout_sec") or 0), 1800)

    def test_echo_stays_short(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_command",)) as paths:
            main_py = paths.evolve / "tools" / "common" / "run_command" / "main.py"
            mod = _load_run_command(main_py)
            mod._agent_root = lambda: paths.agent_root  # type: ignore[method-assign]
            cmd = "Write-Output hi" if sys.platform == "win32" else "echo hi"
            out = mod.run_command({"command": cmd, "dry_run": True})
            self.assertTrue(out.get("ok"))
            self.assertFalse(out.get("long_timeout_tier"))
            self.assertEqual(int(out.get("timeout_sec") or 0), 120)

    def test_rmdir_node_modules_long(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_command",)) as paths:
            main_py = paths.evolve / "tools" / "common" / "run_command" / "main.py"
            mod = _load_run_command(main_py)
            mod._agent_root = lambda: paths.agent_root  # type: ignore[method-assign]
            out = mod.run_command(
                {
                    "command": 'cmd /c "rmdir /s /q node_modules"',
                    "dry_run": True,
                }
            )
            self.assertTrue(out.get("long_timeout_tier"))
            self.assertGreaterEqual(int(out.get("timeout_sec") or 0), 1800)


if __name__ == "__main__":
    unittest.main()
