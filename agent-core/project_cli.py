"""REPL commands for workspace project mode (PROJECT-MODE T-1103, T-1110)."""

from __future__ import annotations

import shlex
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from project_mode import (
    ProjectModeError,
    acceptance_script_exists,
    build_project_goal,
    list_projects,
    normalize_project_id,
    parse_acceptance_spec,
    plan_allows_code_writes,
    project_dir,
    project_root_rel,
    read_task_stats,
    run_acceptance_check,
    snapshot_plan_fingerprints,
    organize_project_documents,
    documentation_ready_for_design,
    read_project_template,
    upgrade_project_to_standard,
    utc_now_iso,
)
from session import Session, utc_now_iso

ProjectCommandKind = Literal[
    "list",
    "new",
    "open",
    "switch",
    "new_thread",
    "organize",
    "confirm_design",
    "start_task",
    "confirm",
    "direct_implement",
    "status",
    "verify",
    "discipline",
    "upgrade",
]
InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]

_PREFIXES = ("项目", "project")


@dataclass(frozen=True, slots=True)
class ProjectCommandResult:
    meta_changed: bool = False
    session: Session | None = None


@dataclass(frozen=True, slots=True)
class ParsedProjectCommand:
    kind: ProjectCommandKind
    project_id: str | None = None
    project_template: str = "standard"


class ProjectCommandError(Exception):
    """Invalid project REPL command."""


_SHORT_PLAN_CONFIRM = frozenset(
    {
        "确认",
        "确认开工",
        "确认计划",
        "开工",
        "开始",
        "开始吧",
        "可以开工",
        "好的",
        "好的确认",
    }
)

_SHORT_DIRECT_IMPLEMENT = frozenset(
    {
        "直接实现",
        "直接开工",
        "direct-implement",
        "implement-now",
        "directimplement",
        "implementnow",
    }
)

_PROJECT_VERBS = frozenset(
    {
        "列表",
        "list",
        "新建",
        "new",
        "create",
        "打开",
        "open",
        "切换",
        "switch",
        "新开线",
        "new-thread",
        "newthread",
        "thread",
        "确认",
        "整理",
        "文档",
        "整理文档",
        "开始整理文档",
        "确认设计",
        "设计确认",
        "开始任务",
        "直接实现",
        "直接开工",
        "direct-implement",
        "implement-now",
        "confirm",
        "状态",
        "status",
        "验收",
        "verify",
        "纪律",
        "discipline",
        "profile",
        "升档",
        "标准模板",
        "upgrade",
        "standard-template",
        "promote",
    }
)

_NATURAL_LANGUAGE_STARTERS = (
    "现在",
    "已经",
    "还是",
    "感觉",
    "觉得",
    "可能",
    "应该",
    "还没",
    "不能",
    "不太",
    "离",
    "这",
    "那",
    "我们",
    "你们",
    "其实",
    "但是",
    "不过",
    "所以",
    "如果",
    "虽然",
)


def _looks_like_project_natural_language(token: str) -> bool:
    """True when payload after 「项目」 is conversational, not a CLI verb."""
    text = (token or "").strip()
    if not text:
        return False
    if len(text) > 12:
        return True
    if any(ch in text for ch in "，。！？、；：,.!?;:"):
        return True
    return any(text.startswith(prefix) for prefix in _NATURAL_LANGUAGE_STARTERS)


def _plausible_project_id_token(text: str) -> bool:
    try:
        normalize_project_id(text)
    except ProjectModeError:
        return False
    return True


def try_short_plan_confirm(session: Session, text: str, output_fn: OutputFn) -> bool:
    """Map explicit project shortcuts without letting documentation start code work."""
    if session.meta.active_shell != "project":
        return False
    status = session.meta.project_plan_status or "draft"
    if status not in {"draft", "plan_dirty"}:
        return False
    normalized = text.strip().casefold().replace(" ", "")
    if normalized in {"整理文档", "开始整理文档"}:
        try:
            message = organize_project_plan(session, session.paths)
        except ProjectModeError as exc:
            output_fn(f"error: {exc}")
            return True
        session.save()
        output_fn(message)
        return True
    if normalized in _SHORT_DIRECT_IMPLEMENT:
        try:
            message = confirm_direct_implement(session)
        except ProjectModeError as exc:
            output_fn(f"error: {exc}")
            return True
        session.save()
        output_fn(message)
        return True
    if normalized not in _SHORT_PLAN_CONFIRM:
        return False
    try:
        message = confirm_project_plan(session)
    except ProjectModeError as exc:
        output_fn(f"error: {exc}")
        return True
    if not getattr(session.meta, "project_entry", ""):
        session.meta.project_entry = "plan"
    session.save()
    output_fn(message)
    return True


