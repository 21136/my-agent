"""T-5811 / IT-5811: manifest persistence and freshness propagation."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_manifest import (
    ManifestError,
    adopt_manifest_change,
    bootstrap_manifest,
    load_manifest,
    mark_evidence_stale,
    manifest_has_l2_stale,
    propagate_stale,
    refresh_manifest,
    save_manifest,
)


class ProjectManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "demo"
        self.root.mkdir()
        for name in (
            "PROJECT.md",
            "SCOPE.md",
            "DESIGN.md",
            "TECH-DESIGN.md",
            "TASKS.md",
            "VERIFY.md",
            "RELEASE.md",
        ):
            (self.root / name).write_text(f"# {name}\n", encoding="utf-8")
        self.addCleanup(self.temp_dir.cleanup)

    def test_it5811_roundtrip_has_seven_artifacts_and_fields(self) -> None:
        manifest = bootstrap_manifest(self.root, "demo", tier="small")
        loaded = load_manifest(self.root / ".plan-agent" / "manifest.json")
        self.assertEqual(loaded, manifest)
        assert loaded is not None
        self.assertEqual(loaded["manifest_revision"], "r0")
        self.assertEqual(loaded["project"]["tier"], "small")
        self.assertEqual({item["status"] for item in loaded["artifacts"]}, {"current"})
        self.assertEqual(
            {item["path"] for item in loaded["artifacts"]},
            {
                "PROJECT.md",
                "SCOPE.md",
                "DESIGN.md",
                "TECH-DESIGN.md",
                "TASKS.md",
                "VERIFY.md",
                "RELEASE.md",
            },
        )

    def test_it5811_l1_is_soft_and_l2_propagates_transitively(self) -> None:
        manifest = bootstrap_manifest(self.root, "demo")
        propagate_stale(manifest, "DESIGN.md", level="L1")
        statuses = {item["path"]: item["status"] for item in manifest["artifacts"]}
        self.assertEqual(statuses["DESIGN.md"], "stale_soft")
        self.assertEqual(statuses["TASKS.md"], "stale_soft")
        self.assertEqual(statuses["RELEASE.md"], "stale_soft")
        self.assertFalse(manifest_has_l2_stale(manifest))

        propagate_stale(manifest, "SCOPE.md", level="L2")
        statuses = {item["path"]: item["status"] for item in manifest["artifacts"]}
        self.assertTrue(manifest_has_l2_stale(manifest))
        self.assertEqual(statuses["SCOPE.md"], "stale")
        self.assertEqual(statuses["RELEASE.md"], "stale")

    def test_it5811_external_edit_keeps_revision_and_marks_stale(self) -> None:
        manifest = bootstrap_manifest(self.root, "demo")
        original_revision = manifest["artifacts"][1]["revision"]
        (self.root / "SCOPE.md").write_text("# changed\n", encoding="utf-8")
        self.assertTrue(refresh_manifest(manifest, self.root))
        scope = next(item for item in manifest["artifacts"] if item["path"] == "SCOPE.md")
        self.assertEqual(scope["revision"], original_revision)
        self.assertEqual(scope["status"], "stale")

    def test_it5811_evidence_stale_does_not_change_revision(self) -> None:
        manifest = bootstrap_manifest(self.root, "demo")
        revision = next(item for item in manifest["artifacts"] if item["path"] == "VERIFY.md")["revision"]
        mark_evidence_stale(manifest)
        verify = next(item for item in manifest["artifacts"] if item["path"] == "VERIFY.md")
        release = next(item for item in manifest["artifacts"] if item["path"] == "RELEASE.md")
        self.assertEqual(verify["status"], "evidence_stale")
        self.assertEqual(release["status"], "evidence_stale")
        self.assertEqual(verify["revision"], revision)

    def test_it5811_adopt_bumps_revision_and_roundtrips_json(self) -> None:
        manifest = bootstrap_manifest(self.root, "demo")
        (self.root / "DESIGN.md").write_text("# adopted\n", encoding="utf-8")
        adopt_manifest_change(manifest, self.root, "DESIGN.md", change_id="CHG-001", level="L1")
        design = next(item for item in manifest["artifacts"] if item["path"] == "DESIGN.md")
        tasks = next(item for item in manifest["artifacts"] if item["path"] == "TASKS.md")
        self.assertEqual(manifest["manifest_revision"], "r1")
        self.assertEqual(design["revision"], "r1")
        self.assertEqual(design["status"], "current")
        self.assertEqual(design["last_adopted_change"], "CHG-001")
        self.assertEqual(tasks["status"], "stale_soft")
        path = self.root / ".plan-agent" / "manifest.json"
        save_manifest(path, manifest)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["manifest_revision"], "r1")

    def test_it5811_rejects_manifest_without_standard_artifacts(self) -> None:
        manifest = bootstrap_manifest(self.root, "demo")
        manifest["artifacts"] = manifest["artifacts"][:-1]
        with self.assertRaises(ManifestError):
            save_manifest(self.root / "bad.json", manifest)


if __name__ == "__main__":
    unittest.main()
