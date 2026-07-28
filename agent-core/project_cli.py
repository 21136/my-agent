"""REPL commands for workspace project mode (PROJECT-MODE T-1103, T-1110)."""

from __future__ import annotations

import shlex
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
    utc_now_iso,
)
from router import TopicRoutingError, apply_confirmed_topics, registered_topic_ids
from session import Session, utc_now_iso

ProjectCommandKind = Literal["list", "new", "open", "switch", "confirm", "status", "verify"]
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


def try_short_plan_confirm(session: Session, text: str, output_fn: OutputFn) -> bool:
    """Map bare 「确认」/「开工」 to 项目 确认 when plan gate is open."""
    if session.meta.active_shell != "project":
        return False
    status = session.meta.project_plan_status or "draft"
    if status not in {"draft", "plan_dirty"}:
        return False
    normalized = text.strip().casefold().replace(" ", "")
    if normalized not in _SHORT_PLAN_CONFIRM:
        return False
    try:
        message = confirm_project_plan(session)
    except ProjectModeError as exc:
        output_fn(f"error: {exc}")
        return True
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
            if rest:
                try:
                    tokens = shlex.split(rest, posix=False)
                except ValueError as exc:
                    raise ProjectCommandError(f"invalid project command: {exc}") from exc
                if tokens:
                    return ParsedProjectCommand(kind="new", project_id=tokens[0])
            raise ProjectCommandError("新项目 <id>")

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
    if verb in {"列表", "list"}:
        return ParsedProjectCommand(kind="list")
    if verb in {"新建", "new", "create"}:
        if len(tokens) < 2:
            raise ProjectCommandError("项目 新建 <id>")
        return ParsedProjectCommand(kind="new", project_id=tokens[1])
    if verb in {"打开", "open"}:
        if len(tokens) < 2:
            raise ProjectCommandError("项目 打开 <id>")
        return ParsedProjectCommand(kind="open", project_id=tokens[1])
    if verb in {"切换", "switch"}:
        if len(tokens) < 2:
            raise ProjectCommandError("项目 切换 <id>")
        return ParsedProjectCommand(kind="switch", project_id=tokens[1])
    if verb in {"确认", "confirm"}:
        return ParsedProjectCommand(kind="confirm")
    if verb in {"状态", "status"}:
        return ParsedProjectCommand(kind="status")
    if verb in {"验收", "verify"}:
        return ParsedProjectCommand(kind="verify")

    raise ProjectCommandError(
        "未知项目命令；可用：列表 | 新建 <id> | 打开 <id> | 切换 <id> | 确认 | 验收 | 状态"
        "（口语也可「新项目 <id>」）"
    )


def _ensure_coding_topic(session: Session) -> None:
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
    root = project_root_rel(pid)
    session.meta.active_shell = "project"
    session.meta.project_id = pid
    session.meta.project_root = root
    session.meta.project_plan_status = plan_status
    if plan_status == "confirmed":
        session.meta.project_plan_confirmed_at = utc_now_iso()
    session.set_goal(build_project_goal(project_root=root, plan_status=plan_status), phase="S4")
    _ensure_coding_topic(session)
    session.meta.updated_at = utc_now_iso()
    from project_switch import record_project_session

    record_project_session(session.paths, pid, session.conversation_id)
    return root


def confirm_project_plan(session: Session) -> str:
    root = (session.meta.project_root or "").strip()
    pid = (session.meta.project_id or "").strip()
    if not root or not pid or session.meta.active_shell != "project":
        raise ProjectModeError("当前会话未打开项目；先「项目 打开 <id>」")

    tasks_path = project_dir(session.paths, pid) / "TASKS.md"
    if not tasks_path.is_file():
        raise ProjectModeError(f"缺少 {root}/TASKS.md；请先让助手生成计划")

    session.meta.project_plan_status = "confirmed"
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
        f"任务：{stats.done}/{stats.total} 已完成，{stats.open_count} 未勾",
    ]
    if not plan_allows_code_writes(status):
        lines.append("提示：计划未确认 — 使用「项目 确认」后开始写代码。")
    return "\n".join(lines)


def run_project_command(
    session: Session,
    paths: AgentPaths,
    command: ParsedProjectCommand,
    *,
    output_fn: OutputFn,
) -> ProjectCommandResult:
    """Run one project command."""
    from project_switch import lookup_project_session, open_project_on_session, switch_to_project

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
                paths, session, command.project_id
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

    if command.kind == "confirm":
        try:
            message = confirm_project_plan(session)
        except ProjectModeError as exc:
            output_fn(f"error: {exc}")
            return ProjectCommandResult()
        session.save()
        output_fn(message)
        return ProjectCommandResult(meta_changed=True)

    if command.kind == "status":
        output_fn(format_project_status(session, paths))
        return ProjectCommandResult()

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
