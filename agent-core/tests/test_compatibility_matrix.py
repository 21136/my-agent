from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from compatibility_matrix import CompatibilityError, CompatibilityMatrix, CompatibilityRecord, CompatibilityStatus


class CompatibilityMatrixTests(unittest.TestCase):
    def record(self, status=CompatibilityStatus.SELF_HOSTING_VERIFIED) -> CompatibilityRecord:
        return CompatibilityRecord(
            model_id="0x567-flash",
            skill_identity="project-verification@1.0.0#hash",
            runtime_version="0.2.0",
            status=status,
            passed_scenarios=("S1", "S2", "S6"),
            evidence=("report.json",),
        )

    def test_stable_requires_self_hosting_verified(self) -> None:
        with self.assertRaises(CompatibilityError):
            self.record(CompatibilityStatus.LOCAL_VERIFIED).promote(CompatibilityStatus.STABLE)
        self.assertEqual(self.record().promote(CompatibilityStatus.STABLE).status, CompatibilityStatus.STABLE)

    def test_failed_scenario_cannot_promote(self) -> None:
        record = CompatibilityRecord(
            model_id="m", skill_identity="s", runtime_version="r",
            status=CompatibilityStatus.PARTIAL,
            passed_scenarios=("S1",), failed_scenarios=("S4",),
        )
        with self.assertRaises(CompatibilityError):
            record.promote(CompatibilityStatus.SELF_HOSTING_VERIFIED)

    def test_matrix_upsert_and_round_trip(self) -> None:
        matrix = CompatibilityMatrix()
        matrix.upsert(self.record(CompatibilityStatus.LOCAL_VERIFIED))
        matrix.upsert(self.record())
        self.assertEqual(len(matrix.records), 1)
        with tempfile.TemporaryDirectory(prefix="compat-matrix-") as directory:
            path = matrix.save(Path(directory) / "matrix.json")
            loaded = CompatibilityMatrix.load(path)
        self.assertEqual(loaded.records, matrix.records)


if __name__ == "__main__":
    unittest.main()
