"""Task-scoped Skill catalog, on-demand body loading, and prompt sections.

Runtime rules (my-agent evolve/skills only — not Cursor .cursor/skills):
- A lightweight catalog is always available when skills exist.
- Full SKILL.md bodies load on demand, capped per task, reused until task reset.
- Explicit user skill requests must load before the turn proceeds.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal

from skill_registry import SkillRegistry, SkillRegistryError, SkillSelection
from turn_intent import is_reply_only_turn

SkillScope = Literal["agent", "worker"]

MAX_SKILL_BODIES_PER_TASK = 3
MAX_AUTO_LOAD_PER_TURN = 2
MAX_SKILL_TOTAL_BODY_CHARS = 32_000

_TASK_RESET_MARKERS = (
    "新任务",
    "另一个任务",
    "换个话题",
    "重新开始",
    "重新来",
    "new task",
    "different task",
    "switch topic",
)

_CONTINUATION_EXACT = frozenset(
    {
        "可以",
        "继续",
        "好的",
        "好",
        "嗯",
        "ok",
        "okay",
        "yes",
        "y",
        "proceed",
        "go on",
        "继续吧",
        "可以的",
        "行",
        "嗯嗯",
    }
)

_EXPLICIT_SKILL_PATTERNS = (
    re.compile(
        r"(?:使用|用|按|遵循|走|加载|应用)\s*(?:skill\s*)?([a-z][a-z0-9-]{1,63})\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bskill\s*[:：]\s*([a-z][a-z0-9-]{1,63})\b", re.IGNORECASE),
    re.compile(r"\buse\s+skill\s+([a-z][a-z0-9-]{1,63})\b", re.IGNORECASE),
)


@dataclass(slots=True)
class LoadedSkillEntry:
    selection: SkillSelection
    loaded_hash: str


@dataclass(slots=True)
class SkillRuntimeState:
    """In-memory per conversation (agent) or per worker run (worker). Never serialized."""

    scope: SkillScope
    task_id: str
    task_anchor: str
    task_binding: str
    loaded: dict[str, LoadedSkillEntry] = field(default_factory=dict)
    explicit_required: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class SkillIndexCacheEntry:
    signature: str
    registry: SkillRegistry


_INDEX_CACHE: dict[str, SkillIndexCacheEntry] = {}


def _skills_root_key(root) -> str:
    from pathlib import Path

    return str(Path(root).resolve())


def _index_signature(root) -> str:
    from pathlib import Path

    root = Path(root)
    if not root.is_dir():
        return "missing"
    parts: list[str] = []
    for skill_dir in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if not skill_dir.is_dir():
            continue
        path = skill_dir / "SKILL.md"
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            parts.append(f"{skill_dir.name}:missing")
            continue
        parts.append(f"{skill_dir.name}:{stat.st_mtime_ns}:{stat.st_size}")
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest


def get_skill_registry(root) -> SkillRegistry:
    """Cached index-only registry; bodies load via ``SkillRegistry.load``."""
    from pathlib import Path

    resolved = Path(root).resolve()
    key = _skills_root_key(resolved)
    signature = _index_signature(resolved)
    cached = _INDEX_CACHE.get(key)
    if cached is not None and cached.signature == signature:
        return cached.registry
    registry = SkillRegistry.discover_index(resolved)
    _INDEX_CACHE[key] = SkillIndexCacheEntry(signature=signature, registry=registry)
    return registry


def invalidate_skill_index_cache(root) -> None:
    _INDEX_CACHE.pop(_skills_root_key(root), None)


def is_skill_task_continuation(user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text:
        return True
    if is_reply_only_turn(text):
        return True
    folded = text.casefold()
    if folded in {item.casefold() for item in _CONTINUATION_EXACT}:
        return True
    if len(text) <= 20 and folded in {m.casefold() for m in _CONTINUATION_EXACT}:
        return True
    return False


def is_skill_task_reset(user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False
    folded = text.casefold()
    return any(marker.casefold() in folded for marker in _TASK_RESET_MARKERS)


def normalize_task_anchor(user_text: str) -> str:
    text = re.sub(r"\s+", " ", (user_text or "").strip())
    if len(text) > 240:
        text = text[:240]
    return text


def resolve_task_binding(session, scope: SkillScope, worker_key: str) -> str:
    if scope == "worker":
        return worker_key or "worker"
    if session is not None:
        active = (getattr(getattr(session, "meta", None), "project_active_task_id", "") or "").strip()
        if active:
            return f"project_active_task:{active}"
        conversation_id = (getattr(session, "conversation_id", "") or "").strip()
        if conversation_id:
            return f"conversation:{conversation_id}"
    return "conversation:unknown"


def _task_id_for_binding(binding: str, anchor: str) -> str:
    material = f"{binding}\n{anchor}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:16]


def _descriptors_for_scope(registry: SkillRegistry, scope: SkillScope):
    if scope == "agent":
        return registry.skills
    allowed = {"worker", "project"}
    return tuple(
        descriptor
        for descriptor in registry.skills
        if allowed.intersection(a.casefold() for a in descriptor.audiences)
    )


def _total_loaded_body_chars(state: SkillRuntimeState) -> int:
    return sum(len(entry.selection.package.body) for entry in state.loaded.values())


def parse_explicit_skill_names(user_text: str) -> tuple[tuple[str, ...], str | None]:
    """Return unique explicit skill ids, or an error message for ambiguity/invalid tokens."""
    text = user_text or ""
    found: list[str] = []
    for pattern in _EXPLICIT_SKILL_PATTERNS:
        for match in pattern.finditer(text):
            name = match.group(1).casefold()
            if name not in found:
                found.append(name)
    if len(found) > MAX_SKILL_BODIES_PER_TASK:
        return (), f"一次最多显式加载 {MAX_SKILL_BODIES_PER_TASK} 个 Skill，请分批说明。"
    return tuple(found), None


def _load_selection(registry: SkillRegistry, name: str, score: int = 0) -> SkillSelection:
    package = registry.load(name)
    return SkillSelection(descriptor=package.descriptor, package=package, score=score)


def _refresh_loaded(registry: SkillRegistry, state: SkillRuntimeState) -> tuple[str, ...]:
    """Drop entries whose disk content changed or skill removed. Returns dropped names."""
    dropped: list[str] = []
    for name, entry in list(state.loaded.items()):
        try:
            package = registry.load(name)
        except SkillRegistryError:
            dropped.append(name)
            state.loaded.pop(name, None)
            continue
        if package.descriptor.content_hash != entry.loaded_hash:
            dropped.append(name)
            state.loaded.pop(name, None)
    return tuple(dropped)


def _auto_select_names(
    registry: SkillRegistry,
    text: str,
    *,
    limit: int,
    scope: SkillScope,
) -> tuple[str, ...]:
    folded = (text or "").casefold()
    scored: list[tuple[int, str]] = []
    for descriptor in _descriptors_for_scope(registry, scope):
        score = SkillRegistry._score(descriptor, folded)
        if score:
            scored.append((score, descriptor.name))
    if not scored:
        return ()
    scored.sort(key=lambda item: (-item[0], item[1]))
    if limit == 1 and len(scored) > 1 and scored[0][0] == scored[1][0]:
        return ()
    top = scored[0][0]
    names: list[str] = []
    for score, name in scored:
        if score < top:
            break
        names.append(name)
        if len(names) >= limit:
            break
    return tuple(names)


def _can_add_body(state: SkillRuntimeState, extra_chars: int) -> bool:
    return _total_loaded_body_chars(state) + extra_chars <= MAX_SKILL_TOTAL_BODY_CHARS


def prepare_skill_turn(
    *,
    state: SkillRuntimeState | None,
    user_text: str,
    skills_root,
    scope: SkillScope,
    worker_key: str = "",
    session=None,
) -> tuple[SkillRuntimeState, str | None]:
    """Advance runtime state for one turn. Returns (state, block_message)."""
    registry = get_skill_registry(skills_root)
    explicit_names, explicit_err = parse_explicit_skill_names(user_text)
    if explicit_err:
        binding = resolve_task_binding(session, scope, worker_key)
        return state or _fresh_state(scope, user_text, binding=binding), explicit_err

    binding = resolve_task_binding(session, scope, worker_key)
    new_task = state is None or is_skill_task_reset(user_text)
    if state is not None and state.task_binding != binding:
        new_task = True

    if new_task or state is None:
        anchor = normalize_task_anchor(user_text) or user_text.strip()[:64] or "task"
        state = SkillRuntimeState(
            scope=scope,
            task_id=_task_id_for_binding(binding, anchor),
            task_anchor=anchor,
            task_binding=binding,
        )

    _refresh_loaded(registry, state)

    matching_text = f"{state.task_anchor}\n{user_text}".strip()
    load_errors: list[str] = []

    for name in explicit_names:
        canonical = name
        matches = [d.name for d in _descriptors_for_scope(registry, scope) if d.name.casefold() == canonical]
        if not matches:
            load_errors.append(f"未找到 Skill「{name}」。")
            continue
        if len(matches) > 1:
            load_errors.append(f"Skill 名称「{name}」存在歧义。")
            continue
        resolved = matches[0]
        try:
            selection = _load_selection(registry, resolved, score=100)
        except SkillRegistryError as exc:
            load_errors.append(f"无法加载 Skill「{resolved}」：{exc}")
            continue
        if not _can_add_body(state, len(selection.package.body)) and resolved not in state.loaded:
            load_errors.append(f"Skill「{resolved}」正文超出本任务字符预算（{MAX_SKILL_TOTAL_BODY_CHARS}）。")
            continue
        if len(state.loaded) >= MAX_SKILL_BODIES_PER_TASK and resolved not in state.loaded:
            load_errors.append(
                f"本任务已加载 {MAX_SKILL_BODIES_PER_TASK} 个 Skill 正文，无法再加载「{resolved}」。"
            )
            continue
        state.loaded[resolved] = LoadedSkillEntry(
            selection=selection, loaded_hash=selection.descriptor.content_hash,
        )
        state.explicit_required = state.explicit_required | frozenset({resolved})

    if load_errors and explicit_names:
        return state, "；".join(load_errors) + " 未加载该 Skill，本回合不会按该 Skill 执行。"

    remaining_slots = MAX_SKILL_BODIES_PER_TASK - len(state.loaded)
    auto_budget = min(MAX_AUTO_LOAD_PER_TURN, remaining_slots)
    if auto_budget > 0 and not explicit_names:
        for name in _auto_select_names(registry, matching_text, limit=auto_budget, scope=scope):
            if name in state.loaded:
                continue
            try:
                selection = _load_selection(registry, name)
            except SkillRegistryError:
                continue
            if not _can_add_body(state, len(selection.package.body)):
                break
            state.loaded[name] = LoadedSkillEntry(
                selection=selection, loaded_hash=selection.descriptor.content_hash,
            )
            if len(state.loaded) >= MAX_SKILL_BODIES_PER_TASK:
                break

    return state, None


def _fresh_state(scope: SkillScope, user_text: str, *, binding: str) -> SkillRuntimeState:
    anchor = normalize_task_anchor(user_text) or "task"
    return SkillRuntimeState(
        scope=scope,
        task_id=_task_id_for_binding(binding, anchor),
        task_anchor=anchor,
        task_binding=binding,
    )


def format_skill_catalog(registry: SkillRegistry, *, scope: SkillScope = "agent") -> str:
    descriptors = _descriptors_for_scope(registry, scope)
    if not descriptors:
        return "[Skill 目录]\n当前没有可用的内置 Skill。"
    lines = [
        "[Skill 目录]（常驻轻量索引；正文按任务按需加载，不自动执行 Skill 脚本）",
    ]
    if scope == "agent":
        lines.append(
            "加载正文：调用工具 load_workflow_skill（参数 skill_id）；或让用户明确说「使用 skill <名称>」。"
        )
    else:
        lines.append(
            "Worker 仅展示 audiences 含 worker/project 的 Skill；正文由任务文本自动匹配，无额外工具。"
        )
    for descriptor in descriptors:
        triggers = "、".join(descriptor.triggers[:8]) or "(无触发词)"
        if len(descriptor.triggers) > 8:
            triggers += "…"
        description = descriptor.description[:200]
        lines.append(
            f"- {descriptor.name} v{descriptor.version} [{descriptor.status}]: {description}"
        )
        lines.append(f"  触发/提及: {triggers}")
    if registry.diagnostics:
        lines.append(f"（{len(registry.diagnostics)} 个 Skill 包未纳入目录，见运行诊断。）")
    return "\n".join(lines)


def format_loaded_skill_context(
    registry: SkillRegistry,
    state: SkillRuntimeState | None,
) -> str:
    if state is None or not state.loaded:
        return ""
    _refresh_loaded(registry, state)
    blocks: list[str] = []
    for name in sorted(state.loaded):
        entry = state.loaded.get(name)
        if entry is None:
            continue
        blocks.append(registry.format_selection_context(entry.selection))
    if not blocks:
        return ""
    header = (
        f"[Skill 已加载正文 · 任务 {state.task_id} · {len(blocks)}/{MAX_SKILL_BODIES_PER_TASK}]\n"
        "allowed_tools 仅为提示，不授予工具权限。\n"
    )
    return header + "\n\n".join(blocks)


def build_skill_prompt_sections(session, skills_root) -> tuple[str, str]:
    """Return (catalog, loaded_context) for loader overlay."""
    registry = get_skill_registry(skills_root)
    state = getattr(session, "_skill_runtime", None)
    scope = getattr(state, "scope", None) or "agent"
    catalog = format_skill_catalog(registry, scope=scope)
    context = format_loaded_skill_context(registry, state)
    return catalog, context


def begin_agent_skill_turn(session, user_text: str) -> str | None:
    skills_root = session.paths.evolve / "skills"
    state = getattr(session, "_skill_runtime", None)
    new_state, block = prepare_skill_turn(
        state=state,
        user_text=user_text,
        skills_root=skills_root,
        scope="agent",
        session=session,
    )
    session._skill_runtime = new_state
    return block


def agent_tool_load_workflow_skill(paths, session, name: object):
    from tools.schema import tool_fail, tool_ok

    if session is None:
        return tool_fail("load_workflow_skill", "unavailable", "当前 Agent 会话未绑定，无法加载 Skill。")
    if not isinstance(name, str) or not name.strip():
        return tool_fail("load_workflow_skill", "invalid_arguments", "请提供 skill_id（evolve/skills 下的目录名）。")
    skills_root = paths.evolve / "skills"
    state = getattr(session, "_skill_runtime", None)
    binding = resolve_task_binding(session, "agent", "")
    if state is None:
        state = _fresh_state("agent", "", binding=binding)
    registry = get_skill_registry(skills_root)
    resolved_matches = [d.name for d in registry.skills if d.name.casefold() == name.strip().casefold()]
    if not resolved_matches:
        return tool_fail("load_workflow_skill", "not_found", f"未找到 Skill「{name.strip()}」。")
    if len(resolved_matches) > 1:
        return tool_fail("load_workflow_skill", "ambiguous", f"Skill 名称「{name.strip()}」存在歧义。")
    resolved = resolved_matches[0]
    try:
        selection = _load_selection(registry, resolved)
    except SkillRegistryError as exc:
        return tool_fail("load_workflow_skill", "invalid_skill", f"无法加载 Skill「{resolved}」：{exc}")
    if len(state.loaded) >= MAX_SKILL_BODIES_PER_TASK and resolved not in state.loaded:
        return tool_fail(
            "load_workflow_skill",
            "budget_exceeded",
            f"本任务已加载 {MAX_SKILL_BODIES_PER_TASK} 个 Skill 正文。",
        )
    if not _can_add_body(state, len(selection.package.body)) and resolved not in state.loaded:
        return tool_fail(
            "load_workflow_skill",
            "budget_exceeded",
            f"Skill 正文超过本任务字符预算（{MAX_SKILL_TOTAL_BODY_CHARS}）。",
        )
    state.loaded[resolved] = LoadedSkillEntry(
        selection=selection,
        loaded_hash=selection.descriptor.content_hash,
    )
    session._skill_runtime = state
    return tool_ok(
        "load_workflow_skill",
        {
            "skill_id": resolved,
            "message": (
                f"已加载 Skill「{resolved}」到本任务 prompt；"
                "allowed_tools 仅为提示，不授予工具权限。"
            ),
        },
    )


def build_worker_skill_prompt_suffix(skills_root, task: str, project_id: str) -> str:
    worker_key = f"worker:{project_id}"
    state, block = prepare_skill_turn(
        state=None,
        user_text=task,
        skills_root=skills_root,
        scope="worker",
        worker_key=worker_key,
        session=None,
    )
    if block:
        return f"\n[Skill 加载失败] {block}"
    registry = get_skill_registry(skills_root)
    catalog = format_skill_catalog(registry, scope="worker")
    state = state or _fresh_state("worker", task, binding=worker_key)
    context = format_loaded_skill_context(registry, state)
    parts = [catalog]
    if context:
        parts.append(context)
    return "\n\n" + "\n\n".join(parts)


def clear_skill_index_cache_for_tests() -> None:
    _INDEX_CACHE.clear()