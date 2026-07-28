"""IT-38 / IT-11 / T-1808-04～05: CLI ↔ desktop meta-command parity."""

from __future__ import annotations

import asyncio
import re
import secrets
import shutil
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from project_mode import PROJECT_ARTIFACTS, normalize_project_id, project_dir
from server import WsBridge, WsSessionHandler, _build_repl, _repl_refreshes_session_state, _run_line
from session import create_new

_PARITY_DOC = Path(__file__).resolve().parents[2] / "docs" / "CLI-DESKTOP-PARITY.md"

# Minimum meta-command fixtures referenced by CLI-DESKTOP-PARITY.md §1 (IT-38).
PARITY_META_FIXTURES: tuple[tuple[str, str], ...] = (
    ("M04", "新会话"),
    ("M05", "压缩"),
    ("M14", "项目 新建"),
)

# IT-11 / T-1808-05: meta lines exercised on both WS channels.
IT11_META_LINES: tuple[str, ...] = (
    "新会话",
    "压缩",
    "只聊",
    "动手",
)

WsChannel = Literal["user.message", "command"]


def _session_snapshot(repl, events: list[dict[str, Any]]) -> dict[str, Any]:
    turn_ends = [event for event in events if event.get("type") == "turn.end"]
    return {
        "conversation_id": repl.session.conversation_id,
        "turn_mode": repl.session.meta.turn_mode,
        "active_shell": repl.session.meta.active_shell,
        "project_id": repl.session.meta.project_id or "",
        "plan_status": repl.session.meta.project_plan_status or "",
        "turn_end_ok": turn_ends[-1].get("ok") if turn_ends else None,
        "session_banner_count": sum(1 for event in events if event.get("type") == "session.banner"),
    }


class ParityTableDocTests(unittest.TestCase):
    """Doc drift guard: parity table still lists IT-38 minimum commands."""

    def test_parity_doc_lists_minimum_meta_commands(self) -> None:
        self.assertTrue(_PARITY_DOC.is_file(), f"missing {_PARITY_DOC}")
        text = _PARITY_DOC.read_text(encoding="utf-8")
        for row_id, trigger in PARITY_META_FIXTURES:
            with self.subTest(row_id=row_id, trigger=trigger):
                self.assertIn(row_id, text)
                self.assertIn(trigger, text)

    def test_parity_doc_has_at_least_fifteen_command_families(self) -> None:
        text = _PARITY_DOC.read_text(encoding="utf-8")
        families = re.findall(r"^\| M\d{2} \|", text, flags=re.MULTILINE)
        self.assertGreaterEqual(len(families), 15)


