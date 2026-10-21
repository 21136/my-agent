"""Tool visibility is independent of ordinary versus project session type."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import BUILTIN_TOOL_NAMES, build_builtin_tool_definition, build_llm_tools
from loader import session_evolved_allowlist
from session import create_new
from tool_proxies import PROXY_EVOLVED_TOOL_NAMES
from tools.registry import ToolRegistry
from tests.isolation_helpers import make_temp_agent_paths


class SessionAgnosticToolAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(
            self,
            copy_tool_dirs=("common/write_evolve",),
            prefix="session-agnostic-tools-",
        )
        (self.paths.workspace / "demo").mkdir()
        self.registry = ToolRegistry.load(self.paths)
        self.ordinary = create_new(self.paths, conversation_id="ordinary-tool-visibility")
        self.project = create_new(self.paths, conversation_id="project-tool-visibility")
        self.project.meta.active_shell = "project"
        self.project.meta.project_id = "demo"
        self.project.meta.project_root = "workspace/demo"

    @staticmethod
    def _names(session, registry: ToolRegistry) -> set[str]:
        return {
            item["function"]["name"]
            for item in build_llm_tools(session, registry=registry)
        }

    def test_agent_mode_tool_definitions_match_for_ordinary_and_project_sessions(self) -> None:
        expected = set(BUILTIN_TOOL_NAMES) | set(PROXY_EVOLVED_TOOL_NAMES)
        self.ordinary.meta.turn_mode = "agent"
        self.project.meta.turn_mode = "agent"

        for intent in ("execute", "read_only", "plan_only"):
            with self.subTest(intent=intent):
                self.ordinary._ordinary_work_intent = intent
                self.project._ordinary_work_intent = intent
                ordinary_names = self._names(self.ordinary, self.registry)
                project_names = self._names(self.project, self.registry)

                self.assertEqual(ordinary_names, expected)
                self.assertEqual(project_names, expected)
                self.assertEqual(ordinary_names, project_names)
                self.assertIn("deliverable_review", ordinary_names)
                self.assertIn("generate_image", ordinary_names)

    def test_project_context_builtin_descriptions_cover_ordinary_sessions(self) -> None:
        codebase = build_builtin_tool_definition("codebase_search")["function"]["description"]
        review = build_builtin_tool_definition("deliverable_review")["function"]["description"]
        self.assertIn("agent root", codebase.casefold())
        self.assertIn("project_id", review)

    def test_active_evolved_allowlist_is_session_agnostic(self) -> None:
        expected = frozenset(
            tool.name for tool in self.registry.evolved() if tool.status == "active"
        )
        self.assertIn("write_evolve", expected)
        self.assertEqual(session_evolved_allowlist(self.ordinary, registry=self.registry), expected)
        self.assertEqual(session_evolved_allowlist(self.project, registry=self.registry), expected)

    def test_authoritative_docs_describe_session_agnostic_tool_access(self) -> None:
        root = Path(__file__).resolve().parents[2]
        tools_doc = (root / "docs" / "TOOLS.md").read_text(encoding="utf-8")
        project_doc = (root / "docs" / "PROJECT-MODE.md").read_text(encoding="utf-8")
        review_doc = (root / "docs" / "DELIVERABLE-REVIEW.md").read_text(encoding="utf-8")
        map_doc = (root / "docs" / "MAP.md").read_text(encoding="utf-8")
        workbench_doc = (root / "docs" / "WORKBENCH-UI.md").read_text(encoding="utf-8")
        workshop_doc = (root / "docs" / "TOOL-WORKSHOP-PROMPTS.md").read_text(encoding="utf-8")
        index = (root / "evolve" / "tool-catalog" / "INDEX.md").read_text(encoding="utf-8")
        project_prompt = (root / "evolve" / "prompts" / "project-boundaries.md").read_text(encoding="utf-8")
        coding_prompt = (root / "evolve" / "prompts" / "coding.md").read_text(encoding="utf-8")
        evolve_bucket = (root / "evolve" / "tool-catalog" / "buckets" / "evolve.md").read_text(encoding="utf-8")

        self.assertIn("普通会话与项目会话", tools_doc)
        self.assertIn("write_evolve", project_doc)
        self.assertIn("已于 2026-10-21", project_doc)
        self.assertNotIn("项目绑定会话内仍禁 `write_evolve`", project_doc)
        self.assertIn("项目绑定会话每次须单独确认", workbench_doc)
        self.assertIn("不会单独禁止 `write_evolve`", workshop_doc)
        self.assertNotIn("project 拒 write_evolve", workshop_doc)
        self.assertIn("project_id", review_doc)
        self.assertIn("workspace/", review_doc)
        self.assertIn("project_id", map_doc)
        self.assertIn("普通会话与项目会话", index)
        for prompt in (project_prompt, coding_prompt, evolve_bucket):
            self.assertNotIn("禁止 `write_evolve`", prompt)
            self.assertIn("项目会话", prompt)
        self.assertIn("每次", evolve_bucket)
        self.assertLessEqual((root / "evolve" / "tool-catalog" / "INDEX.md").stat().st_size, 2048)

    def test_ask_mode_still_hides_execution_tools_but_keeps_read_tools(self) -> None:
        for session in (self.ordinary, self.project):
            session.meta.turn_mode = "ask"
            names = self._names(session, self.registry)
            self.assertIn("read_file", names)
            self.assertNotIn("run_evolved", names)
            self.assertNotIn("generate_image", names)
            for proxy in PROXY_EVOLVED_TOOL_NAMES:
                self.assertNotIn(proxy, names)


if __name__ == "__main__":
    unittest.main()
