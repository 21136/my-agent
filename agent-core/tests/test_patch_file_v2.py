"""T-6202 / IT-6202: atomic structured patch_file v2 contract."""

from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tests.isolation_helpers import temporary_agent_paths
from tools.builtin.run_evolved import run
from tools.registry import ToolRegistry


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class PatchFileV2Tests(unittest.TestCase):
    def test_multiple_hunks_are_atomic_and_recorded(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("coding/patch_file",)) as paths:
            registry = ToolRegistry.load(paths)
            target = paths.workspace / "multi.txt"
            target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
            result = run(
                {
                    "tool_name": "patch_file",
                    "arguments": {
                        "path": "workspace/multi.txt",
                        "hunks": [
                            {"find": "alpha", "replacement": "ALPHA"},
                            {"find": "gamma", "replacement": "GAMMA"},
                        ],
                        "patch_id": "it6202-multi",
                    },
                },
                registry=registry,
            )
            self.assertTrue(result.ok, result.error)
            self.assertEqual(target.read_text(encoding="utf-8"), "ALPHA\nbeta\nGAMMA\n")
            self.assertEqual(result.data.get("mode"), "hunks")
            ledger = paths.data / "patch-ledger.jsonl"
            self.assertIn("it6202-multi", ledger.read_text(encoding="utf-8"))

    def test_failed_hunk_does_not_write(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("coding/patch_file",)) as paths:
            registry = ToolRegistry.load(paths)
            target = paths.workspace / "atomic.txt"
            original = "alpha\nbeta\n"
            target.write_text(original, encoding="utf-8")
            result = run(
                {
                    "tool_name": "patch_file",
                    "arguments": {
                        "path": "workspace/atomic.txt",
                        "hunks": [
                            {"find": "alpha", "replacement": "ALPHA"},
                            {"find": "missing", "replacement": "MISSING"},
                        ],
                    },
                },
                registry=registry,
            )
            self.assertFalse(result.ok)
            self.assertEqual(target.read_text(encoding="utf-8"), original)
            self.assertFalse((paths.data / "patch-ledger.jsonl").exists())

    def test_base_hash_dry_run_and_idempotent_retry(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("coding/patch_file",)) as paths:
            registry = ToolRegistry.load(paths)
            target = paths.workspace / "retry.txt"
            target.write_text("before\n", encoding="utf-8")
            base_hash = _sha256_bytes(target.read_bytes())
            args = {
                "path": "workspace/retry.txt",
                "find": "before",
                "replacement": "after",
                "base_hash": base_hash,
                "patch_id": "it6202-retry",
            }
            preview = run({"tool_name": "patch_file", "arguments": {**args, "dry_run": True}}, registry=registry)
            self.assertTrue(preview.ok, preview.error)
            self.assertEqual(target.read_text(encoding="utf-8"), "before\n")
            applied = run({"tool_name": "patch_file", "arguments": args}, registry=registry)
            self.assertTrue(applied.ok, applied.error)
            repeated = run({"tool_name": "patch_file", "arguments": args}, registry=registry)
            self.assertTrue(repeated.ok, repeated.error)
            self.assertTrue(repeated.data.get("idempotent"))
            self.assertEqual(target.read_text(encoding="utf-8"), "after\n")

            conflict = run(
                {
                    "tool_name": "patch_file",
                    "arguments": {**args, "patch_id": "it6202-conflict", "base_hash": "0" * 64},
                },
                registry=registry,
            )
            self.assertFalse(conflict.ok)
            message = conflict.error.message if conflict.error else ""
            self.assertIn("base_hash mismatch", message)

    def test_reapply_after_revert_uses_the_old_text(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("coding/patch_file",)) as paths:
            registry = ToolRegistry.load(paths)
            target = paths.workspace / "README.md"
            original = "# scratch\n\n旧简介\n"
            target.write_text(original, encoding="utf-8")
            args = {
                "path": "workspace/README.md",
                "find": "旧简介",
                "replacement": "新简介",
            }
            first = run({"tool_name": "patch_file", "arguments": args}, registry=registry)
            self.assertTrue(first.ok, first.error)
            self.assertIn("新简介", target.read_text(encoding="utf-8"))

            again = run({"tool_name": "patch_file", "arguments": args}, registry=registry)
            self.assertTrue(again.ok, again.error)
            self.assertTrue(again.data.get("idempotent"))
            self.assertIn("新简介", target.read_text(encoding="utf-8"))

            target.write_text(original, encoding="utf-8")
            redone = run({"tool_name": "patch_file", "arguments": args}, registry=registry)
            self.assertTrue(redone.ok, redone.error)
            self.assertFalse(redone.data.get("idempotent"))
            self.assertEqual(target.read_text(encoding="utf-8"), "# scratch\n\n新简介\n")
            message = (redone.error.message if redone.error else "") or ""
            self.assertNotIn("different hash", message)

    def test_missing_old_text_is_an_anchor_miss_not_a_ledger_hash_error(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("coding/patch_file",)) as paths:
            registry = ToolRegistry.load(paths)
            target = paths.workspace / "README.md"
            target.write_text("旧简介\n", encoding="utf-8")
            args = {
                "path": "workspace/README.md",
                "find": "旧简介",
                "replacement": "新简介",
            }
            first = run({"tool_name": "patch_file", "arguments": args}, registry=registry)
            self.assertTrue(first.ok, first.error)
            target.write_text("完全不同的内容\n", encoding="utf-8")
            redone = run({"tool_name": "patch_file", "arguments": args}, registry=registry)
            self.assertFalse(redone.ok)
            message = redone.error.message if redone.error else ""
            self.assertIn("anchor not found", message)
            self.assertNotIn("different hash", message)
            self.assertEqual(target.read_text(encoding="utf-8"), "完全不同的内容\n")


if __name__ == "__main__":
    unittest.main()
