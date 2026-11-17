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
_WORKER_SKILL_STATES: dict[tuple[str, str], SkillRuntimeState] = {}


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
    """Stable durable bindings only (work-record item/requirement or host task_id).

    Intentionally excludes parent ``run_id`` / ``host_run`` so short continuations do
  not reset skill task state every provider turn.
    """
    if scope == "worker":
        return worker_key or "worker"
    if session is not None:
        meta = getattr(session, "meta", None)
        item_id = (getattr(meta, "work_record_item_id", "") or "").strip()
        requirement_id = (getattr(meta, "work_record_requirement_id", "") or "").strip()
        if item_id and requirement_id:
            return f"work_record:{item_id}:{requirement_id}"
        active = (getattr(meta, "project_active_task_id", "") or "").strip()
        if active:
            return f"task:{active}"
    return ""


def _conversation_epoch_binding(session, *, new_task: bool, state: SkillRuntimeState | None) -> str:
    if session is None:
        return "conversation_epoch:1"
    epoch = int(getattr(session, "_skill_conversation_epoch", 0) or 0)
    if epoch <= 0:
        epoch = 1
        session._skill_conversation_epoch = epoch
    elif new_task and state is not None:
        epoch += 1
        session._skill_conversation_epoch = epoch
    return f"conversation_epoch:{epoch}"


def _resolve_binding_for_turn(
    session,
    scope: SkillScope,
    worker_key: str,
    *,
    new_task: bool,
    state: SkillRuntimeState | None,
) -> str:
    durable = resolve_task_binding(session, scope, worker_key)
    if durable:
        return durable
    return _conversation_epoch_binding(session, new_task=new_task, state=state)


def _task_id_for_binding(binding: str, anchor: str) -> str:
    material = f"{binding}\n{anchor}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:16]


def _descriptors_for_scope(
    registry: SkillRegistry,
    scope: SkillScope,
    *,
    authorized_skill_ids: frozenset[str] | None = None,
):
    if scope == "agent":
        return registry.skills
    if authorized_skill_ids is not None:
        return tuple(
            descriptor
            for descriptor in registry.skills
            if descriptor.skill_id in authorized_skill_ids
        )
    return ()


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


def _load_selection(registry: SkillRegistry, skill_id: str, score: int = 0) -> SkillSelection:
    package = registry.load(skill_id)
    return SkillSelection(descriptor=package.descriptor, package=package, score=score)


def _refresh_loaded(registry: SkillRegistry, state: SkillRuntimeState) -> tuple[str, ...]:
    """Drop entries whose disk content changed or skill removed. Returns dropped skill_ids."""
    dropped: list[str] = []
    for skill_id, entry in list(state.loaded.items()):
        try:
            package = registry.load(skill_id)
        except SkillRegistryError:
            dropped.append(skill_id)
            state.loaded.pop(skill_id, None)
            continue
        if package.descriptor.content_hash != entry.loaded_hash:
            dropped.append(skill_id)
            state.loaded.pop(skill_id, None)
    return tuple(dropped)


def _block_stale_explicit_skills(
    state: SkillRuntimeState,
    *,
    explicit_this_turn: tuple[str, ...],
) -> str | None:
    missing = state.explicit_required - frozenset(state.loaded.keys())
    if not missing:
        return None
    if explicit_this_turn:
        return None
    labels = ", ".join(sorted(missing))
    return f"显式依赖的 Skill（{labels}）已失效（文件变更、删除或不可用），本回合阻断。"


