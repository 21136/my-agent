"""ToolExecutor — confirm, dry_run, spill, evolve_log (TOOLS.md §6, TASKS T-108–T-110)."""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[1]
_TOOLS_DIR = Path(__file__).resolve().parent
if sys.path and Path(sys.path[0]).resolve() == _TOOLS_DIR.resolve():
    sys.path.pop(0)
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths, PathDeniedForWriteError, PathError, session_write_scope
from runtime_guards import auto_demo_on_write_evolve, write_inline_max_chars
from tool_proxies import rewrite_proxy_tool_call
from tools.builtin import (
    deliverable_review,
    evolution,
    explore,
    fetch_url,
    generate_image,
    show_images,
    glob_file_search,
    codebase_search,
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
    "glob_file_search": glob_file_search.run,
    "codebase_search": codebase_search.run,
    "web_search": web_search.run,
    "fetch_url": fetch_url.run,
    "show_images": show_images.run,
    "generate_image": generate_image.run,
    "run_evolved": run_evolved.run,
    "evolution": evolution.run,
    "propose_context_switch": propose_context_switch.run,
    "deliverable_review": deliverable_review.run,
    "explore": explore.run,
}

# Preview prefix: WsBridge waits on confirm queue without emitting confirm.request.
CONTEXT_SWITCH_CONFIRM_PREFIX = "__context_switch__:"

