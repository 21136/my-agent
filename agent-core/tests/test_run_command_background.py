"""IT-130 · Phase 31 D1 — run_command background → run_service escalate."""

from __future__ import annotations

import importlib.util
import sys
import time
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from run_command_policy import run_command_requires_confirm
from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry


def _load_run_command(main_py: Path):
    spec = importlib.util.spec_from_file_location("run_command_bg_under_test", main_py)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_run_service(main_py: Path):
    spec = importlib.util.spec_from_file_location("run_service_bg_under_test", main_py)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RunCommandBackgroundIT130Tests(unittest.TestCase):
    """IT-130: background dry_run + escalate + always confirm."""

    def test_background_dry_run_preview(self) -> None:
        with temporary_agent_paths(
            copy_tool_dirs=("common/run_command", "common/run_service")
        ) as paths:
            main_py = paths.evolve / "tools" / "common" / "run_command" / "main.py"
            mod = _load_run_command(main_py)
            mod._agent_root = lambda: paths.agent_root  # type: ignore[method-assign]

            out = mod.run_command(
                {
                    "command": "echo bg-preview",
                    "working_dir": "workspace",
                    "background": True,
                    "dry_run": True,
                }
            )
            self.assertTrue(out.get("ok"), out)
            self.assertTrue(out.get("dry_run"))
            self.assertTrue(out.get("background"))
            self.assertEqual(out.get("escalate_to"), "run_service")
            self.assertTrue(str(out.get("name", "")).startswith("cmd-"))

    def test_background_escalates_and_stop(self) -> None:
        with temporary_agent_paths(
            copy_tool_dirs=("common/run_command", "common/run_service")
        ) as paths:
            rc_main = paths.evolve / "tools" / "common" / "run_command" / "main.py"
            rs_main = paths.evolve / "tools" / "common" / "run_service" / "main.py"
            rc = _load_run_command(rc_main)
            rs = _load_run_service(rs_main)
            rc._agent_root = lambda: paths.agent_root  # type: ignore[method-assign]
            rs._agent_root = lambda: paths.agent_root  # type: ignore[method-assign]

            if sys.platform == "win32":
                cmd = "Start-Sleep -Seconds 30"
            else:
                cmd = "sleep 30"

            name = "cmd-it130-bg"
            out = rc.run_command(
                {
                    "command": cmd,
                    "working_dir": "workspace",
                    "background": True,
                    "name": name,
                    "ready_timeout_sec": 0,
                }
            )
            self.assertTrue(out.get("ok"), out)
            self.assertTrue(out.get("escalated"), out)
            self.assertTrue(out.get("background"), out)
            self.assertEqual(out.get("name"), name)
            state = out.get("state") if isinstance(out.get("state"), dict) else {}
            self.assertTrue(state.get("alive") or state.get("pid"), out)

            stop = rs.run_service({"action": "stop", "name": name, "force": True})
            self.assertTrue(stop.get("ok"), stop)
            time.sleep(0.3)

    def test_background_always_confirm(self) -> None:
        needs, reason = run_command_requires_confirm(
            command="npm run build",
            working_dir="workspace/demo",
            project_root="workspace/demo",
            background=True,
        )
        self.assertTrue(needs)
        self.assertEqual(reason, "background")

        with temporary_agent_paths(
            copy_tool_dirs=("common/run_command", "common/run_service")
        ) as paths:
            registry = ToolRegistry.load(paths)
            evolved = registry.get_evolved("run_command")
            assert evolved is not None
            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(
                    allowed_evolved={"run_command"},
                    project_root="workspace/demo",
                ),
                confirm_fn=lambda _preview, allow_approve_all=False: "n",
            )
            needs_exec = executor._needs_confirm(
                registry.get_builtin("run_evolved"),
                evolved,
                {
                    "tool_name": "run_command",
                    "arguments": {
                        "command": "npm run build",
                        "working_dir": "workspace/demo",
                        "background": True,
                    },
                },
                tool_name="run_evolved",
            )
            self.assertTrue(needs_exec)


if __name__ == "__main__":
    unittest.main()