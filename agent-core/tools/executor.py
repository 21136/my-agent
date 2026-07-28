"""ToolExecutor — confirm, dry_run, spill, evolve_log (TOOLS.md §6, TASKS T-108–T-110)."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[1]
_TOOLS_DIR = Path(__file__).resolve().parent
if sys.path and Path(sys.path[0]).resolve() == _TOOLS_DIR.resolve():
    sys.path.pop(0)
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from runtime_guards import auto_demo_on_write_evolve, write_inline_max_chars
from tools.builtin import (
    fetch_url,
    grep,
    list_dir,
    propose_context_switch,
    read_file,
    run_evolved,
    web_search,
)
from tools.logging import (
    EvolveLog,
    conversation_id_from_session,
    read_events,
)
from tools.registry import BuiltinTool, EvolvedTool, ToolRegistry
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok, to_json

ConfirmFn = Callable[[str, bool], str]
EventFn = Callable[[str, dict[str, Any]], None]

_BUILTIN_RUNNERS: dict[str, Callable[..., ToolResult]] = {
    "read_file": read_file.run,
    "list_dir": list_dir.run,
    "grep": grep.run,
    "web_search": web_search.run,
    "fetch_url": fetch_url.run,
    "run_evolved": run_evolved.run,
    "propose_context_switch": propose_context_switch.run,
}

# Preview prefix: WsBridge waits on confirm queue without emitting confirm.request.
CONTEXT_SWITCH_CONFIRM_PREFIX = "__context_switch__:"

_META_FILENAME = "meta.json"
_WORKSPACE_APPROVED_KEY = "workspace_evolved_approved"
_EVENT_SESSION_WORKSPACE_APPROVED = "session_workspace_approved"
_DEFAULT_SPILL_CHARS = 8000
_DEFAULT_PREVIEW_CHARS = 2000
_TOOL_OUTPUTS_DIR = "tool_outputs"


@dataclass
class ScaffoldDemoRecord:
    """Per-segment scaffold demo probe result (Phase 16 M1)."""

    tool_name: str
    tool_dir: str
    demo_result: dict[str, Any] = field(default_factory=dict)
    auto_demo: bool = False


@dataclass
class ExecutorSession:
    """Per-conversation executor state (TOOLS.md §6.3)."""

    session_dir: Path | None = None
    workspace_evolved_approved: bool = False
    allowed_evolved: set[str] | None = None
    blocked_tools: frozenset[str] = field(default_factory=frozenset)
    turn_mode: str = "agent"
    scaffold_tool_turn: bool = False
    active_shell: str = ""
    project_root: str = ""
    project_plan_status: str = ""
    in_execute_segment: bool = False
    segment_scaffold_tools: dict[str, ScaffoldDemoRecord] = field(default_factory=dict)
    task_stop_armed: bool = False
    task_done_baseline: int | None = None

    @classmethod
    def load(cls, session_dir: Path | None, *, allowed_evolved: set[str] | None = None) -> ExecutorSession:
        approved = False
        active_shell = ""
        project_root = ""
        project_plan_status = ""
        if session_dir is not None:
            meta_path = session_dir / _META_FILENAME
            if meta_path.is_file():
                try:
                    payload = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    payload = {}
                if isinstance(payload, dict):
                    approved = bool(payload.get(_WORKSPACE_APPROVED_KEY, False))
                    active_shell = str(payload.get("active_shell", "") or "")
                    project_root = str(payload.get("project_root", "") or "").strip()
                    project_plan_status = str(payload.get("project_plan_status", "") or "")
        return cls(
            session_dir=session_dir,
            workspace_evolved_approved=approved,
            allowed_evolved=allowed_evolved,
            active_shell=active_shell,
            project_root=project_root,
            project_plan_status=project_plan_status,
        )

    def refresh_bound_project_meta(self) -> None:
        """Reload project gate fields from meta.json (may change mid-session)."""
        if self.session_dir is None:
            return
        meta_path = self.session_dir / _META_FILENAME
        if not meta_path.is_file():
            return
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        self.active_shell = str(payload.get("active_shell", "") or "")
        self.project_root = str(payload.get("project_root", "") or "").strip()
        self.project_plan_status = str(payload.get("project_plan_status", "") or "")


_TOOL_SCAFFOLD_FILENAMES = frozenset({"main.py", "tool.toml", "README.md"})
_EVOLVE_TOOL_SCAFFOLD_PATH_RE = re.compile(
    r"^evolve/tools/(?P<scope>[a-z][a-z0-9_]*)/(?P<tool>[a-z][a-z0-9_]*)/"
    r"(?P<file>main\.py|tool\.toml|README\.md)$"
)
_WORKSPACE_WRITE_TOOLS = frozenset({"write_text", "append_text", "copy_move"})
_INLINE_WRITE_TOOLS = frozenset({"write_text", "append_text"})


def _normalize_tool_path(path: str) -> str:
    return path.strip().replace("\\", "/").lstrip("/")


def _tool_scaffold_basename(path: str) -> bool:
    name = _normalize_tool_path(path).rstrip("/").split("/")[-1]
    return name in _TOOL_SCAFFOLD_FILENAMES


def _is_evolve_tool_scaffold_path(path: str) -> bool:
    return _EVOLVE_TOOL_SCAFFOLD_PATH_RE.match(_normalize_tool_path(path)) is not None


def _has_workspace_path(arguments: dict[str, Any]) -> bool:
    value = arguments.get("content_workspace_path")
    return isinstance(value, str) and bool(value.strip())


def _decode_inline_body(arguments: dict[str, Any]) -> str | None:
    import base64

    if _has_workspace_path(arguments):
        return None
    plain = arguments.get("content")
    if isinstance(plain, str):
        return plain
    raw_b64 = arguments.get("content_base64")
    if isinstance(raw_b64, str) and raw_b64.strip():
        try:
            return base64.b64decode(raw_b64, validate=True).decode("utf-8")
        except Exception:
            return plain if isinstance(plain, str) else None
    return None


def _inline_body_guard(
    evolved_name: str,
    outer_arguments: dict[str, Any],
) -> ToolResult | None:
    """Reject oversized inline write payloads before confirm (T-1511)."""
    inner = _merged_evolved_arguments(outer_arguments, evolved_name)
    sources: list[dict[str, Any]] = []
    if isinstance(inner, dict) and inner:
        sources.append(inner)
    if evolved_name == "write_evolve":
        sources.append(outer_arguments)

    checked_workspace = False
    for source in sources:
        if _has_workspace_path(source):
            checked_workspace = True
            break

    if checked_workspace:
        return None

    bodies: list[str] = []
    for source in sources:
        body = _decode_inline_body(source)
        if body is not None:
            bodies.append(body)

    if not bodies:
        return None

    limit = write_inline_max_chars()
    longest = max(len(body) for body in bodies)
    if longest <= limit:
        return None

    return tool_fail(
        "run_evolved",
        ToolErrorCode.VALIDATION_ERROR,
        (
            f"内联正文解码后 {longest} 字符，超过 WRITE_INLINE_MAX_CHARS={limit}。"
            "请 write_text → workspace/_staging* → content_workspace_path"
        ),
        details={
            "guard_type": "inline_write_max",
            "decoded_chars": longest,
            "limit": limit,
            "tool_name": evolved_name,
        },
    )


def _tool_name_from_evolve_path(path: str) -> str | None:
    normalized = path.strip().replace("\\", "/").lstrip("/")
    parts = normalized.split("/")
    if len(parts) >= 4 and parts[0] == "evolve" and parts[1] == "tools":
        return parts[3]
    return None


def _is_run_python_demo_call(inner: dict[str, Any]) -> bool:
    path = inner.get("path")
    if not isinstance(path, str) or not path.strip():
        return False
    normalized = path.strip().replace("\\", "/")
    if "_staging" in normalized:
        return False
    extra_args = inner.get("extra_args", [])
    if extra_args is None:
        extra_args = []
    if not isinstance(extra_args, list):
        return False
    if any(isinstance(arg, str) and arg.strip().lower() == "demo" for arg in extra_args):
        return True
    return normalized.endswith("/main.py") or normalized.endswith("main.py")


def _validate_run_python_scaffold_guard(
    session: ExecutorSession,
    outer_arguments: dict[str, Any],
) -> ToolResult | None:
    """Reject duplicate run_python demo for tools already probed this segment (T-1515)."""
    if not session.in_execute_segment:
        return None
    if session.active_shell == "project":
        return None
    if not session.scaffold_tool_turn and session.active_shell != "grow":
        return None

    inner = _merged_evolved_arguments(outer_arguments, "run_python")
    if not _is_run_python_demo_call(inner):
        return None

    path = inner.get("path")
    if not isinstance(path, str):
        return None
    tool_name = _tool_name_from_evolve_path(path)
    if tool_name is None:
        return None

    record = session.segment_scaffold_tools.get(tool_name)
    if record is None or not record.demo_result.get("attempted"):
        return None

    exit_code = record.demo_result.get("exit_code")
    summary = f"exit_code={exit_code}" if exit_code is not None else "已执行"
    return tool_fail(
        "run_evolved",
        ToolErrorCode.VALIDATION_ERROR,
        (
            f"工具「{tool_name}」已在本 segment 自动运行 demo probe（{summary}）。"
            "请勿重复 run_python demo；请根据上方 demo 结果修复或继续交付。"
        ),
        details={
            "guard_type": "run_python_demo_rejected",
            "tool_name": tool_name,
            "tool_dir": record.tool_dir,
            "demo_exit_code": exit_code,
        },
    )


def _format_guard_notice(guard_type: str, fields: dict[str, Any]) -> str | None:
    if guard_type == "inline_write_max":
        limit = fields.get("limit", write_inline_max_chars())
        decoded = fields.get("decoded_chars")
        return f"[guard] 内联写入超过 {limit} 字符（{decoded}），请改用 workspace/_staging + content_workspace_path"
    if guard_type in {"scaffold_demo_auto", "scaffold_demo_manual"}:
        tool_name = fields.get("tool_name", "?")
        if fields.get("cancelled"):
            return f"[guard] demo probe · {tool_name}：已取消"
        if not fields.get("attempted"):
            reason = fields.get("skipped_reason", "skipped")
            return f"[guard] demo probe · {tool_name}：跳过（{reason}）"
        exit_code = fields.get("exit_code")
        if exit_code == 0:
            return f"[guard] demo probe · {tool_name}：通过（exit 0）"
        return f"[guard] demo probe · {tool_name}：失败（exit {exit_code}）"
    if guard_type == "run_python_demo_rejected":
        tool_name = fields.get("tool_name", "?")
        return f"[guard] 已拒调 run_python demo · {tool_name}（本 segment 已有自动 demo 结果）"
    if guard_type == "task_stop_armed":
        return "[guard] 本轮已勾选完成一条 TASK；请停下，等用户「继续」再做下一项"
    if guard_type == "task_stop":
        message = fields.get("message")
        if isinstance(message, str) and message.strip():
            return message
        return "[guard] task 一停门：请先结束本回合，用户回复「继续」后再写下一产物"
    message = fields.get("message")
    if isinstance(message, str) and message.strip():
        return f"[guard] {message}"
    return None


def _merged_evolved_arguments(arguments: dict[str, Any], evolved_name: str) -> dict[str, Any]:
    if evolved_name == "write_evolve":
        return run_evolved.coalesce_tool_arguments(arguments)
    inner = arguments.get("arguments")
    return inner if isinstance(inner, dict) else {}


def _write_evolve_content_guard(
    path: str,
    inner: dict[str, Any],
    outer_arguments: dict[str, Any],
) -> ToolResult | None:
    """Enforce base64/workspace-path for tool.toml and risky main.py/README.md bodies."""
    import base64

    normalized = path.replace("\\", "/")
    has_b64 = bool(inner.get("content_base64") or inner.get("content_workspace_path"))
    has_outer_b64 = bool(
        outer_arguments.get("content_base64") or outer_arguments.get("content_workspace_path")
    )
    plain = inner.get("content") if isinstance(inner.get("content"), str) else None
    if plain is None and isinstance(outer_arguments.get("content"), str):
        plain = outer_arguments.get("content")

    # C7: validate base64 *before* confirm so bad padding never opens a card (BUG-013).
    for source in (inner, outer_arguments):
        raw_b64 = source.get("content_base64")
        if isinstance(raw_b64, str) and raw_b64.strip():
            try:
                base64.b64decode(raw_b64, validate=True)
            except Exception as exc:
                return tool_fail(
                    "run_evolved",
                    ToolErrorCode.VALIDATION_ERROR,
                    f"content_base64 decode failed: {exc}",
                    details={"path": path},
                )

    if normalized.endswith("tool.toml"):
        if not (has_b64 or has_outer_b64):
            return tool_fail(
                "run_evolved",
                ToolErrorCode.VALIDATION_ERROR,
                "写 tool.toml 必须提供 content_base64 或 content_workspace_path",
                details={"path": path},
            )
        if plain:
            return tool_fail(
                "run_evolved",
                ToolErrorCode.VALIDATION_ERROR,
                "写 tool.toml 不要用 content，请用 content_base64 或 content_workspace_path",
                details={"path": path},
            )
        return None

    if normalized.endswith(("main.py", "README.md")):
        if plain and ("\n" in plain or '"' in plain or len(plain) > 240):
            return tool_fail(
                "run_evolved",
                ToolErrorCode.VALIDATION_ERROR,
                (
                    "写 main.py/README.md 含换行、双引号或较长正文时须用 content_base64 "
                    "或 content_workspace_path（大文件优先 staging）"
                ),
                details={"path": path, "hint": "top-level content_base64 / content_workspace_path"},
            )
    return None


def _write_evolve_wrote_tool_manifest(tool_name: str, arguments: dict[str, Any], result: ToolResult) -> bool:
    if tool_name != "run_evolved" or not result.ok:
        return False
    evolved_name = arguments.get("tool_name")
    if not isinstance(evolved_name, str) or evolved_name.strip() != "write_evolve":
        return False
    data = result.data if isinstance(result.data, dict) else {}
    if data.get("dry_run"):
        return False
    written = data.get("written")
    if isinstance(written, str) and written.replace("\\", "/").endswith("tool.toml"):
        return True
    return False


def _validate_project_mode_call(
    session: ExecutorSession,
    tool_name: str,
    arguments: dict[str, Any],
) -> ToolResult | None:
    from project_mode import project_mode_block_reason

    reason = project_mode_block_reason(
        active_shell=session.active_shell,
        project_root=session.project_root,
        plan_status=session.project_plan_status,
        tool_name=tool_name,
        arguments=arguments,
    )
    if reason is None:
        return None
    return tool_fail(
        tool_name,
        ToolErrorCode.VALIDATION_ERROR,
        reason,
        details={
            "active_shell": session.active_shell,
            "project_plan_status": session.project_plan_status or "draft",
        },
    )


def _validate_foreign_project_write(
    session: ExecutorSession,
    tool_name: str,
    arguments: dict[str, Any],
) -> ToolResult | None:
    """Block writes into another workspace/<id>/ without context switch (T-1903)."""
    if session.active_shell != "project" or not session.project_root.strip():
        return None
    if tool_name != "run_evolved":
        return None
    evolved_name = arguments.get("tool_name")
    if not isinstance(evolved_name, str) or evolved_name.strip() not in _WORKSPACE_WRITE_TOOLS:
        return None

    from context_switch import foreign_workspace_project_write
    from project_mode import extract_run_evolved_paths

    for path in extract_run_evolved_paths(tool_name, arguments):
        foreign = foreign_workspace_project_write(
            path,
            current_project_root=session.project_root,
        )
        if foreign:
            return tool_fail(
                tool_name,
                ToolErrorCode.VALIDATION_ERROR,
                (
                    f"路径 {path!r} 属于其他项目 workspace/{foreign}/；"
                    "请先 propose_context_switch（project.create 或 project.switch）"
                    "并经用户确认后再写"
                ),
                details={
                    "guard_type": "foreign_project_write",
                    "foreign_project_id": foreign,
                    "current_project_root": session.project_root,
                    "path": path,
                },
            )
    return None


def _validate_task_stop_write(
    session: ExecutorSession,
    tool_name: str,
    arguments: dict[str, Any],
) -> ToolResult | None:
    """Phase 20 M1: after marking a TASKS [x], block next-task product writes."""
    from project_mode import task_stop_block_reason

    reason = task_stop_block_reason(
        active_shell=session.active_shell,
        project_root=session.project_root,
        task_stop_armed=session.task_stop_armed,
        tool_name=tool_name,
        arguments=arguments,
    )
    if reason is None:
        return None
    return tool_fail(
        tool_name,
        ToolErrorCode.VALIDATION_ERROR,
        reason,
        details={
            "guard_type": "task_stop",
            "active_shell": session.active_shell,
            "project_root": session.project_root,
        },
    )


def _validate_scaffold_evolved_call(
    executor_session: ExecutorSession,
    evolved_name: str,
    outer_arguments: dict[str, Any],
) -> ToolResult | None:
    inner = _merged_evolved_arguments(outer_arguments, evolved_name)

    if executor_session.scaffold_tool_turn and evolved_name in _WORKSPACE_WRITE_TOOLS:
        path = inner.get("path")
        if isinstance(path, str) and _tool_scaffold_basename(path):
            return tool_fail(
                "run_evolved",
                ToolErrorCode.VALIDATION_ERROR,
                (
                    f"文件名 {path.split('/')[-1]!r} 属于 evolved 工具脚手架，"
                    "不能经 write_text 写入 workspace；请用 write_evolve → evolve/tools/<scope>/<name>/..."
                ),
                details={"requested_tool": evolved_name, "scaffold_tool_turn": True},
            )

    if evolved_name in _WORKSPACE_WRITE_TOOLS:
        path = inner.get("path")
        if isinstance(path, str) and _is_evolve_tool_scaffold_path(path):
            return tool_fail(
                "run_evolved",
                ToolErrorCode.VALIDATION_ERROR,
                (
                    f"路径 {path!r} 属于 evolved 工具脚手架目录，"
                    "不能经 write_text 写入；请用 write_evolve → evolve/tools/<scope>/<name>/..."
                ),
                details={"path": path, "hint": "write_evolve + content_base64"},
            )

    if evolved_name == "write_evolve":
        path = inner.get("path")
        if isinstance(path, str) and path.strip():
            guard = _write_evolve_content_guard(path, inner, outer_arguments)
            if guard is not None:
                return guard
    return None


@dataclass
class ToolExecutor:
    """Dispatch builtins / run_evolved with confirm interaction."""

    registry: ToolRegistry
    session: ExecutorSession = field(default_factory=ExecutorSession)
    confirm_fn: ConfirmFn | None = None
    on_event: EventFn | None = None
    evolve_log: EvolveLog | None = None
    on_registry_reloaded: Callable[[], None] | None = field(default=None, repr=False)
    cancel_event: threading.Event | None = field(default=None, repr=False)

    @classmethod
    def create(
        cls,
        *,
        paths: AgentPaths | None = None,
        session_dir: Path | None = None,
        allowed_evolved: set[str] | None = None,
        confirm_fn: ConfirmFn | None = None,
        on_event: EventFn | None = None,
        evolve_log: EvolveLog | None = None,
    ) -> ToolExecutor:
        agent_paths = paths or AgentPaths.discover()
        registry = ToolRegistry.load(agent_paths)
        session = ExecutorSession.load(session_dir, allowed_evolved=allowed_evolved)
        return cls(
            registry=registry,
            session=session,
            confirm_fn=confirm_fn,
            on_event=on_event,
            evolve_log=evolve_log if evolve_log is not None else EvolveLog.for_agent(agent_paths),
        )

    def reload_registry(self) -> None:
        """Rescan evolve/tools after write_evolve updates a tool manifest."""
        self.registry = ToolRegistry.load(self.registry.agent_paths)

    def begin_execute_segment(self) -> None:
        """Reset per-segment scaffold guard state (T-1515)."""
        self.session.in_execute_segment = True
        self.session.segment_scaffold_tools.clear()

    def begin_turn(self) -> None:
        """Reset per-turn task-stop gate (Phase 20 M1)."""
        self.session.task_stop_armed = False
        self.session.task_done_baseline = None
        if self.session.active_shell != "project" or not self.session.project_root.strip():
            return
        from project_mode import project_id_from_root, read_task_stats

        pid = project_id_from_root(self.session.project_root)
        if not pid:
            return
        tasks_path = self.registry.agent_paths.workspace / pid / "TASKS.md"
        stats = read_task_stats(tasks_path)
        self.session.task_done_baseline = stats.done

    def end_execute_segments(self) -> None:
        self.session.in_execute_segment = False

    def run_scaffold_demo_probe(self, tool_name: str) -> dict[str, Any]:
        """Run cancellable ``main.py demo`` for checker / manual acceptance (T-1514)."""
        name = tool_name.strip()
        evolved = self.registry.get_evolved(name)
        if evolved is None:
            payload = {
                "ok": False,
                "tool_name": name,
                "attempted": False,
                "skipped_reason": "tool not in registry",
            }
            self._record_guard_event("scaffold_demo_manual", payload)
            return payload
        demo_result = run_evolved.run_scaffold_demo(evolved, cancel_event=self.cancel_event)
        self.session.segment_scaffold_tools[name] = ScaffoldDemoRecord(
            tool_name=name,
            tool_dir=evolved.relative_dir,
            demo_result=demo_result,
        )
        self._record_guard_event("scaffold_demo_manual", demo_result)
        return demo_result

    def _maybe_reload_registry_after_write_evolve(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
    ) -> None:
        if not _write_evolve_wrote_tool_manifest(tool_name, arguments, result):
            return
        self.reload_registry()
        if self.on_registry_reloaded is not None:
            self.on_registry_reloaded()
        self._maybe_auto_scaffold_demo(tool_name, arguments, result)

    def _maybe_auto_scaffold_demo(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        write_result: ToolResult,
    ) -> None:
        if not auto_demo_on_write_evolve():
            return
        if not self.session.scaffold_tool_turn:
            return
        if not _write_evolve_wrote_tool_manifest(tool_name, arguments, write_result):
            return
        data = write_result.data if isinstance(write_result.data, dict) else {}
        written = data.get("written")
        if not isinstance(written, str):
            return
        evolved_name = _tool_name_from_evolve_path(written)
        if evolved_name is None:
            return
        evolved = self.registry.get_evolved(evolved_name)
        if evolved is None:
            return
        demo_result = run_evolved.run_scaffold_demo(evolved, cancel_event=self.cancel_event)
        self.session.segment_scaffold_tools[evolved_name] = ScaffoldDemoRecord(
            tool_name=evolved_name,
            tool_dir=evolved.relative_dir,
            demo_result=demo_result,
            auto_demo=True,
        )
        self._record_guard_event("scaffold_demo_auto", demo_result)

    def _record_guard_event(
        self,
        guard_type: str,
        payload: dict[str, Any] | ToolResult,
    ) -> None:
        if isinstance(payload, ToolResult):
            details = payload.error.details if payload.error and isinstance(payload.error.details, dict) else {}
            fields: dict[str, Any] = dict(details)
            if payload.error is not None:
                fields.setdefault("message", payload.error.message)
            fields["ok"] = payload.ok
        else:
            fields = dict(payload)

        fields.pop("guard_type", None)

        if self.evolve_log is not None:
            self.evolve_log.log_guard_event(
                guard_type=guard_type,
                conversation_id=conversation_id_from_session(self.session.session_dir),
                **fields,
            )

        notice = _format_guard_notice(guard_type, fields)
        if notice:
            self._emit_event("guard.notice", {"text": notice})

    def run(self, tool_name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        """Validate, optionally confirm, then execute a tool call."""
        started = time.perf_counter()
        name = tool_name.strip()
        args = dict(arguments or {})
        confirm_decision = "skipped"

        error = self.validate(name, args)
        if error is not None:
            self._maybe_log_validation_guard(error)
            self._log_tool_call(name, args, error, confirm=confirm_decision, started=started)
            return error

        builtin = self.registry.get_builtin(name)
        assert builtin is not None

        if name == "propose_context_switch":
            return self._run_propose_context_switch(args, started=started)

        evolved_target = self._resolve_evolved_target(name, args) if name == "run_evolved" else None
        if self._needs_confirm(builtin, evolved_target, args, tool_name=name):
            confirm_decision = self._ask_confirm(name, args, evolved_target)
            if confirm_decision == "n":
                result = tool_fail(
                    name,
                    ToolErrorCode.CONFIRM_REJECTED,
                    "tool call rejected by user",
                    duration_ms=_elapsed_ms(started),
                )
                self._log_tool_call(name, args, result, confirm=confirm_decision, started=started)
                return result
            if confirm_decision == "a":
                self._approve_workspace_evolved(evolved_target)

        call_id = str(uuid.uuid4())
        self._emit_event(
            "tool.start",
            {
                "tool": name,
                "call_id": call_id,
                "summary": _tool_event_summary(name, args, evolved_target),
                "arguments": _tool_event_args(name, args),
            },
        )
        # C6: always emit tool.end even when execute raises (BUG-011).
        result: ToolResult
        try:
            result = self._maybe_spill_output(self._execute_builtin(name, args, started=started))
        except Exception as exc:
            result = tool_fail(
                name,
                "execution_error",
                f"tool execution failed: {exc}",
                duration_ms=_elapsed_ms(started),
            )
        self._emit_event(
            "tool.end",
            {
                "tool": name,
                "call_id": call_id,
                "ok": result.ok,
                "summary": _tool_result_summary(result),
                "output_path": result.output_path,
            },
        )
        if name == "read_file" and result.ok:
            self._maybe_record_memory_entity_used(args, result)
        if result.ok:
            self._maybe_reload_registry_after_write_evolve(name, args, result)
            self._maybe_arm_task_stop(name, args, result)
        self._log_tool_call(name, args, result, confirm=confirm_decision, started=started)
        return result

    def _run_propose_context_switch(
        self,
        arguments: dict[str, Any],
        *,
        started: float,
    ) -> ToolResult:
        from context_switch import (
            ContextSwitchError,
            apply_context_switch,
            build_context_switch_request,
            done_events,
            normalize_proposal,
        )
        from project_mode import ProjectModeError
        from session import Session

        name = "propose_context_switch"
        try:
            proposal = normalize_proposal(
                action=str(arguments.get("action", "")),
                target=str(arguments.get("target", "")),
                reason=str(arguments.get("reason", "") or ""),
                project_id=(
                    str(arguments["project_id"])
                    if isinstance(arguments.get("project_id"), str)
                    else None
                ),
            )
        except (ContextSwitchError, ProjectModeError, TypeError, ValueError) as exc:
            result = tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                str(exc),
                duration_ms=_elapsed_ms(started),
            )
            self._log_tool_call(name, arguments, result, confirm="skipped", started=started)
            return result

        if self.session.session_dir is None:
            result = tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                "no active session directory",
                duration_ms=_elapsed_ms(started),
            )
            self._log_tool_call(name, arguments, result, confirm="skipped", started=started)
            return result

        paths = self.registry.agent_paths
        previous = Session.load(paths, self.session.session_dir.name)
        request = build_context_switch_request(previous, proposal)
        self._emit_event("context.switch.request", request)

        call_id = str(uuid.uuid4())
        self._emit_event(
            "tool.start",
            {
                "tool": name,
                "call_id": call_id,
                "summary": f"{proposal.action} → {proposal.target}",
                "arguments": {
                    "action": proposal.action,
                    "target": proposal.target,
                    "reason": proposal.reason,
                },
            },
        )

        confirm_decision = "n"
        try:
            if self.confirm_fn is not None:
                confirm_decision = _normalize_confirm_choice(
                    self.confirm_fn(
                        f"{CONTEXT_SWITCH_CONFIRM_PREFIX}{proposal.request_id}",
                        False,
                    ),
                    False,
                )
            else:
                preview = (
                    f"{request.get('title', '换线')}\n"
                    f"当前项目: {(previous.meta.project_id or '（无）')}\n"
                    f"原因: {proposal.reason or '（无）'}\n"
                    + "\n".join(f"- {s}" for s in request.get("side_effects", []))
                )
                confirm_decision = _prompt_confirm_interactive(preview, False)

            if confirm_decision != "y":
                for event in done_events(
                    request_id=proposal.request_id,
                    proposal=proposal,
                    updated=previous,
                    previous=previous,
                    message="用户拒绝换线，仍留在当前会话",
                    choice="n",
                ):
                    et = event.get("type")
                    if isinstance(et, str):
                        payload = {k: v for k, v in event.items() if k != "type"}
                        self._emit_event(et, payload)
                result = tool_fail(
                    name,
                    ToolErrorCode.CONFIRM_REJECTED,
                    "context switch rejected by user",
                    duration_ms=_elapsed_ms(started),
                    details={"action": proposal.action, "target": proposal.target},
                )
            else:
                updated, message = apply_context_switch(paths, previous, proposal)
                for event in done_events(
                    request_id=proposal.request_id,
                    proposal=proposal,
                    updated=updated,
                    previous=previous,
                    message=message,
                    choice="y",
                ):
                    et = event.get("type")
                    if isinstance(et, str):
                        payload = {k: v for k, v in event.items() if k != "type"}
                        self._emit_event(et, payload)
                # Keep executor meta aligned when same session rebound.
                if updated.conversation_id == previous.conversation_id:
                    self.session.active_shell = updated.meta.active_shell
                    self.session.project_root = updated.meta.project_root or ""
                    self.session.project_plan_status = updated.meta.project_plan_status or ""
                result = tool_ok(
                    name,
                    {
                        "action": proposal.action,
                        "target": proposal.target,
                        "message": message,
                        "session_id": updated.conversation_id,
                        "session_replaced": updated.conversation_id != previous.conversation_id,
                        "project_id": updated.meta.project_id,
                        "project_root": updated.meta.project_root,
                        "project_plan_status": updated.meta.project_plan_status,
                    },
                    duration_ms=_elapsed_ms(started),
                )
        except (ContextSwitchError, ProjectModeError) as exc:
            result = tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                str(exc),
                duration_ms=_elapsed_ms(started),
            )
            confirm_decision = "y"
        except Exception as exc:
            result = tool_fail(
                name,
                "execution_error",
                f"context switch failed: {exc}",
                duration_ms=_elapsed_ms(started),
            )

        self._emit_event(
            "tool.end",
            {
                "tool": name,
                "call_id": call_id,
                "ok": result.ok,
                "summary": _tool_result_summary(result),
                "output_path": result.output_path,
            },
        )
        self._log_tool_call(name, arguments, result, confirm=confirm_decision, started=started)
        return result

    def validate(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult | None:
        """Return a failed ToolResult when the call is invalid; otherwise None."""
        self.session.refresh_bound_project_meta()
        name = tool_name.strip()
        if not name:
            return tool_fail(
                tool_name or "unknown",
                ToolErrorCode.VALIDATION_ERROR,
                "tool name is required",
            )

        builtin = self.registry.get_builtin(name)
        if builtin is None:
            return tool_fail(
                name,
                ToolErrorCode.TOOL_NOT_FOUND,
                f"unknown builtin tool: {name}",
            )

        if name in self.session.blocked_tools:
            return tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                f"tool {name!r} is not available in this context (explore subagent)",
                details={"blocked_tools": sorted(self.session.blocked_tools)},
            )

        if name == "run_evolved" and self.session.turn_mode == "ask":
            return tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                "run_evolved is disabled in ask mode (只聊); say 动手 to enable writes",
                details={"turn_mode": "ask"},
            )

        if name != "run_evolved":
            return None

        evolved_name = arguments.get("tool_name")
        if not isinstance(evolved_name, str) or not evolved_name.strip():
            return tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                "tool_name is required for run_evolved",
            )

        evolved = self.registry.get_evolved(evolved_name.strip())
        if evolved is None:
            allowed = sorted(self.session.allowed_evolved or ())
            return tool_fail(
                name,
                ToolErrorCode.TOOL_NOT_FOUND,
                f"未知 evolved 工具：{evolved_name}",
                details={
                    "requested_tool": evolved_name.strip(),
                    "available_tools": allowed,
                    "hint": "tool_name 须出现在本会话 evolved 清单；只观察可用 builtin",
                },
            )

        if self.session.allowed_evolved is not None and evolved.name not in self.session.allowed_evolved:
            allowed = sorted(self.session.allowed_evolved)
            return tool_fail(
                name,
                ToolErrorCode.TOOL_NOT_FOUND,
                f"工具「{evolved.name}」不在本会话清单",
                details={
                    "requested_tool": evolved.name,
                    "available_tools": allowed,
                    "hint": (
                        "确认合适主题后重试，或改用清单内工具；"
                        "只观察可用 read_file / list_dir / grep"
                    ),
                },
            )

        tool_args = arguments.get("arguments")
        if tool_args is not None and not isinstance(tool_args, dict):
            return tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                "arguments must be an object",
            )

        dry_run = arguments.get("dry_run", False)
        if dry_run is not None and not isinstance(dry_run, bool):
            return tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                "dry_run must be a boolean",
            )

        scaffold_error = _validate_scaffold_evolved_call(self.session, evolved.name, arguments)
        if scaffold_error is not None:
            return scaffold_error

        if evolved.name in _INLINE_WRITE_TOOLS or evolved.name == "write_evolve":
            inline_error = _inline_body_guard(evolved.name, arguments)
            if inline_error is not None:
                return inline_error

        if evolved.name == "run_python":
            demo_error = _validate_run_python_scaffold_guard(self.session, arguments)
            if demo_error is not None:
                return demo_error

        project_error = _validate_project_mode_call(self.session, name, arguments)
        if project_error is not None:
            return project_error

        foreign_error = _validate_foreign_project_write(self.session, name, arguments)
        if foreign_error is not None:
            return foreign_error

        task_stop_error = _validate_task_stop_write(self.session, name, arguments)
        if task_stop_error is not None:
            return task_stop_error

        return None

    def _maybe_arm_task_stop(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
    ) -> None:
        """Arm task-stop after TASKS.md done-count increases (Phase 20 M1)."""
        if not result.ok or self.session.task_stop_armed:
            return
        if self.session.active_shell != "project" or not self.session.project_root.strip():
            return
        if tool_name != "run_evolved":
            return
        evolved_name = arguments.get("tool_name")
        if not isinstance(evolved_name, str) or evolved_name.strip() not in _WORKSPACE_WRITE_TOOLS:
            return
        data = result.data if isinstance(result.data, dict) else {}
        if data.get("dry_run"):
            return

        from project_mode import (
            extract_run_evolved_paths,
            is_project_tasks_path,
            project_id_from_root,
            read_task_stats,
        )

        touched_tasks = False
        for path in extract_run_evolved_paths(tool_name, arguments):
            if is_project_tasks_path(path, self.session.project_root):
                touched_tasks = True
                break
        if not touched_tasks:
            return

        pid = project_id_from_root(self.session.project_root)
        if not pid:
            return
        stats = read_task_stats(self.registry.agent_paths.workspace / pid / "TASKS.md")
        baseline = self.session.task_done_baseline
        if baseline is None:
            self.session.task_done_baseline = stats.done
            return
        if stats.done > baseline:
            self.session.task_stop_armed = True
            self._record_guard_event(
                "task_stop_armed",
                {
                    "ok": True,
                    "done": stats.done,
                    "baseline": baseline,
                    "project_root": self.session.project_root,
                },
            )

    def _maybe_log_validation_guard(self, error: ToolResult) -> None:
        if error.error is None or not isinstance(error.error.details, dict):
            return
        guard_type = error.error.details.get("guard_type")
        if not isinstance(guard_type, str) or not guard_type:
            return
        self._record_guard_event(guard_type, error)

    def _cross_session_read_target(self, path_arg: str) -> str | None:
        if self.session.session_dir is None:
            return None
        paths = self.registry.agent_paths
        current = conversation_id_from_session(self.session.session_dir)
        from shell_switch import cross_session_read_target

        return cross_session_read_target(paths, current, path_arg)

    def _needs_confirm(
        self,
        builtin: BuiltinTool,
        evolved: EvolvedTool | None,
        arguments: dict[str, Any],
        *,
        tool_name: str,
    ) -> bool:
        if tool_name in {"read_file", "grep"}:
            path_arg = arguments.get("path")
            if isinstance(path_arg, str) and self._cross_session_read_target(path_arg):
                return True
        if not builtin.confirm:
            return False
        if evolved is not None and not evolved.policy.confirm:
            return False
        if (
            evolved is not None
            and evolved.policy.workspace_only
            and self.session.workspace_evolved_approved
            and not _arguments_use_host_scope(arguments)
        ):
            return False
        return True

    def _resolve_evolved_target(self, tool_name: str, arguments: dict[str, Any]) -> EvolvedTool | None:
        if tool_name != "run_evolved":
            return None
        evolved_name = arguments.get("tool_name")
        if not isinstance(evolved_name, str):
            return None
        return self.registry.get_evolved(evolved_name.strip())

    def _ask_confirm(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        evolved: EvolvedTool | None,
    ) -> str:
        preview = build_confirm_preview(tool_name, arguments, evolved=evolved)
        allow_approve_all = (
            evolved is not None
            and evolved.policy.workspace_only
            and not _arguments_use_host_scope(arguments)
        )
        if self.confirm_fn is not None:
            return _normalize_confirm_choice(self.confirm_fn(preview, allow_approve_all), allow_approve_all)
        return _prompt_confirm_interactive(preview, allow_approve_all)

    def _approve_workspace_evolved(self, evolved: EvolvedTool | None) -> None:
        self.session.workspace_evolved_approved = True
        self._persist_workspace_approval()
        event_payload: dict[str, Any] = {}
        if evolved is not None:
            event_payload["tool_name"] = evolved.name
        self._emit_event(_EVENT_SESSION_WORKSPACE_APPROVED, event_payload)
        if self.evolve_log is not None:
            self.evolve_log.log_session_workspace_approved(
                conversation_id=conversation_id_from_session(self.session.session_dir),
                tool_name=evolved.name if evolved is not None else None,
            )

    def _log_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
        *,
        confirm: str,
        started: float,
    ) -> None:
        if self.evolve_log is None:
            return
        evolved_tool: str | None = None
        dry_run: bool | None = None
        if tool_name == "run_evolved":
            raw_name = arguments.get("tool_name")
            if isinstance(raw_name, str) and raw_name.strip():
                evolved_tool = raw_name.strip()
            raw_dry = arguments.get("dry_run", False)
            if isinstance(raw_dry, bool):
                dry_run = raw_dry
        logged = result
        if result.duration_ms == 0:
            logged = ToolResult(
                ok=result.ok,
                tool=result.tool,
                data=result.data,
                truncated=result.truncated,
                error=result.error,
                duration_ms=_elapsed_ms(started),
                output_path=result.output_path,
            )
        self.evolve_log.log_tool_call(
            tool=tool_name,
            arguments=arguments,
            result=logged,
            conversation_id=conversation_id_from_session(self.session.session_dir),
            confirm=confirm,
            evolved_tool=evolved_tool,
            dry_run=dry_run,
        )

    def _persist_workspace_approval(self) -> None:
        if self.session.session_dir is None:
            return
        self.session.session_dir.mkdir(parents=True, exist_ok=True)
        meta_path = self.session.session_dir / _META_FILENAME
        payload: dict[str, Any] = {}
        if meta_path.is_file():
            try:
                loaded = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    payload = loaded
            except (OSError, json.JSONDecodeError):
                payload = {}
        payload[_WORKSPACE_APPROVED_KEY] = True
        meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.on_event is not None:
            self.on_event(event_type, payload)

    def _maybe_record_memory_entity_used(self, arguments: dict[str, Any], result: ToolResult) -> None:
        path_value = arguments.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            data = result.data if isinstance(result.data, dict) else {}
            alt = data.get("path")
            if isinstance(alt, str) and alt.strip():
                path_value = alt
            else:
                return

        from governance.entity_usage import record_memory_entity_used

        record_memory_entity_used(
            paths=self.registry.agent_paths,
            path_value=path_value,
            evolve_log=self.evolve_log,
            session_dir=self.session.session_dir,
            conversation_id=conversation_id_from_session(self.session.session_dir),
        )

    def _execute_builtin(self, tool_name: str, arguments: dict[str, Any], *, started: float) -> ToolResult:
        runner = _BUILTIN_RUNNERS[tool_name]
        if tool_name == "run_evolved":
            return runner(
                arguments,
                registry=self.registry,
                paths=self.registry.agent_paths,
                allowed_tools=self.session.allowed_evolved,
                cancel_event=self.cancel_event,
            )
        return runner(arguments, paths=self.registry.agent_paths)

    def _maybe_spill_output(self, result: ToolResult) -> ToolResult:
        """Spill oversized structured results to session tool_outputs (§6.4)."""
        return maybe_spill_result(
            result,
            session_dir=self.session.session_dir,
            agent_paths=self.registry.agent_paths,
        )


def spill_threshold_chars() -> int:
    raw = os.environ.get("TOOL_OUTPUT_SPILL_CHARS", str(_DEFAULT_SPILL_CHARS))
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_SPILL_CHARS
    return max(1, value)


def preview_chars() -> int:
    raw = os.environ.get("TOOL_OUTPUT_PREVIEW_CHARS", str(_DEFAULT_PREVIEW_CHARS))
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_PREVIEW_CHARS
    return max(1, value)


def serialize_tool_data(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def maybe_spill_result(
    result: ToolResult,
    *,
    session_dir: Path | None,
    agent_paths: AgentPaths,
) -> ToolResult:
    """When serialized ``data`` exceeds spill threshold, write full text and return preview."""
    if not result.ok or result.data is None:
        return result

    serialized = serialize_tool_data(result.data)
    if len(serialized) <= spill_threshold_chars():
        return result

    preview = serialized[: preview_chars()]
    output_path: str | None = None

    if session_dir is not None:
        output_dir = session_dir / _TOOL_OUTPUTS_DIR
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{uuid.uuid4().hex}.txt"
        output_file.write_text(serialized, encoding="utf-8")
        output_path = agent_paths.to_agent_relative(output_file)

    return tool_ok(
        result.tool,
        {"preview": preview},
        truncated=True,
        duration_ms=result.duration_ms,
        output_path=output_path,
    )


def _tool_event_args(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "run_evolved":
        inner = run_evolved.coalesce_tool_arguments(arguments)
        payload: dict[str, Any] = {"tool_name": arguments.get("tool_name")}
        if isinstance(inner, dict) and inner:
            preview = dict(inner)
            b64 = preview.get("content_base64")
            if isinstance(b64, str) and len(b64) > 64:
                preview["content_base64"] = f"{b64[:48]}…({len(b64)} chars)"
            payload["arguments"] = preview
        if arguments.get("dry_run"):
            payload["dry_run"] = True
        return payload
    return dict(arguments)


def _tool_event_summary(
    tool_name: str,
    arguments: dict[str, Any],
    evolved: EvolvedTool | None,
) -> str:
    if tool_name == "run_evolved":
        evolved_name = arguments.get("tool_name")
        inner = run_evolved.coalesce_tool_arguments(arguments)
        path = inner.get("path") if isinstance(inner, dict) else None
        label = (
            evolved_name.strip()
            if isinstance(evolved_name, str) and evolved_name.strip()
            else "run_evolved"
        )
        if isinstance(path, str) and path.strip():
            return f"{label}: {path.strip()}"
        return label
    for key in ("path", "query", "url", "pattern"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return tool_name


def _tool_result_summary(result: ToolResult) -> str:
    if not result.ok and result.error:
        return result.error.message or "failed"
    data = result.data if isinstance(result.data, dict) else {}
    for key in ("path", "message", "preview"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            return text[:120] + ("…" if len(text) > 120 else "")
    return "ok" if result.ok else "failed"


def _arguments_use_host_scope(arguments: dict[str, Any]) -> bool:
    """True when nested run_evolved arguments reference ``host:`` URIs."""
    nested = arguments.get("arguments")
    if not isinstance(nested, dict):
        return False
    for value in nested.values():
        if isinstance(value, str) and value.strip().lower().startswith("host:"):
            return True
    return False


def build_confirm_preview(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    evolved: EvolvedTool | None = None,
) -> str:
    """Human-readable preview shown before confirm."""
    lines = [f"Tool: {tool_name}"]
    if tool_name == "run_evolved":
        evolved_name = arguments.get("tool_name")
        lines.append(f"Evolved: {evolved_name}")
        inner = run_evolved.coalesce_tool_arguments(arguments)
        if isinstance(inner, dict) and inner:
            preview_inner = dict(inner)
            b64 = preview_inner.get("content_base64")
            if isinstance(b64, str) and len(b64) > 64:
                preview_inner["content_base64"] = f"{b64[:48]}…({len(b64)} chars)"
            lines.append(f"Arguments: {json.dumps(preview_inner, ensure_ascii=False, sort_keys=True)}")
        if arguments.get("dry_run"):
            lines.append("Mode: dry_run")
        if evolved is not None:
            lines.append(f"Policy: workspace_only={evolved.policy.workspace_only}")
            if evolved.name == "host_copy_move" and isinstance(inner, dict):
                try:
                    from host_tools import host_path_confirm_line
                    from paths import AgentPaths

                    agent_paths = AgentPaths.discover()
                    source = inner.get("source")
                    dest = inner.get("dest")
                    if isinstance(source, str):
                        lines.append(host_path_confirm_line(agent_paths, "Source", source, write=False))
                    if isinstance(dest, str):
                        lines.append(host_path_confirm_line(agent_paths, "Dest", dest, write=True))
                except Exception as exc:
                    lines.append(f"Host paths: (preview failed: {exc})")
    else:
        if arguments:
            lines.append(f"Arguments: {json.dumps(arguments, ensure_ascii=False, sort_keys=True)}")
    if tool_name == "read_file":
        path_arg = arguments.get("path")
        if isinstance(path_arg, str):
            try:
                from shell_switch import cross_session_read_target
                from paths import AgentPaths

                paths = AgentPaths.discover()
                # preview only — no session dir required for message
                rel = path_arg.strip().replace("\\", "/")
                if rel.startswith("data/sessions/"):
                    parts = rel.split("/")
                    if len(parts) >= 3:
                        lines.append(f"Cross-session peek: session {parts[2]}")
            except Exception:
                pass
    if tool_name == "grep":
        path_arg = arguments.get("path")
        if isinstance(path_arg, str) and path_arg.strip().replace("\\", "/").startswith("data/sessions/"):
            lines.append("Cross-session peek (grep under data/sessions/)")
    return "\n".join(lines)


def _normalize_confirm_choice(raw: str, allow_approve_all: bool) -> str:
    choice = raw.strip().lower()
    if choice in {"y", "yes"}:
        return "y"
    if choice in {"n", "no"}:
        return "n"
    if allow_approve_all and choice in {"a", "all"}:
        return "a"
    raise ValueError(f"invalid confirm choice: {raw!r}")


def _prompt_confirm_interactive(preview: str, allow_approve_all: bool) -> str:
    print(preview)
    if allow_approve_all:
        prompt = "Confirm [y]es / [n]o / [a]llow workspace evolved this session? "
        valid = {"y", "n", "a"}
    else:
        prompt = "Confirm [y]es / [n]o? "
        valid = {"y", "n"}
    while True:
        try:
            raw = input(prompt)
        except EOFError:
            raw = "n"
        choice = raw.strip().lower()
        if choice in valid:
            return choice
        print(f"Please enter one of: {', '.join(sorted(valid))}")


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _demo() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        evolve = Path(tmp) / "evolve"
        paths = AgentPaths.discover()
        session_dir = paths.data / "sessions" / "_executor_demo"
        session_dir.mkdir(parents=True, exist_ok=True)
        meta_path = session_dir / _META_FILENAME
        if meta_path.is_file():
            meta_path.unlink()
        for old in (session_dir / _TOOL_OUTPUTS_DIR).glob("*.txt") if (session_dir / _TOOL_OUTPUTS_DIR).is_dir() else []:
            old.unlink()
        tool_ws = evolve / "tools" / "common" / "echo_json"
        tool_remote = evolve / "tools" / "coding" / "remote_echo"
        tool_ws.mkdir(parents=True)
        tool_remote.mkdir(parents=True)

        (tool_ws / "main.py").write_text(
            """import json
