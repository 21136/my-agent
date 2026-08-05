"""Tests for evolve_tool_io newline helpers (BUG-025)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from evolve_tool_io import normalize_newlines, write_utf8_text


class EvolveToolIoNewlineTests(unittest.TestCase):
    def test_normalize_collapses_cr_variants(self) -> None:
        self.assertEqual(normalize_newlines("a\r\nb\rc\n"), "a\nb\nc\n")

    def test_write_utf8_text_no_cr_multiplication_on_windows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_bytes(b"x\r\ny\r\n")
            write_utf8_text(path, path.read_bytes().decode("utf-8"))
            self.assertEqual(path.read_bytes(), b"x\ny\n")
            write_utf8_text(path, path.read_text(encoding="utf-8"))
            self.assertEqual(path.read_bytes(), b"x\ny\n")


if __name__ == "__main__":
    unittest.main()
