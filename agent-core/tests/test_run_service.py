"""IT-style tests for evolve/tools/common/run_service (Phase 25)."""

from __future__ import annotations

import importlib.util
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


def _load_run_service(main_py: Path):
    spec = importlib.util.spec_from_file_location("run_service_under_test", main_py)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RunServiceTests(unittest.TestCase):
    def test_start_wait_logs_stop(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_service",)) as paths:
            main_py = paths.evolve / "tools" / "common" / "run_service" / "main.py"
            mod = _load_run_service(main_py)
            mod._agent_root = lambda: paths.agent_root  # type: ignore[method-assign]

            marker = "SERVICE_READY_MARKER_42"
            cmd = (
                f"{sys.executable} -c "
                f"\"import time; print({marker!r}, flush=True); time.sleep(90)\""
            )
            start = mod.run_service(
                {
                    "action": "start",
                    "name": "demo-svc",
                    "command": cmd,
                    "working_dir": ".",
                    "ready_regex": marker,
                    "ready_timeout_sec": 15,
                }
            )
            self.assertTrue(start.get("ok"), start)
            self.assertTrue(start.get("ready"), start)
            self.assertTrue((paths.data / "services" / "demo-svc.json").is_file())
            self.assertTrue((paths.data / "services" / "demo-svc.log").is_file())

            status = mod.run_service({"action": "status", "name": "demo-svc"})
            self.assertTrue(status.get("ok"))
            self.assertTrue(status.get("state", {}).get("alive"))

            logs = mod.run_service({"action": "logs", "name": "demo-svc", "tail_lines": 20})
            self.assertTrue(logs.get("ok"))
            self.assertIn(marker, logs.get("text", ""))

            listed = mod.run_service({"action": "list"})
            self.assertTrue(listed.get("ok"))
            names = [s.get("name") for s in listed.get("services", [])]
            self.assertIn("demo-svc", names)

            again = mod.run_service(
                {
                    "action": "start",
                    "name": "demo-svc",
                    "command": cmd,
                    "ready_timeout_sec": 0,
                }
            )
            self.assertFalse(again.get("ok"))

            stop = mod.run_service({"action": "stop", "name": "demo-svc"})
            self.assertTrue(stop.get("ok"), stop)
            time.sleep(0.5)
            status2 = mod.run_service({"action": "status", "name": "demo-svc"})
            self.assertFalse(status2.get("state", {}).get("alive"))

    def test_invalid_name(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_service",)) as paths:
            main_py = paths.evolve / "tools" / "common" / "run_service" / "main.py"
            mod = _load_run_service(main_py)
            mod._agent_root = lambda: paths.agent_root  # type: ignore[method-assign]
            out = mod.run_service({"action": "start", "name": "../bad", "command": "echo hi"})
            self.assertFalse(out.get("ok"))


class RunServiceConfirmGateTests(unittest.TestCase):
    def test_status_skips_confirm_start_requires(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_service",)) as paths:
            registry = ToolRegistry.load(paths)
            evolved = registry.get_evolved("run_service")
            self.assertIsNotNone(evolved)
            confirms: list[str] = []

            def confirm_fn(preview: str, allow_approve_all: bool = False) -> str:
                confirms.append(preview)
                return "y"

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(allowed_evolved={"run_service"}),
                confirm_fn=confirm_fn,
            )
            result = executor.run(
                "run_evolved",
                {"tool_name": "run_service", "arguments": {"action": "list"}},
            )
            self.assertTrue(result.ok, getattr(result, "error", None) or result)
            self.assertEqual(confirms, [])

            def reject_fn(preview: str, allow_approve_all: bool = False) -> str:
                confirms.append("start")
                return "n"

            executor2 = ToolExecutor(
                registry=registry,
                session=ExecutorSession(allowed_evolved={"run_service"}),
                confirm_fn=reject_fn,
            )
            denied = executor2.run(
                "run_evolved",
                {
                    "tool_name": "run_service",
                    "arguments": {
                        "action": "start",
                        "name": "x",
                        "command": f'{sys.executable} -c "print(1)"',
                    },
                },
            )
            self.assertFalse(denied.ok)
            self.assertIn("start", confirms)


if __name__ == "__main__":
    unittest.main()
