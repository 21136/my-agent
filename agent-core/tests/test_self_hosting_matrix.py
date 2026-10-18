from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

_AGENT_CORE = __import__("pathlib").Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_hosting_matrix import run_host_candidate_probe, run_host_rollback_probe, run_local_deploy_probe, run_local_matrix, run_remote_matrix


class SelfHostingMatrixTests(unittest.TestCase):

    def test_remote_matrix_records_merged_pr_and_passed_checks(self) -> None:
        responses = {
            "view": {"state": "MERGED", "baseRefName": "main", "mergeCommit": {"oid": "b" * 40}},
            "checks": [{"name": "ci", "state": "SUCCESS", "bucket": "pass"}],
        }

        def runner(kind: str):
            return 0, responses[kind], ""

        report = run_remote_matrix(
            repository="21136/my-agent",
            pr_number=18,
            runner=runner,
        )
        scenarios = {item.scenario_id: item for item in report.scenarios}
        self.assertEqual(scenarios["S3"].status.value, "passed")
        self.assertEqual(scenarios["S4"].status.value, "passed")

    def test_host_candidate_probe_cleans_its_interface_lock(self) -> None:
        result = run_host_candidate_probe()
        self.assertEqual(result.status.value, "passed")
        lock_path = _AGENT_CORE.parent / "data" / "sessions" / ".interface.lock"
        self.assertFalse(lock_path.exists())

    def test_host_rollback_probe_records_e6_recovery(self) -> None:
        result = run_host_rollback_probe()
        self.assertEqual(result.scenario_id, "S6")
        self.assertEqual(result.status.value, "passed")
        self.assertEqual(result.level.value, "E6")
        self.assertIn("rollback", result.reason)

    def test_local_deploy_probe_returns_after_stopping_probe_process(self) -> None:
        result = run_local_deploy_probe()
        self.assertEqual(result.scenario_id, "S5")
        self.assertEqual(result.status.value, "passed")
        self.assertTrue(any(item.startswith("service=") for item in result.evidence))
        self.assertTrue(any(item.startswith("artifact_digest=") for item in result.evidence))

    def test_cli_routes_remote_runtime_options_to_remote_matrix(self) -> None:
        expected = run_local_matrix()
        with patch("self_hosting_matrix.run_local_matrix") as local_runner, patch(
            "self_hosting_matrix.run_remote_matrix", return_value=expected
        ) as remote_runner, patch("builtins.print"):
            from self_hosting_matrix import main

            exit_code = main(
                [
                    "--model",
                    "0x567-flash",
                    "--skill",
                    "self-hosting-verification@local",
                    "--runtime",
                    "local",
                    "--repository",
                    "21136/my-agent",
                    "--pr",
                    "18",
                    "--include-runtime",
                ]
            )

        self.assertEqual(exit_code, 0)
        local_runner.assert_not_called()
        remote_runner.assert_called_once_with(
            repository="21136/my-agent",
            pr_number=18,
            include_runtime=True,
            model_id="0x567-flash",
            skill_identity="self-hosting-verification@local",
            runtime_version="local",
        )

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
