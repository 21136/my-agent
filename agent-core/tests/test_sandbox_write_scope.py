"""Session write_scope_root enforcement (SANDBOX-EVOLUTION)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from paths import AgentPaths, PathDeniedForWriteError, session_write_scope
from sandbox_evolution import normalize_write_scope_root, sandbox_write_scope_relative
from session import SessionMeta, create_new, create_sandbox_evolution_session
from tools.executor import ExecutorSession, _validate_session_write_scope


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
            with self.assertRaises(PathDeniedForWriteError):
                self.paths.resolve_under_agent_for_write("desktop/foo.ts", must_exist=False)

    def test_create_sandbox_session_meta(self) -> None:
        session = create_sandbox_evolution_session(self.paths)
        self.assertEqual(session.meta.write_scope_root, ".worktrees/sandbox")
        self.assertTrue(session.messages_path.is_file())
        reloaded = SessionMeta.from_dict(session.meta.to_dict())
        self.assertEqual(reloaded.write_scope_root, ".worktrees/sandbox")

    def test_validate_session_write_scope_blocks_stable_path(self) -> None:
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
        self.assertIsNotNone(result)
        assert result is not None
        err_text = (result.error.message if result.error else "") or ""
        self.assertIn("写入范围", err_text)


if __name__ == "__main__":
    unittest.main()
