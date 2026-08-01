"""Context budget + digest compression (RUNTIME.md §8, TASKS T-208)."""

from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import has_anchor_message
from llm_client import LLMClient, LLMResponse, load_config, resolve_context_limit, resolve_session_model
from session import Session
from tools.schema import ToolErrorCode, tool_fail, to_json

_INTERRUPTED_TOOL_MESSAGE = to_json(
    tool_fail(
        "unknown",
        ToolErrorCode.VALIDATION_ERROR,
        "tool call did not complete (session recovered)",
    )
)

DEFAULT_COMPACT_RATIO = 0.85
DEFAULT_KEEP_TURNS = 8
DEFAULT_DIGEST_MAX_CHARS = 8000
FIRST_COMPACT_USER_MESSAGE = (
    "较早对话已写入 digest.md；最近 {keep_turns} 轮仍完整保留。可说「压缩」手动触发。"
)
DIGEST_SECTION_HEADER = re.compile(r"^#\s*压缩\s+(\d+)\s*$", re.MULTILINE)
DIGEST_TEMPLATE_SECTIONS = (
    "## 目标",
    "## 已做",
    "## 未决",
    "## 活跃项目",
    "## 关键路径与命令",
    "## 用户约束",
)


class SummarizeClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse: ...


@dataclass(frozen=True, slots=True)
class ContextConfig:
    compact_ratio: float
    keep_turns: int
    digest_max_chars: int


@dataclass(frozen=True, slots=True)
class CompactResult:
    compacted: bool
    message: str
    digest_section: int | None = None
    digested_message_count: int = 0


def load_context_config() -> ContextConfig:
    """Load §8.1 constants (env overrides with documented defaults)."""
    ratio_raw = os.environ.get("CONTEXT_COMPACT_RATIO", str(DEFAULT_COMPACT_RATIO))
    keep_raw = os.environ.get("CONTEXT_KEEP_TURNS", str(DEFAULT_KEEP_TURNS))
    digest_raw = os.environ.get("CONTEXT_DIGEST_MAX_CHARS", str(DEFAULT_DIGEST_MAX_CHARS))
    return ContextConfig(
        compact_ratio=float(ratio_raw),
        keep_turns=int(keep_raw),
        digest_max_chars=int(digest_raw),
    )


def estimate_text_tokens(text: str) -> int:
    """MVP token heuristic (RUNTIME.md §8.1): len(text) // 4."""
    return len(text) // 4


def estimate_message_tokens(message: dict[str, Any]) -> int:
    total = 0
    content = message.get("content")
    if isinstance(content, str):
        total += estimate_text_tokens(content)
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if isinstance(call, dict):
                fn = call.get("function")
                if isinstance(fn, dict):
                    for key in ("name", "arguments"):
                        value = fn.get(key)
                        if isinstance(value, str):
                            total += estimate_text_tokens(value)
    return total


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(message) for message in messages)


def estimate_context_tokens(system_prompt: str, messages: list[dict[str, Any]]) -> int:
    return estimate_text_tokens(system_prompt) + estimate_messages_tokens(messages)


def effective_compact_start(session: Session) -> int:
    """First message index eligible for digest (anchor at 0 is never digested)."""
    start = session.meta.compact_before_index
    if has_anchor_message(session) and start < 1:
        return 1
    return start


def iter_turn_ranges(messages: list[dict[str, Any]], start: int) -> list[tuple[int, int]]:
    """Half-open [start, end) ranges; one turn ≈ user + following assistant/tool chain."""
    if start >= len(messages):
        return []

    ranges: list[tuple[int, int]] = []
    index = start
    while index < len(messages):
        if messages[index].get("role") != "user":
            index += 1
            continue
        turn_start = index
        index += 1
        while index < len(messages) and messages[index].get("role") != "user":
            index += 1
        ranges.append((turn_start, index))
    return ranges


def compute_compact_split_index(
    messages: list[dict[str, Any]],
    compact_before: int,
    *,
    keep_turns: int,
) -> int | None:
    """Index where digest ends and kept turns begin; None if nothing to digest."""
    turn_ranges = iter_turn_ranges(messages, compact_before)
    if len(turn_ranges) <= keep_turns:
        return None
    split_index = turn_ranges[-keep_turns][0]
    if split_index <= compact_before:
        return None
    return split_index


