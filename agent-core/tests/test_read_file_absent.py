"""A read of a path that is not on disk yet is information, not a failure."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from exec_reliability import (
    classify_failure,
    is_circuit_countable_failure,
    should_count_segment_failure,
)
from tests.isolation_helpers import temporary_agent_paths
from tools.builtin.read_file import run as read_file_run
from tools.executor import (
    ABSENT_FILE_SUMMARY,
    ExecutorSession,
    ToolExecutor,
    _tool_end_note,
    _tool_result_summary,
)
from tools.registry import ToolRegistry


class ReadFileAbsentTests(unittest.TestCase):
    def test_missing_path_is_information(self) -> None:
        with temporary_agent_paths() as paths:
            events: list[tuple[str, dict[str, Any]]] = []
            session = ExecutorSession()
            executor = ToolExecutor(
                registry=ToolRegistry.load(paths),
                session=session,
                on_event=lambda kind, payload: events.append((kind, payload)),
            )
            result = executor.run("read_file", {"path": "workspace/scratch-readme/README.md"})
            self.assertTrue(result.ok, result.error)
            self.assertIsInstance(result.data, dict)
            assert isinstance(result.data, dict)
            self.assertIs(result.data.get("exists"), False)
            self.assertEqual(result.data.get("content"), "")
            self.assertEqual(classify_failure(result).failure_class, "A")
            self.assertFalse(is_circuit_countable_failure(result))
            self.assertFalse(should_count_segment_failure(result, session))
            self.assertEqual(_tool_end_note(result), "absent")
            self.assertEqual(_tool_result_summary(result), ABSENT_FILE_SUMMARY)
            self.assertEqual(session.last_failure_class, "")
            self.assertEqual(session.segment_failure_count, 0)
            self.assertEqual(session.failure_streak_count, 0)

            end = next(payload for kind, payload in events if kind == "tool.end")
            self.assertTrue(end["ok"])
            self.assertEqual(end["summary"], ABSENT_FILE_SUMMARY)
            self.assertEqual(end["note"], "absent")
            evidence = next(payload for kind, payload in events if kind == "turn.evidence")
            self.assertTrue(evidence["items"][0]["ok"])
            self.assertIsNone(evidence["reliability"]["failure_class"])

            folder = paths.workspace / "scratch-readme"
            folder.mkdir(parents=True)
            events.clear()
            directory = executor.run("read_file", {"path": "workspace/scratch-readme"})
            self.assertFalse(directory.ok)
            self.assertEqual(classify_failure(directory).failure_class, "E")
            self.assertTrue(is_circuit_countable_failure(directory))
            self.assertEqual(session.last_failure_class, "E")
            self.assertGreaterEqual(session.segment_failure_count, 1)
            dir_end = next(payload for kind, payload in events if kind == "tool.end")
            self.assertFalse(dir_end["ok"])
            self.assertNotIn("note", dir_end)

            readme = folder / "README.md"
            readme.write_text("hi\n", encoding="utf-8")
            events.clear()
            present = executor.run("read_file", {"path": "workspace/scratch-readme/README.md"})
            self.assertTrue(present.ok, present.error)
            assert isinstance(present.data, dict)
            self.assertIn("hi", present.data.get("content", ""))
            self.assertIsNot(present.data.get("exists"), False)
            present_end = next(payload for kind, payload in events if kind == "tool.end")
            self.assertNotIn("note", present_end)
            self.assertNotEqual(present_end["summary"], ABSENT_FILE_SUMMARY)

    def test_direct_run_keeps_real_read_errors(self) -> None:
        with temporary_agent_paths() as paths:
            missing = read_file_run({"path": "missing-file-xyz.txt"}, paths=paths)
            self.assertTrue(missing.ok)
            assert isinstance(missing.data, dict)
            self.assertIs(missing.data.get("exists"), False)

            outside = read_file_run({"path": "../outside-agent"}, paths=paths)
            self.assertFalse(outside.ok)
            self.assertEqual(classify_failure(outside).failure_class, "E")