_META_FILENAME = "meta.json"
_WORKSPACE_APPROVED_KEY = "workspace_evolved_approved"
_EVENT_SESSION_WORKSPACE_APPROVED = "session_workspace_approved"
_DEFAULT_SPILL_CHARS = 32000
_DEFAULT_PREVIEW_CHARS = 8000
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
    evolution_id: str = ""
    host_run_id: str = ""
    evolution_authorization_id: str = ""
    evolution_stop_authorization_id: str = ""
    evolution_stop_instance_id: str = ""
    evolution_confirm_with_receipt: Callable[[str, bool], tuple[str, str]] | None = field(default=None, repr=False)
    allowed_evolved: set[str] | None = None
    blocked_tools: frozenset[str] = field(default_factory=frozenset)
    turn_mode: str = "agent"
    scaffold_tool_turn: bool = False
    active_shell: str = ""
    project_root: str = ""
    project_id: str = ""
    project_plan_status: str = ""
    project_workflow_stage: str = ""
    project_entry: str = ""
    project_active_task_id: str = ""
    project_delivery_profile: str = "solo"
    harness: str = "desktop"
    terminal_scope_kind: str = ""
    terminal_cwd: str = ""
    terminal_foreign_root: str = ""
    terminal_host_id: str = ""
    in_execute_segment: bool = False
    segment_scaffold_tools: dict[str, ScaffoldDemoRecord] = field(default_factory=dict)
    turn_evidence: list[dict[str, Any]] = field(default_factory=list)
    llm_retry_notice_emitted: bool = False
    # G14 / EXEC-RELIABILITY M0 — segment-scoped circuit breaker
    failure_streak_fp: str = ""
    failure_streak_count: int = 0
    circuit_open_fingerprints: set[str] = field(default_factory=set)
    circuit_just_opened: str = ""
    playbook_nudged: set[str] = field(default_factory=set)
    pending_playbook_id: str = ""
    # AGENT-HARNESS P5 — segment-wide failure budget (fingerprint-agnostic)
    segment_failure_count: int = 0
    segment_failure_budget_hit: bool = False
    segment_failure_budget_just_hit: bool = False
    # BUG-024 — repeat inline_write_max guard streak (independent of P5 / G14)
    inline_write_guard_streak: int = 0
    inline_write_guard_blocked: bool = False
    inline_write_guard_just_blocked: bool = False
    # G14 M2 — sidebar reliability snapshot
    last_playbook_id: str = ""
    last_failure_class: str = ""
    service_postcondition: str = ""  # "" | "ok" | "fail"
    postcondition_claim_blocked: bool = False
    deliverable_review_calls: int = 0
    explore_builtin_calls: int = 0
    explore_continue_used: bool = False
    terminal_plan_phase: str = ""
    subagent_overlay_pending: str | None = None
    git_review_turn: bool = False
    git_review_pending_paths: list[str] = field(default_factory=list)
    git_review_completed_paths: list[str] = field(default_factory=list)
    git_review_spill_paths: dict[str, list[str]] = field(default_factory=dict)
    git_review_nudge_count: int = 0
    git_review_must_continue: bool = False
    git_review_listed: bool = False
    last_review_verdict: str | None = None
    last_review_blockers_count: int = 0
    ordinary_visible_confirms_this_turn: int = 0
    # Request-scoped ordinary policy; never persisted in SessionMeta.
    ordinary_work_intent: str = "execute"
    ordinary_task_scope: str = ""
    write_scope_root: str = ""

    @classmethod
    def load(cls, session_dir: Path | None, *, allowed_evolved: set[str] | None = None) -> ExecutorSession:
        approved = False
        active_shell = ""
        project_root = ""
        project_id = ""
        project_plan_status = ""
        project_workflow_stage = ""
        project_entry = ""
        project_active_task_id = ""
        project_delivery_profile = "solo"
        harness = "desktop"
        terminal_scope_kind = ""
        terminal_cwd = ""
        terminal_foreign_root = ""
        terminal_host_id = ""
        write_scope_root = ""
        evolution_id = ""
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
                    project_id = str(payload.get("project_id", "") or "").strip()
                    project_plan_status = str(payload.get("project_plan_status", "") or "")
                    project_workflow_stage = str(payload.get("project_workflow_stage", "") or "")
                    project_entry = str(payload.get("project_entry", "") or "")
                    project_active_task_id = str(payload.get("project_active_task_id", "") or "")
                    from project_mode import normalize_delivery_profile

                    project_delivery_profile = normalize_delivery_profile(
                        payload.get("project_delivery_profile", "solo")
                    )
                    from session import normalize_harness, normalize_terminal_path_field

                    harness = normalize_harness(payload.get("harness", "desktop"))
                    terminal_scope_kind = str(
                        payload.get("terminal_scope_kind", "") or ""
                    ).strip()
                    terminal_cwd = normalize_terminal_path_field(
                        payload.get("terminal_cwd", "")
                    )
                    terminal_foreign_root = normalize_terminal_path_field(
                        payload.get("terminal_foreign_root", ""), relative=False
                    )
                    terminal_host_id = str(payload.get("terminal_host_id", "") or "").strip()
                    write_scope_root = str(payload.get("write_scope_root", "") or "").strip()
                    from session import normalize_evolution_id

                    evolution_id = normalize_evolution_id(payload.get("evolution_id", ""))
        return cls(
            session_dir=session_dir,
            workspace_evolved_approved=approved,
            evolution_id=evolution_id,
            allowed_evolved=allowed_evolved,
            active_shell=active_shell,
            project_root=project_root,
            project_id=project_id,
            project_plan_status=project_plan_status,
            project_workflow_stage=project_workflow_stage,
            project_entry=project_entry,
            project_active_task_id=project_active_task_id,
            project_delivery_profile=project_delivery_profile,
            harness=harness,
            terminal_scope_kind=terminal_scope_kind,
            terminal_cwd=terminal_cwd,
            terminal_foreign_root=terminal_foreign_root,
            terminal_host_id=terminal_host_id,
            write_scope_root=write_scope_root,
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
        self.project_id = str(payload.get("project_id", "") or "").strip()
        self.project_plan_status = str(payload.get("project_plan_status", "") or "")
        persisted_stage = str(payload.get("project_workflow_stage", "") or "")
        self.project_workflow_stage = persisted_stage
        self.project_entry = str(payload.get("project_entry", "") or "")
        self.project_active_task_id = str(payload.get("project_active_task_id", "") or "")
        from project_mode import normalize_delivery_profile

        self.project_delivery_profile = normalize_delivery_profile(
            payload.get("project_delivery_profile", "solo")
        )
        from session import normalize_harness, normalize_terminal_path_field

        self.harness = normalize_harness(payload.get("harness", "desktop"))
        self.terminal_scope_kind = str(payload.get("terminal_scope_kind", "") or "").strip()
        self.terminal_cwd = normalize_terminal_path_field(payload.get("terminal_cwd", ""))
        self.terminal_foreign_root = normalize_terminal_path_field(
            payload.get("terminal_foreign_root", ""), relative=False
        )
        self.terminal_host_id = str(payload.get("terminal_host_id", "") or "").strip()
        self.write_scope_root = str(payload.get("write_scope_root", "") or "").strip()
        from session import normalize_evolution_id

        refreshed_evolution_id = normalize_evolution_id(payload.get("evolution_id", ""))
        if refreshed_evolution_id != self.evolution_id:
            self.evolution_authorization_id = ""
        self.evolution_id = refreshed_evolution_id


def _executor_session_meta(session: ExecutorSession):
    """Rebuild SessionMeta fragment for terminal scope helpers."""
    from session import SessionMeta

    return SessionMeta(
        harness=session.harness,  # type: ignore[arg-type]
        terminal_scope_kind=session.terminal_scope_kind or "",  # type: ignore[arg-type]
        terminal_cwd=session.terminal_cwd,
        terminal_foreign_root=session.terminal_foreign_root,
        terminal_host_id=session.terminal_host_id,
        evolution_id=session.evolution_id,
    )


def _is_terminal_executor_session(session: ExecutorSession) -> bool:
    return session.harness == "terminal"


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


def _is_run_command_demo_call(inner: dict[str, Any]) -> bool:
    """Detect run_command that re-runs an evolve tool main.py demo."""
    command = inner.get("command")
    if not isinstance(command, str) or not command.strip():
        return False
    normalized = command.strip().replace("\\", "/")
    if "_staging" in normalized:
        return False
    match = re.search(
        r"evolve/tools/[a-z][a-z0-9_]*/([a-z][a-z0-9_]*)/main\.py",
        normalized,
        flags=re.IGNORECASE,
    )
    return match is not None


def _demo_tool_name_from_run_command(inner: dict[str, Any]) -> str | None:
    command = inner.get("command")
    if not isinstance(command, str):
        return None
    match = re.search(
        r"evolve/tools/[a-z][a-z0-9_]*/([a-z][a-z0-9_]*)/main\.py",
        command.replace("\\", "/"),
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None



def _validate_run_command_dep_repair_guard(
    outer_arguments: dict[str, Any],
) -> ToolResult | None:
    """Reject hand-rolled node_modules wipe; steer to repair_node_modules."""
    evolved = outer_arguments.get("tool_name")
    if not isinstance(evolved, str) or evolved.strip() != "run_command":
        return None
    inner = _merged_evolved_arguments(outer_arguments, "run_command")
    command = inner.get("command") if isinstance(inner.get("command"), str) else ""
    from run_command_policy import is_node_modules_wipe_command

    if not is_node_modules_wipe_command(command):
        return None
    return tool_fail(
        "run_evolved",
        ToolErrorCode.VALIDATION_ERROR,
        (
            "禁止用 run_command 手写删除 node_modules（rmdir/Remove-Item 等）。"
            "请改用 run_evolved → repair_node_modules（working_dir=前端目录），"
            "一次确认完成删除+重装，避免墙钟/确认超时误杀。"
        ),
        details={
            "guard_type": "node_modules_wipe",
            "hint": "repair_node_modules",
            "retry": True,
            "expected": {
                "tool_name": "repair_node_modules",
                "working_dir": "workspace/<id>/frontend",
            },
        },
    )


def _validate_run_python_scaffold_guard(
    session: ExecutorSession,
    outer_arguments: dict[str, Any],
) -> ToolResult | None:
    """Reject duplicate demo for tools already probed this segment (T-1515 / Phase 29)."""
    if not session.in_execute_segment:
        return None
    if session.active_shell == "project":
        return None
    if not session.scaffold_tool_turn and session.active_shell != "grow":
        return None

    evolved = outer_arguments.get("tool_name")
    if not isinstance(evolved, str):
        return None
    evolved_name = evolved.strip()

    tool_name: str | None = None
    if evolved_name == "run_python":
        inner = _merged_evolved_arguments(outer_arguments, "run_python")
        if not _is_run_python_demo_call(inner):
            return None
        path = inner.get("path")
        if not isinstance(path, str):
            return None
        tool_name = _tool_name_from_evolve_path(path)
    elif evolved_name == "run_command":
        inner = _merged_evolved_arguments(outer_arguments, "run_command")
        if not _is_run_command_demo_call(inner):
            return None
        tool_name = _demo_tool_name_from_run_command(inner)
    else:
        return None

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
            "请勿重复 run_python/run_command demo；请根据上方 demo 结果修复或继续交付。"
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
    if guard_type == "node_modules_wipe":
        return (
            "[guard] 勿手写删 node_modules；请用 repair_node_modules（一次确认删+装）"
        )
    if guard_type == "run_python_demo_rejected":
        tool_name = fields.get("tool_name", "?")
        return f"[guard] 已拒调 run_python demo · {tool_name}（本 segment 已有自动 demo 结果）"
    if guard_type == "exec_circuit":
        fp = fields.get("fingerprint", "")
        return f"[guard] 同类失败已熔断，禁止再调同一命令（{fp}）"
    if guard_type == "exec_circuit_opened":
        fp = fields.get("fingerprint", "")
        return f"[guard] 同类失败×{fields.get('count', '?')} 已熔断（{fp}）"
    if guard_type == "exec_failure_class":
        # AGENT-HARNESS P5: still logged + sidebar failure_class; no chat spam.
        return None
    if guard_type == "exec_playbook":
        return f"[guard] 剧本建议 · {fields.get('playbook_id', '?')}"
    message = fields.get("message")
    if isinstance(message, str) and message.strip():
        return f"[guard] {message}"
    return None


def _merged_evolved_arguments(arguments: dict[str, Any], evolved_name: str) -> dict[str, Any]:
    payload = dict(arguments)
    raw_name = payload.get("tool_name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        payload["tool_name"] = evolved_name
    return run_evolved.coalesce_tool_arguments(payload)


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


def _enrich_write_evolve_result(
    result: ToolResult,
    registry: Any,
    allowed_evolved: set[str] | None,
) -> None:
    """Add scope info to write_evolve result so the agent can act on it."""
    if not isinstance(result.data, dict):
        return
    written = result.data.get("written")
    if not isinstance(written, str):
        return
    tool_name = _tool_name_from_evolve_path(written)
    if tool_name is None:
        return
    tool = registry.get_evolved(tool_name)
    if tool is None:
        return
    scope = tool.scope
    topics = list(tool.topics) if tool.topics else []
    visible = allowed_evolved is None or tool_name in allowed_evolved
    result.data["tool_scope"] = scope
    result.data["tool_topics"] = topics
    result.data["tool_visible_now"] = visible
    if not visible and scope != "common" and "common" not in topics:
        result.data["tool_visible_hint"] = (
            f"此工具 scope={scope}，当前会话缺少主题「{scope}」。"
            f"执行「加主题 {scope}」或修改 tool.toml topics 添加「common」后即可使用。"
        )
    elif not visible and scope == "common":
        result.data["tool_visible_hint"] = (
            "工具 scope=common 但未出现在 allowed 清单，可能 status 非 active。"
            f"当前 status={tool.status}，需改为 active 后重试。"
        )


def _resolve_deliverable_review_target(
    paths: AgentPaths,
    session: ExecutorSession,
    arguments: dict[str, Any],
) -> tuple[str, str, Path]:
    """Resolve a review target to a registered workspace project, never an arbitrary root."""
    from external_project import validate_external_binding
    from project_mode import (
        ProjectModeError,
        is_project_dir,
        normalize_project_id,
        project_dir,
        project_id_from_root,
        project_root_rel,
    )

    raw_project_id = arguments.get("project_id")
    if raw_project_id is not None and not isinstance(raw_project_id, str):
        raise ProjectModeError("project_id must be a string")

    bound_project_id = (session.project_id or "").strip()
    if not bound_project_id and session.project_root.strip():
        bound_project_id = project_id_from_root(session.project_root)
    if bound_project_id:
        project_id = normalize_project_id(bound_project_id)
        if isinstance(raw_project_id, str):
            requested_project_id = normalize_project_id(raw_project_id)
            if requested_project_id != project_id:
                raise ProjectModeError("project_id must match the current project in this session")
    else:
        if not isinstance(raw_project_id, str) or not raw_project_id.strip():
            raise ProjectModeError("project_id is required when the session has no bound project")
        project_id = normalize_project_id(raw_project_id)

    resolved_project = project_dir(paths, project_id).resolve(strict=True)
    external_binding = validate_external_binding(paths, project_id)
    if external_binding is None:
        workspace_root = paths.workspace.resolve(strict=True)
        try:
            resolved_project.relative_to(workspace_root)
        except ValueError as exc:
            raise ProjectModeError("project_id must resolve to a project under workspace/") from exc
    if not resolved_project.is_dir() or not is_project_dir(resolved_project):
        raise ProjectModeError(f"workspace/{project_id} is not an existing project")

    return project_id, project_root_rel(project_id), resolved_project


def _validate_project_mode_call(
    session: ExecutorSession,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    agent_paths: AgentPaths | None = None,
) -> ToolResult | None:
    from project_mode import project_mode_block_reason

    ordinary_work_intent = str(getattr(session, "ordinary_work_intent", "execute") or "execute").strip() or "execute"
    reason = project_mode_block_reason(
        active_shell=session.active_shell,
        project_root=session.project_root,
        tool_name=tool_name,
        arguments=arguments,
        ordinary_work_intent=ordinary_work_intent,
    )
    if reason is None:
        return None
    code = ToolErrorCode.VALIDATION_ERROR
    details: dict[str, Any] = {
        "active_shell": session.active_shell,
        "ordinary_work_intent": ordinary_work_intent,
    }
    return tool_fail(
        tool_name,
        code,
        reason,
        details=details,
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


# PROJECT-MODE §0d E8: block archived repl bypass of package managers in project mode
_REPL_BUILD_BYPASS_RE = re.compile(
    r"(?is)"
    r"\b(?:npm|pnpm|yarn|mvn|gradlew?)\b"
    r"|npm\.cmd|pnpm\.cmd|yarn\.cmd|mvn\.cmd"
    r"|nodejs[/\\]+npm"
    r"|NPM_CONFIG_REGISTRY"
)


def _validate_project_repl_build_bypass(
    session: ExecutorSession,
    tool_name: str,
    arguments: dict[str, Any],
) -> ToolResult | None:
    """Refuse repl code that shells out to package managers / Maven (E8)."""
    if session.active_shell != "project":
        return None
    if tool_name != "run_evolved":
        return None
    evolved_name = arguments.get("tool_name")
    if not isinstance(evolved_name, str) or evolved_name.strip() != "repl":
        return None
    inner = arguments.get("arguments")
    if not isinstance(inner, dict):
        inner = {}
    # Also accept top-level code if coalesce left it there
    code = inner.get("code")
    if not isinstance(code, str):
        code = arguments.get("code") if isinstance(arguments.get("code"), str) else ""
    if not code or not _REPL_BUILD_BYPASS_RE.search(code):
        return None
    return tool_fail(
        tool_name,
        ToolErrorCode.VALIDATION_ERROR,
        (
            "项目模式下禁止用 repl 跑 npm/pnpm/yarn/mvn。"
            "请改用 run_evolved → run_command，"
            "参数 working_dir（可用 cwd 别名）指向 workspace/<id>/…"
        ),
        details={
            "guard_type": "project_repl_build_bypass",
            "retry": True,
            "hint": {
                "run_command": {
                    "working_dir": "workspace/<id>/frontend",
                    "command": "npm run build",
                },
            },
        },
    )



def _validate_terminal_plan_phase_block(
    session: ExecutorSession,
    tool_name: str,
    arguments: dict[str, Any],
) -> ToolResult | None:
    """TM-26: planning/replanning phases are read-only on the main executor."""
    if not _is_terminal_executor_session(session):
        return None
    phase = (session.terminal_plan_phase or "").strip()
    if phase not in {"planning", "replanning"}:
        return None
    if tool_name == "run_evolved":
        return tool_fail(
            tool_name,
            ToolErrorCode.PERMISSION_DENIED,
            "terminal planning phase forbids run_evolved (read-only planner)",
            details={"terminal_plan_phase": phase, "retry": False},
        )
    return None


def _validate_terminal_host_write(
    session: ExecutorSession,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    agent_paths: AgentPaths,
) -> ToolResult | None:
    if not _is_terminal_executor_session(session):
        return None
    evolved = arguments.get("tool_name") if tool_name == "run_evolved" else None
    evolved_name = evolved.strip() if isinstance(evolved, str) else ""
    from terminal_scope import terminal_host_write_block_reason

    reason = terminal_host_write_block_reason(
        _executor_session_meta(session),
        agent_paths,
        tool_name=tool_name,
        evolved_name=evolved_name,
    )
    if reason is None:
        return None
    return tool_fail(
        tool_name,
        ToolErrorCode.PERMISSION_DENIED,
        reason,
        details={"guard_type": "terminal_host_readonly", "retry": False},
    )



def _session_requires_ac_progress_binding(session: ExecutorSession) -> bool:
    """Standard projects keep AC+VERIFY binding; light only needs VERIFY."""
    pid = (getattr(session, "project_id", "") or "").strip()
    session_dir = getattr(session, "session_dir", None)
    if not pid or session_dir is None:
        return True
    try:
        from paths import AgentPaths
        from project_mode import read_project_template

        paths = AgentPaths.from_root(Path(session_dir).parents[2])
        return read_project_template(paths, pid) != "light"
    except Exception:
        return True



def _validate_exec_circuit(
    session: ExecutorSession,
    tool_name: str,
    arguments: dict[str, Any],
) -> ToolResult | None:
    """G14: block same tool+command fingerprint after consecutive failures."""
    from exec_reliability import call_fingerprint, circuit_blocks

    fp = call_fingerprint(tool_name, arguments)
    if not circuit_blocks(session, fp):
        return None
    return tool_fail(
        tool_name,
        ToolErrorCode.VALIDATION_ERROR,
        "同类失败已熔断：请换策略或停下来说明，勿重复同一命令。",
        details={
            "guard_type": "exec_circuit",
            "fingerprint": fp,
            "retry": False,
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


def _validate_session_write_scope(
    executor_session: ExecutorSession,
    agent_paths: AgentPaths,
    evolved_name: str,
    outer_arguments: dict[str, Any],
) -> ToolResult | None:
    scope = (executor_session.write_scope_root or "").strip()
    if not scope:
        return None
    inner = _merged_evolved_arguments(outer_arguments, evolved_name)
    path_keys = ("path", "file", "dest", "output", "working_dir", "cwd")
    paths_to_check: list[str] = []
    for key in path_keys:
        raw = inner.get(key)
        if isinstance(raw, str) and raw.strip():
            paths_to_check.append(raw.strip())
    if evolved_name == "write_evolve":
        for key in ("path", "main_path"):
            raw = inner.get(key)
            if isinstance(raw, str) and raw.strip():
                paths_to_check.append(raw.strip())
    for rel in paths_to_check:
        try:
            resolved = agent_paths.resolve_under_agent(rel, must_exist=False)
            agent_paths.assert_within_session_write_scope(
                resolved,
                raw=rel,
                scope_root=scope,
            )
        except PathDeniedForWriteError as exc:
            return tool_fail(
                "run_evolved",
                ToolErrorCode.PATH_DENIED,
                str(exc),
                details={"write_scope_root": scope, "path": rel},
            )
        except PathError as exc:
            return tool_fail(
                "run_evolved",
                ToolErrorCode.VALIDATION_ERROR,
                str(exc),
                details={"path": rel},
            )
    return None


def _ci_repair_blocks_delivery(session: Any, evolved_name: str, arguments: dict[str, Any]) -> bool:
    state = getattr(session, "_ci_repair_state", None)
    if not isinstance(state, dict) or str(state.get("status") or "") != "repair_allowed":
        return False
    if evolved_name in {"git_commit", "git_push"}:
        return True
    if evolved_name == "gh_pr" and str(arguments.get("action") or "").strip().lower() == "create":
        return True
    return False


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
    evolution_client: Any | None = field(default=None, repr=False)

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
        evolution_client: Any | None = None,
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
            evolution_client=evolution_client,
        )

    def reload_registry(self) -> None:
        """Rescan evolve/tools after write_evolve updates a tool manifest."""
        self.registry = ToolRegistry.load(self.registry.agent_paths)

    def begin_execute_segment(self) -> None:
        """Reset per-segment scaffold guard state (T-1515) and G14 circuit."""
        self.session.in_execute_segment = True
        self.session.segment_scaffold_tools.clear()
        from exec_reliability import clear_circuit_state

        clear_circuit_state(self.session)

    def begin_turn(self, *, reset_plan_cap: bool = True) -> None:
        """Reset per-turn guard state (task gates removed; TASKS.md is not read)."""
        from exec_reliability import clear_inline_write_guard

        clear_inline_write_guard(self.session)
        self.session.turn_evidence = []
        self.session.llm_retry_notice_emitted = False
        self.session.service_postcondition = ""
        self.session.postcondition_claim_blocked = False
        self.session.last_failure_class = ""
        self.session.last_playbook_id = ""
        del reset_plan_cap  # legacy kwarg (plan_partner cap is gone)
        self.session.deliverable_review_calls = 0
        self.session.explore_builtin_calls = 0
        self.session.explore_continue_used = False
        self.session.subagent_overlay_pending = None
        self.session.git_review_turn = False
        self.session.git_review_pending_paths = []
        self.session.git_review_completed_paths = []
        self.session.git_review_spill_paths = {}
        self.session.git_review_nudge_count = 0
        self.session.git_review_must_continue = False
        self.session.git_review_listed = False
        self.session.ordinary_visible_confirms_this_turn = 0
        self._emit_turn_evidence()

    def _emit_turn_evidence(self) -> None:
        """Phase 27 M1 + G14 M2 — armed task, evidence, reliability for sidebar."""
        items: list[dict[str, Any]] = []
        for entry in self.session.turn_evidence or []:
            if not isinstance(entry, dict):
                continue
            label = str(entry.get("evolved_name") or entry.get("tool") or "").strip()
            if not label:
                continue
            item = {"tool": label, "ok": bool(entry.get("ok"))}
            for key in (
                "task_id",
                "ac_ids",
                "verify_ids",
                "call_id",
                "paths",
                "test",
                "terminal_session_id",
                "terminal_state",
                "diff_summary",
            ):
                if entry.get(key):
                    item[key] = entry[key]
            items.append(item)

        if self.session.postcondition_claim_blocked:
            postcondition = "blocked"
        elif self.session.service_postcondition in {"ok", "fail"}:
            postcondition = self.session.service_postcondition
        else:
            postcondition = "none"

        open_fps = sorted(self.session.circuit_open_fingerprints or ())
        short_fps: list[str] = []
        for fp in open_fps[:5]:
            text = str(fp)
            if len(text) > 64:
                text = text[:61] + "…"
            short_fps.append(text)

        self._emit_event(
            "turn.evidence",
            {
                "armed_task_id": None,
                "armed_task_text": None,
                "items": items[-20:],
                "total_count": len(items),
                "omitted_count": max(0, len(items) - 20),
                "gate_notice": None,
                "reliability": {
                    "postcondition": postcondition,
                    "circuit_open": short_fps,
                    # D1: do not surface playbook ids in sidebar
                    "playbook_id": None,
                    "failure_class": (self.session.last_failure_class or "").strip() or None,
                },
            },
        )

    def _emit_services_state(self) -> None:
        """Phase 27 M1 — push managed service list after start/stop mutations."""
        try:
            from services_api import dispatch_services_message

            payload = dispatch_services_message(
                self.registry.agent_paths,
                {"type": "services.list"},
            )
            self._emit_event(
                "services.state",
                {"ok": True, "services": payload.get("services") or []},
            )
        except Exception:
            # Observability must not fail the tool call.
            return

    def _maybe_emit_services_state_after_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
    ) -> None:
        if tool_name != "run_evolved":
            return
        evolved = arguments.get("tool_name")
        if not isinstance(evolved, str):
            return
        evolved = evolved.strip()
        if evolved not in {"run_service", "dev_start"}:
            return
        # Refresh on any completed call (list/status also cheap; keeps sidebar truthful).
        _ = result
        self._emit_services_state()

    def _start_tool_progress_heartbeat(
        self,
        call_id: str,
        tool: str,
    ) -> tuple[threading.Event, threading.Thread]:
        """Emit tool.progress every few seconds while a tool is running (Phase 27 M1)."""
        stop = threading.Event()

        def _beat() -> None:
            n = 0
            while not stop.wait(5.0):
                n += 1
                secs = n * 5
                self._emit_event(
                    "tool.progress",
                    {
                        "call_id": call_id,
                        "tool": tool,
                        "text": f"仍在执行… {secs}s",
                        "phase": "running",
                        "elapsed_sec": secs,
                    },
                )

        thread = threading.Thread(target=_beat, name=f"tool-progress-{call_id[:8]}", daemon=True)
        thread.start()
        return stop, thread

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
        _enrich_write_evolve_result(result, self.registry, self.session.allowed_evolved)
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

    def _maybe_record_ci_repair_result(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
    ) -> None:
        if tool_name != "run_evolved" or not result.ok:
            return
        if str(arguments.get("tool_name") or "").strip() != "gh_actions":
            return
        inner = arguments.get("arguments") if isinstance(arguments.get("arguments"), dict) else {}
        if str(inner.get("action") or "").strip() != "repair_decision":
            return
        data = result.data if isinstance(result.data, dict) else {}
        state = data.get("state")
        if not isinstance(state, dict):
            return
        previous = getattr(self.session, "_ci_repair_state", None)
        self.session._ci_repair_state = dict(state)
        if not isinstance(previous, dict) or previous.get("repair_id") != state.get("repair_id") or previous.get("attempt") != state.get("attempt"):
            self.session._ci_repair_auto_turns = 0
        self._emit_event("ci.repair.decision", dict(state))

    def _maybe_record_self_change_result(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
    ) -> None:
        if tool_name != "run_evolved" or not result.ok:
            return
        if str(arguments.get("tool_name") or "").strip() != "self_change":
            return
        data = result.data if isinstance(result.data, dict) else {}
        transaction_id = str(data.get("transaction_id") or "").strip()
        if not transaction_id:
            return
        state = str(data.get("state") or "").strip()
        self.session.self_change_transaction_id = transaction_id
        self._emit_event(
            "self_change.state",
            {
                "transaction_id": transaction_id,
                "state": state,
                "action": str((arguments.get("arguments") or {}).get("action") or ""),
            },
        )

    def run(self, tool_name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        """Validate, optionally confirm, then execute a tool call."""
        started = time.perf_counter()
        name = tool_name.strip()
        args = dict(arguments or {})
        name, args = rewrite_proxy_tool_call(name, args)

        if self.cancel_event is not None and self.cancel_event.is_set():
            canceled = tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                "tool call cancelled",
                duration_ms=_elapsed_ms(started),
            )
            self._log_tool_call(name, args, canceled, confirm="cancelled", started=started)
            return canceled

        confirm_decision = "skipped"

        with session_write_scope(self.session.write_scope_root):
            return self._run_with_write_scope(
                name,
                args,
                started=started,
                confirm_decision=confirm_decision,
            )

    def _run_with_write_scope(
        self,
        name: str,
        args: dict[str, Any],
        *,
        started: float,
        confirm_decision: str,
    ) -> ToolResult:
        if self.session.evolution_id:
            from evolution.policy import evolution_tool_decision

            decision = evolution_tool_decision(self.session.evolution_id, name, args)
            if decision.get("decision") != "allow":
                denied = tool_fail(
                    name,
                    str(decision.get("code") or "permission_denied"),
                    str(decision.get("reason") or "tool is blocked in an evolution session"),
                    duration_ms=_elapsed_ms(started),
                )
                self._log_tool_call(name, args, denied, confirm=confirm_decision, started=started)
                return denied

        error = self.validate(name, args)
        if error is not None:
            self._maybe_log_validation_guard(error)
            self._log_tool_call(name, args, error, confirm=confirm_decision, started=started)
            return error

        builtin = self.registry.get_builtin(name)
        assert builtin is not None

        if name == "propose_context_switch":
            return self._run_propose_context_switch(args, started=started)

        if name == "deliverable_review":
            return self._run_deliverable_review(args, started=started)

        if name == "explore":
            return self._run_explore_builtin(args, started=started)

        if name == "codebase_search":
            return self._run_codebase_search(args, started=started)

        prepared_image_reference = None
        image_confirm_context = None
        if name == "generate_image" and (
            str(args.get("reference_context") or "").strip()
            or str(args.get("reference_id") or "").strip()
        ):
            from image_references import find_image_references

            search = find_image_references(
                str(args.get("reference_context") or ""),
                paths=self.registry.agent_paths,
                current_session_dir=self.session.session_dir,
                reference_id=(str(args["reference_id"]) if args.get("reference_id") else None),
            )
            if search.status == "ambiguous":
                result = tool_ok(
                    name,
                    {
                        "status": "reference_selection_required",
                        "candidates": [candidate.to_public_dict() for candidate in search.candidates],
                    },
                    duration_ms=_elapsed_ms(started),
                )
                self._log_tool_call(name, args, result, confirm="skipped", started=started)
                return result
            if search.status != "selected" or search.selected is None:
                result = tool_fail(
                    name,
                    "reference_not_found",
                    "No accessible local chat image matched the requested context; no image was sent.",
                    duration_ms=_elapsed_ms(started),
                )
                self._log_tool_call(name, args, result, confirm="skipped", started=started)
                return result
            prepared_image_reference = search.selected
            source_title = " ".join(prepared_image_reference.session_title.split())[:80]
            source_context = " / ".join(prepared_image_reference.context.split())[:500]
            image_confirm_context = (
                f"Reference: {prepared_image_reference.filename} from '{source_title}'.\n"
                f"Session updated: {prepared_image_reference.updated_at or 'unknown'}\n"
                f"Conversation context: {source_context}"
            )

        evolved_target = self._resolve_evolved_target(name, args) if name == "run_evolved" else None
        if self._needs_confirm(builtin, evolved_target, args, tool_name=name):
            if self._ordinary_confirm_is_covered(builtin, evolved_target, args, tool_name=name):
                confirm_decision = "ordinary_budget"
            else:
                confirm_decision = self._ask_confirm(
                    name,
                    args,
                    evolved_target,
                    extra_context=image_confirm_context,
                )
                if (
                    confirm_decision in {"y", "a"}
                    and not self._ordinary_confirm_is_destructive(
                        builtin, evolved_target, args, tool_name=name
                    )
                ):
                    self.session.ordinary_visible_confirms_this_turn = min(
                        int(getattr(self.session, "ordinary_visible_confirms_this_turn", 0) or 0)
                        + 1,
                        99,
                    )
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
        card_tool = _card_tool_name(name, args)
        self._emit_event(
            "tool.start",
            {
                "tool": card_tool,
                "call_id": call_id,
                "summary": _tool_event_summary(name, args, evolved_target),
                "arguments": _tool_event_args(name, args),
            },
        )
        # C6: always emit tool.end even when execute raises (BUG-011).
        # Phase 27 M1: heartbeat progress while the tool runs (incl. long run_service waits).
        stop_hb, hb_thread = self._start_tool_progress_heartbeat(call_id, name)
        result: ToolResult
        try:
            if name == "generate_image":
                executed = self._execute_builtin(
                    name,
                    args,
                    started=started,
                    prepared_image_reference=prepared_image_reference,
                )
            else:
                executed = self._execute_builtin(name, args, started=started)
            result = _attach_desktop_inline_display_hint(self._maybe_spill_output(executed))
        except Exception as exc:
            result = tool_fail(
                name,
                "execution_error",
                f"tool execution failed: {exc}",
                duration_ms=_elapsed_ms(started),
            )
        finally:
            stop_hb.set()
            hb_thread.join(timeout=0.2)
        self._maybe_record_self_change_result(name, args, result)
        self._maybe_record_ci_repair_result(name, args, result)
        end_payload: dict[str, Any] = {
            "tool": card_tool,
            "call_id": call_id,
            "ok": result.ok,
            "summary": _tool_result_summary(result),
            "output_path": result.output_path,
        }
        end_note = _tool_end_note(result)
        if end_note:
            end_payload["note"] = end_note
        logs_tail = _result_logs_tail(result)
        if logs_tail:
            end_payload["logs_tail"] = logs_tail
        data = result.data if isinstance(result.data, dict) else {}
        path_hint = (
            data.get("path")
            or data.get("written")
            or data.get("would_write")
            or data.get("source")
            or data.get("dest")
        )
        if isinstance(path_hint, str) and path_hint.strip():
            end_payload["path"] = path_hint.strip()
        diff = data.get("unified_diff") or data.get("diff")
        if isinstance(diff, str) and diff.strip():
            end_payload["diff"] = diff if len(diff) <= 8000 else diff[:7999] + "…"
        display_paths = _tool_end_display_paths(data)
        if display_paths:
            end_payload["display_paths"] = display_paths
        self._emit_event("tool.end", end_payload)
        if name == "read_file" and result.ok and end_note != "absent":
            self._maybe_record_memory_entity_used(args, result)
        self._record_turn_evidence(name, args, result, call_id=call_id)
        self._maybe_emit_services_state_after_tool(name, args, result)
        self._update_exec_circuit(name, args, result)
        self._emit_turn_evidence()
        if result.ok:
            self._maybe_reload_registry_after_write_evolve(name, args, result)
        self._log_tool_call(name, args, result, confirm=confirm_decision, started=started)
        return result

    def _update_exec_circuit(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
    ) -> None:
        """G14: classify failure, queue playbooks, open circuit at threshold."""
        from exec_reliability import (
            call_fingerprint,
            circuit_threshold,
            classify_failure,
            is_circuit_countable_failure,
            queue_playbook_nudge,
            record_circuit_failure,
            record_circuit_success,
            record_segment_failure,
            should_count_segment_failure,
        )

        insight = classify_failure(result)
        countable = is_circuit_countable_failure(result)
        if result.ok:
            self._maybe_clear_inline_write_guard_streak(tool_name, arguments)
        self._maybe_update_service_postcondition(tool_name, arguments, result, insight)

        if insight.failure_class not in {"A"} or countable:
            if countable or insight.failure_class in {"B", "C", "D", "E", "F"}:
                self.session.last_failure_class = insight.failure_class
                self._record_guard_event(
                    "exec_failure_class",
                    {
                        "ok": False,
                        "failure_class": insight.failure_class,
                        "playbook_id": insight.playbook_id,
                        "preview": insight.blob_preview,
                        "tool": tool_name,
                    },
                )
        # D1: playbook auto-nudge abolished — queue is no-op unless legacy env on.
        if insight.playbook_id and queue_playbook_nudge(self.session, insight.playbook_id):
            self.session.last_playbook_id = insight.playbook_id
            self._record_guard_event(
                "exec_playbook",
                {
                    "ok": False,
                    "playbook_id": insight.playbook_id,
                    "failure_class": insight.failure_class,
                },
            )

        fp = call_fingerprint(tool_name, arguments)
        if not countable:
            if result.ok and insight.failure_class == "A":
                record_circuit_success(self.session)
            return
        if should_count_segment_failure(result, self.session):
            record_segment_failure(self.session)
        opened = record_circuit_failure(self.session, fp)
        if opened:
            self._record_guard_event(
                "exec_circuit_opened",
                {
                    "ok": False,
                    "fingerprint": fp,
                    "count": circuit_threshold(),
                    "failure_class": insight.failure_class,
                    "playbook_id": insight.playbook_id,
                },
            )

    def _maybe_update_service_postcondition(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
        insight: Any,
    ) -> None:
        """Track run_service ready+alive for sidebar postcondition (G14 M2)."""
        evolved = ""
        if tool_name == "run_evolved":
            raw = arguments.get("tool_name")
            evolved = raw.strip() if isinstance(raw, str) else ""
        if evolved != "run_service":
            return
        data = result.data if isinstance(result.data, dict) else {}
        action = str(data.get("action") or "").strip().lower()
        if action and action not in {"start", "restart", "status", "wait_ready", ""}:
            return
        state = data.get("state") if isinstance(data.get("state"), dict) else {}
        ready = data.get("ready")
        alive = state.get("alive") if isinstance(state, dict) else None
        if ready is True and alive is True:
            self.session.service_postcondition = "ok"
            return
        if ready is False or alive is False or result.ok is False:
            self.session.service_postcondition = "fail"
            return
        if insight.failure_class in {"B", "E"} and insight.playbook_id:
            self.session.service_postcondition = "fail"

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
                template=(
                    str(arguments["template"])
                    if isinstance(arguments.get("template"), str)
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


    def _run_deliverable_review(
        self,
        arguments: dict[str, Any],
        *,
        started: float,
    ) -> ToolResult:
        """Spawn deliverable review subagent (Phase 47 · DELIVERABLE-REVIEW §4)."""
        from session import Session
        from subagent import SubagentRunner, deliverable_review_max_per_turn

        name = "deliverable_review"
        task = str(arguments.get("task") or "").strip()
        if not task:
            result = tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                "task is required",
                duration_ms=_elapsed_ms(started),
            )
            self._log_tool_call(name, arguments, result, confirm="skipped", started=started)
            return result

        from project_mode import ProjectModeError

        try:
            pid, review_project_root, _resolved_project = _resolve_deliverable_review_target(
                self.registry.agent_paths,
                self.session,
                arguments,
            )
        except (ProjectModeError, OSError) as exc:
            result = tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                str(exc),
                duration_ms=_elapsed_ms(started),
            )
            self._log_tool_call(name, arguments, result, confirm="skipped", started=started)
            return result

        scope = str(arguments.get("scope") or "full").strip() or "full"
        if scope not in {"full", "phase", "files"}:
            scope = "full"
        phase_hint = str(arguments.get("phase_hint") or "").strip()
        paths_raw = arguments.get("paths")
        paths: list[str] = []
        if isinstance(paths_raw, list):
            paths = [str(p).strip() for p in paths_raw if str(p).strip()]
        if scope == "files" and not paths:
            result = tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                "paths required when scope=files",
                duration_ms=_elapsed_ms(started),
            )
            self._log_tool_call(name, arguments, result, confirm="skipped", started=started)
            return result
        if scope == "phase" and not phase_hint:
            result = tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                "phase_hint required when scope=phase",
                duration_ms=_elapsed_ms(started),
            )
            self._log_tool_call(name, arguments, result, confirm="skipped", started=started)
            return result

        facts_raw = arguments.get("facts")
        facts: dict[str, Any] = facts_raw if isinstance(facts_raw, dict) else {}

        from subagent import parse_max_rounds_argument

        try:
            max_rounds = parse_max_rounds_argument(arguments.get("max_rounds"))
        except ValueError as exc:
            result = tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                str(exc),
                duration_ms=_elapsed_ms(started),
            )
            self._log_tool_call(name, arguments, result, confirm="skipped", started=started)
            return result

        cap = deliverable_review_max_per_turn()
        if self.session.deliverable_review_calls >= cap:
            result = tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                f"deliverable_review 每回合最多 {cap} 次",
                duration_ms=_elapsed_ms(started),
                details={"deliverable_review_limit": cap},
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

        paths_agent = self.registry.agent_paths
        session = Session.load(paths_agent, self.session.session_dir.name)
        session = copy.copy(session)
        session.meta = copy.deepcopy(session.meta)
        session.meta.project_id = pid
        session.meta.project_root = review_project_root
        preview = task if len(task) <= 120 else task[:117] + "…"
        call_id = str(uuid.uuid4())
        self._emit_event(
            "review.subagent.start",
            {"task_preview": preview, "call_id": call_id},
        )
        self._emit_event(
            "tool.start",
            {
                "tool": name,
                "call_id": call_id,
                "summary": preview,
                "arguments": {"task": preview, "scope": scope},
            },
        )

        try:
            runner = SubagentRunner(paths=paths_agent, evolve_log=self.evolve_log)
            sub_result = runner.run_deliverable_review(
                task,
                session=session,
                scope=scope,
                phase_hint=phase_hint,
                paths=paths,
                facts=facts,
                max_rounds=max_rounds,
                cancel_event=self.cancel_event,
            )
        except Exception as exc:
            result = tool_fail(
                name,
                "execution_error",
                f"deliverable review subagent failed: {exc}",
                duration_ms=_elapsed_ms(started),
            )
            self._emit_event(
                "review.subagent.done",
                {
                    "summary": result.error.message if result.error else str(exc),
                    "verdict": None,
                    "ok": False,
                    "call_id": call_id,
                },
            )
            self._emit_event(
                "tool.end",
                {
                    "tool": name,
                    "call_id": call_id,
                    "ok": False,
                    "summary": _tool_result_summary(result),
                },
            )
            self._log_tool_call(name, arguments, result, confirm="skipped", started=started)
            return result

        self.session.deliverable_review_calls += 1
        verdict = sub_result.verdict or "warn"
        from subagent import (
            count_review_blockers,
            format_subagent_overlay,
            review_summary_preview,
        )

        self.session.subagent_overlay_pending = format_subagent_overlay(sub_result)
        blockers_count = count_review_blockers(sub_result.summary, verdict=verdict)
        self.session.last_review_verdict = verdict
        self.session.last_review_blockers_count = blockers_count
        session.last_review_verdict = verdict
        session.last_review_blockers_count = blockers_count
        done_payload = {
            "summary": sub_result.summary,
            "summary_preview": review_summary_preview(sub_result.summary),
            "verdict": verdict,
            "blockers_count": blockers_count,
            "ok": True,
            "call_id": call_id,
            "paths_cited": list(sub_result.paths_cited),
        }
        self._emit_event("review.subagent.done", done_payload)

        from project_api import project_state_payload

        self._emit_event("project.state", project_state_payload(session, paths_agent))

        result = tool_ok(
            name,
            {
                "summary": sub_result.summary,
                "verdict": verdict,
                "paths_cited": list(sub_result.paths_cited),
                "truncated": sub_result.truncated,
            },
            duration_ms=_elapsed_ms(started),
        )
        self._emit_event(
            "tool.end",
            {
                "tool": name,
                "call_id": call_id,
                "ok": True,
                "summary": _tool_result_summary(result),
            },
        )
        self._log_tool_call(name, arguments, result, confirm="skipped", started=started)
        return result

    def _run_codebase_search(
        self,
        arguments: dict[str, Any],
        *,
        started: float,
    ) -> ToolResult:
        """Semantic/BM25 codebase search scoped to project_root (Pack 5 · T-5501)."""
        from tools.builtin import codebase_search

        name = "codebase_search"
        if _is_terminal_executor_session(self.session):
            from terminal_scope import terminal_codebase_search_root

            project_root = terminal_codebase_search_root(
                _executor_session_meta(self.session),
                self.registry.agent_paths,
            )
            pid = ""
        else:
            pid = (self.session.project_id or "").strip()
            project_root = self.session.project_root
            if not pid and project_root.strip():
                from project_mode import project_id_from_root

                pid = project_id_from_root(project_root) or ""
        result = codebase_search.run(
            arguments,
            paths=self.registry.agent_paths,
            project_root=project_root,
            project_id=pid,
        )
        self._log_tool_call(name, arguments, result, confirm="skipped", started=started)
        return result

    def _run_explore_builtin(
        self,
        arguments: dict[str, Any],
        *,
        started: float,
    ) -> ToolResult:
        """Parent-invoked read-only explore subagent (Phase 48 · T-4802)."""
        from session import Session
        from subagent import SubagentRunner, explore_builtin_max_per_turn, format_subagent_overlay

        name = "explore"
        task = str(arguments.get("task") or "").strip()
        if not task:
            result = tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                "task is required",
                duration_ms=_elapsed_ms(started),
            )
            self._log_tool_call(name, arguments, result, confirm="skipped", started=started)
            return result

        from subagent import parse_max_rounds_argument

        try:
            max_rounds = parse_max_rounds_argument(arguments.get("max_rounds"))
        except ValueError as exc:
            result = tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                str(exc),
                duration_ms=_elapsed_ms(started),
            )
            self._log_tool_call(name, arguments, result, confirm="skipped", started=started)
            return result

        cap = explore_builtin_max_per_turn()
        if self.session.explore_builtin_calls >= cap:
            result = tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                f"explore 每回合最多 {cap} 次",
                duration_ms=_elapsed_ms(started),
                details={"explore_limit": cap},
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

        paths_agent = self.registry.agent_paths
        session = Session.load(paths_agent, self.session.session_dir.name)
        preview = task if len(task) <= 120 else task[:117] + "…"
        call_id = str(uuid.uuid4())
        self._emit_event(
            "tool.start",
            {
                "tool": name,
                "call_id": call_id,
                "summary": preview,
                "arguments": {"task": preview},
            },
        )

        try:
            from llm_client import LLMClient

            runner = SubagentRunner(paths=paths_agent, evolve_log=self.evolve_log)
            sub_result, did_continue = runner.run_explore_with_continue(
                task,
                session=session,
                llm=LLMClient(),
                confirm_fn=self.confirm_fn,
                cancel_event=self.cancel_event,
                max_rounds=max_rounds,
                continue_already_used=self.session.explore_continue_used,
            )
            if did_continue:
                self.session.explore_continue_used = True
            if sub_result.hit_cap:
                self._emit_event(
                    "turn.notice",
                    {
                        "level": "info",
                        "text": (
                            "[内核] explore 已达本轮上限"
                            + ("，已续跑一轮；" if did_continue else "；")
                            + "主 Agent 续查…"
                        ),
                    },
                )
        except Exception as exc:
            result = tool_fail(
                name,
                "execution_error",
                f"explore subagent failed: {exc}",
                duration_ms=_elapsed_ms(started),
            )
            self._emit_event(
                "tool.end",
                {
                    "tool": name,
                    "call_id": call_id,
                    "ok": False,
                    "summary": _tool_result_summary(result),
                },
            )
            self._log_tool_call(name, arguments, result, confirm="skipped", started=started)
            return result

        self.session.explore_builtin_calls += 1
        self.session.subagent_overlay_pending = format_subagent_overlay(sub_result)
        result = tool_ok(
            name,
            {
                "summary": sub_result.summary,
                "paths_cited": list(sub_result.paths_cited),
                "truncated": sub_result.truncated,
                "tool_rounds": sub_result.tool_rounds,
            },
            duration_ms=_elapsed_ms(started),
        )
        self._emit_event(
            "tool.end",
            {
                "tool": name,
                "call_id": call_id,
                "ok": True,
                "summary": _tool_result_summary(result),
            },
        )
        self._log_tool_call(name, arguments, result, confirm="skipped", started=started)
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

        if name == "run_evolved":
            evolved_name = str(arguments.get("tool_name") or "").strip()
            inner_arguments = arguments.get("arguments") if isinstance(arguments.get("arguments"), dict) else {}
            if _ci_repair_blocks_delivery(self.session, evolved_name, inner_arguments):
                return tool_fail(
                    name,
                    ToolErrorCode.VALIDATION_ERROR,
                    "CI repair turn blocks commit, push, and PR creation until repair is resolved",
                    details={"ci_repair_status": "repair_allowed", "evolved_name": evolved_name},
                )

        if name == "generate_image":
            prompt = arguments.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                return tool_fail(name, ToolErrorCode.VALIDATION_ERROR, "prompt is required")
            reference_context = arguments.get("reference_context")
            if reference_context is not None and (
                not isinstance(reference_context, str) or len(reference_context) > 500
            ):
                return tool_fail(
                    name,
                    ToolErrorCode.VALIDATION_ERROR,
                    "reference_context must be a string of at most 500 characters",
                )
            reference_id = arguments.get("reference_id")
            if reference_id is not None and (
                not isinstance(reference_id, str) or not reference_id.strip()
            ):
                return tool_fail(
                    name,
                    ToolErrorCode.VALIDATION_ERROR,
                    "reference_id must be a non-empty string",
                )

        if name in self.session.blocked_tools:
            return tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                f"tool {name!r} is not available in this context (explore subagent)",
                details={"blocked_tools": sorted(self.session.blocked_tools)},
            )

        circuit_error = _validate_exec_circuit(self.session, name, arguments)
        if circuit_error is not None:
            return circuit_error

        if name == "run_evolved" and self.session.turn_mode == "ask":
            return tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                "run_evolved is disabled in ask mode (只聊); say 动手 to enable writes",
                details={"turn_mode": "ask"},
            )

        if name == "generate_image" and self.session.turn_mode == "ask":
            return tool_fail(
                name,
                ToolErrorCode.PERMISSION_DENIED,
                "generate_image is disabled in ask mode (只聊); switch to 动手 (agent) first",
                details={"turn_mode": "ask"},
            )
        if name == "generate_image" and str(self.session.ordinary_work_intent or "").strip() == "read_only":
            return tool_fail(
                name,
                ToolErrorCode.PERMISSION_DENIED,
                "generate_image blocked: this turn is explicitly read-only (e.g. 只读/只查看)",
                details={
                    "turn_mode": self.session.turn_mode,
                    "ordinary_work_intent": "read_only",
                },
            )

        plan_block = _validate_terminal_plan_phase_block(self.session, name, arguments)
        if plan_block is not None:
            return plan_block

        if name == "deliverable_review":
            task = arguments.get("task")
            if not isinstance(task, str) or not task.strip():
                return tool_fail(
                    name,
                    ToolErrorCode.VALIDATION_ERROR,
                    "task is required",
                    details={"retry": True},
                )
            from project_mode import ProjectModeError

            try:
                review_project_id, review_project_root, resolved_project = _resolve_deliverable_review_target(
                    self.registry.agent_paths,
                    self.session,
                    arguments,
                )
            except (ProjectModeError, OSError) as exc:
                return tool_fail(
                    name,
                    ToolErrorCode.VALIDATION_ERROR,
                    str(exc),
                )
            scope = str(arguments.get("scope") or "full").strip() or "full"
            if scope == "files":
                paths_raw = arguments.get("paths")
                if not isinstance(paths_raw, list) or not any(
                    str(p).strip() for p in paths_raw
                ):
                    return tool_fail(
                        name,
                        ToolErrorCode.VALIDATION_ERROR,
                        "paths required when scope=files",
                    )
                from paths import PathError

                project_prefix = f"workspace/{review_project_id}"
                for raw_path in paths_raw:
                    path = str(raw_path).strip().replace("\\", "/")
                    if not path:
                        continue
                    if path.startswith(("/", "host:")) or re.match(r"^[A-Za-z]:", path):
                        return tool_fail(
                            name,
                            ToolErrorCode.VALIDATION_ERROR,
                            f"path must be relative to project_root: {path}",
                        )
                    if path == project_prefix or path.startswith(f"{project_prefix}/"):
                        agent_path = path
                    elif path == review_project_id or path.startswith(f"{review_project_id}/"):
                        agent_path = f"workspace/{path}"
                    elif path.startswith("workspace/"):
                        return tool_fail(
                            name,
                            ToolErrorCode.VALIDATION_ERROR,
                            f"path must be under project_root: {path}",
                        )
                    else:
                        agent_path = f"{review_project_root}/{path}"
                    try:
                        resolved_path = self.registry.agent_paths.resolve_under_agent(
                            agent_path,
                            must_exist=False,
                        ).resolve()
                        resolved_path.relative_to(resolved_project)
                    except (OSError, PathError, TypeError, ValueError):
                        return tool_fail(
                            name,
                            ToolErrorCode.VALIDATION_ERROR,
                            f"path must resolve under project_root: {path}",
                        )
            if scope == "phase":
                phase_hint = str(arguments.get("phase_hint") or "").strip()
                if not phase_hint:
                    return tool_fail(
                        name,
                        ToolErrorCode.VALIDATION_ERROR,
                        "phase_hint required when scope=phase",
                    )
            return None

        if name == "explore":
            task = arguments.get("task")
            if not isinstance(task, str) or not task.strip():
                return tool_fail(
                    name,
                    ToolErrorCode.VALIDATION_ERROR,
                    "task is required",
                    details={"retry": True},
                )
            return None

        if name != "run_evolved":
            return None

        evolved_name = arguments.get("tool_name")
        if not isinstance(evolved_name, str) or not evolved_name.strip():
            allowed = sorted(self.session.allowed_evolved or ())
            return tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                "tool_name is required for run_evolved",
                details={
                    "expected": {"tool_name": "string (必需)", "arguments": "object (可选)", "dry_run": "boolean (可选)"},
                    "received_keys": sorted(arguments.keys()),
                    "available_tools": allowed,
                    "retry": True,
                },
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
                    "retry": True,
                },
            )

        if evolved.status != "active":
            return tool_fail(
                name,
                ToolErrorCode.TOOL_NOT_FOUND,
                f"工具「{evolved.name}」status={evolved.status}，不可执行（须 active）",
                details={
                    "requested_tool": evolved.name,
                    "status": evolved.status,
                    "hint": "已归档/非 active 工具请改用 run_command 或 run_service",
                    "retry": True,
                },
            )

        # §0e F1 belt-and-suspenders: project-bound sessions always admit scope=project tools
        # even if allowed_evolved was computed before shell/root sync.
        self._ensure_project_scope_tools_allowed()

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

        scope_error = _validate_session_write_scope(
            self.session,
            self.registry.agent_paths,
            evolved.name,
            arguments,
        )
        if scope_error is not None:
            return scope_error

        if evolved.name in _INLINE_WRITE_TOOLS or evolved.name == "write_evolve":
            inline_error = _inline_body_guard(evolved.name, arguments)
            if inline_error is not None:
                return inline_error

        if evolved.name == "run_command":
            wipe_error = _validate_run_command_dep_repair_guard(arguments)
            if wipe_error is not None:
                return wipe_error

        if evolved.name in {"run_python", "run_command"}:
            demo_error = _validate_run_python_scaffold_guard(self.session, arguments)
            if demo_error is not None:
                return demo_error

        if _is_terminal_executor_session(self.session):
            host_block = _validate_terminal_host_write(
                self.session,
                name,
                arguments,
                agent_paths=self.registry.agent_paths,
            )
            if host_block is not None:
                return host_block
            return None

        foreign_error = _validate_foreign_project_write(self.session, name, arguments)
        if foreign_error is not None:
            return foreign_error

        project_error = _validate_project_mode_call(
            self.session,
            name,
            arguments,
            agent_paths=self.registry.agent_paths,
        )
        if project_error is not None:
            return project_error

        repl_bypass = _validate_project_repl_build_bypass(self.session, name, arguments)
        if repl_bypass is not None:
            return repl_bypass

        return None

    def _ensure_project_scope_tools_allowed(self) -> None:
        """Admit active scope=project tools when this executor session is project-bound (F1)."""
        if self.session.allowed_evolved is None:
            return
        bound = (self.session.active_shell or "").strip() == "project" or bool(
            (self.session.project_root or "").strip()
        )
        if not bound:
            return
        extra = {
            tool.name
            for tool in self.registry.evolved()
            if tool.status == "active" and tool.scope == "project"
        }
        if not extra:
            return
        current = set(self.session.allowed_evolved)
        if extra <= current:
            return
        self.session.allowed_evolved = current | extra


    def _record_turn_evidence(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
        *,
        call_id: str = "",
    ) -> None:
        """Append a bounded, structured outcome for the current-run summary."""
        from project_mode import extract_run_evolved_paths

        evolved = ""
        if tool_name == "run_evolved":
            raw = arguments.get("tool_name")
            if isinstance(raw, str):
                evolved = raw.strip()
        data = result.data if isinstance(result.data, dict) else {}
        error_details = result.error.details if result.error and isinstance(result.error.details, dict) else {}
        if not data and error_details:
            data = error_details
        # dry_run / background escalate are not real outcomes.
        if data.get("dry_run") is True:
            return
        if data.get("background") is True or data.get("escalated") is True:
            return

        def clip(value: Any, limit: int = 240) -> str:
            if not isinstance(value, str):
                return ""
            return re.sub(r"\s+", " ", value).strip()[:limit]

        raw_paths = list(extract_run_evolved_paths(tool_name, arguments))
        for key in ("paths", "untracked_paths"):
            values = data.get(key)
            if isinstance(values, list):
                raw_paths.extend(value for value in values if isinstance(value, str))
        status_lines = data.get("status_lines")
        if isinstance(status_lines, list):
            for line in status_lines:
                if isinstance(line, str) and len(line) > 3:
                    raw_paths.append(line[3:].split(" -> ")[-1].strip())
        clean_paths = list(dict.fromkeys(clip(path, 240) for path in raw_paths if clip(path, 240)))[:8]
        evidence: dict[str, Any] = {
            "tool": tool_name,
            "evolved_name": evolved or tool_name,
            "ok": bool(result.ok),
            "paths": clean_paths,
        }
        if call_id.strip():
            evidence["call_id"] = call_id.strip()[:128]

        if evolved == "structured_test":
            status = data.get("status")
            if isinstance(status, str) and status in {"passed", "failed", "blocked", "timeout"}:
                failures: list[dict[str, Any]] = []
                raw_failures = data.get("failed")
                if isinstance(raw_failures, list):
                    for failure in raw_failures:
                        if not isinstance(failure, dict):
                            continue
                        item: dict[str, Any] = {}
                        file = clip(failure.get("file"), 200)
                        test = clip(failure.get("test"), 160)
                        line = failure.get("line")
                        if file:
                            item["file"] = file
                        if isinstance(line, int) and not isinstance(line, bool) and line > 0:
                            item["line"] = line
                        if test:
                            item["test"] = test
                        failures.append(item)
                        if len(failures) >= 3:
                            break
                test_summary: dict[str, Any] = {"status": status, "failed": failures}
                suite = clip(data.get("suite"), 80)
                duration = data.get("duration_ms")
                if suite:
                    test_summary["suite"] = suite
                if isinstance(duration, int) and not isinstance(duration, bool) and duration >= 0:
                    test_summary["duration_ms"] = duration
                reason = clip(data.get("reason"), 80)
                if reason:
                    test_summary["reason"] = reason
                evidence["test"] = test_summary

        if evolved == "interactive_terminal":
            session_id = clip(data.get("session_id"), 128)
            terminal_state = clip(data.get("state"), 32)
            if session_id:
                evidence["terminal_session_id"] = session_id
            if terminal_state:
                evidence["terminal_state"] = terminal_state

        if evolved in {"git_snapshot", "git_diff"}:
            diff_summary = clip(data.get("diff_stat") or data.get("summary"), 320)
            if not diff_summary and data.get("empty") is True:
                diff_summary = "未发现差异"
            elif not diff_summary and isinstance(data.get("empty"), bool):
                diff_summary = "发现差异" if not data["empty"] else "未发现差异"
            elif not diff_summary and evolved in {"git_snapshot", "git_diff"}:
                diff_summary = "发现变更" if clean_paths else "未发现差异"
            if diff_summary:
                evidence["diff_summary"] = diff_summary

        self.session.turn_evidence.append(evidence)


    def _maybe_log_validation_guard(self, error: ToolResult) -> None:
        if error.error is None or not isinstance(error.error.details, dict):
            return
        guard_type = error.error.details.get("guard_type")
        if not isinstance(guard_type, str) or not guard_type:
            return
        if guard_type == "inline_write_max":
            from exec_reliability import inline_write_guard_max, record_inline_write_guard_failure

            record_inline_write_guard_failure(self.session)
            if (
                int(getattr(self.session, "inline_write_guard_streak", 0) or 0)
                >= inline_write_guard_max()
            ):
                error.error.details["retry"] = False
        self._record_guard_event(guard_type, error)

    def _maybe_clear_inline_write_guard_streak(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        """BUG-024 IT-98b: successful write clears repeat-inline streak."""
        if tool_name != "run_evolved":
            return
        evolved = arguments.get("tool_name")
        if not isinstance(evolved, str):
            return
        if evolved not in {"write_text", "append_text", "write_evolve"}:
            return
        inner = _merged_evolved_arguments(arguments, evolved)
        sources: list[dict[str, Any]] = []
        if isinstance(inner, dict) and inner:
            sources.append(inner)
        if evolved == "write_evolve":
            sources.append(arguments)
        for source in sources:
            if _has_workspace_path(source):
                from exec_reliability import clear_inline_write_guard_streak

                clear_inline_write_guard_streak(self.session)
                return
        if evolved in _INLINE_WRITE_TOOLS:
            from exec_reliability import clear_inline_write_guard_streak

            clear_inline_write_guard_streak(self.session)

    def _cross_session_read_target(self, path_arg: str) -> str | None:
        if self.session.session_dir is None:
            return None
        paths = self.registry.agent_paths
        current = conversation_id_from_session(self.session.session_dir)

        from tools.builtin.read_file import resolve_read_path

        if not isinstance(path_arg, str) or not path_arg.strip():
            return None
        try:
            resolved = resolve_read_path(paths, path_arg.strip())
        except (FileNotFoundError, TypeError, ValueError):
            return None
        try:
            rel = paths.to_agent_relative(resolved).replace("\\", "/")
        except Exception:
            return None
        if not rel.startswith("data/sessions/"):
            return None
        parts = rel.split("/")
        if len(parts) < 3:
            return None
        target_id = parts[2].strip()
        current_cid = current.strip()
        if not target_id or target_id == current_cid:
            return None
        return target_id

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
        # run_service: only start/stop/restart need confirm; status/logs/wait/list are read-only.
        if evolved is not None and evolved.name == "run_service":
            from tools.builtin import run_evolved as _run_evolved_mod

            inner = _run_evolved_mod.coalesce_tool_arguments(arguments)
            action = str(inner.get("action") or "").strip().lower()
            if action in {"status", "logs", "wait", "list", "port_status"}:
                return False
            # kill_port / start / stop / restart fall through → confirm
        # http_request (D2): loopback GET/HEAD skip confirm; else require confirm.
        if evolved is not None and evolved.name == "http_request":
            from tools.builtin import run_evolved as _run_evolved_mod

            inner = _run_evolved_mod.coalesce_tool_arguments(arguments)
            if not _http_request_needs_confirm(inner):
                return False
        # browser_open (Phase 33 F1): loopback skip; dry_run skip; external confirm.
        if evolved is not None and evolved.name == "browser_open":
            from tools.builtin import run_evolved as _run_evolved_mod

            inner = _run_evolved_mod.coalesce_tool_arguments(arguments)
            if bool(inner.get("dry_run")):
                return False
            if not _browser_open_needs_confirm(inner):
                return False
        # dev_start dry_run is planning only — skip confirm.
        if evolved is not None and evolved.name == "dev_start":
            from tools.builtin import run_evolved as _run_evolved_mod

            inner = _run_evolved_mod.coalesce_tool_arguments(arguments)
            if bool(inner.get("dry_run")):
                return False
        # git_commit / git_push dry_run is planning only — skip confirm.
        if evolved is not None and evolved.name in {"git_commit", "git_push"}:
            from tools.builtin import run_evolved as _run_evolved_mod

            inner = _run_evolved_mod.coalesce_tool_arguments(arguments)
            if bool(inner.get("dry_run")):
                return False
        # git_snapshot / git_diff are read-only — never confirm.
        if evolved is not None and evolved.name in {"git_snapshot", "git_diff"}:
            return False
        # git_branch: list + dry_run skip confirm; create/switch confirm.
        if evolved is not None and evolved.name == "git_branch":
            from tools.builtin import run_evolved as _run_evolved_mod

            inner = _run_evolved_mod.coalesce_tool_arguments(arguments)
            action = str(inner.get("action") or "").strip().lower()
            if action == "list" or bool(inner.get("dry_run")):
                return False
        # gh_pr: view/checks/list + dry_run skip; create confirms.
        if evolved is not None and evolved.name == "gh_pr":
            from tools.builtin import run_evolved as _run_evolved_mod

            inner = _run_evolved_mod.coalesce_tool_arguments(arguments)
            action = str(inner.get("action") or "").strip().lower()
            if action in {"view", "checks", "list"} or bool(inner.get("dry_run")):
                return False
        # search_replace: default dry_run preview skips confirm; write confirms.
        if evolved is not None and evolved.name == "search_replace":
            from tools.builtin import run_evolved as _run_evolved_mod

            inner = _run_evolved_mod.coalesce_tool_arguments(arguments)
            raw_dry = inner.get("dry_run", arguments.get("dry_run"))
            if raw_dry is None or raw_dry is True:
                return False
        # local_preview: dry_run skip; loopback open without start_command like browser_open.
        if evolved is not None and evolved.name == "local_preview":
            from tools.builtin import run_evolved as _run_evolved_mod

            inner = _run_evolved_mod.coalesce_tool_arguments(arguments)
            if bool(inner.get("dry_run")):
                return False
            start_cmd = inner.get("start_command")
            has_start = isinstance(start_cmd, str) and start_cmd.strip()
            if not has_start and not _browser_open_needs_confirm({"url": inner.get("url")}):
                return False

        # git_restore defaults to a preview; only an explicit dry_run=false may discard.
        if evolved is not None and evolved.name == "git_restore":
            from tools.builtin import run_evolved as _run_evolved_mod

            inner = _run_evolved_mod.coalesce_tool_arguments(arguments)
            raw_dry_run = inner.get("dry_run", arguments.get("dry_run"))
            if raw_dry_run is None or raw_dry_run is True:
                return False
        # db_query: readonly SELECT path skips confirm; write=true requires confirm.
        if evolved is not None and evolved.name == "db_query":
            from tools.builtin import run_evolved as _run_evolved_mod

            inner = _run_evolved_mod.coalesce_tool_arguments(arguments)
            if bool(inner.get("write")):
                pass  # fall through → confirm
            else:
                return False
        # pip_install dry_run skips confirm (tool may be archived; keep for copies).
        if evolved is not None and evolved.name == "pip_install":
            from tools.builtin import run_evolved as _run_evolved_mod

            inner = _run_evolved_mod.coalesce_tool_arguments(arguments)
            if bool(inner.get("dry_run")):
                return False
        # run_command (Phase 29 A2): layered confirm; never skip via approve_all when required.
        if evolved is not None and evolved.name == "run_command":
            from tools.builtin import run_evolved as _run_evolved_mod
            from run_command_policy import run_command_requires_confirm

            inner = _run_evolved_mod.coalesce_tool_arguments(arguments)
            if bool(inner.get("dry_run")) or bool(arguments.get("dry_run")):
                return False
            command = inner.get("command") if isinstance(inner.get("command"), str) else ""
            working = ""
            for key in ("working_dir", "cwd"):
                raw = inner.get(key)
                if isinstance(raw, str) and raw.strip():
                    working = raw.strip()
                    break
            if _is_terminal_executor_session(self.session):
                from terminal_scope import terminal_run_command_requires_confirm

                needs, _reason = terminal_run_command_requires_confirm(
                    working_dir=working,
                    meta=_executor_session_meta(self.session),
                    agent_paths=self.registry.agent_paths,
                )
                if not needs:
                    return False
                return True
            needs, _reason = run_command_requires_confirm(
                command=command,
                working_dir=working,
                project_root=self.session.project_root or "",
                background=bool(inner.get("background")),
                agent_paths=self.registry.agent_paths,
            )
            if not needs:
                return False
            return True
        # write_text / patch_file (Phase 42 H): layered confirm in project shell.
        if evolved is not None and evolved.name in {"write_text", "patch_file"}:
            from tools.builtin import run_evolved as _run_evolved_mod

            inner = _run_evolved_mod.coalesce_tool_arguments(arguments)
            if bool(inner.get("dry_run")) or bool(arguments.get("dry_run")):
                return False
            needs, _reason = resolve_write_confirm(
                evolved_name=evolved.name,
                arguments=arguments,
                session=self.session,
                agent_paths=self.registry.agent_paths,
            )
            if not needs:
                return False
            return True
        if not builtin.confirm:
            return False
        if evolved is not None and not evolved.policy.confirm:
            return False
        if (
            evolved is not None
            and evolved.policy.allow_approve_all
            and self.session.workspace_evolved_approved
            and not _arguments_use_host_scope(arguments)
        ):
            return False
        return True

    def _ordinary_confirm_is_destructive(
        self,
        builtin: BuiltinTool,
        evolved: EvolvedTool | None,
        arguments: dict[str, Any],
        *,
        tool_name: str,
    ) -> bool:
        """True for danger/network/host/outside-project work that must always confirm."""
        if evolved is None:
            return bool(getattr(builtin, "confirm", False))
        name = evolved.name
        if name in {
            "git_push",
            "git_clone",
            "git_restore",
            "gh_pr",
            "pip_install",
            "http_request",
            "browser_open",
            "local_preview",
        }:
            return True
        if _arguments_use_host_scope(arguments):
            return True
        from tools.builtin import run_evolved as _run_evolved_mod

        inner = _run_evolved_mod.coalesce_tool_arguments(arguments)
        if name == "run_command":
            from run_command_policy import classify_run_command, run_command_requires_confirm

            command = inner.get("command") if isinstance(inner.get("command"), str) else ""
            kind = classify_run_command(command)
            if kind in {"danger", "install", "network"}:
                return True
            working = ""
            for key in ("working_dir", "cwd"):
                raw = inner.get(key)
                if isinstance(raw, str) and raw.strip():
                    working = raw.strip()
                    break
            _needs, reason = run_command_requires_confirm(
                command=command,
                working_dir=working,
                project_root=self.session.project_root or "",
                background=bool(inner.get("background")),
                agent_paths=self.registry.agent_paths,
            )
            return reason in {"danger", "install", "network", "background", "outside_project"}
        if name in {"write_text", "patch_file", "search_replace"}:
            from write_policy import is_sensitive_write_path, path_under_project

            raw_path = inner.get("path")
            path = raw_path.strip() if isinstance(raw_path, str) else ""
            if not path:
                return True
            if is_sensitive_write_path(path):
                return True
            if not path_under_project(path, self.session.project_root or ""):
                return True
        return False

    def _ordinary_confirm_is_covered(
        self,
        builtin: BuiltinTool,
        evolved: EvolvedTool | None,
        arguments: dict[str, Any],
        *,
        tool_name: str,
    ) -> bool:
        """Ordinary mode: at most one user-visible confirm per turn for safe work."""
        if self.session.active_shell != "project":
            return False
        if evolved is not None and evolved.name == "write_evolve":
            return False
        if self._ordinary_confirm_is_destructive(
            builtin, evolved, arguments, tool_name=tool_name
        ):
            return False
        shown = int(getattr(self.session, "ordinary_visible_confirms_this_turn", 0) or 0)
        return shown >= 1


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
        *,
        extra_context: str | None = None,
    ) -> str:
        preview = build_confirm_preview(
            tool_name,
            arguments,
            evolved=evolved,
            project_root=self.session.project_root or "",
            active_shell=self.session.active_shell or "",
            agent_paths=self.registry.agent_paths,
            extra_context=extra_context,
        )
        allow_approve_all = (
            evolved is not None
            and evolved.policy.allow_approve_all
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

    def _execute_builtin(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        started: float,
        prepared_image_reference: Any | None = None,
    ) -> ToolResult:
        runner = _BUILTIN_RUNNERS[tool_name]
        if tool_name == "evolution":
            if self.evolution_client is None:
                return tool_fail(tool_name, "permission_denied", "evolution control client is not attached to this trusted Host", duration_ms=_elapsed_ms(started))
            return runner(arguments, client=self.evolution_client, session=self.session)
        if tool_name == "run_evolved":
            return runner(
                arguments,
                registry=self.registry,
                paths=self.registry.agent_paths,
                allowed_tools=self.session.allowed_evolved,
                cancel_event=self.cancel_event,
                current_project_id=self.session.project_id,
            )
        if tool_name == "generate_image":
            conversation_id = self.session.session_dir.name if self.session.session_dir else ""
            return runner(
                arguments,
                paths=self.registry.agent_paths,
                prepared_reference=prepared_image_reference,
                project_root=self.session.project_root or "",
                conversation_id=conversation_id,
            )
        if tool_name == "show_images":
            return runner(
                arguments,
                paths=self.registry.agent_paths,
                current_session_dir=self.session.session_dir,
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


_GIT_DIFF_SPILL_HINT = (
    "整次 git 输出未全量入上下文。请 git_diff name_status/stat_only 列文件，"
    "再 paths 逐文件 git_diff，或 read_file spill 全文；勿仅凭 preview 拒评或停手。"
)


def _inner_git_tool_from_data(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    if "status_lines" in data and "diff_stat" in data:
        return "git_snapshot"
    if "diff" in data and "empty" in data and "name_status" in data:
        return "git_diff"
    if "diff" in data and "empty" in data and not data.get("name_status"):
        return "git_diff"
    inner = data.get("tool_name")
    if isinstance(inner, str) and inner.strip() in {"git_diff", "git_snapshot"}:
        return inner.strip()
    return None


def _build_success_spill_hint(result: ToolResult, output_path: str) -> str:
    read_line = f"Full tool result: read_file {output_path}"
    git_tool = _inner_git_tool_from_data(result.data)
    data = result.data if isinstance(result.data, dict) else {}
    if git_tool or data.get("truncated") or data.get("diff_truncated"):
        if git_tool or "diff" in data or "diff_stat" in data:
            return f"{read_line}。{_GIT_DIFF_SPILL_HINT}"
    return read_line


def maybe_spill_result(
    result: ToolResult,
    *,
    session_dir: Path | None,
    agent_paths: AgentPaths,
) -> ToolResult:
    """When serialized payload exceeds spill threshold, write full text and return preview.

    Success: spill oversized ``data`` (TOOLS.md §6.4).
    Failure: spill entire result envelope when too large for LLM diet (AGENT-HARNESS P4).
    """
    threshold = spill_threshold_chars()
    preview_limit = preview_chars()

    if result.ok:
        if result.data is None:
            return result
        if result.tool == "read_file":
            # read_file already slices by line limit (DEFAULT_LINE_LIMIT) and hints offset;
            # never spill it into another file that would itself spill on read_file.
            return result
        serialized = serialize_tool_data(result.data)
        if len(serialized) <= threshold:
            return result
        output_path = _write_spill_file(serialized, session_dir=session_dir, agent_paths=agent_paths)
        hint = _build_success_spill_hint(result, output_path)
        return tool_ok(
            result.tool,
            {
                "preview": serialized[:preview_limit],
                "spilled": True,
                "hint": hint,
            },
            truncated=True,
            duration_ms=result.duration_ms,
            output_path=output_path,
        )

    # Failure path — spill full envelope (not just data).
    full = to_json(result)
    compact_preview = _compact_tool_failure_preview(result)
    if len(full) <= threshold:
        if compact_preview and result.error and isinstance(result.error.details, dict):
            details = dict(result.error.details)
            details["failure_summary"] = details.get("failure_summary") or compact_preview
            err = result.error
            return tool_fail(
                result.tool,
                err.code,
                err.message,
                duration_ms=result.duration_ms,
                details=details,
                truncated=result.truncated,
                output_path=result.output_path,
            )
        return result

    output_path = _write_spill_file(full, session_dir=session_dir, agent_paths=agent_paths)
    err = result.error
    code = err.code if err is not None else "execution_error"
    message = (err.message if err is not None else "tool failed") or "tool failed"
    if len(message) > 500:
        message = message[:499] + "…"
    preview_body = compact_preview if compact_preview else full[:preview_limit]
    details: dict[str, Any] = {
        "preview": preview_body,
        "spilled": True,
    }
    if output_path:
        details["hint"] = f"Full tool result: read_file {output_path}"
    return tool_fail(
        result.tool,
        code,
        message,
        duration_ms=result.duration_ms,
        details=details,
        truncated=True,
        output_path=output_path,
    )


def _compact_tool_failure_preview(result: ToolResult) -> str | None:
    """Structured preview for run_project_tests failures (Phase 44 T-4404)."""
    if result.ok:
        return None
    payloads: list[dict[str, Any]] = []
    if isinstance(result.data, dict):
        payloads.append(result.data)
    if result.error and isinstance(result.error.details, dict):
        payloads.append(result.error.details)
    try:
        from project_verify import compact_test_failure_preview
    except ImportError:
        return None
    for payload in payloads:
        if payload.get("tool_name") == "run_project_tests" or payload.get("failures"):
            preview = compact_test_failure_preview(payload)
            if preview:
                return preview
    return None


def _write_spill_file(
    text: str,
    *,
    session_dir: Path | None,
    agent_paths: AgentPaths,
) -> str | None:
    if session_dir is None:
        return None
    output_dir = session_dir / _TOOL_OUTPUTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{uuid.uuid4().hex}.txt"
    output_file.write_text(text, encoding="utf-8")
    return agent_paths.to_agent_relative(output_file)


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


_CARD_FILE_TOOLS = frozenset({"write_text", "patch_file", "git_diff", "git_snapshot", "run_command"})


def _card_tool_name(tool_name: str, arguments: dict[str, Any]) -> str:
    """Surface file edits as the flat tool so the card is not 「扩展工具」."""
    if tool_name != "run_evolved":
        return tool_name
    raw = arguments.get("tool_name")
    if isinstance(raw, str) and raw.strip() in _CARD_FILE_TOOLS:
        return raw.strip()
    return tool_name


def _clip_summary(text: str, limit: int = 180) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


_SECRET_KV_RE = re.compile(
    r"(?i)(\b(?:token|api[_-]?key|secret|password|passwd|pwd|access[_-]?key|auth[_-]?token)"
    r"\s*(?:=|:|\s+)\s*)(['\"]?)[^\s'\"&;|]+(\2)"
)
_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+")
_SK_TOKEN_RE = re.compile(r"\b(?:sk|ghp|gho|github_pat)_[A-Za-z0-9_]{8,}\b")
_URL_CRED_RE = re.compile(r"(https?://[^/\s:@]+:)[^@\s/]+(@)")


def _redact_command_secrets(text: str) -> str:
    redacted = _SECRET_KV_RE.sub(r"\1\2***\3", text)
    redacted = _BEARER_RE.sub(r"\1***", redacted)
    redacted = _SK_TOKEN_RE.sub("***", redacted)
    redacted = _URL_CRED_RE.sub(r"\1***\2", redacted)
    return redacted


def _run_command_event_summary(command: str, *, limit: int = 320) -> str:
    """Process timeline + tool.start: show real argv (with secrets redacted), not a blind label."""
    text = command.strip().replace("\r\n", "\n")
    one_line = " ".join(line.strip() for line in text.splitlines() if line.strip())
    one_line = _redact_command_secrets(one_line)
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 1] + "…"


def _tool_event_summary(
    tool_name: str,
    arguments: dict[str, Any],
    evolved: EvolvedTool | None,
) -> str:
    if tool_name == "run_command":
        cmd = arguments.get("command")
        if isinstance(cmd, str) and cmd.strip():
            return _run_command_event_summary(cmd)
    if tool_name == "run_evolved":
        evolved_name = arguments.get("tool_name")
        inner = run_evolved.coalesce_tool_arguments(arguments)
        if isinstance(evolved_name, str) and evolved_name.strip() == "run_command":
            if isinstance(inner, dict):
                cmd = inner.get("command")
                if isinstance(cmd, str) and cmd.strip():
                    return _run_command_event_summary(cmd)
        if isinstance(evolved_name, str) and evolved_name.strip() in {"git_diff", "git_snapshot"}:
            label = evolved_name.strip()
            if isinstance(inner, dict):
                paths = inner.get("paths")
                if isinstance(paths, list) and paths:
                    shown = ", ".join(str(p) for p in paths[:3] if p)
                    if len(paths) > 3:
                        shown += f" +{len(paths) - 3}"
                    return f"{label}: {shown}"
                if inner.get("name_status"):
                    return f"{label}: --name-status"
                if inner.get("stat_only"):
                    return f"{label}: --stat"
            return label
        path = ""
        if isinstance(inner, dict):
            raw_path = inner.get("path") or inner.get("file") or inner.get("dest")
            if isinstance(raw_path, str):
                path = raw_path.strip()
        label = (
            evolved_name.strip()
            if isinstance(evolved_name, str) and evolved_name.strip()
            else "run_evolved"
        )
        if path:
            return f"{label}: {path}"
        return label
    for key in ("path", "query", "url", "pattern"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return tool_name


def _result_change_line(data: dict[str, Any]) -> str:
    path = ""
    for key in ("path", "written", "would_write"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            path = value.strip()
            break
    change = data.get("change_summary")
    change_text = change.strip() if isinstance(change, str) else ""
    if path and change_text:
        return _clip_summary(f"{path} · {change_text}")
    if path:
        return _clip_summary(path)
    if change_text:
        return _clip_summary(change_text)
    return ""


ABSENT_FILE_SUMMARY = "文件还不存在"


def _tool_end_note(result: ToolResult) -> str | None:
    """Informational read of a path that is not on disk yet."""
    data = result.data if isinstance(result.data, dict) else {}
    if result.ok and data.get("exists") is False:
        return "absent"
    return None


def _tool_end_display_paths(data: dict[str, Any]) -> list[str]:
    """Agent-relative image paths for Desktop inline preview (CHAT-IMAGE-DISPLAY)."""
    raw = data.get("display_paths")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(item.strip().replace("\\", "/"))
    return out


def _attach_desktop_inline_display_hint(result: ToolResult) -> ToolResult:
    """Tell the LLM that Desktop already rendered display_paths inline (CHAT-IMAGE-DISPLAY)."""
    if not result.ok or not isinstance(result.data, dict):
        return result
    paths = _tool_end_display_paths(result.data)
    if not paths:
        return result
    if result.data.get("desktop_chat_inline_display"):
        return result
    data = dict(result.data)
    data["desktop_chat_inline_display"] = True
    count = len(paths)
    if count == 1:
        data["llm_hint"] = (
            "Desktop unified chat already shows this image inline (display_paths). "
            "The user can see it in the chat UI. Do not ask whether to show or display it again; "
            "describe what they should see and suggest next steps only."
        )
    else:
        data["llm_hint"] = (
            f"Desktop unified chat already shows all {count} images inline (display_paths). "
            "The user can see them in the chat UI. Do not ask whether to show or display them again; "
            "describe what they should see and suggest next steps only."
        )
    return replace(result, data=data)


def _tool_result_summary(result: ToolResult) -> str:
    data = result.data if isinstance(result.data, dict) else {}
    if _tool_end_note(result) == "absent":
        return ABSENT_FILE_SUMMARY
    if not result.ok and result.error and isinstance(result.error.details, dict):
        summary = result.error.details.get("failure_summary")
        if isinstance(summary, str) and summary.strip():
            first = summary.strip().splitlines()[0]
            return first[:120] + ("…" if len(first) > 120 else "")
    # Prefer structured run_service failure / not-ready hints (Phase 27 IT-91).
    if isinstance(data.get("warning"), str) and data["warning"].strip():
        text = data["warning"].strip()
        return text[:120] + ("…" if len(text) > 120 else "")
    if not result.ok and result.error:
        base = result.error.message or "failed"
        tail = _result_logs_tail(result)
        if tail:
            last = tail.strip().splitlines()[-1].strip() if tail.strip() else ""
            if last:
                combined = f"{base} · {last}"
                return combined[:120] + ("…" if len(combined) > 120 else "")
        return base
    display_paths = _tool_end_display_paths(data)
    if display_paths:
        n = len(display_paths)
        return "已在聊天内联展示图片" if n == 1 else f"已在聊天内联展示 {n} 张图片"
    change_line = _result_change_line(data)
    if change_line:
        return change_line
    for key in ("path", "written", "would_write", "message", "preview"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            return text[:180] + ("…" if len(text) > 180 else "")
    if data.get("ready") is False and data.get("action") in {"start", "restart", "wait"}:
        return "started but not ready"
    return "ok" if result.ok else "failed"


def _result_logs_tail(result: ToolResult) -> str | None:
    """Extract truncated logs_tail from evolved tool data / error details."""
    candidates: list[Any] = []
    if isinstance(result.data, dict):
        candidates.append(result.data.get("logs_tail"))
        start = result.data.get("start")
        if isinstance(start, dict):
            candidates.append(start.get("logs_tail"))
    if result.error and isinstance(result.error.details, dict):
        candidates.append(result.error.details.get("logs_tail"))
        start = result.error.details.get("start")
        if isinstance(start, dict):
            candidates.append(start.get("logs_tail"))
    for raw in candidates:
        if isinstance(raw, str) and raw.strip():
            text = raw.strip()
            if len(text) > 4096:
                return text[-4096:]
            return text
    return None


def _http_request_needs_confirm(inner: dict[str, Any]) -> bool:
    """Phase 26 D2: confirm unless loopback + GET/HEAD."""
    import ipaddress
    from urllib.parse import urlparse

    method = str(inner.get("method") or "GET").strip().upper()
    url = str(inner.get("url") or "").strip()
    if method not in {"GET", "HEAD"}:
        return True
    try:
        host = urlparse(url).hostname
    except Exception:
        return True
    if not host:
        return True
    lowered = host.lower().strip("[]")
    if lowered in {"localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1"}:
        return False
    try:
        return not ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return True


def _browser_open_needs_confirm(inner: dict[str, Any]) -> bool:
    """Phase 33 F1: confirm unless loopback http(s)."""
    import ipaddress
    from urllib.parse import urlparse

    url = str(inner.get("url") or "").strip()
    try:
        parsed = urlparse(url)
    except Exception:
        return True
    if parsed.scheme not in {"http", "https"}:
        return True
    host = parsed.hostname
    if not host:
        return True
    lowered = host.lower().strip("[]")
    if lowered in {"localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1"}:
        return False
    try:
        return not ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return True


def _arguments_use_host_scope(arguments: dict[str, Any]) -> bool:
    """True when nested run_evolved arguments reference ``host:`` URIs."""
    nested = arguments.get("arguments")
    if not isinstance(nested, dict):
        return False
    for value in nested.values():
        if isinstance(value, str) and value.strip().lower().startswith("host:"):
            return True
    return False


def resolve_write_confirm(
    *,
    evolved_name: str,
    arguments: dict[str, Any],
    session: ExecutorSession,
    agent_paths: AgentPaths,
) -> tuple[bool, str]:
    """Layered confirm for write_text / patch_file (Phase 42 Track H)."""
    from tools.builtin import run_evolved as _run_evolved_mod
    from write_policy import write_requires_confirm

    inner = _run_evolved_mod.coalesce_tool_arguments(arguments)
    if _is_terminal_executor_session(session):
        from terminal_scope import terminal_write_requires_confirm

        path = inner.get("path") if isinstance(inner.get("path"), str) else ""
        dry_run = bool(inner.get("dry_run")) or bool(arguments.get("dry_run"))
        return terminal_write_requires_confirm(
            tool=evolved_name,
            path=path,
            meta=_executor_session_meta(session),
            agent_paths=agent_paths,
            dry_run=dry_run,
        )
    path = inner.get("path") if isinstance(inner.get("path"), str) else ""
    on_conflict = inner.get("on_conflict") if isinstance(inner.get("on_conflict"), str) else "skip"
    file_exists: bool | None = None
    if evolved_name == "write_text" and path.strip():
        try:
            resolved = agent_paths.resolve_under_agent_for_write(path.strip(), must_exist=False)
            file_exists = resolved.is_file()
        except Exception:
            file_exists = None
    return write_requires_confirm(
        tool=evolved_name,  # type: ignore[arg-type]
        path=path,
        project_root=session.project_root or "",
        active_shell=session.active_shell or "",
        on_conflict=on_conflict,
        file_exists=file_exists,
    )


def _preview_text_field(value: str, *, label: str, head: int = 80) -> str:
    """One-line summary for large text fields in confirm previews."""
    lines = value.count("\n") + 1
    if len(value) <= head and lines <= 3:
        snippet = value.replace("\n", " ").strip()
        return f"{label}：{snippet}"
    return f"{label}：{len(value)} 字符 · {lines} 行"


RUN_COMMAND_FULL_PREVIEW_MAX = 8000
RUN_COMMAND_FULL_MARKER = "命令全文："


def _run_command_headline(command: str, *, head: int = 120) -> str:
    """First line of the command (never just a char count) so cards are not blind."""
    text = command.strip()
    first = text.splitlines()[0].strip() if text else ""
    extra_lines = len(text.splitlines()) - 1
    if len(first) > head:
        first = first[:head].rstrip() + "…"
    elif extra_lines > 0:
        first = f"{first} …"
    if extra_lines > 0 or len(text) > head:
        first = f"{first}（共 {len(text)} 字符 · {extra_lines + 1} 行）"
    return first


def _run_command_full_block(command: str) -> str:
    """Full command text appended to confirm previews (capped, never summarized away)."""
    text = command.strip()
    if len(text) > RUN_COMMAND_FULL_PREVIEW_MAX:
        cut = len(text) - RUN_COMMAND_FULL_PREVIEW_MAX
        text = text[:RUN_COMMAND_FULL_PREVIEW_MAX] + f"\n…（其余 {cut} 字符未显示）"
    return f"{RUN_COMMAND_FULL_MARKER}\n{text}"


def _summarize_confirm_inner(evolved_name: str, inner: dict[str, Any]) -> list[str]:
    """Compact human lines for evolved tool confirm — never dump full file bodies."""
    from tool_display import display_tool_name

    name = (evolved_name or "").strip()
    if name == "write_text" and isinstance(inner, dict):
        path = inner.get("path") or inner.get("file") or "?"
        lines = [f"{display_tool_name('write_text')}：{path}"]
        content = inner.get("content")
        if isinstance(content, str) and content:
            lines.append(_preview_text_field(content, label="内容"))
        on_conflict = inner.get("on_conflict")
        if on_conflict:
            lines.append(f"冲突处理：{on_conflict}")
        return lines
    if name == "patch_file" and isinstance(inner, dict):
        path = inner.get("path") or inner.get("file") or "?"
        lines = [f"{display_tool_name('patch_file')}：{path}"]
        find = inner.get("find")
        if isinstance(find, str) and find:
            lines.append(_preview_text_field(find, label="查找", head=72))
        replace = inner.get("replace")
        if isinstance(replace, str) and replace:
            lines.append(_preview_text_field(replace, label="替换为", head=72))
        return lines
    if name == "search_replace" and isinstance(inner, dict):
        lines = [f"{display_tool_name('search_replace')}：多文件字面量替换"]
        find = inner.get("find")
        if isinstance(find, str) and find:
            lines.append(_preview_text_field(find, label="查找", head=72))
        replace = inner.get("replace")
        if isinstance(replace, str) and replace:
            lines.append(_preview_text_field(replace, label="替换为", head=72))
        paths = inner.get("paths")
        if isinstance(paths, list) and paths:
            lines.append(f"路径数：{len(paths)}")
        return lines
    if name == "gh_pr" and isinstance(inner, dict):
        action = str(inner.get("action") or "?").strip()
        lines = [f"{display_tool_name('gh_pr')}：{action}"]
        title = inner.get("title")
        if isinstance(title, str) and title.strip():
            lines.append(_preview_text_field(title, label="标题", head=96))
        return lines
    if name == "local_preview" and isinstance(inner, dict):
        url = inner.get("url") or "?"
        lines = [f"{display_tool_name('local_preview')}：{url}"]
        start = inner.get("start_command")
        if isinstance(start, str) and start.strip():
            lines.append(_preview_text_field(start, label="启动命令", head=96))
        return lines
    if name == "run_command" and isinstance(inner, dict):
        cmd = inner.get("command")
        if isinstance(cmd, str) and cmd.strip():
            lines = [f"运行命令：{_run_command_headline(cmd)}"]
            cwd = inner.get("cwd")
            if isinstance(cwd, str) and cwd.strip():
                lines.append(f"工作目录：{cwd}")
            return lines
    preview_inner: dict[str, Any] = dict(inner)
    for key in ("content", "text", "new_text", "find", "replace", "patch", "command"):
        val = preview_inner.get(key)
        if isinstance(val, str) and len(val) > 120:
            preview_inner[key] = f"({len(val)} 字符)"
    b64 = preview_inner.get("content_base64")
    if isinstance(b64, str) and len(b64) > 64:
        preview_inner["content_base64"] = f"{b64[:48]}…({len(b64)} chars)"
    return [f"参数：{json.dumps(preview_inner, ensure_ascii=False, sort_keys=True)}"]


def build_confirm_preview(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    evolved: EvolvedTool | None = None,
    project_root: str = "",
    active_shell: str = "",
    agent_paths: AgentPaths | None = None,
    extra_context: str | None = None,
) -> str:
    """Human-readable preview shown before confirm."""
    lines: list[str] = []
    if tool_name == "generate_image":
        prompt = arguments.get("prompt")
        if extra_context:
            source_header = extra_context.splitlines()[0].strip()
            lines.append(f"{source_header} Image bytes will be sent to 0x567 only after approval; this may use account credits.")
        else:
            lines.append("Image-generation request to 0x567; this may use account credits.")
        if isinstance(prompt, str) and prompt.strip():
            lines.append(f"Prompt: {prompt.strip()[:2000]}")
        lines.append(f"Model: {arguments.get('model', 'gpt-image-2.5-flare')}")
        lines.append(f"Size: {arguments.get('size', '1024x1024')}")
        lines.append(f"Quality: {arguments.get('quality', 'low')}")
    elif tool_name == "run_evolved":
        evolved_name = arguments.get("tool_name")
        inner = run_evolved.coalesce_tool_arguments(arguments)
        if isinstance(evolved_name, str) and evolved_name.strip():
            lines.extend(_summarize_confirm_inner(evolved_name, inner if isinstance(inner, dict) else {}))
        elif isinstance(inner, dict) and inner:
            lines.extend(_summarize_confirm_inner("", inner))
        if arguments.get("dry_run"):
            lines.append("模式：dry_run")
        if evolved is not None:
            if evolved.policy.allow_approve_all:
                lines.append("可本会话一键放行：是")
            if evolved.name == "run_command" and isinstance(inner, dict):
                if bool(inner.get("background")):
                    lines.append("模式：后台 → 转为 run_service")
                else:
                    lines.append("说明：结束后退出；长驻进程请用 run_service")
                try:
                    from run_command_policy import classify_run_command

                    cmd = inner.get("command") if isinstance(inner.get("command"), str) else ""
                    lines.append(f"命令类型：{classify_run_command(cmd)}")
                except Exception:
                    pass
            if (
                evolved.name in {"write_text", "patch_file"}
                and isinstance(inner, dict)
                and agent_paths is not None
            ):
                try:
                    session = ExecutorSession(
                        project_root=project_root,
                        active_shell=active_shell,
                    )
                    _needs, reason = resolve_write_confirm(
                        evolved_name=evolved.name,
                        arguments=arguments,
                        session=session,
                        agent_paths=agent_paths,
                    )
                    lines.append(f"写入策略：{reason}")
                except Exception:
                    pass
            if evolved.name == "browser_open" and isinstance(inner, dict):
                url = inner.get("url") if isinstance(inner.get("url"), str) else ""
                lines.append(f"Open in system browser: {url}")
                lines.append("Note: loopback may skip confirm; external always confirms")
            if evolved.name in {"host_copy_move", "copy_move"} and isinstance(inner, dict):
                source = inner.get("source")
                if isinstance(source, str) and source.strip().lower().startswith("host:"):
                    try:
                        from host_tools import host_path_confirm_line
                        from paths import AgentPaths

                        agent_paths = AgentPaths.discover()
                        dest = inner.get("dest")
                        lines.append(host_path_confirm_line(agent_paths, "Source", source, write=False))
                        if isinstance(dest, str):
                            lines.append(host_path_confirm_line(agent_paths, "Dest", dest, write=True))
                    except Exception as exc:
                        lines.append(f"Host paths: (preview failed: {exc})")
    else:
        if arguments:
            lines.append(f"Arguments: {json.dumps(arguments, ensure_ascii=False, sort_keys=True)}")
    if extra_context:
        detail_lines = extra_context.splitlines()
        if tool_name == "generate_image":
            lines.extend(detail_lines[1:])
        else:
            lines.append(extra_context)
    if tool_name == "read_file":
        path_arg = arguments.get("path")
        if isinstance(path_arg, str):
            try:
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
    if tool_name == "run_evolved" and str(arguments.get("tool_name") or "").strip() == "run_command":
        inner_cmd = run_evolved.coalesce_tool_arguments(arguments)
        cmd_text = inner_cmd.get("command") if isinstance(inner_cmd, dict) else None
        if isinstance(cmd_text, str) and cmd_text.strip():
            # Full command last so the card never asks the user to approve blind.
            lines.append(_run_command_full_block(cmd_text))
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

        _write_manifest(tool_ws / "tool.toml", name="echo_json", allow_approve_all=True)
        _write_manifest(tool_remote / "tool.toml", name="remote_echo", allow_approve_all=False, topics=["coding"])

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
        print("[PASS] allow_approve_all evolved skips confirm after a")

        prompts_queue = ["y"]
        still_confirm = executor.run(
            "run_evolved",
            {"tool_name": "remote_echo", "arguments": {"message": "remote"}},
        )
        assert still_confirm.ok and prompts
        print("[PASS] non-allow_approve_all evolved still confirms")

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
        _write_manifest(tool_write / "tool.toml", name="write_probe", allow_approve_all=True)
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
            _write_manifest(demo_dir / "tool.toml", name="registry_reload_demo", allow_approve_all=False)
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
            _write_manifest(demo_tool_dir / "tool.toml", name="guard_demo_tool", allow_approve_all=True)
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


def _write_manifest(path: Path, *, name: str, allow_approve_all: bool, topics: list[str] | None = None) -> None:
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
allow_approve_all = {"true" if allow_approve_all else "false"}
timeout_sec = 30
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    _demo()