def parse_project_command(text: str) -> ParsedProjectCommand | None:
    stripped = text.strip()
    if not stripped:
        return None

    # Alias: 「新项目 <id>」 → 项目 新建（CONTEXT-SWITCH T-1905）
    for prefix in ("新项目", "new project"):
        if stripped.casefold().startswith(prefix.casefold()):
            rest = stripped[len(prefix) :].strip()
            if not rest:
                return None
            try:
                tokens = shlex.split(rest, posix=False)
            except ValueError as exc:
                raise ProjectCommandError(f"invalid project command: {exc}") from exc
            if tokens and _plausible_project_id_token(tokens[0]):
                return ParsedProjectCommand(
                    kind="new",
                    project_id=tokens[0],
                    project_template=_parse_new_project_template(tokens[1:]),
                )
            return None

    lowered = stripped.casefold()
    prefix_len: int | None = None
    for prefix in _PREFIXES:
        if stripped.startswith(prefix) or lowered.startswith(prefix):
            prefix_len = len(prefix)
            break
    if prefix_len is None:
        return None

    payload = stripped[prefix_len:].strip()
    if not payload or payload.casefold() in {"列表", "list"}:
        return ParsedProjectCommand(kind="list")

    try:
        tokens = shlex.split(payload, posix=False)
    except ValueError as exc:
        raise ProjectCommandError(f"invalid project command: {exc}") from exc

    if not tokens:
        return ParsedProjectCommand(kind="list")

    verb = tokens[0].casefold()
    if len(tokens) >= 2:
        combined = f"{tokens[0]}{tokens[1]}".casefold().replace("-", "").replace("_", "")
        if combined in {"直接实现", "直接开工", "directimplement", "implementnow"}:
            verb = "直接实现"
    if verb in {"标准", "standard"} and len(tokens) >= 2 and tokens[1].casefold() in {
        "模板",
        "template",
    }:
        verb = "标准模板"
    if verb in {"列表", "list"}:
        return ParsedProjectCommand(kind="list")
    if verb in {"新建", "new", "create"}:
        if len(tokens) < 2:
            raise ProjectCommandError("项目 新建 <id> [--light]")
        return ParsedProjectCommand(
            kind="new",
            project_id=tokens[1],
            project_template=_parse_new_project_template(tokens[2:]),
        )
    if verb in {"打开", "open"}:
        if len(tokens) < 2:
            raise ProjectCommandError("项目 打开 <id>")
        return ParsedProjectCommand(kind="open", project_id=tokens[1])
    if verb in {"切换", "switch"}:
        if len(tokens) < 2:
            raise ProjectCommandError("项目 切换 <id>")
        return ParsedProjectCommand(kind="switch", project_id=tokens[1])
    if verb in {"新开线", "new-thread", "newthread", "thread"}:
        if len(tokens) >= 2:
            return ParsedProjectCommand(kind="new_thread", project_id=tokens[1])
        return ParsedProjectCommand(kind="new_thread")
    if verb in {"确认", "confirm"}:
        return ParsedProjectCommand(kind="confirm")
    if verb in {"整理", "文档", "整理文档", "开始整理文档", "documentation", "organize"}:
        return ParsedProjectCommand(kind="organize")
    if verb in {"确认设计", "设计确认", "confirm-design", "design-confirm"}:
        return ParsedProjectCommand(kind="confirm_design")
    if verb in {"直接实现", "直接开工", "direct-implement", "implement-now"}:
        return ParsedProjectCommand(kind="direct_implement")
    if verb in {"开始任务", "启动任务", "start-task", "implement"}:
        if len(tokens) < 2:
            raise ProjectCommandError("项目 开始任务 <T-ID>")
        return ParsedProjectCommand(kind="start_task", project_id=tokens[1])
    if verb in {"状态", "status"}:
        return ParsedProjectCommand(kind="status")
    if verb in {"验收", "verify"}:
        return ParsedProjectCommand(kind="verify")
    if verb in {"纪律", "discipline", "profile"}:
        if len(tokens) < 2:
            raise ProjectCommandError("项目 纪律 solo|ritual|strict|宽松")
        return ParsedProjectCommand(kind="discipline", project_id=tokens[1])
    if verb in {"升档", "标准模板", "upgrade", "standard-template", "promote"}:
        return ParsedProjectCommand(kind="upgrade")

    if verb not in _PROJECT_VERBS and _looks_like_project_natural_language(tokens[0]):
        return None

    # Unknown verb: pass through to main chat instead of blocking the turn.
    # Desktop ordinary M3 sends「项目 直接实现」here. Until M1 registers that
    # verb (project_entry=direct), the line hits is_direct_implement_request.
    return None


