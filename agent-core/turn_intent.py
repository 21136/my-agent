"""Lightweight turn classification for explore spawn (ORCHESTRATION §8, T-703; T-905 recall)."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Literal

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

TurnIntent = Literal["qa", "plan", "execute", "research", "recall", "requirements"]
OrdinaryWorkIntent = Literal["read_only", "plan_only", "execute"]


_EXECUTE_KEYWORDS = (
    "造",
    "实现",
    "写",
    "改",
    "更新",
    "添加",
    "创建",
    "修复",
    "排查",
    "定位",
    "调试",
    "测试",
    "验证",
    "验收",
    "运行",
    "执行",
    "build",
    "implement",
    "write",
    "update",
    "create",
    "scaffold",
    "fix",
    "debug",
    "test",
    "verify",
    "run",
    "patch",
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
_ATTACHMENT_PREFIX = "[附件]"


def user_text_without_attachment_block(user_text: str) -> str:
    """Strip desktop drag-drop prefix so workspace/_drops paths do not trigger explore."""
    text = (user_text or "").strip()
    if not text.startswith(_ATTACHMENT_PREFIX):
        return text
    if "\n\n" not in text:
        return ""
    body = text.split("\n\n", 1)[1].strip()
    return body


def user_message_has_attachment_block(user_text: str) -> bool:
    return (user_text or "").strip().startswith(_ATTACHMENT_PREFIX)


_ATTACHMENT_QA_MARKERS = (
    "面试",
    "模拟面试",
    "mock interview",
    "根据简历",
    "根据附件",
    "帮我看",
    "看一下",
    "请回答",
    "请问",
    "解释一下",
    "说说",
)


def is_attachment_qa_turn(user_text: str) -> bool:
    """Drag-drop + question: answer from staged content, not PRD/requirements mode."""
    if not user_message_has_attachment_block(user_text):
        return False
    body = user_text_without_attachment_block(user_text)
    if not body.strip():
        return True
    lower = body.casefold()
    if "?" in body or "？" in body:
        return True
    if _is_inquiry(body):
        return True
    if any(marker in body or marker in lower for marker in _ATTACHMENT_QA_MARKERS):
        return True
    if is_reply_only_turn(body):
        return True
    return False


def should_use_chat_first_attachment_turn(
    user_text: str,
    intent: str,
    *,
    turn_mode: str,
) -> bool:
    """Cursor-like: attachments already in context — answer without tool rounds."""
    if turn_mode != "agent":
        return False
    if intent not in {"qa", "recall"}:
        return False
    if not user_message_has_attachment_block(user_text):
        return False
    body = user_text_without_attachment_block(user_text)
    lower = body.casefold()
    if _has_explicit_execution_request(body, lower) or has_action_intent(body):
        return False
    return True


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

_ACTION_COMMAND_PREFIXES = (
    "开始做",
    "开始写",
    "开始实现",
    "动手",
    "直接做",
    "直接写",
    "先写",
    "创建项目",
    "创建工具",
    "创建代码",
    "修改文件",
    "修改代码",
    "生成项目",
    "生成代码",
    "运行测试",
    "运行命令",
    "执行测试",
    "执行命令",
    "落地实现",
    "实现一下",
    "做一下",
    "please implement",
    "help me create",
    "start ",
    "create ",
    "modify ",
    "generate ",
    "run ",
    "execute ",
    "implement ",
    "build ",
)

_ACTION_REQUEST_LEADS = (
    "请",
    "帮我",
    "需要你",
    "希望你",
    "让你",
    "直接",
    "先",
    "继续",
    "开始",
    "现在",
    "务必",
    "实际",
    "please",
    "help me",
    "i need you to",
)
_ACTION_REQUEST_VERBS = (
    "修复",
    "修改",
    "实现",
    "写入",
    "创建",
    "更新",
    "运行",
    "执行",
    "测试",
    "验证",
    "验收",
    "定位",
    "排查",
    "调试",
    "改",
    "写",
    "fix",
    "debug",
    "implement",
    "run",
    "test",
    "verify",
    "patch",
)
_NON_EXECUTION_CONNECTORS = (
    "告诉我",
    "解释",
    "说明",
    "如何",
    "怎么",
    "为什么",
    "是否",
    "能否",
    "可否",
    "能不能",
    "可不可以",
    "不要",
    "不需要",
    "无需",
    "不用",
    "tell me",
    "explain",
    "how ",
    "why ",
    "whether ",
    "don't",
    "do not",
    "without ",
)
_EXPLANATION_PREFIXES = (
    "请告诉我",
    "请解释",
    "请说明",
    "告诉我",
    "解释一下",
    "说明一下",
    "please tell me",
    "tell me",
    "please explain",
    "explain ",
    "how ",
    "what ",
    "why ",
)

_REQUIREMENT_HEADINGS = (
    "项目简介",
    "需求说明",
    "项目背景",
    "产品需求",
    "功能需求",
    "系统简介",
    "project brief",
    "requirements",
    "product requirements",
)


def auto_explore_enabled() -> bool:
    return os.environ.get("MY_AGENT_AUTO_EXPLORE", "1").strip() not in {"0", "false", "no"}


_REPLY_ONLY_MARKERS = (
    "只回复",
    "只用一句",
    "只说一句",
    "只回答",
    "只需回复",
    "只要回复",
    "不要调用任何工具",
    "不要调用工具",
    "不要使用任何工具",
    "不要使用工具",
    "reply with only",
    "only reply",
    "do not call any tool",
    "do not use any tool",
    "don't use any tool",
    "don't call any tool",
)


def is_reply_only_turn(user_text: str) -> bool:
    """True when the user asked for a spoken answer and not a work turn."""
    text = (user_text or "").strip()
    if not text:
        return False
    folded = text.casefold()
    return any(marker.casefold() in folded for marker in _REPLY_ONLY_MARKERS)


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
    body = user_text_without_attachment_block(text)
    body_lower = body.casefold()
    if any(kw in body_lower or kw in body for kw in _RESEARCH_ACTION_KEYWORDS):
        return True
    return any(marker in body_lower for marker in _PATH_MARKERS)


def _has_lookup_request(text: str, lower: str) -> bool:
    """User asked to look something up. Filenames like README do not count."""
    body = user_text_without_attachment_block(text)
    body_lower = body.casefold()
    if any(kw in body for kw in ("查", "读", "参照", "看看", "列出", "搜索", "调研", "探索")):
        return True
    if any(marker in body_lower for marker in _PATH_MARKERS):
        return True
    return bool(re.search(r"\b(explore|read|search|list|where)\b", body_lower))


# Status and inventory questions. Bare 「什么」 is intentionally absent so
# 「随便写点什么」 stays a request, while 「写了什么」 / 「哪些」 stay questions.
_INQUIRY_RE = re.compile(
    r"(?:[?？]|哪些|是什么|有什么|有哪些|写了什么|改了什么|做了什么|"
    r"到哪|哪里|哪儿|哪了|多少|是否|为什么|为何|怎么|如何|吗)"
)
# A polite or imperative frame, not a lone 写/改 inside a question.
_IMPERATIVE_FRAME_RE = re.compile(
    r"(?:"
    r"(?:帮我|需要你|希望你|让你|请).{0,16}?(?:写(?!了)|改(?!了)|修|实现|修复|修改|创建|更新|添加|运行|执行)"
    r"|写一个|写个|写一下"
    r"|改一下|改成"
    r"|把[^。？?\n]{0,32}改"
    r"|能不能.{0,12}?(?:写|改|实现|修|创建)"
    r"|可不可以.{0,12}?(?:写|改|实现|修)"
    r")"
)


def _is_inquiry(text: str) -> bool:
    """True for question-shaped lines (progress, inventory, why/how)."""
    return bool(_INQUIRY_RE.search(text or ""))


def _has_imperative_frame(text: str) -> bool:
    """True when the line asks for a change, not merely mentions 写/改."""
    return bool(_IMPERATIVE_FRAME_RE.search(text or ""))


def _has_explicit_execution_request(text: str, lower: str) -> bool:
    """Detect an action request without treating requirement prose as one.

    Long user messages can contain several sentences and project filenames.
    The request/verb pairing is a stronger signal than either message length
    or an artifact mention, so it must be checked before the read-only routes.
    A question that only contains 写/改 (「写了什么」「写到哪了」) is not a request.
    """
    if _is_inquiry(text) and not _has_imperative_frame(text):
        return False
    if any(lower.startswith(prefix) for prefix in _ACTION_COMMAND_PREFIXES):
        return True

    for lead in _ACTION_REQUEST_LEADS:
        for verb in _ACTION_REQUEST_VERBS:
            for match in re.finditer(
                rf"{re.escape(lead)}.{{0,32}}?{re.escape(verb)}",
                lower,
                flags=re.IGNORECASE,
            ):
                between = lower[match.start() + len(lead) : match.end() - len(verb)]
                if any(marker in between for marker in _NON_EXECUTION_CONNECTORS):
                    continue
                return True
    return False


# Bug reports and fix/feature asks. A long complaint such as
# 「delete 不存在的 id 直接崩了…帮我都看一下」 is a work request, not pasted
# requirement prose, so it must keep its tools.
_BUG_REPORT_RE = re.compile(
    r"(?:崩|报错|出错|报了?\s*[A-Za-z]*(?:Error|Exception)|bug|traceback|crash|"
    r"不对|失败|跑不起来|跑不通|不工作|不生效|没反应)",
    re.IGNORECASE,
)
_ACTION_ASK_RE = re.compile(
    r"(?:帮我|帮忙|麻烦你?|请你?|你直接|你就|你来|直接)"
    r"([^。？?！!\n]{0,12}?)"
    r"(?:修|改|处理|弄|解决|加|看一下|看下|看看|查一下|查查)"
)
_FEATURE_ASK_RE = re.compile(
    r"(?:^|[\s，,。；;：:])(?:再|也|还|顺便|另外)?(?:给[^。？?\n]{0,16}?)?"
    r"(?:加一个|加个|加上|再加|增加|新增)"
)
_NO_CHANGE_RE = re.compile(r"(?:不要|别|不用|无需|不需要|先别)\s*(?:动|改|修改|写)")


_CHANGE_VERB_TAIL_RE = re.compile(r"(?:修|改|处理|弄|解决|加)$")


def has_action_intent(user_text: str) -> bool:
    """True for a bug report or fix/feature ask that needs tools.

    「帮我都看一下」 after a crash report, 「你直接改吧」, 「再加一个 --overdue」.
    「帮我看看 X」 alone stays a lookup. Lines that say not to change anything,
    and how/why explanations, stay out.
    """
    text = (user_text or "").strip()
    if not text:
        return False
    if _NO_CHANGE_RE.search(text) or _is_explanation_request(text.casefold()):
        return False
    bug_report = bool(_BUG_REPORT_RE.search(text))
    for match in _ACTION_ASK_RE.finditer(text):
        between = match.group(1).casefold()
        if any(marker in between for marker in _NON_EXECUTION_CONNECTORS):
            continue
        if bug_report or _CHANGE_VERB_TAIL_RE.search(match.group(0)):
            return True
    return bool(_FEATURE_ASK_RE.search(text)) and not _is_inquiry(text)


# Dots inside paths, filenames, versions and numbers are not sentence ends.
_INNER_DOT_RE = re.compile(r"(?<=[\w/\-])\.(?=[\w/])")


def _sentence_count(text: str) -> int:
    cleaned = _INNER_DOT_RE.sub("", text)
    return sum(cleaned.count(mark) for mark in ("。", "！", "？", ".", "!", "?"))


def _is_explanation_request(lower: str) -> bool:
    """Keep how/why questions out of the execution keyword fallback."""
    stripped = lower.strip()
    if any(stripped.startswith(prefix) for prefix in _EXPLANATION_PREFIXES):
        return True
    return bool(
        re.match(
            r"^(?:(?:请|帮我|需要你|希望你|please|help me)\s*)*"
            r"(?:(?:先|直接)\s*)?(?:告诉我|解释|说明|tell me|explain|describe)",
            stripped,
            flags=re.IGNORECASE,
        )
    )


_ORDINARY_PLAN_ONLY_MARKERS = (
    "只给方案",
    "只给计划",
    "只做规划",
    "只规划",
    "仅规划",
    "仅给方案",
    "先不要改代码",
    "先别改代码",
    "不要改代码",
    "不改代码",
    "不要写代码",
    "不写代码",
    "只回答方案",
    "only give me a plan",
    "plan only",
    "do not change code",
    "don't change code",
    "do not write code",
    "don't write code",
)
_ORDINARY_READ_ONLY_MARKERS = (
    "只查看",
    "只读",
    "只回答",
    "只说明",
    "只解释",
    "只看状态",
    "查看状态",
    "检查状态",
    "不要改任何文件",
    "不改任何文件",
    "不要调用工具",
    "不要使用工具",
    "read only",
    "readonly",
    "read-only",
)
_ORDINARY_EXECUTE_VERB_RE = re.compile(
    r"(?:实现|修改|改成|改一下|修复|创建|写入|写一个|更新|运行|执行|测试|验证|"
    r"implement|modify|fix|create|write|update|run|execute|test|verify|patch)"
    r"\s*[^。！？?!\n]{1,80}(?:$|[。！？?!])",
    flags=re.IGNORECASE,
)


def _has_explicit_ordinary_plan_only(text: str) -> bool:
    folded = text.casefold()
    return any(marker.casefold() in folded for marker in _ORDINARY_PLAN_ONLY_MARKERS)


def _has_explicit_ordinary_read_only(text: str) -> bool:
    folded = text.casefold()
    return any(marker.casefold() in folded for marker in _ORDINARY_READ_ONLY_MARKERS)


def _has_explicit_ordinary_execute(text: str, lower: str) -> bool:
    if _has_explicit_execution_request(text, lower) or has_action_intent(text):
        return True
    if _is_inquiry(text) and not _has_imperative_frame(text):
        return False
    return bool(_ORDINARY_EXECUTE_VERB_RE.search(text))


def resolve_ordinary_work_intent(
    user_text: str,
    classified_intent: str | None,
    explicit_override: str | None = None,
) -> OrdinaryWorkIntent:
    """Resolve a non-persistent ordinary work intent for one user request.

    Explicit read-only/plan-only wording wins over stale UI state. A clear
    implementation request wins over plan/document language. The override is
    intentionally considered only after those text-level safety signals.
    """
    text = (user_text or "").strip()
    lower = text.casefold()
    classified = (classified_intent or "").strip().casefold()
    override = (explicit_override or "").strip().casefold()

    if _has_explicit_ordinary_read_only(text):
        return "read_only"
    if _has_explicit_ordinary_plan_only(text):
        return "plan_only"
    if _has_explicit_ordinary_execute(text, lower) or classified == "execute":
        return "execute"
    if override in {"read_only", "plan_only", "execute"}:
        return override  # type: ignore[return-value]
    if classified == "plan":
        return "plan_only"
    if classified in {"qa", "recall", "research", "requirements", ""}:
        return "read_only"
    return "read_only"


def intent_label(intent: TurnIntent, *, spawn_explore: bool = False) -> str:
    """User-visible one-line label for turn.start (T-905)."""
    if intent == "research" and spawn_explore:
        return "先只读探索"
    labels: dict[TurnIntent, str] = {
        "recall": "根据上文直接回顾，不调工具",
        "requirements": "需求输入，只读归纳",
        "qa": "直接回答",
        "plan": "整理方案，少动手",
        "research": "先查阅再回答",
        "execute": "可动手执行",
    }
    return labels.get(intent, intent)


def is_requirement_input(user_text: str) -> bool:
    """Recognize pasted project prose without treating it as an execution request."""
    text = user_text.strip()
    if is_attachment_qa_turn(text):
        return False
    body = user_text_without_attachment_block(text) if user_message_has_attachment_block(text) else text
    if len(body) < 120:
        return False
    lower = body.casefold()
    if _has_explicit_execution_request(body, lower) or _is_explanation_request(lower):
        return False
    if has_action_intent(body):
        return False
    heading = any(marker in body or marker in lower for marker in _REQUIREMENT_HEADINGS)
    numbered = body.startswith(tuple(f"{index}." for index in range(1, 10)))
    sentence_count = _sentence_count(body)
    return heading or (numbered and sentence_count >= 2) or sentence_count >= 4


def _mentions_project_artifacts(text: str, lower: str) -> bool:
    return any(marker in lower for marker in _PROJECT_ARTIFACT_MARKERS)


def classify_turn(user_text: str) -> TurnIntent:
    """Classify user line: recall | qa | plan | execute | research."""
    text = user_text.strip()
    lower = text.casefold()

    if is_recall_turn(text):
        return "recall"

    if is_reply_only_turn(text) and not _has_explicit_execution_request(text, lower):
        return "qa"

    if _has_explicit_execution_request(text, lower):
        return "execute"

    if has_action_intent(text):
        return "execute"

    if _is_explanation_request(lower):
        return "qa"

    if is_requirement_input(text):
        return "requirements"

    # Questions that mention 写/改/计划 are still questions. A real request
    # (帮我改、写一个、把 X 改成 Y、能不能帮我写) already returned execute.
    if (
        _is_inquiry(text)
        and not _has_imperative_frame(text)
        and not _has_lookup_request(text, lower)
    ):
        return "qa"

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


def project_explore_autospawn_disabled(
    *,
    project_id: str = "",
    active_shell: str = "",
) -> bool:
    """Phase 48 / BUG-027: bound project shell must not kernel-auto explore."""
    pid = (project_id or "").strip()
    shell = (active_shell or "").strip()
    return bool(pid) and shell == "project"


def should_spawn_explore(user_text: str, *, explicit: bool = False) -> bool:
    """Whether kernel runs explore subagent before parent loop (ORCHESTRATION §4.2, §8)."""
    if explicit:
        return True
    if not auto_explore_enabled():
        return False
    intent = classify_turn(user_text)
    if intent not in {"execute", "research"}:
        return False
    body = user_text_without_attachment_block(user_text)
    lower = body.casefold()
    if any(marker in body for marker in _SPAWN_MARKERS):
        return True
    return any(marker in lower for marker in _PATH_MARKERS)


def should_spawn_explore_for_turn(
    user_text: str,
    *,
    project_id: str = "",
    active_shell: str = "",
    explicit: bool = False,
) -> bool:
    """Auto explore gate including project-mode disable (AGENT-PARENT-ORCHESTRATION P1)."""
    if project_explore_autospawn_disabled(project_id=project_id, active_shell=active_shell):
        return False
    return should_spawn_explore(user_text, explicit=explicit)


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

    assert project_explore_autospawn_disabled(project_id="huiyi", active_shell="project")
    assert not project_explore_autospawn_disabled(project_id="huiyi", active_shell="grow")
    assert not project_explore_autospawn_disabled(project_id="", active_shell="project")
    assert not should_spawn_explore_for_turn(
        "文档和代码脱节了，你看看",
        project_id="huiyi",
        active_shell="project",
    )
    assert should_spawn_explore_for_turn(
        "按 run_demo 模式造 bar 工具",
        project_id="",
        active_shell="grow",
    )
    print("[PASS] IT-4801: project shell disables auto explore")


if __name__ == "__main__":
    _demo()
