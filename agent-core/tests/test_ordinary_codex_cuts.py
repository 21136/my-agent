# -*- coding: utf-8 -*-
"""Ordinary-mode Codex-like cuts: plan spawn, confirm budget, stage walls, harness spam."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from context import build_llm_messages
from project_api import after_turn_project_hooks
from project_mode import (
    compute_execution_stage,
    create_project,
    ordinary_skip_stage_walls,
    plan_files_fingerprint,
    project_dir,
    project_mode_block_reason,
    should_skip_ordinary_plan_spawn,
    should_treat_ordinary_direct_implement,
)
from session import build_session_chat_history, create_new
from tests.isolation_helpers import make_temp_agent_paths, temporary_agent_paths

_WRITE_APP = {
    "tool_name": "write_text",
    "arguments": {"path": "workspace/lite/src/app.py", "content": "x"},
}


class OrdinaryPlanSpawnCutTests(unittest.TestCase):
    def _bound(self, **overrides: object):
        paths = make_temp_agent_paths(self, prefix="codex-spawn-")
        session = create_new(paths, conversation_id="codex-spawn")
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

    def test_execute_intent_skips_plan_spawn_even_on_plan_entry(self) -> None:
        session = self._bound(project_entry="plan")
        self.assertTrue(
            should_skip_ordinary_plan_spawn(
                session, "帮我把登录改成 JWT", turn_intent="execute"
            )
        )
        self.assertFalse(
            should_treat_ordinary_direct_implement(
                session, "帮我把登录改成 JWT", turn_intent="execute"
            )
        )

    def test_plan_intent_still_allows_spawn(self) -> None:
        session = self._bound(project_entry="plan")
        self.assertFalse(
            should_skip_ordinary_plan_spawn(
                session, "先规划一下鉴权方案", turn_intent="plan"
            )
        )

    def test_direct_entry_skips_spawn(self) -> None:
        session = self._bound(project_entry="direct")
        self.assertTrue(should_skip_ordinary_plan_spawn(session, "继续", turn_intent="execute"))

    def test_question_and_reply_only_skip_plan_partner(self) -> None:
        from turn_intent import classify_turn

        session = self._bound()
        patrol = "巡检测试：请只用一句中文回复「巡检正常」，不要调用任何工具，也不要修改文件。"
        progress = "这个项目现在进度怎么样？一句话说。"
        for text in (patrol, progress):
            self.assertEqual(classify_turn(text), "qa", text)
            self.assertTrue(
                should_skip_ordinary_plan_spawn(session, text, turn_intent="qa"),
                text,
            )
            self.assertTrue(should_skip_ordinary_plan_spawn(session, text), text)
            self.assertFalse(
                should_treat_ordinary_direct_implement(session, text, turn_intent="qa"),
                text,
            )

        direct = self._bound(project_entry="direct")
        self.assertTrue(should_skip_ordinary_plan_spawn(direct, patrol, turn_intent="qa"))
        self.assertFalse(should_treat_ordinary_direct_implement(direct, progress, turn_intent="qa"))

    def test_unset_entry_implement_phrase_skips_spawn(self) -> None:
        from turn_intent import classify_turn

        session = self._bound()
        text = "帮我把登录改成 JWT"
        intent = classify_turn(text)
        self.assertEqual(intent, "execute")
        self.assertTrue(should_skip_ordinary_plan_spawn(session, text, turn_intent=intent))
        self.assertTrue(
            should_treat_ordinary_direct_implement(session, text, turn_intent=intent)
        )


class OrdinaryStageWallCutTests(unittest.TestCase):
    def test_light_template_skips_documentation_write_wall(self) -> None:
        with temporary_agent_paths() as paths:
            create_project(paths, "lite", project_template="light")
            session = create_new(paths, conversation_id="lite-walls")
            session.meta.active_shell = "project"
            session.meta.project_id = "lite"
            session.meta.project_root = "workspace/lite"
            session.meta.project_plan_status = "confirmed"
            session.meta.project_workflow_stage = "documentation"
            session.meta.project_runaway_enabled = False
            self.assertTrue(ordinary_skip_stage_walls(session))
            reason = project_mode_block_reason(
                active_shell="project",
                project_root="workspace/lite",
                plan_status="confirmed",
                workflow_stage="documentation",
                tool_name="run_evolved",
                arguments=_WRITE_APP,
                agent_paths=paths,
                skip_stage_walls=True,
            )
            self.assertIsNone(reason)

    def test_direct_entry_skips_design_write_wall(self) -> None:
        reason = project_mode_block_reason(
            active_shell="project",
            project_root="workspace/demo",
            plan_status="confirmed",
            workflow_stage="design",
            tool_name="run_evolved",
            arguments={
                "tool_name": "write_text",
                "arguments": {"path": "workspace/demo/src/app.py", "content": "x"},
            },
            skip_stage_walls=True,
        )
        self.assertIsNone(reason)

    def test_plan_entry_standard_keeps_documentation_wall(self) -> None:
        reason = project_mode_block_reason(
            active_shell="project",
            project_root="workspace/demo",
            plan_status="confirmed",
            workflow_stage="documentation",
            tool_name="run_evolved",
            arguments={
                "tool_name": "write_text",
                "arguments": {"path": "workspace/demo/src/app.py", "content": "x"},
            },
            skip_stage_walls=False,
        )
        self.assertIsNone(reason)


    def test_light_draft_is_not_an_execution_blocker(self) -> None:
        with temporary_agent_paths() as paths:
            root = create_project(paths, "lite-draft", project_template="light")
            from project_manifest import load_manifest, manifest_path

            manifest = load_manifest(manifest_path(root))
            result = compute_execution_stage(
                project_id="lite-draft",
                plan_status="draft",
                manifest=manifest,
                project_root=root,
                workflow_stage="requirements",
            )
            self.assertNotEqual(result["reason"], "plan_not_confirmed")
            self.assertEqual(result["blockers"], [])
            self.assertEqual(result["status"], "ready")



class OrdinaryHarnessSpamCutTests(unittest.TestCase):
    def test_history_hides_harness_user_and_assistant_lines(self) -> None:
        paths = make_temp_agent_paths(self, prefix="harness-hist-")
        session = create_new(paths, conversation_id="harness-hist")
        session.append_message(
            {
                "role": "user",
                "content": "[Harness] [狂奔续接] 先读 RUNAWAY-PROGRESS.md 与验收清单，不要重复已完成项。",
            }
        )
        session.append_message({"role": "user", "content": "帮我改 app.py"})
        session.append_message(
            {"role": "assistant", "content": "[Harness] 狂奔继续。当前任务：T-001。"}
        )
        session.append_message({"role": "assistant", "content": "已经改好了。"})
        items = build_session_chat_history(session)
        texts = [item["text"] for item in items]
        self.assertEqual(texts, ["帮我改 app.py", "已经改好了。"])
        self.assertFalse(any("[Harness]" in text for text in texts))
        self.assertFalse(any("狂奔续接" in text for text in texts))

    def test_ordinary_llm_payload_drops_harness_lines(self) -> None:
        paths = make_temp_agent_paths(self, prefix="harness-llm-")
        session = create_new(paths, conversation_id="harness-llm")
        session.meta.project_runaway_enabled = False
        session.append_message({"role": "user", "content": "继续"})
        session.append_message(
            {
                "role": "user",
                "content": "[Harness] [狂奔续接] 先读 RUNAWAY-PROGRESS.md",
            }
        )
        session.append_message({"role": "assistant", "content": "好的，开始改。"})
        payload = build_llm_messages(session)
        texts = [str(msg.get("content") or "") for msg in payload]
        self.assertTrue(any("继续" in text for text in texts))
        self.assertFalse(any(text.startswith("[Harness]") for text in texts))


class OrdinaryConfirmBudgetTests(unittest.TestCase):
    def test_second_safe_confirm_is_covered(self) -> None:
        from tools.executor import ToolExecutor

        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            session = create_new(paths, conversation_id="confirm-budget")
            session.meta.active_shell = "project"
            session.meta.project_id = "demo"
            session.meta.project_root = "workspace/demo"
            session.meta.project_plan_status = "confirmed"
            session.meta.project_workflow_stage = "implementation"
            session.meta.project_runaway_enabled = False
            session.save()
            (paths.workspace / "demo").mkdir(parents=True, exist_ok=True)
            executor = ToolExecutor.create(paths=paths, session_dir=session.session_dir)
            executor.session.active_shell = "project"
            executor.session.project_root = "workspace/demo"
            executor.session.project_id = "demo"
            executor.session.project_plan_status = "confirmed"
            executor.session.runaway_enabled = False
            builtin = executor.registry.get_builtin("run_evolved")
            evolved = executor.registry.get_evolved("write_text")
            args = {
                "tool_name": "write_text",
                "arguments": {
                    "path": "workspace/demo/src/app.py",
                    "content": "x",
                    "on_conflict": "overwrite",
                },
            }
            self.assertFalse(
                executor._ordinary_confirm_is_covered(
                    builtin, evolved, args, tool_name="run_evolved"
                )
            )
            executor.session.ordinary_visible_confirms_this_turn = 1
            self.assertTrue(
                executor._ordinary_confirm_is_covered(
                    builtin, evolved, args, tool_name="run_evolved"
                )
            )

    def test_git_push_is_not_covered_by_ordinary_budget(self) -> None:
        from tools.executor import ToolExecutor

        with temporary_agent_paths(copy_tool_dirs=("coding/git_push",)) as paths:
            session = create_new(paths, conversation_id="confirm-push")
            session.meta.active_shell = "project"
            session.meta.project_root = "workspace/demo"
            session.save()
            executor = ToolExecutor.create(paths=paths, session_dir=session.session_dir)
            executor.session.active_shell = "project"
            executor.session.project_root = "workspace/demo"
            executor.session.runaway_enabled = False
            executor.session.ordinary_visible_confirms_this_turn = 1
            builtin = executor.registry.get_builtin("run_evolved")
            evolved = executor.registry.get_evolved("git_push")
            if evolved is None:
                self.skipTest("git_push tool not present in this checkout")
            self.assertTrue(
                executor._ordinary_confirm_is_destructive(
                    builtin, evolved, {"tool_name": "git_push", "arguments": {}},
                    tool_name="run_evolved",
                )
            )
            self.assertFalse(
                executor._ordinary_confirm_is_covered(
                    builtin, evolved, {"tool_name": "git_push", "arguments": {}},
                    tool_name="run_evolved",
                )
            )


class OrdinaryPlanRequestTests(unittest.TestCase):
    def _bind(self, paths, pid: str, entry: str):
        session = create_new(paths, conversation_id=f"plan-req-{pid}")
        session.meta.active_shell = "project"
        session.meta.project_id = pid
        session.meta.project_root = f"workspace/{pid}"
        session.meta.project_plan_status = "draft"
        session.meta.project_workflow_stage = "requirements"
        session.meta.project_runaway_enabled = False
        session.meta.project_entry = entry
        session.plan_files_fingerprint_at_turn_start = plan_files_fingerprint(paths, pid)
        return session

    def _emit(self, session, paths, text: str, *, intent: str | None = None) -> list[dict]:
        session.turn_intent = intent
        session.messages.append({"role": "user", "content": text})
        events: list[dict] = []
        after_turn_project_hooks(session, paths, events.append)
        return [event for event in events if event.get("type") == "plan.request"]

    def test_question_reply_and_recall_skip_plan_request_for_both_entries(self) -> None:
        turns = (
            ("这个项目现在进度怎么样？一句话说。", None),
            ("请只回复一句：巡检正常", None),
            ("刚刚我们说了什么", "recall"),
        )
        for entry in ("plan", "direct"):
            for text, intent in turns:
                with self.subTest(entry=entry, text=text):
                    with temporary_agent_paths() as paths:
                        pid = f"ask-{entry}"
                        create_project(paths, pid)
                        session = self._bind(paths, pid, entry)
                        self.assertEqual(self._emit(session, paths, text, intent=intent), [])

    def test_plan_turn_still_emits_for_both_entries(self) -> None:
        for entry in ("plan", "direct"):
            with self.subTest(entry=entry):
                with temporary_agent_paths() as paths:
                    pid = f"plan-{entry}"
                    create_project(paths, pid)
                    session = self._bind(paths, pid, entry)
                    requests = self._emit(
                        session, paths, "先规划一下鉴权方案", intent="plan"
                    )
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(requests[0]["plan_status"], "draft")

    def test_question_that_changes_the_plan_still_emits_for_both_entries(self) -> None:
        for entry in ("plan", "direct"):
            with self.subTest(entry=entry):
                with temporary_agent_paths() as paths:
                    pid = f"dirty-{entry}"
                    create_project(paths, pid)
                    session = self._bind(paths, pid, entry)
                    tasks = project_dir(paths, pid) / "TASKS.md"
                    tasks.write_text(
                        tasks.read_text(encoding="utf-8") + "\n## Phase 9\n- [ ] T-009 extra\n",
                        encoding="utf-8",
                    )
                    requests = self._emit(session, paths, "这个项目现在进度怎么样？")
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(requests[0]["project_id"], pid)


class OrdinaryDesktopContractTests(unittest.TestCase):
    def test_direct_entry_hides_dual_draft_cta(self) -> None:
        source = (
            Path(__file__).resolve().parents[2]
            / "desktop"
            / "src"
            / "shells"
            / "unified"
            / "project-panel.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("projectEntry !== \"direct\"", source)
        self.assertIn("project_entry", source)

    def test_chat_history_filters_harness_prefix(self) -> None:
        source = (
            Path(__file__).resolve().parents[2]
            / "desktop"
            / "src"
            / "shells"
            / "chat-state.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("function isHarnessChatText", source)
        self.assertIn("[Harness]", source)
        self.assertIn("[狂奔续接]", source)

    def test_design_cta_not_tied_to_documentation_stage(self) -> None:
        source = (
            Path(__file__).resolve().parents[2]
            / "desktop"
            / "src"
            / "shells"
            / "unified"
            / "project-panel.ts"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            "state.needsDesignConfirm || state.workflowStage === \"documentation\"",
            source,
        )
        self.assertIn("if (state.needsDesignConfirm)", source)


if __name__ == "__main__":
    unittest.main()
