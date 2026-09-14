"""T-6201: interactive terminal contract and worker protocol."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from tests.isolation_helpers import make_temp_agent_paths

_AGENT_CORE = Path(__file__).resolve().parents[1]
_TOOL = _AGENT_CORE.parent / "evolve" / "tools" / "common" / "interactive_terminal" / "main.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("interactive_terminal_under_test", _TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakePty:
    instances: list["FakePty"] = []

    def __init__(self, command: str, cwd: str | None = None) -> None:
        self.command = command
        self.cwd = cwd
        self.pid = 12345
        self.exitstatus = 0
        self._alive = True
        self._reads = ["ready\r\n", ""]
        self.writes: list[str] = []
        self.signals: list[str] = []
        FakePty.instances.append(self)

    @classmethod
    def spawn(cls, command: str, cwd: str | None = None):
        return cls(command, cwd)

    def isalive(self) -> bool:
        return self._alive

    def read(self, _size: int) -> str:
        if self._reads:
            return self._reads.pop(0)
        time.sleep(0.01)
        return ""

    def write(self, text: str) -> None:
        self.writes.append(text)
        self._reads.append(text)

    def sendcontrol(self, key: str) -> None:
        self.signals.append(key)

    def terminate(self, force: bool = False) -> None:
        self.signals.append("terminate-force" if force else "terminate")
        self._alive = False


class TimeoutPty:
    def __init__(self) -> None:
        self.timeout: float | None = None

    def read(self, _size: int, *, timeout: float) -> str:
        self.timeout = timeout
        return ""


class InteractiveTerminalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load_tool()
        FakePty.instances.clear()

    def test_command_split_unquotes_windows_arguments(self) -> None:
        self.assertEqual(
            self.mod._split_command(
                'python -u -c "print(123)"'
            ),
            ["python", "-u", "-c", "print(123)"],
        )

    def test_missing_backend_is_explicitly_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            state = {
                "session_id": "it-0000000000000001",
                "command": "python -i",
                "cwd_absolute": temp,
                "state": "starting",
                "created_at": self.mod._utc_now(),
            }
            self.mod._write_state(directory, state)
            # Keep this branch deterministic after the optional backend is installed.
            with patch.object(
                self.mod,
                "_load_pty_process",
                side_effect=RuntimeError("unsupported: test backend unavailable"),
            ):
                result = self.mod._worker_main(directory, pty_cls=None)
            current = json.loads((directory / "state.json").read_text(encoding="utf-8"))
            if self.mod.sys.platform == "win32":
                self.assertEqual(result, 0)
                self.assertEqual(current["state"], "unsupported")
            else:
                self.assertIn(current["state"], {"unsupported", "failed"})

    def test_worker_handles_input_signal_and_close(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "requests").mkdir()
            (directory / "responses").mkdir()
            state = {
                "session_id": "it-0000000000000001",
                "command": "fake",
                "cwd_absolute": temp,
                "state": "starting",
                "created_at": self.mod._utc_now(),
                "last_activity_at": self.mod._utc_now(),
                "idle_timeout_sec": 600,
                "lifetime_timeout_sec": 600,
                "output_start": 0,
                "output_bytes": 0,
            }
            self.mod._write_state(directory, state)

            worker = threading.Thread(target=self.mod._worker_main, args=(directory, FakePty), daemon=True)
            worker.start()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                current = self.mod._read_json(directory / "state.json") or {}
                if current.get("state") == "running":
                    break
                time.sleep(0.02)
            current = self.mod._read_json(directory / "state.json") or {}
            self.assertEqual(current.get("state"), "running")

            response = self.mod._submit_request(directory, "input", {"text": "hello\n"})
            self.assertTrue(response.get("ok"), response)
            response = self.mod._submit_request(directory, "signal", {"signal": "eof"})
            self.assertTrue(response.get("ok"), response)
            response = self.mod._submit_request(directory, "signal", {"signal": "ctrl_c"})
            self.assertTrue(response.get("ok"), response)
            response = self.mod._submit_request(directory, "close", {"force": True})
            self.assertTrue(response.get("ok"), response)
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            fake = FakePty.instances[0]
            self.assertEqual(fake.writes, ["hello\r\n", "\x04"])
            self.assertIn("c", fake.signals)
            self.assertIn("terminate-force", fake.signals)

    def test_worker_can_process_requests_while_pty_read_blocks(self) -> None:
        class BlockingPty(FakePty):
            def read(self, _size: int) -> str:
                time.sleep(10)
                return ""

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "requests").mkdir()
            (directory / "responses").mkdir()
            self.mod._write_state(
                directory,
                {
                    "session_id": "it-0000000000000001",
                    "command": "fake",
                    "cwd_absolute": temp,
                    "state": "starting",
                    "created_at": self.mod._utc_now(),
                    "last_activity_at": self.mod._utc_now(),
                    "idle_timeout_sec": 600,
                    "lifetime_timeout_sec": 600,
                    "output_start": 0,
                    "output_bytes": 0,
                },
            )
            worker = threading.Thread(target=self.mod._worker_main, args=(directory, BlockingPty), daemon=True)
            worker.start()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                current = self.mod._read_json(directory / "state.json") or {}
                if current.get("state") == "running":
                    break
                time.sleep(0.02)
            response = self.mod._submit_request(directory, "close", {"force": True})
            self.assertTrue(response.get("ok"), response)
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())

    def test_worker_retains_output_queued_during_exit(self) -> None:
        class ExitOutputPty(FakePty):
            def terminate(self, force: bool = False) -> None:
                self._reads.append("final output\r\n")
                super().terminate(force=force)

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "requests").mkdir()
            (directory / "responses").mkdir()
            self.mod._write_state(
                directory,
                {
                    "session_id": "it-0000000000000001",
                    "command": "fake",
                    "cwd_absolute": temp,
                    "state": "starting",
                    "created_at": self.mod._utc_now(),
                    "last_activity_at": self.mod._utc_now(),
                    "idle_timeout_sec": 600,
                    "lifetime_timeout_sec": 600,
                    "output_start": 0,
                    "output_bytes": 0,
                },
            )
            worker = threading.Thread(target=self.mod._worker_main, args=(directory, ExitOutputPty), daemon=True)
            worker.start()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                current = json.loads((directory / "state.json").read_text(encoding="utf-8"))
                if current.get("state") == "running":
                    break
                time.sleep(0.02)
            response = self.mod._submit_request(directory, "close", {"force": True})
            self.assertTrue(response.get("ok"), response)
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            output = (directory / "output.log").read_text(encoding="utf-8")
            self.assertIn("final output", output)

    def test_read_pty_uses_non_blocking_timeout_when_supported(self) -> None:
        pty = TimeoutPty()
        self.assertEqual(self.mod._read_pty(pty), "")
        self.assertEqual(pty.timeout, 0)

    def test_dead_worker_marks_session_lost_and_records_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            state = {
                "session_id": "it-0000000000000001",
                "worker_pid": 101,
                "pid": 202,
                "state": "running",
                "alive": True,
            }
            with patch.object(self.mod, "_pid_alive", return_value=False), patch.object(
                self.mod, "_terminate_pid_tree", return_value={"ok": True, "exit_code": 0}
            ) as cleanup:
                current = self.mod._refresh_orphan(directory, state)
            self.assertEqual(current["state"], "lost")
            self.assertFalse(current["alive"])
            self.assertEqual(current["orphan_cleanup"], {"ok": True, "exit_code": 0})
            cleanup.assert_called_once_with(202, force=True)

    def test_stale_running_session_with_reused_pid_is_lost_without_killing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            state = {
                "session_id": "it-0000000000000001",
                "worker_pid": os.getpid(),
                "pid": os.getpid(),
                "state": "running",
                "alive": True,
                "last_activity_at": "2020-01-01T00:00:00+00:00",
            }
            with patch.object(self.mod, "_terminate_pid_tree") as cleanup:
                current = self.mod._refresh_orphan(directory, state)
            self.assertEqual(current["state"], "lost")
            self.assertFalse(current["alive"])
            self.assertIn("stale", current["reason"])
            cleanup.assert_not_called()
            self.assertTrue(self.mod._pid_alive(os.getpid()))
            saved = json.loads((directory / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["state"], "lost")

    def test_stale_running_session_without_worker_pid_is_orphaned(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            current = self.mod._refresh_orphan(
                directory,
                {
                    "session_id": "it-0000000000000001",
                    "state": "running",
                    "alive": True,
                    "last_activity_at": "2020-01-01T00:00:00+00:00",
                },
            )
            self.assertEqual(current["state"], "orphaned")
            self.assertFalse(current["alive"])
            self.assertIn("stale", current["reason"])

    def test_fresh_running_session_with_live_worker_stays_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            state = {
                "session_id": "it-0000000000000001",
                "worker_pid": os.getpid(),
                "state": "running",
                "alive": True,
                "last_activity_at": self.mod._utc_now(),
            }
            current = self.mod._refresh_orphan(directory, state)
            self.assertEqual(current["state"], "running")
            self.assertTrue(current["alive"])

    @unittest.skipUnless(os.name == "nt", "requires Windows process liveness semantics")
    def test_pid_alive_uses_windows_process_query(self) -> None:
        self.assertTrue(self.mod._pid_alive(os.getpid()))
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertTrue(self.mod._pid_alive(child.pid))
        finally:
            child.terminate()
            child.wait(timeout=5)
        self.assertFalse(self.mod._pid_alive(child.pid))

    @unittest.skipUnless(os.name == "nt", "requires the Windows PTY backend")
    def test_run_evolved_hosts_terminal_worker_in_long_lived_process(self) -> None:
        """The evolved wrapper must not own a worker that outlives its call."""
        from tools.builtin.run_evolved import execute_evolved_tool
        from tools.registry import ToolRegistry

        tool = ToolRegistry.load().get_evolved("interactive_terminal")
        self.assertIsNotNone(tool)
        assert tool is not None
        command = 'python -u -c "import time; print(\'hosted-ready\', flush=True); time.sleep(15)"'
        started = execute_evolved_tool(
            tool,
            {"action": "start", "command": command, "lifetime_timeout_sec": 60},
            dry_run=False,
            dry_run_explicit=True,
        )
        self.assertTrue(started.get("ok"), started)
        session_id = started.get("session_id")
        self.assertIsInstance(session_id, str)
        assert isinstance(session_id, str)

        try:
            cursor = 0
            read: dict[str, object] = {}
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                read = execute_evolved_tool(
                    tool,
                    {"action": "read", "session_id": session_id, "cursor": cursor},
                    dry_run=False,
                    dry_run_explicit=True,
                )
                self.assertTrue(read.get("ok"), read)
                output = str(read.get("output", ""))
                cursor = int(read.get("next_cursor", cursor))
                if "hosted-ready" in output:
                    break
                time.sleep(0.05)
            self.assertIn("hosted-ready", str(read.get("output", "")))

            status = execute_evolved_tool(
                tool,
                {"action": "status", "session_id": session_id},
                dry_run=False,
                dry_run_explicit=True,
            )
            self.assertTrue(status.get("ok"), status)
            self.assertEqual(status.get("state", {}).get("state"), "running")

            entered = execute_evolved_tool(
                tool,
                {"action": "input", "session_id": session_id, "text": "ignored\n"},
                dry_run=False,
                dry_run_explicit=True,
            )
            self.assertTrue(entered.get("ok"), entered)
        finally:
            closed = execute_evolved_tool(
                tool,
                {"action": "close", "session_id": session_id, "force": True},
                dry_run=False,
                dry_run_explicit=True,
            )
            self.assertTrue(closed.get("ok"), closed)

    def test_output_cursor_reports_new_data_and_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "output.log").write_bytes(b"old\nnew\n")
            state = {
                "session_id": "it-0000000000000001",
                "state": "running",
                "alive": True,
                "output_start": 2,
                "output_bytes": 6,
            }
            result = self.mod._read_output(directory, {"cursor": 0}, state)
            self.assertTrue(result["cursor_reset"])
            self.assertEqual(result["output"], "old\nnew\n")
            self.assertEqual(result["next_cursor"], 10)

    def test_read_output_remains_available_after_terminal_exit(self) -> None:
        paths = make_temp_agent_paths(self, copy_tool_dirs=("common/interactive_terminal",))
        directory = paths.data / "interactive-terminals" / "it-0000000000000001"
        (directory / "requests").mkdir(parents=True)
        (directory / "responses").mkdir(parents=True)
        (directory / "output.log").write_bytes(b"final output\n")
        self.mod._write_state(
            directory,
            {
                "session_id": "it-0000000000000001",
                "state": "exited",
                "alive": False,
                "exit_code": 0,
                "output_start": 0,
                "output_bytes": 13,
            },
        )

        result = self.mod._existing_action(
            paths,
            {
                "action": "read",
                "session_id": "it-0000000000000001",
                "cursor": 0,
            },
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["output"], "final output\n")


if __name__ == "__main__":
    unittest.main()