def repair_orphaned_tool_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure every assistant ``tool_calls`` block has matching tool role replies.

    OpenAI-compatible APIs reject histories where tool_call_ids lack tool messages
    (e.g. sidecar crash or confirm interrupt after the assistant line was persisted).
    """
    if not messages:
        return []

    repaired: list[dict[str, Any]] = []
    index = 0
    total = len(messages)

    while index < total:
        message = messages[index]
        tool_calls = message.get("tool_calls")
        if message.get("role") == "assistant" and isinstance(tool_calls, list) and tool_calls:
            repaired.append(message)
            index += 1
            existing: dict[str, dict[str, Any]] = {}
            while index < total and messages[index].get("role") == "tool":
                tool_message = messages[index]
                call_id = tool_message.get("tool_call_id")
                if isinstance(call_id, str) and call_id:
                    existing[call_id] = tool_message
                repaired.append(tool_message)
                index += 1
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                call_id = tool_call.get("id")
                if not isinstance(call_id, str) or not call_id:
                    continue
                if call_id not in existing:
                    repaired.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": _INTERRUPTED_TOOL_MESSAGE,
                        }
                    )
            continue

        repaired.append(message)
        index += 1

    return repaired


def build_llm_messages(session: Session) -> list[dict[str, Any]]:
    """Messages for LLM payload: anchor + post-digest history (disk keeps full log)."""
    messages = session.messages
    if not messages:
        return []

    start = effective_compact_start(session)
    if has_anchor_message(session):
        anchor = messages[0]
        tail = messages[start:]
        if start <= 1 and not tail:
            return repair_orphaned_tool_calls([anchor])
        if start <= 1:
            return repair_orphaned_tool_calls([anchor, *tail])
        return repair_orphaned_tool_calls([anchor, *messages[start:]])
    return repair_orphaned_tool_calls(messages[start:])


def should_auto_compact(
    system_prompt: str,
    session: Session,
    *,
    model: str | None = None,
    config: ContextConfig | None = None,
) -> bool:
    """True when estimated tokens reach LLM_CONTEXT_LIMIT × CONTEXT_COMPACT_RATIO."""
    cfg = config or load_context_config()
    resolved_model = model or session.meta.llm_model or resolve_session_model(session.meta.topics)
    limit = resolve_context_limit(resolved_model)
    threshold = int(limit * cfg.compact_ratio)
    llm_messages = build_llm_messages(session)
    tokens = estimate_context_tokens(system_prompt, llm_messages)
    return tokens >= threshold


def estimate_session_context_tokens(session: Session, *, system_prompt: str | None = None) -> int:
    """Estimate tokens for the payload that would be sent on the next main-agent call."""
    system = system_prompt
    if system is None:
        try:
            from loader import build_system_prompt

            system = build_system_prompt(session).prompt
        except Exception:
            system = ""
    return estimate_context_tokens(system, build_llm_messages(session))


def validate_llm_model_switch(session: Session, raw_model: str) -> str:
    """Normalize target model and refuse unsafe Pro→Flash downgrades.

    Policy (B): if estimated context ≥ target_limit × CONTEXT_COMPACT_RATIO (default 85%),
    refuse — user must 「压缩」 or 「新会话」 first. No silent auto-compact on switch.
    """
    from llm_client import normalize_session_model
    from session import SessionError

    canonical = normalize_session_model(raw_model)
    if canonical is None:
        raise SessionError(
            f"unsupported llm model: {raw_model!r} (use deepseek-v4-flash or deepseek-v4-pro)"
        )

    target_limit = resolve_context_limit(canonical)
    current_model = session.meta.llm_model or resolve_session_model(list(session.meta.topics))
    current_limit = resolve_context_limit(current_model)

    # Same ceiling or upgrade (flash→pro): always OK.
    if target_limit >= current_limit:
        return canonical

    cfg = load_context_config()
    threshold = int(target_limit * cfg.compact_ratio)
    tokens = estimate_session_context_tokens(session)
    if tokens >= threshold:
        raise SessionError(
            f"当前上下文约 {tokens} tokens，已达 Flash 切换阈值"
            f"（{threshold}/{target_limit}，{int(cfg.compact_ratio * 100)}%）。"
            "请先「压缩」或「新会话」后再切到 Flash。"
        )
    return canonical


def count_digest_sections(digest_path: Path) -> int:
    if not digest_path.is_file():
        return 0
    text = digest_path.read_text(encoding="utf-8")
    numbers = [int(match) for match in DIGEST_SECTION_HEADER.findall(text)]
    return max(numbers) if numbers else 0


def format_messages_for_digest(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        role = message.get("role", "?")
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            lines.append(f"[{role}]\n{content.strip()}")
            continue
        if role == "assistant" and message.get("tool_calls"):
            lines.append(f"[{role}] (tool_calls)")
            for call in message.get("tool_calls", []):
                if not isinstance(call, dict):
                    continue
                fn = call.get("function")
                if isinstance(fn, dict):
                    name = fn.get("name", "?")
                    lines.append(f"  - {name}")
        elif role == "tool":
            preview = content if isinstance(content, str) else str(content)
            if len(preview) > 500:
                preview = preview[:499] + "…"
            lines.append(f"[{role}]\n{preview}")
    return "\n\n".join(lines)


def build_digest_summarize_prompt(*, goal: str, transcript: str, project_hint: str = "") -> str:
    sections = "\n".join(DIGEST_TEMPLATE_SECTIONS)
    project_line = f"\n活跃项目提示：{project_hint}\n" if project_hint.strip() else ""
    return (
        "将以下对话片段压缩为结构化摘要，用于后续续聊时回忆上下文。\n"
        "要求：\n"
        f"- 严格使用以下 Markdown 二级标题（缺一不可）：\n{sections}\n"
        "- 「活跃项目」须写明 project_root 与未决 task（以 TASKS.md 为准，勿猜）\n"
        "- 只输出摘要正文，不要前言或结语\n"
        "- 保留具体路径、命令、文件名与用户明确约束\n"
        "- 若有关键事实需长期保留，在「用户约束」末尾注明：可说「记住」写入 evolve\n"
        f"\n本次会议目标：{goal.strip() or '(unset)'}{project_line}\n\n"
        f"对话片段：\n{transcript}"
    )


def _project_digest_hint(session: Session) -> str:
    if session.meta.active_shell != "project" or not session.meta.project_root:
        return ""
    return (
        f"根 {session.meta.project_root}；计划 {session.meta.project_plan_status or 'draft'}；"
        "未决见 TASKS.md（须 read_file）"
    )


def summarize_messages_for_digest(
    session: Session,
    messages: list[dict[str, Any]],
    llm: SummarizeClient,
    *,
    config: ContextConfig | None = None,
) -> str:
    cfg = config or load_context_config()
    transcript = format_messages_for_digest(messages)
    if not transcript.strip():
        return _empty_digest_body(session.goal)

    prompt = build_digest_summarize_prompt(
        goal=session.goal,
        transcript=transcript,
        project_hint=_project_digest_hint(session),
    )
    model = session.meta.llm_model or resolve_session_model(session.meta.topics)
    response = llm.chat(
        [{"role": "user", "content": prompt}],
        model=model,
        tools=None,
        temperature=0.0,
    )
    body = (response.content or "").strip()
    if not body:
        body = _empty_digest_body(session.goal)
    return _truncate_digest(body, cfg.digest_max_chars)


def append_digest_section(session: Session, body: str) -> int:
    section_number = count_digest_sections(session.digest_path) + 1
    block = f"# 压缩 {section_number}\n\n{body.strip()}\n"
    if session.digest_path.is_file():
        existing = session.digest_path.read_text(encoding="utf-8").rstrip()
        session.digest_path.write_text(f"{existing}\n\n{block}", encoding="utf-8")
    else:
        session.session_dir.mkdir(parents=True, exist_ok=True)
        session.digest_path.write_text(block, encoding="utf-8")
    return section_number


def compact_context(
    session: Session,
    llm: SummarizeClient,
    *,
    force: bool = False,
    system_prompt: str | None = None,
    config: ContextConfig | None = None,
) -> CompactResult:
    """Compress earlier turns into digest.md; messages.jsonl on disk stays完整."""
    cfg = config or load_context_config()
    compact_before = effective_compact_start(session)
    split_index = compute_compact_split_index(
        session.messages,
        compact_before,
        keep_turns=cfg.keep_turns,
    )
    if split_index is None:
        return CompactResult(
            compacted=False,
            message="无需压缩：可摘要轮次不足（需超过保留的最近轮数）。",
        )

    if not force:
        from loader import build_system_prompt

        system = system_prompt if system_prompt is not None else build_system_prompt(session).prompt
        if not should_auto_compact(system, session, config=cfg):
            return CompactResult(compacted=False, message="未达自动压缩阈值。")

    to_digest = session.messages[compact_before:split_index]
    if not to_digest:
        return CompactResult(
            compacted=False,
            message="无需压缩：没有可摘要的历史消息。",
        )

    digest_body = summarize_messages_for_digest(session, to_digest, llm, config=cfg)
    section_number = append_digest_section(session, digest_body)
    session.meta.compact_before_index = split_index
    session.save()

    return CompactResult(
        compacted=True,
        message=(
            f"已压缩：摘要 {len(to_digest)} 条消息 → digest.md §压缩 {section_number}；"
            f"保留最近 {cfg.keep_turns} 轮完整对话。"
        ),
        digest_section=section_number,
        digested_message_count=len(to_digest),
    )


def session_memory_event(session: Session) -> dict[str, Any]:
    """WebSocket / CLI payload for thread memory visibility (TURN-FEEDBACK §5)."""
    compacted = session.digest_path.is_file() or session.meta.compact_before_index > 1
    sections = count_digest_sections(session.digest_path) if session.digest_path.is_file() else 0
    cfg = load_context_config()

    # UX-019: estimate token usage for frontend indicator
    model = session.meta.llm_model or resolve_session_model(session.meta.topics)
    token_limit = resolve_context_limit(model)
    llm_messages = build_llm_messages(session)
    token_usage = estimate_messages_tokens(llm_messages)

    payload: dict[str, Any] = {
        "type": "session.memory",
        "message_count": len(session.messages),
        "memory_mode": "compact" if compacted else "full",
        "memory_mode_label": "已压缩" if compacted else "未压缩",
        "token_usage": token_usage,
        "token_limit": token_limit,
    }
    if compacted:
        payload["digest_sections"] = sections
        payload["keep_turns"] = cfg.keep_turns
    return payload


def maybe_auto_compact(
    session: Session,
    system_prompt: str,
    llm: SummarizeClient,
    *,
    config: ContextConfig | None = None,
) -> CompactResult | None:
    """Run compact when §8.1 auto threshold is met; returns result or None."""
    cfg = config or load_context_config()
    if not should_auto_compact(system_prompt, session, config=cfg):
        return None
    result = compact_context(
        session,
        llm,
        force=True,
        system_prompt=system_prompt,
        config=cfg,
    )
    return result if result.compacted else None


def _truncate_digest(text: str, max_chars: int) -> str:
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped
    return stripped[: max_chars - 1] + "…"


def _empty_digest_body(goal: str) -> str:
    goal_text = goal.strip() or "(unset)"
    return (
        "## 目标\n"
        f"{goal_text}\n\n"
        "## 已做\n"
        "(无)\n\n"
        "## 未决\n"
        "(无)\n\n"
        "## 关键路径与命令\n"
        "(无)\n\n"
        "## 用户约束\n"
        "(无)"
    )


def _demo() -> None:
    from agent import _MockLLM
    from loader import build_system_prompt
    from paths import AgentPaths
    from session import SessionMeta, create_new, utc_now_iso

    cfg = load_context_config()
    assert cfg.compact_ratio == DEFAULT_COMPACT_RATIO
    assert cfg.keep_turns == DEFAULT_KEEP_TURNS
    assert cfg.digest_max_chars == DEFAULT_DIGEST_MAX_CHARS
    print("[PASS] default context config")

    broken = [
        {
            "role": "assistant",
            "content": "calling tool",
            "tool_calls": [{"id": "call_x", "type": "function", "function": {"name": "grep", "arguments": "{}"}}],
        },
        {"role": "user", "content": "next question"},
    ]
    fixed = repair_orphaned_tool_calls(broken)
    assert len(fixed) == 3
    assert fixed[1]["role"] == "tool" and fixed[1]["tool_call_id"] == "call_x"
    assert fixed[2]["role"] == "user"
    print("[PASS] repair_orphaned_tool_calls inserts missing tool replies")

    assert estimate_text_tokens("abcd") == 1
    assert estimate_text_tokens("") == 0
    print("[PASS] estimate_text_tokens len//4")

    paths = AgentPaths.discover()
    for sid in ("_context_demo", "_context_threshold", "_context_sparse"):
        demo_dir = paths.data / "sessions" / sid
        if demo_dir.is_dir():
            shutil.rmtree(demo_dir)

    session = create_new(paths, conversation_id="_context_demo")
    session.set_goal("Context compression demo")
    session.set_topics([], phase="S4")
    session.messages = [
        {"role": "user", "content": "[本次会议上下文]\nanchor block"},
    ]
    session.meta.compact_before_index = 1

    for turn in range(12):
        session.messages.append({"role": "user", "content": f"user turn {turn}"})
        session.messages.append({"role": "assistant", "content": f"assistant reply {turn}"})

    assert len(iter_turn_ranges(session.messages, 1)) == 12
    split = compute_compact_split_index(session.messages, 1, keep_turns=cfg.keep_turns)
    assert split is not None
    kept_turns = iter_turn_ranges(session.messages, split)
    assert len(kept_turns) == cfg.keep_turns
    print("[PASS] turn split keeps last K turns")

    full_count_before = len(session.messages)
    mock = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content=(
                    "## 目标\nDemo\n\n## 已做\nEarlier turns\n\n## 未决\n(none)\n\n"
                    "## 关键路径与命令\n(none)\n\n## 用户约束\n(none)"
                ),
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            )
        ]
    )
    result = compact_context(session, mock, force=True)
    assert result.compacted
    assert result.digest_section == 1
    assert len(session.messages) == full_count_before
    assert session.meta.compact_before_index == split
    assert session.digest_path.is_file()
    digest_text = session.digest_path.read_text(encoding="utf-8")
    assert "# 压缩 1" in digest_text
    assert "## 目标" in digest_text
    print("[PASS] compact_context: digest append, messages.jsonl count unchanged")

    llm_messages = build_llm_messages(session)
    assert llm_messages[0]["content"].startswith("[本次会议上下文]")
    assert len(iter_turn_ranges(llm_messages, 1)) == cfg.keep_turns
    print("[PASS] build_llm_messages excludes digested prefix")

    system = build_system_prompt(session).prompt
    assert "Earlier turns" in system or "Demo" in system
    print("[PASS] loader injects digest after compact")

    reloaded = session.messages_path.read_text(encoding="utf-8").count("\n")
    session.save()
    assert reloaded <= session.messages_path.read_text(encoding="utf-8").count("\n")
    print("[PASS] messages.jsonl retains full history on disk")

    tiny_limit_session = create_new(paths, conversation_id="_context_threshold")
    tiny_limit_session.set_goal("threshold test")
    tiny_limit_session.set_topics([], phase="S4")
    tiny_limit_session.messages = [
        {"role": "user", "content": "[本次会议上下文]\nanchor"},
        {"role": "user", "content": "x" * 400},
        {"role": "assistant", "content": "y" * 400},
    ]
    tiny_limit_session.meta.compact_before_index = 1
    tiny_limit_session.meta.llm_model = resolve_session_model([])
    system_tiny = build_system_prompt(tiny_limit_session).prompt
    prev_limit = os.environ.get("LLM_CONTEXT_LIMIT")
    os.environ["LLM_CONTEXT_LIMIT"] = "200"
    try:
        assert should_auto_compact(system_tiny, tiny_limit_session)
        print("[PASS] should_auto_compact at 85% threshold")
    finally:
        if prev_limit is None:
            os.environ.pop("LLM_CONTEXT_LIMIT", None)
        else:
            os.environ["LLM_CONTEXT_LIMIT"] = prev_limit

    second_mock = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content="## 目标\nT\n\n## 已做\nA\n\n## 未决\nN\n\n## 关键路径与命令\nC\n\n## 用户约束\nU",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            )
        ]
    )
    assert count_digest_sections(session.digest_path) == 1
    section_two = append_digest_section(session, "## 目标\nsecond section")
    assert section_two == 2
    assert count_digest_sections(session.digest_path) == 2
    section_three = append_digest_section(session, "## 目标\nthird section")
    assert section_three == 3
    assert "# 压缩 3" in session.digest_path.read_text(encoding="utf-8")
    print("[PASS] digest sections numbered sequentially")

    sparse = create_new(paths, conversation_id="_context_sparse")
    sparse.set_goal("sparse")
    sparse.messages = [{"role": "user", "content": "[本次会议上下文]\nonly anchor"}]
    sparse.meta.compact_before_index = 1
    sparse_result = compact_context(sparse, second_mock, force=True)
    assert not sparse_result.compacted
    print("[PASS] force compact with insufficient turns is no-op")

    if load_config().api_key:
        print("[SKIP] live digest LLM (set LLM_API_KEY; optional manual test)")
    else:
        print("[SKIP] live digest: LLM_API_KEY not set")


if __name__ == "__main__":
    _demo()
