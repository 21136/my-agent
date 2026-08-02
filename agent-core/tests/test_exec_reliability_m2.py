"""EXEC-RELIABILITY M2: turn.evidence reliability strip (S-160 contract)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
_ROOT = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from tools.schema import tool_fail


class S160ReliabilityPayloadTests(unittest.TestCase):
    def test_turn_evidence_includes_reliability(self) -> None:
        with temporary_agent_paths() as paths:
            events: list[tuple[str, dict[str, Any]]] = []

            def on_event(etype: str, payload: dict[str, Any]) -> None:
                events.append((etype, payload))

            executor = ToolExecutor(
                registry=ToolRegistry.load(paths),
                session=ExecutorSession(),
                confirm_fn=lambda *_a, **_k: "y",
                on_event=on_event,
            )
            executor.begin_turn()
            ev0 = next(p for t, p in events if t == "turn.evidence")
            rel0 = ev0.get("reliability") or {}
            self.assertEqual(rel0.get("postcondition"), "none")
            self.assertEqual(rel0.get("circuit_open"), [])

            fail = tool_fail(
                "list_dir",
                "execution_error",
                "vite crashed",
                details={
                    "exit_code": 1,
                    "logs_tail": (
                        "Error: Unexpected end of file\n"
                        "    at node_modules/@esbuild/win32-x64/esbuild.exe"
                    ),
                },
            )
            with patch.object(executor, "_execute_builtin", return_value=fail):
                executor.begin_execute_segment()
                for _ in range(3):
                    executor.run("list_dir", {"path": "workspace"})

            evidence = [p for t, p in events if t == "turn.evidence"]
            last = evidence[-1]
            rel = last.get("reliability") or {}
            self.assertEqual(rel.get("failure_class"), "B")
            self.assertIsNone(rel.get("playbook_id"))
            self.assertTrue(rel.get("circuit_open"), rel)

    def test_service_postcondition_fail_in_payload(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_service",)) as paths:
            events: list[tuple[str, dict[str, Any]]] = []

            def on_event(etype: str, payload: dict[str, Any]) -> None:
                events.append((etype, payload))

            soft = tool_fail  # noqa: F841 — clarity
            from tools.schema import tool_ok

            soft_fail = tool_ok(
                "run_evolved",
                {
                    "tool_name": "run_service",
                    "action": "start",
                    "ready": False,
                    "state": {"alive": False, "ready_port": 3000, "status": "exited"},
                    "warning": "started but ready criteria not met",
                    "logs_tail": "ECONNREFUSED 127.0.0.1:3000",
                },
            )
            executor = ToolExecutor(
                registry=ToolRegistry.load(paths),
                session=ExecutorSession(allowed_evolved={"run_service"}),
                confirm_fn=lambda *_a, **_k: "y",
                on_event=on_event,
            )
            executor.begin_execute_segment()
            with patch.object(executor, "_execute_builtin", return_value=soft_fail):
                executor.run(
                    "run_evolved",
                    {
                        "tool_name": "run_service",
                        "arguments": {
                            "action": "start",
                            "command": "npm run dev",
                            "working_dir": "frontend",
                        },
                    },
                )
            last = [p for t, p in events if t == "turn.evidence"][-1]
            rel = last.get("reliability") or {}
            self.assertEqual(rel.get("postcondition"), "fail")

    def test_desktop_strip_contract(self) -> None:
        panel = (
            _ROOT / "desktop" / "src" / "shells" / "unified" / "project-panel.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("sidebar-reliability", panel)
        self.assertIn("renderReliabilityStrip", panel)
        self.assertIn("turnPostcondition", panel)
        css = (_ROOT / "desktop" / "src" / "shells" / "unified" / "unified.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".sidebar-reliability", css)


if __name__ == "__main__":
    unittest.main()
