from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_TOOL_PATH = _AGENT_CORE.parent / "evolve" / "tools" / "coding" / "gh_actions" / "main.py"
_SPEC = importlib.util.spec_from_file_location("gh_actions_under_test", _TOOL_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
parse_check_lines = _MODULE.parse_check_lines
summarize_checks = _MODULE.summarize_checks
classify_ci_failure = _MODULE.classify_ci_failure
repair_decision = _MODULE.repair_decision


class GhActionsTests(unittest.TestCase):
    def test_dry_run_workflow_dispatch_is_explicit(self) -> None:
        original = _MODULE._require_gh
        _MODULE._require_gh = lambda: None
        try:
            result = _MODULE.run_gh_actions({
                "action": "run",
                "workflow": "ci.yml",
                "ref": "main",
                "dry_run": True,
            })
        finally:
            _MODULE._require_gh = original
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["args"][:3], ["gh", "workflow", "run"])
        self.assertIn("main", result["args"])

    def test_dry_run_required_checks_uses_pr_number(self) -> None:
        original = _MODULE._require_gh
        _MODULE._require_gh = lambda: None
        try:
            result = _MODULE.run_gh_actions({
                "action": "required_checks",
                "number": 42,
                "dry_run": True,
            })
        finally:
            _MODULE._require_gh = original
        self.assertTrue(result["ok"])
        self.assertIn("42", result["args"])
        self.assertIn("--required", result["args"])


    def test_parse_required_checks_distinguishes_states(self) -> None:
        text = (
            "build\tpass\t1m\thttps://example/build\n"
            "lint\tpending\t-\thttps://example/lint\n"
            "tests\tfail\t2m\thttps://example/tests\n"
        )
        checks = parse_check_lines(text)
        summary = summarize_checks(checks)
        self.assertEqual(len(checks), 3)
        self.assertEqual(summary["passed"], 1)
        self.assertEqual(summary["pending"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["status"], "failed")

    def test_empty_required_checks_are_unknown_not_passed(self) -> None:
        summary = summarize_checks([])
        self.assertEqual(summary["status"], "unknown")
        self.assertEqual(summary["passed"], 0)


    def test_ci_failure_classification_blocks_auth_and_allows_test_repair(self) -> None:
        auth = classify_ci_failure("HTTP 403 permission denied: token lacks workflow scope")
        test = classify_ci_failure("pytest failed: AssertionError in tests/test_app.py")
        self.assertEqual(auth["category"], "auth")
        self.assertFalse(auth["retryable"])
        self.assertEqual(test["category"], "test")
        self.assertTrue(test["retryable"])

    def test_repair_decision_has_bounded_attempts(self) -> None:
        first = repair_decision("pytest failed", attempt=1, max_attempts=2)
        last = repair_decision("pytest failed", attempt=2, max_attempts=2)
        self.assertEqual(first["action"], "repair_and_rerun")
        self.assertEqual(last["action"], "blocked")


    def test_repair_decision_persists_bounded_state_without_gh_call(self) -> None:
        from types import SimpleNamespace
        from tests.isolation_helpers import temporary_agent_paths

        with temporary_agent_paths() as paths:
            original_loader = _MODULE._load_paths
            _MODULE._load_paths = lambda: SimpleNamespace(discover=lambda start: paths)
            try:
                result = _MODULE.run_gh_actions({
                    "action": "repair_decision",
                    "repair_id": "ci-test",
                    "repository": "21136/my-agent",
                    "pr_number": 42,
                    "head_sha": "a" * 40,
                    "log": "pytest failed: AssertionError",
                    "max_attempts": 2,
                })
            finally:
                _MODULE._load_paths = original_loader

        self.assertTrue(result["ok"])
        self.assertEqual(result["state"]["status"], "repair_allowed")
        self.assertEqual(result["state"]["attempt"], 1)


    def test_repair_complete_moves_to_ready_to_deliver(self) -> None:
        from types import SimpleNamespace
        from tests.isolation_helpers import temporary_agent_paths

        with temporary_agent_paths() as paths:
            original_loader = _MODULE._load_paths
            _MODULE._load_paths = lambda: SimpleNamespace(discover=lambda start: paths)
            try:
                first = _MODULE.run_gh_actions({
                    "action": "repair_decision", "repair_id": "ci-complete",
                    "repository": "21136/my-agent", "pr_number": 42,
                    "head_sha": "a" * 40, "log": "pytest failed", "max_attempts": 2,
                })
                completed = _MODULE.run_gh_actions({
                    "action": "repair_complete", "repair_id": "ci-complete", "tests_ok": True,
                })
            finally:
                _MODULE._load_paths = original_loader
        self.assertEqual(first["state"]["status"], "repair_allowed")
        self.assertEqual(completed["state"]["status"], "ready_to_deliver")

    def test_invalid_action_and_missing_workflow_are_rejected(self) -> None:
        original = _MODULE._require_gh
        _MODULE._require_gh = lambda: None
        try:
            invalid = _MODULE.run_gh_actions({"action": "merge", "dry_run": True})
            missing = _MODULE.run_gh_actions({"action": "run", "dry_run": True})
        finally:
            _MODULE._require_gh = original
        self.assertFalse(invalid["ok"])
        self.assertFalse(missing["ok"])


if __name__ == "__main__":
    unittest.main()
