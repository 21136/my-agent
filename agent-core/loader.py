"""Assemble system prompt: base layer + session overlay (RUNTIME.md §4, TASKS T-204)."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from session import Session, SessionMeta, utc_now_iso
from tools.logging import (
    EVENT_SESSION_START,
    EVENT_TOPICS_CONFIRMED,
    EvolveLog,
    read_events,
)
from tools.registry import BUILTIN_TOOLS, ToolRegistry

SECTION_SEPARATOR = "\n---\n"
CORE_REL = Path("prompts") / "core.txt"
TERMINAL_REL = Path("prompts") / "terminal.txt"
INDEX_LEGACY_REL = Path("_index.toml")
INDEX_CORE_REL = Path("_index.core.toml")
INDEX_USER_REL = Path("_index.user.toml")
INDEX_REL = INDEX_LEGACY_REL  # backward-compat alias for tests referencing INDEX_REL
SAFETY_REL = Path("prompts") / "safety.md"
MEMORIES_DIRNAME = "memories"
PROMPTS_DIRNAME = "prompts"

_ARCHIVED_STATUS = frozenset({"archived"})


class TopicIndexError(Exception):
    """Invalid or conflicting topic index (EXTENSIONS §3.3)."""


class TopicRegisterError(ValueError):
    """Invalid topic registration request (EXTENSIONS §4)."""


TOPIC_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_USER_INDEX_HEADER = (
    "# 用户扩展主题 — 由你策展；git diff 此处即你的扩展变更。\n"
    "# 新增主题：REPL「注册主题 <id>」或手改本文件。\n"
    "# 详见 docs/EXTENSIONS.md\n\n"
)


@dataclass(frozen=True, slots=True)
class RegisterTopicSpec:
    topic_id: str
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class RegisterTopicResult:
    topic_id: str
    index_path: str
    prompt_path: str
    memory_dir: str
    tool_dir: str


@dataclass(frozen=True, slots=True)
class TopicIndexEntry:
    id: str
    name: str
    description: str
    prompt: str
    memory_dirs: tuple[str, ...]
    tool_dirs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MemoryIndexEntry:
    memory_id: str
    topics: tuple[str, ...]
    summary: str
    path: str


_DYNAMIC_SYSTEM_SECTIONS = frozenset(
    {
        "session",
        "terminal_scope",
        "turn_discipline",
        "scaffold_tool",
        "evolve_escalation",
        "subagent_summary",
        "project_mode",
        "digest",
    }
)


def is_dynamic_system_section(name: str) -> bool:
    """True for per-turn / per-loop overlays that should not share a cache block."""
    return name in _DYNAMIC_SYSTEM_SECTIONS


def combine_system_prompt_parts(static: str, dynamic: str) -> str:
    """Join cache-split parts (static first) for providers without explicit markers."""
    static_text = static.strip()
    dynamic_text = dynamic.strip()
    if static_text and dynamic_text:
        return f"{static_text}{SECTION_SEPARATOR}{dynamic_text}"
    return static_text or dynamic_text


@dataclass(frozen=True, slots=True)
class LoadedSystem:
    """Assembled system prompt with traceable section order."""

    prompt: str
    section_names: tuple[str, ...]
    static_prompt: str
    dynamic_prompt: str
    static_section_names: tuple[str, ...]
    dynamic_section_names: tuple[str, ...]


def core_prompt_path(agent_core_dir: Path | None = None) -> Path:
    base = agent_core_dir or _AGENT_CORE
    return base / CORE_REL


def load_core_text(*, agent_core_dir: Path | None = None) -> str:
    path = core_prompt_path(agent_core_dir)
    if not path.is_file():
        return "[core.txt missing — implement T-209]"
    return path.read_text(encoding="utf-8").strip()


def terminal_prompt_path(agent_core_dir: Path | None = None) -> Path:
    base = agent_core_dir or _AGENT_CORE
    return base / TERMINAL_REL


TERMINAL_PLANNER_REL = Path("prompts") / "terminal-planner.txt"


def load_terminal_planner_text(*, agent_core_dir: Path | None = None) -> str:
    base = agent_core_dir or _AGENT_CORE
    path = base / TERMINAL_PLANNER_REL
    if not path.is_file():
        return "[terminal-planner.txt missing — implement T-5730]"
    return path.read_text(encoding="utf-8").strip()


def load_terminal_text(*, agent_core_dir: Path | None = None) -> str:
    path = terminal_prompt_path(agent_core_dir)
    if not path.is_file():
        return "[terminal.txt missing — implement T-5705]"
    return path.read_text(encoding="utf-8").strip()


def load_topic_index(evolve_dir: Path) -> list[TopicIndexEntry]:
    """Load merged topic index: ``_index.core.toml`` + ``_index.user.toml``.

    Falls back to legacy ``_index.toml`` when core is absent (EXTENSIONS §3.3).
    """
    core_path = evolve_dir / INDEX_CORE_REL
    if core_path.is_file():
        core_entries = _load_topic_index_file(core_path)
        user_path = evolve_dir / INDEX_USER_REL
        user_entries = _load_topic_index_file(user_path) if user_path.is_file() else []
        return _merge_topic_index(core_entries, user_entries)

    legacy_path = evolve_dir / INDEX_LEGACY_REL
    if legacy_path.is_file():
        return _load_topic_index_file(legacy_path)
    return []


def _load_topic_index_file(index_path: Path) -> list[TopicIndexEntry]:
    if not index_path.is_file():
        return []
    try:
        payload = tomllib.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []

    topics_raw = payload.get("topic")
    if not isinstance(topics_raw, list):
        return []

    entries: list[TopicIndexEntry] = []
    for item in topics_raw:
        if not isinstance(item, dict):
            continue
        topic_id = item.get("id")
        name = item.get("name")
        description = item.get("description")
        prompt = item.get("prompt")
        if not all(isinstance(value, str) and value.strip() for value in (topic_id, name, description, prompt)):
            continue
        memory_dirs = _as_str_tuple(item.get("memory_dirs"))
        tool_dirs = _as_str_tuple(item.get("tool_dirs"))
        entries.append(
            TopicIndexEntry(
                id=topic_id.strip(),
                name=name.strip(),
                description=description.strip(),
                prompt=prompt.strip(),
                memory_dirs=memory_dirs,
                tool_dirs=tool_dirs,
            )
        )
    return entries


def _merge_topic_index(
    core_entries: list[TopicIndexEntry],
    user_entries: list[TopicIndexEntry],
) -> list[TopicIndexEntry]:
    core_ids = {entry.id for entry in core_entries}
    conflicts = sorted({entry.id for entry in user_entries if entry.id in core_ids})
    if conflicts:
        raise TopicIndexError(
            "user topic id conflicts with core index: " + ", ".join(conflicts)
        )
    return [*core_entries, *user_entries]


def copy_evolve_index_files(src_evolve: Path, dst_evolve: Path) -> None:
    """Copy index files present under *src_evolve* into *dst_evolve* (governance demos)."""
    dst_evolve.mkdir(parents=True, exist_ok=True)
    for rel in (INDEX_CORE_REL, INDEX_USER_REL, INDEX_LEGACY_REL):
        src = src_evolve / rel
        if src.is_file():
            shutil.copy2(src, dst_evolve / rel)


def load_core_topic_ids(evolve_dir: Path) -> frozenset[str]:
    core_path = evolve_dir / INDEX_CORE_REL
    if not core_path.is_file():
        return frozenset()
    return frozenset(entry.id for entry in _load_topic_index_file(core_path))


def load_user_topic_ids(evolve_dir: Path) -> frozenset[str]:
    user_path = evolve_dir / INDEX_USER_REL
    if not user_path.is_file():
        return frozenset()
    return frozenset(entry.id for entry in _load_topic_index_file(user_path))


def validate_register_topic_id(topic_id: str, evolve_dir: Path) -> None:
    """Raise :class:`TopicRegisterError` when *topic_id* cannot be registered."""
    text = topic_id.strip()
    if not text:
        raise TopicRegisterError("topic id is required")
    if not TOPIC_ID_RE.fullmatch(text):
        raise TopicRegisterError(
            "topic id must match [a-z][a-z0-9_]* (lowercase letters, digits, underscore)"
        )
    if not (evolve_dir / INDEX_CORE_REL).is_file():
        raise TopicRegisterError(
            "注册主题 requires evolve/_index.core.toml (run T-801 migration first)"
        )
    if text in load_core_topic_ids(evolve_dir):
        raise TopicRegisterError(f"topic id reserved by core index: {text}")
    if text in load_user_topic_ids(evolve_dir):
        raise TopicRegisterError(f"topic already registered in user index: {text}")


def format_user_topic_toml_block(spec: RegisterTopicSpec) -> str:
    prompt_rel = f"prompts/{spec.topic_id}.md"
    memory_dir = f"memories/{spec.topic_id}"
    tool_dir = f"tools/{spec.topic_id}"
    lines = [
        "[[topic]]",
        f'id = {_toml_string(spec.topic_id)}',
        f"name = {_toml_string(spec.name)}",
        f"description = {_toml_string(spec.description)}",
        f"prompt = {_toml_string(prompt_rel)}",
        f'memory_dirs = [{_toml_string(memory_dir)}]',
        f'tool_dirs = [{_toml_string(tool_dir)}]',
    ]
    return "\n".join(lines)


def build_topic_prompt_scaffold(*, name: str, registered_at: str) -> str:
    return (
        f"# {name}\n\n"
        f"> 用户扩展主题 · 注册于 {registered_at}\n"
        "> 在此写下本主题的硬规则（路径、确认策略、常用模式）。\n\n"
        "## 范围\n\n"
        "（待填写）\n\n"
        "## 硬规则\n\n"
        "（待填写）\n"
    )


def format_register_topic_preview(spec: RegisterTopicSpec, *, evolve_dir: Path) -> str:
    index_rel = INDEX_USER_REL.as_posix()
    prompt_rel = f"prompts/{spec.topic_id}.md"
    memory_rel = f"memories/{spec.topic_id}/"
    tool_rel = f"tools/{spec.topic_id}/"
    block = format_user_topic_toml_block(spec)
    return (
        f"将追加到 evolve/{index_rel}:\n"
        f"{block}\n\n"
        f"将创建:\n"
        f"- evolve/{prompt_rel}\n"
        f"- evolve/{memory_rel}\n"
        f"- evolve/{tool_rel}"
    )


def register_user_topic(
    evolve_dir: Path,
    spec: RegisterTopicSpec,
    *,
    registered_at: str | None = None,
) -> RegisterTopicResult:
    """Append topic to user index and create scaffold paths (EXTENSIONS §4.2)."""
    validate_register_topic_id(spec.topic_id, evolve_dir)
    stamp = registered_at or utc_now_iso()

    prompt_rel = Path("prompts") / f"{spec.topic_id}.md"
    memory_dir = Path("memories") / spec.topic_id
    tool_dir = Path("tools") / spec.topic_id
    prompt_path = evolve_dir / prompt_rel
    memory_path = evolve_dir / memory_dir
    tool_path = evolve_dir / tool_dir
    user_index_path = evolve_dir / INDEX_USER_REL

    conflicts: list[str] = []
    if prompt_path.is_file():
        conflicts.append(prompt_rel.as_posix())
    if memory_path.exists():
        conflicts.append(memory_dir.as_posix() + "/")
    if tool_path.exists():
        conflicts.append(tool_dir.as_posix() + "/")
    if conflicts:
        raise TopicRegisterError("paths already exist: " + ", ".join(conflicts))

    block = format_user_topic_toml_block(spec)
    if user_index_path.is_file():
        existing = user_index_path.read_text(encoding="utf-8")
        if existing and not existing.endswith("\n"):
            existing += "\n"
        user_index_path.write_text(existing + "\n" + block + "\n", encoding="utf-8")
    else:
        user_index_path.write_text(_USER_INDEX_HEADER + block + "\n", encoding="utf-8")

    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(build_topic_prompt_scaffold(name=spec.name, registered_at=stamp), encoding="utf-8")
    memory_path.mkdir(parents=True, exist_ok=True)
    tool_path.mkdir(parents=True, exist_ok=True)

    return RegisterTopicResult(
        topic_id=spec.topic_id,
        index_path=user_index_path.relative_to(evolve_dir).as_posix(),
        prompt_path=prompt_rel.as_posix(),
        memory_dir=memory_dir.as_posix(),
        tool_dir=tool_dir.as_posix(),
    )


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def scan_memory_index(evolve_dir: Path) -> list[MemoryIndexEntry]:
    """Scan ``evolve/memories/**/*.md``; inject active/suspect id+summary (MEMORY §5)."""
    memories_root = evolve_dir / MEMORIES_DIRNAME
    if not memories_root.is_dir():
        return []

    entries: list[MemoryIndexEntry] = []
    for path in sorted(memories_root.rglob("*.md")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        frontmatter = _parse_frontmatter(text)
        if not frontmatter:
            continue
        status = str(frontmatter.get("status", "active")).strip().lower()
        if status in _ARCHIVED_STATUS:
            continue
        memory_id = frontmatter.get("id")
        summary = frontmatter.get("summary")
        if not isinstance(memory_id, str) or not memory_id.strip():
            continue
        if not isinstance(summary, str) or not summary.strip():
            continue
        topics = _frontmatter_topics(frontmatter.get("topics"))
        rel_path = path.relative_to(evolve_dir).as_posix()
        entries.append(
            MemoryIndexEntry(
                memory_id=memory_id.strip(),
                topics=topics,
                summary=summary.strip(),
                path=rel_path,
            )
        )
    entries.sort(key=lambda entry: (entry.memory_id, entry.path))
    return entries


def format_topic_index(entries: list[TopicIndexEntry]) -> str:
    """Render merged topic index for S0 injection (MEMORY §4.1, RUNTIME §4.1)."""
    lines = ["[主题索引]"]
    if not entries:
        lines.append("(none registered)")
        return "\n".join(lines)
    for entry in entries:
        lines.append(f"- {entry.id}: {entry.name} — {entry.description}")
        tool_dirs_label = ", ".join(entry.tool_dirs) if entry.tool_dirs else "(none)"
        lines.append(f"  tool_dirs: {tool_dirs_label}")
    return "\n".join(lines)


def format_memory_index(entries: list[MemoryIndexEntry]) -> str:
    """Render global memory index for S0 injection (MEMORY §5.2)."""
    lines = ["[久远记忆]"]
    if not entries:
        lines.append("(none active)")
        return "\n".join(lines)
    for entry in entries:
        topic_label = ", ".join(entry.topics) if entry.topics else "?"
        lines.append(f"- {entry.memory_id} ({topic_label}): {entry.summary}")
    return "\n".join(lines)


def format_builtin_summary() -> str:
    core_names = {
        "read_file",
        "list_dir",
        "grep",
        "glob_file_search",
        "codebase_search",
        "web_search",
        "fetch_url",
        "run_evolved",
    }
    lines = [
        "[Builtin 工具]",
        "恒为 12 个 function（核心 8 + 编排 4）；evolved 经 run_evolved，见工具索引：",
        "核心：",
    ]
    for tool in BUILTIN_TOOLS:
        if tool.name in core_names:
            lines.append(f"- {tool.name}: {tool.description}")
    lines.append("编排：")
    for tool in BUILTIN_TOOLS:
        if tool.name not in core_names:
            lines.append(f"- {tool.name}: {tool.description}")
    return "\n".join(lines)


def _format_host_scope_overlay(paths: AgentPaths) -> str:
    """List registered host directories so the LLM knows about host:<id>/… paths."""
    from host_scope import load_host_scope

    config = load_host_scope(paths)
    roots = getattr(config, "roots", None) or {}
    if not roots:
        return ""
    lines = ["[托管区 · host scope]", "以下外部目录已注册，可用 host:<id>/… 路径直接访问："]
    for host_id, root_info in roots.items():
        label = getattr(root_info, "label", host_id) or host_id
        rw = "读写" if getattr(root_info, "write", False) else "只读"
        path = getattr(root_info, "path", "") or ""
        lines.append(f"- host:{host_id} ({rw}) — {label} ({path})")
    return "\n".join(lines)


def format_session_overlay(session: Session) -> str:
    topics = ", ".join(session.meta.topics) if session.meta.topics else "(none)"
    goal = session.goal.strip() or "(unset)"
    subagent = "used" if session.subagent_overlay else "none"
    intent = session.turn_intent or "(pending)"
    return "\n".join(
        [
            "[本次会议]",
            f"harness: {session.meta.harness}",
            f"conversation_id: {session.conversation_id}",
            f"goal: {goal}",
            f"topics: {topics}",
            f"llm_model: {session.meta.llm_model}",
            f"turn_mode: {session.meta.turn_mode}",
            f"turn_intent: {intent}",
            f"subagent: {subagent}",
        ]
    )


def format_terminal_scope_overlay(session: Session) -> str:
    """Terminal harness scope block (TERMINAL-MODE §4.2 · T-5705)."""
    from session import is_terminal_harness
    from terminal_scope import TerminalScopeError, resolve_terminal_effective_root

    if not is_terminal_harness(session.meta):
        return ""
    kind = session.meta.terminal_scope_kind or "(unset)"
    lines = [
        "[Terminal scope]",
        "harness: terminal",
        f"terminal_scope_kind: {kind}",
        "turn_mode: agent (fixed · TM-23)",
    ]
    try:
        root = resolve_terminal_effective_root(session.meta, session.paths)
        lines.append(f"effective_root: {root.resolve().as_posix()}")
    except TerminalScopeError as exc:
        lines.append(f"effective_root: (unresolved — {exc})")
    if kind == "agent":
        lines.append(f"terminal_cwd: {session.meta.terminal_cwd or '.'}")
    elif kind == "host":
        lines.append(f"terminal_host_id: {session.meta.terminal_host_id or '(unset)'}")
        if session.meta.terminal_cwd:
            lines.append(f"terminal_cwd: {session.meta.terminal_cwd}")
    elif kind == "foreign":
        lines.append(
            f"terminal_foreign_root: {session.meta.terminal_foreign_root or '(unset)'}"
        )
    lines.extend(
        [
            "wild_mode: inside effective_root → write/run skip confirm; outside → confirm.",
            "no_plan_gate: no project_id; TASKS/MAP/docs may be write_text directly (TM-17).",
            "no_segment_cap: auto-continue segments within total cap (TM-16).",
        ]
    )
    return "\n".join(lines)


def format_turn_discipline_overlay(session: Session) -> str | None:
    """Per-turn overlay hints for T-701 (complements core.txt Turn discipline)."""
    if session.meta.phase != "S4":
        return None
    from session import is_terminal_harness

    if is_terminal_harness(session.meta):
        lines = [
            "[轮次纪律 · terminal]",
            "harness: terminal — fixed agent; no 只聊/动手; no project plan gate or task_paused.",
            "tool_budget: terminal — no segment cap pause; auto-continue within total cap (TM-16).",
        ]
    else:
        lines = [
            "[轮次纪律 · turn]",
            "qa/plan：先文字答；execute：有子代理摘要则直接 write_evolved，勿重复读范例。",
        ]
        if session.meta.turn_mode == "ask":
            short_max = os.environ.get("PARENT_SHORT_MAX", "5")
            lines.append(
                "turn_mode: ask — 只聊：read/list/grep/web/fetch + `探索` 可用；run_evolved 已禁用。说 `动手` 切换。"
            )
            lines.append(
                f"tool_budget: ask — 每轮 ≤{short_max} 轮，run_evolved 已禁用（T-907）"
            )
        else:
            from agent import parent_execute_segment_max

            segment_max = parent_execute_segment_max(
                active_shell=session.meta.active_shell,
            )
            lines.append("turn_mode: agent — 动手：含 run_evolved 写 workspace / evolve。")
            lines.append(
                f"tool_budget: agent — 每 segment ≤{segment_max} 轮"
                + (
                    "（项目模式）"
                    if (session.meta.active_shell or "").strip() == "project"
                    else "，可自动续跑（T-907）"
                )
            )
    if session.scaffold_tool_turn:
        lines.append(
            "scaffold_tool: yes — 本轮创建 evolved 工具：禁 write_text 写脚手架文件名；可暂存 _staging.toml；只用 write_evolve（顶层 path+content_base64）。"
        )
    elif session.subagent_overlay:
        if "[子代理摘要 · checker]" in session.subagent_overlay:
            lines.append(
                "subagent: checker — 验收报告已注入；勿自动修复文件；"
                "verdict≠pass 时勿宣称「已验收/沉淀完成」。"
            )
        elif "[子代理摘要 · deliverable_review]" in session.subagent_overlay:
            lines.append(
                "subagent: review — 交付审查已注入；按 verdict 行动，勿要求用户点验收按钮。"
            )
        elif "[子代理摘要 · plan]" in session.subagent_overlay:
            lines.append(
                "subagent: plan — 计划提案已注入；向用户说明要点，勿口述按钮名。"
            )
        else:
            overlay = (session.subagent_overlay or "").strip()
            if "已达 explore 上限" in overlay or "摘要已截断" in overlay:
                lines.append(
                    "subagent: explore — 已满 cap 或摘要截断；父可补读关键文件，"
                    "勿重复 overlay「已读」路径。"
                )
            else:
                lines.append(
                    "subagent: used — 已注入 explore 摘要；父循环勿对「已读」路径再 read/grep（除非摘要 truncated）。"
                )
    elif session.turn_intent == "recall":
        lines.append(
            "turn_intent: recall — 根据上文直接回顾；父循环不调工具（T-905）。"
        )
    elif session.turn_intent in {"execute", "research"}:
        lines.append(
            f"turn_intent: {session.turn_intent} — 深调研应由子代理完成；可说 `探索 …` 或等待自动 explore。"
        )
    else:
        lines.append("subagent: none — 深调研可建议用户 `探索 …`。")
    return "\n".join(lines)


def format_evolve_escalation_hint(session: Session) -> str | None:
    """EVOLVE §3.2: at most one proactive oral offer per session (T-403)."""
    if session.meta.evolve_offer_used or session.meta.phase != "S4":
        return None
    return "\n".join(
        [
            "[进化升格 · EVOLVE §3.2]",
            "本会话尚未主动提议固化。若发现值得长期保留的规则或事实，可口头问用户是否写入 evolve（每会话最多一次）；",
            "禁止直接声称已写入或生成 proposal 文件。用户确认后由其弱确认（好/要/写进去/对）或说「记住」触发检查点。",
        ]
    )


def load_safety_prompt(evolve_dir: Path) -> str:
    path = evolve_dir / SAFETY_REL
    if not path.is_file():
        return "[safety.md missing at evolve/prompts/safety.md]"
    return path.read_text(encoding="utf-8").strip()


SUBAGENT_PROMPTS_DIR = Path("subagents")
TOOL_WORKSHOP_REL = Path("prompts") / "tool_workshop.md"

_TOOL_WORKSHOP_FALLBACK = """# 工具工坊（Tool Workshop）