def _parse_new_project_template(tokens: list[str]) -> str:
    """Parse leftover ``项目 新建 <id>`` tokens into ``light`` | ``standard``."""
    from project_mode import DEFAULT_PROJECT_TEMPLATE, normalize_project_template

    selected = DEFAULT_PROJECT_TEMPLATE
    skip_next = False
    for index, raw in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        folded = raw.strip().casefold()
        bare = folded.lstrip("-")
        if bare in {"light", "lite"} or folded == "--light":
            selected = "light"
            continue
        if bare in {"standard", "full"} or folded == "--standard":
            selected = "standard"
            continue
        if folded in {"--template", "-t", "template"}:
            if index + 1 >= len(tokens):
                raise ProjectCommandError("项目 新建 <id> --template light|standard")
            skip_next = True
            selected = _coerce_project_template_token(tokens[index + 1])
            continue
        if folded.startswith("--template="):
            selected = _coerce_project_template_token(folded.split("=", 1)[1])
            continue
    return normalize_project_template(selected)


def _coerce_project_template_token(raw: str) -> str:
    value = raw.strip().casefold().lstrip("-")
    if value in {"light", "lite", "standard", "full"}:
        from project_mode import normalize_project_template

        return normalize_project_template(value)
    raise ProjectCommandError("项目模板须为 light 或 standard")


def _ensure_coding_topic(session: Session) -> None:
    from router import TopicRoutingError, apply_confirmed_topics, registered_topic_ids

    if "coding" in session.meta.topics:
        return
    try:
        apply_confirmed_topics(
            session,
            ["coding"],
            mode="append",
            valid_topic_ids=registered_topic_ids(session.paths),
        )
    except TopicRoutingError:
        pass


def bind_project_session(
    session: Session,
    project_id: str,
    *,
    plan_status: str = "draft",
) -> str:
    pid = normalize_project_id(project_id)
    prev_pid = (session.meta.project_id or "").strip()
    root = project_root_rel(pid)
    if pid != prev_pid:
        from project_mode import DEFAULT_PROJECT_DELIVERY_PROFILE

        session.meta.project_delivery_profile = DEFAULT_PROJECT_DELIVERY_PROFILE
        session.meta.project_runaway_enabled = False
        session.meta.project_entry = ""
    session.meta.active_shell = "project"
    session.meta.project_id = pid
    session.meta.project_root = root
    session.meta.project_plan_status = plan_status
    if plan_status == "confirmed":
        session.meta.project_workflow_stage = "implementation"
    else:
        core_docs = [project_dir(session.paths, pid) / name for name in ("PROJECT.md", "DESIGN.md", "TASKS.md", "VERIFY.md")]
        session.meta.project_workflow_stage = (
            "documentation"
            if all(path.is_file() and "待填写" not in path.read_text(encoding="utf-8") for path in core_docs)
            else "requirements"
        )
    if plan_status == "confirmed":
        session.meta.project_plan_confirmed_at = utc_now_iso()
    session.set_goal(build_project_goal(project_root=root, plan_status=plan_status), phase="S4")
    _ensure_coding_topic(session)
    session.meta.updated_at = utc_now_iso()
    from project_switch import record_project_session

    record_project_session(session.paths, pid, session.conversation_id)
    return root


