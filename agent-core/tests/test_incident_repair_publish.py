from __future__ import annotations

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from incident_repair_publish import IncidentRepairPolicy, publish_incident_repair


class IncidentRepairPublishTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.stable = self.base / "stable"
        self.sandbox = self.base / "sandbox"
        self.stable.mkdir()
        self._git(self.stable, "init", "-q")
        (self.stable / "evolve").mkdir()
        (self.stable / "evolve" / "_index.toml").write_text("", encoding="utf-8")
        (self.stable / ".gitattributes").write_text("*.py -text", encoding="utf-8")
        (self.stable / "agent-core").mkdir()
        (self.stable / "agent-core" / "repair.py").write_bytes(b"old\n")
        self._git(self.stable, "add", ".")
        self._git(self.stable, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "base")
        self._git(self.stable, "worktree", "add", "-qb", "experiment/sandbox", str(self.sandbox), "HEAD")
        (self.sandbox / "evolve" / "_index.toml").write_text("", encoding="utf-8")
        (self.stable / ".gitattributes").write_text("*.py -text", encoding="utf-8")
        self.head = self._git(self.stable, "rev-parse", "HEAD").strip()
        self.old_sha = hashlib.sha256(b"old\n").hexdigest()
        self.snapshots = {"stable": {"agent-core/repair.py": b"old\n"}, "sandbox": {"agent-core/repair.py": b"old\n"}}
        self.postimages = {"stable": {"agent-core/repair.py": b"new\n"}, "sandbox": {"agent-core/repair.py": b"new\n"}}
        self.policy = IncidentRepairPolicy(
            publish_enabled=True,
            registered_sandbox_root=self.sandbox,
        )

    def tearDown(self) -> None:
        self._git(self.stable, "worktree", "remove", "--force", str(self.sandbox))
        self.tmp.cleanup()

    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout

    def _publish(self, *, policy: IncidentRepairPolicy | None = None):
        return publish_incident_repair(
            self.stable,
            self.sandbox,
            allowed_paths={"agent-core/repair.py"},
            expected_heads={"stable": self.head, "sandbox": self.head},
            expected_old_sha256={
                "stable": {"agent-core/repair.py": self.old_sha},
                "sandbox": {"agent-core/repair.py": self.old_sha},
            },
            snapshots_by_lane=self.snapshots,
            changes_by_lane=self.postimages,
            policy=policy or self.policy,
        )

    def test_verified_patch_is_published_to_both_lanes(self) -> None:
        result = self._publish()
        self.assertEqual(result.lanes["stable"].status, "published")
        self.assertEqual(result.lanes["sandbox"].status, "published")
        self.assertFalse((self.stable / ".git" / "incident-repair-publish.lock").exists())
        self.assertEqual((self.stable / "agent-core" / "repair.py").read_bytes(), b"new\n")
        self.assertEqual((self.sandbox / "agent-core" / "repair.py").read_bytes(), b"new\n")

    def test_publication_refuses_an_existing_cross_process_lock(self) -> None:
        lock = self.stable / ".git" / "incident-repair-publish.lock"
        lock.write_bytes(b"existing owner")
        result = self._publish()
        self.assertTrue(all(lane.status == "candidate" for lane in result.lanes.values()))
        self.assertEqual(lock.read_bytes(), b"existing owner")
        self.assertEqual((self.stable / "agent-core" / "repair.py").read_bytes(), b"old\n")

    def test_lane_specific_snapshots_and_postimages_are_verified_independently(self) -> None:
        sandbox_file = self.sandbox / "agent-core" / "repair.py"
        sandbox_file.write_bytes(b"old\r\n")
        self._git(self.sandbox, "add", "agent-core/repair.py")
        self._git(self.sandbox, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "sandbox line endings")
        sandbox_head = self._git(self.sandbox, "rev-parse", "HEAD").strip()
        self.snapshots["sandbox"]["agent-core/repair.py"] = b"old\r\n"
        self.postimages["sandbox"]["agent-core/repair.py"] = b"new\r\n"
        result = publish_incident_repair(
            self.stable,
            self.sandbox,
            allowed_paths={"agent-core/repair.py"},
            expected_heads={"stable": self.head, "sandbox": sandbox_head},
            expected_old_sha256={
                "stable": {"agent-core/repair.py": hashlib.sha256(b"old\n").hexdigest()},
                "sandbox": {"agent-core/repair.py": hashlib.sha256(b"old\r\n").hexdigest()},
            },
            snapshots_by_lane=self.snapshots,
            changes_by_lane=self.postimages,
            policy=self.policy,
        )
        self.assertEqual(result.lanes["stable"].status, "published")
        self.assertEqual(result.lanes["sandbox"].status, "published")
        self.assertEqual((self.stable / "agent-core" / "repair.py").read_bytes(), b"new\n")
        self.assertEqual(sandbox_file.read_bytes(), b"new\r\n")

    def test_disabled_policy_returns_candidate_without_writes(self) -> None:
        result = self._publish(policy=IncidentRepairPolicy(publish_enabled=False, registered_sandbox_root=self.sandbox))
        self.assertTrue(all(lane.status == "candidate" for lane in result.lanes.values()))
        self.assertEqual((self.stable / "agent-core" / "repair.py").read_bytes(), b"old\n")
        self.assertEqual((self.sandbox / "agent-core" / "repair.py").read_bytes(), b"old\n")

    def test_any_dirty_target_refuses_both_lanes_before_writing(self) -> None:
        (self.sandbox / "agent-core" / "repair.py").write_bytes(b"local edit\n")
        result = self._publish()
        self.assertEqual(result.lanes["stable"].status, "refused")
        self.assertEqual(result.lanes["sandbox"].status, "refused")
        self.assertEqual((self.stable / "agent-core" / "repair.py").read_bytes(), b"old\n")

    def test_unallowlisted_snapshot_is_refused_before_any_write(self) -> None:
        result = publish_incident_repair(
            self.stable,
            self.sandbox,
            allowed_paths={"agent-core/other.py"},
            expected_heads={"stable": self.head, "sandbox": self.head},
            expected_old_sha256={
                "stable": {"agent-core/repair.py": self.old_sha},
                "sandbox": {"agent-core/repair.py": self.old_sha},
            },
            snapshots_by_lane=self.snapshots,
            changes_by_lane=self.postimages,
            policy=self.policy,
        )
        self.assertTrue(all(lane.status == "refused" for lane in result.lanes.values()))
        self.assertEqual((self.stable / "agent-core" / "repair.py").read_bytes(), b"old\n")
        self.assertEqual((self.sandbox / "agent-core" / "repair.py").read_bytes(), b"old\n")

    def test_snapshot_digest_mismatch_refuses_both_lanes(self) -> None:
        bad_hashes = {
            "stable": {"agent-core/repair.py": "0" * 64},
            "sandbox": {"agent-core/repair.py": self.old_sha},
        }
        result = publish_incident_repair(
            self.stable, self.sandbox,
            allowed_paths={"agent-core/repair.py"},
            expected_heads={"stable": self.head, "sandbox": self.head},
            expected_old_sha256=bad_hashes,
            snapshots_by_lane=self.snapshots,
            changes_by_lane=self.postimages,
            policy=self.policy,
        )
        self.assertTrue(all(lane.status == "refused" for lane in result.lanes.values()))
        self.assertEqual((self.stable / "agent-core" / "repair.py").read_bytes(), b"old\n")

    def test_unregistered_sandbox_is_candidate_only(self) -> None:
        result = self._publish(policy=IncidentRepairPolicy(publish_enabled=True, registered_sandbox_root=None))
        self.assertTrue(all(lane.status == "candidate" for lane in result.lanes.values()))
        self.assertEqual((self.stable / "agent-core" / "repair.py").read_bytes(), b"old\n")


if __name__ == "__main__":
    unittest.main()
