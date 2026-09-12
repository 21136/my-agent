# -*- coding: utf-8 -*-
"""Ordinary-mode direct-implement plan gate + project_entry + verify.py skips."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_cli import (
    confirm_direct_implement,
    parse_project_command,
    run_project_command,
    try_short_plan_confirm,
)
from project_mode import (
    ProjectModeError,
    is_direct_implement_request,
    maybe_auto_confirm_plan_for_direct_implement,
    should_treat_ordinary_direct_implement,
)
from session import Session, SessionMeta, create_new
from tests.isolation_helpers import make_temp_agent_paths


class DirectImplementMarkersTests(unittest.TestCase):
    def test_markers_detect_direct_implement(self) -> None:
        self.assertTrue(is_direct_implement_request("不要只出计划，直接实现 T-001"))
        self.assertTrue(is_direct_implement_request("别计划，直接改 app.py"))
        self.assertTrue(is_direct_implement_request("just implement the CLI now"))
        self.assertFalse(is_direct_implement_request("先规划一下整体架构"))


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


class ProjectEntryMetaTests(unittest.TestCase):
    def test_project_entry_persists_in_meta_json(self) -> None:
        paths = make_temp_agent_paths(self, prefix="entry-meta-")
        session = create_new(paths, conversation_id="entry-persist")
        self.assertEqual(session.meta.project_entry, "")
        session.meta.project_entry = "direct"
        session.save()

        reloaded = Session.load(paths, session.conversation_id)
        self.assertEqual(reloaded.meta.project_entry, "direct")

        payload = SessionMeta.from_dict({"project_entry": "plan"})
        self.assertEqual(payload.project_entry, "plan")
        junk = SessionMeta.from_dict({"project_entry": "nope"})
        self.assertEqual(junk.project_entry, "")


class ProjectEntryTreatTests(unittest.TestCase):
    def _bound(self, **overrides: object) -> Session:
        paths = make_temp_agent_paths(self, prefix="entry-treat-")
        session = create_new(paths, conversation_id="entry-treat")
        session.meta.active_shell = "project"
        session.meta.project_id = "demo"
        session.meta.project_root = "workspace/demo"
        session.meta.project_plan_status = "draft"
        session.meta.project_workflow_stage = "requirements"
        session.meta.project_runaway_enabled = False
        session.meta.project_entry = ""
        for key, value in overrides.items():
            setattr(session.meta, key, value)
        return session

    def test_entry_direct_matches_phrase_gating(self) -> None:
        session = self._bound(project_entry="direct")
        self.assertTrue(should_treat_ordinary_direct_implement(session, "随便写点什么"))
        self.assertTrue(should_treat_ordinary_direct_implement(session, ""))

        phrase = self._bound()
        self.assertTrue(should_treat_ordinary_direct_implement(phrase, "不要只出计划，直接实现 T-001"))
        self.assertFalse(should_treat_ordinary_direct_implement(phrase, "先规划一下整体架构"))

    def test_entry_direct_stays_after_confirm(self) -> None:
        session = self._bound(
            project_entry="direct",
            project_plan_status="confirmed",
            project_workflow_stage="implementation",
        )
        self.assertTrue(should_treat_ordinary_direct_implement(session, "继续改文件"))

    def test_blocked_stages_never_treat(self) -> None:
        for stage in ("documentation", "design"):
            session = self._bound(project_entry="direct", project_workflow_stage=stage)
            self.assertFalse(
                should_treat_ordinary_direct_implement(session, "直接实现"),
                msg=stage,
            )
            phrase = self._bound(project_workflow_stage=stage)
            self.assertFalse(
                should_treat_ordinary_direct_implement(phrase, "不要只出计划，直接实现"),
                msg=f"phrase-{stage}",
            )

    def test_phrase_fallback_off_when_entry_is_plan(self) -> None:
        session = self._bound(project_entry="plan")
        self.assertFalse(should_treat_ordinary_direct_implement(session, "直接实现 T-001"))

    def test_runaway_never_treats_ordinary_direct(self) -> None:
        session = self._bound(project_entry="direct", project_runaway_enabled=True)
        self.assertFalse(should_treat_ordinary_direct_implement(session, "直接实现"))

    def test_auto_confirm_skips_documentation_and_design(self) -> None:
        for stage in ("documentation", "design"):
            session = self._bound(project_workflow_stage=stage)
            self.assertIsNone(maybe_auto_confirm_plan_for_direct_implement(session))


class ProjectDirectImplementCliTests(unittest.TestCase):
    def test_parse_aliases(self) -> None:
        for text in (
            "项目 直接实现",
            "项目 直接开工",
            "project direct-implement",
            "project implement-now",
            "项目 直接 实现",
            "project direct implement",
        ):
            command = parse_project_command(text)
            self.assertIsNotNone(command, text)
            assert command is not None
            self.assertEqual(command.kind, "direct_implement", text)

    def _new_project(self, project_id: str) -> tuple[object, object]:
        paths = make_temp_agent_paths(self, prefix="entry-cli-")
        session = create_new(paths, conversation_id=f"cli-{project_id}")
        command = parse_project_command(f"项目 新建 {project_id}")
        assert command is not None
        run_project_command(session, paths, command, output_fn=lambda _line: None)
        return session, paths

    def test_cli_success_sets_entry_and_confirms(self) -> None:
        session, paths = self._new_project("direct-ok")
        self.assertEqual(session.meta.project_workflow_stage, "requirements")
        self.assertEqual(session.meta.project_plan_status, "draft")

        command = parse_project_command("项目 直接实现")
        assert command is not None
        outputs: list[str] = []
        result = run_project_command(session, paths, command, output_fn=outputs.append)

        self.assertTrue(result.meta_changed)
        self.assertEqual(session.meta.project_entry, "direct")
        self.assertEqual(session.meta.project_plan_status, "confirmed")
        self.assertEqual(session.meta.project_workflow_stage, "implementation")
        self.assertTrue(any("直接实现" in line for line in outputs))
        self.assertTrue(any("计划已确认" in line for line in outputs))
        self.assertFalse(any(line.startswith("error:") for line in outputs))

        reloaded = Session.load(paths, session.conversation_id)
        self.assertEqual(reloaded.meta.project_entry, "direct")
        self.assertEqual(reloaded.meta.project_plan_status, "confirmed")

    def test_confirm_sets_plan_entry_when_empty(self) -> None:
        session, paths = self._new_project("plan-ok")
        command = parse_project_command("项目 确认")
        assert command is not None
        run_project_command(session, paths, command, output_fn=lambda _line: None)
        self.assertEqual(session.meta.project_entry, "plan")
        self.assertEqual(session.meta.project_plan_status, "confirmed")

    def test_short_command_direct_implement(self) -> None:
        session, _paths = self._new_project("short-direct")
        outputs: list[str] = []
        handled = try_short_plan_confirm(session, "直接实现", outputs.append)
        self.assertTrue(handled)
        self.assertEqual(session.meta.project_entry, "direct")
        self.assertEqual(session.meta.project_plan_status, "confirmed")
        self.assertTrue(any("直接实现" in line for line in outputs))

    def test_reject_not_project_shell(self) -> None:
        paths = make_temp_agent_paths(self, prefix="entry-noshell-")
        session = create_new(paths, conversation_id="no-shell")
        session.meta.active_shell = "unified"
        session.meta.project_id = "demo"
        session.meta.project_root = "workspace/demo"
        session.meta.project_plan_status = "draft"
        session.meta.project_workflow_stage = "requirements"
        with self.assertRaisesRegex(ProjectModeError, "项目壳"):
            confirm_direct_implement(session)

    def test_reject_runaway(self) -> None:
        session, _paths = self._new_project("runaway-block")
        session.meta.project_runaway_enabled = True
        with self.assertRaisesRegex(ProjectModeError, "狂奔"):
            confirm_direct_implement(session)
        self.assertNotEqual(session.meta.project_entry, "direct")
        self.assertEqual(session.meta.project_plan_status, "draft")

    def test_reject_documentation_and_design(self) -> None:
        session, paths = self._new_project("stage-block")
        for stage, needle in (("documentation", "文档整理"), ("design", "设计阶段")):
            session.meta.project_workflow_stage = stage
            session.meta.project_plan_status = "draft"
            session.meta.project_entry = ""
            command = parse_project_command("项目 直接实现")
            assert command is not None
            outputs: list[str] = []
            result = run_project_command(session, paths, command, output_fn=outputs.append)
            self.assertFalse(result.meta_changed, stage)
            self.assertTrue(any(line.startswith("error:") and needle in line for line in outputs), outputs)
            self.assertEqual(session.meta.project_plan_status, "draft", stage)
            self.assertEqual(session.meta.project_entry, "", stage)

    def test_reject_already_confirmed(self) -> None:
        session, paths = self._new_project("already-confirmed")
        run_project_command(
            session,
            paths,
            parse_project_command("项目 确认"),  # type: ignore[arg-type]
            output_fn=lambda _line: None,
        )
        self.assertEqual(session.meta.project_plan_status, "confirmed")
        outputs: list[str] = []
        result = run_project_command(
            session,
            paths,
            parse_project_command("项目 直接实现"),  # type: ignore[arg-type]
            output_fn=outputs.append,
        )
        self.assertFalse(result.meta_changed)
        self.assertTrue(any("draft/plan_dirty" in line or "阶段" in line for line in outputs))
        self.assertEqual(session.meta.project_entry, "plan")


class DirectImplementToolFilterTests(unittest.TestCase):
    def test_build_llm_tools_hides_plan_partner(self) -> None:
        from agent import build_llm_tools

        paths = make_temp_agent_paths(self, prefix="entry-tools-")
        session = create_new(paths, conversation_id="direct-implement-tools")
        session.meta.project_id = "demo"
        session.meta.project_root = "workspace/demo"
        session.direct_implement_turn = True
        names = [item["function"]["name"] for item in build_llm_tools(session)]
        self.assertNotIn("plan_partner", names)
        self.assertNotIn("deliverable_review", names)

    def test_project_entry_direct_hides_plan_partner_like_phrase(self) -> None:
        from agent import build_llm_tools

        paths = make_temp_agent_paths(self, prefix="entry-tools2-")
        session = create_new(paths, conversation_id="entry-direct-tools")
        session.meta.active_shell = "project"
        session.meta.project_id = "demo"
        session.meta.project_root = "workspace/demo"
        session.meta.project_plan_status = "confirmed"
        session.meta.project_workflow_stage = "implementation"
        session.meta.project_entry = "direct"
        names = [item["function"]["name"] for item in build_llm_tools(session)]
        self.assertNotIn("plan_partner", names)
        self.assertNotIn("deliverable_review", names)

        session.meta.project_entry = ""
        names_phrase_off = [item["function"]["name"] for item in build_llm_tools(session)]
        self.assertIn("plan_partner", names_phrase_off)


if __name__ == "__main__":
    unittest.main()
