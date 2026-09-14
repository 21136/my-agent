"""Phase 27 M1 — tool.progress / services.state / turn.evidence (IT-91 / IT-93)."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[1]
_ROOT = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor, _result_logs_tail, _tool_result_summary
from tools.registry import ToolRegistry
from tools.schema import tool_fail, tool_ok


class TestExecObservabilityM1(unittest.TestCase):
    def test_it91_logs_tail_on_failed_start_summary(self) -> None:
        """IT-91: run_service failure surfaces logs_tail in summary / helper."""
        fail = tool_fail(
            "run_evolved",
            "validation_error",
            "process exited before ready",
            details={
                "action": "start",
                "ready": False,
                "logs_tail": "boot...\nFATAL: secret too short\n",
            },
        )
        self.assertIn("secret too short", _tool_result_summary(fail))
        self.assertIn("FATAL", _result_logs_tail(fail) or "")

        warn = tool_ok(
            "run_evolved",
            {
                "tool_name": "run_service",
                "action": "start",
                "ready": False,
                "warning": "started but ready criteria not met within timeout",
                "logs_tail": "line-a\nline-b waiting\n",
            },
        )
        self.assertIn("ready criteria", _tool_result_summary(warn))
        self.assertIn("waiting", _result_logs_tail(warn) or "")

    def test_it91_executor_emits_progress_and_logs_tail(self) -> None:
        """IT-91: long tool emits tool.progress; failed start carries logs_tail on tool.end."""
        with temporary_agent_paths(copy_tool_dirs=("common/run_service",)) as paths:
            events: list[tuple[str, dict[str, Any]]] = []

            def on_event(etype: str, payload: dict[str, Any]) -> None:
                events.append((etype, payload))

            registry = ToolRegistry.load(paths)
            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(allowed_evolved={"run_service"}),
                confirm_fn=lambda *_a, **_k: "y",
                on_event=on_event,
            )
            # Intentionally fail ready: command exits immediately.
            result = executor.run(
                "run_evolved",
                {
                    "tool_name": "run_service",
                    "arguments": {
                        "action": "start",
                        "name": "obs-fail",
                        "command": f"{sys.executable} -c \"print('boom'); raise SystemExit(1)\"",
                        "working_dir": ".",
                        "ready_regex": "NEVER_MATCH",
                        "ready_timeout_sec": 3,
                    },
                },
            )
            self.assertFalse(result.ok, result)

            types = [t for t, _ in events]
            self.assertIn("tool.start", types)
            self.assertIn("tool.end", types)
            self.assertIn("services.state", types)
            end = next(p for t, p in events if t == "tool.end")
            self.assertFalse(end.get("ok"))
            # logs_tail may be on end payload or folded into summary
            summary = str(end.get("summary") or "")
            has_tail = isinstance(end.get("logs_tail"), str) and end["logs_tail"].strip()
            self.assertTrue(has_tail or "boom" in summary or "exited" in summary.lower(), end)

    def test_it93_turn_evidence_emitted(self) -> None:
        """IT-93: begin_turn + tool end emit turn.evidence for sidebar."""
        with temporary_agent_paths(copy_tool_dirs=("common/run_service",)) as paths:
            events: list[tuple[str, dict[str, Any]]] = []

            def on_event(etype: str, payload: dict[str, Any]) -> None:
                events.append((etype, payload))

            ws = paths.workspace / "demo"
            ws.mkdir(parents=True)
            (ws / "TASKS.md").write_text(
                "# Tasks\n\n- [ ] T-001 写一个文件\n",
                encoding="utf-8",
            )

            registry = ToolRegistry.load(paths)
            session = ExecutorSession(
                allowed_evolved={"run_service"},
                active_shell="project",
                project_id="demo",
                project_root="workspace/demo",
            )
            executor = ToolExecutor(
                registry=registry,
                session=session,
                confirm_fn=lambda *_a, **_k: "y",
                on_event=on_event,
            )
            executor.begin_turn()
            self.assertEqual(session.armed_task_id, "T-001")

            ev0 = next(p for t, p in events if t == "turn.evidence")
            self.assertEqual(ev0.get("armed_task_id"), "T-001")
            self.assertEqual(ev0.get("items"), [])

            # Cheap read-only list — still records evidence + services.state
            out = executor.run(
                "run_evolved",
                {"tool_name": "run_service", "arguments": {"action": "list"}},
            )
            self.assertTrue(out.ok, out)

            evidence_events = [p for t, p in events if t == "turn.evidence"]
            self.assertGreaterEqual(len(evidence_events), 2)
            last = evidence_events[-1]
            tools = [i["tool"] for i in last.get("items") or []]
            self.assertIn("run_service", tools)
            self.assertTrue(all(i.get("ok") for i in last["items"]))

            # Frontend contract: sidebar renders turn evidence
            panel = (_ROOT / "desktop" / "src" / "shells" / "unified" / "project-panel.ts").read_text(
                encoding="utf-8"
            )
            self.assertIn("sidebar-turn-summary", panel)
            self.assertIn("turnEvidence", panel)

    def test_progress_heartbeat_fires(self) -> None:
        """tool.progress heartbeat while a slow tool runs (>5s)."""
        with temporary_agent_paths(copy_tool_dirs=("common/run_service",)) as paths:
            events: list[tuple[str, dict[str, Any]]] = []

            def on_event(etype: str, payload: dict[str, Any]) -> None:
                events.append((etype, payload))

            registry = ToolRegistry.load(paths)
            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(allowed_evolved={"run_service"}),
                confirm_fn=lambda *_a, **_k: "y",
                on_event=on_event,
            )
            # wait on unknown service fails quickly — use sleep via python tool? 
            # Instead sleep in ready wait with a process that never prints marker.
            started = time.monotonic()
            result = executor.run(
                "run_evolved",
                {
                    "tool_name": "run_service",
                    "arguments": {
                        "action": "start",
                        "name": "obs-slow",
                        "command": f"{sys.executable} -c \"import time; time.sleep(12)\"",
                        "working_dir": ".",
                        "ready_regex": "NEVER",
                        "ready_timeout_sec": 8,
                    },
                },
            )
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 5.0)
            # May ok=False (not ready) or ok with warning depending on process still alive
            _ = result
            progress = [p for t, p in events if t == "tool.progress"]
            self.assertGreaterEqual(len(progress), 1, events)
            self.assertIn("仍在执行", str(progress[0].get("text") or ""))
            # cleanup
            executor.run(
                "run_evolved",
                {
                    "tool_name": "run_service",
                    "arguments": {"action": "stop", "name": "obs-slow", "force": True},
                },
            )


if __name__ == "__main__":
    unittest.main()