def confirm_direct_implement(session: Session) -> str:
    """Ordinary-mode explicit entry: persist ``project_entry=direct`` and open the plan gate."""
    if session.meta.active_shell != "project":
        raise ProjectModeError("当前不在项目壳；「项目 直接实现」仅在项目会话可用。")
    if bool(getattr(session.meta, "project_runaway_enabled", False)):
        raise ProjectModeError("狂奔模式已开启；「项目 直接实现」仅用于普通模式。")
    root = (session.meta.project_root or "").strip()
    pid = (session.meta.project_id or "").strip()
    if not root or not pid:
        raise ProjectModeError("当前会话未打开项目；先「项目 打开 <id>」")
    status = str(session.meta.project_plan_status or "draft")
    if status in {"draft", "plan_dirty"}:
        message = confirm_project_plan(session, skip_stage_walls=True)
        session.meta.project_entry = "direct"
        return f"已选择直接实现入口（project_entry=direct）。{message}"
    session.meta.project_entry = "direct"
    return "已选择直接实现入口（project_entry=direct）。计划已确认，继续写代码。"


def confirm_project_plan(session: Session, *, skip_stage_walls: bool = False) -> str:
    root = (session.meta.project_root or "").strip()
    pid = (session.meta.project_id or "").strip()
    if not root or not pid or session.meta.active_shell != "project":
        raise ProjectModeError("当前会话未打开项目；先「项目 打开 <id>」")
    workflow_stage = getattr(session.meta, "project_workflow_stage", "requirements")
    if not skip_stage_walls:
        if workflow_stage == "documentation":
            raise ProjectModeError("文档整理尚未完成设计确认；当前不能开始写代码")
        if workflow_stage == "design":
            raise ProjectModeError("设计已确认；请使用「项目 开始任务 <T-ID>」授权实现批次")

    tasks_path = project_dir(session.paths, pid) / "TASKS.md"
    if not tasks_path.is_file():
        raise ProjectModeError(f"缺少 {root}/TASKS.md；请先让助手生成计划")

    session.meta.project_plan_status = "confirmed"
    session.meta.project_workflow_stage = "implementation"
    session.meta.project_plan_confirmed_at = utc_now_iso()
    snapshot_plan_fingerprints(session, session.paths, pid)
    session.set_goal(
        build_project_goal(project_root=root, plan_status="confirmed"),
        phase="S4",
    )
    session.meta.updated_at = utc_now_iso()
    stats = read_task_stats(tasks_path)
    return (
        f"计划已确认：{root}（任务 {stats.done}/{stats.total} 已完成）。可以开始写代码。"
    )


def confirm_project_design(session: Session, paths: AgentPaths) -> str:
    root = (session.meta.project_root or "").strip()
    pid = (session.meta.project_id or "").strip()
    if not root or not pid or session.meta.active_shell != "project":
        raise ProjectModeError("当前会话未打开项目；先「项目 打开 <id>」")
    if getattr(session.meta, "project_workflow_stage", "requirements") != "documentation":
        raise ProjectModeError("当前不在文档整理阶段，不能确认设计")
    ready, missing = documentation_ready_for_design(paths, pid)
    if not ready:
        raise ProjectModeError(f"文档尚未达到设计确认标准：缺少 {', '.join(missing)}")
    session.meta.project_workflow_stage = "design"
    session.meta.project_plan_status = "draft"
    session.meta.project_design_confirmed_at = utc_now_iso()
    session.meta.project_active_task_id = ""
    session.meta.updated_at = utc_now_iso()
    session.save()
    return f"设计已确认：{root}。当前进入 design；请选择一个任务，例如「项目 开始任务 T-001」。"


