# -*- coding: utf-8 -*-
"""Dogfood 20261011: bug reports and feature asks keep their tools."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from loader import format_turn_discipline_overlay
from project_mode import should_treat_ordinary_direct_implement
from session import Session, create_new
from tests.isolation_helpers import make_temp_agent_paths
from turn_intent import _sentence_count, classify_turn, has_action_intent, is_requirement_input

# Exact t5b message from the todo-cli dogfood run.
DOGFOOD_T5B = (
    "嗯？我刚试了下，done 不存在的 id 改之前其实也没崩啊。反而现在 delete 一个不存在的 id "
    "直接报 ValueError 崩了。另外你上面说的 --file data/my-todos.json 我照着跑也崩了（目录不存在）。"
    "帮我都看一下"
)

_ACTION_LINES = (
    DOGFOOD_T5B,
    "有个 bug：done 一个不存在的 id 会崩，帮我修一下",
    "工具没异常吧？你直接改吧",
    "再加一个截止日期：add 的时候可以带 --due 2026-10-20，list 按截止日期排序，"
    "再加个 --overdue 只看过期没完成的",
    "加一个按截止日期排序和 --overdue 过滤",
    "跑测试失败了，帮我看一下",
)

_NOT_ACTION_LINES = (
    "请先告诉我如何修复这个 bug，不要动文件。",
    "为什么会报错？",
    "为什么要改这里？",
    "你改了哪些文件？",
    "增加了哪些功能？",
    "帮我看看这个目录",
    "现在数据存在哪、格式是什么样的？",
)

_LONG_BRIEF = (
    "项目做一个记账本。用户每天记几笔收支，月底看分类汇总。数据存在本地文件里。"
    "界面用命令行就够了。以后可能会加图表，但现在不急。分类先固定为餐饮、交通、住房和其他。"
    "金额统一用元，保留两位小数。导出功能暂时不需要。每笔记录包含日期、金额、分类和备注四项，"
    "备注可以留空。月度汇总按分类列出合计，并给出当月总支出和总收入。"
)


class ActionIntentTests(unittest.TestCase):
    def test_dogfood_complaint_is_execute_not_requirements(self) -> None:
        self.assertGreaterEqual(len(DOGFOOD_T5B), 120)
        self.assertFalse(is_requirement_input(DOGFOOD_T5B))
        self.assertEqual(classify_turn(DOGFOOD_T5B), "execute")

    def test_action_lines_execute(self) -> None:
        for line in _ACTION_LINES:
            with self.subTest(line=line):
                self.assertTrue(has_action_intent(line))
                self.assertEqual(classify_turn(line), "execute")

    def test_questions_and_lookups_are_not_action(self) -> None:
        for line in _NOT_ACTION_LINES:
            with self.subTest(line=line):
                self.assertFalse(has_action_intent(line))
                self.assertNotEqual(classify_turn(line), "execute")

    def test_path_and_number_dots_are_not_sentences(self) -> None:
        self.assertEqual(_sentence_count("看 data/my-todos.json 和 v1.2.3 还有 3.14"), 0)
        self.assertEqual(_sentence_count("第一句。第二句. 第三句!"), 3)


class DirectEntryKeepsToolsTests(unittest.TestCase):
    def _bound(self, entry: str) -> Session:
        paths = make_temp_agent_paths(self, prefix="dogfood-entry-")
        session = create_new(paths, conversation_id="dogfood-entry")
        session.meta.active_shell = "project"
        session.meta.project_id = "todo-cli"
        session.meta.project_root = "workspace/todo-cli"
        session.meta.project_plan_status = "confirmed"
        session.meta.project_workflow_stage = "implementation"
        session.meta.project_runaway_enabled = False
        session.meta.project_entry = entry
        return session

    def test_long_message_in_direct_project_gets_tools(self) -> None:
        self.assertEqual(classify_turn(_LONG_BRIEF), "requirements")
        session = self._bound("direct")
        self.assertTrue(
            should_treat_ordinary_direct_implement(
                session, _LONG_BRIEF, turn_intent="requirements"
            )
        )

    def test_plan_entry_keeps_requirements_read_only(self) -> None:
        session = self._bound("plan")
        self.assertFalse(
            should_treat_ordinary_direct_implement(
                session, _LONG_BRIEF, turn_intent="requirements"
            )
        )

    def test_requirements_overlay_says_no_tools_is_by_design(self) -> None:
        session = self._bound("plan")
        session.meta.phase = "S4"
        session.turn_intent = "requirements"
        overlay = format_turn_discipline_overlay(session) or ""
        self.assertIn("不是故障", overlay)
        self.assertIn("不要说工具调用异常", overlay)


if __name__ == "__main__":
    unittest.main()
