"""Phase 23 M1 — tool allowlist is all active (no topic hard lock). S-80 · S-82."""

from __future__ import annotations

import secrets
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from loader import session_evolved_allowlist, session_evolved_tools
from session import create_new
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry

from tests.isolation_helpers import make_temp_agent_paths


class ToolCatalogM1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(
            self,
            copy_tool_dirs=(
                "common/write_text",
                "coding/patch_file",
                "workflow/sort_by_extension",
                "project/report_progress",
            ),
        )
        self.registry = ToolRegistry.load(self.paths)

    def test_s80_empty_topics_allows_non_common_active(self) -> None:
        session = create_new(
            self.paths,
            conversation_id=f"_m1_s80_{secrets.token_hex(3)}",
        )
        session.meta.topics = []
        session.meta.active_shell = "grow"
        session.save()
        allow = session_evolved_allowlist(session, registry=self.registry)
        self.assertIn("patch_file", allow)
        self.assertIn("sort_by_extension", allow)
        self.assertIn("write_text", allow)
        # Catalog overlay may still be topic-narrow until M3
        listed = {t.name for t in session_evolved_tools(session, registry=self.registry)}
        self.assertIn("write_text", listed)

    def test_s82_suspect_not_in_allowlist(self) -> None:
        active = {
            t.name for t in self.registry.evolved() if t.status == "active"
        }
        allow = session_evolved_allowlist(
            create_new(self.paths, conversation_id=f"_m1_s82_{secrets.token_hex(3)}"),
            registry=self.registry,
        )
        for tool in self.registry.evolved():
            if tool.status == "suspect":
                self.assertNotIn(tool.name, allow)
            if tool.status == "active":
                self.assertIn(tool.name, active)
                self.assertIn(tool.name, allow)

    def test_executor_rejects_unknown_not_topic_gate(self) -> None:
        session = create_new(
            self.paths,
            conversation_id=f"_m1_ex_{secrets.token_hex(3)}",
        )
        session.meta.topics = []
        session.save()
        allow = set(session_evolved_allowlist(session, registry=self.registry))
        executor = ToolExecutor(
            registry=self.registry,
            session=ExecutorSession(
                session_dir=session.session_dir,
                allowed_evolved=allow,
            ),
            confirm_fn=lambda _p: True,
        )
        # patch_file is active → not "不在本会话清单"
        err = executor.validate(
            "run_evolved",
            {"tool_name": "patch_file", "arguments": {}},
        )
        if err is not None:
            self.assertNotIn("不在本会话清单", err.message)
        # unknown name still fails
        bad = executor.validate(
            "run_evolved",
            {"tool_name": "no_such_tool_xyz", "arguments": {}},
        )
        self.assertIsNotNone(bad)
        assert bad is not None
        self.assertFalse(bad.ok)
        self.assertIsNotNone(bad.error)
        assert bad.error is not None
        self.assertIn("未知", bad.error.message)


if __name__ == "__main__":
    unittest.main()
