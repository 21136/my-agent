from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import _is_explicit_current_project_overview, _is_explicit_current_work_overview


@pytest.mark.parametrize("text", [
    "\u5f53\u524d\u5de5\u4f5c\u6982\u51b5",
    "\u76ee\u524d\u5de5\u4f5c\u6982\u51b5",
    "\u8bf7\u7ed9\u6211\u76ee\u524d\u5de5\u4f5c\u6982\u51b5",
    "\u5e2e\u6211\u770b\u770b\u5f53\u524d\u5de5\u4f5c\u6982\u51b5\u5427",
    "\u8bf7\u7ed9\u6211\u5f53\u524d\u5de5\u4f5c\u6574\u4f53\u6982\u51b5",
    "\u5f53\u524d\u5de5\u4f5c\u603b\u89c8\uff1f",
])
def test_explicit_current_work_overview_phrasings_are_recognized(text):
    assert _is_explicit_current_work_overview(text)


@pytest.mark.parametrize("text", [
    "\u4e0d\u8981\u663e\u793a\u5f53\u524d\u5de5\u4f5c\u6574\u4f53\u6982\u51b5",
    "\u522b\u7ed9\u6211\u76ee\u524d\u5de5\u4f5c\u6982\u51b5",
    "\u8bf7\u89e3\u91ca\u201c\u5f53\u524d\u5de5\u4f5c\u6982\u51b5\u201d\u8fd9\u4e2a\u8bf4\u6cd5",
    "> \u5f53\u524d\u5de5\u4f5c\u6982\u51b5",
    "\u6211\u521a\u624d\u770b\u4e86\u76ee\u524d\u5de5\u4f5c\u6982\u51b5",
    "\u8bf7\u7ed9\u6211\u5f53\u524d\u5de5\u4f5c\u6982\u51b5\u5e76\u4fee\u590d\u767b\u5f55\u9875",
    "\u5f53\u524d\u5de5\u4f5c\u6982\u51b5\uff0c\u7136\u540e\u5e2e\u6211\u6267\u884c\u4fee\u6539",
    "\u8bf7\u6982\u62ec\u5168\u9879\u76ee\u5f53\u524d\u5de5\u4f5c\u6982\u51b5",
    "\u67e5\u770b\u6240\u6709\u9879\u76ee\u5de5\u4f5c\u603b\u89c8",
    "\u8de8\u4f1a\u8bdd\u5f53\u524d\u5de5\u4f5c\u6982\u51b5",
    "\u67e5\u6240\u6709\u4f1a\u8bdd\u7684\u76ee\u524d\u5de5\u4f5c\u6982\u51b5",
    "\u4eca\u5929\u771f\u5fd9\uff0c\u73b0\u5728\u5de5\u4f5c\u6982\u51b5\u5927\u6982\u8fd8\u4e0d\u9519",
])
def test_negative_quoted_action_or_broad_scope_is_not_routed_to_current_session_overview(text):
    assert not _is_explicit_current_work_overview(text)


@pytest.mark.parametrize("text", [
    "当前项目情况如何？", "本项目情况如何", "请给我当前项目工作概况",
    "看看本项目工作总览吧",
])
def test_explicit_current_project_read_only_questions_are_recognized(text):
    assert _is_explicit_current_project_overview(text)


@pytest.mark.parametrize("text", [
    "项目B情况如何", "查看项目 beta 的工作概况", "不要查看当前项目情况如何",
    "当前项目情况如何并修复代码", '他说“当前项目情况如何”',
])
def test_other_project_or_non_read_only_question_is_not_current_project_overview(text):
    assert not _is_explicit_current_project_overview(text)
