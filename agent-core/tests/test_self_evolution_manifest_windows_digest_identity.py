from __future__ import annotations

import ctypes
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

import self_evolution_manifest as manifest  # noqa: E402


@unittest.skipUnless(os.name == "nt", "Windows handle digest contract")
class WindowsDigestIdentityTests(unittest.TestCase):
    def test_rejects_identity_change_between_pre_read_and_post_read_checks(self) -> None:
        read_started = False
        before_identity = (7, b"before")
        after_identity = (7, b"after!")
        info = {
            "size": 7,
            "write_time": 100,
            "change_time": 200,
            "links": 1,
            "delete_pending": False,
            "directory": False,
        }

        def handle_info(_handle):
            return (after_identity if read_started else before_identity), dict(info)

        class ReadTrackingStream(io.BytesIO):
            def read(self, *args):
                nonlocal read_started
                read_started = True
                return super().read(*args)

        kernel = mock.Mock()
        kernel.DuplicateHandle.return_value = True
        kernel.GetCurrentProcess.return_value = 1
        fake_msvcrt = mock.Mock()
        fake_msvcrt.open_osfhandle.return_value = 17
        expected = dict(info)

        with mock.patch.object(ctypes, "WinDLL", return_value=kernel), \
                mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}), \
                mock.patch.object(manifest.os, "fdopen", return_value=ReadTrackingStream(b"payload")), \
                mock.patch.object(manifest, "_windows_handle_info", side_effect=handle_info):
            with self.assertRaisesRegex(manifest.ManifestError, "^file_changed_during_sweep$"):
                manifest._windows_digest_handle(23, expected, [0])


if __name__ == "__main__":
    unittest.main()
