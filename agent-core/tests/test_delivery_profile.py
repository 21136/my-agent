"""Phase 47 · deliverable review + delivery profile (IT-472–IT-474, IT-473)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from loader import build_system_prompt, ensure_task_paused_text
from plan_agent import get_plan_agent
from progress_gate import report_progress_evidence_block_reason, report_progress_review_block_reason
from project_cli import (
  ProjectCommandError,
  bind_project_session,
  parse_project_command,
  run_project_command,
)
from project_mode import (
    format_project_overlay,
    get_delivery_profile,
    normalize_delivery_profile,
    ritual_task_stop_enabled,
    set_delivery_profile,
)
from session import SessionMeta, create_new
from subagent import (
    SubagentResult,
    _parse_review_verdict_from_text,
    count_review_blockers,
    format_subagent_overlay,
)
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from tools.schema import ToolErrorCode

from tests.isolation_helpers import make_temp_agent_paths, temporary_agent_paths


class DeliveryProfileMetaTests(unittest.TestCase):
    def test_default_profile_is_solo(self) -> None:
        meta = SessionMeta()
        self.assertEqual(get_delivery_profile(meta), "solo")
        self.assertFalse(ritual_task_stop_enabled(meta))

    def test_normalize_aliases(self) -> None:
        self.assertEqual(normalize_delivery_profile("strict"), "ritual")
        self.assertEqual(normalize_delivery_profile("宽松"), "solo")


class DeliveryProfileCliTests(unittest.TestCase):
    def test_bind_new_project_resets_profile_to_solo(self) -> None:
        paths = make_temp_agent_paths(self)
        session = create_new(paths)
        session.meta.project_id = "proj-a"
        session.meta.project_delivery_profile = "ritual"
        bind_project_session(session, "proj-b", plan_status="draft")
        self.assertEqual(session.meta.project_id, "proj-b")
        self.assertEqual(session.meta.project_delivery_profile, "solo")

    def test_bind_same_project_keeps_profile(self) -> None:
        paths = make_temp_agent_paths(self)
        session = create_new(paths)
        session.meta.project_id = "proj-a"
        session.meta.project_delivery_profile = "ritual"
        bind_project_session(session, "proj-a", plan_status="confirmed")
        self.assertEqual(session.meta.project_delivery_profile, "ritual")

    def test_parse_discipline_command(self) -> None:
        cmd = parse_project_command("项目 纪律 ritual")
        self.assertIsNotNone(cmd)
        assert cmd is not None
        self.assertEqual(cmd.kind, "discipline")
        self.assertEqual(cmd.project_id, "ritual")

    def test_run_discipline_updates_meta(self) -> None:
        paths = make_temp_agent_paths(self)
        session = create_new(paths)
        session.meta.active_shell = "project"
        session.meta.project_id = "demo"
        session.meta.project_root = "workspace/demo"
        outputs: list[str] = []

        def out_fn(msg: str) -> None:
            outputs.append(msg)

        cmd = parse_project_command("项目 纪律 strict")
        self.assertIsNotNone(cmd)
        assert cmd is not None
        result = run_project_command(session, paths, cmd, output_fn=out_fn)
        self.assertTrue(result.meta_changed)
        self.assertEqual(session.meta.project_delivery_profile, "ritual")
        self.assertTrue(any("ritual" in line for line in outputs))


class SoloOverlayAndPromptTests(unittest.TestCase):
    def test_solo_overlay_no_task_stop(self) -> None:
        overlay = format_project_overlay(
            project_root="workspace/x",
            project_id="x",
            plan_status="confirmed",
            delivery_profile="solo",
        )
        self.assertIn("project_delivery_profile: solo", overlay)
        self.assertNotIn("task_stop:", overlay)
        self.assertIn("deliverable_review", overlay)

    def test_ritual_overlay_keeps_task_stop(self) -> None:
        overlay = format_project_overlay(
            project_root="workspace/x",
            project_id="x",
            plan_status="confirmed",
            delivery_profile="ritual",
        )
        self.assertIn("task_stop:", overlay)

    def test_it473_solo_prompt_snapshot(self) -> None:
        from paths import AgentPaths

        paths = AgentPaths.discover()
        session = create_new(paths)
        session.meta.active_shell = "project"
        session.meta.project_id = "demo"
        session.meta.project_root = "workspace/demo"
        session.meta.project_delivery_profile = "solo"
        loaded = build_system_prompt(session, paths=paths)
        self.assertIn("project-delivery-solo", loaded.prompt)
        self.assertNotIn("回复「继续」开始下一项", loaded.prompt)
        self.assertNotIn("回复『继续』开始下一项", loaded.prompt)

    def test_it4803_solo_routes_drift_to_deliverable_review(self) -> None:
        from paths import AgentPaths

        paths = AgentPaths.discover()
        session = create_new(paths)
        session.meta.active_shell = "project"
        session.meta.project_id = "huiyi"
        session.meta.project_root = "workspace/huiyi"
        session.meta.project_delivery_profile = "solo"
        loaded = build_system_prompt(session, paths=paths)
        self.assertIn("deliverable_review", loaded.prompt)
        self.assertIn("脱节", loaded.prompt)
        self.assertIn("workspace/{project_id}", loaded.prompt)
        self.assertIn("docs/TOOLS.md", loaded.prompt)
        self.assertNotIn("看结构 / 调研 | `explore`", loaded.prompt)

    def test_solo_skips_task_paused_marker(self) -> None:
        text = ensure_task_paused_text("done", delivery_profile="solo")
        self.assertEqual(text, "done")


class SoloProgressGateTests(unittest.TestCase):
    def test_unknown_allowed_in_solo(self) -> None:
        reason = report_progress_evidence_block_reason(
            active_shell="project",
            armed_task_text="",
            turn_evidence=[],
            delivery_profile="solo",
        )
        self.assertIsNone(reason)

    def test_l1_failure_blocks_solo(self) -> None:
        from progress_gate import make_evidence_entry

        reason = report_progress_evidence_block_reason(
            active_shell="project",
            armed_task_text="任意",
            turn_evidence=[
                make_evidence_entry(
                    tool_name="run_evolved",
                    evolved_name="run_project_tests",
                    ok=False,
                )
            ],
            delivery_profile="solo",
        )
        self.assertIsNotNone(reason)
        self.assertIn("编译/测试失败", reason or "")


class SoloTaskStopTests(unittest.TestCase):
    def test_it472_solo_no_task_stop_arm(self) -> None:
        with temporary_agent_paths(
            copy_tool_dirs=("common/write_text", "project/report_progress"),
        ) as paths:
            pid = "solo-stop"
            proj = paths.workspace / pid
            proj.mkdir(parents=True)
            (proj / "TASKS.md").write_text(
                "- [ ] T-001 first\n- [ ] T-002 second\n",
                encoding="utf-8",
            )
            registry = ToolRegistry.load(paths)
            allow = {"write_text", "report_progress"}
            executor = ToolExecutor.create(
                paths=paths,
                session_dir=None,
                allowed_evolved=allow,
                confirm_fn=lambda _p, _a: "y",
            )
            executor.session.active_shell = "project"
            executor.session.project_root = f"workspace/{pid}"
            executor.session.project_id = pid
            executor.session.project_plan_status = "confirmed"
            executor.session.project_delivery_profile = "solo"
            executor.begin_turn()

            executor.run(
                "run_evolved",
                {
                    "tool_name": "write_text",
                    "arguments": {
                        "path": f"workspace/{pid}/src/A.java",
                        "content": "class A {}",
                        "on_conflict": "overwrite",
                    },
                },
            )
            result = executor.run(
                "run_evolved",
                {
                    "tool_name": "report_progress",
                    "arguments": {"summary": "T-001 done", "task_line": 0},
                },
            )
            self.assertTrue(result.ok, result.error)
            self.assertFalse(executor.session.task_stop_armed)

            second = executor.run(
                "run_evolved",
                {
                    "tool_name": "report_progress",
                    "arguments": {"summary": "double tap same turn", "task_line": 0},
                },
            )
            self.assertFalse(second.ok)
            self.assertIn(
                "report_progress",
                (second.error.message if second.error else "") or "",
            )


class PlanAgentExternalModTests(unittest.TestCase):
    def test_it474_solo_owner_edit_no_external_banner(self) -> None:
        paths = make_temp_agent_paths(self)
        pid = "owner-edit"
        proj = paths.workspace / pid
        proj.mkdir(parents=True)
        (proj / "TASKS.md").write_text("- [ ] T-001 open\n", encoding="utf-8")
        (proj / "MAP.md").write_text("# map\n", encoding="utf-8")
        (proj / "PROJECT.md").write_text("# proj\n", encoding="utf-8")

        session = create_new(paths)
        session.meta.project_id = pid
        session.meta.project_delivery_profile = "solo"

        agent = get_plan_agent(paths, pid)
        agent.build_state(session)
        (proj / "TASKS.md").write_text("- [ ] T-001 open\n- [ ] T-002 added\n", encoding="utf-8")
        state = agent.build_state(session)
        self.assertFalse(state.get("external_changes"))


class ReviewSubagentTests(unittest.TestCase):
    def test_parse_review_verdict(self) -> None:
        self.assertEqual(
            _parse_review_verdict_from_text("ok\nREVIEW_VERDICT: warn"),
            "warn",
        )

    def test_count_review_blockers(self) -> None:
        self.assertEqual(
            count_review_blockers("P0 init.sql 缺失\nwarnings: …", verdict="warn"),
            1,
        )
        self.assertEqual(count_review_blockers("无阻塞", verdict="fail"), 1)

    def test_review_overlay_format(self) -> None:
        overlay = format_subagent_overlay(
            SubagentResult(
                kind="review",
                summary="init.sql broken",
                paths_cited=["workspace/huiyi/database/init.sql"],
                tool_rounds=2,
                truncated=False,
                task="验收 huiyi",
                verdict="fail",
            )
        )
        self.assertIn("deliverable_review", overlay)
        self.assertIn("FAIL", overlay)
        self.assertIn("验收按钮", overlay)

    def test_it471_deliverable_review_executor_emits_events(self) -> None:
        with temporary_agent_paths(
            copy_tool_dirs=("common/write_text",),
        ) as paths:
            pid = "review-exec"
            proj = paths.workspace / pid
            proj.mkdir(parents=True)
            (proj / "TASKS.md").write_text("- [ ] T-001 open\n", encoding="utf-8")
            (proj / "MAP.md").write_text("# map\n", encoding="utf-8")
            (proj / "PROJECT.md").write_text("# proj\n", encoding="utf-8")

            session = create_new(paths, conversation_id=f"it471_{pid}")
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.active_shell = "project"
            session.meta.project_plan_status = "confirmed"
            session.save()

            registry = ToolRegistry.load(paths)
            exec_session = ExecutorSession.load(session.session_dir, allowed_evolved=set())
            events: list[tuple[str, dict]] = []

            def on_event(et: str, payload: dict) -> None:
                events.append((et, payload))

            executor = ToolExecutor(
                registry=registry,
                session=exec_session,
                confirm_fn=lambda _p, _a: "y",
                on_event=on_event,
            )
            mock_result = SubagentResult(
                kind="review",
                summary="P0: init.sql 缺表\nREVIEW_VERDICT: fail",
                paths_cited=[f"workspace/{pid}/database/init.sql"],
                tool_rounds=1,
                truncated=False,
                task="验收数据库",
                verdict="fail",
            )
            with patch(
                "subagent.SubagentRunner.run_deliverable_review",
                return_value=mock_result,
            ):
                result = executor.run(
                    "deliverable_review",
                    {"task": "验收数据库", "scope": "full"},
                )
            self.assertTrue(result.ok, result.error)
            types = [et for et, _ in events]
            self.assertIn("review.subagent.start", types)
            self.assertIn("review.subagent.done", types)
            done = next(payload for et, payload in events if et == "review.subagent.done")
            self.assertEqual(done.get("verdict"), "fail")
            self.assertIn("summary_preview", done)
            self.assertGreaterEqual(done.get("blockers_count", 0), 1)
            self.assertIn("[子代理摘要 · deliverable_review]", executor.session.subagent_overlay_pending or "")

    def test_scope_phase_requires_hint(self) -> None:
        with temporary_agent_paths() as paths:
            registry = ToolRegistry.load(paths)
            executor = ToolExecutor.create(
                paths=paths,
                session_dir=None,
                allowed_evolved=set(),
                confirm_fn=lambda _p, _a: "y",
            )
            executor.session.project_id = "demo"
            executor.session.project_root = "workspace/demo"
            executor.session.active_shell = "project"
            result = executor.run(
                "deliverable_review",
                {"task": "查 phase", "scope": "phase"},
            )
            self.assertFalse(result.ok)
            assert result.error is not None
            self.assertIn("phase_hint", result.error.message)


class RitualReviewProgressGateTests(unittest.TestCase):
    def test_review_block_reason_env_gated(self) -> None:
        with patch.dict("os.environ", {"RITUAL_REVIEW_BLOCKS_PROGRESS": "1"}):
            reason = report_progress_review_block_reason(
                active_shell="project",
                delivery_profile="ritual",
                last_review_verdict="fail",
                last_review_blockers_count=2,
            )
        self.assertIsNotNone(reason)
        self.assertIn("交付审查", reason or "")

    def test_solo_never_review_blocked(self) -> None:
        with patch.dict("os.environ", {"RITUAL_REVIEW_BLOCKS_PROGRESS": "1"}):
            reason = report_progress_review_block_reason(
                active_shell="project",
                delivery_profile="solo",
                last_review_verdict="fail",
                last_review_blockers_count=2,
            )
        self.assertIsNone(reason)

    def test_it471_ritual_review_blocks_report_progress(self) -> None:
        with temporary_agent_paths(
            copy_tool_dirs=("common/write_text", "project/report_progress"),
        ) as paths:
            pid = "ritual-review-gate"
            proj = paths.workspace / pid
            proj.mkdir(parents=True)
            (proj / "TASKS.md").write_text("- [ ] T-001 first\n", encoding="utf-8")

            registry = ToolRegistry.load(paths)
            allow = {"write_text", "report_progress"}
            executor = ToolExecutor.create(
                paths=paths,
                session_dir=None,
                allowed_evolved=allow,
                confirm_fn=lambda _p, _a: "y",
            )
            executor.session.active_shell = "project"
            executor.session.project_root = f"workspace/{pid}"
            executor.session.project_id = pid
            executor.session.project_plan_status = "confirmed"
            executor.session.project_delivery_profile = "ritual"
            executor.session.last_review_verdict = "fail"
            executor.session.last_review_blockers_count = 2
            executor.begin_turn()

            with patch.dict("os.environ", {"RITUAL_REVIEW_BLOCKS_PROGRESS": "1"}):
                result = executor.run(
                    "run_evolved",
                    {
                        "tool_name": "report_progress",
                        "arguments": {"summary": "T-001 done", "task_line": 0},
                    },
                )
            self.assertFalse(result.ok)
            assert result.error is not None
            self.assertEqual(
                (result.error.details or {}).get("guard_type"),
                "progress_gate_review",
            )

    def test_project_state_review_fields(self) -> None:
        paths = make_temp_agent_paths(self)
        session = create_new(paths)
        session.meta.active_shell = "project"
        session.meta.project_id = "demo"
        session.meta.project_root = "workspace/demo"
        session.meta.project_delivery_profile = "ritual"
        session.last_review_verdict = "warn"
        session.last_review_blockers_count = 2
        from project_api import project_state_payload

        payload = project_state_payload(session, paths)
        self.assertEqual(payload["delivery_profile"], "ritual")
        self.assertEqual(payload["review_verdict"], "warn")
        self.assertEqual(payload["review_blockers_count"], 2)


class ProjectCommandPassthroughTests(unittest.TestCase):
    def test_natural_language_after_project_prefix_returns_none(self) -> None:
        self.assertIsNone(
            parse_project_command("项目现在是能跑，但是离可交付肯定还不行"),
        )
        self.assertIsNone(parse_project_command("项目 现在已经可以跑了"))
        self.assertIsNone(parse_project_command("project is not ready for delivery"))

    def test_explicit_commands_still_parse(self) -> None:
        cmd = parse_project_command("项目 纪律 ritual")
        self.assertIsNotNone(cmd)
        assert cmd is not None
        self.assertEqual(cmd.kind, "discipline")
        cmd2 = parse_project_command("项目 列表")
        self.assertIsNotNone(cmd2)
        assert cmd2 is not None
        self.assertEqual(cmd2.kind, "list")

    def test_incomplete_known_command_still_errors(self) -> None:
        with self.assertRaises(ProjectCommandError):
            parse_project_command("项目 新建")

    def test_new_project_alias_requires_valid_id(self) -> None:
        self.assertIsNone(parse_project_command("新项目能跑了吗"))
        cmd = parse_project_command("新项目 huiyi-demo")
        self.assertIsNotNone(cmd)
        assert cmd is not None
        self.assertEqual(cmd.kind, "new")
        self.assertEqual(cmd.project_id, "huiyi-demo")


if __name__ == "__main__":
    unittest.main()
