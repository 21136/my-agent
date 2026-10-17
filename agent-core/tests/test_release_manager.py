from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from release_manager import ReleaseController, build_artifact, verify_artifact


class ReleaseManagerTests(unittest.TestCase):
    def test_build_artifact_has_digest_and_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-artifact-") as directory:
            root = Path(directory) / "project"
            root.mkdir()
            (root / "app.py").write_text("print('ok')\n", encoding="utf-8")
            artifact = build_artifact(root, Path(directory) / "artifact.zip", version="0.1.0")
        self.assertEqual(artifact.version, "0.1.0")
        self.assertTrue(artifact.path.endswith("artifact.zip"))
        self.assertEqual(len(artifact.digest), 64)
        self.assertIn("app.py", artifact.files)


    def test_verify_artifact_rejects_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-digest-") as directory:
            root = Path(directory) / "project"
            root.mkdir()
            (root / "app.py").write_text("print('ok')\n", encoding="utf-8")
            artifact = build_artifact(root, Path(directory) / "artifact.zip", version="0.1.0")
            self.assertTrue(verify_artifact(Path(artifact.path), artifact.digest))
            self.assertFalse(verify_artifact(Path(artifact.path), "0" * 64))

    def test_deploy_health_failure_recovers_previous(self) -> None:
        calls: list[str] = []
        controller = ReleaseController(
            deploy=lambda artifact: calls.append("deploy") or {"ok": True, "version": "new"},
            health=lambda state: calls.append("health") or state.get("version") == "old",
            rollback=lambda previous: calls.append("rollback") or {"ok": True, "version": "old"},
        )
        result = controller.deploy_with_rollback({"version": "old"}, {"version": "new"})
        self.assertFalse(result.ok)
        self.assertEqual(result.state, "rolled_back")
        self.assertEqual(calls, ["deploy", "health", "rollback", "health"])


if __name__ == "__main__":
    unittest.main()