你在工具工坊会话：沉淀可复用、够广的 evolved 工具。
先查 evolve/tool-catalog/INDEX.md 能否用现有工具覆盖；能则不新建。
写文件只用 write_evolve（先 main.py 再 tool.toml，status=draft）；细则见 buckets/evolve.md。
验收：验收 <name>；PASS 后改 active + INDEX。"""


def load_evolve_prompt_file(evolve_dir: Path, relative: str, *, fallback: str) -> str:
    path = evolve_dir / relative
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            pass
    return fallback


def load_subagent_prompt(
    evolve_dir: Path,
    name: str,
    *,
    fallback: str,
    extra: str | None = None,
) -> str:
    base = load_evolve_prompt_file(
        evolve_dir, f"subagents/{name}.md", fallback=fallback
    )
    if extra:
        return f"{base}\n\n---\n\n{extra}"
    return base


def is_project_bound(session: Session) -> bool:
    """True when session has both project_id and project_root (TOOL-WORKSHOP §4.1)."""
    pid = (session.meta.project_id or "").strip()
    if not pid:
        return False
    root = (session.meta.project_root or "").strip()
    if not root:
        return False
    return True


def is_workshop_eligible(session: Session) -> bool:
    """Non-project-bound sessions receive tool_workshop overlay (W3)."""
    return not is_project_bound(session)


def load_tool_workshop_prompt(evolve_dir: Path) -> str:
    return load_evolve_prompt_file(
        evolve_dir, str(TOOL_WORKSHOP_REL).replace("\\", "/"), fallback=_TOOL_WORKSHOP_FALLBACK
    )


def load_topic_prompt(
    evolve_dir: Path,
    topic_id: str,
    *,
    index: list[TopicIndexEntry] | None = None,
) -> str | None:
    """Load one topic prompt file; path from ``_index.toml`` ``prompt`` field."""
    path = resolve_topic_prompt_path(evolve_dir, topic_id, index=index)
    if path is None or not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip()


def resolve_topic_prompt_path(
    evolve_dir: Path,
    topic_id: str,
    *,
    index: list[TopicIndexEntry] | None = None,
) -> Path | None:
    """Map topic id → ``evolve/`` relative prompt path (MEMORY §3.2)."""
    entries = index if index is not None else load_topic_index(evolve_dir)
    for entry in entries:
        if entry.id == topic_id:
            return evolve_dir / Path(entry.prompt)
    return evolve_dir / PROMPTS_DIRNAME / f"{topic_id}.md"


def load_confirmed_topic_prompts(
    evolve_dir: Path,
    topic_ids: list[str] | tuple[str, ...],
    *,
    index: list[TopicIndexEntry] | None = None,
) -> list[tuple[str, str]]:
    """S3 overlay: full prompt text per confirmed topic (MEMORY §4.3, RUNTIME §4.2.7)."""
    entries = index if index is not None else load_topic_index(evolve_dir)
    by_id = {entry.id: entry for entry in entries}
    sections: list[tuple[str, str]] = []
    for topic_id in topic_ids:
        if topic_id == "safety":
            continue
        entry = by_id.get(topic_id)
        if entry is None:
            continue
        path = evolve_dir / Path(entry.prompt)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            sections.append((topic_id, text))
    return sections


def session_start_log_fields(session: Session) -> dict[str, Any]:
    """Fields for ``session_start`` evolve_log event (MEMORY §8)."""
    evolve_dir = session.paths.evolve
    return {
        "conversation_id": session.conversation_id,
        "memory_ids_loaded": [entry.memory_id for entry in scan_memory_index(evolve_dir)],
        "topics_available": [entry.id for entry in load_topic_index(evolve_dir)],
    }


def topics_confirmed_log_fields(
    session: Session,
    *,
    registry: ToolRegistry | None = None,
) -> dict[str, Any]:
    """Fields for ``topics_confirmed`` evolve_log event (MEMORY §8)."""
    evolve_dir = session.paths.evolve
    topic_index = load_topic_index(evolve_dir)
    by_id = {entry.id: entry for entry in topic_index}
    prompt_files: list[str] = []
    for topic_id, _ in load_confirmed_topic_prompts(
        evolve_dir, session.meta.topics, index=topic_index
    ):
        entry = by_id.get(topic_id)
        if entry is not None:
            prompt_files.append(entry.prompt)
    reg = registry or ToolRegistry.load(session.paths)
    evolved_tools = sorted(session_evolved_allowlist(session, registry=reg))
    return {
        "conversation_id": session.conversation_id,
        "topics_confirmed": list(session.meta.topics),
        "prompt_files_loaded": prompt_files,
        "evolved_tools_listed": evolved_tools,
    }


def log_session_start(
    session: Session,
    *,
    evolve_log: EvolveLog | None = None,
) -> None:
    """Append S0 session startup line to ``data/evolve_log.jsonl`` (T-306)."""
    log = evolve_log or EvolveLog.for_agent(session.paths)
    fields = session_start_log_fields(session)
    log.log_session_start(
        conversation_id=fields["conversation_id"],
        memory_ids_loaded=fields["memory_ids_loaded"],
        topics_available=fields["topics_available"],
    )


def log_topics_confirmed(
    session: Session,
    *,
    registry: ToolRegistry | None = None,
    evolve_log: EvolveLog | None = None,
) -> None:
    """Append S3 topic confirmation line to ``data/evolve_log.jsonl`` (T-306)."""
    log = evolve_log or EvolveLog.for_agent(session.paths)
    fields = topics_confirmed_log_fields(session, registry=registry)
    log.log_topics_confirmed(
        conversation_id=fields["conversation_id"],
        topics_confirmed=fields["topics_confirmed"],
        prompt_files_loaded=fields["prompt_files_loaded"],
        evolved_tools_listed=fields["evolved_tools_listed"],
    )


def format_session_evolved_catalog(
    topics: list[str],
    *,
    registry: ToolRegistry | None = None,
    extra_tools: tuple | None = None,
) -> str:
    """Human-readable evolved catalog for system overlay (TOOLS.md §4.3, T-308)."""
    reg = registry or ToolRegistry.load()
    by_name = {tool.name: tool for tool in reg.session_evolved(topics)}
    if extra_tools:
        for tool in extra_tools:
            by_name.setdefault(tool.name, tool)
    session_tools = tuple(by_name[name] for name in sorted(by_name))

    by_scope: dict[str, list[str]] = {}
    for tool in session_tools:
        label = f"- {tool.name}: {tool.description}"
        by_scope.setdefault(tool.scope, []).append(label)

    lines = [
        "[本会话可用 evolved 工具]（调用 run_evolved.tool_name）",
    ]
    for scope in ("common", *sorted(key for key in by_scope if key != "common")):
        if scope not in by_scope:
            continue
        if scope == "common":
            heading = "始终"
        elif scope == "project":
            heading = "项目壳"
        else:
            heading = "本会话主题"
        lines.append(f"## {scope}（{heading}）")
        lines.extend(by_scope[scope])
    if len(session_tools) == 0:
        lines.append("(none active for current topics)")
    return "\n".join(lines)


def format_capability_hints(
    session: Session,
    *,
    registry: ToolRegistry | None = None,
) -> str:
    """Short capability hints (Phase 23 M3: tool names live in INDEX, not here)."""
    _ = registry  # retained for call-site compatibility
    lines = [
        "[能力提示]",
        "- 工具怎么选：先看上方工具索引；细节 `read_file evolve/tool-catalog/buckets/<桶>.md`。"
        "执行面：凡 status=active 均可调；`run_command`/`write_text`/`patch_file` 优先扁平原语，"
        "其余 evolved 经 `run_evolved`（`status=archived` 不可调，见 docs/ARCHIVED-TOOLS.md）。",
        "- 只读：read_file · list_dir · glob_file_search · grep（本地）；web_search · fetch_url（网络）",
        "- 写/改：write_text（新建）/ patch_file（改已有）；或 run_evolved → copy_move / move_to_trash；先试 dry_run",
        "- 执行：run_command（一次性 shell）；长驻 run_evolved → run_service 或 run_command background:true",
        "- 查项目/跨会话：run_evolved → project_catalog；再 read_file data/sessions/<id>/messages.jsonl"
        "（读**其他**会话须 confirm）",
        "- 造新工具：run_evolved → write_evolve（细则见 buckets/evolve.md；scaffold 回合另有短提示）",
        "- 若无合适工具：诚实说明限制；可说「记住」提交 tool 建议到 evolve/proposals",
    ]
    if session.meta.topics:
        lines.insert(
            2,
            f"- 已加载主题 prompt：{', '.join(session.meta.topics)}（管习惯/记忆，不管工具锁）",
        )
    return "\n".join(lines)


def format_tool_loop_user_message(
    session: Session,
    *,
    tool_rounds: int,
    tool_loop_max: int,
    registry: ToolRegistry | None = None,
    segment: int | None = None,
    total_tool_rounds: int | None = None,
) -> str:
    """User-facing message when the tool inner loop hits its cap without progress."""
    reg = registry or ToolRegistry.load(session.paths)
    allowed = sorted(session_evolved_allowlist(session, registry=reg))
    topics = session.meta.topics
    topic_label = "、".join(topics) if topics else "（未确认）"
    tools_label = ", ".join(allowed) if allowed else "（无）"

    segment_note = ""
    if segment is not None and segment > 1:
        segment_note = f"（execute segment {segment}"
        if total_tool_rounds is not None:
            segment_note += f"，累计 {total_tool_rounds} 轮"
        segment_note += "）"

    return (
        f"本条消息的 segment 工具预算已用尽（本 segment 上限 {tool_loop_max} 轮"
        f"{segment_note}，已执行 {tool_rounds} 轮），未能得到最终文字回复，且本段无可见进展。\n\n"
        "每条用户消息都会重新计算工具预算；若任务未完成，请发新消息（如「继续」）再试。\n\n"
        "常见原因：\n"
        "1. 任务需要的能力尚无对应 evolved 工具（或工具 status 非 active）\n"
        "2. 在反复观察（read_file / grep / list_dir / glob_file_search）而未收敛到结论\n"
        "3. 子代理/编排 builtin 预算用尽，或 segment 内无可见进展\n\n"
        f"本会话可用 evolved（凡 active）：{tools_label}\n"
        f"当前主题（管 prompt/memory，不管工具锁）：{topic_label}\n\n"
        "建议：简化问题后重试；查阅 evolve/tool-catalog/INDEX.md 或对应 buckets；"
        "若长期缺工具，可说「记住」提交 tool 建议。"
    )


def format_segment_pause_message(
    *,
    segment: int,
    total_tool_rounds: int,
    auto_continue: bool,
) -> str:
    """Message when execute segment cap hit with progress (T-705)."""
    lines = [
        f"本条消息的工具预算已用尽（segment {segment}，本消息累计 {total_tool_rounds} 轮），已有进展。",
        "",
        "已完成部分：见上文 tool 结果与 assistant 回复。",
    ]
    if auto_continue:
        lines.append("")
        lines.append("将自动继续下一 segment。")
    else:
        lines.append("")
        lines.append("请发一条新消息（如「继续」）以开始下一轮工具预算。")
    return "\n".join(lines)


TASK_PAUSED_MARKER = "本项已完成。回复「继续」开始下一项。"


def format_task_paused_notice(*, next_open_task: str | None = None) -> str:
    """User-facing notice when project task-stop gate ends the turn (T-2006)."""
    lines = [TASK_PAUSED_MARKER]
    if next_open_task:
        lines.append(f"下一项：{next_open_task}")
    return "\n".join(lines)


def ensure_task_paused_text(
    text: str,
    *,
    next_open_task: str | None = None,
    delivery_profile: str = "solo",
) -> str:
    """Append pause notice if missing from assistant text."""
    from project_mode import normalize_delivery_profile

    if normalize_delivery_profile(delivery_profile) == "solo":
        return (text or "").rstrip()
    body = (text or "").rstrip()
    if "回复「继续」" in body or "回复『继续』" in body or "本项已完成" in body:
        return body
    notice = format_task_paused_notice(next_open_task=next_open_task)
    if not body:
        return notice
    return f"{body}\n\n{notice}"


def format_total_cap_message(*, total_tool_rounds: int, total_max: int) -> str:
    """Message when PARENT_EXECUTE_TOTAL_MAX is reached (T-705)."""
    return (
        f"本条用户消息的工具预算已用尽（上限 {total_max} 轮，已用 {total_tool_rounds} 轮）。\n\n"
        "已完成部分：见上文 tool 结果。\n\n"
        "请发一条新消息（如「继续」）以开始下一轮工具预算，或缩小任务范围后重试。"
    )


def format_tool_interrupt_notice(
    kind: str,
    *,
    tool_label: str = "",
) -> str:
    """User/LLM-facing copy when a tool was cancelled, timed out, or confirm rejected."""
    label = (tool_label or "工具").strip() or "工具"
    if kind == "confirm_rejected":
        return (
            f"{label}确认被拒绝或超时（不是工具回合上限）。"
            "若仍要执行，请再发消息并在确认框点「接受」；"
            "前端依赖损坏请优先 `run_evolved` → `repair_node_modules`，"
            "勿手写 `rmdir`/`npm install` 拆两步。"
        )
    if kind == "timeout":
        return (
            f"{label}因回合墙钟超时被系统自动停止"
            "（不是你点了「停止」，也不是工具回合上限）。"
            "工具执行期间墙钟会暂停；若仍触发，请再发「继续」。"
            "前端依赖请用 `repair_node_modules` 一次搞定，勿拆成 rmdir + npm install。"
        )
    return (
        f"{label}执行被停止（不是工具回合上限）。"
        "若你点了「停止」才会中断；长任务请等待完成或确认框点接受。"
        "若要重试请再发「继续」。前端依赖优先 `repair_node_modules`。"
    )


def format_tool_interrupt_kernel_message(
    kind: str,
    *,
    tool_label: str = "",
) -> str:
    """Inject into transcript so the model does not invent a budget-cap story."""
    return f"[内核] {format_tool_interrupt_notice(kind, tool_label=tool_label)}"


def session_evolved_allowlist(
    session: Session,
    *,
    registry: ToolRegistry | None = None,
) -> frozenset[str]:
    """Allowed ``run_evolved.tool_name`` values (Phase 23 M1: all ``active``, no topic lock)."""
    reg = registry or ToolRegistry.load(session.paths)
    return frozenset(
        tool.name for tool in reg.evolved() if tool.status == "active"
    )

def detect_scaffold_tool_turn(user_text: str) -> bool:
    """True when the user is asking to create/scaffold an evolved tool this turn."""
    text = user_text.strip()
    if not text:
        return False
    lower = text.casefold()
    markers = (
        "write_evolve",
        "evolve/tools/",
        "tool.toml",
        "新工具",
        "创建工具",
        "造工具",
        "加工具",
        "注册工具",
        "scaffold",
        "/main.py",
        "json_query",
        "scaffold tool",
        "scaffold a tool",
        "new evolved tool",
        "evolve tool",
    )
    if any(m in lower or m in text for m in markers):
        return True
    if "工具" in text and any(v in text for v in ("创建", "造", "写", "实现", "加", "新建")):
        return True
    if re.search(
        r"\b(build|create|implement|add|scaffold|write|make)\b.{0,48}\btool\b",
        text,
        flags=re.IGNORECASE,
    ):
        return True
    return False


def format_scaffold_tool_overlay() -> str:
    """Per-turn overlay when user is scaffolding an evolved tool."""
    return """[本轮：创建 evolved 工具 — 硬约束]
