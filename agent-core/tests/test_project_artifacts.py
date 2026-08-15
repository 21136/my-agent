"""T-5812 / IT-5812: seven-file templates and one-time legacy migration."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_api import project_state_payload
from project_manifest import STANDARD_ARTIFACTS, load_manifest
from project_mode import create_project, migrate_legacy_project, project_dir
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


class ProjectArtifactTests(unittest.TestCase):
    def test_it5812_new_project_has_non_empty_standard_artifacts(self) -> None:
        with temporary_agent_paths() as paths:
            root = create_project(paths, "template-demo")
            for name in STANDARD_ARTIFACTS:
                with self.subTest(name=name):
                    self.assertTrue((root / name).is_file())
                    self.assertTrue((root / name).read_text(encoding="utf-8").strip())

            manifest = load_manifest(root / ".plan-agent" / "manifest.json")
            self.assertIsNotNone(manifest)
            assert manifest is not None
            self.assertEqual(
                {item["path"] for item in manifest["artifacts"] if item["path"] in STANDARD_ARTIFACTS},
                set(STANDARD_ARTIFACTS),
            )

    def test_it5812_migrates_legacy_project_content_once(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "legacy-demo"
            root = project_dir(paths, pid)
            root.mkdir(parents=True)
            (root / "PROJECT.md").write_text(
                "# Legacy\n\n## 目标\n保留旧目标\n\n## 验收标准\n- AC-007 old acceptance\n",
                encoding="utf-8",
            )
            (root / "TASKS.md").write_text("- [ ] T-009 old task\n", encoding="utf-8")
            (root / "MAP.md").write_text("# Legacy map\n入口：old.py\n", encoding="utf-8")
            (root / "ENV.md").write_text("# ENV\nquality: old command\n", encoding="utf-8")

            session = create_new(paths, conversation_id="_it5812_legacy")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            payload = project_state_payload(session, paths)
            manifest = load_manifest(root / ".plan-agent" / "manifest.json")
            self.assertIsNotNone(payload["manifest"])
            for name in STANDARD_ARTIFACTS:
                with self.subTest(name=name):
                    self.assertTrue((root / name).is_file())
                    self.assertTrue((root / name).read_text(encoding="utf-8").strip())
            self.assertIn("保留旧目标", (root / "SCOPE.md").read_text(encoding="utf-8"))
            self.assertIn("old.py", (root / "DESIGN.md").read_text(encoding="utf-8"))
            self.assertIn("old command", (root / "VERIFY.md").read_text(encoding="utf-8"))
            self.assertIn("req:", (root / "TASKS.md").read_text(encoding="utf-8"))
            self.assertEqual(manifest["manifest_revision"], "r0")
            self.assertEqual(
                {item["status"] for item in manifest["artifacts"]},
                {"current"},
            )

            before = {name: (root / name).read_text(encoding="utf-8") for name in STANDARD_ARTIFACTS}
            self.assertFalse(migrate_legacy_project(paths, pid))
            after = {name: (root / name).read_text(encoding="utf-8") for name in STANDARD_ARTIFACTS}
            self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
