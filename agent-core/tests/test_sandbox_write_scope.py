"""Session write_scope_root enforcement (SANDBOX-EVOLUTION)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from paths import AgentPaths, PathDeniedForWriteError, session_write_scope
from sandbox_evolution import (
    coerce_session_write_path,
    is_sandbox_agent_root,
    normalize_write_scope_root,
    sandbox_write_scope_relative,
)
from loader import format_session_overlay
from session import SessionMeta, create_new, create_sandbox_evolution_session
from tools.executor import ExecutorSession, _validate_session_write_scope, resolve_write_confirm


class SandboxWriteScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "evolve").mkdir()
        (root / "evolve" / "_index.toml").write_text("", encoding="utf-8")
        (root / "data").mkdir()
        (root / "workspace").mkdir()
        sandbox = root / ".worktrees" / "sandbox"
        sandbox.mkdir(parents=True)
        (sandbox / "evolve").mkdir()
        (sandbox / "evolve" / "_index.toml").write_text("", encoding="utf-8")
        (sandbox / "desktop").mkdir()
        (sandbox / "agent-core").mkdir()
        self.paths = AgentPaths.from_root(root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_normalize_write_scope_rejects_traversal(self) -> None:
        with self.assertRaises(Exception):
            normalize_write_scope_root("../escape")

    def test_resolve_scope_relative(self) -> None:
        rel = sandbox_write_scope_relative(self.paths)
        self.assertEqual(rel, ".worktrees/sandbox")

    def test_resolve_under_agent_for_write_respects_scope(self) -> None:
        scope = sandbox_write_scope_relative(self.paths)
        with session_write_scope(scope):
            ok = self.paths.resolve_under_agent_for_write(
                ".worktrees/sandbox/desktop/foo.ts",
                must_exist=False,
            )
            self.assertTrue(str(ok).endswith("foo.ts"))
            coerced = self.paths.resolve_under_agent_for_write("desktop/foo.ts", must_exist=False)
            self.assertIn(".worktrees", str(coerced).replace("\\", "/"))
            self.assertTrue(str(coerced).endswith("foo.ts"))
            stable_abs = str(self.paths.agent_root / "agent-core" / "server.py")
            from_abs = self.paths.resolve_under_agent_for_write(stable_abs, must_exist=False)
            self.assertIn(".worktrees", str(from_abs).replace("\\", "/"))
            with self.assertRaises(Exception):
                self.paths.resolve_under_agent_for_write("../escape.ts", must_exist=False)

    def test_sandbox_agent_root_has_no_write_scope(self) -> None:
        sandbox = self.paths.agent_root / ".worktrees" / "sandbox"
        sandbox_paths = AgentPaths.from_root(sandbox)
        self.assertTrue(is_sandbox_agent_root(sandbox_paths))
        self.assertEqual(sandbox_write_scope_relative(sandbox_paths), "")

    def test_create_sandbox_session_meta(self) -> None:
        session = create_sandbox_evolution_session(self.paths)
        self.assertEqual(session.meta.write_scope_root, ".worktrees/sandbox")
        self.assertEqual(session.meta.turn_mode, "agent")
        self.assertTrue(session.messages_path.is_file())
        reloaded = SessionMeta.from_dict(session.meta.to_dict())
        self.assertEqual(reloaded.write_scope_root, ".worktrees/sandbox")

    def test_create_sandbox_session_from_sandbox_sidecar(self) -> None:
        sandbox = self.paths.agent_root / ".worktrees" / "sandbox"
        (sandbox / "data").mkdir(exist_ok=True)
        (sandbox / "workspace").mkdir(exist_ok=True)
        sandbox_paths = AgentPaths.from_root(sandbox)
        session = create_sandbox_evolution_session(sandbox_paths)
        self.assertEqual(session.meta.write_scope_root, "")
        seed = session.messages[0]["content"]
        self.assertIn("实验树", seed)
        self.assertIn("自我修改", seed)

    def test_validate_session_write_scope_accepts_repo_relative_product_path(self) -> None:
        exec_session = ExecutorSession(write_scope_root=".worktrees/sandbox")
        result = _validate_session_write_scope(
            exec_session,
            self.paths,
            "write_text",
            {
                "tool_name": "write_text",
                "arguments": {"path": "desktop/hack.ts", "content": "x"},
            },
        )
        self.assertIsNone(result)
        denied = _validate_session_write_scope(
            exec_session,
            self.paths,
            "write_text",
            {
                "tool_name": "write_text",
                "arguments": {"path": "../outside.ts", "content": "x"},
            },
        )
        self.assertIsNotNone(denied)

    def test_overlay_tells_the_model_to_edit_here(self) -> None:
        session = create_sandbox_evolution_session(self.paths)
        overlay = format_session_overlay(session)
        self.assertIn("实验自我修改", overlay)
        self.assertIn("desktop/", overlay)
        self.assertIn("不要让用户去 Cursor", overlay)

    def test_sandbox_scope_write_does_not_ask_for_confirm(self) -> None:
        exec_session = ExecutorSession(write_scope_root=".worktrees/sandbox")
        needs, reason = resolve_write_confirm(
            evolved_name="write_text",
            arguments={"tool_name": "write_text", "arguments": {"path": "desktop/foo.ts", "content": "x"}},
            session=exec_session,
            agent_paths=self.paths,
        )
        self.assertFalse(needs)
        self.assertEqual(reason, "skip:sandbox_write_scope")
        secret, secret_reason = resolve_write_confirm(
            evolved_name="write_text",
            arguments={"tool_name": "write_text", "arguments": {"path": ".env", "content": "x"}},
            session=exec_session,
            agent_paths=self.paths,
        )
        self.assertTrue(secret)
        self.assertNotEqual(secret_reason, "skip:sandbox_write_scope")

    def test_coerce_does_not_double_prefix(self) -> None:
        scope = ".worktrees/sandbox"
        already = coerce_session_write_path(self.paths, ".worktrees/sandbox/desktop/a.ts", scope)
        self.assertEqual(already, ".worktrees/sandbox/desktop/a.ts")
        self.assertEqual(
            coerce_session_write_path(self.paths, "../desktop/a.ts", scope),
            "../desktop/a.ts",
        )


if __name__ == "__main__":
    unittest.main()
