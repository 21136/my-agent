"""Phase 57 · Terminal harness meta (IT-570) + scope resolution (IT-575/576)."""

from __future__ import annotations

import json
import secrets
import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from host_scope import HOST_SCOPE_FILENAME, HostScopeConfig, add_host_root, save_host_scope
from session import (
    META_FILENAME,
    Session,
    SessionError,
    SessionMeta,
    create_new,
    create_terminal_session,
    generate_conversation_id,
    is_terminal_harness,
    resume_terminal_session,
)
from terminal_scope import (
    R3_PROMPT_HEADER,
    TerminalScopeFields,
    TerminalStartupDenied,
    TerminalStartupNeedsPrompt,
    apply_r3_choice,
    classify_terminal_startup,
    format_r3_prompt,
    is_terminal_startup_denied,
    resolve_terminal_cwd_candidate,
    resolve_terminal_effective_root,
    resolve_terminal_startup_scope,
    scope_fields_to_session_kwargs,
)
from tests.isolation_helpers import make_temp_agent_paths


class TerminalHarnessMetaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)

    def test_it_570_create_terminal_session_persists_harness_and_scope(self) -> None:
        cid = f"_t570-{secrets.token_hex(3)}"
        session = create_terminal_session(
            self.paths,
            conversation_id=cid,
            terminal_scope_kind="agent",
            terminal_cwd="workspace/huiyi",
        )
        self.assertEqual(session.meta.harness, "terminal")
        self.assertEqual(session.meta.terminal_scope_kind, "agent")
        self.assertEqual(session.meta.terminal_cwd, "workspace/huiyi")
        self.assertTrue(is_terminal_harness(session.meta))

        reloaded = Session.load(self.paths, cid)
        self.assertEqual(reloaded.meta.harness, "terminal")
        self.assertEqual(reloaded.meta.terminal_scope_kind, "agent")
        self.assertEqual(reloaded.meta.terminal_cwd, "workspace/huiyi")

        raw = json.loads((self.paths.data / "sessions" / cid / META_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(raw["harness"], "terminal")
        self.assertEqual(raw["terminal_scope_kind"], "agent")
        self.assertEqual(raw["terminal_cwd"], "workspace/huiyi")

    def test_it_570_desktop_create_defaults_harness_desktop(self) -> None:
        cid = f"_t570d-{secrets.token_hex(3)}"
        session = create_new(self.paths, conversation_id=cid)
        self.assertEqual(session.meta.harness, "desktop")
        self.assertEqual(session.meta.terminal_scope_kind, "")
        self.assertFalse(is_terminal_harness(session.meta))

        reloaded = Session.load(self.paths, cid)
        self.assertEqual(reloaded.meta.harness, "desktop")

    def test_it_570_legacy_meta_without_harness_loads_as_desktop(self) -> None:
        cid = f"_t570l-{secrets.token_hex(3)}"
        session_dir = self.paths.data / "sessions" / cid
        session_dir.mkdir(parents=True)
        (session_dir / "messages.jsonl").write_text("", encoding="utf-8")
        (session_dir / META_FILENAME).write_text(
            json.dumps({"topics": [], "llm_model": "", "phase": "S4"}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )

        loaded = Session.load(self.paths, cid)
        self.assertEqual(loaded.meta.harness, "desktop")

    def test_it_570_harness_immutable_on_save(self) -> None:
        cid = f"_t570i-{secrets.token_hex(3)}"
        session = create_terminal_session(
            self.paths,
            conversation_id=cid,
            terminal_scope_kind="foreign",
            terminal_foreign_root="D:/other-clone/huiyi",
        )
        session.meta.harness = "desktop"
        with self.assertRaises(SessionError):
            session.save()

    def test_it_570_terminal_forces_turn_mode_agent(self) -> None:
        cid = f"_t570a-{secrets.token_hex(3)}"
        session = create_terminal_session(
            self.paths,
            conversation_id=cid,
            terminal_scope_kind="host",
            terminal_cwd="huiyi",
            terminal_host_id="projects",
        )
        self.assertEqual(session.meta.turn_mode, "agent")

        with self.assertRaises(SessionError):
            session.set_turn_mode("ask")

        session_dir = self.paths.data / "sessions" / cid
        payload = json.loads((session_dir / META_FILENAME).read_text(encoding="utf-8"))
        payload["turn_mode"] = "ask"
        (session_dir / META_FILENAME).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        reloaded = Session.load(self.paths, cid)
        self.assertEqual(reloaded.meta.turn_mode, "agent")
        reloaded.save()
        saved = json.loads((session_dir / META_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(saved["turn_mode"], "agent")

    def test_it_570_scope_variants_roundtrip(self) -> None:
        cases = [
            {
                "terminal_scope_kind": "agent",
                "terminal_cwd": "workspace/demo",
                "terminal_foreign_root": "",
                "terminal_host_id": "",
            },
            {
                "terminal_scope_kind": "host",
                "terminal_cwd": "huiyi",
                "terminal_foreign_root": "",
                "terminal_host_id": "projects",
            },
            {
                "terminal_scope_kind": "foreign",
                "terminal_cwd": "",
                "terminal_foreign_root": "D:/other-clone/huiyi",
                "terminal_host_id": "",
            },
        ]
        for index, fields in enumerate(cases):
            cid = f"_t570s{index}-{secrets.token_hex(2)}"
            session = create_terminal_session(self.paths, conversation_id=cid, **fields)
            reloaded = Session.load(self.paths, cid)
            for key, expected in fields.items():
                self.assertEqual(getattr(reloaded.meta, key), expected, msg=f"{cid}:{key}")

    def test_session_meta_from_dict_terminal_scope_normalization(self) -> None:
        meta = SessionMeta.from_dict(
            {
                "harness": "terminal",
                "terminal_scope_kind": "AGENT",
                "terminal_cwd": "workspace\\huiyi",
                "turn_mode": "ask",
            }
        )
        self.assertEqual(meta.harness, "terminal")
        self.assertEqual(meta.terminal_scope_kind, "agent")
        self.assertEqual(meta.terminal_cwd, "workspace/huiyi")
        self.assertEqual(meta.turn_mode, "agent")


class TerminalEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)

    def test_it_571_terminal_skips_desktop_last_session(self) -> None:
        desktop_id = generate_conversation_id()
        desktop = create_new(self.paths, conversation_id=desktop_id)
        desktop.save()

        from session import read_terminal_last_session_id, write_terminal_last_session_id

        write_terminal_last_session_id(self.paths, desktop_id)
        self.assertEqual(read_terminal_last_session_id(self.paths), desktop_id)

        scope = TerminalScopeFields(
            terminal_scope_kind="agent",
            terminal_cwd="workspace/huiyi",
        )
        session = resume_terminal_session(self.paths, scope)
        self.assertEqual(session.meta.harness, "terminal")
        self.assertNotEqual(session.conversation_id, desktop_id)

    def test_it_571_assert_session_harness_rejects_desktop(self) -> None:
        from session import HarnessMismatchError, assert_session_harness, load_session_for_harness

        desktop = create_new(self.paths, conversation_id=generate_conversation_id())
        with self.assertRaises(HarnessMismatchError):
            assert_session_harness(desktop, "terminal")

        term = create_terminal_session(
            self.paths,
            conversation_id=generate_conversation_id(),
            terminal_scope_kind="agent",
            terminal_cwd="workspace/demo",
        )
        with self.assertRaises(HarnessMismatchError):
            load_session_for_harness(
                self.paths,
                term.conversation_id,
                expected="desktop",
            )

    def test_it_571_resume_desktop_skips_terminal_pointer(self) -> None:
        from session import (
            read_last_conversation_id,
            resume_desktop_or_create,
            write_last_conversation_id,
        )

        term_id = generate_conversation_id()
        term = create_terminal_session(
            self.paths,
            conversation_id=term_id,
            terminal_scope_kind="agent",
            terminal_cwd="workspace/demo",
        )
        term.save()
        write_last_conversation_id(self.paths, term_id)

        desktop_id = generate_conversation_id()
        desktop = create_new(self.paths, conversation_id=desktop_id)
        desktop.save()

        loaded = resume_desktop_or_create(self.paths)
        self.assertEqual(loaded.meta.harness, "desktop")
        self.assertEqual(loaded.conversation_id, desktop_id)
        self.assertNotEqual(read_last_conversation_id(self.paths), term_id)

    def test_it_571_list_sessions_hides_terminal(self) -> None:
        from session import list_session_summaries

        desktop_id = generate_conversation_id()
        create_new(self.paths, conversation_id=desktop_id).save()
        term_id = generate_conversation_id()
        create_terminal_session(
            self.paths,
            conversation_id=term_id,
            terminal_scope_kind="agent",
            terminal_cwd="workspace/demo",
        ).save()

        ids = {item["session_id"] for item in list_session_summaries(self.paths)}
        self.assertIn(desktop_id, ids)
        self.assertNotIn(term_id, ids)

    def test_terminal_last_session_written_on_save(self) -> None:
        from session import read_terminal_last_session_id

        cid = generate_conversation_id()
        session = create_terminal_session(
            self.paths,
            conversation_id=cid,
            terminal_scope_kind="agent",
            terminal_cwd="workspace/demo",
        )
        session.save()
        self.assertEqual(read_terminal_last_session_id(self.paths), cid)

        cid2 = generate_conversation_id()
        session2 = create_terminal_session(
            self.paths,
            conversation_id=cid2,
            terminal_scope_kind="agent",
            terminal_cwd="workspace/demo",
        )
        session2.save()
        self.assertEqual(read_terminal_last_session_id(self.paths), cid2)

    def test_resume_terminal_reuses_terminal_session(self) -> None:
        cid = generate_conversation_id()
        first = create_terminal_session(
            self.paths,
            conversation_id=cid,
            terminal_scope_kind="agent",
            terminal_cwd="workspace/demo",
        )
        first.save()
        scope = TerminalScopeFields(terminal_scope_kind="agent", terminal_cwd="workspace/other")
        resumed = resume_terminal_session(self.paths, scope)
        self.assertEqual(resumed.conversation_id, cid)


class InterfaceLockNoTakeoverTests(unittest.TestCase):
    def test_live_lock_cannot_be_stolen(self) -> None:
        from interface_lock import AcquireStatus, acquire_lock, release_lock

        paths = make_temp_agent_paths(self)
        first = acquire_lock(paths, "terminal")
        self.assertEqual(first.status, AcquireStatus.ACQUIRED)
        stolen = acquire_lock(paths, "electron", takeover=True)
        self.assertEqual(stolen.status, AcquireStatus.CONFLICT)
        assert stolen.holder is not None
        self.assertEqual(stolen.holder.ui, "terminal")
        release_lock(paths, ui="terminal")


class TerminalScopeResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)

    def test_r1_agent_workspace_cwd(self) -> None:
        project = self.paths.workspace / "huiyi"
        project.mkdir(parents=True)
        outcome = classify_terminal_startup(self.paths, project)
        self.assertIsInstance(outcome, TerminalScopeFields)
        assert isinstance(outcome, TerminalScopeFields)
        self.assertEqual(outcome.terminal_scope_kind, "agent")
        self.assertEqual(outcome.terminal_cwd, "workspace/huiyi")

    def test_r2_registered_host_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            host_dir = Path(tmp) / "projects"
            repo = host_dir / "huiyi"
            repo.mkdir(parents=True)
            config = HostScopeConfig()
            add_host_root(
                self.paths,
                config,
                host_id="projects",
                directory=host_dir,
                label="Projects",
                read=True,
                write=True,
            )
            save_host_scope(self.paths, config)
            outcome = classify_terminal_startup(self.paths, repo, config=config)
            self.assertIsInstance(outcome, TerminalScopeFields)
            assert isinstance(outcome, TerminalScopeFields)
            self.assertEqual(outcome.terminal_scope_kind, "host")
            self.assertEqual(outcome.terminal_host_id, "projects")
            self.assertEqual(outcome.terminal_cwd, "huiyi")

    def test_r3_prompt_frozen_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            prompt = format_r3_prompt(cwd)
            self.assertIn(R3_PROMPT_HEADER, prompt)
            self.assertIn(cwd.resolve().as_posix(), prompt)
            self.assertIn("[1] 仅本次使用此目录", prompt)
            self.assertIn("请选择 1/2/3：", prompt)

    def test_it_576_ssh_cwd_denied(self) -> None:
        ssh_dir = self.paths.agent_root / ".ssh"
        ssh_dir.mkdir(parents=True)
        config = HostScopeConfig()
        self.assertTrue(is_terminal_startup_denied(ssh_dir, config))
        outcome = classify_terminal_startup(self.paths, ssh_dir, config=config)
        self.assertIsInstance(outcome, TerminalStartupDenied)

    def test_it_576_classify_denied_under_ssh(self) -> None:
        ssh_dir = self.paths.agent_root / ".ssh" / "nested"
        ssh_dir.mkdir(parents=True)
        outcome = classify_terminal_startup(self.paths, ssh_dir)
        self.assertIsInstance(outcome, TerminalStartupDenied)

    def test_it_575_r3_choice_one_foreign_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            external = Path(tmp) / "other-clone" / "huiyi"
            external.mkdir(parents=True)
            outcome = classify_terminal_startup(self.paths, external)
            self.assertIsInstance(outcome, TerminalStartupNeedsPrompt)

            fields = apply_r3_choice("1", external, self.paths)
            self.assertIsNotNone(fields)
            assert fields is not None
            self.assertEqual(fields.terminal_scope_kind, "foreign")
            self.assertEqual(
                fields.terminal_foreign_root,
                external.resolve().as_posix(),
            )
            self.assertEqual(fields.terminal_cwd, "")
            self.assertEqual(fields.terminal_host_id, "")

    def test_it_575_foreign_session_persisted_without_host_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            external = Path(tmp) / "other-clone" / "huiyi"
            external.mkdir(parents=True)
            host_scope_path = self.paths.data / HOST_SCOPE_FILENAME
            self.assertFalse(host_scope_path.is_file())

            fields = apply_r3_choice("1", external, self.paths)
            assert fields is not None
            cid = f"_t575-{secrets.token_hex(3)}"
            session = create_terminal_session(
                self.paths,
                conversation_id=cid,
                **scope_fields_to_session_kwargs(fields),
            )
            session.save()
            self.assertFalse(host_scope_path.is_file())

            raw = json.loads(
                (self.paths.data / "sessions" / cid / META_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(raw["terminal_scope_kind"], "foreign")
            self.assertEqual(raw["terminal_foreign_root"], external.resolve().as_posix())

    def test_r3_choice_two_registers_host(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            external = Path(tmp) / "extproj"
            external.mkdir(parents=True)
            fields = apply_r3_choice(
                "2",
                external,
                self.paths,
                host_id="extproj",
                host_label="External",
                host_write=True,
            )
            assert fields is not None
            self.assertEqual(fields.terminal_scope_kind, "host")
            self.assertEqual(fields.terminal_host_id, "extproj")
            host_scope_path = self.paths.data / HOST_SCOPE_FILENAME
            self.assertTrue(host_scope_path.is_file())
            payload = json.loads(host_scope_path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload.get("host_roots", [])), 1)

    def test_r3_choice_three_cancels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            external = Path(tmp) / "cancel-me"
            external.mkdir()
            self.assertIsNone(apply_r3_choice("3", external, self.paths))

    def test_resolve_terminal_cwd_relative_to_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shell = Path(tmp) / "shell"
            nested = shell / "nested" / "repo"
            nested.mkdir(parents=True)
            resolved = resolve_terminal_cwd_candidate("nested/repo", shell_cwd=shell)
            self.assertEqual(resolved, nested.resolve())

    def test_resolve_terminal_startup_scope_with_input_fn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            external = Path(tmp) / "via-input"
            external.mkdir()
            outcome = resolve_terminal_startup_scope(
                self.paths,
                shell_cwd=external,
                input_fn=lambda _prompt: "1",
            )
            self.assertIsInstance(outcome, TerminalScopeFields)
            assert isinstance(outcome, TerminalScopeFields)
            self.assertEqual(outcome.terminal_scope_kind, "foreign")

    def test_resolve_terminal_effective_root_variants(self) -> None:
        workspace_repo = self.paths.workspace / "demo"
        workspace_repo.mkdir(parents=True)
        agent_meta = SessionMeta(
            harness="terminal",
            terminal_scope_kind="agent",
            terminal_cwd="workspace/demo",
        )
        self.assertEqual(
            resolve_terminal_effective_root(agent_meta, self.paths),
            workspace_repo.resolve(),
        )

        with tempfile.TemporaryDirectory() as tmp:
            foreign = Path(tmp) / "foreign-root"
            foreign.mkdir()
            foreign_meta = SessionMeta(
                harness="terminal",
                terminal_scope_kind="foreign",
                terminal_foreign_root=foreign.as_posix(),
            )
            self.assertEqual(
                resolve_terminal_effective_root(foreign_meta, self.paths),
                foreign.resolve(),
            )


class TerminalExecutorGatingTests(unittest.TestCase):
    """IT-572 / IT-573 · executor terminal wild writes + no plan gate."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(
            self,
            copy_tool_dirs=("common/write_text", "coding/patch_file"),
        )
        self.repo = self.paths.workspace / "huiyi"
        self.backend = self.repo / "backend"
        self.backend.mkdir(parents=True)
        (self.backend / "App.java").write_text("class App {}\n", encoding="utf-8")

    def _terminal_executor(self, **session_overrides) -> "ToolExecutor":
        from tools.executor import ExecutorSession, ToolExecutor
        from tools.registry import ToolRegistry

        registry = ToolRegistry.load(self.paths)
        session = ExecutorSession(
            allowed_evolved={"write_text", "patch_file", "run_command"},
            harness="terminal",
            terminal_scope_kind="agent",
            terminal_cwd="workspace/huiyi",
            **session_overrides,
        )
        return ToolExecutor(registry=registry, session=session)

    def test_it_572_write_under_effective_root_skips_confirm(self) -> None:
        from tools.executor import resolve_write_confirm

        executor = self._terminal_executor()
        needs, reason = resolve_write_confirm(
            evolved_name="write_text",
            arguments={
                "tool_name": "write_text",
                "arguments": {
                    "path": "workspace/huiyi/backend/App.java",
                    "content": "class App { void m() {} }\n",
                    "on_conflict": "overwrite",
                },
            },
            session=executor.session,
            agent_paths=self.paths,
        )
        self.assertFalse(needs)
        self.assertIn("terminal_wild", reason)

        evolved = executor.registry.get_evolved("write_text")
        builtin = executor.registry.get_builtin("run_evolved")
        assert evolved is not None and builtin is not None
        self.assertFalse(
            executor._needs_confirm(
                builtin,
                evolved,
                {
                    "tool_name": "write_text",
                    "arguments": {
                        "path": "workspace/huiyi/backend/App.java",
                        "content": "class App { void m() {} }\n",
                        "on_conflict": "overwrite",
                    },
                },
                tool_name="run_evolved",
            )
        )

    def test_it_572_write_outside_effective_root_requires_confirm(self) -> None:
        from tools.executor import resolve_write_confirm

        executor = self._terminal_executor()
        needs, reason = resolve_write_confirm(
            evolved_name="write_text",
            arguments={
                "tool_name": "write_text",
                "arguments": {
                    "path": "agent-core/foo.py",
                    "content": "x",
                    "on_conflict": "overwrite",
                },
            },
            session=executor.session,
            agent_paths=self.paths,
        )
        self.assertTrue(needs)
        self.assertEqual(reason, "confirm:outside_terminal_root")

    def test_it_573_terminal_skips_plan_gate_and_has_no_project_id(self) -> None:
        from tools.executor import ExecutorSession, ToolExecutor
        from tools.registry import ToolRegistry

        cid = generate_conversation_id()
        session = create_terminal_session(
            self.paths,
            conversation_id=cid,
            terminal_scope_kind="agent",
            terminal_cwd="workspace/huiyi",
        )
        self.assertEqual(session.meta.harness, "terminal")
        self.assertEqual(session.meta.project_id, "")

        registry = ToolRegistry.load(self.paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession(
                allowed_evolved={"write_text"},
                harness="terminal",
                terminal_scope_kind="agent",
                terminal_cwd="workspace/huiyi",
                active_shell="project",
                project_root="workspace/huiyi",
                project_plan_status="draft",
            ),
        )
        err = executor.validate(
            "run_evolved",
            {
                "tool_name": "write_text",
                "arguments": {
                    "path": "workspace/huiyi/backend/App.java",
                    "content": "class App { void m() {} }\n",
                },
            },
        )
        self.assertIsNone(err)

    def test_auto_continue_enabled_for_terminal_harness(self) -> None:
        import os

        from agent import auto_continue_enabled

        prev = os.environ.get("MY_AGENT_AUTO_CONTINUE")
        os.environ["MY_AGENT_AUTO_CONTINUE"] = "0"
        self.addCleanup(lambda: os.environ.pop("MY_AGENT_AUTO_CONTINUE", None) if prev is None else os.environ.__setitem__("MY_AGENT_AUTO_CONTINUE", prev))
        self.assertTrue(auto_continue_enabled(harness="terminal"))
        self.assertFalse(auto_continue_enabled(active_shell="project"))


class TerminalPromptLoaderTests(unittest.TestCase):
    """T-5705 · terminal.txt + loader branch."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.repo = self.paths.workspace / "huiyi"
        self.repo.mkdir(parents=True)

    def test_t_5705_terminal_session_uses_terminal_prompt(self) -> None:
        from loader import build_system_prompt, load_terminal_text
        from session import Session

        cid = generate_conversation_id()
        raw = create_terminal_session(
            self.paths,
            conversation_id=cid,
            terminal_scope_kind="agent",
            terminal_cwd="workspace/huiyi",
        )
        session = Session.load(self.paths, cid)
        session.goal = raw.goal

        terminal_text = load_terminal_text()
        self.assertIn("Terminal harness", terminal_text)
        self.assertNotIn("T-5705", terminal_text)

        loaded = build_system_prompt(session, paths=self.paths)
        self.assertIn("terminal", loaded.section_names)
        self.assertNotIn("core", loaded.section_names)
        self.assertIn("terminal_scope", loaded.section_names)
        self.assertNotIn("project_mode", loaded.section_names)
        self.assertNotIn("project_prompt", loaded.section_names)
        self.assertIn("effective_root:", loaded.prompt)
        self.assertIn(self.repo.resolve().as_posix(), loaded.prompt)
        self.assertIn("no_plan_gate", loaded.prompt)
        self.assertIn("Claude Code", loaded.prompt)
        self.assertIn("轮次纪律 · terminal", loaded.prompt)
        self.assertIn("harness: terminal", loaded.prompt)

    def test_t_5705_desktop_session_still_uses_core_prompt(self) -> None:
        from loader import build_system_prompt, load_core_text
        from session import Session

        cid = generate_conversation_id()
        create_new(self.paths, conversation_id=cid)
        session = Session.load(self.paths, cid)

        loaded = build_system_prompt(session, paths=self.paths)
        self.assertIn("core", loaded.section_names)
        self.assertNotIn("terminal_scope", loaded.section_names)
        core_text = load_core_text()
        self.assertIn(core_text.split("\n")[0], loaded.prompt)


class TerminalTurnCancelTests(unittest.TestCase):
    """IT-574 · Ctrl+C maps to turn.cancel during in-flight terminal turns."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.repo = self.paths.workspace / "huiyi"
        self.repo.mkdir(parents=True)

    def _terminal_repl(self) -> "TerminalRepl":
        from cli_terminal import TerminalRepl

        session = create_terminal_session(
            self.paths,
            conversation_id=generate_conversation_id(),
            terminal_scope_kind="agent",
            terminal_cwd="workspace/huiyi",
        )
        scope = TerminalScopeFields(terminal_scope_kind="agent", terminal_cwd="workspace/huiyi")
        return TerminalRepl.from_terminal_session(
            session,
            paths=self.paths,
            scope_fields=scope,
            input_fn=lambda _prompt: "",
            output_fn=lambda _text: None,
        )

    def test_it_574_request_cancel_aborts_slow_turn(self) -> None:
        import threading
        import time

        from agent import Agent
        from llm_client import LLMCancelledError, LLMResponse
        from main import make_confirm_fn

        class SlowCancelLLM:
            def __init__(self) -> None:
                self._cancel_event: threading.Event | None = None
                self.started = threading.Event()

            def set_cancel_event(self, event: threading.Event) -> None:
                self._cancel_event = event

            def chat(self, *_args, **_kwargs) -> LLMResponse:
                self.started.set()
                assert self._cancel_event is not None
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if self._cancel_event.is_set():
                        raise LLMCancelledError("cancelled")
                    time.sleep(0.01)
                raise AssertionError("timed out waiting for cancel")

        repl = self._terminal_repl()
        slow_llm = SlowCancelLLM()
        repl.agent = Agent.create(repl.session, llm=slow_llm)
        repl.agent.executor.confirm_fn = make_confirm_fn(
            repl.output_fn,
            repl.input_fn,
            checkpoint_gate=repl._checkpoint_gate,
            cancel_event=repl.agent.cancel_event,
        )
        guard = repl._turn_cancel_guard
        assert guard is not None

        outputs: list[str] = []
        repl.output_fn = outputs.append

        def run_turn() -> None:
            repl.handle_line("stop mid-turn")

        worker = threading.Thread(target=run_turn, daemon=True)
        worker.start()
        for _ in range(200):
            if guard.turn_busy:
                break
            time.sleep(0.01)
        self.assertTrue(guard.turn_busy)
        self.assertTrue(guard.request_cancel())
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(repl.last_turn_finish_reason, "cancelled")

    def test_it_574_confirm_honors_cancel_event(self) -> None:
        import threading

        from main import make_confirm_fn

        cancel = threading.Event()
        cancel.set()
        choice = make_confirm_fn(
            output_fn=lambda _text: None,
            input_fn=lambda _prompt: "y",
            cancel_event=cancel,
        )("preview", False)
        self.assertEqual(choice, "n")

    def test_repl_turn_cancel_guard_noop_when_idle(self) -> None:
        from main import ReplTurnCancelGuard

        repl = self._terminal_repl()
        guard = ReplTurnCancelGuard(repl)
        self.assertFalse(guard.request_cancel())


if __name__ == "__main__":
    unittest.main()
