from __future__ import annotations

import sys
import unittest

_AGENT_CORE = __import__("pathlib").Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_hosting_matrix import run_local_matrix


class SelfHostingMatrixTests(unittest.TestCase):
    def test_local_matrix_runs_s1_s2_and_marks_remote_scenarios_not_run(self) -> None:
        report = run_local_matrix()
        scenarios = {item.scenario_id: item for item in report.scenarios}
        self.assertEqual(scenarios["S1"].status.value, "passed")
        self.assertEqual(scenarios["S2"].status.value, "passed")
        for scenario_id in ("S3", "S4", "S5", "S6"):
            self.assertEqual(scenarios[scenario_id].status.value, "not_run")
        self.assertEqual(report.disposition, "partial")


if __name__ == "__main__":
    unittest.main()
