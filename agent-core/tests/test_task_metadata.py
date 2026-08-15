"""T-5814 / IT-5814: parse TASKS artifact associations."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_mode import create_project, parse_task_metadata, parse_tasks_metadata
from progress_gate import task_evidence_contract
from tests.isolation_helpers import temporary_agent_paths


class TaskMetadataTests(unittest.TestCase):
    def test_it5814_template_task_has_fixed_associations(self) -> None:
        with temporary_agent_paths() as paths:
            root = create_project(paths, "metadata-demo")
            tasks = parse_tasks_metadata((root / "TASKS.md").read_text(encoding="utf-8"))
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["id"], "T-001")
            self.assertEqual(tasks[0]["req"], ["REQ-001"])
            self.assertEqual(tasks[0]["ac"], ["AC-001"])
            self.assertEqual(tasks[0]["design"], ["UX-001", "TD-001"])
            self.assertEqual(tasks[0]["verify"], ["V-001"])
            self.assertEqual(tasks[0]["evidence"], ["run_project_tests"])

    def test_it5814_parses_inline_and_indented_metadata(self) -> None:
        text = (
            "- [ ] T-007 wire API req: REQ-007; ac: AC-007, AC-008\n"
            "  design: UX-007, TD-007\n"
            "  verify: V-007\n"
            "  evidence: run_project_tests\n"
            "\n"
            "- [ ] T-008 documentation only\n"
        )
        tasks = parse_tasks_metadata(text)
        self.assertEqual(tasks[0]["text"], "wire API")
        self.assertEqual(tasks[0]["req"], ["REQ-007"])
        self.assertEqual(tasks[0]["ac"], ["AC-007", "AC-008"])
        self.assertEqual(tasks[0]["design"], ["UX-007", "TD-007"])
        self.assertEqual(tasks[0]["verify"], ["V-007"])
        self.assertEqual(tasks[0]["evidence"], ["run_project_tests"])
        self.assertEqual(tasks[1]["id"], "T-008")
        self.assertEqual(tasks[1]["req"], [])

    def test_it5814_single_task_parser_deduplicates_values(self) -> None:
        metadata = parse_task_metadata("req: REQ-001, REQ-001 | evidence: run_project_tests")
        self.assertEqual(metadata["req"], ["REQ-001"])
        self.assertEqual(metadata["evidence"], ["run_project_tests"])

    def test_it5815_contract_keeps_task_ac_and_verify_identity(self) -> None:
        text = (
            "- [ ] T-021 implement API\n"
            "  ac: AC-021\n"
            "  verify: V-021\n"
        )
        contract = task_evidence_contract(text, task_id="T-021")
        self.assertIsNotNone(contract)
        assert contract is not None
        self.assertEqual(contract["task_id"], "T-021")
        self.assertEqual(contract["ac_ids"], ["AC-021"])
        self.assertEqual(contract["verify_ids"], ["V-021"])
        self.assertTrue(contract["metadata_present"])


if __name__ == "__main__":
    unittest.main()
