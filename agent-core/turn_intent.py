"""Lightweight turn classification for explore spawn (ORCHESTRATION §8, T-703; T-905 recall)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

TurnIntent = Literal["qa", "plan", "execute", "research", "recall"]

_EXECUTE_KEYWORDS = (
    "造",
    "实现",
    "写",
    "改",
    "更新",
    "添加",
    "创建",
    "build",
    "implement",
    "write",
    "update",
    "create",
    "scaffold",
)
_RESEARCH_ACTION_KEYWORDS = (
    "查",
    "读",
    "参照",
    "看看",
    "列出",
    "搜索",
    "调研",
    "探索",
    "explore",
    "read",
    "search",
    "list",
    "where",
)
_SPAWN_MARKERS = (
    "造",
    "实现",
    "查",
    "读",
    "参照",
    "探索",
    "调研",
    "看看",
    "列出",
    "搜索",
    "更新",
    "改",
    "写",
    "模式",
)
_PATH_MARKERS = ("evolve/", "docs/", "workspace/", "agent-core/")
_PROJECT_ARTIFACT_MARKERS = ("project.md", "tasks.md", "map.md", "三件套")

_RECALL_TIME_MARKERS = ("刚刚", "刚才", "上一轮", "上面", "前文")
_RECALL_CONTENT_MARKERS = (
    "说了什么",
    "说了啥",
    "聊到什么",
    "聊了啥",
    "聊到哪",
    "说到哪",
    "回顾",
    "recap",
    "总结一下",
)
_RECALL_REFERENCE_MARKERS = (
    "你刚才",
    "你刚刚",
    "刚才推荐",
    "刚刚推荐",
    "刚才说",
    "刚刚说",
    "刚才提",
    "刚刚提",
)
_MIXED_RESEARCH_MARKERS = (
    "顺便查",
    "顺便看",
    "顺便读",
    "同时查",
    "另外查",
    "再去查",
    "再去读",
    "顺便看看",
)


def auto_explore_enabled() -> bool:
    return os.environ.get("MY_AGENT_AUTO_EXPLORE", "1").strip() not in {"0", "false", "no"}


def is_recall_turn(user_text: str) -> bool:
    """Session-local recap: answer from thread context only (TURN-FEEDBACK §4)."""
    text = user_text.strip()
    if not text:
        return False
    lower = text.casefold()

    if any(marker in text for marker in _MIXED_RESEARCH_MARKERS):
        return False

    if any(marker in text for marker in _RECALL_REFERENCE_MARKERS):
        return True

    if any(marker in text for marker in _RECALL_TIME_MARKERS):
        if any(marker in lower for marker in _RECALL_CONTENT_MARKERS):
            return True
        if any(marker in lower for marker in ("什么", "哪", "啥")):
            return True
        if any(marker in lower for marker in ("原文", "贴出来", "再说一遍", "再贴")):
            return True

    if lower.startswith(("总结一下我们", "总结刚才", "recap")):
        return True

    return False


def _has_research_action(text: str, lower: str) -> bool:
    if any(kw in lower or kw in text for kw in _RESEARCH_ACTION_KEYWORDS):
        return True
    return any(marker in lower for marker in _PATH_MARKERS)


def intent_label(intent: TurnIntent, *, spawn_explore: bool = False) -> str:
    """User-visible one-line label for turn.start (T-905)."""
    if intent == "research" and spawn_explore:
        return "先只读探索"
    labels: dict[TurnIntent, str] = {
        "recall": "根据上文直接回顾，不调工具",
        "qa": "直接回答",
        "plan": "整理方案，少动手",
        "research": "先查阅再回答",
        "execute": "可动手执行",
    }
    return labels.get(intent, intent)


def _mentions_project_artifacts(text: str, lower: str) -> bool:
    return any(marker in lower for marker in _PROJECT_ARTIFACT_MARKERS)


def classify_turn(user_text: str) -> TurnIntent:
    """Classify user line: recall | qa | plan | execute | research."""
    text = user_text.strip()
    lower = text.casefold()

    if is_recall_turn(text):
        return "recall"

    if lower.startswith(("探索", "调研", "explore ")):
        return "research"

    if _mentions_project_artifacts(text, lower):
        return "plan"

    research_hits = sum(
        1 for kw in _RESEARCH_ACTION_KEYWORDS if kw in lower or kw in text
    )
    execute_hits = sum(1 for kw in _EXECUTE_KEYWORDS if kw in lower or kw in text)

    if research_hits and research_hits >= execute_hits:
        if any(marker in lower for marker in ("?", "？", "吗", "how", "what", "where")):
            return "research"
        if execute_hits == 0:
            return "research"

    if execute_hits:
        return "execute"

    if any(marker in lower for marker in ("计划", "方案", "步骤", "plan", "roadmap", "规划")):
        return "plan"

    if any(marker in lower for marker in ("?", "？", "吗", "是否", "why", "what", "which")):
        return "qa"

    # E1: generic 什么/哪些 without lookup action → qa, not research
    if any(marker in lower for marker in ("什么", "哪些")) and not _has_research_action(text, lower):
        return "qa"

    if research_hits:
        return "research"

    return "qa"


def should_spawn_explore(user_text: str, *, explicit: bool = False) -> bool:
    """Whether kernel runs explore subagent before parent loop (ORCHESTRATION §4.2, §8)."""
    if explicit:
        return True
    if not auto_explore_enabled():
        return False
    intent = classify_turn(user_text)
    if intent not in {"execute", "research"}:
        return False
    text = user_text.strip()
    lower = text.casefold()
    if any(marker in text for marker in _SPAWN_MARKERS):
        return True
    return any(marker in lower for marker in _PATH_MARKERS)


def spawn_explore_for_intent(intent: TurnIntent) -> bool:
    """Table: research/execute → explore; qa/plan/recall → no."""
    return intent in {"execute", "research"}


def _demo() -> None:
    cases: list[tuple[str, TurnIntent]] = [
        ("1+1 等于几", "qa"),
        ("Should we use SQLite?", "qa"),
        ("帮我列个 Phase 7 实施计划", "plan"),
        ("roadmap for orchestration", "plan"),
        ("查 evolve/tools/coding 有哪些工具", "research"),
        ("探索 docs 结构", "research"),
        ("按 run_demo 模式造 bar 工具", "execute"),
        ("implement foo following run_tests", "execute"),
        ("用纯 Java 实现斗地主，先帮我填 PROJECT.md 和 TASKS.md", "plan"),
        ("刚刚我们说了什么", "recall"),
        ("你刚才推荐的第三个工具叫什么", "recall"),
        ("我是软件工程学生，你觉得还需要加什么工具", "qa"),
        ("刚才说到哪了，顺便查一下 evolve 有没有 dep_check", "research"),
    ]
    for text, expected in cases:
        got = classify_turn(text)
        assert got == expected, f"classify_turn({text!r}) = {got!r}, want {expected!r}"
    print(f"[PASS] classify_turn ({len(cases)} cases)")

    assert is_recall_turn("刚刚我们说了什么")
    assert not is_recall_turn("查 evolve/tools 有哪些")
    assert intent_label("recall") == "根据上文直接回顾，不调工具"
    print("[PASS] T-905: recall + intent_label")

    assert not should_spawn_explore("1+1 等于几")
    assert not should_spawn_explore("刚刚我们说了什么")
    assert not should_spawn_explore("帮我规划一下架构")
    assert not should_spawn_explore("Where is T-206 documented?")
    assert should_spawn_explore("按 run_demo 模式造 bar 工具")
    assert should_spawn_explore("读 evolve/tools/coding/run_demo/tool.toml")
    assert should_spawn_explore("探索 MAP", explicit=True)
    print("[PASS] should_spawn_explore (qa/plan/recall no; execute/research + markers yes)")

    assert spawn_explore_for_intent("research")
    assert spawn_explore_for_intent("execute")
    assert not spawn_explore_for_intent("qa")
    assert not spawn_explore_for_intent("plan")
    assert not spawn_explore_for_intent("recall")
    print("[PASS] spawn_explore_for_intent matches ORCHESTRATION table")

    os.environ["MY_AGENT_AUTO_EXPLORE"] = "0"
    assert not should_spawn_explore("按 run_demo 造 bar")
    os.environ.pop("MY_AGENT_AUTO_EXPLORE", None)
    assert should_spawn_explore("按 run_demo 造 bar")
    print("[PASS] MY_AGENT_AUTO_EXPLORE=0 disables auto spawn")


if __name__ == "__main__":
    _demo()
