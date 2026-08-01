"""Phase 22 / PROJECT-SIDEBAR §15.10 — visible plan partner (S-70～S-72 · IT-70)."""

from __future__ import annotations

import json
import secrets
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from plan_agent import (
    drop_plan_agent,
    get_plan_agent,
    looks_like_new_task_utterance,
    looks_like_plan_meta_command,
)
from project_mode import create_project, normalize_project_id, project_dir

from tests.isolation_helpers import make_temp_agent_paths


class PlanPartnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.pid = normalize_project_id(f"pp-{secrets.token_hex(3)}")
        create_project(self.paths, self.pid)
        drop_plan_agent(self.pid)
        self.agent = get_plan_agent(self.paths, self.pid)
        self.tasks = project_dir(self.paths, self.pid) / "TASKS.md"

    def tearDown(self) -> None:
        drop_plan_agent(self.pid)

    def _write_tasks(self, body: str) -> None:
        self.tasks.write_text(body.rstrip() + "\n", encoding="utf-8")

    def test_s70_exact_duplicate_auto_fix(self) -> None:
        """S-70: identical undone tasks → auto_fix removes one + notice."""
        self._write_tasks(
            "## Phase 1\n"
            "- [ ] Implement login form validation rules\n"
            "- [ ] Implement login form validation rules\n"
            "- [ ] Write unit tests for auth\n"
        )
        state = self.agent.build_state()
        actions = state.get("auto_fix_actions") or []
        self.assertTrue(any("自动清理" in a for a in actions), actions)
        text = self.tasks.read_text(encoding="utf-8")
        self.assertEqual(text.count("Implement login form validation rules"), 1)

    def test_s71_near_duplicate_not_suggested(self) -> None:
        """S-71: fuzzy near-dups (parallel scaffolds) must NOT become merge_dup cards."""
        self._write_tasks(
            "## Phase 1\n"
            "- [ ] T-003 医师 Entity + Mapper + XML\n"
            "- [ ] T-007 医保政策 Entity + Mapper + XML\n"
            "- [ ] Phase 2 测试：对医师管理模块进行后端接口与前端联调测试\n"
            "- [ ] Phase 3 测试：对医保政策模块进行后端接口与前端联调测试\n"
        )
        before = self.tasks.read_text(encoding="utf-8")
        state = self.agent.build_state()
        self.assertEqual(state.get("auto_fix_actions") or [], [])
        suggestions = state.get("suggestions") or []
        merge = [s for s in suggestions if isinstance(s, dict) and s.get("kind") == "merge_dup"]
        self.assertEqual(merge, [], suggestions)
        self.assertEqual(self.tasks.read_text(encoding="utf-8"), before)

    def test_s72_suggestions_are_structured_cards(self) -> None:
        """S-72: quality issues emit structured suggestion objects (not plain strings)."""
        self._write_tasks(
            "## Phase 1\n"
            "- [ ] short\n"
            "- [ ] A reasonably detailed follow-up task for coverage\n"
            "## Empty Phase\n"
            "## Phase 3\n"
            "- [ ] Another reasonably detailed task item here\n"
        )
        state = self.agent.build_state()
        suggestions = state.get("suggestions") or []
        self.assertTrue(suggestions)
        for s in suggestions:
            self.assertIsInstance(s, dict)
            self.assertIn("id", s)
            self.assertIn("title", s)
            self.assertIn("body", s)
            self.assertTrue(s.get("action"), msg="Ignore-only cards banned")
        # empty phase must not appear (no action)
        kinds = {s.get("kind") for s in suggestions}
        self.assertNotIn("empty_phase", kinds)
        self.assertEqual(state.get("warnings") or [], [])

    def test_too_short_accept_deletes(self) -> None:
        self._write_tasks(
            "## Phase 1\n"
            "- [ ] shorty\n"
            "- [ ] A reasonably detailed follow-up task for coverage\n"
        )
        state = self.agent.build_state()
        short = [s for s in state["suggestions"] if s.get("kind") == "too_short"]
        self.assertTrue(short)
        self.assertEqual(short[0].get("action"), "drop_task")
        self.agent.accept_suggestion(short[0]["id"])
        text = self.tasks.read_text(encoding="utf-8")
        self.assertNotIn("shorty", text)
        self.assertIn("reasonably detailed", text)

    def test_accept_rebuilds_after_memory_clear(self) -> None:
        """Sidecar restart clears _last_suggestions; accept must still resolve by id."""
        self._write_tasks(
            "## Phase 1\n"
            "- [ ] shorty\n"
            "- [ ] A reasonably detailed follow-up task for coverage\n"
        )
        state = self.agent.build_state()
        short = [s for s in state["suggestions"] if s.get("kind") == "too_short"]
        self.assertTrue(short)
        sid = short[0]["id"]
        self.agent._last_suggestions = {}
        self.agent.accept_suggestion(sid)
        self.assertNotIn("shorty", self.tasks.read_text(encoding="utf-8"))

    def test_it70_ignore_persists(self) -> None:
        self._write_tasks(
            "## Phase 1\n"
            "- [ ] short\n"
            "- [ ] A reasonably detailed follow-up task for coverage\n"
        )
        state = self.agent.build_state()
        short = [s for s in state["suggestions"] if s.get("kind") == "too_short"]
        self.assertTrue(short)
        sid = short[0]["id"]
        self.agent.ignore_suggestion(sid)
        state2 = self.agent.build_state()
        ids = {s["id"] for s in state2.get("suggestions") or []}
        self.assertNotIn(sid, ids)

    def test_meta_command_does_not_add_task(self) -> None:
        from unittest.mock import MagicMock

        self._write_tasks(
            "## Phase 1\n"
            "- [ ] Real open task with enough text here\n"
        )
        before = self.tasks.read_text(encoding="utf-8")
        self.assertTrue(looks_like_plan_meta_command("优化下任务"))

        fake_resp = MagicMock()
        fake_resp.content = '{"operations":[]}'
        fake_llm = MagicMock()
        fake_llm.chat.return_value = fake_resp
        fake_llm._plan_model = "deepseek-v4-flash"
        self.agent._llm = fake_llm

        summary = self.agent.reason_about_intent("优化下任务")
        fake_llm.chat.assert_called()
        self.assertIn("未把", summary)
        self.assertNotIn("优化下任务", self.tasks.read_text(encoding="utf-8"))
        self.assertEqual(
            self.tasks.read_text(encoding="utf-8").count("- [ ]"),
            before.count("- [ ]"),
        )

    def test_optimize_moves_open_task_out_of_done_phase(self) -> None:
        import json as json_mod
        from unittest.mock import MagicMock

        self._write_tasks(
            "## Phase 1 — scaffold\n"
            "- [x] Done scaffold item with enough text\n"
            "- [ ] Configure database connection verify\n"
            "## Phase 4 — work\n"
            "- [x] Done module item with enough text\n"
            "- [ ] Next service item with enough text\n"
        )
        lines = self.tasks.read_text(encoding="utf-8").splitlines()
        db_line = next(i for i, L in enumerate(lines) if "Configure database" in L)

        fake_resp = MagicMock()
        fake_resp.content = json_mod.dumps(
            {
                "operations": [
                    {
                        "kind": "move",
                        "line": db_line,
                        "phase": "Phase 4 — work",
                        "reason": "open task stuck in completed early phase",
                    }
                ]
            },
            ensure_ascii=False,
        )
        fake_llm = MagicMock()
        fake_llm.chat.return_value = fake_resp
        fake_llm._plan_model = "deepseek-v4-flash"
        self.agent._llm = fake_llm

        summary = self.agent.reason_about_intent("优化下计划")
        self.assertIn("移到", summary)
        text = self.tasks.read_text(encoding="utf-8")
        phase1, _, rest = text.partition("## Phase 4")
        self.assertNotIn("Configure database", phase1)
        self.assertIn("Configure database", rest)
        fake_llm.chat.assert_called()
        sys_msg = fake_llm.chat.call_args.args[0][0]["content"]
        self.assertIn("Plan Agent", sys_msg)
        self.assertIn("只执行", sys_msg)

    def test_mutate_utterance_does_not_dump_as_task(self) -> None:
        """Plan-channel: reorder/skip phrasing without LLM must not become a Phase line."""
        self._write_tasks(
            "## Phase 1\n"
            "- [ ] Real open task with enough text here\n"
        )
        self.assertFalse(looks_like_new_task_utterance("把部署提前"))
        summary = self.agent._plan_channel_fallback("把部署提前", extra="LLM 不可用")
        self.assertIn("兜底", summary)
        self.assertIn("兜底", summary)
        self.assertIn("不会把原话写进 TASKS", summary)
        self.assertNotIn("把部署提前", self.tasks.read_text(encoding="utf-8"))

    def test_bare_task_title_still_adds(self) -> None:
        self._write_tasks(
            "## Phase 1\n"
            "- [ ] Real open task with enough text here\n"
        )
        self.assertTrue(looks_like_new_task_utterance("写登录页校验"))
        summary = self.agent._plan_channel_fallback("写登录页校验")
        self.assertIn("已添加", summary)
        self.assertIn("写登录页校验", self.tasks.read_text(encoding="utf-8"))

    def test_add_honors_new_phase_title(self) -> None:
        """LLM-chosen new phase must be created — not silently remapped to Phase 1."""
        from project_mode import add_task_to_tasks_md

        self._write_tasks(
            "## Phase 1 — done stuff\n"
            "- [x] Already finished scaffold task here\n"
            "## Phase 4 — current\n"
            "- [ ] T-012 Service work still open here\n"
        )
        result = add_task_to_tasks_md(
            self.paths,
            self.pid,
            "Phase 1.5 — 数据库联通",
            "配置数据库连接并验证联通性",
        )
        text = self.tasks.read_text(encoding="utf-8")
        self.assertIn("## Phase 1.5 — 数据库联通", text)
        self.assertIn("配置数据库连接并验证联通性", text)
        self.assertEqual(result.get("phase"), "Phase 1.5 — 数据库联通")
        # Must not reopen as only child under completed Phase 1 semantics via remap
        phase1_block = text.split("## Phase 4")[0]
        self.assertNotIn("配置数据库连接", phase1_block)

    def test_progress_brief_marks_complete_phases(self) -> None:
        from plan_agent import _plan_progress_brief

        brief = _plan_progress_brief(
            "## Phase 1 — scaffold\n"
            "- [x] Done one with enough text\n"
            "## Phase 4 — work\n"
            "- [x] Partial done with enough text\n"
            "- [ ] Open one with enough text\n"
        )
        self.assertIn("已完成", brief)
        self.assertIn("进行中", brief)
        self.assertIn("Phase 4", brief)
        self.assertIn("当前前沿 Phase: Phase 4", brief)

    def test_add_honors_new_phase_title(self) -> None:
        """LLM-chosen new phase must be created — not silently remapped to Phase 1."""
        from project_mode import add_task_to_tasks_md

        self._write_tasks(
            "## Phase 1 — done stuff\n"
            "- [x] Already finished scaffold task here\n"
            "## Phase 4 — current\n"
            "- [ ] T-012 Service work still open here\n"
        )
        result = add_task_to_tasks_md(
            self.paths,
            self.pid,
            "Phase 1.5 — 数据库联通",
            "配置数据库连接并验证联通性",
        )
        text = self.tasks.read_text(encoding="utf-8")
        self.assertIn("## Phase 1.5 — 数据库联通", text)
        self.assertIn("配置数据库连接并验证联通性", text)
        self.assertEqual(result.get("phase"), "Phase 1.5 — 数据库联通")
        # Must not reopen as only child under completed Phase 1 semantics via remap
        phase1_block = text.split("## Phase 4")[0]
        self.assertNotIn("配置数据库连接", phase1_block)

    def test_partner_notices_on_plan_state(self) -> None:
        from unittest.mock import MagicMock

        self._write_tasks(
            "## Phase 1\n"
            "- [ ] Real open task with enough text here\n"
        )
        fake_resp = MagicMock()
        fake_resp.content = '{"operations":[]}'
        fake_llm = MagicMock()
        fake_llm.chat.return_value = fake_resp
        fake_llm._plan_model = "deepseek-v4-flash"
        self.agent._llm = fake_llm
        summary = self.agent.reason_about_intent("优化下计划")
        self.assertTrue(summary)
        state = self.agent.build_state()
        self.assertTrue(state.get("partner_notices"))
        self.assertTrue(any("未改" in n or "未把" in n for n in state["partner_notices"]))

    def test_progress_brief_marks_complete_phases(self) -> None:
        from plan_agent import _plan_progress_brief

        brief = _plan_progress_brief(
            "## Phase 1 — scaffold\n"
            "- [x] Done one with enough text\n"
            "## Phase 4 — work\n"
            "- [x] Partial done with enough text\n"
            "- [ ] Open one with enough text\n"
        )
        self.assertIn("已完成", brief)
        self.assertIn("进行中", brief)
        self.assertIn("Phase 4", brief)
        self.assertIn("当前前沿 Phase: Phase 4", brief)

    def test_progress_brief_flags_sandwich(self) -> None:
        from plan_agent import _PLAN_SYSTEM, _plan_progress_brief

        brief = _plan_progress_brief(
            "## Phase 1 — scaffold\n"
            "- [x] Done scaffold A with enough text\n"
            "- [x] Done scaffold B with enough text\n"
            "- [ ] Configure database connection verify\n"
            "## Phase 2 — module\n"
            "- [x] Done module item with enough text\n"
            "## Phase 4 — later\n"
            "- [ ] Next service item with enough text\n"
        )
        self.assertIn("夹心", brief)
        self.assertIn("下一项被拽回", brief)
        self.assertIn("禁止空操作", brief)
        self.assertIn("意图分流", _PLAN_SYSTEM)
        self.assertIn("夹心", _PLAN_SYSTEM)
        self.assertIn("跳段", _PLAN_SYSTEM)
        self.assertIn("并行模块", _PLAN_SYSTEM)

    def test_progress_brief_flags_jump_and_empty(self) -> None:
        from plan_agent import _plan_progress_brief

        brief = _plan_progress_brief(
            "## Phase 1 — early\n"
            "- [ ] Open A with enough text here\n"
            "- [ ] Open B with enough text here\n"
            "## Phase 2 — empty\n"
            "## Phase 3 — late\n"
            "- [x] Stolen start with enough text\n"
        )
        self.assertIn("跳段", brief)
        self.assertIn("空 Phase", brief)

    def test_next_task_exposed(self) -> None:
        self._write_tasks(
            "## Phase 1\n"
            "- [x] Done already with enough text\n"
            "- [ ] Next open task with enough text\n"
        )
        state = self.agent.build_state()
        self.assertEqual(state.get("next_task"), "Next open task with enough text")
        self.assertIsInstance(state.get("next_task_line"), int)

    def test_report_progress_prefers_task_id_over_stale_line(self) -> None:
        """Plan Partner insert shifts lines; stale task_line must not toggle neighbor."""
        from project_mode import resolve_progress_task_line

        self._write_tasks(
            "## Phase 1\n"
            "- [ ] Configure database connection and verify connectivity\n"
            "## Phase 4\n"
            "- [ ] T-011 Required material Entity + Mapper + XML\n"
            "- [ ] T-012 Required material Service + ServiceImpl\n"
        )
        lines = self.tasks.read_text(encoding="utf-8").splitlines()
        line_011 = next(i for i, L in enumerate(lines) if "T-011" in L)
        line_012 = next(i for i, L in enumerate(lines) if "T-012" in L)
        self.assertEqual(line_012, line_011 + 1)

        resolved, note = resolve_progress_task_line(
            self.paths,
            self.pid,
            task_line=line_012,  # stale / wrong neighbor
            summary="T-011 Required material Entity + Mapper + XML done",
        )
        self.assertEqual(resolved, line_011)
        self.assertIn("ignored stale", note)

        self.agent.report_progress(
            line_012,
            "T-011 Required material Entity + Mapper + XML done",
        )
        text = self.tasks.read_text(encoding="utf-8")
        self.assertIn("- [x] T-011", text)
        self.assertIn("- [ ] T-012", text)

    def test_resolve_by_task_text_ignores_stale_line(self) -> None:
        from project_mode import resolve_progress_task_line

        self._write_tasks(
            "## Phase 4\n"
            "- [ ] T-012 Required material Service + ServiceImpl\n"
            "- [ ] Configure database connection and verify connectivity\n"
        )
        lines = self.tasks.read_text(encoding="utf-8").splitlines()
        line_012 = next(i for i, L in enumerate(lines) if "T-012" in L)
        line_db = next(i for i, L in enumerate(lines) if "数据库" in L or "database" in L.lower())

        resolved, note = resolve_progress_task_line(
            self.paths,
            self.pid,
            task_line=line_db,
            summary="必备材料 Service + ServiceImpl 完成",
            task_id="T-012",
            task_text="T-012 Required material Service + ServiceImpl",
        )
        self.assertEqual(resolved, line_012)
        self.assertIn("ignored stale", note)


if __name__ == "__main__":
    unittest.main()
