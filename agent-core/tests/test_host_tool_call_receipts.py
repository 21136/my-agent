from __future__ import annotations

import sys
import threading
import unittest
import uuid
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from tools.schema import tool_ok


class TestHostToolCallReceipts(unittest.TestCase):
    def _executor(self, paths, *, host_run_id=None):
        session = ExecutorSession(host_run_id=host_run_id or str(uuid.uuid4()))
        executor = ToolExecutor(registry=ToolRegistry.load(paths), session=session, confirm_fn=lambda *_a, **_k: "y")
        return executor, session

    def test_captures_only_registered_builtin_result_with_private_minimal_shape(self):
        with temporary_agent_paths() as paths:
            executor, session = self._executor(paths)
            executor._execute_builtin = lambda name, args, **kwargs: tool_ok(name, {"secret": args["path"]})
            result = executor.run("read_file", {"path": "private/path.txt"})
            self.assertTrue(result.ok)
            receipts = session.host_tool_receipts
            self.assertEqual(len(receipts), 1)
            self.assertEqual(set(receipts[0]), {"host_run_id", "call_id", "tool", "ok"})
            self.assertEqual(receipts[0]["host_run_id"], session.host_run_id)
            self.assertEqual(receipts[0]["tool"], "read_file")
            self.assertIs(type(receipts[0]["ok"]), bool)
            self.assertTrue(receipts[0]["ok"])
            self.assertEqual(str(uuid.UUID(receipts[0]["call_id"])), receipts[0]["call_id"])
            self.assertEqual(session.host_tool_receipts_omitted_count, 0)
            self.assertNotIn("private/path.txt", repr(receipts))
            receipts[0]["tool"] = "tampered"
            self.assertEqual(session.host_tool_receipts[0]["tool"], "read_file")

    def test_begin_turn_resets_receipts_and_omissions(self):
        with temporary_agent_paths() as paths:
            executor, session = self._executor(paths)
            executor._execute_builtin = lambda name, args, **kwargs: tool_ok(name, {})
            executor.run("read_file", {"path": "x"})
            executor.begin_turn()
            self.assertEqual(session.host_tool_receipts, [])
            self.assertEqual(session.host_tool_receipts_omitted_count, 0)

    def test_rejected_validation_and_unknown_identifiers_do_not_create_receipts(self):
        with temporary_agent_paths() as paths:
            executor, session = self._executor(paths)
            from tools.schema import tool_fail
            original_validate = executor.validate
            executor.validate = lambda *_args, **_kwargs: tool_fail("read_file", "validation_error", "synthetic")
            invalid = executor.run("read_file", {})
            self.assertFalse(invalid.ok)
            self.assertEqual(session.host_tool_receipts, [])
            self.assertEqual(session.host_tool_receipts_omitted_count, 0)
            executor.validate = original_validate
            unknown = executor.run("model_supplied_tool_name", {})
            self.assertFalse(unknown.ok)
            self.assertEqual(session.host_tool_receipts, [])
            self.assertEqual(session.host_tool_receipts_omitted_count, 1)

    def test_dry_run_argument_is_not_claimed_as_completion(self):
        with temporary_agent_paths() as paths:
            executor, session = self._executor(paths)
            executor._execute_builtin = lambda name, args, **kwargs: tool_ok(name, {})
            executor.run("read_file", {"path": "x", "dry_run": True})
            self.assertEqual(session.host_tool_receipts, [])
            self.assertEqual(session.host_tool_receipts_omitted_count, 1)

    def test_background_and_escalated_results_are_omitted(self):
        with temporary_agent_paths() as paths:
            executor, session = self._executor(paths)
            for marker in ("background", "escalated"):
                executor._execute_builtin = lambda name, args, _marker=marker, **kwargs: tool_ok(
                    name, {_marker: True}
                )
                executor.run("read_file", {"path": "x"})
            self.assertEqual(session.host_tool_receipts, [])
            self.assertEqual(session.host_tool_receipts_omitted_count, 2)

    def test_missing_or_invalid_host_run_id_does_not_capture(self):
        with temporary_agent_paths() as paths:
            executor, session = self._executor(paths, host_run_id="not-a-uuid")
            executor._execute_builtin = lambda name, args, **kwargs: tool_ok(name, {})
            executor.run("read_file", {"path": "x"})
            self.assertEqual(session.host_tool_receipts, [])
            self.assertEqual(session.host_tool_receipts_omitted_count, 0)

    def test_execution_exception_is_omitted_not_claimed_as_receipt(self):
        with temporary_agent_paths() as paths:
            executor, session = self._executor(paths)
            def explode(*args, **kwargs):
                raise RuntimeError("do not retain exception text")
            executor._execute_builtin = explode
            result = executor.run("read_file", {"path": "x"})
            self.assertFalse(result.ok)
            self.assertEqual(session.host_tool_receipts, [])
            self.assertEqual(session.host_tool_receipts_omitted_count, 1)

    def test_each_turn_is_bounded_and_reports_omissions(self):
        with temporary_agent_paths() as paths:
            executor, session = self._executor(paths)
            executor._execute_builtin = lambda name, args, **kwargs: tool_ok(name, {})
            for _ in range(23):
                executor.run("read_file", {"path": "x"})
            self.assertEqual(len(session.host_tool_receipts), 20)
            self.assertEqual(session.host_tool_receipts_omitted_count, 3)

    def test_evolved_call_never_exposes_model_name_in_receipts(self):
        with temporary_agent_paths() as paths:
            executor, session = self._executor(paths)
            executor._execute_builtin = lambda name, args, **kwargs: tool_ok(name, {"tool_name": "model_secret"})
            executor.run("run_evolved", {"tool_name": "not_installed", "arguments": {}})
            self.assertEqual(session.host_tool_receipts, [])
            self.assertEqual(session.host_tool_receipts_omitted_count, 1)

    def test_completed_evolved_wrapper_uses_builtin_label_only(self):
        with temporary_agent_paths() as paths:
            executor, session = self._executor(paths)
            executor.validate = lambda *_args, **_kwargs: None
            executor._execute_builtin = lambda name, args, **kwargs: tool_ok(
                name, {"tool_name": "private_evolved_name", "background": False}
            )
            executor.run("run_evolved", {"tool_name": "private_evolved_name", "arguments": {}})
            self.assertEqual(session.host_tool_receipts[0]["tool"], "run_evolved")
            self.assertNotIn("private_evolved_name", repr(session.host_tool_receipts))

    def test_snapshot_pins_run_identity_until_begin_turn_rebinds_it(self):
        with temporary_agent_paths() as paths:
            old_run_id = str(uuid.uuid4())
            new_run_id = str(uuid.uuid4())
            executor, session = self._executor(paths, host_run_id=old_run_id)
            executor.begin_turn()
            executor._execute_builtin = lambda name, args, **kwargs: tool_ok(name, {})
            executor.run("read_file", {"path": "old-run.txt"})

            old_snapshot = session.snapshot_host_tool_receipts()
            self.assertEqual(len(old_snapshot), 4)
            old_generation, snapshot_run_id, entries, omitted = old_snapshot
            self.assertEqual(snapshot_run_id, old_run_id)
            self.assertEqual(len(entries), 1)
            self.assertEqual(omitted, 0)

            # Simulate bind_host_run_context updating session identity before Agent.begin_turn.
            session.host_run_id = new_run_id
            pre_turn_snapshot = session.snapshot_host_tool_receipts()
            self.assertEqual(pre_turn_snapshot[0], old_generation)
            self.assertEqual(pre_turn_snapshot[1], old_run_id)
            self.assertEqual(pre_turn_snapshot[2], entries)
            self.assertEqual(pre_turn_snapshot[3], 0)

            executor.begin_turn()
            new_snapshot = session.snapshot_host_tool_receipts()
            self.assertEqual(new_snapshot[0], old_generation + 1)
            self.assertEqual(new_snapshot[1], new_run_id)
            self.assertEqual(new_snapshot[2], [])
            self.assertEqual(new_snapshot[3], 0)
            # A previously returned snapshot remains detached and correctly attributed.
            self.assertEqual(old_snapshot[1], old_run_id)
            self.assertEqual(len(old_snapshot[2]), 1)

    def test_new_turn_fences_late_call_from_previous_generation(self):
        with temporary_agent_paths() as paths:
            executor, session = self._executor(paths)
            entered = threading.Event()
            release = threading.Event()
            def delayed(name, args, **kwargs):
                entered.set()
                release.wait(2)
                return tool_ok(name, {})
            executor._execute_builtin = delayed
            worker = threading.Thread(target=lambda: executor.run("read_file", {"path": "x"}))
            worker.start()
            self.assertTrue(entered.wait(2))
            executor.begin_turn()
            release.set()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(session.host_tool_receipts, [])


if __name__ == "__main__":
    unittest.main()
