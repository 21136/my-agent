from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_hosting_verify import ScenarioResult, ScenarioStatus, SelfHostingReport, VerificationLevel


class SelfHostingVerifyTests(unittest.TestCase):
    def test_report_does_not_promote_not_run_to_verified(self) -> None:
        report = SelfHostingReport("run-1", "0x567-flash", "skill@1#hash", "0.2")
        report.add(ScenarioResult("S1", ScenarioStatus.PASSED, VerificationLevel.E3, ("pytest",)))
        report.add(ScenarioResult("S2", ScenarioStatus.NOT_RUN, VerificationLevel.E0))
        self.assertEqual(report.disposition, "partial")

    def test_failed_and_blocked_are_distinct(self) -> None:
        failed = SelfHostingReport("run-2", "m", "s", "r")
        failed.add(ScenarioResult("S1", ScenarioStatus.FAILED, VerificationLevel.E2, reason="test"))
        self.assertEqual(failed.disposition, "failed")
        blocked = SelfHostingReport("run-3", "m", "s", "r")
        blocked.add(ScenarioResult("S1", ScenarioStatus.BLOCKED, VerificationLevel.E0, reason="approval"))
        self.assertEqual(blocked.disposition, "blocked")

    def test_duplicate_scenario_is_rejected_and_report_persists(self) -> None:
        report = SelfHostingReport("run-4", "m", "s", "r")
        result = ScenarioResult("S1", ScenarioStatus.PASSED, VerificationLevel.E1)
        report.add(result)
        with self.assertRaises(ValueError):
            report.add(result)
        with tempfile.TemporaryDirectory(prefix="self-hosting-report-") as directory:
            path = report.save(Path(directory) / "report.json")
            self.assertTrue(path.is_file())
            self.assertIn('"disposition": "verified"', path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
