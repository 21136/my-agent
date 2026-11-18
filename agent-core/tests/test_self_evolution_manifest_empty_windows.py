from __future__ import annotations

import hashlib
import ctypes
from unittest import mock
import os
from pathlib import Path
import sys
import tempfile
import unittest

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

import self_evolution_manifest as manifest
import self_evolution_snapshot_review as review


@unittest.skipUnless(os.name == "nt", "Windows native stream behavior")
class WindowsEmptyFileManifestTests(unittest.TestCase):
    def test_real_empty_file_is_captured_with_empty_digest(self):
        with tempfile.TemporaryDirectory(prefix="manifest-empty-file-") as temp:
            root = Path(temp)
            (root / "empty.txt").write_bytes(b"")
            captured = manifest.capture_tree_manifest(root)
            entry, = captured.entries
            self.assertEqual(entry.path, "empty.txt")
            self.assertEqual(entry.size, 0)
            self.assertEqual(entry.sha256, hashlib.sha256(b"").hexdigest())

    def test_full_tree_baseline_freeze_includes_real_empty_file(self):
        with tempfile.TemporaryDirectory(prefix="manifest-freeze-empty-") as temp:
            root = Path(temp)
            (root / "empty.txt").write_bytes(b"")
            (root / "normal.txt").write_bytes(b"normal\n")
            baseline = review.freeze_baseline(candidate_root=root, candidate_id="fixture")
            files = {item.path: item for item in baseline.files}
            self.assertTrue(baseline.full_tree)
            self.assertEqual(files["empty.txt"].content, b"")
            self.assertEqual(files["empty.txt"].sha256, hashlib.sha256(b"").hexdigest())
            self.assertEqual(files["normal.txt"].content, b"normal\n")

    def test_real_empty_file_with_named_stream_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="manifest-empty-ads-") as temp:
            root = Path(temp)
            path = root / "empty.txt"
            path.write_bytes(b"")
            Path(str(path) + ":hidden").write_bytes(b"secret")
            with self.assertRaisesRegex(manifest.ManifestError, "non_default_stream_detected"):
                manifest.capture_tree_manifest(root)

    def test_fallback_refuses_named_stream_even_if_backup_read_omits_all_entries(self):
        with tempfile.TemporaryDirectory(prefix="manifest-empty-ads-hidden-") as temp:
            root = Path(temp)
            path = root / "empty.txt"
            path.write_bytes(b"")
            Path(str(path) + ":hidden").write_bytes(b"secret")
            with manifest._windows_open_trusted_root(root) as (_, parent, _, _):
                with manifest._windows_open_relative(parent, path.name, directory=False) as handle:
                    def omitted(api, handle, buf, size, transferred, abort, security, context):
                        ctypes.cast(transferred, ctypes.POINTER(ctypes.c_ulong)).contents.value = 0
                        return True
                    with mock.patch.object(manifest, "_windows_backup_read_call", side_effect=omitted):
                        with self.assertRaisesRegex(manifest.ManifestError, "non_default_stream_detected"):
                            manifest._windows_default_streams(handle, directory=False)

    def test_missing_or_truncated_stream_query_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="manifest-truncated-info-") as temp:
            root = Path(temp)
            path = root / "empty.txt"
            path.write_bytes(b"")
            with manifest._windows_open_trusted_root(root) as (_, parent, _, _):
                with manifest._windows_open_relative(parent, path.name, directory=False) as handle:
                    def omitted(api, handle, buf, size, transferred, abort, security, context):
                        ctypes.cast(transferred, ctypes.POINTER(ctypes.c_ulong)).contents.value = 0
                        return True
                    with mock.patch.object(manifest, "_windows_backup_read_call", side_effect=omitted):
                        for payload in (b"", b"\0" * 23, b"\0" * 4 + (14).to_bytes(4, "little") + b"\0" * 16):
                            with self.subTest(payload=payload):
                                with mock.patch.object(manifest, "_windows_file_stream_info", return_value=payload, create=True):
                                    with self.assertRaises(manifest.ManifestError):
                                        manifest._windows_default_streams(handle, directory=False)

    def test_fallback_refuses_nonempty_file_if_backup_read_omits_all_entries(self):
        with tempfile.TemporaryDirectory(prefix="manifest-nonempty-missing-") as temp:
            root = Path(temp)
            path = root / "payload.txt"
            path.write_bytes(b"x")
            with manifest._windows_open_trusted_root(root) as (_, parent, _, _):
                with manifest._windows_open_relative(parent, path.name, directory=False) as handle:
                    def omitted(api, handle, buf, size, transferred, abort, security, context):
                        ctypes.cast(transferred, ctypes.POINTER(ctypes.c_ulong)).contents.value = 0
                        return True
                    with mock.patch.object(manifest, "_windows_backup_read_call", side_effect=omitted):
                        with self.assertRaisesRegex(manifest.ManifestError, "default_stream_unavailable"):
                            manifest._windows_default_streams(handle, directory=False)


if __name__ == "__main__":
    unittest.main()
