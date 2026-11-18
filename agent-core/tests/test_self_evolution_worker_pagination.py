from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_evolution_candidate_tools import build_self_evolution_candidate_tools
from self_evolution_verification import VerificationRunner, WorkspaceBinding
from self_evolution_worker import _TOOL_SCHEMAS, _parse_call


class SelfEvolutionWorkerPaginationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "candidate"
        self.root.mkdir()
        self.src = self.root / "src"
        self.src.mkdir()
        for index in range(1737):
            (self.src / f"module-{index:04}.py").write_text(f"VALUE = {index}\n", encoding="utf-8")
        deep = self.src / "deep"
        deep.mkdir()
        for index in range(40):
            (deep / f"nested-{index:03}.py").write_text("pass\n", encoding="utf-8")
        (self.root / "large.bin").write_bytes(b"x" * (3 * 1024 * 1024))
        self.binding = WorkspaceBinding(
            task_id="task-a", candidate_root=self.root, candidate_id="candidate-a",
            config_identity="config-a", source_paths=("src/module-0000.py",),
        )
        self.bindings = {"task-a": self.binding}
        self.context = SimpleNamespace(
            session_id="session-a", task_id="task-a", attempt_id="attempt-a", run_id="run-a", candidate_id="candidate-a",
            candidate_digest="digest-a", cancel_event=threading.Event(),
            current_candidate_digest=lambda: "digest-a",
        )
        self.runner = VerificationRunner(
            {"noop": (sys.executable, "-c", "pass")},
            workspace_resolver=lambda task_id: self.bindings.get(task_id),
        )
        tools = build_self_evolution_candidate_tools(
            resolve_candidate_root=lambda context: self.bindings.get(context.task_id).candidate_root
            if context.task_id in self.bindings else None,
            validate_binding=lambda context, root: context.task_id in self.bindings
            and Path(root).resolve() == Path(self.bindings[context.task_id].candidate_root).resolve(),
            verification_runner=self.runner,
            registered_check_ids={"noop"},
        )
        self.observe = next(tool.invoke for tool in tools if tool.name == "observe_candidate")

    def test_paginated_root_observation_finds_files_past_legacy_cap_and_ignores_binary_total(self) -> None:
        cursor = None
        paths: list[str] = []
        pages = 0
        while True:
            arguments = {} if cursor is None else {"cursor": cursor}
            result = self.observe(self.context, arguments)
            self.assertEqual(result["status"], "observed")
            self.assertLessEqual(len(result["files"]), 128)
            paths.extend(item["path"] for item in result["files"])
            pages += 1
            self.assertLess(pages, 16)
            cursor = result["next_cursor"]
            if cursor is None:
                self.assertFalse(result["has_more"])
                break
            self.assertTrue(result["has_more"])
        self.assertEqual(len(paths), 1778)
        self.assertEqual(paths, sorted(paths, key=lambda path: (path.casefold(), path)))
        self.assertIn("large.bin", paths)
        self.assertGreater(len(paths), 256)

    def test_directory_scope_paginates_and_cursor_is_bound_and_bounded(self) -> None:
        first = self.observe(self.context, {"directory": "src"})
        self.assertTrue(first["next_cursor"])
        self.assertTrue(all(item["path"].startswith("src/") for item in first["files"]))
        next_page = self.observe(self.context, {"directory": "src", "cursor": first["next_cursor"]})
        self.assertTrue(all(item["path"].startswith("src/") for item in next_page["files"]))
        with self.assertRaises(PermissionError):
            self.observe(self.context, {"directory": "docs", "cursor": first["next_cursor"]})
        with self.assertRaises(PermissionError):
            self.observe(self.context, {"directory": "src", "cursor": "!" + first["next_cursor"]})
        with self.assertRaises(PermissionError):
            self.observe(self.context, {"directory": "src", "cursor": "x" * 1024})
        other_root = self.base / "candidate-b"
        other_root.mkdir()
        self.bindings["task-b"] = WorkspaceBinding(
            task_id="task-b", candidate_root=other_root, candidate_id="candidate-b",
            config_identity="config-b", source_paths=(),
        )
        other_context = SimpleNamespace(**vars(self.context))
        other_context.task_id = "task-b"
        with self.assertRaises(PermissionError):
            self.observe(other_context, {"directory": "src", "cursor": first["next_cursor"]})

    def test_parser_accepts_optional_observe_fields_and_legacy_empty_arguments(self) -> None:
        self.assertEqual(_TOOL_SCHEMAS["observe_candidate"]["required"], [])
        for arguments in ({}, {"directory": "src"}, {"cursor": "opaque"},
                          {"directory": "src", "cursor": "opaque"}):
            parsed = _parse_call({
                "type": "function", "id": "call-1",
                "function": {"name": "observe_candidate", "arguments": arguments},
            })
            self.assertEqual(parsed.arguments, arguments)
        with self.assertRaises(ValueError):
            _parse_call({"type": "function", "id": "call-2", "function": {
                "name": "observe_candidate", "arguments": {"unknown": "x"},
            }})
        with self.assertRaises(ValueError):
            _parse_call({"type": "function", "id": "call-3", "function": {
                "name": "observe_candidate", "arguments": {"cursor": "x" * 1024},
            }})


if __name__ == "__main__":
    unittest.main()