def start_project_task(session: Session, paths: AgentPaths, task_id: str) -> str:
    root = (session.meta.project_root or "").strip()
    pid = (session.meta.project_id or "").strip()
    normalized_task = task_id.strip().upper()
    if not root or not pid or session.meta.active_shell != "project":
        raise ProjectModeError("当前会话未打开项目；先「项目 打开 <id>」")
    if getattr(session.meta, "project_workflow_stage", "requirements") != "design":
        raise ProjectModeError("必须先确认设计，再启动实现任务")
    if not re.fullmatch(r"T-\d+(?:-\d+)*", normalized_task):
        raise ProjectModeError("任务 ID 必须是 T-001 格式")
    tasks_path = project_dir(paths, pid) / "TASKS.md"
    if not tasks_path.is_file():
        raise ProjectModeError(f"缺少 {root}/TASKS.md，无法启动实现任务")
    tasks = tasks_path.read_text(encoding="utf-8")
    if not re.search(rf"(?im)^\s*-\s*\[\s\]\s+.*\b{re.escape(normalized_task)}\b", tasks):
        raise ProjectModeError(f"开放任务中不存在 {normalized_task}；只能启动当前队列中的未完成任务")
    session.meta.project_workflow_stage = "implementation"
    session.meta.project_plan_status = "confirmed"
    session.meta.project_plan_confirmed_at = utc_now_iso()
    session.meta.project_active_task_id = normalized_task
    session.set_goal(build_project_goal(project_root=root, plan_status="confirmed"), phase="S4")
    session.meta.updated_at = utc_now_iso()
    session.save()
    return f"已授权实现 {normalized_task}：仅限当前任务范围；完成后必须按 VERIFY.md 验证。"


def organize_project_plan(session: Session, paths: AgentPaths) -> str:
    root = (session.meta.project_root or "").strip()
    pid = (session.meta.project_id or "").strip()
    if not root or not pid or session.meta.active_shell != "project":
        raise ProjectModeError("当前会话未打开项目；先「项目 打开 <id>」")
    workflow_stage = getattr(session.meta, "project_workflow_stage", "requirements")
    if workflow_stage != "requirements":
        raise ProjectModeError(f"当前阶段为 {workflow_stage}，不能重新开始文档整理")
    result = organize_project_documents(paths, pid)
    session.meta.project_workflow_stage = "documentation"
    session.meta.project_plan_status = "draft"
    session.meta.project_plan_confirmed_at = ""
    session.set_goal(build_project_goal(project_root=root, plan_status="draft"), phase="S4")
    session.meta.updated_at = utc_now_iso()
    generated = ", ".join(result["generated"]) or "无（已有内容已保留）"
    return f"文档整理已开始：{root}；生成/保留四个核心制品。新生成：{generated}。请确认设计后再进入实现。"


def format_project_status(session: Session, paths: AgentPaths) -> str:
    root = (session.meta.project_root or "").strip()
    if not root:
        return "当前会话未绑定项目。"
    pid = session.meta.project_id or Path(root).name
    status = session.meta.project_plan_status or "draft"
    tasks_path = project_dir(paths, pid) / "TASKS.md" if pid else paths.workspace / "TASKS.md"
    stats = read_task_stats(tasks_path)
    lines = [
        f"项目：{pid}",
        f"根目录：{root}",
        f"计划：{status}",
        f"阶段：{getattr(session.meta, 'project_workflow_stage', 'requirements')}",
        f"纪律：{session.meta.project_delivery_profile or 'solo'}",
        f"模板：{read_project_template(paths, pid) if pid else 'standard'}",
        f"任务：{stats.done}/{stats.total} 已完成，{stats.open_count} 未勾",
    ]
    entry = getattr(session.meta, "project_entry", "") or ""
    if entry == "direct":
        lines.append("入口：直接实现")
    elif entry == "plan":
        lines.append("入口：先计划")
    if not plan_allows_code_writes(status):
        lines.append("提示：计划未确认 — 使用「项目 确认」或「项目 直接实现」后开始写代码。")
    return "\n".join(lines)


