from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import self_evolution_snapshot_review as review


@unittest.skipUnless(os.name == "nt", "requires Windows handle-based manifest")
class SnapshotReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "candidate"
        self.root.mkdir()
        (self.root / "modify.py").write_text("value = 1\n", encoding="utf-8")
        (self.root / "delete.py").write_text("removed = True\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def freeze(self, paths=("modify.py", "delete.py", "added.py")):
        return review.freeze_baseline(candidate_root=self.root, candidate_id="candidate-1",
                                      paths=paths)

    def test_uncommitted_modified_added_deleted_files_compare_frozen_bytes(self):
        baseline = self.freeze()
        (self.root / "modify.py").write_text("value = 2\n", encoding="utf-8")
        (self.root / "delete.py").unlink()
        (self.root / "added.py").write_text("new_value = 3\n", encoding="utf-8")
        result = review.review_candidate(baseline, candidate_id="candidate-1",
                                         disclose_paths=("modify.py", "added.py", "delete.py"))
        changes = {row["path"]: row for row in result["files"]}
        self.assertEqual(changes["modify.py"]["change"], "modified")
        self.assertIn("-value = 1", changes["modify.py"]["diff"])
        self.assertIn("+value = 2", changes["modify.py"]["diff"])
        self.assertEqual(changes["added.py"]["change"], "added")
        self.assertEqual(changes["delete.py"]["change"], "deleted")
        self.assertFalse(result["full_candidate_coverage"])
        self.assertFalse(result["merge_authorized"])
        self.assertNotIn(str(self.root), repr(result))

    def test_default_metadata_only_and_binary_never_disclosed(self):
        baseline = self.freeze(("modify.py",))
        (self.root / "modify.py").write_bytes(b"\0hidden binary data")
        result = review.review_candidate(baseline, candidate_id="candidate-1")
        self.assertEqual(result["files"][0]["content_kind"], "binary_or_unsupported")
        self.assertNotIn("diff", result["files"][0])
        self.assertNotIn("hidden binary data", repr(result))

    def test_text_default_does_not_disclose(self):
        baseline = self.freeze(("modify.py",))
        (self.root / "modify.py").write_text("value = 2\n", encoding="utf-8")
        result = review.review_candidate(baseline, candidate_id="candidate-1")
        self.assertNotIn("diff", result["files"][0])
        self.assertFalse(result["content_complete"])

    def test_baseline_is_frozen_not_reread_after_edit(self):
        baseline = self.freeze(("modify.py",))
        (self.root / "modify.py").write_text("value = 44\n", encoding="utf-8")
        result = review.review_candidate(baseline, candidate_id="candidate-1", disclose_paths=("modify.py",))
        self.assertIn("-value = 1", result["files"][0]["diff"])
        self.assertIn("+value = 44", result["files"][0]["diff"])

    def test_candidate_mutation_during_review_is_rejected(self):
        baseline = self.freeze(("modify.py",))
        target = self.root / "modify.py"
        target.write_text("value = 2\n", encoding="utf-8")
        original = review._read_bound_file
        def mutate_after_read(*args, **kwargs):
            result = original(*args, **kwargs)
            target.write_text("value = 3\n", encoding="utf-8")
            return result
        with mock.patch.object(review, "_read_bound_file", side_effect=mutate_after_read):
            with self.assertRaises(review.ReviewError) as caught:
                review.review_candidate(baseline, candidate_id="candidate-1", disclose_paths=("modify.py",))
        self.assertEqual(caught.exception.code, "candidate_changed_during_review")

    def test_mutation_while_formatting_diff_is_rejected(self):
        baseline = self.freeze(("modify.py",))
        target = self.root / "modify.py"
        target.write_text("value = 2\n", encoding="utf-8")
        original = review.difflib.unified_diff
        def mutate_during_diff(*args, **kwargs):
            target.write_text("value = 3\n", encoding="utf-8")
            return original(*args, **kwargs)
        with mock.patch.object(review.difflib, "unified_diff", side_effect=mutate_during_diff):
            with self.assertRaises(review.ReviewError) as caught:
                review.review_candidate(baseline, candidate_id="candidate-1", disclose_paths=("modify.py",))
        self.assertEqual(caught.exception.code, "candidate_changed_during_review")

    def test_mutation_during_baseline_freeze_is_rejected(self):
        target = self.root / "modify.py"
        original = review._read_bound_file
        def mutate_after_read(*args, **kwargs):
            result = original(*args, **kwargs)
            target.write_text("racing = True\n", encoding="utf-8")
            return result
        with mock.patch.object(review, "_read_bound_file", side_effect=mutate_after_read):
            with self.assertRaises(review.ReviewError) as caught:
                self.freeze(("modify.py",))
        self.assertEqual(caught.exception.code, "candidate_changed_during_freeze")

    def test_hardlink_candidate_is_denied(self):
        baseline = self.freeze(("modify.py",))
        outside = Path(self.temp.name) / "outside"
        outside.write_text("outside content", encoding="utf-8")
        (self.root / "modify.py").unlink()
        try:
            (self.root / "modify.py").hardlink_to(outside)
        except OSError as exc:
            self.skipTest(f"hard links unavailable: {exc}")
        with self.assertRaises(review.ReviewError):
            review.review_candidate(baseline, candidate_id="candidate-1")

    def test_symlink_candidate_is_denied_when_available(self):
        baseline = self.freeze(("modify.py",))
        outside = Path(self.temp.name) / "outside"
        outside.write_text("outside content", encoding="utf-8")
        target = self.root / "modify.py"
        target.unlink()
        try:
            target.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaises(review.ReviewError):
            review.review_candidate(baseline, candidate_id="candidate-1")

    def test_replaced_candidate_root_is_denied(self):
        baseline = self.freeze(("modify.py",))
        self.root.rename(Path(self.temp.name) / "previous")
        self.root.mkdir()
        (self.root / "modify.py").write_text("value = 1\n", encoding="utf-8")
        with self.assertRaises(review.ReviewError) as caught:
            review.review_candidate(baseline, candidate_id="candidate-1")
        self.assertEqual(caught.exception.code, "candidate_identity_changed")

    def test_candidate_identity_and_traversal_refused(self):
        baseline = self.freeze(("modify.py",))
        with self.assertRaises(review.ReviewError):
            review.review_candidate(baseline, candidate_id="other")
        with self.assertRaises(review.ReviewError):
            self.freeze(("../outside",))

    def test_same_bytes_replaced_file_is_identity_change(self):
        baseline = self.freeze(("modify.py",))
        path = self.root / "modify.py"
        old = self.root / "old.py"
        path.rename(old)
        path.write_text("value = 1\n", encoding="utf-8")
        old.unlink()
        result = review.review_candidate(baseline, candidate_id="candidate-1")
        self.assertEqual(result["files"][0]["change"], "replaced")
        self.assertEqual(result["files"][0]["before_sha256"], result["files"][0]["after_sha256"])
        self.assertNotEqual(result["files"][0]["before_file_identity"], result["files"][0]["after_file_identity"])

    def test_case_alias_does_not_look_like_an_absent_file(self):
        with self.assertRaises(review.ReviewError) as caught:
            self.freeze(("MODIFY.py",))
        self.assertEqual(caught.exception.code, "selected_path_alias")

    def test_frozen_baseline_tampering_is_refused(self):
        baseline = self.freeze(("modify.py",))
        altered_file = replace(baseline.files[0], content=b"forged")
        tampered = replace(baseline, files=(altered_file,))
        with self.assertRaises(review.ReviewError) as caught:
            review.review_candidate(tampered, candidate_id="candidate-1")
        self.assertEqual(caught.exception.code, "baseline_integrity_failed")

    def test_unselected_change_is_not_reported_as_full_coverage(self):
        baseline = self.freeze(("modify.py",))
        (self.root / "delete.py").write_text("unselected change", encoding="utf-8")
        result = review.review_candidate(baseline, candidate_id="candidate-1")
        self.assertEqual(result["files"], [])
        self.assertFalse(result["full_candidate_coverage"])
        self.assertEqual(result["scope"], "explicit_paths_point_in_time_not_atomic")

    def test_oversized_text_change_is_metadata_only_even_when_authorized(self):
        baseline = self.freeze(("modify.py",))
        (self.root / "modify.py").write_bytes(b"A" * (review._MAX_FILE_BYTES + 1))
        result = review.review_candidate(baseline, candidate_id="candidate-1", disclose_paths=("modify.py",))
        row = result["files"][0]
        self.assertEqual(row["content_kind"], "binary_or_unsupported")
        self.assertEqual(row["after_content_status"], "over_text_limit")
        self.assertNotIn("diff", row)
        self.assertFalse(result["content_complete"])

    def test_truncation_is_explicit(self):
        baseline = self.freeze(("modify.py",))
        (self.root / "modify.py").write_text("value = 2\n" * 80, encoding="utf-8")
        result = review.review_candidate(baseline, candidate_id="candidate-1",
            disclose_paths=("modify.py",), max_diff_bytes=80)
        self.assertTrue(result["truncated"])
        self.assertFalse(result["content_complete"])
        self.assertLessEqual(len(result["files"][0]["diff"].encode()), 80)



    def test_full_tree_review_finds_unknown_nested_add_delete_modify_and_replacement(self):
        nested = self.root / "worker" / "nested"
        nested.mkdir(parents=True)
        (nested / "edited.py").write_text("old = 1\n", encoding="utf-8")
        (nested / "removed.py").write_text("gone = True\n", encoding="utf-8")
        (nested / "replaced.py").write_text("same = True\n", encoding="utf-8")
        baseline = review.freeze_baseline(candidate_root=self.root, candidate_id="candidate-1")
        (nested / "edited.py").write_text("new = 2\n", encoding="utf-8")
        (nested / "removed.py").unlink()
        (nested / "replaced.py").unlink()
        (nested / "replaced.py").write_text("same = True\n", encoding="utf-8")
        surprise = self.root / "worker" / "new-dir" / "unselected.py"
        surprise.parent.mkdir()
        surprise.write_text("surprise = 3\n", encoding="utf-8")
        result = review.review_candidate(baseline, candidate_id="candidate-1",
            disclose_paths=("worker/nested/edited.py", "worker/new-dir/unselected.py"))
        changes = {row["path"]: row for row in result["files"]}
        self.assertEqual(changes["worker/nested/edited.py"]["change"], "modified")
        self.assertIn("-old = 1", changes["worker/nested/edited.py"]["diff"])
        self.assertIn("+new = 2", changes["worker/nested/edited.py"]["diff"])
        self.assertEqual(changes["worker/nested/removed.py"]["change"], "deleted")
        self.assertEqual(changes["worker/nested/replaced.py"]["change"], "replaced")
        self.assertEqual(changes["worker/new-dir/unselected.py"]["change"], "added")
        self.assertIn("+surprise = 3", changes["worker/new-dir/unselected.py"]["diff"])
        self.assertTrue(result["full_candidate_coverage"])
        self.assertFalse(result["snapshot_coverage_complete"])
        self.assertFalse(result["host_coverage_claim_permitted"])
        self.assertFalse(result["merge_authorized"])
        self.assertFalse(result["host_coverage_claim_permitted"])
        self.assertEqual(result["scope"], "bounded_full_tree_point_in_time_not_atomic")

    def test_full_tree_review_refuses_excluded_git_metadata(self):
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("[core]\n", encoding="utf-8")

        with self.assertRaises(review.ReviewError) as caught:
            review.freeze_baseline(candidate_root=self.root, candidate_id="candidate-1")

        self.assertEqual(caught.exception.code, "candidate_capture_scope_unsupported")

    def test_full_tree_scan_blocks_when_file_count_exceeds_review_bound(self):
        baseline = review.freeze_baseline(candidate_root=self.root, candidate_id="candidate-1")
        with mock.patch.object(review, "_MAX_TREE_FILES", 1):
            with self.assertRaises(review.ReviewError) as caught:
                review.freeze_baseline(candidate_root=self.root, candidate_id="candidate-1")
        self.assertEqual(caught.exception.code, "review_file_count_exceeded")
        self.assertEqual(len(baseline.files), 2)

    def test_large_binary_baseline_and_unknown_addition_are_hash_only(self):
        large = self.root / "old.bin"
        large.write_bytes(b"\0OLD" + b"X" * (review._MAX_FILE_BYTES + 1))
        baseline = review.freeze_baseline(candidate_root=self.root, candidate_id="candidate-1")
        frozen = next(item for item in baseline.files if item.path == "old.bin")
        self.assertIsNone(frozen.content)
        self.assertEqual(frozen.content_status, "over_text_limit")
        unchanged = review.review_candidate(baseline, candidate_id="candidate-1")
        self.assertTrue(unchanged["content_complete"])
        large.write_bytes(b"\0NEW" + b"Y" * (review._MAX_FILE_BYTES + 1))
        (self.root / "added.bin").write_bytes(b"\0NEW-ASSET" + b"Z" * (review._MAX_FILE_BYTES + 1))
        result = review.review_candidate(baseline, candidate_id="candidate-1",
                                         disclose_paths=("old.bin", "added.bin"))
        rows = {row["path"]: row for row in result["files"]}
        self.assertEqual(rows["old.bin"]["change"], "modified")
        self.assertEqual(rows["added.bin"]["change"], "added")
        for row in rows.values():
            self.assertNotIn("diff", row)
            self.assertEqual(row["after_content_status"], "over_text_limit")
            self.assertIsNotNone(row["after_sha256"])
        self.assertFalse(result["content_complete"])
        self.assertNotIn("NEW-ASSET", repr(result))

    def test_small_binary_is_not_retained_and_delete_is_incomplete(self):
        (self.root / "asset.bin").write_bytes(b"\0secret")
        baseline = review.freeze_baseline(candidate_root=self.root, candidate_id="candidate-1")
        frozen = next(item for item in baseline.files if item.path == "asset.bin")
        self.assertIsNone(frozen.content)
        self.assertEqual(frozen.content_status, "binary_or_unsupported")
        (self.root / "asset.bin").unlink()
        result = review.review_candidate(baseline, candidate_id="candidate-1", disclose_paths=("asset.bin",))
        row = result["files"][0]
        self.assertEqual(row["change"], "deleted")
        self.assertEqual(row["before_content_status"], "binary_or_unsupported")
        self.assertNotIn("diff", row)
        self.assertFalse(result["content_complete"])

    def test_binary_bytes_do_not_consume_retained_text_budget(self):
        (self.root / "asset.bin").write_bytes(b"\0" * 2048)
        with mock.patch.object(review, "_MAX_TOTAL_BYTES", 100):
            baseline = review.freeze_baseline(candidate_root=self.root, candidate_id="candidate-1")
        self.assertIsNone(next(item for item in baseline.files if item.path == "asset.bin").content)

    def test_more_than_32_mib_binary_is_not_charged_as_text(self):
        asset = self.root / "large.bin"
        with asset.open("wb") as stream:
            block = b"\x00" * (1024 * 1024)
            for _ in range(33):
                stream.write(block)
        baseline = review.freeze_baseline(candidate_root=self.root, candidate_id="candidate-1")
        frozen = next(item for item in baseline.files if item.path == "large.bin")
        self.assertIsNone(frozen.content)
        self.assertEqual(frozen.content_status, "over_text_limit")
        result = review.review_candidate(baseline, candidate_id="candidate-1")
        self.assertTrue(result["content_complete"])
        self.assertEqual(result["files"], [])

    def test_hash_only_file_mutated_between_sweeps_is_blocked(self):
        asset = self.root / "large.bin"
        asset.write_bytes(b"\x00" * (review._MAX_FILE_BYTES + 1))
        original = review._capture
        calls = 0
        def change_after_first_capture(*args):
            nonlocal calls
            captured = original(*args)
            calls += 1
            if calls == 1:
                asset.write_bytes(b"\x01" * (review._MAX_FILE_BYTES + 1))
            return captured
        with mock.patch.object(review, "_capture", side_effect=change_after_first_capture):
            with self.assertRaises(review.ReviewError) as caught:
                review.freeze_baseline(candidate_root=self.root, candidate_id="candidate-1")
        self.assertEqual(caught.exception.code, "candidate_changed_during_freeze")

    def test_large_binary_hash_only_metadata_tampering_is_rejected(self):
        (self.root / "asset.bin").write_bytes(b"\0" * (review._MAX_FILE_BYTES + 1))
        baseline = review.freeze_baseline(candidate_root=self.root, candidate_id="candidate-1")
        files = tuple(replace(f, content_status="text") if f.path == "asset.bin" else f for f in baseline.files)
        with self.assertRaises(review.ReviewError) as caught:
            review.review_candidate(replace(baseline, files=files), candidate_id="candidate-1")
        self.assertEqual(caught.exception.code, "baseline_integrity_failed")

    def test_full_tree_review_marks_binary_content_incomplete_without_disclosing_it(self):
        baseline = review.freeze_baseline(candidate_root=self.root, candidate_id="candidate-1")
        (self.root / "worker.bin").write_bytes(b"\x00secret payload")
        result = review.review_candidate(baseline, candidate_id="candidate-1")
        row = next(row for row in result["files"] if row["path"] == "worker.bin")
        self.assertEqual(row["change"], "added")
        self.assertEqual(row["content_kind"], "binary_or_unsupported")
        self.assertNotIn("diff", row)
        self.assertFalse(result["content_complete"])
        self.assertNotIn("secret payload", repr(result))


    def test_full_tree_scan_blocks_when_total_frozen_bytes_exceed_review_bound(self):
        with mock.patch.object(review, "_MAX_TOTAL_BYTES", 1):
            with self.assertRaises(review.ReviewError) as caught:
                review.freeze_baseline(candidate_root=self.root, candidate_id="candidate-1")
        self.assertEqual(caught.exception.code, "review_total_size_exceeded")


    def test_full_tree_replacement_with_new_bytes_is_labeled_and_reviewable(self):
        baseline = review.freeze_baseline(candidate_root=self.root, candidate_id="candidate-1")
        target = self.root / "modify.py"
        previous = self.root / "old-name.py"
        target.rename(previous)
        target.write_text("value = 99\n", encoding="utf-8")
        previous.unlink()
        result = review.review_candidate(baseline, candidate_id="candidate-1",
                                         disclose_paths=("modify.py",))
        row = next(row for row in result["files"] if row["path"] == "modify.py")
        self.assertEqual(row["change"], "replaced")
        self.assertTrue(row["content_changed"])
        self.assertIn("-value = 1", row["diff"])
        self.assertIn("+value = 99", row["diff"])

if __name__ == "__main__":
    unittest.main()
