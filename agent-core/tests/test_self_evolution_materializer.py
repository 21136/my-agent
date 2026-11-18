from __future__ import annotations

import os
import shutil
import subprocess
import sys
import stat
import tempfile
import unittest
from unittest import mock
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_evolution_materializer import (  # noqa: E402
    MaterializationError,
    SafeSnapshotMaterializer,
)


class _IsolatedTestMaterializer(SafeSnapshotMaterializer):
    """Test-only gate substitute; does not establish production cleanup safety."""

    def _cleanup_is_race_safe(self) -> bool:
        return True


class SafeSnapshotMaterializerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="safe-materializer-test-")
        self.root = Path(self.tmp.name)
        self.repo = self.root / "source"
        self.repo.mkdir()
        self.git("init", "--quiet", str(self.repo))
        self.git("-C", str(self.repo), "config", "user.name", "Test User")
        self.git("-C", str(self.repo), "config", "user.email", "test@example.invalid")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "main.py").write_bytes(b"print('safe')\n")
        self.git("-C", str(self.repo), "add", ".")
        self.git("-C", str(self.repo), "commit", "--quiet", "-m", "fixture")
        self.commit = self.git("-C", str(self.repo), "rev-parse", "HEAD")
        self.objects = Path(self.git("-C", str(self.repo), "rev-parse", "--git-path", "objects"))
        if not self.objects.is_absolute():
            self.objects = self.repo / self.objects
        self.git_executable = shutil.which("git")
        self.assertIsNotNone(self.git_executable)
        # Existing behavior tests run in a private disposable repository and
        # scratch root. This test-only substitute exercises the materializer
        # logic without asserting that the host's cleanup primitive is safe.
        self.materializer = _IsolatedTestMaterializer(self.git_executable)
        self.production_materializer = SafeSnapshotMaterializer(self.git_executable)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def git(*args: str) -> str:
        proc = subprocess.run(["git", *args], capture_output=True, check=False)
        if proc.returncode:
            raise AssertionError(f"git fixture failed ({proc.returncode}): {proc.stderr!r}")
        return proc.stdout.decode("utf-8", errors="strict").strip()

    def test_materialize_bound_persists_authenticated_candidate_identity(self) -> None:
        destination = self.root / "12345678-1234-4234-8234-123456789abc"
        key = b"isolated-test-integrity-key-32-bytes"
        result = self.materializer.materialize_bound(
            self.objects, self.commit, destination,
            candidate_id="12345678-1234-4234-8234-123456789abc", integrity_key=key,
        )
        binding = self.materializer.read_binding(destination, expected_candidate_id="12345678-1234-4234-8234-123456789abc", integrity_key=key)
        self.assertEqual(binding["candidate_id"], "12345678-1234-4234-8234-123456789abc")
        self.assertEqual(binding["commit_oid"], self.commit)
        self.assertEqual(binding["manifest_digest"], result.manifest_digest)
        self.assertEqual(binding["directory_identity"], list(self.materializer._directory_identity(destination)))

    @unittest.skipUnless(os.name == "nt", "Windows candidate directory identity validation")
    def test_windows_bound_candidate_path_replacement_is_rejected_and_preserved(self) -> None:
        destination = self.root / "12345678-1234-4234-8234-123456789abc"
        moved = self.root / "candidate-bound-original"
        replacement = self.root / "candidate-bound-replacement"
        key = b"isolated-test-integrity-key-32-bytes"
        self.materializer.materialize_bound(
            self.objects, self.commit, destination,
            candidate_id="12345678-1234-4234-8234-123456789abc", integrity_key=key,
        )
        replacement.mkdir()
        sentinel = replacement / "sentinel.txt"
        sentinel.write_text("preserve attacker-controlled replacement", encoding="utf-8")
        destination.rename(moved)
        replacement.rename(destination)
        with self.assertRaises(MaterializationError) as caught:
            self.materializer.read_binding(destination, expected_candidate_id="12345678-1234-4234-8234-123456789abc", integrity_key=key)
        self.assertEqual(caught.exception.code, "binding_invalid")
        self.assertEqual((destination / "sentinel.txt").read_text(encoding="utf-8"), "preserve attacker-controlled replacement")
        self.assertTrue((moved / ".self-evolution-binding.json").is_file())

    @unittest.skipUnless(os.name == "nt", "Windows binding path replacement race")
    def test_windows_binding_read_blocks_candidate_and_ancestor_replacement_until_validation(self) -> None:
        import self_evolution_materializer as materializer_module

        candidate_id = "12345678-1234-4234-8234-123456789abc"
        key = b"isolated-test-integrity-key-32-bytes"
        for kind in ("candidate", "ancestor"):
            with self.subTest(kind=kind):
                base = self.root / ("binding-race-" + kind)
                base.mkdir()
                parent = base / "parent"
                parent.mkdir()
                destination = parent / candidate_id
                self.materializer.materialize_bound(self.objects, self.commit, destination,
                                                    candidate_id=candidate_id, integrity_key=key)
                replacement = base / "replacement"
                replacement.mkdir()
                if kind == "ancestor":
                    (replacement / candidate_id).mkdir()
                victim = destination if kind == "candidate" else parent
                moved = base / "moved"
                attempted = False
                swapped = False
                original_read = materializer_module._windows_read_relative_file

                def attack_after_read(handle, name, limit):
                    nonlocal attempted, swapped
                    data = original_read(handle, name, limit)
                    attempted = True
                    try:
                        victim.rename(moved)
                    except OSError as exc:
                        if getattr(exc, "winerror", None) not in (5, 32, 33):
                            raise
                    else:
                        replacement.rename(victim)
                        swapped = True
                    return data

                result = None
                try:
                    with mock.patch.object(materializer_module, "_windows_read_relative_file", side_effect=attack_after_read):
                        result = self.materializer.read_binding(destination, expected_candidate_id=candidate_id, integrity_key=key)
                except MaterializationError:
                    pass
                self.assertTrue(attempted)
                self.assertFalse(swapped and result is not None,
                                 "read_binding returned authenticated identity for a replaced path")
                if not swapped:
                    self.assertIsNotNone(result)
                    self.assertEqual(result["candidate_id"], candidate_id)
                    victim.rename(moved)  # Handle protection ends after return.
                    self.assertTrue(moved.is_dir())

    @unittest.skipUnless(os.name == "nt", "Windows binding file race")
    def test_windows_binding_file_cannot_be_replaced_or_written_during_hmac_validation(self) -> None:
        import self_evolution_materializer as materializer_module

        candidate_id = "12345678-1234-4234-8234-123456789abc"
        key = b"isolated-test-integrity-key-32-bytes"
        for attack in ("replace", "write", "hardlink_write"):
            with self.subTest(attack=attack):
                parent = self.root / ("binding-file-race-" + attack)
                parent.mkdir()
                destination = parent / candidate_id
                self.materializer.materialize_bound(self.objects, self.commit, destination,
                                                    candidate_id=candidate_id, integrity_key=key)
                binding = destination / ".self-evolution-binding.json"
                original = binding.read_bytes()
                replacement = parent / "replacement.json"
                replacement.write_bytes(b"replacement")
                alias = parent / "binding-hardlink.json"
                if attack == "hardlink_write":
                    os.link(binding, alias)
                attempted = False
                blocked = False
                real_compare = materializer_module.hmac.compare_digest

                def attack_during_hmac(left, right):
                    nonlocal attempted, blocked
                    attempted = True
                    try:
                        if attack == "replace":
                            os.replace(replacement, binding)
                        elif attack == "hardlink_write":
                            alias.write_bytes(b"modified")
                        else:
                            binding.write_bytes(b"modified")
                    except OSError as exc:
                        if getattr(exc, "winerror", None) not in (5, 32, 33) and exc.errno != 13:
                            raise
                        blocked = True
                    return real_compare(left, right)

                with mock.patch.object(materializer_module.hmac, "compare_digest", side_effect=attack_during_hmac):
                    record = self.materializer.read_binding(destination, expected_candidate_id=candidate_id, integrity_key=key)
                self.assertTrue(attempted)
                self.assertTrue(blocked, "binding file was changed during HMAC validation")
                self.assertEqual(binding.read_bytes(), original)
                self.assertEqual(record["candidate_id"], candidate_id)
                # A returned record is not a persistent file lock.
                binding.write_bytes(b"after return")
                self.assertEqual(binding.read_bytes(), b"after return")

    @unittest.skipUnless(os.name == "nt", "Windows binding existing writable handle")
    def test_windows_binding_refuses_preexisting_writer_and_delete_handle(self) -> None:
        import ctypes
        from ctypes import wintypes

        candidate_id = "12345678-1234-4234-8234-123456789abc"
        key = b"isolated-test-integrity-key-32-bytes"
        destination = self.root / candidate_id
        self.materializer.materialize_bound(self.objects, self.commit, destination,
                                            candidate_id=candidate_id, integrity_key=key)
        binding = destination / ".self-evolution-binding.json"
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        for access in (0x40000000, 0x00010000):
            with self.subTest(access=access):
                handle = create(str(binding), access, 7, None, 3, 0x00200000, None)
                if handle == wintypes.HANDLE(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    with self.assertRaises(MaterializationError):
                        self.materializer.read_binding(destination, expected_candidate_id=candidate_id, integrity_key=key)
                finally:
                    kernel.CloseHandle(handle)
        self.assertEqual(self.materializer.read_binding(destination, expected_candidate_id=candidate_id,
                                                         integrity_key=key)["candidate_id"], candidate_id)

    @unittest.skipUnless(os.name == "nt", "Windows binding preexisting writable mapping")
    def test_windows_binding_refuses_preexisting_writable_mapping(self) -> None:
        import mmap

        candidate_id = "12345678-1234-4234-8234-123456789abc"
        key = b"isolated-test-integrity-key-32-bytes"
        destination = self.root / candidate_id
        self.materializer.materialize_bound(self.objects, self.commit, destination,
                                            candidate_id=candidate_id, integrity_key=key)
        binding = destination / ".self-evolution-binding.json"
        with binding.open("r+b") as source:
            mapping = mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_WRITE)
        try:
            with self.assertRaises(MaterializationError):
                self.materializer.read_binding(destination, expected_candidate_id=candidate_id, integrity_key=key)
        finally:
            mapping.close()
        record = self.materializer.read_binding(destination, expected_candidate_id=candidate_id, integrity_key=key)
        self.assertEqual(record["candidate_id"], candidate_id)

    @unittest.skipUnless(os.name == "nt", "Windows binding validation handle lifetime")
    def test_windows_binding_keeps_candidate_pinned_during_signature_validation(self) -> None:
        import self_evolution_materializer as materializer_module

        candidate_id = "12345678-1234-4234-8234-123456789abc"
        destination = self.root / candidate_id
        moved = self.root / "validation-moved"
        key = b"isolated-test-integrity-key-32-bytes"
        self.materializer.materialize_bound(self.objects, self.commit, destination,
                                            candidate_id=candidate_id, integrity_key=key)
        original_compare = materializer_module.hmac.compare_digest
        blocked = False

        def race_during_authentication(left, right):
            nonlocal blocked
            try:
                destination.rename(moved)
            except OSError as exc:
                if getattr(exc, "winerror", None) not in (5, 32, 33):
                    raise
                blocked = True
            return original_compare(left, right)

        with mock.patch.object(materializer_module.hmac, "compare_digest", side_effect=race_during_authentication):
            record = self.materializer.read_binding(destination, expected_candidate_id=candidate_id, integrity_key=key)
        self.assertTrue(blocked, "candidate rename was not blocked during authentication")
        self.assertEqual(record["candidate_id"], candidate_id)
        destination.rename(moved)
        self.assertTrue(moved.is_dir())

    @unittest.skipUnless(os.name == "nt", "Windows competing delete handle")
    def test_windows_binding_fails_closed_if_candidate_has_existing_delete_handle(self) -> None:
        import ctypes
        from ctypes import wintypes

        candidate_id = "12345678-1234-4234-8234-123456789abc"
        destination = self.root / candidate_id
        key = b"isolated-test-integrity-key-32-bytes"
        self.materializer.materialize_bound(self.objects, self.commit, destination,
                                            candidate_id=candidate_id, integrity_key=key)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = create(str(destination), 0x00010000, 7, None, 3, 0x02200000, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            with self.assertRaises(MaterializationError):
                self.materializer.read_binding(destination, expected_candidate_id=candidate_id, integrity_key=key)
        finally:
            kernel.CloseHandle(handle)
        record = self.materializer.read_binding(destination, expected_candidate_id=candidate_id, integrity_key=key)
        self.assertEqual(record["candidate_id"], candidate_id)

    @unittest.skipUnless(os.name == "nt", "Windows binding reparse rejection")
    def test_windows_binding_rejects_junction_ancestor(self) -> None:
        candidate_id = "12345678-1234-4234-8234-123456789abc"
        key = b"isolated-test-integrity-key-32-bytes"
        target = self.root / "binding-target"
        target.mkdir()
        destination = target / candidate_id
        self.materializer.materialize_bound(self.objects, self.commit, destination,
                                            candidate_id=candidate_id, integrity_key=key)
        linked = self.root / "binding-junction"
        command = os.environ.get("COMSPEC", "cmd.exe")
        result = subprocess.run([command, "/d", "/c", "mklink", "/J", str(linked), str(target)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                check=False)
        if result.returncode != 0:
            self.skipTest("Windows junction creation is unavailable")
        with self.assertRaises(MaterializationError):
            self.materializer.read_binding(linked / candidate_id, expected_candidate_id=candidate_id, integrity_key=key)
        self.assertTrue((destination / ".self-evolution-binding.json").is_file())

    def test_materialize_bound_rejects_binding_tampering_and_replacement(self) -> None:
        destination = self.root / "12345678-1234-4234-8234-123456789abc"
        key = b"isolated-test-integrity-key-32-bytes"
        self.materializer.materialize_bound(
            self.objects, self.commit, destination,
            candidate_id="12345678-1234-4234-8234-123456789abc", integrity_key=key,
        )
        binding_path = destination / ".self-evolution-binding.json"
        binding_path.write_text(binding_path.read_text(encoding="utf-8").replace(self.commit, "0" * len(self.commit)), encoding="utf-8")
        with self.assertRaises(MaterializationError) as caught:
            self.materializer.read_binding(destination, expected_candidate_id="12345678-1234-4234-8234-123456789abc", integrity_key=key)
        self.assertEqual(caught.exception.code, "binding_tampered")
    def test_binding_rejects_changed_expected_id_and_renamed_same_directory(self) -> None:
        candidate_id = "12345678-1234-4234-8234-123456789abc"
        other_id = "12345678-1234-4234-8234-123456789abd"
        destination = self.root / candidate_id
        key = b"isolated-test-integrity-key-32-bytes"
        self.materializer.materialize_bound(self.objects, self.commit, destination,
                                            candidate_id=candidate_id, integrity_key=key)
        with self.assertRaises(MaterializationError) as mismatch:
            self.materializer.read_binding(destination, expected_candidate_id=other_id, integrity_key=key)
        self.assertEqual(mismatch.exception.code, "binding_identity_mismatch")
        renamed = self.root / other_id
        destination.rename(renamed)
        for expected in (candidate_id, other_id):
            with self.subTest(expected=expected), self.assertRaises(MaterializationError) as mismatch:
                self.materializer.read_binding(renamed, expected_candidate_id=expected, integrity_key=key)
            self.assertEqual(mismatch.exception.code, "binding_identity_mismatch")
        self.assertTrue((renamed / "src" / "main.py").is_file())

    def test_binding_rejects_same_directory_moved_to_another_parent_with_same_name(self) -> None:
        candidate_id = "12345678-1234-4234-8234-123456789abc"
        key = b"isolated-test-integrity-key-32-bytes"
        destination = self.root / candidate_id
        other_parent = self.root / "different-parent"
        other_parent.mkdir()
        self.materializer.materialize_bound(self.objects, self.commit, destination,
                                            candidate_id=candidate_id, integrity_key=key)
        moved = other_parent / candidate_id
        destination.rename(moved)
        with self.assertRaises(MaterializationError) as caught:
            self.materializer.read_binding(moved, expected_candidate_id=candidate_id, integrity_key=key)
        self.assertEqual(caught.exception.code, "binding_identity_mismatch")
        self.assertTrue((moved / "src" / "main.py").is_file())

    @unittest.skipUnless(os.name == "nt", "Windows directory replacement identity")
    def test_windows_binding_refuses_replacement_with_copied_valid_binding(self) -> None:
        candidate_id = "12345678-1234-4234-8234-123456789abc"
        destination = self.root / candidate_id
        original = self.root / "original-candidate"
        key = b"isolated-test-integrity-key-32-bytes"
        self.materializer.materialize_bound(self.objects, self.commit, destination,
                                            candidate_id=candidate_id, integrity_key=key)
        destination.rename(original)
        destination.mkdir()
        shutil.copyfile(original / ".self-evolution-binding.json", destination / ".self-evolution-binding.json")
        sentinel = destination / "keep.txt"
        sentinel.write_text("preserve", encoding="utf-8")
        with self.assertRaises(MaterializationError) as caught:
            self.materializer.read_binding(destination, expected_candidate_id=candidate_id, integrity_key=key)
        self.assertEqual(caught.exception.code, "binding_identity_mismatch")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
        self.assertTrue((original / "src" / "main.py").is_file())

    def test_cleanup_failure_before_publication_does_not_claim_publication_unknown(self) -> None:
        candidate_id = "12345678-1234-4234-8234-123456789abc"
        destination = self.root / candidate_id
        with mock.patch.object(self.materializer, "_read_commit_tree", side_effect=MaterializationError("invalid", code="invalid_commit")):
            with mock.patch("self_evolution_materializer._cleanup_temporary_tree", side_effect=OSError("injected")):
                with self.assertRaises(MaterializationError) as caught:
                    self.materializer.materialize_bound(self.objects, self.commit, destination, candidate_id=candidate_id,
                                                        integrity_key=b"isolated-test-integrity-key-32-bytes")
        self.assertEqual(caught.exception.code, "temporary_cleanup_failed")
        self.assertIsNone(caught.exception.publication_status)
        self.assertFalse(destination.exists())

    def test_materializes_commit_without_checkout_and_preserves_executable_mode(self) -> None:
        result_path = self.root / "candidate"
        result = self.materializer.materialize(self.objects, self.commit, result_path)
        self.assertEqual(result.commit_oid, self.commit)
        self.assertEqual(result.candidate_kind, "materialized_snapshot")
        self.assertEqual(result.tree_oid, self.git("-C", str(self.repo), "rev-parse", "HEAD^{tree}"))
        self.assertRegex(result.manifest_digest, r"\A[0-9a-f]{64}\Z")
        self.assertEqual((result_path / "src" / "main.py").read_bytes(), b"print('safe')\n")
        self.assertGreaterEqual(result.file_count, 1)
        self.assertEqual(result.total_bytes, len(b"print('safe')\n"))
        self.assertFalse((result_path / ".git").exists())

    def test_hostile_repository_filter_config_and_attributes_are_never_executed(self) -> None:
        sentinel = self.root / "filter-ran.txt"
        filter_script = self.root / "smudge.cmd"
        filter_script.write_text(f"@echo pwned>{sentinel}\r\n@type\r\n", encoding="utf-8")
        (self.repo / ".gitattributes").write_text("src/main.py filter=hostile\n", encoding="utf-8")
        self.git("-C", str(self.repo), "add", ".gitattributes")
        self.git("-C", str(self.repo), "commit", "--quiet", "-m", "attributes")
        self.commit = self.git("-C", str(self.repo), "rev-parse", "HEAD")
        self.git("-C", str(self.repo), "config", "filter.hostile.smudge", str(filter_script))
        self.git("-C", str(self.repo), "config", "filter.hostile.required", "true")
        include_script = self.root / "include-marker.txt"
        include_config = self.root / "hostile-include"
        include_config.write_text(f"[core]\n\tfsmonitor = {filter_script}\n", encoding="utf-8")
        self.git("-C", str(self.repo), "config", "include.path", str(include_config))

        destination = self.root / "candidate-hostile-config"
        self.materializer.materialize(self.objects, self.commit, destination)

        self.assertEqual((destination / "src" / "main.py").read_bytes(), b"print('safe')\n")
        self.assertFalse(sentinel.exists())
        self.assertFalse(include_script.exists())

    def test_rejects_symlink_gitlink_and_windows_unsafe_paths(self) -> None:
        blob = self.git("-C", str(self.repo), "hash-object", "-w", "--stdin")
        cases = [
            ("symlink", "120000", "link", blob),
            ("gitlink", "160000", "vendor", self.commit),
            ("windows_reserved", "100644", "CON.txt", blob),
            ("windows_ads", "100644", "stream:evil", blob),
        ]
        for name, mode, path, oid in cases:
            with self.subTest(name=name):
                self.git("-c", "core.protectNTFS=false", "-C", str(self.repo), "read-tree", "HEAD")
                self.git("-c", "core.protectNTFS=false", "-C", str(self.repo), "update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}")
                tree = self.git("-c", "core.protectNTFS=false", "-C", str(self.repo), "write-tree")
                commit_oid = self.git("-C", str(self.repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                                     "commit-tree", tree, "-m", name)
                destination = self.root / f"candidate-{name}"
                with self.assertRaises(MaterializationError):
                    self.materializer.materialize(self.objects, commit_oid, destination)
                self.assertFalse(destination.exists())

    def test_rejects_existing_destination_and_bounds_blob_bytes(self) -> None:
        destination = self.root / "occupied"
        destination.mkdir()
        (destination / "keep").write_text("untouched", encoding="utf-8")
        with self.assertRaises(MaterializationError) as caught:
            self.materializer.materialize(self.objects, self.commit, destination)
        self.assertEqual(caught.exception.code, "destination_exists")
        self.assertEqual((destination / "keep").read_text(encoding="utf-8"), "untouched")

        bounded = _IsolatedTestMaterializer(self.git_executable, max_object_bytes=2)
        with self.assertRaises(MaterializationError) as caught:
            bounded.materialize(self.objects, self.commit, self.root / "too-large")
        self.assertEqual(caught.exception.code, "blob_too_large")
        self.assertFalse((self.root / "too-large").exists())

    def test_rejects_object_alternates_and_never_reads_their_objects(self) -> None:
        alternate = self.root / "alternate-objects"
        alternate.mkdir()
        alternate_repo = self.root / "alternate-repo"
        alternate_repo.mkdir()
        self.git("init", "--quiet", str(alternate_repo))
        self.git("-C", str(alternate_repo), "config", "user.name", "Alt")
        self.git("-C", str(alternate_repo), "config", "user.email", "alt@example.invalid")
        (alternate_repo / "only-alternate").write_text("not permitted", encoding="utf-8")
        self.git("-C", str(alternate_repo), "add", ".")
        self.git("-C", str(alternate_repo), "commit", "--quiet", "-m", "alternate")
        alternate_commit = self.git("-C", str(alternate_repo), "rev-parse", "HEAD")
        alternate = Path(self.git("-C", str(alternate_repo), "rev-parse", "--git-path", "objects"))
        if not alternate.is_absolute():
            alternate = alternate_repo / alternate
        (self.objects / "info").mkdir(exist_ok=True)
        (self.objects / "info" / "alternates").write_text(str(alternate), encoding="utf-8")

        destination = self.root / "candidate-no-alternate"
        with self.assertRaises(MaterializationError):
            self.materializer.materialize(self.objects, alternate_commit, destination)
        self.assertFalse(destination.exists())

    def test_rejects_casefold_collisions_and_object_store_size_overrun(self) -> None:
        blob = self.git("-C", str(self.repo), "hash-object", "-w", "--stdin")
        for name in ("Case.txt", "case.txt"):
            self.git("-c", "core.ignorecase=false", "-c", "core.protectNTFS=false", "-C", str(self.repo),
                     "update-index", "--add", "--cacheinfo", f"100644,{blob},{name}")
        tree = self.git("-c", "core.ignorecase=false", "-c", "core.protectNTFS=false", "-C", str(self.repo), "write-tree")
        collision_commit = self.git("-C", str(self.repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                                    "commit-tree", tree, "-m", "case collision")
        destination = self.root / "candidate-collision"
        with self.assertRaises(MaterializationError) as caught:
            self.materializer.materialize(self.objects, collision_commit, destination)
        self.assertEqual(caught.exception.code, "path_collision")
        self.assertFalse(destination.exists())

        tiny_store = _IsolatedTestMaterializer(self.git_executable, max_object_store_bytes=1)
        with self.assertRaises(MaterializationError) as caught:
            tiny_store.materialize(self.objects, self.commit, self.root / "candidate-store-limit")
        self.assertEqual(caught.exception.code, "object_store_too_large")
        self.assertFalse((self.root / "candidate-store-limit").exists())

    def test_reads_packed_objects_from_a_private_snapshot(self) -> None:
        self.git("-C", str(self.repo), "gc", "--prune=now")
        self.assertTrue(any((self.objects / "pack").glob("*.pack")))
        destination = self.root / "candidate-packed"
        result = self.materializer.materialize(self.objects, self.commit, destination)
        self.assertEqual((destination / "src" / "main.py").read_bytes(), b"print('safe')\n")
        self.assertGreater(result.object_store_bytes, 0)

    def test_detects_object_content_replacement_by_hash(self) -> None:
        import zlib

        loose = self.objects / self.commit[:2] / self.commit[2:]
        original = zlib.decompress(loose.read_bytes())
        corrupted = original.replace(b"fixture", b"hostile")
        self.assertNotEqual(original, corrupted)
        loose.chmod(stat.S_IWRITE | stat.S_IREAD)
        loose.write_bytes(zlib.compress(corrupted))
        with self.assertRaises(MaterializationError) as caught:
            self.materializer.materialize(self.objects, self.commit, self.root / "candidate-corrupt")
        self.assertEqual(caught.exception.code, "object_hash_mismatch")
        self.assertFalse((self.root / "candidate-corrupt").exists())

    def test_production_cleanup_gate_fails_closed_when_runtime_cannot_prove_safety(self) -> None:
        destination = self.root / "candidate-cleanup-unsafe"
        # No supported runtime currently proves the required ancestor and
        # scratch binding; rmtree's symlink flag is not sufficient.
        safe_cleanup = self.production_materializer._cleanup_is_race_safe()
        self.assertFalse(safe_cleanup)
        if safe_cleanup:
            result = self.production_materializer.materialize(self.objects, self.commit, destination)
            self.assertEqual(result.commit_oid, self.commit)
            self.assertTrue((destination / "src" / "main.py").is_file())
        else:
            with mock.patch("self_evolution_materializer.tempfile.mkdtemp",
                            side_effect=AssertionError("scratch must not be created")):
                with self.assertRaises(MaterializationError) as caught:
                    self.production_materializer.materialize(self.objects, self.commit, destination)
            self.assertEqual(caught.exception.code, "unsafe_cleanup_unavailable")
            self.assertFalse(destination.exists())

    def test_cleanup_gate_does_not_treat_rmtree_flag_as_ancestor_binding(self) -> None:
        # Model a runtime with symlink-safe rmtree, without bypassing the gate.
        # That capability does not bind the path's ancestors.
        with mock.patch.object(shutil.rmtree, "avoids_symlink_attacks", True):
            self.assertFalse(self.production_materializer._cleanup_is_race_safe())
            with mock.patch("self_evolution_materializer.tempfile.mkdtemp") as create:
                with self.assertRaises(MaterializationError) as caught:
                    self.production_materializer.materialize(
                        self.objects, self.commit, self.root / "candidate-closed-gate"
                    )
            self.assertEqual(caught.exception.code, "unsafe_cleanup_unavailable")
            create.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows native publication race")
    def test_windows_published_snapshot_can_move_with_pinned_parent(self) -> None:
        import self_evolution_materializer as materializer_module

        parent = self.root / "after-publish-parent"
        moved = self.root / "after-publish-moved"
        replacement = self.root / "after-publish-replacement"
        parent.mkdir()
        replacement.mkdir()
        stage = parent / "stage"
        stage.mkdir()
        (stage / "candidate.txt").write_text("published", encoding="utf-8")
        with materializer_module._WindowsDirectoryHandle.open(
            parent, desired_access=(materializer_module._WindowsDirectoryHandle._GENERIC_READ_ATTRIBUTES
                                    | materializer_module._WindowsDirectoryHandle._FILE_TRAVERSE
                                    | materializer_module._WindowsDirectoryHandle._FILE_ADD_SUBDIRECTORY)
        ) as parent_handle:
            with materializer_module._WindowsDirectoryHandle.open(
                stage, desired_access=(materializer_module._WindowsDirectoryHandle._DELETE
                                       | materializer_module._WindowsDirectoryHandle._GENERIC_READ_ATTRIBUTES
                                       | materializer_module._WindowsDirectoryHandle._SYNCHRONIZE)
            ) as stage_handle:
                parent_handle.rename_handle_noreplace(stage_handle, "candidate")
            parent.rename(moved)
            replacement.rename(parent)
            self.assertNotEqual(parent_handle.identity, self.materializer._directory_identity(parent))

        self.assertEqual((moved / "candidate" / "candidate.txt").read_text(encoding="utf-8"), "published")
        self.assertFalse((parent / "candidate").exists())

    @unittest.skipUnless(os.name == "nt", "Windows native ancestor replacement race")
    def test_windows_parent_identity_check_does_not_bind_later_cleanup_path(self) -> None:
        parent = self.root / "cleanup-parent"
        moved = self.root / "cleanup-parent-moved"
        replacement = self.root / "cleanup-parent-replacement"
        parent.mkdir()
        replacement.mkdir()
        scratch_name = ".self-evolution-materializer-fixture"
        (parent / scratch_name).mkdir()
        replacement_scratch = replacement / scratch_name
        replacement_scratch.mkdir()
        (replacement_scratch / "sentinel").write_text("must survive", encoding="utf-8")

        identity = self.materializer._directory_identity(parent)
        self.assertEqual(self.materializer._directory_identity(parent), identity)
        # Actual filesystem swap after the check, before any would-be cleanup.
        parent.rename(moved)
        replacement.rename(parent)
        redirected = parent / scratch_name
        self.assertNotEqual(self.materializer._directory_identity(parent), identity)
        self.assertEqual((redirected / "sentinel").read_text(encoding="utf-8"), "must survive")
        self.assertTrue((moved / scratch_name).is_dir())
        # Production gate must reject before creation or deletion, even in the
        # presence of a real replacement tree. The spy never performs deletion.
        with mock.patch("self_evolution_materializer.shutil.rmtree") as cleanup:
            with mock.patch("self_evolution_materializer.tempfile.mkdtemp") as create:
                with self.assertRaises(MaterializationError) as caught:
                    self.production_materializer.materialize(self.objects, self.commit, parent / "candidate")
        self.assertEqual(caught.exception.code, "unsafe_cleanup_unavailable")
        cleanup.assert_not_called()
        create.assert_not_called()
        self.assertEqual((redirected / "sentinel").read_text(encoding="utf-8"), "must survive")

    def test_directory_publication_does_not_replace_racing_destination(self) -> None:
        import self_evolution_materializer as materializer_module

        stage = self.root / "stage"
        stage.mkdir()
        (stage / "candidate.txt").write_text("candidate", encoding="utf-8")
        destination = self.root / "published"
        original_publish = materializer_module._publish_directory_noreplace

        def create_destination_then_publish(source, target):
            Path(target).mkdir()
            return original_publish(source, target)

        with mock.patch.object(materializer_module, "_publish_directory_noreplace",
                               side_effect=create_destination_then_publish):
            with self.assertRaises(MaterializationError) as caught:
                materializer_module._publish_directory_noreplace(stage, destination)

        self.assertEqual(caught.exception.code, "destination_race")
        self.assertTrue(destination.is_dir())
        self.assertEqual(list(destination.iterdir()), [])
        self.assertEqual((stage / "candidate.txt").read_text(encoding="utf-8"), "candidate")

    def test_parent_identity_drift_aborts_before_publishing(self) -> None:
        import self_evolution_materializer as materializer_module

        destination = self.root / "candidate-parent-drift"
        original = materializer_module.SafeSnapshotMaterializer._directory_identity
        calls = 0

        def drifting_identity(path):
            nonlocal calls
            identity = original(path)
            if Path(path) == self.root:
                calls += 1
                if calls >= 2:
                    return (identity[0], identity[1] + 1)
            return identity

        with mock.patch.object(materializer_module.SafeSnapshotMaterializer, "_directory_identity", side_effect=drifting_identity):
            with self.assertRaises(MaterializationError) as caught:
                self.materializer.materialize(self.objects, self.commit, destination)

        self.assertEqual(caught.exception.code, "parent_identity_changed")
        self.assertFalse(destination.exists())

    def test_snapshot_identity_is_path_independent_and_reparse_parent_is_rejected(self) -> None:
        first = self.materializer.materialize(self.objects, self.commit, self.root / "candidate-identity-a")
        second = self.materializer.materialize(self.objects, self.commit, self.root / "candidate-identity-b")
        self.assertEqual(first.manifest_digest, second.manifest_digest)
        self.assertEqual(first.tree_oid, second.tree_oid)

        real_parent = self.root / "real-parent"
        real_parent.mkdir()
        linked_parent = self.root / "linked-parent"
        try:
            linked_parent.symlink_to(real_parent, target_is_directory=True)
        except OSError as symlink_error:
            # Windows often disables unprivileged directory symlinks; a junction
            # is also a real reparse point and does not require Developer Mode.
            if os.name == "nt":
                command = os.environ.get("COMSPEC", "cmd.exe")
                result = subprocess.run(
                    [command, "/d", "/c", "mklink", "/J", str(linked_parent), str(real_parent)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False,
                )
                if result.returncode != 0:
                    self.skipTest(f"directory symlink/junction unavailable: {type(symlink_error).__name__}")
            else:
                self.skipTest(f"directory symlink unavailable: {type(symlink_error).__name__}")
        with self.assertRaises(MaterializationError) as caught:
            self.materializer.materialize(self.objects, self.commit, linked_parent / "candidate")
        self.assertEqual(caught.exception.code, "linked_path")
        self.assertFalse((real_parent / "candidate").exists())

    @unittest.skipUnless(os.name == "nt", "Windows native directory-handle validation")
    def test_windows_directory_handle_remains_bound_after_path_replacement(self) -> None:
        import self_evolution_materializer as materializer_module

        path = self.root / "handle-bound-parent"
        moved = self.root / "handle-bound-parent-moved"
        replacement = self.root / "handle-bound-parent-replacement"
        path.mkdir()
        replacement.mkdir()
        with materializer_module._WindowsDirectoryHandle.open(path) as opened:
            original_identity = opened.identity
            path.rename(moved)
            replacement.rename(path)
            self.assertEqual(opened.identity, original_identity)
            with materializer_module._WindowsDirectoryHandle.open(path) as current:
                self.assertNotEqual(current.identity, original_identity)

    @unittest.skipUnless(os.name == "nt", "Windows native directory-handle validation")
    def test_windows_handle_relative_publish_targets_pinned_parent_after_path_replacement(self) -> None:
        import self_evolution_materializer as materializer_module

        parent = self.root / "publish-parent"
        moved_parent = self.root / "publish-parent-pinned"
        replacement_parent = self.root / "publish-parent-replacement"
        parent.mkdir()
        replacement_parent.mkdir()
        stage = parent / "stage"
        stage.mkdir()
        (stage / "candidate.txt").write_text("pinned", encoding="utf-8")

        with materializer_module._WindowsDirectoryHandle.open(
            parent, desired_access=(materializer_module._WindowsDirectoryHandle._GENERIC_READ_ATTRIBUTES
                                    | materializer_module._WindowsDirectoryHandle._FILE_TRAVERSE
                                    | materializer_module._WindowsDirectoryHandle._FILE_ADD_SUBDIRECTORY)
        ) as parent_handle:
            parent.rename(moved_parent)
            replacement_parent.rename(parent)
            # Open the source through the renamed original parent after its
            # former path has been occupied by an attacker-controlled directory.
            with materializer_module._WindowsDirectoryHandle.open(
                moved_parent / "stage", desired_access=(materializer_module._WindowsDirectoryHandle._DELETE
                    | materializer_module._WindowsDirectoryHandle._GENERIC_READ_ATTRIBUTES
                    | materializer_module._WindowsDirectoryHandle._SYNCHRONIZE)
            ) as stage_handle:
                parent_handle.rename_handle_noreplace(stage_handle, "published")

        self.assertEqual((moved_parent / "published" / "candidate.txt").read_text(encoding="utf-8"), "pinned")
        self.assertFalse((parent / "published").exists())
        self.assertTrue(parent.is_dir())

    @unittest.skipUnless(os.name == "nt", "Windows native directory-handle validation")
    def test_windows_handle_relative_publish_refuses_existing_child(self) -> None:
        import self_evolution_materializer as materializer_module

        parent = self.root / "publish-no-replace"
        parent.mkdir()
        stage = parent / "stage"
        stage.mkdir()
        occupied = parent / "occupied"
        occupied.mkdir()
        (occupied / "keep.txt").write_text("keep", encoding="utf-8")

        with materializer_module._WindowsDirectoryHandle.open(
            parent, desired_access=(materializer_module._WindowsDirectoryHandle._GENERIC_READ_ATTRIBUTES
                                    | materializer_module._WindowsDirectoryHandle._FILE_TRAVERSE
                                    | materializer_module._WindowsDirectoryHandle._FILE_ADD_SUBDIRECTORY)
        ) as parent_handle:
            with materializer_module._WindowsDirectoryHandle.open(
                stage, desired_access=(materializer_module._WindowsDirectoryHandle._DELETE
                    | materializer_module._WindowsDirectoryHandle._GENERIC_READ_ATTRIBUTES
                    | materializer_module._WindowsDirectoryHandle._SYNCHRONIZE)
            ) as stage_handle:
                with self.assertRaises(MaterializationError) as caught:
                    parent_handle.rename_handle_noreplace(stage_handle, "occupied")

        self.assertEqual(caught.exception.code, "destination_race")
        self.assertTrue(stage.is_dir())
        self.assertEqual((occupied / "keep.txt").read_text(encoding="utf-8"), "keep")

    @unittest.skipUnless(os.name == "nt", "Windows handle-relative file creation")
    def test_windows_handle_relative_file_create_write_and_readback(self) -> None:
        import self_evolution_materializer as materializer_module

        parent = self.root / "relative-file-parent"
        parent.mkdir()
        payload = b"candidate bytes\x00with binary data"
        with materializer_module._WindowsDirectoryHandle.open(parent) as parent_handle:
            materializer_module._windows_create_file_relative(parent_handle._handle, "candidate.bin", payload)
        self.assertEqual((parent / "candidate.bin").read_bytes(), payload)

    @unittest.skipUnless(os.name == "nt", "Windows handle-relative file creation race")
    def test_windows_handle_relative_file_creation_stays_on_pinned_parent_after_replacement(self) -> None:
        import self_evolution_materializer as materializer_module

        parent = self.root / "relative-file-race-parent"
        moved = self.root / "relative-file-race-original"
        replacement = self.root / "relative-file-race-replacement"
        parent.mkdir()
        replacement.mkdir()
        sentinel = replacement / "sentinel.bin"
        sentinel.write_bytes(b"must remain untouched")
        with materializer_module._WindowsDirectoryHandle.open(parent) as parent_handle:
            parent.rename(moved)
            replacement.rename(parent)
            materializer_module._windows_create_file_relative(parent_handle._handle, "created.bin", b"bound")
        self.assertEqual((moved / "created.bin").read_bytes(), b"bound")
        self.assertFalse((parent / "created.bin").exists())
        self.assertEqual((parent / sentinel.name).read_bytes(), b"must remain untouched")

    @unittest.skipUnless(os.name == "nt", "Windows handle-relative file reparse rejection")
    def test_windows_handle_relative_file_creation_rejects_reparse_parent_without_touching_target(self) -> None:
        import self_evolution_materializer as materializer_module

        target = self.root / "relative-file-reparse-target"
        target.mkdir()
        sentinel = target / "sentinel.bin"
        sentinel.write_bytes(b"preserve")
        parent = self.root / "relative-file-reparse-parent"
        parent.mkdir()
        linked = parent / "linked"
        command = os.environ.get("COMSPEC", "cmd.exe")
        result = subprocess.run(
            [command, "/d", "/c", "mklink", "/J", str(linked), str(target)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if result.returncode != 0:
            self.skipTest("Windows junction creation is unavailable")
        with materializer_module._WindowsDirectoryHandle.open(parent) as parent_handle:
            with self.assertRaises(MaterializationError) as caught:
                materializer_module._windows_create_path_relative(
                    parent_handle._handle, ["linked", "escape.bin"], b"must not escape",
                )
        self.assertEqual(caught.exception.code, "linked_path")
        self.assertFalse((target / "escape.bin").exists())
        self.assertEqual(sentinel.read_bytes(), b"preserve")

    @unittest.skipUnless(os.name == "nt", "Windows handle-relative file creation refusal")
    def test_windows_handle_relative_file_creation_refuses_occupied_name_without_following(self) -> None:
        import self_evolution_materializer as materializer_module

        parent = self.root / "relative-file-occupied-parent"
        parent.mkdir()
        existing = parent / "occupied.bin"
        existing.write_bytes(b"preserve")
        with materializer_module._WindowsDirectoryHandle.open(parent) as parent_handle:
            with self.assertRaises(MaterializationError) as caught:
                materializer_module._windows_create_file_relative(parent_handle._handle, existing.name, b"overwrite")
        self.assertEqual(caught.exception.code, "destination_exists")
        self.assertEqual(existing.read_bytes(), b"preserve")

    @unittest.skipUnless(os.name == "nt", "Windows native directory-handle validation")
    def test_windows_directory_handle_rejects_reparse_point(self) -> None:
        import self_evolution_materializer as materializer_module

        target = self.root / "handle-reparse-target"
        target.mkdir()
        linked = self.root / "handle-reparse-link"
        command = os.environ.get("COMSPEC", "cmd.exe")
        result = subprocess.run(
            [command, "/d", "/c", "mklink", "/J", str(linked), str(target)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode != 0:
            self.skipTest("Windows junction creation is unavailable")
        with self.assertRaises(MaterializationError) as caught:
            materializer_module._WindowsDirectoryHandle.open(linked)
        self.assertEqual(caught.exception.code, "linked_path")
    def test_temporary_cleanup_failure_is_not_silently_reported_as_success(self) -> None:
        destination = self.root / "candidate-cleanup-failure"
        original_rmtree = shutil.rmtree
        patch_target = ("self_evolution_materializer._windows_mark_delete" if os.name == "nt"
                        else "self_evolution_materializer.shutil.rmtree")
        with mock.patch(patch_target, side_effect=OSError("injected cleanup failure")):
            with self.assertRaises(MaterializationError) as caught:
                self.materializer.materialize(self.objects, self.commit, destination)
        self.assertEqual(caught.exception.code, "publication_unknown")
        self.assertEqual(caught.exception.publication_status, "unknown")
        # Publication is atomic; cleanup failure does not remove/overwrite a published candidate.
        self.assertTrue((destination / "src" / "main.py").is_file())
        for temp_root in self.root.glob(".self-evolution-materializer-*"):
            original_rmtree(temp_root)

    def test_rejects_invalid_commit_id_without_creating_destination(self) -> None:
        destination = self.root / "candidate-invalid-oid"
        with self.assertRaises(MaterializationError) as caught:
            self.materializer.materialize(self.objects, "../not-an-object", destination)
        self.assertEqual(caught.exception.code, "invalid_object_id")
        self.assertFalse(destination.exists())




    @unittest.skipUnless(os.name == "nt", "Windows handle-relative cleanup")
    def test_windows_cleanup_deletes_only_regular_tree_bound_to_root_handle(self) -> None:
        import self_evolution_materializer as materializer_module

        with tempfile.TemporaryDirectory(prefix="isolated-cleanup-") as isolated_name:
            isolated = Path(isolated_name)
            scratch = isolated / "scratch"
            scratch.mkdir()
            (scratch / "nested").mkdir()
            (scratch / "nested" / "owned.bin").write_bytes(b"owned")
            (scratch / "empty").mkdir()
            root_identity = self.materializer._directory_identity(scratch)
            untouched = isolated / "outside.txt"
            untouched.write_text("leave intact", encoding="utf-8")

            materializer_module._cleanup_temporary_tree(
                scratch, expected_parent_identity=self.materializer._directory_identity(isolated),
                expected_root_identity=root_identity,
            )

            self.assertFalse(scratch.exists())
            self.assertEqual(untouched.read_text(encoding="utf-8"), "leave intact")

    @unittest.skipUnless(os.name == "nt", "Windows handle-relative cleanup hard-link refusal")
    def test_windows_cleanup_refuses_hard_link_without_deleting_either_name(self) -> None:
        import self_evolution_materializer as materializer_module

        with tempfile.TemporaryDirectory(prefix="isolated-cleanup-hardlink-") as isolated_name:
            isolated = Path(isolated_name)
            scratch = isolated / "scratch"
            scratch.mkdir()
            outside = isolated / "outside.bin"
            outside.write_bytes(b"do not delete")
            alias = scratch / "alias.bin"
            try:
                os.link(outside, alias)
            except OSError as exc:
                self.skipTest(f"hard-link creation unavailable: {exc}")
            root_identity = self.materializer._directory_identity(scratch)

            with self.assertRaises(MaterializationError) as caught:
                materializer_module._cleanup_temporary_tree(
                    scratch, expected_parent_identity=self.materializer._directory_identity(isolated),
                    expected_root_identity=root_identity,
                )

            self.assertEqual(caught.exception.code, "cleanup_hard_link")
            self.assertTrue(scratch.is_dir())
            self.assertEqual(alias.read_bytes(), b"do not delete")
            self.assertEqual(outside.read_bytes(), b"do not delete")

    @unittest.skipUnless(os.name == "nt", "Windows handle-relative cleanup reparse refusal")
    def test_windows_cleanup_refuses_reparse_without_following_or_deleting_target(self) -> None:
        import self_evolution_materializer as materializer_module

        with tempfile.TemporaryDirectory(prefix="isolated-cleanup-reparse-") as isolated_name:
            isolated = Path(isolated_name)
            scratch = isolated / "scratch"
            scratch.mkdir()
            target = isolated / "outside-target"
            target.mkdir()
            sentinel = target / "sentinel.bin"
            sentinel.write_bytes(b"outside")
            link = scratch / "junction"
            command = os.environ.get("COMSPEC", "cmd.exe")
            completed = subprocess.run(
                [command, "/d", "/c", "mklink", "/J", str(link), str(target)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            )
            if completed.returncode:
                self.skipTest("junction creation is unavailable in this Windows environment")
            root_identity = self.materializer._directory_identity(scratch)

            with self.assertRaises(MaterializationError) as caught:
                materializer_module._cleanup_temporary_tree(
                    scratch, expected_parent_identity=self.materializer._directory_identity(isolated),
                    expected_root_identity=root_identity,
                )

            self.assertEqual(caught.exception.code, "cleanup_reparse")
            self.assertTrue(scratch.is_dir())
            self.assertTrue(link.exists())
            self.assertEqual(sentinel.read_bytes(), b"outside")

    @unittest.skipUnless(os.name == "nt", "Windows cleanup dynamic-entry race")
    def test_windows_cleanup_fails_closed_and_reports_remaining_tree_after_concurrent_add(self) -> None:
        import self_evolution_materializer as materializer_module

        with tempfile.TemporaryDirectory(prefix="isolated-cleanup-dynamic-") as isolated_name:
            isolated = Path(isolated_name)
            scratch = isolated / "scratch"
            scratch.mkdir()
            (scratch / "planned.bin").write_bytes(b"planned")
            outside = isolated / "outside.bin"
            outside.write_bytes(b"untouched")
            root_identity = self.materializer._directory_identity(scratch)
            real_disposition = materializer_module._windows_mark_delete
            injected = False

            def race_during_delete(handle):
                nonlocal injected
                real_disposition(handle)
                if not injected:
                    injected = True
                    (scratch / "late.bin").write_bytes(b"late arrival")

            with mock.patch.object(materializer_module, "_windows_mark_delete", side_effect=race_during_delete):
                with self.assertRaises(MaterializationError) as caught:
                    materializer_module._cleanup_temporary_tree(
                        scratch, expected_parent_identity=self.materializer._directory_identity(isolated),
                        expected_root_identity=root_identity,
                    )

            self.assertEqual(caught.exception.code, "cleanup_directory_changed")
            self.assertTrue(scratch.is_dir())
            self.assertEqual((scratch / "late.bin").read_bytes(), b"late arrival")
            self.assertEqual(outside.read_bytes(), b"untouched")

    @unittest.skipUnless(os.name == "nt", "Windows cleanup root substitution race")
    def test_windows_cleanup_keeps_replacement_path_and_external_sentinel(self) -> None:
        import self_evolution_materializer as materializer_module

        with tempfile.TemporaryDirectory(prefix="isolated-cleanup-race-") as isolated_name:
            isolated = Path(isolated_name)
            scratch = isolated / "scratch"
            moved = isolated / "moved-original"
            replacement = isolated / "replacement"
            scratch.mkdir()
            (scratch / "owned.bin").write_bytes(b"owned")
            replacement.mkdir()
            sentinel = replacement / "sentinel.bin"
            sentinel.write_bytes(b"must survive")
            expected_root = self.materializer._directory_identity(scratch)
            enumerate_real = materializer_module._manifest._windows_enumerate_directory
            raced = False

            def swap_after_root_open(handle):
                nonlocal raced
                if not raced:
                    raced = True
                    try:
                        scratch.rename(moved)
                        replacement.rename(scratch)
                    except OSError:
                        # The pinned root handle denies delete-sharing, so the
                        # attack cannot redirect enumeration or disposition.
                        pass
                return enumerate_real(handle)

            with mock.patch.object(materializer_module._manifest, "_windows_enumerate_directory",
                                   side_effect=swap_after_root_open):
                materializer_module._cleanup_temporary_tree(
                    scratch, expected_parent_identity=self.materializer._directory_identity(isolated),
                    expected_root_identity=expected_root,
                )

            self.assertTrue(raced)
            self.assertFalse(scratch.exists())
            self.assertEqual((replacement / "sentinel.bin").read_bytes(), b"must survive")
            self.assertFalse(moved.exists())


    @unittest.skipUnless(os.name == "nt", "Windows native cleanup ancestor-race test")
    def test_windows_cleanup_pins_ancestor_chain_and_preserves_replacement_tree(self) -> None:
        import self_evolution_materializer as materializer_module

        with tempfile.TemporaryDirectory(prefix="isolated-cleanup-ancestor-") as isolated_name:
            isolated = Path(isolated_name)
            parent = isolated / "parent"
            moved = isolated / "original-parent"
            replacement = isolated / "replacement-parent"
            parent.mkdir()
            replacement.mkdir()
            scratch = parent / "scratch"
            scratch.mkdir()
            (scratch / "owned.tmp").write_text("owned", encoding="utf-8")
            replacement_scratch = replacement / scratch.name
            replacement_scratch.mkdir()
            sentinel = replacement_scratch / "sentinel.txt"
            sentinel.write_text("must survive", encoding="utf-8")
            root_id = self.materializer._directory_identity(scratch)
            parent_id = self.materializer._directory_identity(parent)
            import contextlib
            real_open = materializer_module._windows_open_relative_for_delete
            attempted = False
            swapped = False

            @contextlib.contextmanager
            def swap_before_scratch_open(parent_handle, name, *, directory):
                nonlocal attempted, swapped
                if name == scratch.name and not attempted:
                    attempted = True
                    parent.rename(moved)
                    replacement.rename(parent)
                    swapped = True
                with real_open(parent_handle, name, directory=directory) as result:
                    yield result

            with mock.patch.object(materializer_module, "_windows_open_relative_for_delete",
                                   side_effect=swap_before_scratch_open):
                materializer_module._cleanup_temporary_tree(
                    scratch, expected_parent_identity=parent_id, expected_root_identity=root_id,
                )
            self.assertTrue(attempted)
            self.assertTrue(swapped, "the test must actually replace the ancestor")
            self.assertEqual((parent / scratch.name / "sentinel.txt").read_text(encoding="utf-8"), "must survive")
            self.assertFalse((moved / scratch.name).exists())

    @unittest.skipUnless(os.name == "nt", "Windows preflight directory identity race")
    def test_windows_cleanup_refuses_replaced_intermediate_directory_at_each_depth(self) -> None:
        import contextlib
        import self_evolution_materializer as materializer_module

        for depth in ("outer", "inner"):
            with self.subTest(replaced=depth), tempfile.TemporaryDirectory(prefix="isolated-cleanup-dir-swap-") as isolated_name:
                isolated = Path(isolated_name)
                scratch = isolated / "scratch"
                original = scratch / "outer" / "inner"
                original.mkdir(parents=True)
                payload = original / "planned.bin"
                payload.write_bytes(b"must not be deleted")
                replacement = isolated / "replacement"
                replacement.mkdir()
                (replacement / "not-preflight.txt").write_bytes(b"unbound sentinel")
                moved = isolated / "original-directory"
                parent_id = self.materializer._directory_identity(isolated)
                root_id = self.materializer._directory_identity(scratch)
                real_open = materializer_module._windows_open_relative_for_delete
                swapped = False

                @contextlib.contextmanager
                def swap_before_open(parent_handle, name, *, directory):
                    nonlocal swapped
                    if name == depth and not swapped:
                        swapped = True
                        if depth == "outer":
                            (scratch / "outer").rename(moved)
                            (moved / "inner").rename(replacement / "inner")
                            replacement.rename(scratch / "outer")
                        else:
                            original.rename(moved)
                            (moved / "planned.bin").rename(replacement / "planned.bin")
                            replacement.rename(original)
                    with real_open(parent_handle, name, directory=directory) as result:
                        yield result

                with mock.patch.object(materializer_module, "_windows_open_relative_for_delete", side_effect=swap_before_open):
                    with self.assertRaises(MaterializationError) as caught:
                        materializer_module._cleanup_temporary_tree(
                            scratch, expected_parent_identity=parent_id, expected_root_identity=root_id,
                        )
                self.assertTrue(swapped)
                self.assertEqual(caught.exception.code, "cleanup_identity_changed")
                self.assertEqual(payload.read_bytes(), b"must not be deleted")
                replacement_in_tree = scratch / "outer" if depth == "outer" else original
                self.assertEqual((replacement_in_tree / "not-preflight.txt").read_bytes(), b"unbound sentinel")
                self.assertTrue(moved.is_dir())


if __name__ == "__main__":
    unittest.main()
