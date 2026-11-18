from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_evolution_attribution import (  # noqa: E402
    AttributionError,
    capture_baseline,
    check_changes,
)


class AttributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.stable = base / "stable"
        self.sandbox = base / "sandbox"
        self.stable.mkdir()
        self.sandbox.mkdir()
        self.paths = ["agent-core/example.py", "desktop/view.ts"]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def put(self, root: Path, rel: str, data: bytes) -> None:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def test_clean_baseline_and_unchanged_finish_are_safe_noops(self) -> None:
        self.put(self.stable, self.paths[0], b"same")
        self.put(self.sandbox, self.paths[0], b"same")
        baseline = capture_baseline(self.stable, self.sandbox, self.paths)
        result = check_changes(baseline)
        self.assertTrue(result["safe_to_consider"])
        self.assertEqual(result["changed_paths"], [])
        self.assertEqual(result["preexisting_paths"], [])
        self.assertEqual(result["blocked_paths"], [])
        self.assertEqual(len(result["summary_sha256"]), 64)

    def test_safe_to_consider_is_explicitly_scoped_to_declared_paths(self) -> None:
        baseline = capture_baseline(self.stable, self.sandbox, [self.paths[0]])
        self.put(self.sandbox, "outside-declared-scope.txt", b"unreviewed")
        result = check_changes(baseline)
        self.assertTrue(result["safe_to_consider"])
        self.assertEqual(result["evidence_scope"], "declared_paths_point_in_time_only")
        self.assertFalse(result["full_candidate_coverage"])
        self.assertEqual([entry["path"] for entry in result["entries"]], [self.paths[0]])

    def test_preexisting_sandbox_change_stays_separate(self) -> None:
        self.put(self.stable, self.paths[0], b"stable")
        self.put(self.sandbox, self.paths[0], b"already changed")
        baseline = capture_baseline(self.stable, self.sandbox, [self.paths[0]])
        result = check_changes(baseline)
        self.assertEqual(result["preexisting_paths"], [self.paths[0]])
        self.assertEqual(result["changed_paths"], [])
        self.assertIn(self.paths[0], result["blocked_paths"])
        self.assertFalse(result["safe_to_consider"])

    def test_clean_change_is_attributed_but_never_authorized(self) -> None:
        rel = self.paths[0]
        self.put(self.stable, rel, b"old")
        self.put(self.sandbox, rel, b"old")
        baseline = capture_baseline(self.stable, self.sandbox, [rel])
        self.put(self.sandbox, rel, b"new")
        result = check_changes(baseline)
        self.assertEqual(result["changed_paths"], [rel])
        self.assertEqual(result["preexisting_paths"], [])
        self.assertEqual(result["blocked_paths"], [])
        self.assertTrue(result["safe_to_consider"])
        self.assertFalse(result["merge_authorized"])

    def test_dirty_start_followed_by_change_fails_closed_as_whole_file(self) -> None:
        rel = self.paths[0]
        self.put(self.stable, rel, b"stable")
        self.put(self.sandbox, rel, b"preexisting")
        baseline = capture_baseline(self.stable, self.sandbox, [rel])
        self.put(self.sandbox, rel, b"preexisting plus task")
        result = check_changes(baseline)
        self.assertIn(rel, result["preexisting_paths"])
        self.assertIn(rel, result["changed_paths"])
        self.assertIn(rel, result["blocked_paths"])

    def test_clean_and_dirty_paths_are_reported_separately(self) -> None:
        clean, dirty = self.paths
        self.put(self.stable, clean, b"clean base")
        self.put(self.sandbox, clean, b"clean base")
        self.put(self.stable, dirty, b"stable base")
        self.put(self.sandbox, dirty, b"preexisting")
        baseline = capture_baseline(self.stable, self.sandbox, [clean, dirty])
        self.put(self.sandbox, clean, b"task change")
        self.put(self.sandbox, dirty, b"preexisting plus task")
        result = check_changes(baseline)
        self.assertEqual(result["changed_paths"], sorted([clean, dirty]))
        self.assertEqual(result["preexisting_paths"], [dirty])
        self.assertEqual(result["blocked_paths"], [dirty])
        self.assertTrue(result["safe_to_consider"] is False)
        self.assertNotIn(clean, result["reasons"])

    def test_stable_modification_blocks_change_attribution(self) -> None:
        rel = self.paths[0]
        self.put(self.stable, rel, b"old")
        self.put(self.sandbox, rel, b"old")
        baseline = capture_baseline(self.stable, self.sandbox, [rel])
        self.put(self.stable, rel, b"external stable edit")
        self.put(self.sandbox, rel, b"sandbox edit")
        result = check_changes(baseline)
        self.assertIn(rel, result["blocked_paths"])
        self.assertFalse(result["safe_to_consider"])
        self.assertIn("stable_changed", result["reasons"][rel])

    def test_missing_to_created_is_detected(self) -> None:
        rel = self.paths[0]
        baseline = capture_baseline(self.stable, self.sandbox, [rel])
        self.put(self.sandbox, rel, b"created")
        result = check_changes(baseline)
        self.assertEqual(result["changed_paths"], [rel])

    def test_deleted_file_is_blocked(self) -> None:
        rel = self.paths[0]
        self.put(self.stable, rel, b"same")
        self.put(self.sandbox, rel, b"same")
        baseline = capture_baseline(self.stable, self.sandbox, [rel])
        (self.sandbox / rel).unlink()
        result = check_changes(baseline)
        self.assertIn(rel, result["blocked_paths"])
        self.assertIn("sandbox_missing_after_baseline", result["reasons"][rel])

    def test_symlink_and_junction_style_reparse_are_rejected(self) -> None:
        rel = self.paths[0]
        external = Path(self.tmp.name) / "outside"
        external.mkdir()
        link = self.sandbox / "agent-core"
        try:
            link.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            if os.name != "nt":
                self.skipTest("directory symlinks unavailable")
            link.parent.mkdir(parents=True, exist_ok=True)
            command = f'mklink /J "{link}" "{external}"'
            completed = subprocess.run(["cmd", "/c", command], capture_output=True, text=True)
            if completed.returncode != 0:
                self.skipTest("symlink and junction creation unavailable")
        baseline = capture_baseline(self.stable, self.sandbox, [rel])
        result = check_changes(baseline)
        self.assertIn(rel, result["blocked_paths"])
        self.assertIn("link_or_reparse_point", result["reasons"][rel])

    def test_root_with_reparse_ancestor_is_rejected(self) -> None:
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        link = Path(self.tmp.name) / "root-link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlink creation unavailable")
        linked_root = link / "candidate"
        linked_root.mkdir()
        with self.assertRaises(AttributionError):
            capture_baseline(linked_root, self.sandbox, [self.paths[0]])

    def test_non_windows_path_reader_fails_closed_without_opening_files(self) -> None:
        from unittest import mock
        import self_evolution_attribution as attribution
        root = self.sandbox
        with mock.patch.object(attribution.os, "name", "posix"), \
             mock.patch.object(attribution.os, "open", side_effect=AssertionError("must not open")):
            state = attribution._read_state(root, self.paths[0], [0], (0, 0))
        self.assertEqual(state.status, "unsafe")
        self.assertEqual(state.detail, "safe_relative_handle_traversal_unavailable")

    def test_hard_linked_file_is_never_attributed(self) -> None:
        rel = self.paths[0]
        self.put(self.stable, rel, b"baseline")
        self.put(self.sandbox, rel, b"baseline")
        os.link(self.sandbox / rel, Path(self.tmp.name) / "outside-hard-link")
        baseline = capture_baseline(self.stable, self.sandbox, [rel])
        result = check_changes(baseline)
        self.assertFalse(result["safe_to_consider"])
        self.assertIn(rel, result["blocked_paths"])
        self.assertIn("sandbox_hard_link_alias_detected", result["reasons"][rel])

    @unittest.skipUnless(os.name == "nt", "Windows handle traversal")
    def test_windows_attribution_does_not_open_candidate_by_absolute_path(self) -> None:
        from unittest import mock
        import self_evolution_attribution as attribution
        rel = self.paths[0]
        self.put(self.stable, rel, b"same")
        self.put(self.sandbox, rel, b"same")
        with mock.patch.object(attribution.os, "open", side_effect=AssertionError("unsafe pathname open")):
            baseline = capture_baseline(self.stable, self.sandbox, [rel])
            result = check_changes(baseline)
        self.assertTrue(result["safe_to_consider"])

    @unittest.skipUnless(os.name == "nt", "Windows junction and handle traversal")
    def test_junction_swap_during_file_open_cannot_redirect_read(self) -> None:
        import contextlib
        from unittest import mock
        import self_evolution_manifest as manifest
        rel = self.paths[0]
        self.put(self.stable, rel, b"original")
        self.put(self.sandbox, rel, b"original")
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "example.py").write_bytes(b"outside secret")
        parent = self.sandbox / "agent-core"
        held = self.sandbox / "agent-core-held"
        original = manifest._windows_open_relative
        swapped = []

        @contextlib.contextmanager
        def during_open(parent_handle, name, **kwargs):
            if name != "example.py" or swapped or not held.exists():
                with original(parent_handle, name, **kwargs) as handle:
                    yield handle
                return
            # The caller holds the original parent handle. Put a real junction
            # at the old pathname only during the child open, then restore it.
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(parent), str(outside)],
                                    capture_output=True, text=True)
            if result.returncode:
                raise AssertionError("junction creation failed: " + result.stderr)
            try:
                with original(parent_handle, name, **kwargs) as handle:
                    swapped.append(True)
                    yield handle
            finally:
                os.rmdir(parent)

        @contextlib.contextmanager
        def controlled_open(parent_handle, name, **kwargs):
            if name == "example.py" and not swapped and not held.exists():
                parent.rename(held)
                try:
                    with during_open(parent_handle, name, **kwargs) as handle:
                        yield handle
                finally:
                    held.rename(parent)
            else:
                with original(parent_handle, name, **kwargs) as handle:
                    yield handle

        # Exercise only sandbox; stable is captured without intervention first.
        import self_evolution_attribution as attribution
        _, identity = attribution._root(self.sandbox)
        with mock.patch.object(manifest, "_windows_open_relative", controlled_open):
            state = attribution._read_state(self.sandbox, rel, [0], identity)
        self.assertTrue(swapped)
        self.assertEqual(state.status, "present")
        self.assertEqual(state.sha256, hashlib.sha256(b"original").hexdigest())
        self.assertNotEqual(state.sha256, hashlib.sha256(b"outside secret").hexdigest())

    @unittest.skipUnless(os.name == "nt", "Windows handle traversal")
    def test_handle_digest_enforces_attribution_byte_limit_while_reading(self) -> None:
        import self_evolution_manifest as manifest
        self.put(self.sandbox, "limited.bin", b"abcd")
        with manifest._windows_open_trusted_root(self.sandbox) as (_, root, _, _):
            with manifest._windows_open_relative(root, "limited.bin", directory=False) as handle:
                _, info = manifest._windows_handle_info(handle)
                with self.assertRaisesRegex(manifest.ManifestError, "^size_limit_exceeded$"):
                    manifest._windows_digest_handle(handle, info, [0], max_file_bytes=3)

    def test_hard_link_added_after_baseline_blocks_attribution(self) -> None:
        rel = self.paths[0]
        self.put(self.stable, rel, b"same")
        self.put(self.sandbox, rel, b"same")
        baseline = capture_baseline(self.stable, self.sandbox, [rel])
        os.link(self.sandbox / rel, Path(self.tmp.name) / "outside-hard-link")
        result = check_changes(baseline)
        self.assertIn("sandbox_hard_link_alias_detected", result["reasons"][rel])
        self.assertFalse(result["safe_to_consider"])

    def test_overlapping_stable_and_sandbox_roots_are_rejected(self) -> None:
        nested = self.stable / "candidate"
        nested.mkdir()
        for stable, sandbox in ((self.stable, nested), (nested, self.stable)):
            with self.subTest(stable=stable, sandbox=sandbox), self.assertRaises(AttributionError) as caught:
                capture_baseline(stable, sandbox, ["agent-core/example.py"])
            self.assertEqual(str(caught.exception), "stable_and_sandbox_roots_must_not_overlap")

    def test_path_escape_and_git_metadata_are_rejected(self) -> None:
        for bad in ("../secret", "agent-core/../../secret", ".git/config", "agent-core/.git/config", "C:/secret", "/absolute", "src/file.py:stream", "src/trailing.", "src/CON.txt"):
            with self.subTest(path=bad), self.assertRaises(AttributionError):
                capture_baseline(self.stable, self.sandbox, [bad])


    def test_casefold_path_aliases_are_rejected(self) -> None:
        with self.assertRaises(AttributionError) as caught:
            capture_baseline(self.stable, self.sandbox, ["src/Module.py", "src/module.py"])
        self.assertEqual(str(caught.exception), "path_aliases")

    def test_duplicate_and_excessive_paths_are_rejected(self) -> None:
        with self.assertRaises(AttributionError):
            capture_baseline(self.stable, self.sandbox, ["a", "a"])
        with self.assertRaises(AttributionError):
            capture_baseline(self.stable, self.sandbox, [f"f{i}" for i in range(1001)])

    def test_baseline_is_process_sealed_not_caller_supplied_hashes(self) -> None:
        baseline = capture_baseline(self.stable, self.sandbox, [self.paths[0]])
        forged = type(baseline)(
            baseline.stable_root, baseline.sandbox_root, baseline.paths, baseline.records,
            baseline.stable_root_identity, baseline.sandbox_root_identity, "0" * 64,
        )
        with self.assertRaises(AttributionError):
            check_changes(forged)

    def test_root_replacement_blocks_all_paths(self) -> None:
        baseline = capture_baseline(self.stable, self.sandbox, [self.paths[0]])
        moved = Path(self.tmp.name) / "sandbox-old"
        self.sandbox.rename(moved)
        self.sandbox.mkdir()
        result = check_changes(baseline)
        self.assertIn(self.paths[0], result["blocked_paths"])
        self.assertIn("sandbox_root_identity_changed", result["reasons"][self.paths[0]])
        entry = result["entries"][0]
        self.assertEqual(entry["current"]["sandbox"]["status"], "unsafe")

    def test_parent_directory_replacement_blocks_path_identity(self) -> None:
        rel = self.paths[0]
        self.put(self.stable, rel, b"same")
        self.put(self.sandbox, rel, b"same")
        baseline = capture_baseline(self.stable, self.sandbox, [rel])
        old = self.sandbox / "agent-core-old"
        (self.sandbox / "agent-core").rename(old)
        (self.sandbox / "agent-core").mkdir()
        self.put(self.sandbox, rel, b"same")
        result = check_changes(baseline)
        self.assertIn(rel, result["blocked_paths"])
        self.assertIn("sandbox_parent_path_identity_changed", result["reasons"][rel])

    def test_oversize_file_is_blocked_without_reading_unbounded_data(self) -> None:
        rel = self.paths[0]
        self.put(self.stable, rel, b"x" * (8 * 1024 * 1024 + 1))
        self.put(self.sandbox, rel, b"x" * (8 * 1024 * 1024 + 1))
        baseline = capture_baseline(self.stable, self.sandbox, [rel])
        result = check_changes(baseline)
        self.assertIn(rel, result["blocked_paths"])
        self.assertTrue(any("size_limit_exceeded" in reason for reason in result["reasons"][rel]))

    def test_result_digest_covers_machine_readable_summary(self) -> None:
        baseline = capture_baseline(self.stable, self.sandbox, [self.paths[0]])
        result = check_changes(baseline)
        payload = {key: value for key, value in result.items() if key != "summary_sha256"}
        expected = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(result["summary_sha256"], expected)


if __name__ == "__main__":
    unittest.main()
