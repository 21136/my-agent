from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

import self_evolution_materializer as materializer


@unittest.skipUnless(os.name == "nt", "Windows native handles")
class ScratchHandleCloseTests(unittest.TestCase):
    def test_close_declares_handle_width_and_checks_result(self):
        full_handle = 0x123456789ABC
        close = mock.MagicMock(return_value=1)
        kernel = mock.Mock(CloseHandle=close)
        tree = materializer._TemporaryTree(Path("C:/scratch"), (1, 2), (1, 3), root_handle=full_handle)
        with mock.patch.object(materializer.ctypes, "WinDLL", return_value=kernel):
            tree.close_root()
        self.assertEqual(close.argtypes, [wintypes.HANDLE])
        self.assertIs(close.restype, wintypes.BOOL)
        close.assert_called_once_with(full_handle)
        self.assertIsNone(tree.root_handle)

    def test_close_failure_preserves_handle_and_reports_error(self):
        close = mock.MagicMock(return_value=0)
        kernel = mock.Mock(CloseHandle=close)
        tree = materializer._TemporaryTree(Path("C:/scratch"), (1, 2), (1, 3), root_handle=0x123456789ABC)
        with mock.patch.object(materializer.ctypes, "WinDLL", return_value=kernel):
            with self.assertRaises(materializer.MaterializationError) as error:
                tree.close_root()
        self.assertEqual(error.exception.code, "safe_handle_close_failed")
        self.assertEqual(tree.root_handle, 0x123456789ABC)


if __name__ == "__main__":
    unittest.main()