def run_project_command(
    session: Session,
    paths: AgentPaths,
    command: ParsedProjectCommand,
    *,
    output_fn: OutputFn,
) -> ProjectCommandResult:
    """Run one project command."""
    from project_switch import (
        lookup_project_session,
        open_project_on_session,
        start_new_project_thread,
        switch_to_project,
    )

    if command.kind == "list":
        items = list_projects(paths)
        if not items:
            output_fn("(无 workspace 项目；「项目 新建 <id>」创建)")
            return ProjectCommandResult()
        output_fn("workspace 项目：")
        current = (session.meta.project_id or "").strip()
        for item_id in items:
            stats = read_task_stats(project_dir(paths, item_id) / "TASKS.md")
            marker = ""
            if item_id == current:
                marker = " · 当前"
            else:
                sid = lookup_project_session(paths, item_id)
                if sid:
                    marker = f" · 会话 {sid}"
            output_fn(f"  - {item_id} ({stats.done}/{stats.total}){marker}")
        if current:
            output_fn(f"当前会话：{session.conversation_id} → {current}")
        return ProjectCommandResult()

    if command.kind == "new":
        assert command.project_id is not None
        from context_switch import ContextSwitchError, create_project_with_session_isolation

        try:
            updated, message = create_project_with_session_isolation(
                paths,
                session,
                command.project_id,
                project_template=command.project_template,
            )
        except (ProjectModeError, ContextSwitchError) as exc:
            output_fn(f"error: {exc}")
            return ProjectCommandResult()
        output_fn(message)
        if updated.conversation_id != session.conversation_id:
            return ProjectCommandResult(meta_changed=True, session=updated)
        return ProjectCommandResult(meta_changed=True)

    if command.kind == "open":
        assert command.project_id is not None
        try:
            updated, message = open_project_on_session(paths, session, command.project_id)
        except ProjectModeError as exc:
            output_fn(f"error: {exc}")
            return ProjectCommandResult()
        output_fn(message)
        if updated.conversation_id == session.conversation_id:
            return ProjectCommandResult(meta_changed=True)
        return ProjectCommandResult(meta_changed=True, session=updated)

    if command.kind == "switch":
        assert command.project_id is not None
        try:
            updated, message = switch_to_project(paths, session, command.project_id)
        except ProjectModeError as exc:
            output_fn(f"error: {exc}")
            return ProjectCommandResult()
        output_fn(message)
        if updated.conversation_id == session.conversation_id:
            return ProjectCommandResult(meta_changed=True)
        return ProjectCommandResult(meta_changed=True, session=updated)

    if command.kind == "new_thread":
        try:
            updated, message = start_new_project_thread(
                paths,
                session,
                project_id=command.project_id,
            )
        except ProjectModeError as exc:
            output_fn(f"error: {exc}")
            return ProjectCommandResult()
        output_fn(message)
        return ProjectCommandResult(meta_changed=True, session=updated)

    if command.kind == "confirm":
        try:
            message = confirm_project_plan(session)
        except ProjectModeError as exc:
            output_fn(f"error: {exc}")
            return ProjectCommandResult()
        if not getattr(session.meta, "project_entry", ""):
            session.meta.project_entry = "plan"
        session.save()
        output_fn(message)
        return ProjectCommandResult(meta_changed=True)

    if command.kind == "direct_implement":
        try:
            message = confirm_direct_implement(session)
        except ProjectModeError as exc:
            output_fn(f"error: {exc}")
            return ProjectCommandResult()
        session.save()
        output_fn(message)
        return ProjectCommandResult(meta_changed=True)

    if command.kind == "organize":
        try:
            message = organize_project_plan(session, paths)
        except ProjectModeError as exc:
            output_fn(f"error: {exc}")
            return ProjectCommandResult()
        session.save()
        output_fn(message)
        return ProjectCommandResult(meta_changed=True)

    if command.kind == "confirm_design":
        try:
            message = confirm_project_design(session, paths)
        except ProjectModeError as exc:
            output_fn(f"error: {exc}")
            return ProjectCommandResult()
        output_fn(message)
        return ProjectCommandResult(meta_changed=True)

    if command.kind == "start_task":
        assert command.project_id is not None
        try:
            message = start_project_task(session, paths, command.project_id)
        except ProjectModeError as exc:
            output_fn(f"error: {exc}")
            return ProjectCommandResult()
        output_fn(message)
        return ProjectCommandResult(meta_changed=True)

    if command.kind == "status":
        output_fn(format_project_status(session, paths))
        return ProjectCommandResult()

    if command.kind == "discipline":
        from project_mode import normalize_delivery_profile, set_delivery_profile

        assert command.project_id is not None
        profile = set_delivery_profile(session, command.project_id)
        session.save()
        label = "严格（ritual）" if profile == "ritual" else "宽松（solo）"
        output_fn(f"项目交付纪律已设为 {profile}（{label}）。")
        return ProjectCommandResult(meta_changed=True)

    if command.kind == "upgrade":
        pid = (session.meta.project_id or "").strip()
        if not pid or session.meta.active_shell != "project":
            output_fn("error: 当前会话未打开项目；先「项目 打开 <id>」再升档")
            return ProjectCommandResult()
        try:
            result = upgrade_project_to_standard(paths, pid)
        except ProjectModeError as exc:
            output_fn(f"error: {exc}")
            return ProjectCommandResult()
        created = ", ".join(result.get("created") or []) or "无（已有制品已保留）"
        if result.get("already_standard"):
            output_fn(f"项目 {pid} 已是标准七文件模板；无需升档。")
        else:
            output_fn(
                f"已升档为标准模板：workspace/{pid}；"
                f"补全制品：{created}；L2 闸门已恢复。"
            )
        return ProjectCommandResult(meta_changed=True)

    if command.kind == "verify":
        pid = (session.meta.project_id or "").strip()
        if not pid or session.meta.active_shell != "project":
            output_fn("error: 当前会话未打开项目")
            return ProjectCommandResult()
        plan_status = session.meta.project_plan_status or "draft"
        if not plan_allows_code_writes(plan_status):
            output_fn("error: 计划未确认，无法运行验收")
            return ProjectCommandResult()
        from project_mode import read_project_artifacts

        artifacts = read_project_artifacts(paths, pid)
        acceptance = parse_acceptance_spec(artifacts.get("PROJECT.md", ""))
        if acceptance is None:
            output_fn("error: PROJECT.md 未定义验收命令")
            return ProjectCommandResult()
        if not acceptance_script_exists(paths, pid, acceptance):
            output_fn(f"error: 验收脚本不存在（{acceptance.script_rel}）")
            return ProjectCommandResult()
        result = run_acceptance_check(paths, pid, acceptance)
        if not result.get("ok"):
            output_fn(f"验收失败：{result.get('error', 'unknown')}")
            return ProjectCommandResult()
        if result.get("passed"):
            output_fn(
                f"验收通过：{result.get('command')} → exit {result.get('exit_code')}"
            )
        else:
            output_fn(
                "验收未通过："
                f"exit {result.get('exit_code')}（期望 {result.get('expected_exit_code')}）"
            )
            if result.get("stderr"):
                output_fn(str(result.get("stderr")))
        return ProjectCommandResult()

    return ProjectCommandResult()


