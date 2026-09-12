# -*- coding: utf-8 -*-
"""Ordinary-mode direct-implement plan gate + verify.py confirm skips."""

from __future__ import annotations

import unittest


class DirectImplementMarkersTests(unittest.TestCase):
    def test_markers_detect_direct_implement(self) -> None:
        from project_mode import is_direct_implement_request

        self.assertTrue(is_direct_implement_request("不要只出计划，直接实现 T-001"))
        self.assertTrue(is_direct_implement_request("别计划，直接改 app.py"))
        self.assertTrue(is_direct_implement_request("just implement the CLI now"))
        self.assertTrue(is_direct_implement_request("项目 直接实现"))
        self.assertFalse(is_direct_implement_request("先规划一下整体架构"))

    def test_desktop_direct_implement_command_falls_through_until_m1(self) -> None:
        from project_cli import parse_project_command

        # M3 Desktop CTA sends this command. M1 (PR #2) will register the verb;
        # until then it must reach chat phrase detection, not error as unknown CLI.
        self.assertIsNone(parse_project_command("项目 直接实现"))


class VerifyCommandPolicyTests(unittest.TestCase):
    def test_verify_py_is_build_test(self) -> None:
        from run_command_policy import classify_run_command, run_command_requires_confirm

        self.assertEqual(classify_run_command("python verify.py"), "build_test")
        self.assertEqual(classify_run_command("python workspace/demo/verify.py"), "build_test")
        needs, reason = run_command_requires_confirm(
            command="python verify.py",
            working_dir="workspace/demo",
            project_root="workspace/demo",
        )
        self.assertFalse(needs)
        self.assertTrue(reason.startswith("skip:"))


if __name__ == "__main__":
    unittest.main()


class DirectImplementToolFilterTests(unittest.TestCase):
    def test_build_llm_tools_hides_plan_partner(self) -> None:
        from agent import build_llm_tools
        from paths import AgentPaths
        from session import create_new

        paths = AgentPaths.discover()
        session = create_new(paths, conversation_id="direct-implement-tools")
        session.meta.project_id = "demo"
        session.meta.project_root = "workspace/demo"
        session.direct_implement_turn = True
        names = [item["function"]["name"] for item in build_llm_tools(session)]
        self.assertNotIn("plan_partner", names)
        self.assertNotIn("deliverable_review", names)
