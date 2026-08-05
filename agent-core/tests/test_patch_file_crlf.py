"""BUG-025 — patch_file / write_text CRLF safety (IT-99)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tools.builtin.run_evolved import run
from tools.registry import ToolRegistry

from tests.isolation_helpers import temporary_agent_paths


def _double_cr_count(raw: bytes) -> int:
    return raw.count(b"\r\r")


class PatchFileCrlfTests(unittest.TestCase):
    def test_find_patch_does_not_multiply_cr_on_crlf_file(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("coding/patch_file",)) as paths:
            registry = ToolRegistry.load(paths)
            rel = "workspace/_crlf_patch.vue"
            target = paths.workspace / "_crlf_patch.vue"
            target.write_bytes(b"line1\r\nline2\r\nline3\r\nline4\r\nline5\r\n")

            for idx in range(5):
                result = run(
                    {
                        "tool_name": "patch_file",
                        "arguments": {
                            "path": rel,
                            "find": f"line{idx + 1}",
                            "replacement": f"LINE{idx + 1}",
                        },
                    },
                    registry=registry,
                )
                self.assertTrue(result.ok, result.error)
                raw = target.read_bytes()
                self.assertEqual(_double_cr_count(raw), 0, raw)

            self.assertIn(b"LINE5", target.read_bytes())

    def test_write_text_does_not_amplify_existing_cr(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            registry = ToolRegistry.load(paths)
            rel = "workspace/_crlf_write.vue"
            target = paths.workspace / "_crlf_write.vue"
            polluted = b"<t>\r\r\n</t>\r\r\n"
            target.write_bytes(polluted)

            content = target.read_bytes().decode("utf-8")
            before = _double_cr_count(target.read_bytes())
            result = run(
                {
                    "tool_name": "write_text",
                    "arguments": {
                        "path": rel,
                        "content": content,
                        "on_conflict": "overwrite",
                    },
                },
                registry=registry,
            )
            self.assertTrue(result.ok, result.error)
            after = _double_cr_count(target.read_bytes())
            self.assertLessEqual(after, before)
            self.assertEqual(after, 0)

    def test_line_range_patch_still_works(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("coding/patch_file",)) as paths:
            registry = ToolRegistry.load(paths)
            rel = "workspace/_line_patch.txt"
            target = paths.workspace / "_line_patch.txt"
            target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

            result = run(
                {
                    "tool_name": "patch_file",
                    "arguments": {
                        "path": rel,
                        "start_line": 2,
                        "end_line": 2,
                        "replacement": "BETA\n",
                    },
                },
                registry=registry,
            )
            self.assertTrue(result.ok, result.error)
            self.assertEqual(target.read_text(encoding="utf-8"), "alpha\nBETA\ngamma\n")


if __name__ == "__main__":
    unittest.main()