import sys

payload = json.load(sys.stdin)
print(json.dumps({"ok": True, "echo": payload.get("message", "")}))
""",
            encoding="utf-8",
        )
        (tool_remote / "main.py").write_text(
            """import json
import sys

payload = json.load(sys.stdin)
print(json.dumps({"ok": True, "remote": payload.get("message", "")}))
""",
            encoding="utf-8",
        )

        _write_manifest(tool_ws / "tool.toml", name="echo_json", workspace_only=True)
        _write_manifest(tool_remote / "tool.toml", name="remote_echo", workspace_only=False, topics=["coding"])

        registry = ToolRegistry.load(paths)
        from tools.registry import parse_tool_manifest

        echo_tool = parse_tool_manifest(tool_ws / "tool.toml", evolve_dir=evolve)
        remote_tool = parse_tool_manifest(tool_remote / "tool.toml", evolve_dir=evolve)
        registry = ToolRegistry(agent_paths=paths, evolved=[echo_tool, remote_tool])

        prompts: list[str] = []
        events: list[tuple[str, dict[str, Any]]] = []

        def scripted_confirm(preview: str, allow_approve_all: bool) -> str:
            prompts.append(preview)
            if not prompts_queue:
                return "n"
            choice = prompts_queue.pop(0)
            return _normalize_confirm_choice(choice, allow_approve_all)

        prompts_queue: list[str] = []
        log_path = Path(tmp) / "evolve_log.jsonl"

        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession(session_dir=session_dir, allowed_evolved={"echo_json", "remote_echo"}),
            confirm_fn=scripted_confirm,
            on_event=lambda event_type, payload: events.append((event_type, payload)),
            evolve_log=EvolveLog(log_path),
        )

        read = executor.run("read_file", {"path": "docs/MAP.md"})
        assert read.ok, read
        assert not prompts
        print("[PASS] read_file runs without confirm")

        prompts_queue = ["n"]
        rejected = executor.run(
            "run_evolved",
            {"tool_name": "echo_json", "arguments": {"message": "blocked"}},
        )
        assert not rejected.ok and rejected.error.code == ToolErrorCode.CONFIRM_REJECTED
        print("[PASS] confirm n rejects run_evolved")

        prompts_queue = ["y"]
        approved = executor.run(
            "run_evolved",
            {"tool_name": "echo_json", "arguments": {"message": "hello"}},
        )
        assert approved.ok and approved.data["echo"] == "hello"
        assert not executor.session.workspace_evolved_approved
        print("[PASS] confirm y executes once")

        prompts_queue = ["a"]
        approved_all = executor.run(
            "run_evolved",
            {"tool_name": "echo_json", "arguments": {"message": "batch"}},
        )
        assert approved_all.ok
        assert executor.session.workspace_evolved_approved
        meta = json.loads((session_dir / _META_FILENAME).read_text(encoding="utf-8"))
        assert meta[_WORKSPACE_APPROVED_KEY] is True
        assert any(evt[0] == _EVENT_SESSION_WORKSPACE_APPROVED for evt in events)
        print("[PASS] confirm a sets workspace_evolved_approved + event")

        prompts.clear()
        skipped = executor.run(
            "run_evolved",
            {"tool_name": "echo_json", "arguments": {"message": "no prompt"}},
        )
        assert skipped.ok and not prompts
        print("[PASS] workspace_only evolved skips confirm after a")

        prompts_queue = ["y"]
        still_confirm = executor.run(
            "run_evolved",
            {"tool_name": "remote_echo", "arguments": {"message": "remote"}},
        )
        assert still_confirm.ok and prompts
        print("[PASS] non-workspace_only evolved still confirms")

        reloaded = ToolExecutor(
            registry=registry,
            session=ExecutorSession.load(session_dir, allowed_evolved={"echo_json", "remote_echo"}),
            confirm_fn=scripted_confirm,
        )
        assert reloaded.session.workspace_evolved_approved
        prompts.clear()
        resumed = reloaded.run(
            "run_evolved",
            {"tool_name": "echo_json", "arguments": {"message": "resumed"}},
        )
        assert resumed.ok and not prompts
        print("[PASS] workspace approval restored from meta.json")

        # --- T-109: dry_run + spill ---
        tool_write = evolve / "tools" / "common" / "write_probe"
        tool_write.mkdir(parents=True)
        (tool_write / "main.py").write_text(
            """import json
