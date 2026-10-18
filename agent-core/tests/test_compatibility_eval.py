from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from compatibility_eval import record_report, status_for_report
from compatibility_matrix import CompatibilityMatrix, CompatibilityStatus
from self_hosting_verify import ScenarioResult, ScenarioStatus, SelfHostingReport, VerificationLevel


class CompatibilityEvalTests(unittest.TestCase):
    def test_local_partial_report_maps_to_local_verified(self) -> None:
        report = SelfHostingReport("run", "0x567-flash", "skill@1#h", "runtime")
        report.add(ScenarioResult("S1", ScenarioStatus.PASSED, VerificationLevel.E3))
        report.add(ScenarioResult("S2", ScenarioStatus.PASSED, VerificationLevel.E2))
        report.add(ScenarioResult("S3", ScenarioStatus.NOT_RUN, VerificationLevel.E0))
        self.assertEqual(status_for_report(report), CompatibilityStatus.LOCAL_VERIFIED)

    def test_failed_report_maps_to_partial(self) -> None:
        report = SelfHostingReport("run", "m", "s", "r")
        report.add(ScenarioResult("S1", ScenarioStatus.FAILED, VerificationLevel.E2))
        self.assertEqual(status_for_report(report), CompatibilityStatus.PARTIAL)

    def test_record_report_updates_matrix(self) -> None:
        report = SelfHostingReport("run", "m", "s", "r")
        report.add(ScenarioResult("S1", ScenarioStatus.PASSED, VerificationLevel.E3, ("pytest",)))
        matrix = CompatibilityMatrix()
        record = record_report(matrix, report)
        self.assertEqual(matrix.records, [record])
        self.assertEqual(record.passed_scenarios, ("S1",))


if __name__ == "__main__":
    unittest.main()