def _auto_select_names(
    registry: SkillRegistry,
    text: str,
    *,
    limit: int,
    scope: SkillScope,
    authorized_skill_ids: frozenset[str] | None = None,
) -> tuple[str, ...]:
    folded = (text or "").casefold()
    scored: list[tuple[int, str]] = []
    for descriptor in _descriptors_for_scope(
        registry, scope, authorized_skill_ids=authorized_skill_ids
    ):
        score = SkillRegistry._score(descriptor, folded)
        if score:
            scored.append((score, descriptor.skill_id))
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
    authorized_skill_ids: frozenset[str] | None = None,
) -> tuple[SkillRuntimeState, str | None]:
    """Advance runtime state for one turn. Returns (state, block_message)."""
    registry = get_skill_registry(skills_root)
    explicit_names, explicit_err = parse_explicit_skill_names(user_text)
    if explicit_err:
        binding = resolve_task_binding(session, scope, worker_key)
        return state or _fresh_state(scope, user_text, binding=binding), explicit_err

    new_task = state is None or is_skill_task_reset(user_text)
    durable = resolve_task_binding(session, scope, worker_key)
    if state is not None and durable and state.task_binding != durable:
        new_task = True
    binding = _resolve_binding_for_turn(
        session, scope, worker_key, new_task=new_task, state=state,
    )
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
    stale_block = _block_stale_explicit_skills(state, explicit_this_turn=explicit_names)
    if stale_block:
        return state, stale_block

    matching_text = f"{state.task_anchor}\n{user_text}".strip()
    load_errors: list[str] = []

    for ref in explicit_names:
        try:
            skill_id = registry.resolve_skill_id(ref)
        except SkillRegistryError as exc:
            load_errors.append(f"无法解析 Skill「{ref}」：{exc}")
            continue
        if scope == "worker" and authorized_skill_ids is not None and skill_id not in authorized_skill_ids:
            load_errors.append(f"Skill「{ref}」未获本任务授权（skill_id 不在 granted_skill_ids）。")
            continue
        try:
            selection = _load_selection(registry, skill_id, score=100)
        except SkillRegistryError as exc:
            load_errors.append(f"无法加载 Skill「{skill_id}」：{exc}")
            state.loaded.pop(skill_id, None)
            state.explicit_required = state.explicit_required - frozenset({skill_id})
            continue
        if not _can_add_body(state, len(selection.package.body)) and skill_id not in state.loaded:
            load_errors.append(f"Skill「{skill_id}」正文超出本任务字符预算（{MAX_SKILL_TOTAL_BODY_CHARS}）。")
            continue
        if len(state.loaded) >= MAX_SKILL_BODIES_PER_TASK and skill_id not in state.loaded:
            load_errors.append(
                f"本任务已加载 {MAX_SKILL_BODIES_PER_TASK} 个 Skill 正文，无法再加载「{skill_id}」。"
            )
            continue
        state.loaded[skill_id] = LoadedSkillEntry(
            selection=selection, loaded_hash=selection.descriptor.content_hash,
        )
        state.explicit_required = state.explicit_required | frozenset({skill_id})

    if load_errors and explicit_names:
        for ref in explicit_names:
            try:
                skill_id = registry.resolve_skill_id(ref)
            except SkillRegistryError:
                continue
            state.loaded.pop(skill_id, None)
            state.explicit_required = state.explicit_required - frozenset({skill_id})
        return state, "；".join(load_errors) + " 未加载该 Skill，本回合不会按该 Skill 执行。"

    remaining_slots = MAX_SKILL_BODIES_PER_TASK - len(state.loaded)
    auto_budget = min(MAX_AUTO_LOAD_PER_TURN, remaining_slots)
    if auto_budget > 0 and not explicit_names:
        for name in _auto_select_names(
            registry,
            matching_text,
            limit=auto_budget,
            scope=scope,
            authorized_skill_ids=authorized_skill_ids,
        ):
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
            "加载正文：调用工具 load_workflow_skill（参数 skill_id，目录名）；"
            "display name 有歧义时必须用 skill_id。allowed_tools 不授予权限。"
        )
    for descriptor in descriptors:
        triggers = "、".join(descriptor.triggers[:8]) or "(无触发词)"
        if len(descriptor.triggers) > 8:
            triggers += "…"
        description = descriptor.description[:200]
        id_note = f" id={descriptor.skill_id}" if descriptor.skill_id != descriptor.name else ""
        lines.append(
            f"- {descriptor.name}{id_note} v{descriptor.version} [{descriptor.status}]: {description}"
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
    try:
        skill_id = registry.resolve_skill_id(str(name).strip())
    except SkillRegistryError as exc:
        code = "ambiguous" if "ambiguous" in str(exc) else "not_found"
        return tool_fail("load_workflow_skill", code, str(exc))
    try:
        selection = _load_selection(registry, skill_id)
    except SkillRegistryError as exc:
        return tool_fail("load_workflow_skill", "invalid_skill", f"无法加载 Skill「{skill_id}」：{exc}")
    if len(state.loaded) >= MAX_SKILL_BODIES_PER_TASK and skill_id not in state.loaded:
        return tool_fail(
            "load_workflow_skill",
            "budget_exceeded",
            f"本任务已加载 {MAX_SKILL_BODIES_PER_TASK} 个 Skill 正文。",
        )
    if not _can_add_body(state, len(selection.package.body)) and skill_id not in state.loaded:
        return tool_fail(
            "load_workflow_skill",
            "budget_exceeded",
            f"Skill 正文超过本任务字符预算（{MAX_SKILL_TOTAL_BODY_CHARS}）。",
        )
    state.loaded[skill_id] = LoadedSkillEntry(
        selection=selection,
        loaded_hash=selection.descriptor.content_hash,
    )
    session._skill_runtime = state
    return tool_ok(
        "load_workflow_skill",
        {
            "skill_id": skill_id,
            "display_name": selection.descriptor.name,
            "message": (
                f"已加载 Skill id={skill_id}（{selection.descriptor.name}）到本任务 prompt；"
                "allowed_tools 仅为提示，不授予工具权限。"
            ),
        },
    )


def format_worker_authorized_catalog(
    registry: SkillRegistry,
    authorized_skill_ids: tuple[str, ...],
) -> str:
    if not authorized_skill_ids:
        return (
            "[Skill 授权目录] 本任务未授予任何 workflow skill_id；"
            "不暴露 evolve/skills 全局目录。"
        )
    allowed = frozenset(authorized_skill_ids)
    lines = [
        "[Skill 授权目录] 仅列出 host 授予的 skill_id；调用 load_worker_workflow_skill 加载正文。",
        "allowed_tools 仅为提示，不授予工具权限。",
    ]
    for descriptor in registry.skills:
        if descriptor.skill_id not in allowed:
            continue
        description = descriptor.description[:160]
        lines.append(f"- {descriptor.skill_id} ({descriptor.name}): {description}")
    return "\n".join(lines)


def worker_skill_state_key(conversation_id: str, worker_task_id: str) -> tuple[str, str]:
    return (conversation_id, worker_task_id)


def get_worker_skill_state(conversation_id: str, worker_task_id: str) -> SkillRuntimeState | None:
    return _WORKER_SKILL_STATES.get(worker_skill_state_key(conversation_id, worker_task_id))


def store_worker_skill_state(conversation_id: str, worker_task_id: str, state: SkillRuntimeState) -> None:
    _WORKER_SKILL_STATES[worker_skill_state_key(conversation_id, worker_task_id)] = state


def worker_tool_load_workflow_skill(
    *,
    skills_root,
    conversation_id: str,
    worker_task_id: str,
    project_id: str,
    authorized_skill_ids: tuple[str, ...],
    skill_ref: object,
    task_text: str = "",
) -> dict[str, object]:
    if type(skill_ref) is not str or not skill_ref.strip():
        return {"ok": False, "error": "invalid_arguments", "message": "skill_id required"}
    token = skill_ref.strip()
    if ".." in token or "/" in token or "\\" in token:
        return {"ok": False, "error": "path_denied", "message": "skill_id must be a registry id"}
    allowed = frozenset(authorized_skill_ids)
    registry = get_skill_registry(skills_root)
    if not allowed:
        return {"ok": False, "error": "skill_not_authorized", "message": "no granted_skill_ids for this task"}
    try:
        skill_id = registry.resolve_skill_id(token)
    except SkillRegistryError as exc:
        return {"ok": False, "error": "resolve_failed", "message": str(exc)}
    if skill_id not in allowed:
        return {"ok": False, "error": "skill_not_authorized", "skill_id": skill_id}
    worker_key = f"worker:{project_id}:{worker_task_id}"
    prior = get_worker_skill_state(conversation_id, worker_task_id)
    state, block = prepare_skill_turn(
        state=prior,
        user_text=f"load skill {skill_id}\n{task_text}".strip(),
        skills_root=skills_root,
        scope="worker",
        worker_key=worker_key,
        session=None,
        authorized_skill_ids=allowed,
    )
    if block:
        return {"ok": False, "error": "load_blocked", "message": block}
    try:
        selection = _load_selection(registry, skill_id)
    except SkillRegistryError as exc:
        return {"ok": False, "error": "invalid_skill", "message": str(exc)}
    if not _can_add_body(state, len(selection.package.body)) and skill_id not in state.loaded:
        return {
            "ok": False,
            "error": "budget_exceeded",
            "message": f"Skill 正文超过本任务字符预算（{MAX_SKILL_TOTAL_BODY_CHARS}）。",
        }
    if len(state.loaded) >= MAX_SKILL_BODIES_PER_TASK and skill_id not in state.loaded:
        return {
            "ok": False,
            "error": "budget_exceeded",
            "message": f"本任务已加载 {MAX_SKILL_BODIES_PER_TASK} 个 Skill 正文。",
        }
    state.loaded[skill_id] = LoadedSkillEntry(
        selection=selection,
        loaded_hash=selection.descriptor.content_hash,
    )
    store_worker_skill_state(conversation_id, worker_task_id, state)
    body_preview = selection.package.body[:2000]
    context = format_loaded_skill_context(registry, state)
    return {
        "ok": True,
        "skill_id": skill_id,
        "display_name": selection.descriptor.name,
        "body_chars": len(selection.package.body),
        "body_preview": body_preview,
        "skill_context": context,
        "message": "Skill 正文已加载到本 worker 任务上下文；allowed_tools 仅为提示。",
    }


def build_worker_skill_prompt_suffix(
    skills_root,
    task: str,
    *,
    conversation_id: str,
    project_id: str,
    worker_task_id: str,
    authorized_skill_ids: tuple[str, ...] = (),
) -> str:
    worker_key = f"worker:{project_id}:{worker_task_id}"
    allowed = frozenset(authorized_skill_ids)
    prior = get_worker_skill_state(conversation_id, worker_task_id)
    state, block = prepare_skill_turn(
        state=prior,
        user_text=task.strip(),
        skills_root=skills_root,
        scope="worker",
        worker_key=worker_key,
        session=None,
        authorized_skill_ids=allowed if allowed else frozenset(),
    )
    if block:
        return f"\n[Skill 加载失败] {block}"
    store_worker_skill_state(conversation_id, worker_task_id, state)
    registry = get_skill_registry(skills_root)
    parts = [format_worker_authorized_catalog(registry, authorized_skill_ids)]
    context = format_loaded_skill_context(registry, state)
    if context:
        parts.append(context)
    return "\n\n" + "\n\n".join(parts)


def clear_skill_index_cache_for_tests() -> None:
    _INDEX_CACHE.clear()


def clear_worker_skill_states_for_tests() -> None:
    _WORKER_SKILL_STATES.clear()