- **禁止** write_text 写 `evolve/tools/<scope>/<name>/` 下 `main.py` / `tool.toml` / `README.md`；可 `write_text` 暂存到 `workspace/_staging.toml` 再 `content_workspace_path`
- **只用** run_evolved → write_evolve：`path` + `content_base64` 与 `tool_name` **同级**，`arguments` 为 `{}`
- **顺序**：先 `.../main.py`，再 `.../tool.toml`（`status = "draft"`）
- `tool.toml` **必须** `content_base64`；**`on_conflict: overwrite`**（默认 skip 遇已存在文件会失败）"""


def format_write_evolve_cookbook(*, scaffold_turn: bool = False) -> str:
    """Mandatory overlay when write_evolve is available (fixes LLM nested JSON escaping)."""
    staging = (
        "6. 备选（**仅 scaffold 回合**）：`write_text` → `workspace/_staging.toml`，"
        "再 `content_workspace_path: \"_staging.toml\"`\n"
        if scaffold_turn
        else ""
    )
    return f"""[write_evolve 调用规范 — 必读]
通过 run_evolved 写 evolve/tools/ 下新工具时：
1. **顶层字段**（与 tool_name 同级，见 run_evolved 函数 schema）：`path`、`content_base64`、`on_conflict`
2. **禁止**把 TOML/多行正文放进 `arguments.content`（双引号会导致 tool_calls JSON 解析失败）
3. **顺序**：先 `.../main.py`，再 `.../tool.toml`（tool.toml 先用 `status = "draft"`）
4. `content_base64` = UTF-8 正文的**标准 base64**（tool.toml 必须用 base64）
5. **`on_conflict: overwrite`**（默认 skip 在文件已存在时返回失败）
{staging}示例：`{{"tool_name":"write_evolve","path":"evolve/tools/data/foo/main.py","content_base64":"cHJpbnQoJ29rJyk=","on_conflict":"overwrite","arguments":{{}}}}`"""


TOOL_CATALOG_INDEX_REL = Path("evolve") / "tool-catalog" / "INDEX.md"
TOOL_CATALOG_INDEX_MAX_CHARS = 2048


def load_tool_catalog_index(
    paths: AgentPaths,
    *,
    max_chars: int = TOOL_CATALOG_INDEX_MAX_CHARS,
) -> str:
    """Load L0 tool INDEX for system overlay (Phase 23 M3). Truncates if oversized."""
    path = paths.agent_root / TOOL_CATALOG_INDEX_REL
    if not path.is_file():
        return (
            "[工具索引缺失] 期望路径：evolve/tool-catalog/INDEX.md。"
            "请用 read_file 查 evolve/tools/ 或恢复 INDEX。"
        )
    text = path.read_text(encoding="utf-8").strip()
    if len(text) <= max_chars:
        return text
    return (
        text[: max_chars - 80].rstrip()
        + "\n\n…（INDEX 已截断；完整文件：read_file evolve/tool-catalog/INDEX.md）"
    )


def format_evolved_catalog_overlay(
    session: Session,
    *,
    registry: ToolRegistry | None = None,
) -> str:
    """Phase 23 M3: inject short INDEX (+ hints); no full per-tool catalog listing."""
    reg = registry or ToolRegistry.load(session.paths)
    index = load_tool_catalog_index(session.paths)
    hints = format_capability_hints(session, registry=reg)
    parts = [index, hints]
    # write_evolve cookbook: only when scaffolding this turn (avoid every-turn bulk)
    allow = session_evolved_allowlist(session, registry=reg)
    if "write_evolve" in allow and session.scaffold_tool_turn:
        parts.append(format_write_evolve_cookbook(scaffold_turn=True))
    return "\n\n".join(parts)


def load_project_prompt(evolve_dir: Path, *, profile: str = "solo") -> str:
    from project_mode import load_project_prompt as _load

    return _load(evolve_dir, profile=profile)


def load_digest(session: Session) -> str | None:
    if not session.digest_path.is_file():
        return None
    text = session.digest_path.read_text(encoding="utf-8").strip()
    return text or None


def build_system_prompt(
    session: Session,
    *,
    paths: AgentPaths | None = None,
    registry: ToolRegistry | None = None,
    include_overlay: bool = True,
    agent_core_dir: Path | None = None,
) -> LoadedSystem:
    """Build single system string (§4.1 base + optional §4.2 overlay)."""
    agent_paths = paths or session.paths
    reg = registry or ToolRegistry.load(agent_paths)
    evolve_dir = agent_paths.evolve
    topic_index = load_topic_index(evolve_dir)
    from session import is_terminal_harness

    terminal = is_terminal_harness(session.meta)
    core_section = "terminal" if terminal else "core"
    core_text = (
        load_terminal_text(agent_core_dir=agent_core_dir)
        if terminal
        else load_core_text(agent_core_dir=agent_core_dir)
    )

    sections: list[tuple[str, str]] = [
        (core_section, core_text),
        ("topic_index", format_topic_index(topic_index)),
        ("memory_index", format_memory_index(scan_memory_index(evolve_dir))),
        ("builtin_summary", format_builtin_summary()),
        ("host_scope", _format_host_scope_overlay(agent_paths)),
    ]

    if include_overlay:
        sections.append(("session", format_session_overlay(session)))
        if terminal:
            scope_overlay = format_terminal_scope_overlay(session)
            if scope_overlay:
                sections.append(("terminal_scope", scope_overlay))
        turn_discipline = format_turn_discipline_overlay(session)
        if turn_discipline:
            sections.append(("turn_discipline", turn_discipline))
        sections.append(("safety", load_safety_prompt(evolve_dir)))

        for topic_id, prompt_text in load_confirmed_topic_prompts(
            evolve_dir, session.meta.topics, index=topic_index
        ):
            sections.append((f"topic_prompt:{topic_id}", prompt_text))

        if is_workshop_eligible(session) and not terminal:
            sections.append(("tool_workshop", load_tool_workshop_prompt(evolve_dir)))

        sections.append(
            (
                "evolved_catalog",
                format_evolved_catalog_overlay(session, registry=reg),
            )
        )

        if session.scaffold_tool_turn:
            sections.append(("scaffold_tool", format_scaffold_tool_overlay()))

        escalation = format_evolve_escalation_hint(session)
        if escalation:
            sections.append(("evolve_escalation", escalation))

        if session.subagent_overlay:
            sections.append(("subagent_summary", session.subagent_overlay.strip()))

        if session.meta.active_shell == "project" and session.meta.project_root and not terminal:
            from project_mode import (
                build_tasks_injection_slice,
                extract_task_id,
                first_open_task_line,
                format_project_overlay,
                get_delivery_profile,
                is_project_continue_utterance,
                project_dir,
                read_milestone_review_overlay_key,
                read_task_stats,
                read_tasks_text_for_injection,
            )

            profile = get_delivery_profile(session.meta)

            tasks_path = (
                project_dir(session.paths, session.meta.project_id) / "TASKS.md"
                if session.meta.project_id
                else None
            )
            stats = read_task_stats(tasks_path) if tasks_path is not None else None
            tasks_text = read_tasks_text_for_injection(tasks_path)
            last_user = ""
            for msg in reversed(session.messages):
                if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                    last_user = msg["content"]
                    break
            continue_turn = is_project_continue_utterance(last_user)
            next_open = first_open_task_line(tasks_text)
            open_slice = (
                build_tasks_injection_slice(tasks_text)
                if (session.meta.project_plan_status or "draft") == "confirmed"
                else ""
            )
            milestone_key: str | None = None
            if session.meta.project_id:
                from plan_agent import get_plan_agent

                try:
                    milestone_key = get_plan_agent(
                        session.paths, session.meta.project_id
                    ).milestone_review_overlay_key()
                except Exception:
                    milestone_key = None
                if not milestone_key:
                    milestone_key = read_milestone_review_overlay_key(
                        session.paths, session.meta.project_id
                    )
            sections.append(
                (
                    "project_prompt",
                    load_project_prompt(evolve_dir, profile=profile),
                )
            )
            overlay = format_project_overlay(
                project_root=session.meta.project_root,
                project_id=session.meta.project_id,
                plan_status=session.meta.project_plan_status or "draft",
                task_stats=stats,
                continue_turn=continue_turn,
                next_open_task=next_open,
                armed_task_id=extract_task_id(next_open or ""),
                open_tasks_slice=open_slice or None,
                delivery_profile=profile,
                milestone_review_suggested=milestone_key,
            )
            digest_text = load_digest(session) or ""
            if profile == "solo" and digest_text and (
                "report_progress" in digest_text and "必须" in digest_text
            ):
                overlay += (
                    "\ndigest_profile_note: solo — 忽略 digest 中与一停/"
                    "强制 report_progress 冲突的旧叙述"
                )
            sections.append(
                (
                    "project_mode",
                    overlay,
                )
            )

        digest = load_digest(session)
        if digest:
            sections.append(("digest", f"[对话摘要 digest]\n{digest}"))

    non_empty = [(name, text.strip()) for name, text in sections if text.strip()]
    prompt = SECTION_SEPARATOR.join(text for _, text in non_empty)
    static_pairs = [(name, text) for name, text in non_empty if not is_dynamic_system_section(name)]
    dynamic_pairs = [(name, text) for name, text in non_empty if is_dynamic_system_section(name)]
    static_prompt = SECTION_SEPARATOR.join(text for _, text in static_pairs)
    dynamic_prompt = SECTION_SEPARATOR.join(text for _, text in dynamic_pairs)
    return LoadedSystem(
        prompt=prompt,
        section_names=tuple(name for name, _ in non_empty),
        static_prompt=static_prompt,
        dynamic_prompt=dynamic_prompt,
        static_section_names=tuple(name for name, _ in static_pairs),
        dynamic_section_names=tuple(name for name, _ in dynamic_pairs),
    )


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, flags=re.DOTALL)
    if not match:
        return {}
    block = match.group(1)
    result: dict[str, Any] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            result[key] = [item.strip() for item in inner.split(",") if item.strip()] if inner else []
        else:
            result[key] = value
    return result


def _frontmatter_topics(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    return ()


def _install_demo_evolved_tool(
    tool_dir: Path,
    *,
    name: str,
    topics: list[str],
    description: str,
) -> None:
    tool_dir.mkdir(parents=True, exist_ok=True)
    (tool_dir / "main.py").write_text(
        "import json\nprint(json.dumps({'ok': True}))\n",
        encoding="utf-8",
    )
    topics_literal = ", ".join(f'"{topic}"' for topic in topics)
    (tool_dir / "tool.toml").write_text(
        f"""[tool]