class MetaCommandHandleLineTests(unittest.TestCase):
    """REPL handle_line path (CLI and desktop user.message text share this)."""

    def setUp(self) -> None:
        self.paths = AgentPaths.discover()
        self.outputs: list[str] = []
        self.session = create_new(
            self.paths,
            conversation_id=f"_parity_hl_{secrets.token_hex(4)}",
        )
        self.repl = _build_repl(
            self.session,
            self.paths,
            WsBridge(emit=lambda _e: None, paths=self.paths),
        )
        self.repl.output_fn = self.outputs.append
        self._session_dirs: list[str] = [self.session.conversation_id]
        self._project_ids: list[str] = []
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        for pid in self._project_ids:
            shutil.rmtree(project_dir(self.paths, normalize_project_id(pid)), ignore_errors=True)
        for sid in self._session_dirs:
            shutil.rmtree(self.paths.data / "sessions" / sid, ignore_errors=True)

    def test_new_session_replaces_conversation(self) -> None:
        """M04 · 新会话: handle_line starts a fresh session (S-03 / BUG-007 class)."""
        old_id = self.repl.session.conversation_id
        outcome = self.repl.handle_line("新会话")
        self.assertEqual(outcome, "continue")
        new_id = self.repl.session.conversation_id
        self.assertNotEqual(new_id, old_id)
        self._session_dirs.append(new_id)
        self.assertTrue((self.paths.data / "sessions" / new_id).is_dir())

    @patch("main.compact_context")
    def test_compact_meta_command_invokes_compact_context(self, mock_compact) -> None:
        """M05 · 压缩: meta-command routes to compact_context(force=True)."""
        mock_compact.return_value = SimpleNamespace(message="已压缩（测试）", compacted=True)
        outcome = self.repl.handle_line("压缩")
        self.assertEqual(outcome, "continue")
        mock_compact.assert_called_once()
        _args, kwargs = mock_compact.call_args
        self.assertIs(_args[0], self.repl.session)
        self.assertTrue(kwargs.get("force"))
        self.assertTrue(any("已压缩" in line for line in self.outputs))

    def test_project_new_meta_command_creates_workspace_triad(self) -> None:
        """M14 · 项目 新建: handle_line binds project shell and workspace triad."""
        project_id = f"parity-hl-{secrets.token_hex(4)}"
        self._project_ids.append(project_id)
        outcome = self.repl.handle_line(f"项目 新建 {project_id}")
        self.assertEqual(outcome, "continue")

        pid = normalize_project_id(project_id)
        dest = project_dir(self.paths, pid)
        self.assertTrue(dest.is_dir())
        for name in PROJECT_ARTIFACTS:
            self.assertTrue((dest / name).is_file(), f"missing {name}")

        self.assertEqual(self.repl.session.meta.active_shell, "project")
        self.assertEqual(self.repl.session.meta.project_id, pid)
        self.assertEqual(self.repl.session.meta.project_plan_status, "draft")
        self.assertTrue(any("计划待确认" in line for line in self.outputs))


class WsMetaCommandParityTests(unittest.TestCase):
    """Desktop WS user.message vs command both reach handle_line with parity outcomes."""

    def setUp(self) -> None:
        self.paths = AgentPaths.discover()
        self.events: list[dict[str, Any]] = []
        self.session = create_new(
            self.paths,
            conversation_id=f"_parity_ws_{secrets.token_hex(4)}",
        )
        self.bridge = WsBridge(emit=self.events.append, paths=self.paths)
        self.repl = _build_repl(self.session, self.paths, self.bridge)
        self.handler = WsSessionHandler(self.paths)
        self._session_dirs: list[str] = [self.session.conversation_id]
        self._project_ids: list[str] = []
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        for pid in self._project_ids:
            shutil.rmtree(project_dir(self.paths, normalize_project_id(pid)), ignore_errors=True)
        for sid in self._session_dirs:
            shutil.rmtree(self.paths.data / "sessions" / sid, ignore_errors=True)

    def _dispatch(self, message: dict[str, Any]) -> None:
        asyncio.run(self.handler._dispatch(message, self.repl, self.bridge))

    def _event_types(self) -> list[str]:
        return [event.get("type", "") for event in self.events]

    def test_user_message_new_session_emits_session_state_and_turn_end(self) -> None:
        old_id = self.repl.session.conversation_id
        self._dispatch({"type": "user.message", "text": "新会话"})
        new_id = self.repl.session.conversation_id
        self.assertNotEqual(new_id, old_id)
        self._session_dirs.append(new_id)

        types = self._event_types()
        self.assertIn("turn.end", types)
        self.assertIn("session.banner", types)
        self.assertIn("session.history", types)
        turn_end = next(event for event in self.events if event.get("type") == "turn.end")
        self.assertTrue(turn_end.get("ok"))

    def test_command_new_session_matches_user_message_refresh_semantics(self) -> None:
        """command always emit_session_state; 新会话 is in _repl_refreshes_session_state."""
        self.assertTrue(_repl_refreshes_session_state("新会话"))
        old_id = self.repl.session.conversation_id
        self._dispatch({"type": "command", "name": "新会话"})
        new_id = self.repl.session.conversation_id
        self.assertNotEqual(new_id, old_id)
        self._session_dirs.append(new_id)
        self.assertIn("session.banner", self._event_types())

    @patch("main.compact_context")
    def test_user_message_compact_refreshes_session_state(self, mock_compact) -> None:
        mock_compact.return_value = SimpleNamespace(message="digest ok", compacted=True)
        self._dispatch({"type": "user.message", "text": "压缩"})
        self.assertTrue(_repl_refreshes_session_state("压缩"))
        self.assertIn("session.banner", self._event_types())
        self.assertIn("turn.end", self._event_types())
        mock_compact.assert_called_once()

    @patch("main.compact_context")
    def test_command_compact_emits_session_state(self, mock_compact) -> None:
        mock_compact.return_value = SimpleNamespace(message="digest ok", compacted=True)
        self._dispatch({"type": "command", "name": "压缩"})
        self.assertGreaterEqual(self._event_types().count("session.banner"), 1)
        mock_compact.assert_called_once()

    def test_user_message_project_new_via_run_line(self) -> None:
        project_id = f"parity-ws-{secrets.token_hex(4)}"
        self._project_ids.append(project_id)
        asyncio.run(_run_line(self.repl, self.bridge, f"项目 新建 {project_id}", self.paths))
        pid = normalize_project_id(project_id)
        self.assertTrue(project_dir(self.paths, pid).is_dir())
        self.assertEqual(self.repl.session.meta.project_id, pid)
        turn_end = next(event for event in self.events if event.get("type") == "turn.end")
        self.assertTrue(turn_end.get("ok"))


