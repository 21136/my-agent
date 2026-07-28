"""Phase 16 M1 runtime guards — inline max, scaffold demo, run_python reject (T-1511–T-1520)."""

from __future__ import annotations

import base64
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from runtime_guards import auto_demo_on_write_evolve, write_inline_max_chars
from tools.builtin.run_evolved import run_scaffold_demo
from tools.executor import (
    ExecutorSession,
    ScaffoldDemoRecord,
    ToolExecutor,
    _inline_body_guard,
    _validate_run_python_scaffold_guard,
)
from tools.logging import EvolveLog, read_events
from tools.registry import ToolRegistry, parse_tool_manifest

from tests.isolation_helpers import make_temp_agent_paths


def _write_manifest(path: Path, *, name: str) -> None:
    path.write_text(
        f"""[tool]
name = "{name}"
description = "m1 test tool"
version = "1.0.0"
status = "active"
topics = ["common"]

[entry]
type = "python"
path = "main.py"

[schema.input]
type = "object"

[schema.output]
type = "object"

[policy]
confirm = false
dry_run_supported = false
workspace_only = true
timeout_sec = 30
""",
        encoding="utf-8",
    )


class InlineWriteGuardTests(unittest.TestCase):
    def test_default_limit_is_8192(self) -> None:
        self.assertEqual(write_inline_max_chars(), 8192)

    def test_rejects_9000_char_write_text(self) -> None:
        err = _inline_body_guard(
            "write_text",
            {"tool_name": "write_text", "arguments": {"path": "x.txt", "content": "a" * 9000}},
        )
        self.assertIsNotNone(err)
        assert err is not None
        self.assertFalse(err.ok)
        self.assertEqual(err.error.details.get("guard_type"), "inline_write_max")

    def test_inline_write_guard_event_logs_without_crash(self) -> None:
        paths = make_temp_agent_paths(self, copy_tool_dirs=("common/write_text",))
        log_path = paths.data / "evolve_log_inline_guard_test.jsonl"
        session_dir = paths.data / "sessions" / "_inline_guard_log"
        session_dir.mkdir(parents=True, exist_ok=True)
        executor = ToolExecutor(
            registry=ToolRegistry.load(paths),
            session=ExecutorSession(session_dir=session_dir, turn_mode="agent"),
            evolve_log=EvolveLog(log_path),
        )
        result = executor.run(
            "run_evolved",
            {
                "tool_name": "write_text",
                "arguments": {"path": "_big.txt", "content": "a" * 9000},
            },
        )
        self.assertFalse(result.ok)
        guard_events = [e for e in read_events(log_path) if e.get("event") == "guard"]
        self.assertEqual(len(guard_events), 1)
        self.assertEqual(guard_events[0].get("guard_type"), "inline_write_max")

    def test_allows_workspace_path_reference(self) -> None:
        err = _inline_body_guard(
            "write_evolve",
            {
                "tool_name": "write_evolve",
                "path": "evolve/tools/common/x/main.py",
                "content_workspace_path": "_staging/main.py",
            },
        )
        self.assertIsNone(err)

    def test_staging_write_text_not_blocked_by_run_python_guard(self) -> None:
        session = ExecutorSession(
            scaffold_tool_turn=True,
            active_shell="grow",
            in_execute_segment=True,
        )
        session.segment_scaffold_tools["x"] = ScaffoldDemoRecord(
            tool_name="x",
            tool_dir="evolve/tools/common/x",
            demo_result={"attempted": True, "exit_code": 0},
        )
        err = _validate_run_python_scaffold_guard(
            session,
            {
                "tool_name": "run_python",
                "arguments": {"path": "workspace/_staging_main.py", "extra_args": ["demo"]},
            },
        )
        self.assertIsNone(err)


class ScaffoldDemoTests(unittest.TestCase):
    def test_run_scaffold_demo_cancellable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evolve = Path(tmp)
            tool_dir = evolve / "tools" / "common" / "sleepy_demo"
            tool_dir.mkdir(parents=True)
            (tool_dir / "main.py").write_text(
                """import time
if __name__ == "__main__":
    time.sleep(30)
    print("done")
""",
                encoding="utf-8",
            )
            manifest = tool_dir / "tool.toml"
            _write_manifest(manifest, name="sleepy_demo")
            tool = parse_tool_manifest(manifest, evolve_dir=evolve)
            cancel = threading.Event()

            def cancel_soon() -> None:
                time.sleep(0.15)
                cancel.set()

            threading.Thread(target=cancel_soon, daemon=True).start()
            started = time.perf_counter()
            result = run_scaffold_demo(tool, cancel_event=cancel)
            elapsed = time.perf_counter() - started
            self.assertTrue(result.get("cancelled") or result.get("skipped_reason") == "cancelled")
            self.assertLess(elapsed, 5.0)

    def test_auto_demo_after_tool_toml_writes_guard_log(self) -> None:
        paths = make_temp_agent_paths(
            self,
            copy_tool_dirs=("common/write_evolve",),
        )
        tool_dir = paths.evolve / "tools" / "common" / "m1_auto_demo_test"
        log_path = paths.data / "evolve_log_m1_test.jsonl"
        session_dir = paths.data / "sessions" / "_m1_auto_demo"
        session_dir.mkdir(parents=True, exist_ok=True)

        tool_dir.mkdir(parents=True, exist_ok=True)
        (tool_dir / "main.py").write_text(
            'if __name__ == "__main__":\n    print("[PASS] auto demo")\n',
            encoding="utf-8",
        )
        _write_manifest(tool_dir / "tool.toml", name="m1_auto_demo_test")
        from loader import session_evolved_allowlist
        from session import Session, SessionMeta, utc_now_iso

        session = Session(
            conversation_id="_m1_auto_demo",
            session_dir=session_dir,
            goal="",
            meta=SessionMeta(
                topics=["coding"],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[],
            paths=paths,
        )
        registry = ToolRegistry.load(paths)
        allowed = session_evolved_allowlist(session, registry=registry)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession(
                session_dir=session_dir,
                scaffold_tool_turn=True,
                active_shell="grow",
                allowed_evolved=allowed,
            ),
            confirm_fn=lambda _p, _a: "y",
            evolve_log=EvolveLog(log_path),
        )
        executor.begin_execute_segment()
        manifest_text = (tool_dir / "tool.toml").read_text(encoding="utf-8")
        (tool_dir / "tool.toml").unlink()
        result = executor.run(
            "run_evolved",
            {
                "tool_name": "write_evolve",
                "path": "evolve/tools/common/m1_auto_demo_test/tool.toml",
                "content_base64": base64.b64encode(manifest_text.encode("utf-8")).decode("ascii"),
                "on_conflict": "overwrite",
                "arguments": {},
            },
        )
        self.assertTrue(result.ok, result.error)
        record = executor.session.segment_scaffold_tools.get("m1_auto_demo_test")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertTrue(record.auto_demo)
        self.assertTrue(record.demo_result.get("attempted"))
        self.assertEqual(record.demo_result.get("exit_code"), 0)
        events = [e for e in read_events(log_path) if e.get("event") == "guard"]
        self.assertTrue(any(e.get("guard_type") == "scaffold_demo_auto" for e in events))


class EnvDefaultsTests(unittest.TestCase):
    def test_auto_demo_default_on(self) -> None:
        self.assertTrue(auto_demo_on_write_evolve())


if __name__ == "__main__":
    unittest.main()