name = "{name}"
description = "{description}"
version = "1.0.0"
status = "active"
topics = [{topics_literal}]

[entry]
type = "python"
path = "main.py"

[schema.input]
type = "object"
required = []

[schema.output]
type = "object"

[policy]
confirm = true
dry_run_supported = true
workspace_only = true
timeout_sec = 60
""",
        encoding="utf-8",
    )


def _demo() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        agent_core = root / "agent-core"
        (agent_core / "prompts").mkdir(parents=True)
        (agent_core / "prompts" / "core.txt").write_text(
            "CORE-STUB: identity and boundaries.",
            encoding="utf-8",
        )

        evolve = root / "evolve"
        (evolve / "prompts").mkdir(parents=True)
        (evolve / "prompts" / "safety.md").write_text(
            "SAFETY-STUB: always-on rules.",
            encoding="utf-8",
        )
        (evolve / "prompts" / "workflow.md").write_text(
            "# Workflow topic\nPrefer small steps.",
            encoding="utf-8",
        )
        (evolve / "prompts" / "coding.md").write_text(
            "# Coding topic\nUse Python 3.12+; read docs before coding.",
            encoding="utf-8",
        )
        (evolve / "memories" / "workflow").mkdir(parents=True)
        (evolve / "memories" / "workflow" / "demo.md").write_text(
            "---\nid: demo-memory\ntopics: [workflow]\nstatus: active\n"
            "summary: Demo memory entry\n---\n\nBody.",
            encoding="utf-8",
        )
        (evolve / "memories" / "coding").mkdir(parents=True)
        (evolve / "memories" / "coding" / "archived.md").write_text(
            "---\nid: old-fact\ntopics: [coding]\nstatus: archived\n"
            "summary: Should not appear\n---\n\nArchived body.",
            encoding="utf-8",
        )
        (evolve / "memories" / "coding" / "suspect.md").write_text(
            "---\nid: suspect-fact\ntopics: [coding]\nstatus: suspect\n"
            "summary: Suspect but still indexed\n---\n\nSuspect body.",
            encoding="utf-8",
        )
        (evolve / "memories" / "workflow" / "multi.md").write_text(
            "---\nid: multi-topic\ntopics: [coding, workflow]\nstatus: active\n"
            "summary: Spans two topics\n---\n\nBody.",
            encoding="utf-8",
        )
        (evolve / "memories" / "workflow" / "no-frontmatter.md").write_text(
            "# Not a memory file\nNo YAML header.",
            encoding="utf-8",
        )
        (evolve / "memories" / "workflow" / "missing-id.md").write_text(
            "---\ntopics: [workflow]\nstatus: active\nsummary: no id field\n---\n",
            encoding="utf-8",
        )
        (evolve / INDEX_CORE_REL).write_text(
            '[[topic]]\nid = "workflow"\nname = "工作流"\n'
            'description = "整理与重复任务"\n'
            'prompt = "prompts/workflow.md"\n'
            'memory_dirs = ["memories/workflow"]\n'
            'tool_dirs = ["tools/workflow"]\n\n'
            '[[topic]]\nid = "coding"\nname = "开发"\n'
            'description = "Python 开发"\n'
            'prompt = "prompts/coding.md"\n'
            'memory_dirs = ["memories/coding"]\n'
            'tool_dirs = ["tools/coding"]\n',
            encoding="utf-8",
        )
        shutil.copytree(
            AgentPaths.discover().evolve / "tools" / "common" / "write_text",
            evolve / "tools" / "common" / "write_text",
        )
        _install_demo_evolved_tool(
            evolve / "tools" / "coding" / "coding_probe",
            name="coding_probe",
            description="Coding-only evolved probe",
            topics=["coding"],
        )
        _install_demo_evolved_tool(
            evolve / "tools" / "workflow" / "workflow_probe",
            name="workflow_probe",
            description="Workflow-only evolved probe",
            topics=["workflow"],
        )
        (root / "workspace").mkdir()
        (root / "data" / "sessions" / "loader-demo").mkdir(parents=True)

        paths = AgentPaths.from_root(root)
        session = Session(
            conversation_id="loader-demo",
            session_dir=paths.data / "sessions" / "loader-demo",
            goal="Test system assembly",
            meta=SessionMeta(
                topics=["workflow"],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[],
            paths=paths,
        )
        session.digest_path.write_text("# 压缩 1\nEarlier chat summary.", encoding="utf-8")

        loaded = build_system_prompt(session, paths=paths, agent_core_dir=agent_core)
        names = loaded.section_names

        assert names[0] == "core"
        assert "CORE-STUB" in loaded.prompt
        print("[PASS] section 1: core.txt")

        assert "topic_index" in names
        assert "workflow" in loaded.prompt
        assert "tool_dirs: tools/workflow" in loaded.prompt
        print("[PASS] section 2: topic index from _index.toml (incl. tool_dirs)")

        assert "memory_index" in names
        assert "demo-memory" in loaded.prompt
        assert "demo-memory (workflow): Demo memory entry" in loaded.prompt
        print("[PASS] section 3: memory id+summary index")

        memory_entries = scan_memory_index(evolve)
        memory_ids = {entry.memory_id for entry in memory_entries}
        assert "demo-memory" in memory_ids
        assert "multi-topic" in memory_ids
        assert "suspect-fact" in memory_ids
        assert "old-fact" not in memory_ids
        memory_block = format_memory_index(memory_entries)
        assert "multi-topic (coding, workflow): Spans two topics" in memory_block
        assert "suspect-fact (coding): Suspect but still indexed" in memory_block
        assert "old-fact" not in memory_block
        assert "Should not appear" not in memory_block
        assert "no id field" not in memory_block
        print("[PASS] T-302: scan memories; archived skipped; MEMORY §5.2 format")

        assert "builtin_summary" in names
        assert "read_file" in loaded.prompt
        assert names.index("builtin_summary") < names.index("session")
        print("[PASS] section 4: builtin summary before overlay")

        assert "session" in names
        assert "loader-demo" in loaded.prompt
        print("[PASS] overlay: session block")

        assert "safety" in names
        assert "SAFETY-STUB" in loaded.prompt
        print("[PASS] overlay: safety always loaded")

        assert "topic_prompt:workflow" in names
        assert "Prefer small steps" in loaded.prompt
        print("[PASS] overlay: topic prompt for workflow")

        coding_session = Session(
            conversation_id="loader-coding",
            session_dir=paths.data / "sessions" / "loader-coding",
            goal="Implement loader",
            meta=SessionMeta(
                topics=["coding", "workflow"],
                llm_model="deepseek-v4-pro",
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[],
            paths=paths,
        )
        coding_loaded = build_system_prompt(coding_session, paths=paths, agent_core_dir=agent_core)
        assert "topic_prompt:coding" in coding_loaded.section_names
        assert "topic_prompt:workflow" in coding_loaded.section_names
        assert "Use Python 3.12+" in coding_loaded.prompt
        assert coding_loaded.section_names.index("safety") < coding_loaded.section_names.index(
            "topic_prompt:coding"
        )
        assert coding_loaded.section_names.index("topic_prompt:coding") < coding_loaded.section_names.index(
            "evolved_catalog"
        )
        prompts = load_confirmed_topic_prompts(evolve, ["coding", "workflow", "safety"])
        assert [tid for tid, _ in prompts] == ["coding", "workflow"]
        assert resolve_topic_prompt_path(evolve, "coding", index=load_topic_index(evolve)) == (
            evolve / "prompts" / "coding.md"
        )
        print("[PASS] T-305: confirmed topics inject full prompt via _index prompt path")

        demo_reg = ToolRegistry.load(paths)
        workflow_only = Session(
            conversation_id="loader-workflow-tools",
            session_dir=paths.data / "sessions" / "loader-workflow-tools",
            goal="tool filter",
            meta=SessionMeta(
                topics=["workflow"],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[],
            paths=paths,
        )
        workflow_loaded = build_system_prompt(
            workflow_only, paths=paths, agent_core_dir=agent_core, registry=demo_reg
        )
        # Phase 23 M3: overlay is INDEX (or missing stub), not per-tool catalog lines
        assert "工具索引" in workflow_loaded.prompt or "工具索引缺失" in workflow_loaded.prompt
        assert "- workflow_probe:" not in workflow_loaded.prompt
        assert "- coding_probe:" not in workflow_loaded.prompt
        coding_allow = session_evolved_allowlist(coding_session, registry=demo_reg)
        workflow_allow = session_evolved_allowlist(workflow_only, registry=demo_reg)
        # Phase 23 M1: allowlist is all active (not topic-filtered)
        assert coding_allow == workflow_allow
        assert coding_allow == frozenset({"write_text", "coding_probe", "workflow_probe"})
        coding_only = Session(
            conversation_id="loader-coding-tools",
            session_dir=paths.data / "sessions" / "loader-coding-tools",
            goal="tool filter",
            meta=SessionMeta(
                topics=["coding"],
                llm_model="deepseek-v4-pro",
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[],
            paths=paths,
        )
        coding_only_allow = session_evolved_allowlist(coding_only, registry=demo_reg)
        assert coding_only_allow == frozenset({"write_text", "coding_probe", "workflow_probe"})

        from tools.executor import ToolExecutor

        executor = ToolExecutor.create(
            paths=paths,
            session_dir=session.session_dir,
            allowed_evolved=set(coding_only_allow),
            confirm_fn=lambda _preview, _allow_all: "y",
        )
        blocked = executor.run(
            "run_evolved",
            {"tool_name": "workflow_probe", "arguments": {}},
        )
        assert blocked.ok is False
        assert blocked.error is not None
        assert "不在本会话清单" in blocked.error.message
        allowed_call = executor.run(
            "run_evolved",
            {"tool_name": "coding_probe", "arguments": {}},
        )
        assert allowed_call.ok is True
        print("[PASS] T-308: evolved catalog (common+topic) + run_evolved allowlist")

        coding_only_loaded = build_system_prompt(
            coding_only, paths=paths, agent_core_dir=agent_core, registry=demo_reg
        )
        assert "[能力提示]" in coding_only_loaded.prompt
        writing_only = Session(
            conversation_id="loader-writing-hints",
            session_dir=paths.data / "sessions" / "loader-writing-hints",
            goal="hints",
            meta=SessionMeta(
                topics=["writing"],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[],
            paths=paths,
        )
        writing_loaded = build_system_prompt(
            writing_only, paths=paths, agent_core_dir=agent_core, registry=demo_reg
        )
        assert "尚无 active 的专用 evolved 工具" in writing_loaded.prompt
        print("[PASS] capability hints when topic has no evolved tools")

        log_path = paths.data / "evolve_log.jsonl"
        log = EvolveLog(log_path)
        start_fields = session_start_log_fields(session)
        assert "demo-memory" in start_fields["memory_ids_loaded"]
        assert "workflow" in start_fields["topics_available"]
        log_session_start(session, evolve_log=log)
        log_topics_confirmed(coding_session, evolve_log=log, registry=ToolRegistry.load(paths))
        events = read_events(log_path)
        start_evt = next(event for event in events if event.get("event") == EVENT_SESSION_START)
        confirm_evt = next(event for event in events if event.get("event") == EVENT_TOPICS_CONFIRMED)
        assert start_evt["conversation_id"] == "loader-demo"
        assert isinstance(start_evt["memory_ids_loaded"], list)
        assert isinstance(start_evt["topics_available"], list)
        assert confirm_evt["topics_confirmed"] == ["coding", "workflow"]
        assert "prompts/coding.md" in confirm_evt["prompt_files_loaded"]
        assert "prompts/workflow.md" in confirm_evt["prompt_files_loaded"]
        assert "write_text" in confirm_evt["evolved_tools_listed"]
        print("[PASS] T-306: evolve_log session_start + topics_confirmed")

        assert "evolved_catalog" in names
        assert "write_text" in loaded.prompt
        print("[PASS] overlay: evolved catalog (common)")

        assert "digest" in names
        assert "Earlier chat summary" in loaded.prompt
        print("[PASS] overlay: digest when digest.md exists")

        assert loaded.prompt.count(SECTION_SEPARATOR) == len(names) - 1
        print("[PASS] sections joined with --- separator")

        empty_topics = Session(
            conversation_id="loader-empty",
            session_dir=paths.data / "sessions" / "loader-empty",
            goal="",
            meta=SessionMeta(topics=[], llm_model="deepseek-v4-flash", updated_at=utc_now_iso()),
            messages=[],
            paths=paths,
        )
        empty_loaded = build_system_prompt(empty_topics, paths=paths, agent_core_dir=agent_core)
        assert "safety" in empty_loaded.section_names
        assert "SAFETY-STUB" in empty_loaded.prompt
        assert not any(name.startswith("topic_prompt:") for name in empty_loaded.section_names)
        print("[PASS] safety loads even when topics=[]")

        base_only = build_system_prompt(
            session, paths=paths, include_overlay=False, agent_core_dir=agent_core
        )
        assert base_only.section_names == ("core", "topic_index", "memory_index", "builtin_summary")
        assert "SAFETY-STUB" not in base_only.prompt
        print("[PASS] include_overlay=False yields S0 base only")

        # T-801: dual index merge (EXTENSIONS §3)
        legacy_root = Path(tempfile.mkdtemp(prefix="loader-t801-legacy-"))
        try:
            legacy_evolve = legacy_root / "evolve"
            legacy_evolve.mkdir(parents=True)
            (legacy_evolve / INDEX_LEGACY_REL).write_text(
                '[[topic]]\nid = "workflow"\nname = "工作流"\n'
                'description = "legacy only"\n'
                'prompt = "prompts/workflow.md"\n'
                'memory_dirs = ["memories/workflow"]\n'
                'tool_dirs = ["tools/workflow"]\n',
                encoding="utf-8",
            )
            legacy_topics = load_topic_index(legacy_evolve)
            assert len(legacy_topics) == 1
            assert legacy_topics[0].id == "workflow"
            print("[PASS] T-801: legacy _index.toml only")
        finally:
            shutil.rmtree(legacy_root, ignore_errors=True)

        merge_root = Path(tempfile.mkdtemp(prefix="loader-t801-merge-"))
        try:
            merge_evolve = merge_root / "evolve"
            merge_evolve.mkdir(parents=True)
            (merge_evolve / INDEX_CORE_REL).write_text(
                '[[topic]]\nid = "coding"\nname = "开发"\n'
                'description = "core"\n'
                'prompt = "prompts/coding.md"\n'
                'memory_dirs = ["memories/coding"]\n'
                'tool_dirs = ["tools/coding"]\n',
                encoding="utf-8",
            )
            (merge_evolve / INDEX_USER_REL).write_text(
                '[[topic]]\nid = "data"\nname = "数据处理"\n'
                'description = "user ext"\n'
                'prompt = "prompts/data.md"\n'
                'memory_dirs = ["memories/data"]\n'
                'tool_dirs = ["tools/data"]\n',
                encoding="utf-8",
            )
            merged = load_topic_index(merge_evolve)
            assert [entry.id for entry in merged] == ["coding", "data"]
            print("[PASS] T-801: core + user merge")
        finally:
            shutil.rmtree(merge_root, ignore_errors=True)

        conflict_root = Path(tempfile.mkdtemp(prefix="loader-t801-conflict-"))
        try:
            conflict_evolve = conflict_root / "evolve"
            conflict_evolve.mkdir(parents=True)
            (conflict_evolve / INDEX_CORE_REL).write_text(
                '[[topic]]\nid = "workflow"\nname = "工作流"\n'
                'description = "core"\n'
                'prompt = "prompts/workflow.md"\n'
                'memory_dirs = []\n'
                'tool_dirs = []\n',
                encoding="utf-8",
            )
            (conflict_evolve / INDEX_USER_REL).write_text(
                '[[topic]]\nid = "workflow"\nname = "dup"\n'
                'description = "conflict"\n'
                'prompt = "prompts/workflow.md"\n'
                'memory_dirs = []\n'
                'tool_dirs = []\n',
                encoding="utf-8",
            )
            try:
                load_topic_index(conflict_evolve)
                raise AssertionError("expected TopicIndexError")
            except TopicIndexError as exc:
                assert "workflow" in str(exc)
            print("[PASS] T-801: core/user id conflict raises TopicIndexError")
        finally:
            shutil.rmtree(conflict_root, ignore_errors=True)

    # Real repo smoke (uses checked-in core + safety).
    paths = AgentPaths.discover()
    repo_session = Session(
        conversation_id="_loader_smoke",
        session_dir=paths.data / "sessions" / "_loader_smoke",
        goal="smoke",
        meta=SessionMeta(topics=["workflow"], llm_model="deepseek-v4-flash", updated_at=utc_now_iso()),
        messages=[],
        paths=paths,
    )
    repo_loaded = build_system_prompt(repo_session)
    assert "safety" in repo_loaded.section_names
    assert "write_text" in repo_loaded.prompt
    print("[PASS] real repo: safety + write_text in system prompt")

    repo_topics = load_topic_index(paths.evolve)
    assert len(repo_topics) >= 4
    assert any(e.id == "coding" and e.tool_dirs == ("tools/coding",) for e in repo_topics)
    assert any(e.id == "safety" and e.tool_dirs == () for e in repo_topics)
    assert any(e.id == "data" and e.tool_dirs == ("tools/data",) for e in repo_topics)
    topic_block = format_topic_index(repo_topics)
    assert "coding:" in topic_block and "开发与代码" in topic_block
    assert "tool_dirs: tools/coding" in topic_block
    assert "tool_dirs: (none)" in topic_block
    base_repo = build_system_prompt(repo_session, include_overlay=False)
    assert "tool_dirs: tools/coding" in base_repo.prompt
    assert "tool_dirs: tools/workflow" in base_repo.prompt
    print("[PASS] T-301: merged topic index → id/name/description/tool_dirs in system")

    repo_memories = scan_memory_index(paths.evolve)
    repo_memory_block = format_memory_index(repo_memories)
    assert repo_memory_block.startswith("[久远记忆]")
    base_repo_mem = build_system_prompt(repo_session, include_overlay=False)
    assert "[久远记忆]" in base_repo_mem.prompt
    if repo_memories:
        entry = repo_memories[0]
        assert f"- {entry.memory_id} (" in base_repo_mem.prompt
    else:
        assert "(none active)" in base_repo_mem.prompt
    print("[PASS] T-302: memory index in S0 (repo has %d active)" % len(repo_memories))

    coding_prompt_path = paths.evolve / "prompts" / "coding.md"
    if coding_prompt_path.is_file():
        coding_session = Session(
            conversation_id="_loader_coding",
            session_dir=paths.data / "sessions" / "_loader_coding",
            goal="smoke",
            meta=SessionMeta(topics=["coding"], llm_model="deepseek-v4-pro", updated_at=utc_now_iso()),
            messages=[],
            paths=paths,
        )
        coding_loaded = build_system_prompt(coding_session)
        assert "topic_prompt:coding" in coding_loaded.section_names
        assert coding_prompt_path.read_text(encoding="utf-8").strip() in coding_loaded.prompt
        print("[PASS] T-305: real repo coding.md full text in system overlay")

        log_path = paths.data / "evolve_log.jsonl"
        log = EvolveLog(log_path)
        log_session_start(coding_session, evolve_log=log)
        log_topics_confirmed(coding_session, evolve_log=log, registry=ToolRegistry.load(paths))
        tail = read_events(log_path, limit=2)
        assert tail[-2]["event"] == EVENT_SESSION_START
        assert tail[-2]["topics_available"]
        assert tail[-1]["event"] == EVENT_TOPICS_CONFIRMED
        assert tail[-1]["topics_confirmed"] == ["coding"]
        assert "prompts/coding.md" in tail[-1]["prompt_files_loaded"]
        assert "write_text" in tail[-1]["evolved_tools_listed"]
        print("[PASS] T-306: real repo evolve_log fields")

        assert any(entry.memory_id == "project-my-agent" for entry in repo_memories)
        assert "project-my-agent (coding):" in base_repo_mem.prompt
        assert "先 tool 后 skill" in base_repo_mem.prompt
        assert "import agent_core" in coding_loaded.prompt
        assert "topic_prompt:coding" in coding_loaded.section_names
        print("[PASS] T-307: coding prompt + memory index in system (repo evolve samples)")

        repo_reg = ToolRegistry.load(paths)
        repo_allow = session_evolved_allowlist(coding_session, registry=repo_reg)
        repo_catalog = format_evolved_catalog_overlay(coding_session, registry=repo_reg)
        required_tools = frozenset(
            {
                "write_text",
                "patch_file",
                "copy_move",
                "move_to_trash",
                "write_evolve",
                "run_demo",
                "run_tests",
                "git_snapshot",
                "run_command",
            }
        )
        assert required_tools.issubset(repo_allow)
        assert "工具索引" in repo_catalog
        assert "buckets/" in repo_catalog
        assert "[本会话可用 evolved 工具]" not in repo_catalog
        assert "- run_demo:" not in repo_catalog
        print("[PASS] T-308: INDEX overlay + allowlist all active (Phase 23 M3)")

        workflow_m3 = Session(
            conversation_id="loader-workflow-m3",
            session_dir=paths.data / "sessions" / "loader-workflow-m3",
            goal="整理下载目录",
            meta=SessionMeta(
                topics=["workflow"],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[],
            paths=paths,
        )
        wf_allow = session_evolved_allowlist(workflow_m3, registry=repo_reg)
        wf_catalog = format_evolved_catalog_overlay(workflow_m3, registry=repo_reg)
        required_wf_tools = frozenset(
            {
                "write_text",
                "patch_file",
                "copy_move",
                "move_to_trash",
                "write_evolve",
                "sort_by_extension",
                "rename_batch",
                "flatten_dir",
                "dedupe_by_name",
                "archive_by_date",
                "run_command",
            }
        )
        assert required_wf_tools.issubset(wf_allow)
        assert "工具索引" in wf_catalog
        assert "organize.md" in wf_catalog
        print("[PASS] T-502: workflow tools in allowlist; INDEX points organize bucket")

        data_session = Session(
            conversation_id="loader-data-t805",
            session_dir=paths.data / "sessions" / "loader-data-t805",
            goal="preview csv",
            meta=SessionMeta(
                topics=["data"],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[],
            paths=paths,
        )
        data_allow = session_evolved_allowlist(data_session, registry=repo_reg)
        data_catalog = format_evolved_catalog_overlay(data_session, registry=repo_reg)
        assert "csv_head" in data_allow
        assert "工具索引" in data_catalog
        data_prompt_path = paths.evolve / "prompts" / "data.md"
        if data_prompt_path.is_file():
            data_loaded = build_system_prompt(data_session)
            assert "topic_prompt:data" in data_loaded.section_names
            assert "csv_head" in data_loaded.prompt  # from topic prompt, not full catalog
        print("[PASS] T-805: data topic prompt + csv_head allowlist; INDEX overlay")
    else:
        print("[SKIP] T-305 real repo: evolve/prompts/coding.md not present (see T-307)")

    fresh_s4 = Session(
        "_esc",
        paths.data / "sessions" / "_esc",
        "g",
        SessionMeta(topics=["workflow"], llm_model="flash", updated_at=utc_now_iso(), phase="S4"),
        [],
        paths,
    )
    with_hint = build_system_prompt(fresh_s4)
    assert "evolve_escalation" in with_hint.section_names
    assert "每会话最多一次" in with_hint.prompt
    used_s4 = Session(
        "_esc2",
        paths.data / "sessions" / "_esc2",
        "g",
        SessionMeta(
            topics=["workflow"],
            llm_model="flash",
            updated_at=utc_now_iso(),
            phase="S4",
            evolve_offer_used=True,
        ),
        [],
        paths,
    )
    without_hint = build_system_prompt(used_s4)
    assert "evolve_escalation" not in without_hint.section_names
    print("[PASS] T-403: evolve_escalation hint in system when offer slot unused")

    core_text = load_core_text()
    assert "my-agent" in core_text
    assert "run_evolved" in core_text
    assert "Do not pretend" in core_text or "pretend" in core_text.lower()
    assert "read_file" in core_text and "grep" in core_text
    assert "T-209" not in core_text
    print("[PASS] core.txt: identity, boundaries, no pretend (T-209)")

    assert "Turn discipline" in core_text
    assert "子代理摘要" in core_text or "explore subagent" in core_text.lower()
    assert "TASKS.md" in core_text
    assert "parallel" in core_text.lower() or "Parallel" in core_text
    print("[PASS] T-701: core.txt Turn discipline (qa/plan, subagent, execute, parallel)")

    discipline_session = Session(
        "_t701",
        paths.data / "sessions" / "_t701",
        "discipline",
        SessionMeta(topics=["coding"], llm_model="pro", updated_at=utc_now_iso(), phase="S4"),
        [],
        paths,
    )
    discipline_loaded = build_system_prompt(discipline_session)
    assert "subagent: none" in discipline_loaded.prompt
    assert "turn_discipline" in discipline_loaded.section_names
    assert "轮次纪律" in discipline_loaded.prompt
    print("[PASS] T-701: session overlay subagent: none + turn_discipline section")

    from subagent import SubagentResult, format_subagent_overlay

    discipline_session.subagent_overlay = format_subagent_overlay(
        SubagentResult(
            kind="explore",
            summary="demo summary",
            paths_cited=["evolve/tools/coding/run_demo/tool.toml"],
            tool_rounds=2,
            truncated=False,
            task="demo",
        )
    )
    with_sub = build_system_prompt(discipline_session)
    assert "subagent: used" in with_sub.prompt
    assert "subagent_summary" in with_sub.section_names
    assert "[子代理摘要 · explore]" in with_sub.prompt
    assert "subagent: used" in with_sub.prompt
    assert "勿重复" in with_sub.prompt or "勿对" in with_sub.prompt
    print("[PASS] T-701: subagent: used + summary overlay; discipline hints when used")

    ask_overlay = Session(
        "_t702",
        paths.data / "sessions" / "_t702",
        "ask",
        SessionMeta(topics=[], llm_model="flash", updated_at=utc_now_iso(), phase="S4", turn_mode="ask"),
        [],
        paths,
    )
    ask_loaded = build_system_prompt(ask_overlay)
    assert "turn_mode: ask" in ask_loaded.prompt
    assert "run_evolved 已禁用" in ask_loaded.prompt
    assert "tool_budget: ask" in ask_loaded.prompt
    agent_overlay = Session(
        "_t907",
        paths.data / "sessions" / "_t907",
        "agent budget",
        SessionMeta(topics=[], llm_model="flash", updated_at=utc_now_iso(), phase="S4", turn_mode="agent"),
        [],
        paths,
    )
    agent_loaded = build_system_prompt(agent_overlay)
    assert "tool_budget: agent" in agent_loaded.prompt
    print("[PASS] T-702/T-907: overlay turn_mode + tool_budget hints")

    from turn_intent import classify_turn, should_spawn_explore

    assert classify_turn("帮我列计划") == "plan"
    assert not should_spawn_explore("帮我列计划")
    intent_session = Session(
        "_t703",
        paths.data / "sessions" / "_t703",
        "intent",
        SessionMeta(topics=[], llm_model="flash", updated_at=utc_now_iso(), phase="S4"),
        [],
        paths,
    )
    intent_session.turn_intent = "execute"
    intent_loaded = build_system_prompt(intent_session)
    assert "turn_intent: execute" in intent_loaded.prompt
    print("[PASS] T-703: turn_intent in session overlay + plan skips explore")

    pause_msg = format_segment_pause_message(segment=1, total_tool_rounds=10, auto_continue=False)
    assert "已完成部分" in pause_msg
    assert "继续" in pause_msg
    total_msg = format_total_cap_message(total_tool_rounds=50, total_max=50)
    assert "工具预算已用尽" in total_msg
    cancel_msg = format_tool_interrupt_notice("cancelled", tool_label="run_command")
    assert "不是工具回合上限" in cancel_msg
    timeout_msg = format_tool_interrupt_notice("timeout", tool_label="run_command")
    assert "墙钟" in timeout_msg
    assert "不是你点了" in timeout_msg
    print("[PASS] T-705: segment pause + total cap + interrupt notices")

    assert detect_scaffold_tool_turn("build a parser tool for evolve")
    assert not detect_scaffold_tool_turn("what tools are available?")
    assert "_staging.toml" in format_write_evolve_cookbook(scaffold_turn=True)
    assert "_staging.toml" not in format_write_evolve_cookbook(scaffold_turn=False)
    print("[PASS] P2: scaffold detect EN + cookbook staging only on scaffold turn")


if __name__ == "__main__":
    _demo()
