from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest.mock as mock
import unittest
from dataclasses import replace
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

import self_evolution_manifest as manifest  # noqa: E402


class ReviewManifestSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "candidate"
        self.root.mkdir()
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text("private fixture contents", encoding="utf-8")
        self.captured = manifest.capture_tree_manifest(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def binding(self):
        return manifest.create_review_manifest_binding(
            session_id="session-a", task_id="task-a", attempt_id="attempt-a",
            candidate_id="candidate-a", manifest=self.captured,
        )

    def project(self, binding, *, session="session-a", task="task-a",
                attempt="attempt-a", candidate="candidate-a", snapshot=None):
        return manifest.project_review_file_manifest(
            binding, session_id=session, task_id=task, attempt_id=attempt,
            candidate_id=candidate, manifest=snapshot or self.captured,
        )

    def test_current_capture_allows_full_tree_metadata_projection_without_disclosure(self) -> None:
        binding = self.binding()
        result = self.project(binding)

        self.assertTrue(self.captured.sweep_coverage_complete)
        self.assertTrue(self.captured.full_candidate_coverage)
        self.assertFalse(self.captured.snapshot_coverage_complete)
        self.assertFalse(self.captured.host_coverage_claim_permitted)
        self.assertNotIn(str(self.root), repr(result))
        self.assertNotIn("private fixture contents", repr(result))
        self.assertFalse(result["merge_authorized"])

    def test_real_temp_tree_projects_only_safe_relative_metadata(self) -> None:
        binding = self.binding()
        result = self.project(binding)

        self.assertEqual(result["files"], [{"path": "src/main.py", "kind": "file",
                                             "size": len(b"private fixture contents"),
                                             "sha256": hashlib.sha256(
                                                 b"private fixture contents").hexdigest()}])
        self.assertNotIn(str(self.root), repr(result))
        self.assertNotIn("private fixture contents", repr(result))
        self.assertNotIn("session_id", result)
        self.assertFalse(result["merge_authorized"])
        self.assertTrue(result["sweep_coverage_complete"])
        self.assertFalse(result["snapshot_coverage_complete"])
        self.assertTrue(result["full_candidate_coverage"])
        self.assertFalse(result["host_coverage_claim_permitted"])

    def test_content_diff_is_explicitly_refused_without_reopening_or_emitting_candidate_bytes(self) -> None:
        binding = self.binding()

        with mock.patch.object(manifest, "capture_tree_manifest", side_effect=AssertionError("must not reread")):
            with self.assertRaisesRegex(manifest.ManifestError, "^review_content_diff_not_supported$") as caught:
                manifest.project_review_content_diff(
                    binding, session_id="session-a", task_id="task-a", attempt_id="attempt-a",
                    candidate_id="candidate-a", manifest=self.captured,
                )
        self.assertNotIn("private fixture contents", str(caught.exception))
        self.assertNotIn(str(self.root), str(caught.exception))

    def test_content_diff_refuses_incomplete_tree_when_git_metadata_is_excluded(self) -> None:
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
        incomplete = manifest.capture_tree_manifest(self.root)
        binding = manifest.create_review_manifest_binding(
            session_id="session-a", task_id="task-a", attempt_id="attempt-a",
            candidate_id="candidate-a", manifest=incomplete,
        )

        with self.assertRaisesRegex(manifest.ManifestError, "^review_manifest_coverage_incomplete$"):
            manifest.project_review_content_diff(
                binding, session_id="session-a", task_id="task-a", attempt_id="attempt-a",
                candidate_id="candidate-a", manifest=incomplete,
            )

    def test_content_diff_refusal_still_rejects_identity_replay(self) -> None:
        binding = self.binding()

        with self.assertRaisesRegex(manifest.ManifestError, "^review_binding_mismatch$"):
            manifest.project_review_content_diff(
                binding, session_id="session-b", task_id="task-a", attempt_id="attempt-a",
                candidate_id="candidate-a", manifest=self.captured,
            )

    def test_review_projection_enforces_per_file_and_manifest_byte_bounds(self) -> None:
        oversized = manifest.TreeEntry(
            "src/main.py", "file", 0, manifest.MAX_FILE_BYTES + 1, "a" * 64, 1, 10, 1)
        captured = manifest.TreeManifest(
            root=str(self.root), root_identity=(10, 20), entries=(oversized,), digest="",
            entry_count=1, file_bytes=manifest.MAX_FILE_BYTES + 1, excluded_metadata=(),
            sweep_coverage_complete=True, full_candidate_coverage=True,
        )
        captured = replace(captured, digest=manifest._computed_manifest_digest(captured))
        captured = replace(captured, _capture_seal=manifest._manifest_capture_seal(captured))
        binding = manifest.create_review_manifest_binding(
            session_id="session-a", task_id="task-a", attempt_id="attempt-a",
            candidate_id="candidate-a", manifest=captured,
        )

        with self.assertRaisesRegex(manifest.ManifestError, "^review_manifest_bounds_invalid$"):
            manifest.project_review_file_manifest(
                binding, session_id="session-a", task_id="task-a", attempt_id="attempt-a",
                candidate_id="candidate-a", manifest=captured,
            )

    def test_review_projection_rejects_control_characters_in_captured_paths(self) -> None:
        malformed = manifest.TreeEntry("src/bad\u200b.py", "file", 0, 1, "a" * 64, 1, 10, 1)
        captured = manifest.TreeManifest(
            root=str(self.root), root_identity=(10, 20), entries=(malformed,), digest="",
            entry_count=1, file_bytes=1, excluded_metadata=(),
            sweep_coverage_complete=True, full_candidate_coverage=True,
        )
        captured = replace(captured, digest=manifest._computed_manifest_digest(captured))
        captured = replace(captured, _capture_seal=manifest._manifest_capture_seal(captured))
        binding = manifest.create_review_manifest_binding(
            session_id="session-a", task_id="task-a", attempt_id="attempt-a",
            candidate_id="candidate-a", manifest=captured,
        )

        with self.assertRaisesRegex(manifest.ManifestError, "^review_manifest_entry_invalid$"):
            manifest.project_review_file_manifest(
                binding, session_id="session-a", task_id="task-a", attempt_id="attempt-a",
                candidate_id="candidate-a", manifest=captured,
            )

    def test_binding_rejects_cross_session_or_candidate_replay(self) -> None:
        binding = self.binding()
        for changes in ({"session": "session-b"}, {"candidate": "candidate-b"},
                        {"task": "task-b"}, {"attempt": "attempt-b"}):
            with self.subTest(changes=changes), self.assertRaises(manifest.ManifestError):
                self.project(
                    binding, session=changes.get("session", "session-a"),
                    task=changes.get("task", "task-a"),
                    attempt=changes.get("attempt", "attempt-a"),
                    candidate=changes.get("candidate", "candidate-a"),
                )

    def test_binding_rejects_path_shaped_identity_values(self) -> None:
        for value in (str(self.root), r"C:\secret", "session/../other"):
            with self.subTest(value=value), self.assertRaisesRegex(
                    manifest.ManifestError, "^invalid_review_session_id$"):
                manifest.create_review_manifest_binding(
                    session_id=value, task_id="task-a", attempt_id="attempt-a",
                    candidate_id="candidate-a", manifest=self.captured,
                )

    def test_binding_tampering_is_rejected_and_root_is_not_represented(self) -> None:
        binding = self.binding()
        self.assertNotIn(str(self.root), repr(binding))
        self.assertNotIn(str(self.root), repr(self.captured))
        tampered = replace(binding, candidate_digest="f" * 64)
        with self.assertRaisesRegex(manifest.ManifestError, "^review_binding_integrity_check_failed$"):
            self.project(tampered)

    def test_manifest_metadata_and_digest_tampering_are_rejected(self) -> None:
        binding = self.binding()
        changed_count = replace(self.captured, entry_count=self.captured.entry_count + 1)
        changed_digest = replace(self.captured, digest="f" * 64)
        for changed in (changed_count, changed_digest):
            with self.subTest(changed=changed.digest), self.assertRaisesRegex(
                    manifest.ManifestError, "^manifest_integrity_check_failed$"):
                self.project(binding, snapshot=changed)

    def test_process_seal_rejects_claims_of_atomic_snapshot_or_host_authority(self) -> None:
        captured = self.captured
        for marker in ("snapshot_coverage_complete", "host_coverage_claim_permitted"):
            forged = replace(captured, **{marker: True})
            forged = replace(forged, digest=manifest._computed_manifest_digest(forged))
            forged = replace(forged, _capture_seal=manifest._manifest_capture_seal(forged))
            with self.subTest(marker=marker), self.assertRaisesRegex(
                    manifest.ManifestError, "^manifest_integrity_check_failed$"):
                manifest.create_review_manifest_binding(
                    session_id="session-a", task_id="task-a", attempt_id="attempt-a",
                    candidate_id="candidate-a", manifest=forged,
                )

    def test_untrusted_manifest_cannot_escalate_coverage_flags(self) -> None:
        forged = replace(
            self.captured, snapshot_coverage_complete=True,
            full_candidate_coverage=True, host_coverage_claim_permitted=True,
        )
        with self.assertRaisesRegex(manifest.ManifestError, "^manifest_integrity_check_failed$"):
            manifest.create_review_manifest_binding(
                session_id="session-a", task_id="task-a", attempt_id="attempt-a",
                candidate_id="candidate-a", manifest=forged,
            )

    def test_projection_rereads_bound_root_before_refusing_incomplete_coverage(self) -> None:
        binding = self.binding()
        (self.root / "src" / "main.py").write_text("drift", encoding="utf-8")

        with self.assertRaisesRegex(manifest.ManifestError, "^review_candidate_changed_since_capture$"):
            self.project(binding)

    def test_recapture_rejects_candidate_content_drift(self) -> None:
        binding = self.binding()
        current_file = self.root / "src" / "main.py"
        current_file.write_text("changed after review", encoding="utf-8")
        current = manifest.capture_tree_manifest(self.root)

        with self.assertRaisesRegex(manifest.ManifestError, "^review_candidate_changed_since_capture$"):
            manifest._verify_review_recapture(binding, self.captured, current)

    def test_recapture_rejects_candidate_root_replacement(self) -> None:
        binding = self.binding()
        moved = self.root.with_name("candidate-old")
        self.root.rename(moved)
        self.root.mkdir()
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text("private fixture contents", encoding="utf-8")
        current = manifest.capture_tree_manifest(self.root)

        with self.assertRaisesRegex(manifest.ManifestError, "^review_candidate_changed_since_capture$"):
            manifest._verify_review_recapture(binding, self.captured, current)

    def test_key_material_directories_are_rejected_case_insensitively(self) -> None:
        paths = (
            "keys/signing", "KEYS/signing", "sub/key/signing", "signing/material",
            "keystore/store", "key-store/store", "key_store/store", "keyring/store",
            "certs/material", "certificates/material", "pki/material", "tls/material",
            "ssl/material", ".gnupg/material", "gpg/material",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue(manifest._review_path_is_sensitive(path))
        for path in ("src/monkey.py", "docs/keyboard.md", "src/signing.py"):
            with self.subTest(path=path):
                self.assertFalse(manifest._review_path_is_sensitive(path))

    def test_real_candidate_with_extensionless_key_is_denied_without_path_disclosure(self) -> None:
        key_dir = self.root / "keys"
        key_dir.mkdir()
        (key_dir / "signing").write_text("private synthetic key material", encoding="utf-8")
        captured = manifest.capture_tree_manifest(self.root)
        binding = manifest.create_review_manifest_binding(
            session_id="session-a", task_id="task-a", attempt_id="attempt-a",
            candidate_id="candidate-a", manifest=captured,
        )

        with self.assertRaisesRegex(manifest.ManifestError, "^review_manifest_sensitive_path_detected$") as caught:
            manifest.project_review_file_manifest(
                binding, session_id="session-a", task_id="task-a", attempt_id="attempt-a",
                candidate_id="candidate-a", manifest=captured,
            )
        self.assertNotIn("keys/signing", str(caught.exception))
        self.assertNotIn("private synthetic key material", str(caught.exception))
        self.assertNotIn(str(self.root), str(caught.exception))

    def test_sensitive_path_policy_rejects_secrets_and_configuration_files(self) -> None:
        for path in (".env", ".envrc", "keys/private.pem", "config/app.json", ".ssh/id_ed25519", "app-settings.yaml"):
            with self.subTest(path=path):
                self.assertTrue(manifest._review_path_is_sensitive(path))
        self.assertFalse(manifest._review_path_is_sensitive("src/main.py"))

    def test_content_projection_unavailable_even_with_caller_asserted_intent(self) -> None:
        binding = self.binding()
        with mock.patch.object(manifest, "_read_review_file_from_bound_root",
                               side_effect=AssertionError("must not read")):
            with self.assertRaisesRegex(manifest.ManifestError, "^review_content_disclosure_unavailable$"):
                manifest.project_review_file_contents(
                    binding, session_id="session-a", task_id="task-a", attempt_id="attempt-a",
                    candidate_id="candidate-a", manifest=self.captured,
                    host_selected_paths=["src/main.py"], user_requested=True,
                )

    def test_all_content_projection_inputs_fail_closed_before_reading(self) -> None:
        binding = self.binding()
        cases = (
            (False, "session-a", ["src/main.py"]),
            (True, "session-a", ["src/main.py"]),
            (True, "session-b", ["src/main.py"]),
            (True, "session-a", ["../secret"]),
            (True, "session-a", [".env"]),
            (True, "session-a", ["src/main.py", "src/MAIN.py"]),
        )
        with mock.patch.object(manifest, "_read_review_file_from_bound_root",
                               side_effect=AssertionError("must not read")):
            for intent, session, paths in cases:
                with self.subTest(intent=intent, session=session, paths=paths):
                    with self.assertRaisesRegex(manifest.ManifestError, "^review_content_disclosure_unavailable$"):
                        manifest.project_review_file_contents(
                            binding, session_id=session, task_id="task-a", attempt_id="attempt-a",
                            candidate_id="candidate-a", manifest=self.captured,
                            host_selected_paths=paths, user_requested=intent,
                        )

    @unittest.skipUnless(__import__("os").name == "nt", "Windows handle reader")
    def test_internal_handle_reader_binds_bytes_and_rejects_drift(self) -> None:
        entry = next(e for e in self.captured.entries if e.path == "src/main.py")
        content = manifest._read_review_file_from_bound_root(
            str(self.root), self.captured.root_identity, entry, 256 * 1024)
        self.assertEqual(content, b"private fixture contents")
        (self.root / "src" / "main.py").write_bytes(b"changed after capture")
        with self.assertRaises(manifest.ManifestError):
            manifest._read_review_file_from_bound_root(
                str(self.root), self.captured.root_identity, entry, 256 * 1024)


if __name__ == "__main__":
    unittest.main()
