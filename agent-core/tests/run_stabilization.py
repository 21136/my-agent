"""Phase 18 stabilization Gate runner (IT-G · T-1807-01/02/03).

Run from ``agent-core/``::

    python tests/run_stabilization.py

Exit **0** when all tests pass (``expectedFailure`` / xfail counts as pass).
Exit **1** on any failure or error.  Prints a per-module PASS/FAIL summary (T-1807-02).

``GATE_MODULES`` — full unittest modules (M1-C～M1-F).
``GATE_CHECKER_TARGETS`` — partial class/method paths (checker core subset).

Deferred from Gate (see STABILIZATION.md §6.1): IT-62 (T-1824).
IT-38 / IT-11 in ``tests.test_cli_desktop_parity`` (T-1808-04/05; not in ``GATE_MODULES``).
Known xfail: none for corruption notices (IT-55/IT-56 done · T-1823-02/05).
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

# Gate modules in STABILIZATION.md §6.1 order (subset grows per T-1806/T-1807).
GATE_MODULES: tuple[str, ...] = (
    # M1-C · sidecar logging (IT-58)
    "tests.test_sidecar_logging",
    # M1-D · project / switch / import contracts (IT-01, IT-02, IT-06)
    "tests.test_project_lifecycle",
    "tests.test_project_switch",
    "tests.test_context_switch",
    "tests.test_module_contracts",
    # M1-E · confirm / shell / cancel / routing (IT-03, IT-04, IT-05, IT-08, IT-17)
    "tests.test_confirm_pipeline",
    "tests.test_cross_session_read",
    "tests.test_shell_session_ownership",
    "tests.test_turn_cancel",
    "tests.test_activity_router",
    # M1-F · guards M0 + IT-51 LLM timeout chain (IT-21, T-1806-01, T-1806-05)
    "tests.test_runtime_guards",
    # M1-F · guards M1 (IT-22～IT-23, T-1806-02)
    "tests.test_runtime_guards_m1",
    # M1-F · orphaned tool_calls repair (IT-42, T-1806-04, BUG-005)
    "tests.test_orphaned_tool_calls",
    # M1-F · evolve_log secrets redaction (IT-60, T-1806-06, T-110)
    "tests.test_sanitize_log_value",
    # M1-F · session corruption baseline (IT-55 jsonl + IT-56 state.json, T-1806-07/08)
    "tests.test_session_corruption",
)

# M1-F · checker M0/M1 core (IT-24～IT-25, T-1806-03, T-1614).
# Full file: 19 cases; Gate runs 18 — omits broken TOML parse edge probe.
GATE_CHECKER_TARGETS: tuple[str, ...] = (
    "tests.test_checker_subagent.ParseCheckerCommandTests",
    "tests.test_checker_subagent.VerdictMergeTests",
    "tests.test_checker_subagent.HardChecklistTests.test_missing_tool_fails",
    "tests.test_checker_subagent.HardChecklistTests.test_write_text_passes_with_demo",
    "tests.test_checker_subagent.CheckerTaskFromRecordTests",
    "tests.test_checker_subagent.CompletionGateTests",
    "tests.test_checker_subagent.CheckerRunnerTests",
    "tests.test_checker_subagent.AutoCheckerSpawnTests",
)


CHECKER_SUBSET_LABEL = "tests.test_checker_subagent (subset)"


@dataclass(frozen=True)
class GateEntryResult:
    label: str
    tests_run: int
    failures: int
    errors: int
    expected_failures: int
    unexpected_successes: int
    skipped: int

    @property
    def ok(self) -> bool:
        return self.failures == 0 and self.errors == 0 and self.unexpected_successes == 0

    def status_word(self) -> str:
        return "PASS" if self.ok else "FAIL"


def gate_entries() -> list[tuple[str, unittest.TestSuite]]:
    """Ordered (label, suite) pairs — one row per summary line."""
    loader = unittest.TestLoader()
    entries: list[tuple[str, unittest.TestSuite]] = [
        (module_name, loader.loadTestsFromName(module_name))
        for module_name in GATE_MODULES
    ]
    checker_suite = unittest.TestSuite()
    for target in GATE_CHECKER_TARGETS:
        checker_suite.addTests(loader.loadTestsFromName(target))
    entries.append((CHECKER_SUBSET_LABEL, checker_suite))
    return entries


def load_gate_suite() -> unittest.TestSuite:
    """Build the Gate test suite (modules + checker partial targets)."""
    suite = unittest.TestSuite()
    for _label, part in gate_entries():
        suite.addTests(part)
    return suite


def _entry_result(label: str, result: unittest.TestResult) -> GateEntryResult:
    return GateEntryResult(
        label=label,
        tests_run=result.testsRun,
        failures=len(result.failures),
        errors=len(result.errors),
        expected_failures=len(result.expectedFailures),
        unexpected_successes=len(result.unexpectedSuccesses),
        skipped=len(result.skipped),
    )


def _format_entry_line(row: GateEntryResult) -> str:
    detail = f"{row.tests_run} run"
    if row.failures:
        detail += f", {row.failures} fail"
    if row.errors:
        detail += f", {row.errors} err"
    if row.expected_failures:
        detail += f", {row.expected_failures} xfail"
    if row.unexpected_successes:
        detail += f", {row.unexpected_successes} uxpass"
    if row.skipped:
        detail += f", {row.skipped} skip"
    return f"  {row.label:<48} {row.status_word():<4}  {detail}"


def print_gate_summary(rows: list[GateEntryResult]) -> None:
    """Per-module PASS/FAIL table (stdout)."""
    total_run = sum(r.tests_run for r in rows)
    total_fail = sum(r.failures for r in rows)
    total_err = sum(r.errors for r in rows)
    total_xfail = sum(r.expected_failures for r in rows)
    total_uxpass = sum(r.unexpected_successes for r in rows)
    all_ok = all(r.ok for r in rows)

    print()
    print("Gate summary (IT-G):")
    for row in rows:
        print(_format_entry_line(row))
    print("-" * 72)
    total_status = "OK" if all_ok else "FAIL"
    total_detail = f"{total_run} run"
    if total_fail:
        total_detail += f", {total_fail} fail"
    if total_err:
        total_detail += f", {total_err} err"
    if total_xfail:
        total_detail += f", {total_xfail} xfail"
    if total_uxpass:
        total_detail += f", {total_uxpass} uxpass"
    print(f"  {'TOTAL':<48} {total_status:<4}  {total_detail}")
    print()


def run_gate(*, verbosity: int = 2) -> int:
    runner = unittest.TextTestRunner(verbosity=verbosity)
    rows: list[GateEntryResult] = []
    for label, suite in gate_entries():
        result = runner.run(suite)
        rows.append(_entry_result(label, result))
    print_gate_summary(rows)
    return 0 if all(r.ok for r in rows) else 1


def main() -> None:
    raise SystemExit(run_gate())


if __name__ == "__main__":
    main()
