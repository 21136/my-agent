from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

import self_evolution_manifest as manifest  # noqa: E402


class TreeManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.stable = self.base / "stable"
        self.candidate = self.base / "candidate"
        self.stable.mkdir()
        self.candidate.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def put(self, root: Path, relative: str, value: bytes) -> None:
        target = root.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)

    def test_manifest_covers_tracked_untracked_ignored_and_empty_directories(self) -> None:
        self.put(self.candidate, "src/tracked.py", b"tracked")
        self.put(self.candidate, "ignored/cache.bin", b"ignored but present")
        (self.candidate / "empty").mkdir()
        (self.candidate / ".gitignore").write_text("ignored/\n", encoding="utf-8")

        snapshot = manifest.capture_tree_manifest(self.candidate)

        self.assertEqual(snapshot.scope, "candidate_default_streams_point_in_time_excluding_git_metadata_subtrees")
        self.assertFalse(snapshot.snapshot_coverage_complete)
        self.assertIn("excluding_git_metadata_subtrees", snapshot.scope)
        self.assertIn("not_durable_or_cross_volume", snapshot.identity_scope)
        self.assertTrue(snapshot.full_candidate_coverage)
        self.assertFalse(snapshot.host_coverage_claim_permitted)
        paths = {entry.path for entry in snapshot.entries}
        self.assertTrue({"src", "src/tracked.py", "ignored", "ignored/cache.bin", "empty", ".gitignore"} <= paths)
        ignored = next(entry for entry in snapshot.entries if entry.path == "ignored/cache.bin")
        self.assertEqual(ignored.kind, "file")
        self.assertIn("without_git_ignore_filtering", snapshot.ignored_item_policy)
        self.assertEqual(snapshot.ignore_policy_files, (".gitignore",))

    def test_manifest_paths_reject_non_printing_and_format_control_characters(self) -> None:
        for bad_path in ("src/bad\x01.py", "src/zero\u200bwidth.py"):
            with self.subTest(path=bad_path), self.assertRaises(manifest.ManifestError):
                manifest.validate_relative_path(bad_path)

    def test_real_gitignored_file_is_included_and_git_metadata_is_explicitly_excluded(self) -> None:
        (self.candidate / ".gitignore").write_text("ignored/\n", encoding="utf-8")
        self.put(self.candidate, "ignored/cache.bin", b"git says ignored")
        completed = subprocess.run(["git", "init", "--quiet", str(self.candidate)],
                                   capture_output=True, text=True)
        if completed.returncode != 0:
            self.skipTest("git init unavailable")
        ignored = subprocess.run(["git", "-C", str(self.candidate), "check-ignore", "--quiet", "ignored/cache.bin"],
                                 capture_output=True)
        self.assertEqual(ignored.returncode, 0)

        snapshot = manifest.capture_tree_manifest(self.candidate)

        self.assertIn("ignored/cache.bin", {entry.path for entry in snapshot.entries})
        self.assertEqual([item["path"] for item in snapshot.excluded_metadata], [".git"])
        self.assertTrue(snapshot.excluded_metadata[0]["streams_checked"] == "default_only")
        self.assertTrue(snapshot.excluded_metadata[0]["subtree_excluded"])
        self.assertFalse(snapshot.full_candidate_coverage)

    def test_git_metadata_exclusion_prevents_full_candidate_coverage(self):
        (self.candidate / ".git").mkdir()
        (self.candidate / ".git" / "config").write_text("[core]\n", encoding="utf-8")

        snapshot = manifest.capture_tree_manifest(self.candidate)

        self.assertTrue(snapshot.sweep_coverage_complete)
        self.assertFalse(snapshot.full_candidate_coverage)
        self.assertEqual(snapshot.excluded_metadata[0]["path"], ".git")

    def test_attribution_reports_added_modified_deleted_without_merging(self) -> None:
        for root in (self.stable, self.candidate):
            self.put(root, "same.txt", b"same")
            self.put(root, "changed.txt", b"base")
            self.put(root, "removed.txt", b"old")
        baseline = manifest.capture_tree_baseline(self.stable, self.candidate)
        self.put(self.candidate, "changed.txt", b"new")
        self.put(self.candidate, "added.txt", b"added")
        (self.candidate / "removed.txt").unlink()

        result = manifest.attribute_tree_changes(baseline)

        actions = {item["path"]: item["action"] for item in result["changes"]}
        self.assertEqual(actions, {"added.txt": "add", "changed.txt": "modify", "removed.txt": "delete"})
        changes = {item["path"]: item for item in result["changes"]}
        self.assertIsNone(changes["added.txt"]["before"])
        self.assertIsNotNone(changes["added.txt"]["after"])
        self.assertIsNotNone(changes["removed.txt"]["before"])
        self.assertIsNone(changes["removed.txt"]["after"])
        self.assertNotEqual(changes["changed.txt"]["before"]["sha256"],
                            changes["changed.txt"]["after"]["sha256"])
        self.assertEqual(result["evidence_scope"], "candidate_default_streams_point_in_time_excluding_git_metadata_subtrees")
        self.assertFalse(result["snapshot_coverage_complete"])
        self.assertFalse(result["full_candidate_coverage"])
        self.assertFalse(result["actor_identity_proven"])
        self.assertFalse(result["merge_authorized"])
        self.assertFalse(result["safe_to_consider"])
        self.assertIn("root_identity_is_not_durable_proof", result["blockers"])

    def test_baseline_manifest_tampering_is_rejected(self) -> None:
        self.put(self.stable, "same.txt", b"same")
        self.put(self.candidate, "same.txt", b"same")
        baseline = manifest.capture_tree_baseline(self.stable, self.candidate)
        changed_entry = replace(baseline.candidate.entries[0], sha256="0" * 64)
        forged_manifest = replace(baseline.candidate, entries=(changed_entry,))
        forged = replace(baseline, candidate=forged_manifest)
        with self.assertRaises(manifest.ManifestError) as caught:
            manifest.attribute_tree_changes(forged)
        self.assertEqual(str(caught.exception), "manifest_integrity_check_failed")

    def test_candidate_identity_replacement_is_attributed_even_when_bytes_match(self) -> None:
        self.put(self.stable, "same.txt", b"same")
        self.put(self.candidate, "same.txt", b"same")
        baseline = manifest.capture_tree_baseline(self.stable, self.candidate)
        path = self.candidate / "same.txt"
        replacement = self.candidate / "replacement.tmp"
        replacement.write_bytes(b"same")
        replacement.replace(path)
        result = manifest.attribute_tree_changes(baseline)
        self.assertEqual([change["path"] for change in result["changes"]], ["same.txt"])

    def test_preexisting_candidate_changes_and_stable_drift_block_attribution(self) -> None:
        self.put(self.stable, "dirty.txt", b"stable")
        self.put(self.candidate, "dirty.txt", b"preexisting")
        baseline = manifest.capture_tree_baseline(self.stable, self.candidate)
        self.put(self.candidate, "dirty.txt", b"preexisting plus task")
        self.put(self.stable, "clean.txt", b"new stable file")

        result = manifest.attribute_tree_changes(baseline)

        self.assertFalse(result["safe_to_consider"])
        self.assertIn("candidate_path_was_preexisting_and_changed", result["blockers"])
        self.assertIn("stable_tree_changed_since_baseline", result["blockers"])

    def test_file_and_total_byte_budgets_are_enforced_on_open_handles(self) -> None:
        self.put(self.candidate, "one.bin", b"four")
        with mock.patch.object(manifest, "MAX_FILE_BYTES", 3):
            with self.assertRaisesRegex(manifest.ManifestError, "^size_limit_exceeded$"):
                manifest.capture_tree_manifest(self.candidate)

        (self.candidate / "one.bin").write_bytes(b"12")
        self.put(self.candidate, "two.bin", b"34")
        with mock.patch.object(manifest, "MAX_FILE_BYTES", 8), \
             mock.patch.object(manifest, "MAX_TOTAL_BYTES", 3):
            with self.assertRaisesRegex(manifest.ManifestError, "^size_limit_exceeded$"):
                manifest.capture_tree_manifest(self.candidate)

    def test_repeated_scans_release_native_handles(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows native handle accounting")
        import ctypes
        from ctypes import wintypes
        self.put(self.candidate, "nested/value.bin", b"small fixture")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        count = kernel.GetProcessHandleCount
        count.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        count.restype = wintypes.BOOL
        current = kernel.GetCurrentProcess
        current.restype = wintypes.HANDLE
        def handles() -> int:
            value = wintypes.DWORD()
            self.assertTrue(count(current(), ctypes.byref(value)))
            return int(value.value)
        before = handles()
        for _ in range(12):
            manifest.capture_tree_manifest(self.candidate)
        self.assertLessEqual(handles(), before + 2, "native scan handles leaked across completed sweeps")

    def test_backupread_initial_request_exceeds_win32_stream_id_size(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows BackupRead buffer contract")
        import ctypes
        from ctypes import wintypes
        class Win32StreamId(ctypes.Structure):
            _fields_ = [("stream_id", wintypes.DWORD), ("stream_attributes", wintypes.DWORD),
                        ("size", ctypes.c_longlong), ("stream_name_size", wintypes.DWORD),
                        ("stream_name", wintypes.WCHAR * 1)]
        struct_size = ctypes.sizeof(Win32StreamId)
        self.assertEqual(struct_size, 24)
        self.put(self.candidate, "payload.bin", b"buffer contract fixture")
        capacities = []
        real_call = manifest._windows_backup_read_call

        def capture_call(api, handle, buffer, capacity, transferred, abort, security, context):
            if not abort:
                capacities.append(capacity)
            return real_call(api, handle, buffer, capacity, transferred, abort, security, context)

        with mock.patch.object(manifest, "_windows_backup_read_call", side_effect=capture_call, create=True):
            manifest.capture_tree_manifest(self.candidate)
        self.assertTrue(capacities)
        self.assertEqual(capacities[0], 64 * 1024)
        self.assertGreater(capacities[0], struct_size)
        self.assertTrue(all(value > struct_size for value in capacities))

    def test_ntfs_alternate_streams_are_rejected_on_files_dirs_and_git_metadata(self) -> None:
        if os.name != "nt":
            self.skipTest("real NTFS alternate data stream test")
        import ctypes
        from ctypes import wintypes
        volume = ctypes.create_unicode_buffer(261)
        filesystem = ctypes.create_unicode_buffer(261)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        get_volume = kernel.GetVolumeInformationW
        get_volume.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
                               ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD]
        get_volume.restype = wintypes.BOOL
        if not get_volume(self.candidate.drive + "\\", volume, len(volume), None, None, None, filesystem, len(filesystem)):
            self.skipTest("cannot query test volume filesystem")
        if filesystem.value.casefold() != "ntfs":
            self.skipTest(f"ADS regression requires NTFS, found {filesystem.value}")

        cases = ("file", "directory", "git-file", "git-directory")
        for case in cases:
            with self.subTest(entry_kind=case):
                root = self.base / ("ads-" + case)
                root.mkdir()
                if case == "file":
                    entry = root / "payload.bin"
                    # Force BackupRead to cross multiple large buffers before
                    # it reaches the alternate stream header.
                    entry.write_bytes(b"D" * (3 * 64 * 1024))
                elif case == "directory":
                    entry = root / "folder"
                    entry.mkdir()
                elif case == "git-file":
                    entry = root / ".git"
                    entry.write_text("gitdir: elsewhere\n", encoding="utf-8")
                else:
                    entry = root / ".git"
                    entry.mkdir()
                try:
                    with open(f"{entry}:codex_manifest_ads", "wb") as stream:
                        stream.write(b"must be detected")
                except OSError as exc:
                    self.skipTest(f"NTFS ADS creation unavailable: {exc}")
                with self.assertRaisesRegex(manifest.ManifestError, "^non_default_stream_detected$"):
                    manifest.capture_tree_manifest(root)

    def test_casefold_aliases_fail_closed(self) -> None:
        with self.assertRaises(manifest.ManifestError) as caught:
            manifest.validate_relative_paths(["src/Module.py", "src/module.py"])
        self.assertEqual(str(caught.exception), "windows_path_alias_detected")

    def test_symlink_or_reparse_entry_is_rejected_without_following(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_bytes(b"secret")
        link = self.candidate / "linked"
        self._make_directory_link(link, outside)
        with self.assertRaises(manifest.ManifestError) as caught:
            manifest.capture_tree_manifest(self.candidate)
        self.assertEqual(str(caught.exception), "link_or_reparse_point_detected")

    def test_hard_link_alias_is_rejected(self) -> None:
        first = self.candidate / "first.bin"
        first.write_bytes(b"same inode")
        try:
            os.link(first, self.candidate / "second.bin")
        except OSError as exc:
            self.skipTest(f"hard link unavailable: {exc}")
        with self.assertRaises(manifest.ManifestError) as caught:
            manifest.capture_tree_manifest(self.candidate)
        self.assertEqual(str(caught.exception), "hard_link_alias_detected")

    def test_root_identity_replacement_between_baseline_and_check_is_rejected(self) -> None:
        self.put(self.stable, "same.txt", b"same")
        self.put(self.candidate, "same.txt", b"same")
        baseline = manifest.capture_tree_baseline(self.stable, self.candidate)
        moved = self.base / "old-candidate"
        self.candidate.rename(moved)
        self.candidate.mkdir()
        self.put(self.candidate, "same.txt", b"same")
        with self.assertRaises(manifest.ManifestError) as caught:
            manifest.attribute_tree_changes(baseline)
        self.assertEqual(str(caught.exception), "root_identity_changed_since_baseline")

    def test_directory_swap_to_external_junction_never_reads_external_bytes(self) -> None:
        """A real junction swap races a held relative directory handle."""
        if os.name != "nt":
            self.skipTest("Windows native relative-handle traversal")
        import ctypes
        from ctypes import wintypes
        inside = self.candidate / "sub"
        outside = self.base / "outside"
        parked = self.base / "parked-sub"
        inside.mkdir()
        outside.mkdir()
        (inside / "value.bin").write_bytes(b"inside fixture")
        (outside / "value.bin").write_bytes(b"outside secret fixture")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        outside_handle = kernel.CreateFileW(str(outside / "value.bin"), 0x80000000, 7, None, 3,
                                            0x02000000, None)
        self.assertNotEqual(outside_handle, wintypes.HANDLE(-1).value)
        outside_identity = manifest._windows_handle_identity(outside_handle)
        real_list = manifest._windows_enumerate_directory
        real_digest = manifest._windows_digest_handle
        read_identities = []
        swapped = False

        enumerations = 0

        def swap_then_enumerate(handle):
            nonlocal swapped, enumerations
            # Candidate root is enumerated first; this second call holds `sub`.
            enumerations += 1
            if not swapped and enumerations == 2:
                inside.rename(parked)
                completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(inside), str(outside)],
                                           capture_output=True, text=True)
                if completed.returncode:
                    parked.rename(inside)
                    raise AssertionError("isolated junction creation failed: " + completed.stderr)
                swapped = True
            return real_list(handle)

        def record_read(handle, expected, budget):
            read_identities.append(manifest._windows_handle_identity(handle))
            return real_digest(handle, expected, budget)

        try:
            with mock.patch.object(manifest, "_windows_enumerate_directory", side_effect=swap_then_enumerate), \
                 mock.patch.object(manifest, "_windows_digest_handle", side_effect=record_read):
                error = None
                try:
                    manifest.capture_tree_manifest(self.candidate)
                except manifest.ManifestError as exc:
                    error = exc
            self.assertTrue(swapped, "the test must perform a real isolated junction replacement")
            self.assertNotIn(outside_identity, read_identities, "scanner read a file through the external junction")
            self.assertIsNotNone(error, "the replaced directory must make the sweep fail closed")
            self.assertIn(str(error), {"directory_changed_during_sweep", "link_or_reparse_point_detected"})
        finally:
            kernel.CloseHandle(outside_handle)
            if swapped and inside.exists():
                os.rmdir(inside)
            if parked.exists():
                parked.rename(inside)

    def test_windows_native_relative_open_holds_parent_across_junction_swap(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows native relative-open experiment")
        import ctypes
        from ctypes import wintypes
        inside, parked, outside = self.candidate / "sub", self.base / "parked", self.base / "outside"
        inside.mkdir()
        outside.mkdir()
        (inside / "value.bin").write_bytes(b"inside")
        (outside / "value.bin").write_bytes(b"outside")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(inside), 0x80000000, 7, None, 3, 0x02200000, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        junction_created = False
        try:
            parent_identity = manifest._windows_handle_identity(handle)
            inside.rename(parked)
            completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(inside), str(outside)],
                                       capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            junction_created = True
            self.assertEqual(manifest._windows_handle_identity(handle), parent_identity)
            with manifest._windows_open_relative_for_research(handle, "value.bin", directory=False) as child:
                child_identity = manifest._windows_handle_identity(child)
                self.assertNotEqual(child_identity, parent_identity)
                import msvcrt
                duplicate = kernel.DuplicateHandle
                duplicate.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                      ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                duplicate.restype = wintypes.BOOL
                process = kernel.GetCurrentProcess
                process.restype = wintypes.HANDLE
                copy = wintypes.HANDLE()
                self.assertTrue(duplicate(process(), child, process(), ctypes.byref(copy), 0, False, 2))
                with os.fdopen(msvcrt.open_osfhandle(copy.value, os.O_RDONLY | os.O_BINARY), "rb") as f:
                    self.assertEqual(f.read(), b"inside")
                self.assertEqual(manifest._windows_handle_identity(child), child_identity)
        finally:
            kernel.CloseHandle(handle)
            if junction_created:
                os.rmdir(inside)
            if parked.exists():
                parked.rename(inside)

    def test_windows_native_directory_enumeration_uses_held_handle_after_swap(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows native handle enumeration experiment")
        import ctypes
        from ctypes import wintypes
        inside, parked, outside = self.candidate / "sub", self.base / "parked-enum", self.base / "outside-enum"
        inside.mkdir()
        outside.mkdir()
        (inside / "inside.txt").write_bytes(b"inside")
        (outside / "outside.txt").write_bytes(b"outside")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(inside), 0x80000000, 7, None, 3, 0x02200000, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        junction_created = False
        try:
            inside.rename(parked)
            completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(inside), str(outside)],
                                       capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            junction_created = True
            self.assertEqual(manifest._windows_enumerate_directory(handle), ("inside.txt",))
            self.assertEqual(manifest._windows_enumerate_directory(handle), ("inside.txt",))
        finally:
            kernel.CloseHandle(handle)
            if junction_created:
                os.rmdir(inside)
            if parked.exists():
                parked.rename(inside)

    def test_windows_native_enumeration_handles_multiple_query_buffers(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows native handle enumeration experiment")
        import ctypes
        from ctypes import wintypes
        directory = self.candidate / "many"
        directory.mkdir()
        expected = {f"{i:04d}-" + "a" * 105 + ".txt" for i in range(400)}
        for name in expected:
            (directory / name).write_bytes(b"")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(directory), 0x80000000, 7, None, 3, 0x02200000, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        try:
            self.assertEqual(set(manifest._windows_enumerate_directory(handle)), expected)
        finally:
            kernel.CloseHandle(handle)

    def test_windows_native_relative_open_refuses_path_components(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows native relative-open experiment")
        for name in ("..", ".", "../x", "..\\x", "C:x", "x:stream", "x\x00y"):
            with self.subTest(name=name), self.assertRaises(manifest.ManifestError):
                with manifest._windows_open_relative_for_research(0, name, directory=False):
                    self.fail("invalid relative component opened")

    def test_windows_native_relative_open_refuses_junction(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows native relative-open experiment")
        import ctypes
        from ctypes import wintypes
        outside = self.base / "outside"
        outside.mkdir()
        link = self.candidate / "linked"
        self._make_directory_link(link, outside)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        parent = kernel.CreateFileW(str(self.candidate), 0x80000000, 7, None, 3, 0x02200000, None)
        self.assertNotEqual(parent, ctypes.c_void_p(-1).value)
        try:
            with self.assertRaises(manifest.ManifestError) as caught:
                with manifest._windows_open_relative_for_research(parent, "linked", directory=True):
                    self.fail("a reparse entry must not be exposed")
            self.assertEqual(str(caught.exception), "link_or_reparse_point_detected")
        finally:
            kernel.CloseHandle(parent)

    def test_unsupported_platform_fails_before_examining_root(self) -> None:
        from types import SimpleNamespace
        with mock.patch.object(manifest, "os", SimpleNamespace(name="posix")):
            with mock.patch.object(manifest, "_safe_root", side_effect=AssertionError("must not inspect root")):
                with self.assertRaises(manifest.ManifestError) as caught:
                    manifest.capture_tree_manifest(self.candidate)
        self.assertEqual(str(caught.exception), "safe_relative_handle_traversal_unavailable")

    def test_baseline_creation_is_also_closed_before_reading_either_tree(self) -> None:
        from types import SimpleNamespace
        with mock.patch.object(manifest, "os", SimpleNamespace(name="posix")):
            with mock.patch.object(manifest, "_safe_root", side_effect=AssertionError("must not inspect root")):
                with self.assertRaisesRegex(manifest.ManifestError, "^safe_relative_handle_traversal_unavailable$"):
                    manifest.capture_tree_baseline(self.stable, self.candidate)

    def test_directory_stamp_change_with_unchanged_names_fails_closed(self) -> None:
        real_info = manifest._windows_handle_info
        calls = 0

        def changed_info(handle):
            nonlocal calls
            identity, info = real_info(handle)
            if info["directory"] and identity == root_identity:
                calls += 1
                if calls == 2:
                    info = dict(info, change_time=int(info["change_time"]) + 1)
            return identity, info

        with manifest._windows_open_trusted_root(self.candidate) as (_, _, root_identity, _):
            pass
        with mock.patch.object(manifest, "_windows_handle_info", side_effect=changed_info):
            with self.assertRaisesRegex(manifest.ManifestError, "^directory_changed_during_sweep$"):
                manifest.capture_tree_manifest(self.candidate)
        self.assertGreaterEqual(calls, 2)

    def test_file_stream_change_during_sweep_fails_closed(self) -> None:
        self.put(self.candidate, "value.bin", b"fixture")
        real_streams = manifest._windows_default_streams
        file_calls = 0

        def changed_streams(handle, *, directory):
            nonlocal file_calls
            streams = real_streams(handle, directory=directory)
            if not directory:
                file_calls += 1
                if file_calls == 2:
                    return streams + ("synthetic-change",)
            return streams

        with mock.patch.object(manifest, "_windows_default_streams", side_effect=changed_streams):
            with self.assertRaisesRegex(manifest.ManifestError, "^file_changed_during_sweep$"):
                manifest.capture_tree_manifest(self.candidate)
        self.assertGreaterEqual(file_calls, 2)

    def test_concurrent_directory_mutation_is_reported_as_inconsistent(self) -> None:
        import threading
        self.put(self.candidate, "initial.txt", b"before")
        real_list = manifest._windows_enumerate_directory
        enumerated = threading.Event()
        mutated = threading.Event()
        errors = []

        def mutate_concurrently():
            if not enumerated.wait(5):
                errors.append("scanner did not reach root enumeration")
                return
            self.put(self.candidate, "raced.txt", b"concurrent filesystem mutation")
            mutated.set()

        thread = threading.Thread(target=mutate_concurrently, daemon=True)
        thread.start()

        def racing_list(handle):
            names = real_list(handle)
            if not enumerated.is_set():
                enumerated.set()
                if not mutated.wait(5):
                    raise AssertionError("concurrent mutator did not finish")
            return names

        with mock.patch.object(manifest, "_windows_enumerate_directory", side_effect=racing_list):
            with self.assertRaises(manifest.ManifestError) as caught:
                manifest.capture_tree_manifest(self.candidate)
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(str(caught.exception), "directory_changed_during_sweep")

    def test_root_replacement_or_path_alias_rejected(self) -> None:
        for root in ("../outside", "C:/outside", "src/../outside", "src/file.txt:stream", "src/CON.txt", "src/trailing."):
            with self.subTest(root=root), self.assertRaises(manifest.ManifestError):
                manifest.validate_relative_path(root)

    def _make_directory_link(self, link: Path, target: Path) -> None:
        try:
            link.symlink_to(target, target_is_directory=True)
            return
        except (OSError, NotImplementedError):
            if os.name != "nt":
                self.skipTest("directory symlink creation unavailable")
        completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                   capture_output=True, text=True)
        if completed.returncode != 0:
            self.skipTest("Windows junction creation unavailable")

    def test_root_symlink_is_rejected(self) -> None:
        alias = self.base / "candidate-alias"
        self._make_directory_link(alias, self.candidate)
        with self.assertRaises(manifest.ManifestError):
            manifest.capture_tree_manifest(alias)


if __name__ == "__main__":
    unittest.main()
