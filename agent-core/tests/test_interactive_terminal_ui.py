"""M0 contract tests for making persistent terminal sessions visible in Desktop."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
_ROOT = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tests.isolation_helpers import temporary_agent_paths


class InteractiveTerminalUiTests(unittest.TestCase):
    def test_terminal_list_returns_flattened_safe_snapshots(self) -> None:
        from terminal_api import dispatch_terminal_message

        with temporary_agent_paths(copy_tool_dirs=("common/interactive_terminal",)) as paths:
            session_dir = paths.data / "interactive-terminals" / "it-0123456789abcdef"
            (session_dir / "requests").mkdir(parents=True)
            (session_dir / "responses").mkdir(parents=True)
            (session_dir / "output.log").write_bytes(b"secret-looking output")
            (session_dir / "state.json").write_text(
                json.dumps(
                    {
                        "session_id": "it-0123456789abcdef",
                        "command": "python -i",
                        "cwd": "workspace/demo",
                        "cwd_absolute": str(paths.workspace / "demo"),
                        "state": "running",
                        "alive": True,
                        "worker_pid": os.getpid(),
                        "exit_code": None,
                        "signal": None,
                        "reason": None,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "last_activity_at": datetime.now(timezone.utc).isoformat(),
                        "output_start": 0,
                        "output_bytes": 21,
                    }
                ),
                encoding="utf-8",
            )

            listed = dispatch_terminal_message(
                paths,
                {"type": "terminal.list", "request_id": "terminal-test-1"},
            )
            from server import WsBridge, WsSessionHandler

            events: list[dict[str, object]] = []
            asyncio.run(
                WsSessionHandler(paths)._dispatch_terminal(
                    {"type": "terminal.list", "request_id": "server-test-1"},
                    WsBridge(emit=events.append, paths=paths),
                )
            )

        self.assertEqual(listed["type"], "terminal.list.done")
        self.assertEqual(listed["request_id"], "terminal-test-1")
        self.assertTrue(listed["ok"])
        self.assertEqual(len(listed["sessions"]), 1)
        session = listed["sessions"][0]
        self.assertEqual(session["session_id"], "it-0123456789abcdef")
        self.assertEqual(session["state"], "running")
        self.assertTrue(session["alive"])
        self.assertEqual(session["cwd"], "workspace/demo")
        self.assertNotIn("cwd_absolute", session)
        self.assertNotIn("worker_pid", session)
        self.assertNotIn("pid", session)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "terminal.list.done")
        self.assertEqual(events[0]["request_id"], "server-test-1")

    def test_terminal_snapshot_cannot_claim_non_active_session_is_alive(self) -> None:
        from terminal_api import _snapshot

        snapshot = _snapshot(
            {
                "session_id": "it-0123456789abcdef",
                "state": "closed",
                "alive": True,
            }
        )

        assert snapshot is not None
        self.assertFalse(snapshot["alive"])

    def test_terminal_snapshot_marks_stale_running_as_lost(self) -> None:
        from terminal_api import _snapshot

        now = datetime(2026, 9, 14, 12, tzinfo=timezone.utc).timestamp()
        snapshot = _snapshot(
            {
                "session_id": "it-0123456789abcdef",
                "state": "running",
                "alive": True,
                "command": "python -i",
                "last_activity_at": "2026-09-09T12:00:00+00:00",
            },
            now=now,
        )

        assert snapshot is not None
        self.assertEqual(snapshot["state"], "lost")
        self.assertFalse(snapshot["alive"])
        self.assertIn("stale", snapshot["reason"] or "")

    def test_terminal_list_persists_stale_running_session(self) -> None:
        from terminal_api import dispatch_terminal_message

        with temporary_agent_paths(copy_tool_dirs=("common/interactive_terminal",)) as paths:
            session_dir = paths.data / "interactive-terminals" / "it-0123456789abcdef"
            session_dir.mkdir(parents=True)
            (session_dir / "state.json").write_text(
                json.dumps(
                    {
                        "session_id": "it-0123456789abcdef",
                        "command": "python -i",
                        "cwd": "workspace/demo",
                        "state": "running",
                        "alive": True,
                        "created_at": "2026-09-09T12:00:00+00:00",
                        "last_activity_at": "2026-09-09T12:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            listed = dispatch_terminal_message(
                paths,
                {"type": "terminal.list", "request_id": "stale-list-1"},
            )
            saved = json.loads((session_dir / "state.json").read_text(encoding="utf-8"))

        self.assertTrue(listed["ok"])
        self.assertEqual(listed["sessions"][0]["state"], "orphaned")
        self.assertFalse(listed["sessions"][0]["alive"])
        self.assertEqual(saved["state"], "orphaned")
        self.assertFalse(saved["alive"])

    def test_terminal_output_preserves_cursor_contract(self) -> None:
        from terminal_api import dispatch_terminal_message

        calls: list[dict[str, object]] = []

        class FakeTerminal:
            @staticmethod
            def interactive_terminal(payload: dict[str, object]) -> dict[str, object]:
                calls.append(payload)
                return {
                    "ok": True,
                    "session_id": "it-0123456789abcdef",
                    "output": "READY\\n",
                    "cursor": 0,
                    "next_cursor": 6,
                    "cursor_reset": False,
                    "truncated": False,
                }

        with temporary_agent_paths(copy_tool_dirs=("common/interactive_terminal",)) as paths:
            with patch("terminal_api._load_interactive_terminal", return_value=FakeTerminal):
                result = dispatch_terminal_message(
                    paths,
                    {
                        "type": "terminal.output",
                        "request_id": "output-test-1",
                        "session_id": "it-0123456789abcdef",
                        "cursor": 0,
                        "max_chars": 1024,
                    },
                )

        self.assertEqual(result["type"], "terminal.output.done")
        self.assertTrue(result["ok"])
        self.assertEqual(result["request_id"], "output-test-1")
        self.assertEqual(result["session_id"], "it-0123456789abcdef")
        self.assertEqual(result["output"], "READY\\n")
        self.assertEqual(result["next_cursor"], 6)
        self.assertFalse(result["cursor_reset"])
        self.assertFalse(result["truncated"])
        self.assertEqual(calls, [{
            "action": "read",
            "session_id": "it-0123456789abcdef",
            "cursor": 0,
            "max_chars": 1024,
        }])

    def test_terminal_close_confirms_with_session_context(self) -> None:
        from terminal_api import dispatch_terminal_message

        calls: list[dict[str, object]] = []
        confirmations: list[str] = []

        class FakeTerminal:
            @staticmethod
            def interactive_terminal(payload: dict[str, object]) -> dict[str, object]:
                calls.append(payload)
                if payload["action"] == "status":
                    return {
                        "ok": True,
                        "session_id": "it-0123456789abcdef",
                        "state": {
                            "state": "running",
                            "command": "python -i",
                            "cwd": "workspace/demo",
                            "alive": True,
                        },
                    }
                return {
                    "ok": True,
                    "session_id": "it-0123456789abcdef",
                    "state": "closed",
                }

        def confirm(preview: str, allow_approve_all: bool) -> str:
            self.assertFalse(allow_approve_all)
            confirmations.append(preview)
            return "y"

        with temporary_agent_paths(copy_tool_dirs=("common/interactive_terminal",)) as paths:
            with patch("terminal_api._load_interactive_terminal", return_value=FakeTerminal):
                result = dispatch_terminal_message(
                    paths,
                    {
                        "type": "terminal.close",
                        "request_id": "close-test-1",
                        "session_id": "it-0123456789abcdef",
                    },
                    confirm_fn=confirm,
                )

        self.assertEqual(result["type"], "terminal.close.done")
        self.assertTrue(result["ok"])
        self.assertEqual(result["request_id"], "close-test-1")
        self.assertEqual(result["session_id"], "it-0123456789abcdef")
        self.assertEqual(calls[0]["action"], "status")
        self.assertEqual(calls[1], {
            "action": "close",
            "session_id": "it-0123456789abcdef",
            "force": False,
        })
        self.assertEqual(len(confirmations), 1)
        self.assertIn("python -i", confirmations[0])
        self.assertIn("workspace/demo", confirmations[0])

    def test_terminal_close_rejection_does_not_submit_close(self) -> None:
        from terminal_api import dispatch_terminal_message

        calls: list[dict[str, object]] = []

        class FakeTerminal:
            @staticmethod
            def interactive_terminal(payload: dict[str, object]) -> dict[str, object]:
                calls.append(payload)
                return {
                    "ok": True,
                    "session_id": "it-0123456789abcdef",
                    "state": {
                        "state": "running",
                        "command": "python -i",
                        "cwd": "workspace/demo",
                        "alive": True,
                    },
                }

        with temporary_agent_paths(copy_tool_dirs=("common/interactive_terminal",)) as paths:
            with patch("terminal_api._load_interactive_terminal", return_value=FakeTerminal):
                result = dispatch_terminal_message(
                    paths,
                    {
                        "type": "terminal.close",
                        "request_id": "close-test-reject",
                        "session_id": "it-0123456789abcdef",
                    },
                    confirm_fn=lambda _preview, _allow: "n",
                )

        self.assertEqual(result["type"], "terminal.close.done")
        self.assertFalse(result["ok"])
        self.assertEqual(result["request_id"], "close-test-reject")
        self.assertEqual(result["session_id"], "it-0123456789abcdef")
        self.assertEqual(result["error"], "terminal close rejected by user")
        self.assertEqual([call["action"] for call in calls], ["status"])

    def test_terminal_close_rejects_non_boolean_force(self) -> None:
        from terminal_api import TerminalApiError, dispatch_terminal_message

        with temporary_agent_paths(copy_tool_dirs=("common/interactive_terminal",)) as paths:
            with self.assertRaisesRegex(TerminalApiError, "force must be boolean"):
                dispatch_terminal_message(
                    paths,
                    {
                        "type": "terminal.close",
                        "session_id": "it-0123456789abcdef",
                        "force": "false",
                    },
                    confirm_fn=lambda _preview, _allow: "y",
                )

    def test_server_routes_output_and_close_to_terminal_dispatch(self) -> None:
        from server import WsBridge, WsSessionHandler

        routed: list[str] = []

        async def fake_dispatch(message: dict[str, object], _bridge: object) -> None:
            routed.append(str(message["type"]))

        with temporary_agent_paths(copy_tool_dirs=("common/interactive_terminal",)) as paths:
            handler = WsSessionHandler(paths)
            handler._dispatch_terminal = fake_dispatch  # type: ignore[method-assign]
            bridge = WsBridge(emit=lambda _event: None, paths=paths)
            asyncio.run(handler._dispatch({"type": "terminal.output"}, object(), bridge))
            asyncio.run(handler._dispatch({"type": "terminal.close"}, object(), bridge))

        self.assertEqual(routed, ["terminal.output", "terminal.close"])

    def test_desktop_has_terminal_visibility_contract(self) -> None:
        api = (_ROOT / "desktop" / "src" / "api" / "ws.ts").read_text(encoding="utf-8")
        panel = (_ROOT / "desktop" / "src" / "shells" / "unified" / "project-panel.ts").read_text(
            encoding="utf-8"
        )
        index = (_ROOT / "desktop" / "src" / "shells" / "unified" / "index.ts").read_text(
            encoding="utf-8"
        )
        css = (_ROOT / "desktop" / "src" / "shells" / "unified" / "unified.css").read_text(
            encoding="utf-8"
        )
        server = (_ROOT / "agent-core" / "server.py").read_text(encoding="utf-8")

        self.assertIn('type: "terminal.list.done"', api)
        self.assertIn('type: "terminal.output.done"', api)
        self.assertIn('type: "terminal.close.done"', api)
        self.assertIn("listTerminals", api)
        self.assertIn("readTerminalOutput", api)
        self.assertIn("closeTerminal", api)
        self.assertIn("terminal.list", server)
        self.assertIn("terminal.output", server)
        self.assertIn("terminal.close", server)
        self.assertIn("terminalSessions", panel)
        self.assertIn("terminalDetails", panel)
        self.assertIn("patchPanelHtml", panel)
        self.assertIn("terminalsEndedCollapsed", panel)
        self.assertIn("toggle-ended-terminals", panel)
        self.assertIn("terminals-show-all-ended", panel)
        self.assertIn("terminalHumanTitle", panel)
        self.assertIn("terminal-output", index)
        self.assertIn('id="sidebar-terminals"', index)
        self.assertIn("refreshTerminals", index)
        self.assertIn("toggle-ended-terminals", index)
        self.assertIn("sidebar-terminals", css)
        self.assertIn("sidebar-terminals-ended-list", css)
        self.assertNotIn("els.terminalsPanel.innerHTML = renderTerminalsPanel(state)", panel)
        self.assertNotIn("els.servicesPanel.innerHTML = renderServicesPanel(state)", panel)
        self.assertIn("now-focus-card", panel)
        self.assertIn('aria-label="当下焦点"', panel)

    def test_terminal_list_helpers(self) -> None:
        script = _ROOT / "desktop" / "tests" / "terminal-list.test.ts"
        result = subprocess.run(
            ["node", "--experimental-strip-types", str(script)],
            cwd=_ROOT / "desktop",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("terminal-list tests ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
