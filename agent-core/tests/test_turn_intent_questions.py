# -*- coding: utf-8 -*-
"""Question-shaped lines stay qa; real 写/改 requests stay execute."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_mode import should_treat_ordinary_direct_implement
from session import create_new
from tests.isolation_helpers import make_temp_agent_paths
from turn_intent import classify_turn

# Questions. A lone 写/改/计划 must not make these execute or plan.
_QUESTIONS = (
    "这个项目有哪些计划文件？分别写了什么？",
    "这个项目有哪些计划文件？",
    "现在写到哪了？",
    "代码改到哪了？",
    "计划里写了什么？",
    "TASKS 里写了哪些任务？",
    "有没有写说明文件？",
    "为什么要改这里？",
    "这个方案怎么样？",
    "验证做了吗？",
    "测试过了吗？",
    "你改了哪些文件？",
    "运行到哪了？",
    "分别写了什么",
    "这个项目现在进度怎么样？一句话说。",
    "地图里写了什么入口？",
    "验收标准写了吗",
    "实现到哪一步了？",
)

# Requests that must stay executable even when they look polite or contain ？.
_REQUESTS = (
    "帮我改一下登录按钮",
    "写一个播放器页面",
    "把登录改成 JWT",
    "能不能帮我写一个播放列表？",
    "请把颜色改成深色",
    "继续改 app.py",
    "随便写点什么",
    "帮我修复这个 bug",
    "创建一个配置文件",
    "先写登录模块",
    "直接实现 T-001",
    "可不可以帮我改一下标题？",
    "帮我把登录改成 JWT",
    "请帮我写一个设置页",
)

_PLAN = (
    "帮我列个 Phase 7 实施计划",
    "先规划一下鉴权方案",
    "用纯 Java 实现斗地主，先帮我填 PROJECT.md 和 TASKS.md",
)

_RESEARCH = (
    "查 evolve/tools/coding 有哪些工具",
    "读一下 docs/TOOLS.md 里的约定",
)

_RECALL = (
    "刚刚我们说了什么",
    "你刚才推荐的第三个工具叫什么",
)


class QuestionVersusRequestTests(unittest.TestCase):
    def test_questions_are_qa(self) -> None:
        for text in _QUESTIONS:
            with self.subTest(text=text):
                self.assertEqual(classify_turn(text), "qa", text)

    def test_requests_are_execute(self) -> None:
        for text in _REQUESTS:
            with self.subTest(text=text):
                self.assertEqual(classify_turn(text), "execute", text)

    def test_plan_research_and_recall_stay(self) -> None:
        for text in _PLAN:
            with self.subTest(text=text):
                self.assertEqual(classify_turn(text), "plan", text)
        for text in _RESEARCH:
            with self.subTest(text=text):
                self.assertEqual(classify_turn(text), "research", text)
        for text in _RECALL:
            with self.subTest(text=text):
                self.assertEqual(classify_turn(text), "recall", text)

    def test_questions_do_not_flip_unset_entry_even_if_labeled_execute(self) -> None:
        paths = make_temp_agent_paths(self, prefix="intent-q-")
        session = create_new(paths, conversation_id="intent-q")
        session.meta.active_shell = "project"
        session.meta.project_id = "demo"
        session.meta.project_root = "workspace/demo"
        session.meta.project_plan_status = "draft"
        session.meta.project_workflow_stage = "requirements"
        session.meta.project_runaway_enabled = False
        session.meta.project_entry = ""
        for text in _QUESTIONS:
            with self.subTest(text=text):
                self.assertFalse(
                    should_treat_ordinary_direct_implement(
                        session, text, turn_intent="execute"
                    ),
                    text,
                )
                self.assertFalse(
                    should_treat_ordinary_direct_implement(
                        session, text, turn_intent=classify_turn(text)
                    ),
                    text,
                )

    def test_real_requests_still_open_direct_implement(self) -> None:
        paths = make_temp_agent_paths(self, prefix="intent-r-")
        session = create_new(paths, conversation_id="intent-r")
        session.meta.active_shell = "project"
        session.meta.project_id = "demo"
        session.meta.project_root = "workspace/demo"
        session.meta.project_plan_status = "draft"
        session.meta.project_workflow_stage = "requirements"
        session.meta.project_runaway_enabled = False
        session.meta.project_entry = ""
        for text in (
            "帮我改一下登录按钮",
            "写一个播放器页面",
            "把登录改成 JWT",
            "能不能帮我写一个播放列表？",
        ):
            with self.subTest(text=text):
                intent = classify_turn(text)
                self.assertEqual(intent, "execute", text)
                self.assertTrue(
                    should_treat_ordinary_direct_implement(
                        session, text, turn_intent=intent
                    ),
                    text,
                )