import sys
from pathlib import Path

payload = json.load(sys.stdin)
target = Path(payload["path"])
if payload.get("dry_run"):
    print(json.dumps({"ok": True, "dry_run": True, "would_write": str(target)}))
else:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload.get("content", ""), encoding="utf-8")
    print(json.dumps({"ok": True, "written": str(target)}))
""",
            encoding="utf-8",
        )
        _write_manifest(tool_write / "tool.toml", name="write_probe", workspace_only=True)
        write_tool = parse_tool_manifest(tool_write / "tool.toml", evolve_dir=evolve)
        registry = ToolRegistry(agent_paths=paths, evolved=[echo_tool, remote_tool, write_tool])
        executor.registry = registry
        executor.session.allowed_evolved = {"echo_json", "remote_echo", "write_probe"}
        executor.session.workspace_evolved_approved = True

        out_file = paths.workspace / "_executor_t109_probe.txt"
        if out_file.exists():
            out_file.unlink()

        dry = executor.run(
            "run_evolved",
            {
                "tool_name": "write_probe",
                "arguments": {"path": str(out_file), "content": "live"},
                "dry_run": True,
            },
        )
        assert dry.ok and dry.data.get("dry_run") is True
        assert not out_file.exists()
        print("[PASS] dry_run does not write file")

        live = executor.run(
            "run_evolved",
            {
                "tool_name": "write_probe",
                "arguments": {"path": str(out_file), "content": "live"},
                "dry_run": False,
            },
        )
        assert live.ok and out_file.read_text(encoding="utf-8") == "live"
        print("[PASS] live run_evolved writes file")

        big_name = "_executor_t109_big.txt"
        big_path = paths.workspace / big_name
        big_path.write_text("line\n" * 3000, encoding="utf-8")
        try:
            big = executor.run("read_file", {"path": big_name})
            assert big.ok and big.truncated is True
            assert big.output_path is not None
            assert isinstance(big.data, dict) and "preview" in big.data
            assert len(big.data["preview"]) == preview_chars()
            spilled = session_dir / _TOOL_OUTPUTS_DIR / Path(big.output_path).name
            assert spilled.is_file()
            assert len(spilled.read_text(encoding="utf-8")) > spill_threshold_chars()
            print("[PASS] oversized builtin result spills to tool_outputs")
        finally:
            if big_path.exists():
                big_path.unlink()
            if out_file.exists():
                out_file.unlink()

        log_events = read_events(log_path)
        tool_calls = [event for event in log_events if event.get("event") == "tool_call"]
        assert len(tool_calls) >= 8
        assert any(event.get("confirm") == "n" and event.get("error_code") == ToolErrorCode.CONFIRM_REJECTED for event in tool_calls)
        assert any(event.get("event") == "session_workspace_approved" for event in log_events)
        assert all(event.get("conversation_id") == session_dir.name for event in log_events if "conversation_id" in event)
        print(f"[PASS] evolve_log records {len(tool_calls)} tool_call line(s)")

        # --- T-602a: entity_used L2 on read_file evolve/memories/** ---
        with tempfile.TemporaryDirectory() as t602_tmp:
            root = Path(t602_tmp)
            evolve_root = root / "evolve"
            evolve_root.mkdir()
            (evolve_root / "_index.core.toml").write_text('[[topic]]\nid = "workflow"\n', encoding="utf-8")
            mem_path = evolve_root / "memories" / "workflow" / "t602-demo.md"
            mem_path.parent.mkdir(parents=True)
            mem_path.write_text(
                "---\n"
                "id: t602-demo\n"
                "topics: [workflow]\n"
                "status: active\n"
                "summary: executor entity_used demo\n"
                "use_count: 0\n"
                "---\n\n"
                "## body\n",
                encoding="utf-8",
            )
            (root / "workspace").mkdir()
            (root / "data").mkdir()
            t602_session = root / "data" / "sessions" / "_t602"
            t602_session.mkdir(parents=True)
            t602_paths = AgentPaths.from_root(root)
            t602_log = Path(t602_tmp) / "t602_evolve_log.jsonl"
            t602_executor = ToolExecutor(
                registry=ToolRegistry.load(t602_paths),
                session=ExecutorSession(session_dir=t602_session),
                evolve_log=EvolveLog(t602_log),
            )
            read_mem = t602_executor.run(
                "read_file",
                {"path": "evolve/memories/workflow/t602-demo.md"},
            )
            assert read_mem.ok
            entity_events = [event for event in read_events(t602_log) if event.get("event") == "entity_used"]
            assert len(entity_events) == 1
            assert entity_events[0]["entity_id"] == "t602-demo"
            assert entity_events[0]["level"] == "L2"
            assert entity_events[0]["type"] == "memory"
            assert entity_events[0]["conversation_id"] == "_t602"
            updated = mem_path.read_text(encoding="utf-8")
            assert "use_count: 1" in updated
            assert "last_used_at:" in updated
            meta = json.loads((t602_session / "meta.json").read_text(encoding="utf-8"))
            assert meta["pending_feedback"][-1]["entity_id"] == "t602-demo"
            assert meta["pending_feedback"][-1]["level"] == "L2"
            print("[PASS] T-602a: read_file evolve/memories/** → entity_used + frontmatter + pending_feedback")

            before_entity_count = len(entity_events)
            index_read = t602_executor.run("read_file", {"path": "evolve/_index.core.toml"})
            assert index_read.ok
            after_entity_count = len(
                [event for event in read_events(t602_log) if event.get("event") == "entity_used"]
            )
            assert after_entity_count == before_entity_count
            print("[PASS] T-602a: non-memory read_file does not emit entity_used")

        ask_exec = ToolExecutor(
            registry=ToolRegistry.load(paths),
            session=ExecutorSession(session_dir=session_dir, turn_mode="ask"),
            evolve_log=EvolveLog(log_path),
        )
        ask_blocked = ask_exec.validate(
            "run_evolved",
            {"tool_name": "write_text", "arguments": {"path": "_x.txt", "content": "hi"}},
        )
        assert ask_blocked is not None
        assert not ask_blocked.ok
        assert ask_blocked.error is not None
        assert ask_blocked.error.code == ToolErrorCode.VALIDATION_ERROR
        assert "ask" in ask_blocked.error.message.casefold()
        agent_exec = ToolExecutor(
            registry=ToolRegistry.load(paths),
            session=ExecutorSession(session_dir=session_dir, turn_mode="agent"),
            evolve_log=EvolveLog(log_path),
        )
        agent_ok = agent_exec.validate(
            "run_evolved",
            {"tool_name": "write_text", "arguments": {"path": "_x.txt", "content": "hi"}},
        )
        assert agent_ok is None
        print("[PASS] T-702: ask mode blocks run_evolved; agent mode allows validate")

        blocked_scaffold = _validate_scaffold_evolved_call(
            ExecutorSession(scaffold_tool_turn=True),
            "write_text",
            {"tool_name": "write_text", "arguments": {"path": "stage/main.py", "content": "x"}},
        )
        assert blocked_scaffold is not None and not blocked_scaffold.ok
        print("[PASS] scaffold turn blocks write_text for main.py")

        staging_ok = _validate_scaffold_evolved_call(
            ExecutorSession(scaffold_tool_turn=True),
            "write_text",
            {"tool_name": "write_text", "arguments": {"path": "_staging.toml", "content": "[tool]\n"}},
        )
        assert staging_ok is None
        print("[PASS] scaffold turn allows write_text staging file")

        blocked_toml_plain = _validate_scaffold_evolved_call(
            ExecutorSession(),
            "write_evolve",
            {
                "tool_name": "write_evolve",
                "path": "evolve/tools/common/x/tool.toml",
                "content": "[tool]\nname=x\n",
                "arguments": {},
            },
        )
        assert blocked_toml_plain is not None and not blocked_toml_plain.ok
        print("[PASS] write_evolve tool.toml requires content_base64")

        blocked_multiline_py = _validate_scaffold_evolved_call(
            ExecutorSession(),
            "write_evolve",
            {
                "tool_name": "write_evolve",
                "arguments": {
                    "path": "evolve/tools/common/x/main.py",
                    "content": 'print("hi")\n',
                },
            },
        )
        assert blocked_multiline_py is not None and not blocked_multiline_py.ok
        print("[PASS] write_evolve multiline main.py requires content_base64")

        workspace_readme_ok = _validate_scaffold_evolved_call(
            ExecutorSession(),
            "write_text",
            {"tool_name": "write_text", "arguments": {"path": "project1/README.md", "content": "# Hi"}},
        )
        assert workspace_readme_ok is None
        print("[PASS] write_text allows workspace project README.md")

        blocked_evolve_scaffold = _validate_scaffold_evolved_call(
            ExecutorSession(),
            "write_text",
            {
                "tool_name": "write_text",
                "arguments": {"path": "evolve/tools/common/x/tool.toml", "content": "[tool]"},
            },
        )
        assert blocked_evolve_scaffold is not None
        print("[PASS] write_text blocked for evolve/tools scaffold path")

        import base64

        real_registry = ToolRegistry.load(paths)
        reload_exec = ToolExecutor(
            registry=real_registry,
            session=ExecutorSession(allowed_evolved={"write_evolve"}),
            confirm_fn=lambda _preview, _allow: "y",
            evolve_log=EvolveLog(log_path),
        )
        reloaded_names: list[str] = []
        reload_exec.on_registry_reloaded = lambda: reloaded_names.append("ok")
        demo_dir = paths.evolve / "tools" / "common" / "registry_reload_demo"
        try:
            assert reload_exec.registry.get_evolved("registry_reload_demo") is None
            demo_dir.mkdir(parents=True, exist_ok=True)
            (demo_dir / "main.py").write_text('print("reload")\n', encoding="utf-8")
            _write_manifest(demo_dir / "tool.toml", name="registry_reload_demo", workspace_only=False)
            manifest_text = (demo_dir / "tool.toml").read_text(encoding="utf-8")
            (demo_dir / "tool.toml").unlink()
            reload_result = reload_exec.run(
                "run_evolved",
                {
                    "tool_name": "write_evolve",
                    "path": "evolve/tools/common/registry_reload_demo/tool.toml",
                    "content_base64": base64.b64encode(manifest_text.encode("utf-8")).decode("ascii"),
                    "on_conflict": "overwrite",
                    "arguments": {},
                },
            )
            assert reload_result.ok, reload_result.error
            assert reload_exec.registry.get_evolved("registry_reload_demo") is not None
            assert reloaded_names == ["ok"]
            print("[PASS] write_evolve tool.toml reloads registry in-session")
        finally:
            if demo_dir.is_dir():
                for child in demo_dir.iterdir():
                    child.unlink()
                demo_dir.rmdir()

        # --- Phase 16 M1: inline max + scaffold demo + run_python guard ---
        prev_inline = os.environ.get("WRITE_INLINE_MAX_CHARS")
        os.environ["WRITE_INLINE_MAX_CHARS"] = "8192"
        inline_exec = ToolExecutor(
            registry=ToolRegistry.load(paths),
            session=ExecutorSession(session_dir=session_dir, turn_mode="agent"),
            evolve_log=EvolveLog(log_path),
        )
        too_big = inline_exec.validate(
            "run_evolved",
            {
                "tool_name": "write_text",
                "arguments": {"path": "_big.txt", "content": "x" * 9000},
            },
        )
        assert too_big is not None and not too_big.ok
        assert too_big.error is not None
        assert too_big.error.details is not None
        assert too_big.error.details.get("guard_type") == "inline_write_max"
        ok_small = inline_exec.validate(
            "run_evolved",
            {"tool_name": "write_text", "arguments": {"path": "_ok.txt", "content": "hi"}},
        )
        assert ok_small is None
        print("[PASS] T-1511: WRITE_INLINE_MAX_CHARS rejects oversized write_text")

        guard_exec = ToolExecutor(
            registry=ToolRegistry.load(paths),
            session=ExecutorSession(
                session_dir=session_dir,
                scaffold_tool_turn=True,
                active_shell="grow",
                turn_mode="agent",
            ),
            evolve_log=EvolveLog(log_path),
        )
        guard_exec.begin_execute_segment()
        demo_tool_dir = paths.evolve / "tools" / "common" / "guard_demo_tool"
        try:
            demo_tool_dir.mkdir(parents=True, exist_ok=True)
            (demo_tool_dir / "main.py").write_text(
                'if __name__ == "__main__":\n    import sys\n    print("[PASS] guard demo")\n',
                encoding="utf-8",
            )
            _write_manifest(demo_tool_dir / "tool.toml", name="guard_demo_tool", workspace_only=True)
            guard_exec.registry = ToolRegistry.load(paths)
            probe = guard_exec.run_scaffold_demo_probe("guard_demo_tool")
            assert probe.get("attempted") is True and probe.get("exit_code") == 0
            blocked_demo = guard_exec.validate(
                "run_evolved",
                {
                    "tool_name": "run_python",
                    "arguments": {
                        "path": "evolve/tools/common/guard_demo_tool/main.py",
                        "extra_args": ["demo"],
                    },
                },
            )
            assert blocked_demo is not None and not blocked_demo.ok
            assert blocked_demo.error is not None
            assert blocked_demo.error.details is not None
            assert blocked_demo.error.details.get("guard_type") == "run_python_demo_rejected"
            project_session = ExecutorSession(
                session_dir=session_dir,
                scaffold_tool_turn=True,
                active_shell="project",
                in_execute_segment=True,
                turn_mode="agent",
            )
            project_session.segment_scaffold_tools["guard_demo_tool"] = ScaffoldDemoRecord(
                tool_name="guard_demo_tool",
                tool_dir="evolve/tools/common/guard_demo_tool",
                demo_result={"attempted": True, "exit_code": 0},
            )
            project_ok = _validate_run_python_scaffold_guard(
                project_session,
                {
                    "tool_name": "run_python",
                    "arguments": {
                        "path": "evolve/tools/common/guard_demo_tool/main.py",
                        "extra_args": ["demo"],
                    },
                },
            )
            assert project_ok is None
            guard_events = [e for e in read_events(log_path) if e.get("event") == "guard"]
            assert any(e.get("guard_type") == "scaffold_demo_manual" for e in guard_events)
            print("[PASS] T-1514/T-1515/T-1516: scaffold demo + run_python reject + guard log")
        finally:
            if demo_tool_dir.is_dir():
                for child in demo_tool_dir.iterdir():
                    child.unlink()
                demo_tool_dir.rmdir()
            if prev_inline is None:
                os.environ.pop("WRITE_INLINE_MAX_CHARS", None)
            else:
                os.environ["WRITE_INLINE_MAX_CHARS"] = prev_inline

        print(to_json(approved, indent=2))


def _write_manifest(path: Path, *, name: str, workspace_only: bool, topics: list[str] | None = None) -> None:
    topic_list = topics or ["common"]
    topics_literal = ", ".join(f'"{topic}"' for topic in topic_list)
    path.write_text(
        f"""[tool]
name = "{name}"
description = "demo tool"
version = "1.0.0"
status = "active"
topics = [{topics_literal}]

[entry]
type = "python"
path = "main.py"

[schema.input]
type = "object"
required = ["message"]
[schema.input.properties.message]
type = "string"

[schema.output]
type = "object"

[policy]
confirm = true
dry_run_supported = true
workspace_only = {"true" if workspace_only else "false"}
timeout_sec = 30
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    _demo()
