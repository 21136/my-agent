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


if __name__ == "__main__":
    unittest.main()
