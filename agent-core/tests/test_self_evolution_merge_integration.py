from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import self_evolution_snapshot_review as review
import self_evolution_merge_integration as integration


@unittest.skipUnless(os.name == "nt", "snapshot review uses Windows held handles")
class HostSelectedApplyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.candidate = self.root / "candidate"
        for root in (self.repo, self.candidate):
            (root / "agent-core").mkdir(parents=True)
            (root / "agent-core/a.py").write_bytes(b"old a\\n")
            (root / "agent-core/b.py").write_bytes(b"old b\\n")
        self.git("init", "--quiet")
        self.git("config", "user.name", "test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("add", "agent-core")
        self.git("commit", "--quiet", "-m", "baseline")
        self.baseline = review.freeze_baseline(candidate_root=self.candidate, candidate_id="candidate-1")
        (self.candidate / "agent-core/a.py").write_bytes(b"new a\\n")
        (self.candidate / "agent-core/b.py").write_bytes(b"candidate b\\n")
        (self.repo / "agent-core/b.py").write_bytes(b"user edit b\\n")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True).stdout.decode().strip()

    def prepare(self, selected=("agent-core/a.py",)):
        return integration.prepare_host_selected_apply(
            repository_root=self.repo, baseline=self.baseline,
            session_id="session-1", task_id="task-1", attempt_id="attempt-1",
            candidate_id="candidate-1", selected_paths=selected,
            trusted_host_binding_is_current=lambda: True,
        )

    def test_real_review_claim_and_candidate_apply_preserves_unselected_user_change(self):
        before_head = self.git("rev-parse", "HEAD")
        index = self.repo / self.git("rev-parse", "--git-path", "index")
        before_index = index.read_bytes()
        prepared = self.prepare()
        seen = []
        def private_native_claim(request):
            seen.append(request)
            return integration.NativeClaim(approved=True, request_sha256=request["request_sha256"],
                                           operation_id=request["operation_id"], selected_paths=request["selected_paths"])
        result = prepared.apply(trusted_native_claim=private_native_claim)
        self.assertEqual(result.status, "applied", result)
        self.assertEqual(result.written_paths, ("agent-core/a.py",))
        self.assertEqual((self.repo / "agent-core/a.py").read_bytes(), b"new a\\n")
        self.assertEqual((self.repo / "agent-core/b.py").read_bytes(), b"user edit b\\n")
        self.assertEqual(self.git("rev-parse", "HEAD"), before_head)
        self.assertEqual(index.read_bytes(), before_index)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["task_id"], "task-1")
        self.assertEqual(seen[0]["files"][0]["after_sha256"], integration.sha256_bytes(b"new a\\n"))
        with self.assertRaises(integration.HostApplyError):
            prepared.apply(trusted_native_claim=private_native_claim)

    def test_reviewed_add_and_delete_apply_and_keep_unselected_dirty_file(self):
        (self.candidate / "agent-core/a.py").unlink()
        (self.candidate / "agent-core/new.py").write_bytes(b"new file")
        prepared = self.prepare(("agent-core/a.py", "agent-core/new.py"))
        result = prepared.apply(trusted_native_claim=lambda request: integration.NativeClaim(
            True, request["request_sha256"], request["operation_id"], request["selected_paths"]))
        self.assertEqual(result.status, "applied", result)
        self.assertFalse((self.repo / "agent-core/a.py").exists())
        self.assertEqual((self.repo / "agent-core/new.py").read_bytes(), b"new file")
        self.assertEqual((self.repo / "agent-core/b.py").read_bytes(), b"user edit b\\n")

    def test_candidate_change_during_native_prompt_aborts_before_write(self):
        prepared = self.prepare()
        def claim(request):
            (self.candidate / "agent-core/a.py").write_bytes(b"different after prompt\\n")
            return integration.NativeClaim(True, request["request_sha256"], request["operation_id"], request["selected_paths"])
        result = prepared.apply(trusted_native_claim=claim)
        self.assertEqual(result.status, "stale_after_approval")
        self.assertEqual(result.error_code, "candidate_changed_after_approval")
        self.assertEqual((self.repo / "agent-core/a.py").read_bytes(), b"old a\\n")

    def test_forged_receipt_shape_and_wrong_identity_never_authorize(self):
        prepared = self.prepare()
        result = prepared.apply(trusted_native_claim=lambda request: {"approved": True, "issuer": "electron-main"})
        self.assertNotEqual(result.status, "applied")
        self.assertEqual((self.repo / "agent-core/a.py").read_bytes(), b"old a\\n")
        with self.assertRaises(integration.HostApplyError):
            integration.prepare_host_selected_apply(
                repository_root=self.repo, baseline=self.baseline,
                session_id="session-1", task_id="task-1", attempt_id="attempt-1",
                candidate_id="forged", selected_paths=("agent-core/a.py",),
                trusted_host_binding_is_current=lambda: True,
            )

    def test_native_claim_with_wrong_digest_is_rejected_and_not_replayed(self):
        prepared = self.prepare()
        def bad_claim(request):
            return integration.NativeClaim(True, "0" * 64, request["operation_id"], request["selected_paths"])
        result = prepared.apply(trusted_native_claim=bad_claim)
        self.assertEqual(result.status, "approval_rejected")
        self.assertEqual((self.repo / "agent-core/a.py").read_bytes(), b"old a\\n")
        with self.assertRaises(integration.HostApplyError):
            prepared.apply(trusted_native_claim=bad_claim)

    def test_target_user_edit_during_native_prompt_is_not_overwritten(self):
        prepared = self.prepare()
        def claim(request):
            (self.repo / "agent-core/a.py").write_bytes(b"human edit")
            return integration.NativeClaim(True, request["request_sha256"], request["operation_id"], request["selected_paths"])
        result = prepared.apply(trusted_native_claim=claim)
        self.assertEqual(result.status, "stale_after_approval")
        self.assertEqual(result.error_code, "target_changed_after_approval")
        self.assertEqual((self.repo / "agent-core/a.py").read_bytes(), b"human edit")

    def test_review_must_be_full_and_selected_paths_must_be_real_changes(self):
        with self.assertRaises(integration.HostApplyError):
            self.prepare(("agent-core/not-reviewed.py",))
        scoped = review.freeze_baseline(candidate_root=self.candidate, candidate_id="candidate-1", paths=("agent-core/a.py",))
        with self.assertRaises(integration.HostApplyError):
            integration.prepare_host_selected_apply(
                repository_root=self.repo, baseline=scoped,
                session_id="session-1", task_id="task-1", attempt_id="attempt-1",
                candidate_id="candidate-1", selected_paths=("agent-core/a.py",),
                trusted_host_binding_is_current=lambda: True,
            )

    def test_candidate_drift_after_first_write_stops_remaining_paths(self):
        (self.repo / "agent-core/b.py").write_bytes(b"old b\\n")
        self.git("update-index", "--refresh")
        prepared = self.prepare(("agent-core/a.py", "agent-core/b.py"))
        original = integration.executor._apply_one
        def apply_then_change(root, binding, content):
            original(root, binding, content)
            if binding.path == "agent-core/a.py":
                (self.candidate / "agent-core/b.py").write_bytes(b"changed after approval")
        def claim(request):
            return integration.NativeClaim(True, request["request_sha256"], request["operation_id"], request["selected_paths"])
        with mock.patch.object(integration.executor, "_apply_one", side_effect=apply_then_change):
            result = prepared.apply(trusted_native_claim=claim)
        self.assertEqual(result.status, "partial_failure")
        self.assertEqual(result.error_code, "trusted_execution_guard_rejected")
        self.assertEqual(result.written_paths, ("agent-core/a.py",))
        self.assertEqual(result.pending_paths, ("agent-core/b.py",))
        self.assertEqual((self.repo / "agent-core/b.py").read_bytes(), b"old b\\n")

    def test_selected_dirty_user_change_is_never_overwritten(self):
        with self.assertRaises(Exception):
            self.prepare(("agent-core/b.py",))
        self.assertEqual((self.repo / "agent-core/b.py").read_bytes(), b"user edit b\\n")
