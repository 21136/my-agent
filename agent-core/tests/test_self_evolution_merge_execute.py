from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

import self_evolution_merge_execute as merge  # noqa: E402


class SelectedMergeSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self._git("init", "--quiet")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "Test")
        (self.root / "agent-core").mkdir()
        (self.root / "agent-core" / "sample.py").write_bytes(b"before\n")
        (self.root / "agent-core" / "other.py").write_bytes(b"other\n")
        self._git("add", "agent-core/sample.py", "agent-core/other.py")
        self._git("commit", "--quiet", "-m", "base")
        self.manifest = [{
            "path": "agent-core/sample.py", "action": "modify",
            "beforeSha256": hashlib.sha256(b"before\n").hexdigest(),
            "afterSha256": hashlib.sha256(b"after\n").hexdigest(),
            "beforeMode": "100644", "afterMode": "100644",
            "beforeSize": 7, "afterSize": 6,
            "beforeOid": self._git("hash-object", "agent-core/sample.py"),
            "afterOid": self._blob_oid(b"after\n"), "contentRef": "candidate-blob-1",
        }]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _blob_oid(self, value: bytes) -> str:
        result = subprocess.run(["git", "-C", str(self.root), "hash-object", "--stdin"], input=value, check=True, capture_output=True)
        return result.stdout.decode().strip()

    def _git(self, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(self.root), *args], check=True,
                                capture_output=True, text=True)
        return result.stdout.strip()

    def test_captures_real_git_state_and_detects_post_approval_selected_file_drift(self):
        snapshot = merge.capture_selected_merge_state(self.root, self.manifest)
        self.assertEqual(snapshot.head, self._git("rev-parse", "HEAD"))
        self.assertTrue(merge.revalidate_selected_merge_state(snapshot))
        (self.root / "agent-core" / "sample.py").write_bytes(b"user edit after approval\n")

        self.assertFalse(merge.revalidate_selected_merge_state(snapshot))
        self.assertEqual((self.root / "agent-core" / "sample.py").read_bytes(), b"user edit after approval\n")

    def test_read_only_git_status_does_not_refresh_index_after_timestamp_change(self):
        target = self.root / "agent-core/sample.py"
        info = target.stat()
        os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns + 5_000_000_000))
        index = self.root / self._git("rev-parse", "--git-path", "index")
        before = index.stat()
        before_bytes = index.read_bytes()

        status = merge._git(self.root, "status", "--porcelain=v2", "-z", "--untracked-files=all")

        self.assertEqual(status, b"")
        self.assertEqual(index.read_bytes(), before_bytes)
        self.assertEqual((index.stat().st_dev, index.stat().st_ino), (before.st_dev, before.st_ino))
        snapshot = merge.capture_selected_merge_state(self.root, self.manifest)
        self.assertTrue(merge.revalidate_selected_merge_state(snapshot))

    def test_same_size_content_change_with_restored_mtime_is_rejected(self):
        snapshot = merge.capture_selected_merge_state(self.root, self.manifest)
        target = self.root / "agent-core/sample.py"
        info = target.stat()
        target.write_bytes(b"BEFORE\n")
        os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertFalse(merge.revalidate_selected_merge_state(snapshot))
        self.assertEqual(target.read_bytes(), b"BEFORE\n")

    def test_same_bytes_external_index_replacement_still_rejected(self):
        snapshot = merge.capture_selected_merge_state(self.root, self.manifest)
        index = self.root / self._git("rev-parse", "--git-path", "index")
        replacement = index.with_name("independent-index-copy")
        replacement.write_bytes(index.read_bytes())
        os.replace(replacement, index)
        self.assertFalse(merge.revalidate_selected_merge_state(snapshot))

    def test_detects_unselected_worktree_and_index_changes_after_snapshot(self):
        snapshot = merge.capture_selected_merge_state(self.root, self.manifest)
        other = self.root / "agent-core" / "other.py"
        other.write_bytes(b"unselected user edit\n")
        self.assertFalse(merge.revalidate_selected_merge_state(snapshot))

        other.write_bytes(b"other\n")
        fresh = merge.capture_selected_merge_state(self.root, self.manifest)
        self._git("update-index", "--assume-unchanged", "agent-core/other.py")
        self.assertFalse(merge.revalidate_selected_merge_state(fresh))

    def test_rejects_dirty_selected_path_before_creating_a_preflight_snapshot(self):
        target = self.root / "agent-core" / "sample.py"
        target.write_bytes(b"pre-existing user change\n")
        with self.assertRaises(merge.MergeSafetyError) as caught:
            merge.capture_selected_merge_state(self.root, self.manifest)
        self.assertEqual(caught.exception.code, "selected_path_has_existing_change")
        self.assertEqual(target.read_bytes(), b"pre-existing user change\n")

    def test_rejects_path_traversal_and_symlink_ancestor(self):
        for path in ("../outside.py", "agent-core/../outside.py", ".git/config"):
            with self.subTest(path=path), self.assertRaises(merge.MergeSafetyError):
                merge.capture_selected_merge_state(self.root, [{**self.manifest[0], "path": path}])

        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        link = self.root / "agent-core" / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        spec = [{**self.manifest[0], "path": "agent-core/escape/file.py"}]
        with self.assertRaises(merge.MergeSafetyError) as caught:
            merge.capture_selected_merge_state(self.root, spec)
        self.assertEqual(caught.exception.code, "selected_path_reparse_or_symlink")

    @unittest.skipUnless(os.name == "nt", "Windows ACL regression requires Windows")
    def test_windows_explicit_acl_is_not_lost_by_modify(self):
        target = self.root / "agent-core" / "sample.py"
        # Add an explicit, non-inherited read ACE to the disposable file.
        def sddl(path):
            literal = str(path).replace("'", "''")
            script = f"(Get-Acl -LiteralPath '{literal}').GetSecurityDescriptorSddlForm('Owner,Group,Access')"
            return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                  capture_output=True, check=True, text=True).stdout.strip()

        initial_acl = sddl(target)
        subprocess.run(["icacls", str(target), "/grant", "*S-1-1-0:(R)"],
                       capture_output=True, check=True)
        before_acl = sddl(target)
        self.assertNotEqual(before_acl, initial_acl)
        self.assertIn(";;;WD)", before_acl)  # explicit Everyone ACE, not inherited
        before_index = (self.root / self._git("rev-parse", "--git-path", "index")).read_bytes()
        before_head = self._git("rev-parse", "HEAD")
        approvals = []

        result = merge.execute_selected_merge(
            self.root, self.manifest,
            content_resolver=lambda _ref: b"after\n",
            trusted_host_approval_consumer=lambda request: approvals.append(request) or True,
        )

        self.assertEqual(result.status, "applied", result)
        self.assertIsNone(result.error_code)
        self.assertEqual(result.written_paths, ("agent-core/sample.py",))
        self.assertEqual(result.pending_paths, ())
        self.assertEqual(target.read_bytes(), b"after\n")
        self.assertEqual(sddl(target), before_acl)
        self.assertEqual((self.root / self._git("rev-parse", "--git-path", "index")).read_bytes(), before_index)
        self.assertEqual(self._git("rev-parse", "HEAD"), before_head)
        self.assertEqual(len(approvals), 1)
        self.assertFalse(result.production_ready)

    @unittest.skipUnless(os.name == "nt", "ReplaceFileW regression requires Windows")
    def test_windows_replace_failure_reports_uncertain_and_retains_prepared_file(self):
        target = self.root / "agent-core/sample.py"
        with mock.patch.object(merge, "_windows_replace_file", side_effect=OSError(1176, "injected failure")):
            result = merge.execute_selected_merge(
                self.root, self.manifest, content_resolver=lambda _ref: b"after\n",
                trusted_host_approval_consumer=lambda request: True,
            )
        self.assertEqual(result.status, "partial_failure")
        self.assertEqual(result.error_code, "windows_replace_result_uncertain")
        self.assertEqual(result.native_error_code, 1176)
        self.assertEqual(result.written_paths, ())
        self.assertEqual(result.pending_paths, ("agent-core/sample.py",))
        self.assertEqual(result.manual_review_paths, ("agent-core/sample.py",))
        self.assertEqual(target.read_bytes(), b"before\n")
        self.assertEqual(len(result.recovery_paths), 1)
        self.assertEqual(Path(result.recovery_paths[0]).read_bytes(), b"after\n")

    @unittest.skipUnless(os.name == "nt", "ReplaceFileW regression requires Windows")
    def test_windows_uncertain_after_real_replace_retains_original_backup(self):
        original = merge._windows_replace_file
        def replace_then_report_failure(target, replacement, backup):
            original(target, replacement, backup)
            raise OSError(1177, "injected ambiguous result")
        with mock.patch.object(merge, "_windows_replace_file", side_effect=replace_then_report_failure):
            result = merge.execute_selected_merge(
                self.root, self.manifest, content_resolver=lambda _ref: b"after\n",
                trusted_host_approval_consumer=lambda request: True,
            )
        self.assertEqual(result.status, "partial_failure")
        self.assertEqual(result.error_code, "windows_replace_result_uncertain")
        self.assertEqual(result.native_error_code, 1177)
        self.assertEqual(result.written_paths, ("agent-core/sample.py",))
        self.assertEqual(result.manual_review_paths, ("agent-core/sample.py",))
        self.assertEqual((self.root / "agent-core/sample.py").read_bytes(), b"after\n")
        self.assertEqual(len(result.recovery_paths), 1)
        self.assertTrue(result.recovery_paths[0].endswith(".backup"))
        self.assertEqual(Path(result.recovery_paths[0]).read_bytes(), b"before\n")

    @unittest.skipUnless(os.name == "nt", "ReplaceFileW regression requires Windows")
    def test_windows_acl_restore_failure_keeps_backup_and_does_not_report_success(self):
        with mock.patch.object(merge, "_windows_restore_dacl", side_effect=merge.MergeSafetyError("windows_acl_restore_failed", "injected")):
            result = merge.execute_selected_merge(
                self.root, self.manifest, content_resolver=lambda _ref: b"after\n",
                trusted_host_approval_consumer=lambda request: True,
            )
        self.assertEqual(result.status, "partial_failure")
        self.assertEqual(result.error_code, "windows_acl_restore_failed")
        self.assertEqual(result.written_paths, ("agent-core/sample.py",))
        self.assertEqual(result.manual_review_paths, ("agent-core/sample.py",))
        self.assertEqual(len(result.recovery_paths), 1)
        self.assertEqual(Path(result.recovery_paths[0]).read_bytes(), b"before\n")

    @unittest.skipUnless(os.name == "nt", "Windows ACL regression requires Windows")
    def test_windows_batch_applies_add_and_modify_after_approval(self):
        new_bytes = b"new content\n"
        added = {"path": "agent-core/a-new.py", "action": "add",
                 "beforeSha256": None, "afterSha256": hashlib.sha256(new_bytes).hexdigest(),
                 "beforeMode": None, "afterMode": "100644", "beforeSize": None,
                 "afterSize": len(new_bytes), "beforeOid": None,
                 "afterOid": self._blob_oid(new_bytes), "contentRef": "new-blob"}
        approvals = []
        result = merge.execute_selected_merge(
            self.root, [added, self.manifest[0]],
            content_resolver=lambda ref: {"new-blob": new_bytes, "candidate-blob-1": b"after\n"}[ref],
            trusted_host_approval_consumer=lambda request: approvals.append(request) or True,
        )
        self.assertEqual(result.status, "applied", result)
        self.assertEqual(result.written_paths, ("agent-core/a-new.py", "agent-core/sample.py"))
        self.assertEqual(result.pending_paths, ())
        self.assertEqual(len(approvals), 1)
        self.assertEqual((self.root / "agent-core/a-new.py").read_bytes(), new_bytes)
        self.assertEqual((self.root / "agent-core/sample.py").read_bytes(), b"after\n")

    @unittest.skipUnless(os.name == "nt", "Windows ACL regression requires Windows")
    def test_windows_internal_modify_uses_replace_file(self):
        binding = merge._parse_bindings(self.manifest)[0]
        target = self.root / "agent-core/sample.py"
        merge._apply_one(self.root, binding, b"after\n")
        self.assertEqual(target.read_bytes(), b"after\n")
        self.assertEqual(list((self.root / "agent-core").glob(".self-evolution-apply-*.tmp")), [])

    def test_explicit_host_one_shot_applies_only_selected_bytes_without_touching_git(self):
        target = self.root / "agent-core" / "sample.py"
        before_head = self._git("rev-parse", "HEAD")
        index_path = self.root / self._git("rev-parse", "--git-path", "index")
        before_index = index_path.read_bytes()
        before_security = merge._windows_security_descriptor(target) if os.name == "nt" else None
        confirmations = []

        def consume_once(request):
            confirmations.append(request)
            return request["selectedPaths"] == ("agent-core/sample.py",) and request["files"][0]["afterSha256"] == hashlib.sha256(b"after\n").hexdigest()

        result = merge.execute_selected_merge(
            self.root, self.manifest,
            content_resolver=lambda ref: b"after\n" if ref == "candidate-blob-1" else None,
            trusted_host_approval_consumer=consume_once,
        )

        self.assertEqual(result.status, "applied")
        self.assertEqual(result.written_paths, ("agent-core/sample.py",))
        self.assertEqual(target.read_bytes(), b"after\n")
        if os.name == "nt":
            self.assertEqual(merge._windows_security_descriptor(target), before_security)
        self.assertEqual((self.root / "agent-core/other.py").read_bytes(), b"other\n")
        self.assertEqual(self._git("rev-parse", "HEAD"), before_head)
        self.assertEqual(index_path.read_bytes(), before_index)
        self.assertEqual(len(confirmations), 1)
        self.assertFalse(result.global_atomic)
        self.assertIn("external_race_window", result.limitations)

    def test_approval_callback_drift_is_rejected_before_any_write(self):
        if os.name == "nt":
            target = self.root / "agent-core/new.py"
            selected = [{"path": "agent-core/new.py", "action": "add",
                         "beforeSha256": None, "afterSha256": hashlib.sha256(b"after\n").hexdigest(),
                         "beforeMode": None, "afterMode": "100644", "beforeSize": None,
                         "afterSize": 6, "beforeOid": None, "afterOid": self._blob_oid(b"after\n"),
                         "contentRef": "candidate-blob-1"}]
        else:
            target = self.root / "agent-core/sample.py"
            selected = self.manifest

        def change_during_confirmation(request):
            self.assertEqual(request["selectedPaths"], (selected[0]["path"],))
            target.write_bytes(b"concurrent user edit\n")
            return True

        result = merge.execute_selected_merge(
            self.root, selected,
            content_resolver=lambda _ref: b"after\n",
            trusted_host_approval_consumer=change_during_confirmation,
        )

        self.assertEqual(result.status, "stale_after_approval")
        self.assertEqual(result.written_paths, ())
        self.assertEqual(result.pending_paths, (selected[0]["path"],))
        self.assertEqual(target.read_bytes(), b"concurrent user edit\n")

    def test_external_change_between_file_writes_returns_partial_and_manual_review(self):
        second = self.root / "agent-core/z-last.py"
        second.write_bytes(b"second before\n")
        self._git("add", "agent-core/z-last.py")
        self._git("commit", "--quiet", "-m", "second")
        specs = [self.manifest[0], {
            "path": "agent-core/z-last.py", "action": "modify",
            "beforeSha256": hashlib.sha256(b"second before\n").hexdigest(),
            "afterSha256": hashlib.sha256(b"second after\n").hexdigest(),
            "beforeMode": "100644", "afterMode": "100644",
            "beforeSize": 14, "afterSize": 13,
            "beforeOid": self._git("hash-object", "agent-core/z-last.py"),
            "afterOid": self._blob_oid(b"second after\n"), "contentRef": "second-blob",
        }]
        original = merge._apply_one
        calls = []

        def edit_after_first(root, binding, content):
            original(root, binding, content)
            calls.append(binding.path)
            if len(calls) == 1:
                second.write_bytes(b"outside edit\n")

        with mock.patch.object(merge, "_apply_one", side_effect=edit_after_first):
            result = merge.execute_selected_merge(
                self.root, specs,
                content_resolver=lambda ref: {"candidate-blob-1": b"after\n", "second-blob": b"second after\n"}[ref],
                trusted_host_approval_consumer=lambda request: True,
            )

        self.assertEqual(result.status, "partial_failure")
        self.assertEqual(result.written_paths, ("agent-core/sample.py",))
        self.assertEqual(result.pending_paths, ("agent-core/z-last.py",))
        self.assertEqual(result.manual_review_paths, ("agent-core/z-last.py",))
        self.assertEqual(result.error_code, "selected_file_state_changed")
        self.assertEqual(second.read_bytes(), b"outside edit\n")

    def test_add_and_delete_apply_only_selected_files(self):
        remove_path = "agent-core/other.py"
        remove_bytes = b"other\n"
        add_bytes = b"new selected file\n"
        specs = [
            {"path": "agent-core/new.py", "action": "add",
             "beforeSha256": None, "afterSha256": hashlib.sha256(add_bytes).hexdigest(),
             "beforeMode": None, "afterMode": "100644", "beforeSize": None,
             "afterSize": len(add_bytes), "beforeOid": None,
             "afterOid": self._blob_oid(add_bytes), "contentRef": "new-blob"},
            {"path": remove_path, "action": "delete",
             "beforeSha256": hashlib.sha256(remove_bytes).hexdigest(), "afterSha256": None,
             "beforeMode": "100644", "afterMode": None, "beforeSize": len(remove_bytes),
             "afterSize": None, "beforeOid": self._git("rev-parse", "HEAD:agent-core/other.py"),
             "afterOid": None, "contentRef": None},
        ]
        result = merge.execute_selected_merge(
            self.root, specs, content_resolver=lambda ref: add_bytes,
            trusted_host_approval_consumer=lambda request: request["selectedPaths"] == ("agent-core/new.py", remove_path),
        )
        self.assertEqual(result.status, "applied")
        self.assertEqual((self.root / "agent-core/new.py").read_bytes(), add_bytes)
        self.assertFalse((self.root / remove_path).exists())
        self.assertEqual((self.root / "agent-core/sample.py").read_bytes(), b"before\n")

    def test_partial_failure_reports_written_and_pending_without_claiming_atomicity(self):
        second = self.root / "agent-core/z-last.py"
        second.write_bytes(b"second before\n")
        self._git("add", "agent-core/z-last.py")
        self._git("commit", "--quiet", "-m", "second")
        specs = [self.manifest[0], {
            "path": "agent-core/z-last.py", "action": "modify",
            "beforeSha256": hashlib.sha256(b"second before\n").hexdigest(),
            "afterSha256": hashlib.sha256(b"second after\n").hexdigest(),
            "beforeMode": "100644", "afterMode": "100644",
            "beforeSize": 14, "afterSize": 13,
            "beforeOid": self._git("hash-object", "agent-core/z-last.py"),
            "afterOid": self._blob_oid(b"second after\n"), "contentRef": "second-blob",
        }]
        original = merge._apply_one
        calls = []

        def fail_second(root, binding, content):
            calls.append(binding.path)
            if len(calls) == 2:
                raise OSError("controlled fixture failure")
            return original(root, binding, content)

        with mock.patch.object(merge, "_apply_one", side_effect=fail_second):
            result = merge.execute_selected_merge(
                self.root, specs,
                content_resolver=lambda ref: {"candidate-blob-1": b"after\n", "second-blob": b"second after\n"}[ref],
                trusted_host_approval_consumer=lambda request: True,
            )

        self.assertEqual(result.status, "partial_failure", result)
        self.assertEqual(result.written_paths, ("agent-core/sample.py",))
        self.assertEqual(result.pending_paths, ("agent-core/z-last.py",))
        self.assertEqual((self.root / "agent-core/sample.py").read_bytes(), b"after\n")
        self.assertEqual(second.read_bytes(), b"second before\n")
        self.assertFalse(result.global_atomic)

    def test_execution_fails_closed_even_for_accepted_looking_untrusted_receipt(self):
        before_head = self._git("rev-parse", "HEAD")
        before_index = self._git("rev-parse", "--git-path", "index")
        index_path = Path(before_index)
        if not index_path.is_absolute():
            index_path = self.root / index_path
        before_index_bytes = index_path.read_bytes()
        before_content = (self.root / "agent-core" / "sample.py").read_bytes()
        receipt = {"decision": "accepted", "issuer": "electron-main", "nonce": "looks-valid"}

        with self.assertRaises(merge.MergeSafetyError) as caught:
            merge.execute_selected_merge(self.root, self.manifest, content_resolver=lambda _ref: b"after\n", approval_receipt=receipt)

        self.assertEqual(caught.exception.code, "untrusted_approval_receipt")
        self.assertEqual(self._git("rev-parse", "HEAD"), before_head)
        self.assertEqual(index_path.read_bytes(), before_index_bytes)
        self.assertEqual((self.root / "agent-core" / "sample.py").read_bytes(), before_content)


if __name__ == "__main__":
    unittest.main()
