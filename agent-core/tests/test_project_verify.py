"""Tests for Phase 44 run_project_tests (IT-441～445)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from progress_gate import evidence_satisfies, make_evidence_entry
from project_verify import (
    compact_test_failure_preview,
    enrich_test_failure_payload,
    format_failures_summary,
    parse_pytest_output,
    run_project_tests,
)
from tests.isolation_helpers import temporary_agent_paths
from tools.registry import ToolRegistry


class ProjectVerifyTests(unittest.TestCase):
    def test_it441_pytest_parser_file_line(self) -> None:
        stdout = (
            "FAILED tests/test_demo.py::test_fail - AssertionError: boom\n"
            "tests/test_demo.py:42: AssertionError: boom\n"
        )
        failures, parse_ok = parse_pytest_output(stdout, "")
        self.assertTrue(parse_ok or failures)
        self.assertGreaterEqual(len(failures), 1)
        has_line = any(f.line == 42 for f in failures)
        self.assertTrue(has_line)

    def test_it443_gate_accepts_run_project_tests(self) -> None:
        ok, _ = evidence_satisfies(
            "test",
            [
                make_evidence_entry(
                    tool_name="run_project_tests",
                    evolved_name="run_project_tests",
                    ok=True,
                )
            ],
        )
        self.assertTrue(ok)

    def test_it444_gate_rejects_failed_run_project_tests(self) -> None:
        ok, _ = evidence_satisfies(
            "test",
            [
                make_evidence_entry(
                    tool_name="run_project_tests",
                    evolved_name="run_project_tests",
                    ok=False,
                )
            ],
        )
        self.assertFalse(ok)

    def test_dry_run(self) -> None:
        with temporary_agent_paths() as paths:
            proj = paths.workspace / "pv-demo"
            proj.mkdir(parents=True)
            (proj / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
            result = run_project_tests(
                paths,
                working_dir="workspace/pv-demo",
                suite="pytest",
                dry_run=True,
            )
            self.assertTrue(result.get("ok"))
            self.assertTrue(result.get("dry_run"))
            self.assertIn("command", result)

    def test_t4404_failure_summary_and_compact_preview(self) -> None:
        failures = [
            {"file": "tests/test_demo.py", "line": 42, "message": "AssertionError: boom"},
        ]
        summary = format_failures_summary(failures)
        self.assertIn("test_demo.py:42", summary)
        payload = enrich_test_failure_payload(
            {
                "ok": False,
                "failures": failures,
                "raw_excerpt": "x" * 2000,
                "suite": "pytest",
            }
        )
        self.assertIn("failure_summary", payload)
        self.assertLess(len(payload["raw_excerpt"]), 600)
        preview = compact_test_failure_preview(payload)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIn("test_demo.py", preview)

    def test_t4404_spill_uses_compact_preview(self) -> None:
        import os

        from tools.executor import _TOOL_OUTPUTS_DIR, maybe_spill_result, to_json
        from tools.schema import tool_fail

        prev_spill = os.environ.get("TOOL_OUTPUT_SPILL_CHARS")
        prev_preview = os.environ.get("TOOL_OUTPUT_PREVIEW_CHARS")
        os.environ["TOOL_OUTPUT_SPILL_CHARS"] = "400"
        os.environ["TOOL_OUTPUT_PREVIEW_CHARS"] = "800"
        try:
            paths = AgentPaths.discover()
            session_dir = paths.data / "sessions" / "_it4404_compact"
            session_dir.mkdir(parents=True, exist_ok=True)
            big_log = "LINE\n" * 500
            payload = enrich_test_failure_payload(
                {
                    "ok": False,
                    "tool_name": "run_project_tests",
                    "failures": [
                        {"file": "a.py", "line": 1, "message": "fail"},
                    ],
                    "raw_excerpt": big_log,
                }
            )
            result = tool_fail(
                "run_evolved",
                "validation_error",
                "tests failed",
                details=payload,
            )
            self.assertGreater(len(to_json(result)), 400)
            spilled = maybe_spill_result(
                result,
                session_dir=session_dir,
                agent_paths=paths,
            )
            self.assertTrue(spilled.truncated)
            assert spilled.error is not None
            details = spilled.error.details or {}
            preview = str(details.get("preview") or "")
            self.assertIn("a.py", preview)
            self.assertNotIn("LINE\n" * 50, preview)
        finally:
            if prev_spill is None:
                os.environ.pop("TOOL_OUTPUT_SPILL_CHARS", None)
            else:
                os.environ["TOOL_OUTPUT_SPILL_CHARS"] = prev_spill
            if prev_preview is None:
                os.environ.pop("TOOL_OUTPUT_PREVIEW_CHARS", None)
            else:
                os.environ["TOOL_OUTPUT_PREVIEW_CHARS"] = prev_preview
            spill_dir = paths.data / "sessions" / "_it4404_compact" / _TOOL_OUTPUTS_DIR
            if spill_dir.is_dir():
                for old in spill_dir.glob("*.txt"):
                    old.unlink(missing_ok=True)

    def test_it442_jest_fail_line_parser(self) -> None:
        from project_verify import parse_jest_output

        stdout = "FAIL src/foo.test.ts:12:3\n"
        failures, ok = parse_jest_output(stdout, "")
        self.assertTrue(ok)
        self.assertEqual(failures[0].file, "src/foo.test.ts")
        self.assertEqual(failures[0].line, 12)

    def test_registry_loads_run_project_tests(self) -> None:
        live = AgentPaths.discover()
        registry = ToolRegistry.load(live)
        tool = registry.get_evolved("run_project_tests")
        self.assertIsNotNone(tool)
        assert tool is not None
        self.assertEqual(tool.status, "active")
        self.assertEqual(tool.scope, "project")


if __name__ == "__main__":
    unittest.main()
