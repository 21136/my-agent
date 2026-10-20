"""T-6202 / IT-6202: atomic structured patch_file v2 contract."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tests.isolation_helpers import temporary_agent_paths
from tools.builtin.run_evolved import run
from tools.registry import ToolRegistry


def _load_patch_tool(paths):
    script = paths.evolve / "tools" / "coding" / "patch_file" / "main.py"
    spec = importlib.util.spec_from_file_location("patch_file_ledger_failure_under_test", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
            self.assertEqual(result.data.get("write_state"), "applied")
            self.assertTrue(result.data.get("applied"))
            self.assertTrue(result.data.get("ledger_recorded"))
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
            self.assertEqual((result.error.details or {}).get("write_state"), "not_applied")
            self.assertFalse((result.error.details or {}).get("applied"))
            self.assertEqual(target.read_text(encoding="utf-8"), original)
            self.assertFalse((paths.data / "patch-ledger.jsonl").exists())

    def test_ledger_failure_reports_that_file_was_already_applied(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("coding/patch_file",)) as paths:
            target = paths.workspace / "ledger-failure.txt"
            target.write_text("before\n", encoding="utf-8")
            module = _load_patch_tool(paths)
            args = {
                "path": "workspace/ledger-failure.txt",
                "find": "before",
                "replacement": "after",
                "patch_id": "it6202-ledger-failure",
            }

            with patch.dict(os.environ, {"MY_AGENT_ROOT": str(paths.agent_root)}):
                with patch.object(module, "_append_ledger", side_effect=OSError("ledger unavailable")):
                    result = module.run_patch(args)

            self.assertFalse(result["ok"])
            self.assertEqual(result.get("write_state"), "applied_unledgered")
            self.assertTrue(result.get("applied"))
            self.assertFalse(result.get("ledger_recorded"))
            self.assertEqual(result.get("result_hash"), _sha256_bytes(target.read_bytes()))
            self.assertEqual(target.read_text(encoding="utf-8"), "after\n")
            self.assertIn("do not retry", result["error"].lower())
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
            self.assertEqual(preview.data.get("write_state"), "not_applied")
            self.assertFalse(preview.data.get("applied"))
            self.assertEqual(target.read_text(encoding="utf-8"), "before\n")
            applied = run({"tool_name": "patch_file", "arguments": args}, registry=registry)
            self.assertTrue(applied.ok, applied.error)
            self.assertEqual(applied.data.get("write_state"), "applied")
            self.assertTrue(applied.data.get("ledger_recorded"))
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
            self.assertIn("没有找到要替换的原文", message)
            self.assertNotIn("different hash", message)
            self.assertEqual(target.read_text(encoding="utf-8"), "完全不同的内容\n")

    def test_fuzzy_indent_and_trailing_whitespace_fallback(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("coding/patch_file",)) as paths:
            registry = ToolRegistry.load(paths)
            target = paths.workspace / "indent.py"
            target.write_text(
                "class Demo:\n"
                "    def greet(self):   \n"
                "        x = 1\n"
                "        return x\n",
                encoding="utf-8",
            )
            # Model passes find with 0 outer indent and no trailing spaces
            result = run(
                {
                    "tool_name": "patch_file",
                    "arguments": {
                        "path": "workspace/indent.py",
                        "find": "def greet(self):\n    x = 1\n    return x",
                        "replacement": "def greet(self):\n    x = 42\n    return x + 1",
                    },
                },
                registry=registry,
            )
            self.assertTrue(result.ok, result.error)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "class Demo:\n"
                "    def greet(self):\n"
                "        x = 42\n"
                "        return x + 1\n",
            )


if __name__ == "__main__":
    unittest.main()