def _demo() -> None:
    from session import create_new

    paths = AgentPaths.discover()
    demo_session_dir = paths.data / "sessions" / "project-cli-demo"
    if demo_session_dir.is_dir():
        import shutil

        shutil.rmtree(demo_session_dir)
    session = create_new(paths, conversation_id="project-cli-demo")
    outputs: list[str] = []

    def out(text: str) -> None:
        outputs.append(text)

    run_project_command(session, paths, ParsedProjectCommand(kind="list"), output_fn=out)
    assert outputs and "workspace" in outputs[-1] or "cli-demo" in "".join(outputs)
    print("[PASS] project list")

    demo_id = "cli-demo-proj"
    dest = project_dir(paths, demo_id)
    if dest.is_dir():
        import shutil

        shutil.rmtree(dest)
    run_project_command(
        session,
        paths,
        ParsedProjectCommand(kind="new", project_id=demo_id),
        output_fn=out,
    )
    assert session.meta.project_id == demo_id
    assert session.meta.active_shell == "project"
    print("[PASS] project new")

    assert try_short_plan_confirm(session, "确认", out)
    assert session.meta.project_plan_status == "confirmed"
    print("[PASS] short plan confirm")

    import shutil

    shutil.rmtree(dest, ignore_errors=True)
    print("[PASS] T-1103: project_cli demo")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