class CommandUserMessageEquivalenceTests(unittest.TestCase):
    """IT-11 / T-1808-05: command WS and user.message share handle_line session effects."""

    def setUp(self) -> None:
        self.paths = AgentPaths.discover()
        self.handler = WsSessionHandler(self.paths)
        self._session_dirs: list[str] = []
        self._project_ids: list[str] = []
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        for pid in self._project_ids:
            shutil.rmtree(project_dir(self.paths, normalize_project_id(pid)), ignore_errors=True)
        for sid in self._session_dirs:
            shutil.rmtree(self.paths.data / "sessions" / sid, ignore_errors=True)

    def _run_meta_channel(
        self,
        channel: WsChannel,
        line: str,
        *,
        label: str,
    ) -> tuple[dict[str, Any], list[str]]:
        snap, events, _repl = self._run_meta_sequence(channel, [line], label=label)
        return snap, events

    def _run_meta_sequence(
        self,
        channel: WsChannel,
        lines: list[str],
        *,
        label: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], Any]:
        events: list[dict[str, Any]] = []
        session = create_new(
            self.paths,
            conversation_id=f"_it11_{label}_{secrets.token_hex(4)}",
        )
        self._session_dirs.append(session.conversation_id)
        bridge = WsBridge(emit=events.append, paths=self.paths)
        repl = _build_repl(session, self.paths, bridge)
        for line in lines:
            if channel == "user.message":
                asyncio.run(
                    self.handler._dispatch({"type": "user.message", "text": line}, repl, bridge)
                )
            else:
                asyncio.run(
                    self.handler._dispatch({"type": "command", "name": line}, repl, bridge)
                )
        if repl.session.conversation_id not in self._session_dirs:
            self._session_dirs.append(repl.session.conversation_id)
        return _session_snapshot(repl, events), events, repl

    def _compare_meta_channels(
        self,
        line: str,
        *,
        label: str,
        compare_project: bool = False,
    ) -> None:
        user_snap, _user_events = self._run_meta_channel("user.message", line, label=f"{label}_u")
        cmd_snap, _cmd_events = self._run_meta_channel("command", line, label=f"{label}_c")

        self.assertEqual(user_snap["turn_end_ok"], True)
        self.assertEqual(cmd_snap["turn_end_ok"], True)
        self.assertEqual(user_snap["turn_mode"], cmd_snap["turn_mode"])
        self.assertEqual(user_snap["active_shell"], cmd_snap["active_shell"])
        self.assertEqual(user_snap["plan_status"], cmd_snap["plan_status"])
        if compare_project:
            self.assertEqual(user_snap["project_id"], cmd_snap["project_id"])
        else:
            self.assertNotEqual(user_snap["conversation_id"], cmd_snap["conversation_id"])

    @patch("main.compact_context")
    def test_it11_meta_lines_match_session_effects(self, mock_compact) -> None:
        """T-1808-05: core meta commands behave the same on both WS channels."""
        mock_compact.return_value = SimpleNamespace(message="digest ok", compacted=True)

        for line in IT11_META_LINES:
            with self.subTest(line=line):
                if line == "新会话":
                    user_snap, _ = self._run_meta_channel("user.message", line, label="ns_u")
                    cmd_snap, _ = self._run_meta_channel("command", line, label="ns_c")
                    self.assertTrue(user_snap["turn_end_ok"])
                    self.assertTrue(cmd_snap["turn_end_ok"])
                    self.assertNotEqual(user_snap["conversation_id"], cmd_snap["conversation_id"])
                    continue
                if line == "只聊":
                    self._compare_meta_channels(line, label="ask")
                    self.assertEqual(
                        self._run_meta_channel("user.message", line, label="ask_chk")[0]["turn_mode"],
                        "ask",
                    )
                    continue
                if line == "动手":
                    self._compare_meta_channels(line, label="agent")
                    self.assertEqual(
                        self._run_meta_channel("user.message", line, label="agent_chk")[0]["turn_mode"],
                        "agent",
                    )
                    continue
                self._compare_meta_channels(line, label="compact")

    def test_it11_project_new_matches_on_both_channels(self) -> None:
        pid_user = f"it11-u-{secrets.token_hex(4)}"
        pid_cmd = f"it11-c-{secrets.token_hex(4)}"
        self._project_ids.extend([pid_user, pid_cmd])
        user_snap, _ = self._run_meta_channel(
            "user.message",
            f"项目 新建 {pid_user}",
            label="proj_u",
        )
        cmd_snap, _ = self._run_meta_channel(
            "command",
            f"项目 新建 {pid_cmd}",
            label="proj_c",
        )
        self.assertTrue(user_snap["turn_end_ok"])
        self.assertTrue(cmd_snap["turn_end_ok"])
        self.assertEqual(user_snap["active_shell"], "project")
        self.assertEqual(cmd_snap["active_shell"], "project")
        self.assertEqual(user_snap["plan_status"], "draft")
        self.assertEqual(cmd_snap["plan_status"], "draft")
        self.assertEqual(user_snap["project_id"], normalize_project_id(pid_user))
        self.assertEqual(cmd_snap["project_id"], normalize_project_id(pid_cmd))

    def test_it11_project_confirm_matches_on_both_channels(self) -> None:
        for channel in ("user.message", "command"):
            with self.subTest(channel=channel):
                project_id = f"it11-confirm-{secrets.token_hex(4)}"
                self._project_ids.append(project_id)
                snap, _, _ = self._run_meta_sequence(
                    channel,  # type: ignore[arg-type]
                    [f"项目 新建 {project_id}", "项目 确认"],
                    label=f"confirm_{channel}",
                )
                self.assertEqual(snap["plan_status"], "confirmed")

    def test_it11_command_always_pushes_session_state(self) -> None:
        """WS nuance: command always emit_session_state; user.message only on refresh meta."""
        user_snap, user_events = self._run_meta_channel("user.message", "只聊", label="banner_u")
        cmd_snap, cmd_events = self._run_meta_channel("command", "只聊", label="banner_c")
        self.assertEqual(user_snap["turn_mode"], cmd_snap["turn_mode"])
        self.assertEqual(user_snap["session_banner_count"], 0)
        self.assertGreaterEqual(cmd_snap["session_banner_count"], 1)
        self.assertFalse(any(event.get("type") == "session.banner" for event in user_events))
        self.assertTrue(any(event.get("type") == "session.banner" for event in cmd_events))

    def test_it11_refresh_meta_both_emit_session_state(self) -> None:
        self.assertTrue(_repl_refreshes_session_state("新会话"))
        for channel in ("user.message", "command"):
            with self.subTest(channel=channel):
                snap, events = self._run_meta_channel(channel, "新会话", label=f"refresh_{channel}")
                self.assertGreaterEqual(snap["session_banner_count"], 1)
                self.assertTrue(any(event.get("type") == "session.history" for event in events))


if __name__ == "__main__":
    unittest.main()
