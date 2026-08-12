"""Builtin LLM tool definitions + agent main loop (TOOLS.md, RUNTIME.md §7, TASKS T-202/T-206)."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from loader import (
    format_session_evolved_catalog,
    format_tool_interrupt_kernel_message,
    format_tool_interrupt_notice,
    format_tool_loop_user_message,
    session_evolved_allowlist,
)
from llm_client import (
    LLMApiError,
    LLMCancelledError,
    LLMClient,
    LLMError,
    LLMNetworkError,
    LLMResponse,
    LLMTimeoutError,
    StreamHandlers,
    load_config,
    resolve_session_model,
)
from paths import AgentPaths
from session import ANCHOR_HEADER, Session, SessionMeta, build_anchor_message, utc_now_iso
from tools.executor import ExecutorSession, ToolExecutor
from tools.logging import EvolveLog, read_events
from tool_proxies import PROXY_EVOLVED_TOOL_NAMES, build_proxy_tool_definitions
from tools.registry import BUILTIN_TOOLS, ToolRegistry
from tools.schema import ToolErrorCode, ToolResult, tool_fail, to_json

DEFAULT_TOOL_LOOP_MAX = 50
MAIN_LOOP_TEMPERATURE = 0.3
DELIVERY_COMPLETE_MARKER = "交付完成"
SCAFFOLD_COMPLETE_MARKERS = ("已验收", "沉淀完成")
SCAFFOLD_COMPLETE_REPLACEMENT = "〔验收未通过·已拦截〕"
QA_SOFT_REMINDER_MESSAGE = (
    "[内核] 请直接回答：根据已有上下文给出文字回复，勿再调用工具。"
)
RECALL_SOFT_REMINDER_MESSAGE = (
    "[内核] 根据上文直接回顾，勿 read_file/grep messages.jsonl。"
)
ACTION_ANNOUNCE_NUDGE_MESSAGE = (
    "[内核] 你刚宣布了具体操作但本回合未调用工具。"
    "请立刻用工具执行；若只需说明或提问，请改写为不含「正在/接下来/我来/建表…」的纯文字。"
)
ORCH_DEFER_NUDGE_MESSAGE = (
    "[内核] 口头「等 N 秒再查 / 稍后再起」不能结束本回合。"
    "请立刻调用 run_service（wait / logs / status 或带 ready_timeout 的 blocking start），"
    "在同一回合内完成起服链；勿让用户再发「继续」。"
)
TURN_DRIFT_NOTICE = (
    "[提醒] 这类问题可以直接根据上文回答；若仍在查文件，可回复「别查了直接说」。"
)
_DEFAULT_PARENT_SHORT_MAX = 5
_DEFAULT_EXECUTE_SEGMENT_MAX = 50
_DEFAULT_PROJECT_EXECUTE_SEGMENT_MAX = 15  # AGENT-HARNESS P2 · T-4102
_DEFAULT_EXECUTE_TOTAL_MAX = 50

_CHECKLIST_PROGRESS_RE = re.compile(
    r"(?:\[[xX✓✔]\]|[-*]\s*\[[xX]\])",
)

# Future-tense / in-progress action announce without tools (huiyi: 「重新建库建表：」).
_ACTION_ANNOUNCE_RE = re.compile(
    r"(?:"
    r"(?:重新)?建(?:库|表)"
    r"|我来(?:帮你)?(?:做|写|改|跑|建|修|启|执行)"
    r"|现在(?:就)?(?:开始|去|来)(?:建|写|跑|修|启|安装|编译|导入|执行)"
    r"|接下来(?:我)?(?:来|去|会|将)?(?:建|写|跑|修|启|安装|编译|导入|执行)"
    r"|马上(?:就)?(?:建|写|跑|修|启|执行)"
    r"|正在(?:重新)?(?:建|写|跑|修|启|安装|编译|导入)"
    r"|先(?:去)?(?:建|写|跑|修|启|导入|执行)"
    r"|I(?:'ll| will)\s+(?:now\s+)?(?:create|write|run|fix|start|build|import)"
    r"|Let me\s+(?:create|write|run|fix|start|build|import)"
    r")",
    re.IGNORECASE,
)

# Verbal deferral without run_service wait (Pack 6 · T-5602 / G13 extension).
_ORCH_DEFER_RE = re.compile(
    r"(?:"
    r"等\s*\d+\s*(?:～|~|-)?\s*\d*\s*秒"
    r"|\d+\s*秒(?:钟)?\s*后.*?(?:查|看|检查|起|启动|日志)"
    r"|稍后再(?:起|查|看|检查|启动)"
    r"|启动中[，,].*稍后"
    r"|(?:我)?先(?:去)?(?:查|看|检查)一下再(?:告诉|说|起)"
    r"|\bwait\s+\d+\s*(?:seconds?|secs?)\b"
    r"|\b(?:in|after)\s+\d+\s*(?:seconds?|secs?)\b.*?\b(?:check|inspect|look|logs?)\b"
    r")",
    re.IGNORECASE,
)


def parent_short_max() -> int:
    raw = os.environ.get("PARENT_SHORT_MAX", str(_DEFAULT_PARENT_SHORT_MAX))
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_PARENT_SHORT_MAX
    return max(1, value)


def parent_execute_segment_max(*, active_shell: str = "") -> int:
    """Per-segment tool budget (AGENT-HARNESS P2).

    ``active_shell=project`` defaults to 15; other shells default to 50.
    Explicit ``PARENT_EXECUTE_SEGMENT_MAX`` always overrides (tests / ops).
    """
    if "PARENT_EXECUTE_SEGMENT_MAX" in os.environ:
        raw = os.environ.get("PARENT_EXECUTE_SEGMENT_MAX", "")
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    if (active_shell or "").strip() == "project":
        return _DEFAULT_PROJECT_EXECUTE_SEGMENT_MAX
    return _DEFAULT_EXECUTE_SEGMENT_MAX


def parent_execute_total_max() -> int:
    raw = os.environ.get("PARENT_EXECUTE_TOTAL_MAX", str(_DEFAULT_EXECUTE_TOTAL_MAX))
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_EXECUTE_TOTAL_MAX
    return max(1, value)


def auto_continue_enabled(*, active_shell: str = "", harness: str = "") -> bool:
    """Whether execute may auto-start the next segment in the same turn (T-705).

    Phase 20 / TASK-STOP S4: project shell always pauses at segment/task boundary.
    Terminal harness (TM-16): no segment_cap_pause — always continue within total cap.
    grow/daily honor ``MY_AGENT_AUTO_CONTINUE`` (default **off**): each user message
    gets a fresh tool budget; send another message (e.g. 「继续」) for the next segment.
    """
    if (harness or "").strip() == "terminal":
        return True
    if (active_shell or "").strip() == "project":
        return False
    return os.environ.get("MY_AGENT_AUTO_CONTINUE", "0").strip() not in {"0", "false", "no"}


def is_delivery_complete(text: str) -> bool:
    return DELIVERY_COMPLETE_MARKER in text


def claims_scaffold_complete(text: str) -> bool:
    """True when assistant text claims scaffold acceptance (T-1623)."""
    if not isinstance(text, str) or not text.strip():
        return False
    return any(marker in text for marker in SCAFFOLD_COMPLETE_MARKERS)


def announces_pending_action(text: str) -> bool:
    """True when assistant text announces an imminent side-effecting action (no tools yet)."""
    if not isinstance(text, str) or not text.strip():
        return False
    return _ACTION_ANNOUNCE_RE.search(text) is not None


def announces_orchestration_defer(text: str) -> bool:
    """True when assistant verbally defers service checks to later (Pack 6 · IT-560)."""
    if not isinstance(text, str) or not text.strip():
        return False
    return _ORCH_DEFER_RE.search(text) is not None


def pending_action_nudge_message(text: str) -> str | None:
    """Nudge body for no-tool assistant text, or None if no gate applies."""
    if announces_orchestration_defer(text):
        return ORCH_DEFER_NUDGE_MESSAGE
    if announces_pending_action(text):
        return ACTION_ANNOUNCE_NUDGE_MESSAGE
    return None


def apply_scaffold_completion_gate(text: str, verdict: str | None) -> str:
    """Strip scaffold completion claims when checker verdict is not pass (T-1623)."""
    if verdict == "pass" or not claims_scaffold_complete(text):
        return text
    cleaned = text
    for marker in SCAFFOLD_COMPLETE_MARKERS:
        cleaned = cleaned.replace(marker, SCAFFOLD_COMPLETE_REPLACEMENT)
    return cleaned


def apply_service_postcondition_gate(
    text: str,
    messages: list[dict[str, Any]],
) -> str:
    """Rewrite start/alive success claims when postcondition unmet (G14 / T-3501)."""
    from exec_reliability import apply_service_success_gate, run_service_postcondition_ok

    return apply_service_success_gate(
        text,
        postcondition_ok=run_service_postcondition_ok(messages),
    )


def tool_result_shows_progress(content: str) -> bool:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    return data.get("tool") == "run_evolved" and data.get("ok") is True


def assistant_shows_checklist_progress(content: str | None) -> bool:
    if not isinstance(content, str) or not content.strip():
        return False
    return _CHECKLIST_PROGRESS_RE.search(content) is not None


def segment_messages_show_progress(messages: list[dict[str, Any]], start_index: int) -> bool:
    for msg in messages[start_index:]:
        role = msg.get("role")
        if role == "tool" and isinstance(msg.get("content"), str):
            if tool_result_shows_progress(msg["content"]):
                return True
        if role == "assistant" and assistant_shows_checklist_progress(msg.get("content")):
            return True
    return False

# OpenAI function names exposed to the LLM (RUNTIME.md §7.2).
BUILTIN_TOOL_NAMES: tuple[str, ...] = tuple(tool.name for tool in BUILTIN_TOOLS)

_BUILTIN_DESCRIPTIONS: dict[str, str] = {
    "read_file": (
        "Read a text file under agent root or workspace (max 512KB, UTF-8). "
        "Use for docs, workspace files, evolve/memories, and spilled tool outputs."
    ),
    "list_dir": (
        "List directory entries under agent root. "
        "Set recursive=true to include one level of children."
    ),
    "grep": (
        "Search local file contents under agent root by regex pattern. "
        "Prefer ripgrep when available."
    ),
    "glob_file_search": (
        "Find files by glob pattern (e.g. '**/*.py', '**/test_*.ts') under a directory. "
        "Use before read_file when you need paths by name; use grep for content search."
    ),
    "codebase_search": (
        "Semantic/BM25 search for code chunks in the bound project. "
        "Use for 'where is X implemented' before glob/grep on large trees."
    ),
    "web_search": (
        "Search the web for links and snippets. "
        "Use fetch_url when full page text is needed."
    ),
    "fetch_url": (
        "Fetch an HTTP/HTTPS URL and return extracted plain text "
        "(HTML stripped). Pair with web_search for page bodies."
    ),
    "run_evolved": (
        "Run a registered evolved tool by name (see session catalog). "
        "For write_evolve ONLY: put path and content_base64 as TOP-LEVEL fields "
        "(same level as tool_name), NOT nested inside arguments.content — "
        "nested TOML quotes break JSON. Example: "
        '{"tool_name":"write_evolve","path":"evolve/tools/data/foo/main.py",'
        '"content_base64":"<base64>","on_conflict":"overwrite","arguments":{}}. '
        "Write main.py before tool.toml. Supports dry_run."
    ),
    "propose_context_switch": (
        "Propose switching conversation context: new/switch workspace project, "
        "or start a fresh ordinary chat (session.new). "
        "Use when the user wants a NEW project, another project, "
        "to leave project for write_evolve / tool scaffolding, "
        "or a clean chat ('新话题'/'新开一局'). "
        "From a project-bound session, session.new parks the project and opens grow "
        "(unbound) chat where write_evolve is allowed. "
        "ALWAYS call this BEFORE writing files under a different workspace/<id>/ "
        "or before evolve writes from project shell. Requires user confirm/reject."
    ),
    "plan_partner": (
        "Invoke plan subagent for TASKS/MAP/PROJECT/ENV (and archive restore). "
        "Returns summary; proposals appear in sidebar — user must adopt before write. "
        "Do NOT write_text/patch_file plan-domain files. "
        "User says 任务不见了 / 误勾 / 恢复 Phase N / restore T-xxx → call this with "
        "task like「从归档恢复 Phase N 的 T-020…」; sidebar checkbox = done+archive, "
        "not delete; only restore via this tool."
    ),
    "deliverable_review": (
        "Spawn read-only deliverable review subagent for the bound project. "
        "Parent should run build/tests first when possible; pass results in facts. "
        "Use when user asks for acceptance, what's missing, deliverability, or "
        "doc/code drift (e.g. 脱节, 你看看是否对齐)."
    ),
    "explore": (
        "Spawn read-only explore subagent with an explicit task string (paths and "
        "checkpoints). Parent must write scope, e.g. 只读 workspace/huiyi/src. "
        "Not for doc/code drift — use deliverable_review instead."
    ),
}


_BUILTIN_PARAMETERS: dict[str, dict[str, Any]] = {
    "read_file": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path relative to agent root or workspace",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    "list_dir": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path relative to agent root",
            },
            "recursive": {
                "type": "boolean",
                "description": "When true, include immediate children (default false)",
                "default": False,
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    "grep": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for",
            },
            "path": {
                "type": "string",
                "description": "File or directory under agent root",
            },
            "glob": {
                "type": "string",
                "description": "Optional glob filter (e.g. '*.py')",
            },
            "ignore_case": {
                "type": "boolean",
                "description": "Case-insensitive search (default false)",
                "default": False,
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum matches to return (default 50)",
                "default": 50,
            },
        },
        "required": ["pattern", "path"],
        "additionalProperties": False,
    },
    "glob_file_search": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern (e.g. '**/*.py')",
            },
            "path": {
                "type": "string",
                "description": "Search root relative to agent root (default '.')",
                "default": ".",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum file paths to return (default 200, cap 1000)",
                "default": 200,
            },
            "ignore_case": {
                "type": "boolean",
                "description": "Case-insensitive glob match (default false)",
                "default": False,
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
    "codebase_search": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language or keyword query (e.g. 'JWT login validation')",
            },
            "top_k": {
                "type": "integer",
                "description": "Max hits to return (default 5, cap 8)",
                "default": 5,
            },
            "path_prefix": {
                "type": "string",
                "description": "Optional path prefix under project_root to narrow hits",
            },
            "force_refresh": {
                "type": "boolean",
                "description": "Rebuild index before search (default false)",
                "default": False,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "web_search": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Web search query",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results (default 5, hard cap 10)",
                "default": 5,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "fetch_url": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "HTTP or HTTPS URL to fetch",
            },
            "max_chars": {
                "type": "integer",
                "description": "Max characters to return (default 32000, hard cap 128000)",
                "default": 32000,
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    "run_evolved": {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": "string",
                "description": "Evolved tool name from the session catalog",
            },
            "arguments": {
                "type": "object",
                "description": (
                    "Inner arguments for the evolved tool. "
                    "For write_evolve prefer empty {} and use top-level path + content_base64 instead."
                ),
                "additionalProperties": True,
                "default": {},
            },
            "path": {
                "type": "string",
                "description": (
                    "write_evolve only: target path evolve/tools/<scope>/<name>/main.py or tool.toml"
                ),
            },
            "content_base64": {
                "type": "string",
                "description": (
                    "write_evolve only: UTF-8 file body as standard base64. "
                    "REQUIRED for tool.toml and any file containing double quotes. "
                    "Do NOT use arguments.content for those."
                ),
            },
            "content_workspace_path": {
                "type": "string",
                "description": (
                    "write_evolve only: copy body from an existing workspace/ file "
                    "(alternative to content_base64)"
                ),
            },
            "on_conflict": {
                "type": "string",
                "enum": ["skip", "rename", "overwrite"],
                "description": "write_evolve only: default skip",
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "Preview without side effects when supported (default false). "
                    "Top-level dry_run wins if true; if false, inner arguments.dry_run may apply."
                ),
                "default": False,
            },
        },
        "required": ["tool_name"],
        "additionalProperties": False,
    },
    "propose_context_switch": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "project.create",
                    "project.switch",
                    "session.new",
                ],
                "description": (
                    "project.create = new workspace project + dedicated session; "
                    "project.switch = switch to an existing project session; "
                    "session.new = blank ordinary chat (active_shell=grow; parks project "
                    "session if any — use before write_evolve from project window)"
                ),
            },
            "target": {
                "type": "string",
                "description": (
                    "For project.*: project id (lowercase). "
                    "For session.new: current (only value after shell consolidation)"
                ),
            },
            "project_id": {
                "type": "string",
                "description": "Optional; reserved for future project-scoped session.new",
            },
            "reason": {
                "type": "string",
                "description": "Short reason shown on the confirm card",
            },
            "template": {
                "type": "string",
                "description": (
                    "project.create only: recipe id under evolve/scaffolds/ "
                    "(e.g. spring-vue). Runs scaffold_project phase=init after create."
                ),
            },
        },
        "required": ["action", "target"],
        "additionalProperties": False,
    },
    "plan_partner": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "What to change in plan domain (TASKS/MAP/PROJECT/ENV), "
                    "or restore from archive e.g. 「从归档恢复 Phase 7 的 T-020～T-025」"
                ),
            },
            "include_recent_user_lines": {
                "type": "integer",
                "minimum": 0,
                "maximum": 5,
                "default": 2,
                "description": "Optional recent main-chat user lines for context (default 2)",
            },
        },
        "required": ["task"],
        "additionalProperties": False,
    },
    "deliverable_review": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "User intent or focus, e.g. 验收 huiyi / Phase 7 是否完成",
            },
            "scope": {
                "type": "string",
                "enum": ["full", "phase", "files"],
                "default": "full",
            },
            "phase_hint": {
                "type": "string",
                "description": "When scope=phase, e.g. Phase 7",
            },
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "When scope=files, relative to project_root",
            },
            "facts": {
                "type": "object",
                "description": "Parent-supplied L1 results; subagent must not re-run heavy commands",
            },
            "max_rounds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 32,
                "description": "Optional tool-round budget for review subagent (default SUBAGENT cap)",
            },
        },
        "required": ["task"],
        "additionalProperties": False,
    },
    "explore": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "Read-only investigation scope written by parent agent, "
                    "e.g. 只读 workspace/huiyi，列出 src 下路由文件"
                ),
            },
            "max_rounds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 32,
                "description": "Optional tool-round budget for explore subagent (default SUBAGENT cap)",
            },
        },
        "required": ["task"],
        "additionalProperties": False,
    },
}


def builtin_parameters(name: str) -> dict[str, Any]:
    """JSON Schema for a builtin's arguments (TOOLS.md §7)."""
    try:
        return _BUILTIN_PARAMETERS[name]
    except KeyError as exc:
        raise KeyError(f"unknown builtin tool: {name!r}") from exc


def build_builtin_tool_definition(name: str) -> dict[str, Any]:
    """Single OpenAI-compatible tool entry for *name*."""
    if name not in _BUILTIN_PARAMETERS:
        raise KeyError(f"unknown builtin tool: {name!r}")
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": _BUILTIN_DESCRIPTIONS[name],
            "parameters": _BUILTIN_PARAMETERS[name],
        },
    }


def build_builtin_tools(*, registry: ToolRegistry | None = None) -> list[dict[str, Any]]:
    """Return builtin tools for LLM chat (no flat evolved)."""
    reg = registry or ToolRegistry.load()
    registry_names = tuple(tool.name for tool in reg.builtins())
    if registry_names != BUILTIN_TOOL_NAMES:
        raise RuntimeError(
            f"registry builtins {registry_names!r} != expected {BUILTIN_TOOL_NAMES!r}"
        )
    return [build_builtin_tool_definition(name) for name in BUILTIN_TOOL_NAMES]


def build_llm_tools(
    session: Session,
    *,
    registry: ToolRegistry | None = None,
) -> list[dict[str, Any]]:
    """Builtin tools exposed to the LLM for this turn (T-702: ask omits run_evolved)."""
    tools = build_builtin_tools(registry=registry)
    tools.extend(build_proxy_tool_definitions())
    if session.meta.turn_mode == "ask":
        blocked = {"run_evolved", *PROXY_EVOLVED_TOOL_NAMES}
        tools = [item for item in tools if item["function"]["name"] not in blocked]
    pid = (session.meta.project_id or "").strip()
    if not pid:
        blocked_project = {"plan_partner", "deliverable_review"}
        tools = [item for item in tools if item["function"]["name"] not in blocked_project]
    return tools


class ToolLoopExceededError(RuntimeError):
    """Tool inner loop exceeded ``DEFAULT_TOOL_LOOP_MAX`` rounds."""


class ChatClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        stream: StreamHandlers | None = None,
    ) -> LLMResponse: ...


@dataclass
class TurnResult:
    assistant_text: str
    tool_rounds: int
    finish_reason: str | None
    tool_loop_exceeded: bool = False
    subagent_used: bool = False
    subagent_tool_rounds: int = 0
    turn_intent: str | None = None
    execute_segments: int = 1
    total_tool_rounds: int = 0
    notices: list[str] = field(default_factory=list)
    qa_soft_reminder_injected: bool = False
    checker_used: bool = False
    checker_verdict: str | None = None
    checker_tool_rounds: int = 0


@dataclass
class ToolLoopSegmentResult:
    final_text: str
    tool_rounds: int
    finish_reason: str | None
    exceeded: bool = False
    had_progress: bool = False
    qa_soft_reminder_injected: bool = False
    action_announce_nudge_injected: bool = False


@dataclass
class Agent:
    """One conversation turn: user input → LLM tool loop → persisted messages."""

    session: Session
    executor: ToolExecutor
    llm: ChatClient
    tool_loop_max: int = DEFAULT_TOOL_LOOP_MAX
    stream_handlers: StreamHandlers | None = None
    on_turn_event: Any | None = field(default=None, repr=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _cancel_finish_reason_fn: Callable[[], str | None] | None = field(default=None, repr=False)
    _pause_turn_wall: Callable[[], None] | None = field(default=None, repr=False)
    _resume_turn_wall: Callable[[], None] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        setter = getattr(self.llm, "set_cancel_event", None)
        if callable(setter):
            setter(self.cancel_event)
        self.executor.cancel_event = self.cancel_event

    def bind_cancel_event(self, event: threading.Event) -> None:
        self.cancel_event = event
        self.executor.cancel_event = event
        setter = getattr(self.llm, "set_cancel_event", None)
        if callable(setter):
            setter(event)

    def bind_cancel_finish_reason(self, resolver: Callable[[], str | None]) -> None:
        self._cancel_finish_reason_fn = resolver

    def bind_turn_wall_hooks(
        self,
        pause: Callable[[], None] | None,
        resume: Callable[[], None] | None,
    ) -> None:
        self._pause_turn_wall = pause
        self._resume_turn_wall = resume

    def _compact_summarize_wall_begin(self) -> None:
        pause = self._pause_turn_wall
        if callable(pause):
            pause()

    def _compact_summarize_wall_end(self) -> None:
        resume = self._resume_turn_wall
        if callable(resume):
            resume()

    def _interrupt_finish_reason(self) -> str:
        if self.cancel_event.is_set():
            resolver = self._cancel_finish_reason_fn
            if callable(resolver):
                reason = resolver()
                if reason:
                    return reason
            return "cancelled"
        return "cancelled"

    def request_cancel(self) -> None:
        self.cancel_event.set()
        cancel_request = getattr(self.llm, "cancel_current_request", None)
        if callable(cancel_request):
            cancel_request()

    @classmethod
    def create(
        cls,
        session: Session,
        *,
        llm: ChatClient | None = None,
        confirm_fn: Any | None = None,
        tool_loop_max: int = DEFAULT_TOOL_LOOP_MAX,
        stream_handlers: StreamHandlers | None = None,
    ) -> Agent:
        registry = ToolRegistry.load(session.paths)
        allowed = session_evolved_allowlist(session, registry=registry)
        executor = ToolExecutor.create(
            paths=session.paths,
            session_dir=session.session_dir,
            allowed_evolved=allowed,
            confirm_fn=confirm_fn,
        )
        executor.session.turn_mode = session.meta.turn_mode
        agent = cls(
            session=session,
            executor=executor,
            llm=llm or LLMClient(),
            tool_loop_max=tool_loop_max,
            stream_handlers=stream_handlers,
        )
        agent.executor.on_registry_reloaded = agent._sync_allowed_evolved
        return agent

    def _sync_allowed_evolved(self) -> None:
        self.executor.session.allowed_evolved = session_evolved_allowlist(
            self.session,
            registry=self.executor.registry,
        )

    def _sync_turn_mode(self) -> None:
        from project_mode import get_delivery_profile
        from session import normalize_harness, normalize_terminal_path_field

        self.executor.session.turn_mode = self.session.meta.turn_mode
        self.executor.session.scaffold_tool_turn = self.session.scaffold_tool_turn
        self.executor.session.active_shell = self.session.meta.active_shell
        self.executor.session.project_root = self.session.meta.project_root
        self.executor.session.project_id = self.session.meta.project_id
        self.executor.session.project_plan_status = self.session.meta.project_plan_status
        self.executor.session.project_delivery_profile = get_delivery_profile(
            self.session.meta
        )
        self.executor.session.harness = normalize_harness(self.session.meta.harness)
        self.executor.session.terminal_scope_kind = str(
            self.session.meta.terminal_scope_kind or ""
        ).strip()
        self.executor.session.terminal_cwd = normalize_terminal_path_field(
            self.session.meta.terminal_cwd
        )
        self.executor.session.terminal_foreign_root = normalize_terminal_path_field(
            self.session.meta.terminal_foreign_root,
            relative=False,
        )
        self.executor.session.terminal_host_id = str(
            self.session.meta.terminal_host_id or ""
        ).strip()

    def _emit_turn_event(self, event: dict[str, Any]) -> None:
        handler = self.on_turn_event
        if handler is not None:
            handler(event)

    def _patch_last_assistant_message(self, content: str) -> None:
        for index in range(len(self.session.messages) - 1, -1, -1):
            msg = self.session.messages[index]
            if msg.get("role") == "assistant" and isinstance(msg.get("content"), str):
                self.session.messages[index] = {**msg, "content": content}
                return

    def _should_auto_spawn_checker(self) -> bool:
        from runtime_guards import checker_auto_on_scaffold
        from subagent import find_auto_checker_target

        if not checker_auto_on_scaffold():
            return False
        if not self.session.scaffold_tool_turn:
            return False
        if self.session.meta.active_shell != "grow":
            return False
        return find_auto_checker_target(self.executor.session) is not None

    def _emit_checker_verdict(self, result: Any) -> None:
        from subagent import format_checker_verdict_notice, format_subagent_overlay

        verdict = result.verdict or "fail"
        tool_name = result.tool_name or result.task
        self._emit_turn_event(
            {
                "type": "checker.verdict",
                "tool_name": tool_name,
                "verdict": verdict,
            }
        )
        self._emit_turn_event(
            {
                "type": "turn.notice",
                "level": "info",
                "text": format_checker_verdict_notice(tool_name, verdict),
            }
        )
        overlay = format_subagent_overlay(result)
        self._emit_turn_event({"type": "turn.notice", "level": "info", "text": overlay})

    def _run_auto_checker_after_scaffold(self) -> Any | None:
        """Spawn checker after grow scaffold auto demo (T-1620 / T-1622)."""
        from subagent import (
            SubagentRunner,
            checker_task_from_demo_record,
            find_auto_checker_target,
            format_subagent_overlay,
        )

        record = find_auto_checker_target(self.executor.session)
        if record is None:
            return None

        tool_name = record.tool_name
        self._emit_turn_event(
            {
                "type": "turn.notice",
                "level": "info",
                "text": f"[内核] 自动验收 {tool_name}…",
            }
        )

        runner = SubagentRunner(
            paths=self.session.paths,
            evolve_log=EvolveLog.for_agent(self.session.paths),
        )
        result = runner.run_checker(
            checker_task_from_demo_record(record),
            session=self.session,
            llm=self.llm,
            confirm_fn=self.executor.confirm_fn,
            cancel_event=self.cancel_event,
        )

        self.session.scaffold_check_status = result.verdict
        self.session.scaffold_check_tool = tool_name
        self.session.subagent_overlay = format_subagent_overlay(result)
        self._emit_checker_verdict(result)
        return result

    def _finalize_scaffold_checker(
        self,
        *,
        final_text: str,
        finish_reason: str | None,
    ) -> tuple[str, str | None, bool, str | None, int]:
        """Run auto checker and apply completion gate (T-1620–T-1623)."""
        checker_used = False
        checker_verdict: str | None = None
        checker_tool_rounds = 0

        if finish_reason in {"cancelled", "timeout", "context_switched"}:
            return final_text, finish_reason, checker_used, checker_verdict, checker_tool_rounds
        if not self._should_auto_spawn_checker():
            return final_text, finish_reason, checker_used, checker_verdict, checker_tool_rounds

        try:
            result = self._run_auto_checker_after_scaffold()
        except LLMCancelledError:
            return final_text, "cancelled", checker_used, checker_verdict, checker_tool_rounds

        if result is None:
            return final_text, finish_reason, checker_used, checker_verdict, checker_tool_rounds

        checker_used = True
        checker_verdict = result.verdict
        checker_tool_rounds = result.tool_rounds

        if checker_verdict == "fail" and (self.session.project_id or "").strip():
            try:
                from plan_agent import get_plan_agent
                from project_mode import get_delivery_profile

                get_plan_agent(
                    self.session.paths,
                    self.session.project_id,
                ).emit_bug_promote_from_review(
                    result.summary,
                    source="checker",
                    delivery_profile=get_delivery_profile(self.session.meta),
                    verdict=checker_verdict,
                )
            except Exception:
                pass

        if final_text:
            gated = apply_scaffold_completion_gate(final_text, checker_verdict)
            if gated != final_text:
                final_text = gated
                self._patch_last_assistant_message(gated)

        return final_text, finish_reason, checker_used, checker_verdict, checker_tool_rounds

    def _apply_service_postcondition_gate(
        self,
        *,
        final_text: str,
        finish_reason: str | None,
    ) -> str:
        """G14: rewrite '已启动/可访问' claims when run_service postcondition unmet."""
        from session import is_terminal_harness

        if is_terminal_harness(self.session.meta):
            return final_text
        if finish_reason in {"cancelled", "timeout", "context_switched"}:
            return final_text
        if not final_text:
            return final_text
        gated = apply_service_postcondition_gate(final_text, self.session.messages)
        if gated == final_text:
            return final_text
        self._patch_last_assistant_message(gated)
        self.executor.session.postcondition_claim_blocked = True
        try:
            self.executor._emit_turn_evidence()
        except Exception:
            pass
        self._emit_turn_event(
            {
                "type": "turn.notice",
                "level": "warn",
                "text": "成功声明未满足后置条件（进程未就绪），已拦截口头「已启动/可访问」。",
            }
        )
        try:
            log = self.executor.evolve_log
            if log is not None:
                log.log_guard_event(
                    guard_type="service_postcondition",
                    conversation_id=self.session.conversation_id,
                    preview=final_text[:240],
                )
        except Exception:
            pass
        return gated

    def _resolve_parent_loop_max(self, intent: str) -> int:
        """Budget follows turn_mode (T-907); intent does not cap tool rounds."""
        if intent == "recall":
            return 1
        if self.session.meta.turn_mode == "agent":
            return parent_execute_segment_max(
                active_shell=self.session.meta.active_shell,
            )
        return min(self.tool_loop_max, parent_short_max())

    def _run_parent_tool_loop(
        self,
        *,
        max_rounds: int,
        tools: list[dict[str, Any]],
        model: str,
        segment_start_index: int,
        qa_soft_reminder: bool = False,
        recall_soft_reminder: bool = False,
    ) -> ToolLoopSegmentResult:
        from context import (
            FIRST_COMPACT_USER_MESSAGE,
            build_llm_messages,
            build_llm_messages_with_optional_trim,
            load_context_config,
            maybe_auto_compact,
            session_memory_event,
            should_auto_compact,
        )
        from loader import build_system_prompt
        from llm_client import (
            build_chat_messages,
            llm_usage_event,
            load_config,
            resolve_model_entry,
        )

        tool_rounds = 0
        segment_retried = False
        final_text = ""
        finish_reason: str | None = None
        reminder_injected = False
        recall_injected = False
        action_nudge_injected = False
        tools_payload: list[dict[str, Any]] | None = tools if tools else None

        loaded_system = build_system_prompt(self.session)
        static_system = loaded_system.static_prompt
        dynamic_system = loaded_system.dynamic_prompt
        model_entry = resolve_model_entry(model)
        vendor = model_entry.vendor if model_entry is not None else ""

        def _refresh_dynamic_system() -> None:
            nonlocal loaded_system, dynamic_system
            loaded_system = build_system_prompt(self.session)
            dynamic_system = loaded_system.dynamic_prompt

        for _ in range(max_rounds):
            if self.cancel_event.is_set():
                return ToolLoopSegmentResult(
                    final_text="",
                    tool_rounds=tool_rounds,
                    finish_reason=self._interrupt_finish_reason(),
                    exceeded=False,
                    had_progress=segment_messages_show_progress(
                        self.session.messages,
                        segment_start_index,
                    ),
                    qa_soft_reminder_injected=reminder_injected,
                    action_announce_nudge_injected=action_nudge_injected,
                )
            full_system = loaded_system.prompt
            if self.cancel_event.is_set():
                return ToolLoopSegmentResult(
                    final_text="",
                    tool_rounds=tool_rounds,
                    finish_reason=self._interrupt_finish_reason(),
                    exceeded=False,
                    had_progress=segment_messages_show_progress(
                        self.session.messages,
                        segment_start_index,
                    ),
                    qa_soft_reminder_injected=reminder_injected,
                    action_announce_nudge_injected=action_nudge_injected,
                )
            if should_auto_compact(full_system, self.session, model=model):
                self._emit_turn_event(
                    {
                        "type": "turn.notice",
                        "level": "info",
                        "text": "正在压缩对话摘要…",
                    }
                )
            compact_result = maybe_auto_compact(
                self.session,
                full_system,
                self.llm,
                on_summarize_begin=self._compact_summarize_wall_begin,
                on_summarize_end=self._compact_summarize_wall_end,
            )
            if compact_result and compact_result.compacted:
                self._emit_turn_event(
                    {
                        "type": "turn.notice",
                        "level": "info",
                        "text": compact_result.message,
                    }
                )
                if compact_result.digest_section == 1:
                    keep = load_context_config().keep_turns
                    self._emit_turn_event(
                        {
                            "type": "turn.notice",
                            "level": "info",
                            "text": FIRST_COMPACT_USER_MESSAGE.format(keep_turns=keep),
                        }
                    )
                self._emit_turn_event(session_memory_event(self.session))
                _refresh_dynamic_system()
                full_system = loaded_system.prompt
            if compact_result and compact_result.compacted:
                working, trimmed = build_llm_messages_with_optional_trim(
                    self.session,
                    full_system,
                    model,
                    force_trim=True,
                )
                if trimmed:
                    self._emit_turn_event(
                        {
                            "type": "turn.notice",
                            "level": "info",
                            "text": "压缩后上下文仍较大，已截短 tool 输出以便续聊。",
                        }
                    )
            else:
                working = build_llm_messages(self.session)

            if recall_soft_reminder and not recall_injected:
                self.session.append_message(
                    {"role": "user", "content": RECALL_SOFT_REMINDER_MESSAGE}
                )
                recall_injected = True

            try:
                if getattr(self.executor.session, "segment_failure_budget_hit", False) or getattr(
                    self.executor.session, "inline_write_guard_blocked", False
                ):
                    tools_payload = None
                self._emit_turn_event({"type": "llm.pending"})
                response = self.llm.chat(
                    build_chat_messages(
                        working,
                        system_prompt=full_system,
                        static_system=static_system,
                        dynamic_system=dynamic_system,
                        vendor=vendor,
                    ),
                    model=model,
                    tools=tools_payload,
                    temperature=MAIN_LOOP_TEMPERATURE,
                    reasoning_effort=self.session.meta.reasoning_effort,
                    stream=self.stream_handlers,
                )
                usage_event = llm_usage_event(response.usage)
                if usage_event is not None:
                    self._emit_turn_event(usage_event)
            except LLMCancelledError:
                return ToolLoopSegmentResult(
                    final_text="",
                    tool_rounds=tool_rounds,
                    finish_reason=self._interrupt_finish_reason(),
                    exceeded=False,
                    had_progress=segment_messages_show_progress(
                        self.session.messages,
                        segment_start_index,
                    ),
                    qa_soft_reminder_injected=reminder_injected,
                    action_announce_nudge_injected=action_nudge_injected,
                )
            except (LLMApiError, LLMNetworkError) as exc:
                if not segment_retried:
                    segment_retried = True
                    err_kind = type(exc).__name__.replace("LLM", "").replace("Error", "").lower()
                    self._emit_turn_event(
                        {
                            "type": "turn.notice",
                            "level": "warn",
                            "text": f"LLM {err_kind}，2s 后重试…（{exc}）",
                        }
                    )
                    time.sleep(2.0)
                    continue
                return ToolLoopSegmentResult(
                    final_text="",
                    tool_rounds=tool_rounds,
                    finish_reason="error",
                    exceeded=False,
                    had_progress=segment_messages_show_progress(
                        self.session.messages,
                        segment_start_index,
                    ),
                    qa_soft_reminder_injected=reminder_injected,
                    action_announce_nudge_injected=action_nudge_injected,
                )
            except LLMTimeoutError:
                timeout_sec = int(load_config().timeout_sec)
                self._emit_turn_event(
                    {
                        "type": "turn.notice",
                        "level": "warn",
                        "text": f"LLM 请求超时（{timeout_sec}s），本回合已停止。",
                    }
                )
                return ToolLoopSegmentResult(
                    final_text="",
                    tool_rounds=tool_rounds,
                    finish_reason="timeout",
                    exceeded=False,
                    had_progress=segment_messages_show_progress(
                        self.session.messages,
                        segment_start_index,
                    ),
                    qa_soft_reminder_injected=reminder_injected,
                    action_announce_nudge_injected=action_nudge_injected,
                )
            # after SSE stream returns, check cancel before processing tool calls
            if self.cancel_event.is_set():
                return ToolLoopSegmentResult(
                    final_text="",
                    tool_rounds=tool_rounds,
                    finish_reason=self._interrupt_finish_reason(),
                    exceeded=False,
                    had_progress=segment_messages_show_progress(
                        self.session.messages,
                        segment_start_index,
                    ),
                    qa_soft_reminder_injected=reminder_injected,
                    action_announce_nudge_injected=action_nudge_injected,
                )

            finish_reason = response.finish_reason

            if not response.tool_calls:
                final_text = (response.content or "").strip()
                # Agent mode: announced side-effect without tools → nudge once, continue.
                nudge_message = pending_action_nudge_message(final_text)
                if (
                    nudge_message
                    and tools_payload
                    and not recall_soft_reminder
                    and self.session.meta.turn_mode == "agent"
                    and not action_nudge_injected
                    and max_rounds > 1
                ):
                    self.session.append_message(
                        {"role": "assistant", "content": final_text}
                    )
                    self.session.append_message(
                        {"role": "user", "content": nudge_message}
                    )
                    action_nudge_injected = True
                    guard_type = (
                        "orchestration_defer_no_tools"
                        if announces_orchestration_defer(final_text)
                        else "action_announce_no_tools"
                    )
                    notice = (
                        "检测到口头延期起服，已要求本回合调用 run_service wait/logs。"
                        if guard_type == "orchestration_defer_no_tools"
                        else "检测到空头动作声明，已要求本回合调用工具。"
                    )
                    self._emit_turn_event(
                        {
                            "type": "turn.notice",
                            "level": "warn",
                            "text": notice,
                        }
                    )
                    try:
                        log = self.executor.evolve_log
                        if log is not None:
                            log.log_guard_event(
                                guard_type=guard_type,
                                conversation_id=self.session.conversation_id,
                                preview=final_text[:240],
                            )
                    except Exception:
                        pass
                    continue
                if final_text:
                    assistant_msg = {"role": "assistant", "content": final_text}
                    self.session.append_message(assistant_msg)
                break

            tool_rounds += 1
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": response.content,
                "tool_calls": response.tool_calls,
            }
            if response.reasoning_content:
                assistant_msg["reasoning_content"] = response.reasoning_content
            self.session.append_message(assistant_msg)

            dynamic_dirty = False
            for tool_call in response.tool_calls:
                if self.cancel_event.is_set():
                    break
                tool_name = ""
                try:
                    tool_name, arguments = _parse_tool_call(tool_call)
                except ToolCallArgumentError as exc:
                    result = _tool_result_for_argument_error(exc)
                else:
                    result = self.executor.run(tool_name, arguments)

                if tool_name in {"deliverable_review", "explore"} and result.ok:
                    pending = getattr(self.executor.session, "subagent_overlay_pending", None)
                    if pending:
                        existing = (self.session.subagent_overlay or "").strip()
                        self.session.subagent_overlay = (
                            f"{existing}\n\n{pending}" if existing else pending
                        )
                        self.executor.session.subagent_overlay_pending = None
                        dynamic_dirty = True
                if tool_name == "deliverable_review" and result.ok:
                    data = result.data if isinstance(result.data, dict) else {}
                    verdict = str(data.get("verdict") or "").strip() or None
                    if verdict:
                        self.session.last_review_verdict = verdict
                    blockers = int(
                        getattr(self.executor.session, "last_review_blockers_count", 0) or 0
                    )
                    self.session.last_review_blockers_count = blockers

                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", ""),
                    "content": to_json(result),
                }
                self.session.append_message(tool_message)

                if not result.ok:
                    from progress_gate import (
                        is_progress_gate_tool_error,
                        PROGRESS_GATE_G9_KERNEL_MESSAGE,
                    )

                    if is_progress_gate_tool_error(result):
                        self.session.append_message(
                            {"role": "user", "content": PROGRESS_GATE_G9_KERNEL_MESSAGE}
                        )
                        self._emit_turn_event(
                            {
                                "type": "turn.notice",
                                "level": "warn",
                                "text": "进度闸门拒勾：禁止口头完成收口。",
                            }
                        )

                interrupt_kind = _tool_interrupt_kind(result)
                if interrupt_kind and self.cancel_event.is_set():
                    # Wall-clock auto-stop also surfaces as tool "cancelled".
                    fr = self._interrupt_finish_reason()
                    if fr == "timeout":
                        interrupt_kind = "timeout"
                if interrupt_kind:
                    label = _tool_interrupt_label(result, tool_name)
                    notice = format_tool_interrupt_notice(
                        interrupt_kind, tool_label=label
                    )
                    self._emit_turn_event(
                        {
                            "type": "turn.notice",
                            "level": "warn",
                            "text": notice,
                        }
                    )
                    self.session.append_message(
                        {
                            "role": "user",
                            "content": format_tool_interrupt_kernel_message(
                                interrupt_kind, tool_label=label
                            ),
                        }
                    )

                # G14: circuit just opened → inject kernel nudge once per open.
                opened_fp = getattr(self.executor.session, "circuit_just_opened", "") or ""
                if opened_fp:
                    from exec_reliability import EXEC_CIRCUIT_NUDGE_MESSAGE

                    self.executor.session.circuit_just_opened = ""
                    self.session.append_message(
                        {"role": "user", "content": EXEC_CIRCUIT_NUDGE_MESSAGE}
                    )
                    self._emit_turn_event(
                        {
                            "type": "turn.notice",
                            "level": "warn",
                            "text": "同类失败已熔断，请换策略或停下说明。",
                        }
                    )

                # AGENT-HARNESS P5: segment failure budget → stop tools, ask for text close.
                if getattr(self.executor.session, "segment_failure_budget_just_hit", False):
                    from exec_reliability import EXEC_SEGMENT_FAILURE_NUDGE_MESSAGE

                    self.executor.session.segment_failure_budget_just_hit = False
                    self.session.append_message(
                        {"role": "user", "content": EXEC_SEGMENT_FAILURE_NUDGE_MESSAGE}
                    )
                    self._emit_turn_event(
                        {
                            "type": "turn.notice",
                            "level": "warn",
                            "text": "本段失败次数已达上限，请说明根因或换策略。",
                        }
                    )
                    tools_payload = None
                    break

                # BUG-024: repeat inline_write_max → stop tools, force staging path.
                if getattr(self.executor.session, "inline_write_guard_just_blocked", False):
                    from exec_reliability import EXEC_INLINE_WRITE_NUDGE_MESSAGE

                    self.executor.session.inline_write_guard_just_blocked = False
                    self.session.append_message(
                        {"role": "user", "content": EXEC_INLINE_WRITE_NUDGE_MESSAGE}
                    )
                    self._emit_turn_event(
                        {
                            "type": "turn.notice",
                            "level": "warn",
                            "text": (
                                "内联写入多次超限，已停止工具；"
                                "请改用 _staging + content_workspace_path"
                            ),
                        }
                    )
                    tools_payload = None
                    break

                # —— retry on recoverable validation errors (TOOL-RETRY) ——
                if (
                    not result.ok
                    and _is_retryable(result)
                    and tool_rounds > 0
                ):
                    error = result.error
                    error_text = error.message if error else str(result)
                    details = getattr(error, "details", None) or {}
                    received = details.get("received_keys", [])
                    hint_parts = [
                        f"[内核] 工具调用失败，请修正后重试（不消耗回合配额）：",
                        f"  {tool_name}: {error_text}",
                    ]
                    if received:
                        hint_parts.append(f"  你传入了这些键：{', '.join(received)}")
                    expected = details.get("expected")
                    if isinstance(expected, dict):
                        params = [f"{k} ({v})" for k, v in expected.items()]
                        hint_parts.append(f"  期望参数：{', '.join(params)}")
                    # don't count this round; LLM retries next iteration
                    tool_rounds -= 1
                    self.session.append_message(
                        {"role": "user", "content": "\n".join(hint_parts)}
                    )
                    break  # stop processing remaining tool_calls from this response
                if (
                    result.ok
                    and tool_name == "propose_context_switch"
                    and isinstance(result.data, dict)
                    and result.data.get("session_replaced")
                ):
                    new_cid = result.data.get("session_id")
                    if isinstance(new_cid, str) and new_cid.strip():
                        self.session.save()
                        self.session = Session.load(
                            self.session.paths,
                            new_cid.strip(),
                        )
                        self.executor.session = ExecutorSession.load(
                            self.session.session_dir,
                            allowed_evolved=session_evolved_allowlist(
                                self.session,
                                registry=self.executor.registry,
                            ),
                        )
                        self._sync_turn_mode()
                        loaded_system = build_system_prompt(self.session)
                        static_system = loaded_system.static_prompt
                        dynamic_system = loaded_system.dynamic_prompt
                        # M0: do not continue the tool loop on the old session.
                        notice = str(result.data.get("message") or "已切换上下文")
                        self.session.append_message(
                            {"role": "assistant", "content": notice}
                        )
                        self.session.save()
                        return ToolLoopSegmentResult(
                            final_text=notice,
                            tool_rounds=tool_rounds,
                            finish_reason="context_switched",
                            exceeded=False,
                            had_progress=True,
                        )

            if dynamic_dirty:
                _refresh_dynamic_system()

            if self.cancel_event.is_set():
                notice = ""
                if tool_rounds > 0:
                    kind = self._interrupt_finish_reason()
                    if kind not in {"timeout", "cancelled"}:
                        kind = "cancelled"
                    notice = format_tool_interrupt_notice(
                        kind, tool_label="本回合工具"
                    )
                    self.session.append_message(
                        {"role": "assistant", "content": notice}
                    )
                return ToolLoopSegmentResult(
                    final_text=notice,
                    tool_rounds=tool_rounds,
                    finish_reason=self._interrupt_finish_reason(),
                    exceeded=False,
                    had_progress=segment_messages_show_progress(
                        self.session.messages,
                        segment_start_index,
                    ),
                    qa_soft_reminder_injected=reminder_injected,
                    action_announce_nudge_injected=action_nudge_injected,
                )

            if (
                qa_soft_reminder
                and not reminder_injected
                and max_rounds > 1
                and tool_rounds == max_rounds - 1
            ):
                self.session.append_message(
                    {"role": "user", "content": QA_SOFT_REMINDER_MESSAGE}
                )
                reminder_injected = True
        else:
            had_progress = segment_messages_show_progress(
                self.session.messages,
                segment_start_index,
            )
            return ToolLoopSegmentResult(
                final_text="",
                tool_rounds=tool_rounds,
                finish_reason="tool_loop_exceeded",
                exceeded=True,
                had_progress=had_progress,
                qa_soft_reminder_injected=reminder_injected,
                action_announce_nudge_injected=action_nudge_injected,
            )

        had_progress = segment_messages_show_progress(
            self.session.messages,
            segment_start_index,
        )
        return ToolLoopSegmentResult(
            final_text=final_text,
            tool_rounds=tool_rounds,
            finish_reason=finish_reason,
            exceeded=False,
            had_progress=had_progress,
            qa_soft_reminder_injected=reminder_injected,
            action_announce_nudge_injected=action_nudge_injected,
        )

    def _run_execute_segments(
        self,
        *,
        intent: str,
        subagent_used: bool,
        subagent_tool_rounds: int,
        tools: list[dict[str, Any]],
        model: str,
        qa_soft_reminder: bool = False,
    ) -> TurnResult:
        from loader import (
            format_segment_pause_message,
            format_tool_loop_user_message,
            format_total_cap_message,
        )

        segment_max = parent_execute_segment_max(
            active_shell=self.session.meta.active_shell,
        )
        total_max = parent_execute_total_max()
        auto_continue = auto_continue_enabled(
            active_shell=self.session.meta.active_shell,
            harness=self.session.meta.harness,
        )

        total_tool_rounds = 0
        segment = 1
        notices: list[str] = []
        final_text = ""
        finish_reason: str | None = None
        tool_loop_exceeded = False

        while total_tool_rounds < total_max:
            remaining = total_max - total_tool_rounds
            this_segment_max = min(segment_max, remaining)
            segment_start_index = len(self.session.messages)
            self.executor.begin_execute_segment()

            if segment > 1:
                notice = f"…继续执行 (segment {segment}/…)"
                notices.append(notice)
                self.session.append_message(
                    {
                        "role": "user",
                        "content": (
                            f"[内核] 继续 execute segment {segment}"
                            f"（累计 {total_tool_rounds} 轮工具调用）"
                        ),
                    }
                )
                segment_start_index = len(self.session.messages)

            loop_result = self._run_parent_tool_loop(
                max_rounds=this_segment_max,
                tools=tools,
                model=model,
                segment_start_index=segment_start_index,
                qa_soft_reminder=qa_soft_reminder,
            )
            total_tool_rounds += loop_result.tool_rounds

            if loop_result.finish_reason in {"cancelled", "timeout"}:
                finish_reason = loop_result.finish_reason
                if loop_result.final_text:
                    final_text = loop_result.final_text
                break

            if loop_result.final_text:
                final_text = loop_result.final_text
                finish_reason = loop_result.finish_reason
                if is_delivery_complete(final_text):
                    break
                break

            if not loop_result.exceeded:
                break

            if loop_result.had_progress and auto_continue and total_tool_rounds < total_max:
                segment += 1
                continue

            tool_loop_exceeded = True
            if loop_result.had_progress:
                final_text = format_segment_pause_message(
                    segment=segment,
                    total_tool_rounds=total_tool_rounds,
                    auto_continue=auto_continue,
                )
                finish_reason = "segment_cap_pause"
            else:
                final_text = format_tool_loop_user_message(
                    self.session,
                    tool_rounds=loop_result.tool_rounds,
                    tool_loop_max=this_segment_max,
                    registry=self.executor.registry,
                    segment=segment,
                    total_tool_rounds=total_tool_rounds,
                )
                finish_reason = "tool_loop_exceeded"
            self.session.append_message({"role": "assistant", "content": final_text})
            break

        if (
            finish_reason not in {"cancelled", "timeout"}
            and total_tool_rounds >= total_max
            and not final_text
        ):
            tool_loop_exceeded = True
            final_text = format_total_cap_message(
                total_tool_rounds=total_tool_rounds,
                total_max=total_max,
            )
            finish_reason = "total_cap_exceeded"
            self.session.append_message({"role": "assistant", "content": final_text})

        (
            final_text,
            finish_reason,
            checker_used,
            checker_verdict,
            checker_tool_rounds,
        ) = self._finalize_scaffold_checker(
            final_text=final_text,
            finish_reason=finish_reason,
        )

        final_text = self._apply_service_postcondition_gate(
            final_text=final_text,
            finish_reason=finish_reason,
        )

        final_text, finish_reason = self._apply_task_stop_finish(
            final_text=final_text,
            finish_reason=finish_reason,
        )

        self.session.save()
        self.executor.end_execute_segments()
        return TurnResult(
            assistant_text=final_text,
            tool_rounds=total_tool_rounds,
            finish_reason=finish_reason,
            tool_loop_exceeded=tool_loop_exceeded,
            subagent_used=subagent_used,
            subagent_tool_rounds=subagent_tool_rounds,
            turn_intent=intent,
            execute_segments=segment,
            total_tool_rounds=total_tool_rounds,
            notices=notices,
            checker_used=checker_used,
            checker_verdict=checker_verdict,
            checker_tool_rounds=checker_tool_rounds,
        )

    def _apply_task_stop_finish(
        self,
        *,
        final_text: str,
        finish_reason: str | None,
    ) -> tuple[str, str | None]:
        """Mark turn as task_paused when project checkbox was completed (T-2006)."""
        from session import is_terminal_harness

        if is_terminal_harness(self.session.meta):
            return final_text, finish_reason
        if not self.executor.session.task_stop_armed:
            return final_text, finish_reason
        from project_mode import get_delivery_profile, ritual_task_stop_enabled

        if not ritual_task_stop_enabled(self.session.meta):
            return final_text, finish_reason
        if self.session.meta.active_shell != "project":
            return final_text, finish_reason
        if finish_reason in {
            "cancelled",
            "timeout",
            "error",
            "tool_loop_exceeded",
            "total_cap_exceeded",
            "segment_cap_pause",
        }:
            return final_text, finish_reason

        from loader import ensure_task_paused_text
        from project_mode import first_open_task_line, project_id_from_root

        next_task = None
        root = self.session.meta.project_root or ""
        pid = project_id_from_root(root) or (self.session.meta.project_id or "")
        if pid:
            tasks_path = self.session.paths.workspace / pid / "TASKS.md"
            if tasks_path.is_file():
                next_task = first_open_task_line(tasks_path.read_text(encoding="utf-8"))

        updated = ensure_task_paused_text(
            final_text or "",
            next_open_task=next_task,
            delivery_profile=get_delivery_profile(self.session.meta),
        )
        if updated != (final_text or ""):
            if final_text:
                self._patch_last_assistant_message(updated)
            else:
                self.session.append_message({"role": "assistant", "content": updated})
            self._emit_turn_event(
                {
                    "type": "turn.notice",
                    "level": "info",
                    "text": updated if not final_text else "本项已完成。回复「继续」开始下一项。",
                }
            )
        return updated, "task_paused"

    def _maybe_pre_spawn_plan(
        self,
        user_text: str,
        *,
        force_skip: bool = False,
    ) -> Any | None:
        """Kernel pre-spawn Plan subagent when LLM classifies plan-domain intent (T-3905)."""
        if force_skip:
            return None
        pid = (self.session.meta.project_id or "").strip()
        if not pid:
            return None
        if self.session.meta.turn_mode != "agent":
            return None

        from plan_agent import classify_plan_spawn_intent, get_plan_agent

        decision = classify_plan_spawn_intent(user_text, llm=self.llm, meta=self.session.meta)
        if not decision.spawn:
            return None

        from subagent import SubagentRunner

        preview = user_text if len(user_text) <= 120 else user_text[:117] + "…"
        self._emit_turn_event({"type": "plan.subagent.start", "task_preview": preview})

        try:
            runner = SubagentRunner(
                paths=self.session.paths,
                evolve_log=EvolveLog.for_agent(self.session.paths),
            )
            result = runner.run_plan(
                user_text,
                session=self.session,
                include_recent_user_lines=2,
            )
        except Exception as exc:
            self._emit_turn_event(
                {
                    "type": "plan.subagent.done",
                    "summary": str(exc),
                    "proposal_count": 0,
                    "ok": False,
                }
            )
            return None

        self._emit_turn_event(
            {
                "type": "plan.subagent.done",
                "summary": result.summary,
                "proposal_count": len(result.proposal_ids),
                "proposal_ids": list(result.proposal_ids),
                "adopt_pending": result.adopt_pending,
                "ok": True,
            }
        )

        from project_api import project_state_payload

        agent = get_plan_agent(self.session.paths, pid)
        for payload in (
            project_state_payload(self.session, self.session.paths),
            agent.build_state(self.session),
        ):
            self._emit_turn_event(payload)

        self.executor.session.plan_partner_calls = min(
            self.executor.session.plan_partner_calls + 1,
            99,
        )
        return result

    def run_turn(
        self,
        user_text: str,
        *,
        spawn_explore: bool | None = None,
        force_skip_plan_spawn: bool = False,
    ) -> TurnResult:
        """Append user message, optional explore subagent, then parent tool loop."""
        prepare_session_for_s4(self.session)
        # Sync shell/root onto executor BEFORE allowlist so F1 can see project binding.
        self._sync_turn_mode()
        self._sync_allowed_evolved()
        self.executor._ensure_project_scope_tools_allowed()
        self.session.subagent_overlay = None
        self.session.turn_intent = None
        self.session.scaffold_tool_turn = False
        self.session.scaffold_check_status = None
        self.session.scaffold_check_tool = None

        self.session.append_message({"role": "user", "content": user_text})

        from loader import detect_scaffold_tool_turn
        from turn_intent import classify_turn, intent_label, should_spawn_explore_for_turn

        intent = classify_turn(user_text)
        self.session.turn_intent = intent
        self.session.scaffold_tool_turn = detect_scaffold_tool_turn(user_text)
        self.executor.session.scaffold_tool_turn = self.session.scaffold_tool_turn

        from activity_router import apply_route_topics, infer_topic_scope

        scope = infer_topic_scope(user_text, paths=self.session.paths)
        topics_to_add: tuple[str, ...] = ()
        if scope and scope not in {"common"}:
            topics_to_add = (scope,)
        elif scope == "common":
            topics_to_add = ()

        from project_mode import project_plan_gate_open
        from session import is_terminal_harness

        terminal = is_terminal_harness(self.session.meta)
        if project_plan_gate_open(self.session.meta) and not terminal:
            # Keep active_shell=project during draft (PROJECT-MODE §0e F2).
            # activity_router already routes plan-gate turns to project.
            topics_to_add = topics_to_add or ("coding",)

        topics_changed = apply_route_topics(self.session, topics_to_add)
        if topics_changed:
            self._sync_allowed_evolved()
        self._sync_turn_mode()
        self.executor._ensure_project_scope_tools_allowed()
        self.executor.begin_turn()

        subagent_tool_rounds = 0
        overlay_parts: list[str] = []

        plan_result = self._maybe_pre_spawn_plan(
            user_text,
            force_skip=force_skip_plan_spawn,
        )
        if plan_result is not None:
            from subagent import format_subagent_overlay

            overlay_parts.append(format_subagent_overlay(plan_result))
            subagent_tool_rounds += plan_result.tool_rounds

        spawn_explore_flag = spawn_explore
        if intent == "recall":
            spawn_explore_flag = False
        elif spawn_explore_flag is None:
            spawn_explore_flag = should_spawn_explore_for_turn(
                user_text,
                project_id=self.session.meta.project_id,
                active_shell=self.session.meta.active_shell,
            )

        self._emit_turn_event(
            {
                "type": "turn.start",
                "intent": intent,
                "intent_label": intent_label(intent, spawn_explore=bool(spawn_explore_flag)),
            }
        )
        if project_plan_gate_open(self.session.meta) and intent == "execute" and not terminal:
            self._emit_turn_event(
                {
                    "type": "turn.notice",
                    "level": "warn",
                    "text": (
                        "[项目] 计划未确认：写源码与 run_python 暂不可用；"
                        "计划域改动请用 plan_partner，完成后点「确认开工」。"
                    ),
                }
            )
        if spawn_explore_flag:
            from explore_scope import build_kernel_auto_explore_task
            from subagent import SubagentRunner, format_subagent_overlay

            try:
                runner = SubagentRunner(
                    paths=self.session.paths,
                    evolve_log=EvolveLog.for_agent(self.session.paths),
                )
                explore_task = build_kernel_auto_explore_task(
                    user_text,
                    project_id=self.session.meta.project_id or "",
                    active_shell=self.session.meta.active_shell or "",
                    scaffold_tool_turn=bool(self.session.scaffold_tool_turn),
                )
                explore_result, did_continue = runner.run_explore_with_continue(
                    explore_task,
                    session=self.session,
                    llm=self.llm,
                    confirm_fn=self.executor.confirm_fn,
                    continue_already_used=self.executor.session.explore_continue_used,
                )
            except LLMError as exc:
                self._emit_turn_event(
                    {
                        "type": "turn.notice",
                        "level": "warn",
                        "text": (
                            "[内核] explore 子代理调用失败，主 Agent 直接续查"
                            f"（{exc}）"
                        ),
                    }
                )
            else:
                if explore_result.hit_cap:
                    notice = (
                        "[内核] explore 已达本轮上限"
                        + ("，已续跑一轮；" if did_continue else "；")
                        + "主 Agent 续查…"
                    )
                    self._emit_turn_event(
                        {"type": "turn.notice", "level": "info", "text": notice}
                    )
                if did_continue:
                    self.executor.session.explore_continue_used = True
                overlay_parts.append(format_subagent_overlay(explore_result))
                subagent_tool_rounds += explore_result.tool_rounds

        self.session.subagent_overlay = "\n\n".join(overlay_parts) if overlay_parts else None
        subagent_used = bool(overlay_parts)

        tools = build_llm_tools(self.session, registry=self.executor.registry)
        from llm_routing import resolve_model_id_for_role

        model = resolve_model_id_for_role("main_turn", self.session.meta)

        if intent == "recall":
            tools = []
            loop_max = self._resolve_parent_loop_max(intent)
            segment_start_index = len(self.session.messages)
            loop_result = self._run_parent_tool_loop(
                max_rounds=loop_max,
                tools=tools,
                model=model,
                segment_start_index=segment_start_index,
                recall_soft_reminder=True,
            )
            return self._finish_short_tool_loop(
                loop_result=loop_result,
                loop_max=loop_max,
                intent=intent,
                spawn_explore_flag=subagent_used,
                subagent_tool_rounds=subagent_tool_rounds,
            )

        if self.session.meta.turn_mode == "agent":
            return self._run_execute_segments(
                intent=intent,
                subagent_used=subagent_used,
                subagent_tool_rounds=subagent_tool_rounds,
                tools=tools,
                model=model,
                qa_soft_reminder=(intent == "qa"),
            )

        loop_max = self._resolve_parent_loop_max(intent)
        segment_start_index = len(self.session.messages)
        loop_result = self._run_parent_tool_loop(
            max_rounds=loop_max,
            tools=tools,
            model=model,
            segment_start_index=segment_start_index,
            qa_soft_reminder=(intent == "qa"),
        )
        return self._finish_short_tool_loop(
            loop_result=loop_result,
            loop_max=loop_max,
            intent=intent,
            spawn_explore_flag=subagent_used,
            subagent_tool_rounds=subagent_tool_rounds,
        )

    def _finish_short_tool_loop(
        self,
        *,
        loop_result: ToolLoopSegmentResult,
        loop_max: int,
        intent: str,
        spawn_explore_flag: bool,
        subagent_tool_rounds: int,
    ) -> TurnResult:
        """Complete ask/recall parent short loop (T-702 / T-905)."""
        if loop_result.finish_reason in {"cancelled", "timeout"}:
            self.session.save()
            return TurnResult(
                assistant_text=loop_result.final_text or "",
                tool_rounds=loop_result.tool_rounds,
                finish_reason=loop_result.finish_reason,
                subagent_used=bool(spawn_explore_flag),
                subagent_tool_rounds=subagent_tool_rounds,
                turn_intent=intent,
                total_tool_rounds=loop_result.tool_rounds,
                qa_soft_reminder_injected=loop_result.qa_soft_reminder_injected,
            )

        drift_notice = (
            intent in {"qa", "recall"}
            and loop_result.tool_rounds >= 2
            and not loop_result.exceeded
        )
        if drift_notice:
            self._emit_turn_event(
                {
                    "type": "turn.notice",
                    "level": "warn",
                    "text": TURN_DRIFT_NOTICE,
                }
            )

        if loop_result.final_text:
            from context import session_memory_event

            self.session.save()
            self._emit_turn_event(session_memory_event(self.session))
            return TurnResult(
                assistant_text=loop_result.final_text,
                tool_rounds=loop_result.tool_rounds,
                finish_reason=loop_result.finish_reason,
                subagent_used=bool(spawn_explore_flag),
                subagent_tool_rounds=subagent_tool_rounds,
                turn_intent=intent,
                total_tool_rounds=loop_result.tool_rounds,
                qa_soft_reminder_injected=loop_result.qa_soft_reminder_injected,
            )

        from loader import format_tool_loop_user_message
        from context import session_memory_event

        final_text = format_tool_loop_user_message(
            self.session,
            tool_rounds=loop_result.tool_rounds,
            tool_loop_max=loop_max,
            registry=self.executor.registry,
        )
        self.session.append_message({"role": "assistant", "content": final_text})
        self.session.save()
        self._emit_turn_event(session_memory_event(self.session))
        return TurnResult(
            assistant_text=final_text,
            tool_rounds=loop_result.tool_rounds,
            finish_reason="tool_loop_exceeded",
            tool_loop_exceeded=True,
            subagent_used=bool(spawn_explore_flag),
            subagent_tool_rounds=subagent_tool_rounds,
            turn_intent=intent,
            total_tool_rounds=loop_result.tool_rounds,
            qa_soft_reminder_injected=loop_result.qa_soft_reminder_injected,
        )


def has_anchor_message(session: Session) -> bool:
    if not session.messages:
        return False
    first = session.messages[0]
    if first.get("role") != "user":
        return False
    content = first.get("content")
    return isinstance(content, str) and content.startswith(ANCHOR_HEADER)


def prepare_session_for_s4(session: Session) -> None:
    """Insert §5 anchor block once before main-loop history."""
    if has_anchor_message(session):
        return
    session.messages.insert(0, build_anchor_message(session))
    session.meta.compact_before_index = max(session.meta.compact_before_index, 1)
    session.save()


def _is_retryable(result: ToolResult) -> bool:
    """True when the error is recoverable via parameter correction (TOOL-RETRY).

    Process/command outcomes (exit_code, cancel) must **consume** the tool-round
    budget — free retries are only for schema/argument mistakes.
    """
    if not result.error:
        return False
    if result.error.code == ToolErrorCode.CONFIRM_REJECTED:
        return False  # user explicitly said no
    details = result.error.details or {}
    if details.get("retry") is False:
        return False
    if details.get("guard_type") == "exec_circuit":
        return False
    # Evolved/shell already ran: not a parameter typo.
    if "exit_code" in details:
        return False
    msg = (result.error.message or "").lower()
    if "cancelled" in msg or "timed out" in msg or "timeout" in msg:
        return False
    if details.get("retry") is True:
        return True
    if result.error.code == ToolErrorCode.VALIDATION_ERROR:
        return True
    if result.error.code == ToolErrorCode.TOOL_NOT_FOUND:
        # only retry if there's a close match available
        return bool(details.get("available_tools"))
    return False


def _tool_interrupt_kind(result: ToolResult) -> str | None:
    """Return confirm_rejected / cancelled when the failure is a user interrupt."""
    if result.ok or not result.error:
        return None
    if result.error.code == ToolErrorCode.CONFIRM_REJECTED:
        return "confirm_rejected"
    msg = (result.error.message or "").lower()
    if "cancelled" in msg or "canceled" in msg:
        return "cancelled"
    return None


def _tool_interrupt_label(result: ToolResult, tool_name: str) -> str:
    details = getattr(result.error, "details", None) or {}
    evolved = details.get("tool_name")
    if isinstance(evolved, str) and evolved.strip():
        return evolved.strip()
    if isinstance(result.data, dict):
        inner = result.data.get("tool_name")
        if isinstance(inner, str) and inner.strip():
            return inner.strip()
    return (tool_name or "工具").strip() or "工具"


def _repair_json_arguments(raw: str) -> str:
    """Attempt conservative repair of common LLM JSON argument errors.

    Only fixes unambiguous cases; returns unchanged string otherwise.
    """
    text = raw.strip()
    if not text:
        return text

    # 1. trailing comma before closing brace/bracket: {"a": 1,} → {"a": 1}
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # 2. single-quoted keys/string values at top level: {'a': 'b'} → {"a": "b"}
    if text.startswith("{") and "'" in text and '"' not in text:
        text = re.sub(r"'([^']*)'", r'"\1"', text)

    return text


class ToolCallArgumentError(ValueError):
    """Raised when tool_call.function.arguments is not valid JSON."""

    def __init__(self, tool_name: str, message: str, *, raw_args: str = "") -> None:
        self.tool_name = tool_name
        self.raw_args = raw_args
        super().__init__(message)


def _parse_tool_call(tool_call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    fn = tool_call.get("function")
    if not isinstance(fn, dict):
        raise ValueError("tool_call missing function object")
    name = fn.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("tool_call missing function name")
    tool_name = name.strip()
    raw_args = fn.get("arguments", "{}")
    if isinstance(raw_args, dict):
        return tool_name, raw_args
    if not isinstance(raw_args, str):
        raw_args = str(raw_args)
    try:
        parsed = json.loads(raw_args or "{}")
    except json.JSONDecodeError as exc:
        repair = _repair_json_arguments(raw_args)
        if repair != raw_args:
            try:
                parsed = json.loads(repair)
            except json.JSONDecodeError:
                hint = (
                    "Fix JSON escaping in arguments (nested quotes in write_evolve content often break parsing). "
                    "Prefer content_base64 for multi-line tool.toml / main.py bodies."
                )
                raise ToolCallArgumentError(
                    tool_name,
                    f"tool_call arguments JSON invalid: {exc.msg} (char {exc.pos}). {hint}",
                    raw_args=raw_args[:400],
                ) from exc
        else:
            hint = (
                "Fix JSON escaping in arguments (nested quotes in write_evolve content often break parsing). "
                "Prefer content_base64 for multi-line tool.toml / main.py bodies."
            )
            raise ToolCallArgumentError(
                tool_name,
                f"tool_call arguments JSON invalid: {exc.msg} (char {exc.pos}). {hint}",
                raw_args=raw_args[:400],
            ) from exc
    if not isinstance(parsed, dict):
        raise ToolCallArgumentError(
            tool_name,
            "tool_call arguments must decode to a JSON object",
            raw_args=raw_args[:400],
        )
    return tool_name, parsed


def _tool_result_for_argument_error(exc: ToolCallArgumentError) -> ToolResult:
    details: dict[str, Any] = {"retry": True}
    if exc.raw_args:
        details["arguments_preview"] = exc.raw_args
    return tool_fail(
        exc.tool_name,
        ToolErrorCode.VALIDATION_ERROR,
        str(exc),
        details=details,
    )


@dataclass
class _MockLLM:
    """Scripted chat responses for demos and tests."""

    responses: list[LLMResponse] = field(default_factory=list)
    config: Any = field(default_factory=load_config)

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        reasoning_effort: str | None = None,
        stream: StreamHandlers | None = None,
    ) -> LLMResponse:
        if not self.responses:
            raise RuntimeError("mock LLM has no scripted responses left")
        response = self.responses.pop(0)
        if stream is not None:
            if response.reasoning_content and stream.on_reasoning_delta is not None:
                stream.on_reasoning_delta(response.reasoning_content)
            if response.content and stream.on_content_delta is not None:
                stream.on_content_delta(response.content)
        return response


def _demo_tools() -> None:
    registry = ToolRegistry.load()
    tools = build_builtin_tools(registry=registry)

    assert len(tools) == 8, len(tools)
    print(f"[PASS] build_builtin_tools returns {len(tools)} tools")

    names = [item["function"]["name"] for item in tools]
    expected = [t.name for t in BUILTIN_TOOLS]
    assert names == expected, names
    print(f"[PASS] tool names: {', '.join(names)}")

    for item in tools:
        assert item["type"] == "function"
        fn = item["function"]
        assert isinstance(fn["description"], str) and fn["description"]
        params = fn["parameters"]
        assert params["type"] == "object"
        assert isinstance(params.get("properties"), dict)
        for key in params.get("required", []):
            assert key in params["properties"], f"{fn['name']}: missing required {key!r}"
    print("[PASS] each tool has type=function and valid parameters schema")

    # Evolved tools must not appear as flat LLM functions (TOOLS.md §4.3).
    evolved_names = {tool.name for tool in registry.evolved()}
    flat_names = set(names)
    leaked = evolved_names & flat_names
    assert not leaked, leaked
    assert "write_text" not in flat_names
    print("[PASS] no evolved tools flattened as LLM functions")

    # JSON round-trip (what llm_client.chat will send).
    payload = json.dumps(tools, ensure_ascii=False)
    roundtrip = json.loads(payload)
    assert roundtrip == tools
    print(f"[PASS] JSON-serializable ({len(payload)} chars)")

    catalog = format_session_evolved_catalog([], registry=registry)
    assert "write_text" in catalog
    assert not any(line.startswith("- run_evolved:") for line in catalog.splitlines())
    print("[PASS] format_session_evolved_catalog lists write_text under common")

    empty_topics = format_session_evolved_catalog(["nonexistent-topic"], registry=registry)
    assert "write_text" in empty_topics
    print("[PASS] common tools always in catalog regardless of topics")

    print()
    print("Sample tool definition (read_file):")
    print(json.dumps(build_builtin_tool_definition("read_file"), indent=2, ensure_ascii=False))


def _demo_loop() -> None:
    paths = AgentPaths.discover()
    session_dir = paths.data / "sessions" / "_agent_loop_demo"
    session_dir.mkdir(parents=True, exist_ok=True)

    session = Session(
        conversation_id="_agent_loop_demo",
        session_dir=session_dir,
        goal="Verify agent tool loop",
        meta=SessionMeta(
            topics=[],
            llm_model=resolve_session_model([]),
            updated_at=utc_now_iso(),
            phase="S4",
        ),
        messages=[],
        paths=paths,
    )
    session.save()

    grep_args = json.dumps(
        {"pattern": "T-206", "path": "docs/TASKS.md", "max_results": 1},
        ensure_ascii=False,
    )
    mock = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": "call_grep_1",
                        "type": "function",
                        "function": {"name": "grep", "arguments": grep_args},
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            ),
            LLMResponse(
                model="mock",
                content="Found T-206 in TASKS.md.",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
        ]
    )

    agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")
    result = agent.run_turn("Where is T-206 documented?", spawn_explore=False)

    assert result.tool_rounds == 1
    assert "T-206" in result.assistant_text
    assert has_anchor_message(session)
    assert session.messages[0]["role"] == "user"
    assert ANCHOR_HEADER in session.messages[0]["content"]
    print("[PASS] anchor block inserted before history")

    roles = [msg["role"] for msg in session.messages]
    assert roles.count("tool") == 1
    assert roles[-1] == "assistant"
    print("[PASS] tool loop: user → assistant(tool_calls) → tool → assistant")

    reloaded = Session.load(paths, "_agent_loop_demo")
    assert len(reloaded.messages) == len(session.messages)
    assert reloaded.messages[-1]["content"] == result.assistant_text
    print("[PASS] messages.jsonl persisted across reload")

    stuck = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": "list_dir",
                            "arguments": json.dumps({"path": "docs"}),
                        },
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            )
            for i in range(parent_short_max() + 1)
        ]
    )
    stuck_session = Session(
        conversation_id="_agent_loop_stuck",
        session_dir=paths.data / "sessions" / "_agent_loop_stuck",
        goal="loop cap",
        meta=SessionMeta(
            topics=[],
            llm_model=resolve_session_model([]),
            updated_at=utc_now_iso(),
            phase="S4",
            turn_mode="ask",
        ),
        messages=[],
        paths=paths,
    )
    stuck_session.save()
    stuck_agent = Agent.create(stuck_session, llm=stuck, confirm_fn=lambda _p, _a: "y")
    stuck_result = stuck_agent.run_turn("list forever", spawn_explore=False)
    if not stuck_result.tool_loop_exceeded:
        print("[FAIL] expected tool_loop_exceeded TurnResult")
        raise SystemExit(1)
    print(f"[PASS] tool loop capped at {parent_short_max()} rounds")

    if load_config().api_key:
        try:
            live_session = Session(
                conversation_id="_agent_live",
                session_dir=paths.data / "sessions" / "_agent_live",
                goal="List docs folder",
                meta=SessionMeta(
                    topics=[],
                    llm_model=resolve_session_model([]),
                    updated_at=utc_now_iso(),
                    phase="S4",
                ),
                messages=[],
                paths=paths,
            )
            live_session.save()
            live_agent = Agent.create(live_session, confirm_fn=lambda _p, _a: "y")
            live = live_agent.run_turn(
                "用 list_dir 列出 docs 目录（path=docs，不要 recursive），一句话总结条目数。",
                spawn_explore=False,
            )
            assert live.assistant_text
            print(f"[PASS] live turn ({live.tool_rounds} tool round(s)): {live.assistant_text[:100]!r}")
        except LLMError as exc:
            print(f"[SKIP] live agent turn: {exc}")
    else:
        print("[SKIP] live agent turn: LLM_API_KEY not set")

    _demo_m3_workflow_sort(paths)


def _demo_m3_workflow_sort(paths: AgentPaths) -> None:
    """T-503: mock LLM schedules workflow evolved tool; evolve_log records the call."""
    registry = ToolRegistry.load(paths)
    demo_dir = paths.workspace / "_agent_m3_sort"
    if demo_dir.exists():
        for child in sorted(demo_dir.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
    demo_dir.mkdir(parents=True)
    (demo_dir / "photo.jpg").write_text("jpg", encoding="utf-8")
    (demo_dir / "readme").write_text("no ext", encoding="utf-8")

    rel = "workspace/_agent_m3_sort"
    sort_payload = json.dumps(
        {"tool_name": "sort_by_extension", "arguments": {"path": rel}, "dry_run": False},
        ensure_ascii=False,
    )

    session_dir = paths.data / "sessions" / "_agent_m3_sort"
    session_dir.mkdir(parents=True, exist_ok=True)
    session = Session(
        conversation_id="_agent_m3_sort",
        session_dir=session_dir,
        goal="整理 workspace 下载夹",
        meta=SessionMeta(
            topics=["workflow"],
            llm_model=resolve_session_model(["workflow"]),
            updated_at=utc_now_iso(),
            phase="S4",
        ),
        messages=[],
        paths=paths,
    )
    session.save()

    allow = session_evolved_allowlist(session, registry=registry)
    assert "sort_by_extension" in allow
    assert "write_text" in allow

    log_path = paths.data / "evolve_log.jsonl"
    before = len(read_events(log_path))

    mock = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": "call_sort_1",
                        "type": "function",
                        "function": {"name": "run_evolved", "arguments": sort_payload},
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            ),
            LLMResponse(
                model="mock",
                content="已按扩展名整理 _agent_m3_sort：jpg 与无扩展名文件各 1 个。",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
        ]
    )
    agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")
    result = agent.run_turn(f"用 sort_by_extension 整理 {rel} 目录", spawn_explore=False)

    assert result.tool_rounds == 1
    assert (demo_dir / "jpg" / "photo.jpg").is_file()
    assert (demo_dir / "_no_ext" / "readme").is_file()
    events = read_events(log_path)
    new_calls = [
        event
        for event in events[before:]
        if event.get("event") == "tool_call"
        and event.get("tool") == "run_evolved"
        and event.get("evolved_tool") == "sort_by_extension"
    ]
    assert new_calls
    print("[PASS] T-503: workflow session schedules sort_by_extension; evolve_log recorded")

    for child in sorted(demo_dir.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    demo_dir.rmdir()


def _demo_t706(paths: AgentPaths) -> None:
    """T-706: execute spawns explore first; parent uses overlay without re-reading."""
    registry = ToolRegistry.load(paths)
    session_dir = paths.data / "sessions" / "_agent_t706"
    session_dir.mkdir(parents=True, exist_ok=True)
    session = Session(
        conversation_id="_agent_t706",
        session_dir=session_dir,
        goal="造 coding 工具",
        meta=SessionMeta(
            topics=["coding"],
            llm_model=resolve_session_model(["coding"]),
            updated_at=utc_now_iso(),
            phase="S4",
        ),
        messages=[],
        paths=paths,
    )
    session.save()

    read_args = json.dumps(
        {"path": "evolve/tools/coding/run_demo/tool.toml"},
        ensure_ascii=False,
    )
    write_payload = json.dumps(
        {
            "tool_name": "write_evolve",
            "path": "evolve/tools/coding/bar/main.py",
            "content_base64": __import__("base64").b64encode(
                b'def main():\n    pass\n'
            ).decode("ascii"),
            "on_conflict": "overwrite",
            "arguments": {},
            "dry_run": True,
        },
        ensure_ascii=False,
    )

    log_path = paths.data / "evolve_log.jsonl"
    before = len(read_events(log_path))

    mock = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": "sub_read",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": read_args},
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            ),
            LLMResponse(
                model="mock",
                content=(
                    "run_demo：agent-core 下跑 demo 验收；tool.toml topics=[coding]。"
                    "父代理应仿此结构 write_evolve 造 bar。"
                ),
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
            LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": "parent_write",
                        "type": "function",
                        "function": {"name": "run_evolved", "arguments": write_payload},
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            ),
            LLMResponse(
                model="mock",
                content="已按 run_demo 范例 dry_run 写入 bar 工具骨架。",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
        ]
    )

    agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")
    result = agent.run_turn("按 run_demo 模式造 bar 工具（coding 主题）")

    assert result.subagent_used
    assert result.subagent_tool_rounds == 1
    assert result.tool_rounds == 1
    assert "bar" in result.assistant_text or "run_demo" in result.assistant_text

    parent_reads = [
        msg
        for msg in session.messages
        if msg.get("role") == "assistant"
        and isinstance(msg.get("tool_calls"), list)
        for tc in msg["tool_calls"]
        if isinstance(tc, dict)
        and tc.get("function", {}).get("name") == "read_file"
    ]
    assert not parent_reads, "parent loop should not read_file when subagent overlay present"

    write_results = [
        msg
        for msg in session.messages
        if msg.get("role") == "tool"
        and isinstance(msg.get("content"), str)
        and "write_evolve" in msg.get("content", "")
    ]
    assert write_results, "parent should run write_evolve after explore"
    assert '"ok": true' in write_results[-1]["content"] or "'ok': True" in write_results[-1]["content"]

    from loader import build_system_prompt

    system_before_turn = build_system_prompt(session).prompt
    assert "[子代理摘要 · explore]" in system_before_turn or session.subagent_overlay

    sub_events = [
        e
        for e in read_events(log_path)[before:]
        if e.get("event") == "subagent_run"
    ]
    assert sub_events
    print("[PASS] T-706: execute spawns explore then parent loop (no parent read_file)")


def _demo_t702(paths: AgentPaths) -> None:
    """T-702: ask mode omits run_evolved from LLM tools and executor rejects it."""
    session_dir = paths.data / "sessions" / "_agent_t702"
    session_dir.mkdir(parents=True, exist_ok=True)
    session = Session(
        conversation_id="_agent_t702",
        session_dir=session_dir,
        goal="mode test",
        meta=SessionMeta(
            topics=[],
            llm_model=resolve_session_model([]),
            updated_at=utc_now_iso(),
            phase="S4",
            turn_mode="ask",
        ),
        messages=[],
        paths=paths,
    )
    session.save()

    ask_tools = build_llm_tools(session)
    ask_names = [item["function"]["name"] for item in ask_tools]
    assert "run_evolved" not in ask_names
    assert len(ask_names) == 7
    print("[PASS] T-702: ask mode LLM tools exclude run_evolved")

    agent = Agent.create(session, confirm_fn=lambda _p, _a: "y")
    blocked = agent.executor.validate(
        "run_evolved",
        {"tool_name": "write_text", "arguments": {"path": "_t702.txt", "content": "x"}},
    )
    assert blocked is not None and not blocked.ok
    print("[PASS] T-702: ask mode executor rejects run_evolved")

    session.set_turn_mode("agent")
    agent_tools = build_llm_tools(session)
    assert any(item["function"]["name"] == "run_evolved" for item in agent_tools)
    agent._sync_turn_mode()
    assert agent.executor.validate(
        "run_evolved",
        {"tool_name": "write_text", "arguments": {"path": "_t702.txt", "content": "x"}},
    ) is None
    print("[PASS] T-702: agent mode restores run_evolved")


def _demo_t703(paths: AgentPaths) -> None:
    """T-703: classify_turn drives explore spawn; qa/plan skip subagent."""
    from turn_intent import classify_turn, should_spawn_explore

    session_dir = paths.data / "sessions" / "_agent_t703"
    session_dir.mkdir(parents=True, exist_ok=True)

    qa_session = Session(
        conversation_id="_agent_t703_qa",
        session_dir=session_dir,
        goal="qa",
        meta=SessionMeta(
            topics=[],
            llm_model=resolve_session_model([]),
            updated_at=utc_now_iso(),
            phase="S4",
        ),
        messages=[],
        paths=paths,
    )
    qa_session.save()
    qa_mock = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content="2",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
        ]
    )
    qa_agent = Agent.create(qa_session, llm=qa_mock, confirm_fn=lambda _p, _a: "y")
    qa_result = qa_agent.run_turn("1+1 等于几", spawn_explore=None)
    assert qa_result.turn_intent == "qa"
    assert not qa_result.subagent_used
    assert classify_turn("1+1 等于几") == "qa"
    assert not should_spawn_explore("1+1 等于几")
    print("[PASS] T-703: qa intent → no explore spawn")

    exec_session = Session(
        conversation_id="_agent_t703_exec",
        session_dir=paths.data / "sessions" / "_agent_t703_exec",
        goal="build",
        meta=SessionMeta(
            topics=["coding"],
            llm_model=resolve_session_model(["coding"]),
            updated_at=utc_now_iso(),
            phase="S4",
        ),
        messages=[],
        paths=paths,
    )
    exec_session.save()
    read_args = json.dumps(
        {"path": "evolve/tools/coding/run_demo/tool.toml"},
        ensure_ascii=False,
    )
    exec_mock = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": "sub_r",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": read_args},
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            ),
            LLMResponse(
                model="mock",
                content="run_demo 范例已读。",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
            LLMResponse(
                model="mock",
                content="将按摘要 write_evolve。",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
        ]
    )
    exec_agent = Agent.create(exec_session, llm=exec_mock, confirm_fn=lambda _p, _a: "y")
    exec_result = exec_agent.run_turn("按 run_demo 模式造 bar 工具")
    assert exec_result.turn_intent == "execute"
    assert exec_result.subagent_used
    assert exec_session.turn_intent == "execute"
    from loader import build_system_prompt

    overlay = build_system_prompt(exec_session).prompt
    assert "turn_intent: execute" in overlay
    print("[PASS] T-703: execute intent → auto explore + turn_intent in overlay")


def _write_evolve_payload(name: str) -> str:
    import base64

    body = f'print("{name}")\n'
    return json.dumps(
        {
            "tool_name": "write_evolve",
            "path": f"evolve/tools/coding/{name}/main.py",
            "content_base64": base64.b64encode(body.encode("utf-8")).decode("ascii"),
            "on_conflict": "overwrite",
            "arguments": {},
            "dry_run": True,
        },
        ensure_ascii=False,
    )


def _demo_t705(paths: AgentPaths) -> None:
    """T-705: mock large execute spans 2 segments with write_evolved progress."""
    prev_segment = os.environ.get("PARENT_EXECUTE_SEGMENT_MAX")
    prev_total = os.environ.get("PARENT_EXECUTE_TOTAL_MAX")
    prev_auto = os.environ.get("MY_AGENT_AUTO_CONTINUE")
    os.environ["PARENT_EXECUTE_SEGMENT_MAX"] = "2"
    os.environ["PARENT_EXECUTE_TOTAL_MAX"] = "10"
    os.environ["MY_AGENT_AUTO_CONTINUE"] = "1"

    try:
        session_dir = paths.data / "sessions" / "_agent_t705"
        session_dir.mkdir(parents=True, exist_ok=True)
        session = Session(
            conversation_id="_agent_t705",
            session_dir=session_dir,
            goal="造三个 coding 工具",
            meta=SessionMeta(
                topics=["coding"],
                llm_model=resolve_session_model(["coding"]),
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[],
            paths=paths,
        )
        session.save()

        mock = _MockLLM(
            responses=[
                LLMResponse(
                    model="mock",
                    content=None,
                    tool_calls=[
                        {
                            "id": "seg1_w1",
                            "type": "function",
                            "function": {
                                "name": "run_evolved",
                                "arguments": _write_evolve_payload("foo"),
                            },
                        }
                    ],
                    finish_reason="tool_calls",
                    usage=None,
                    raw={},
                ),
                LLMResponse(
                    model="mock",
                    content=None,
                    tool_calls=[
                        {
                            "id": "seg1_w2",
                            "type": "function",
                            "function": {
                                "name": "run_evolved",
                                "arguments": _write_evolve_payload("bar"),
                            },
                        }
                    ],
                    finish_reason="tool_calls",
                    usage=None,
                    raw={},
                ),
                LLMResponse(
                    model="mock",
                    content=None,
                    tool_calls=[
                        {
                            "id": "seg2_w3",
                            "type": "function",
                            "function": {
                                "name": "run_evolved",
                                "arguments": _write_evolve_payload("baz"),
                            },
                        }
                    ],
                    finish_reason="tool_calls",
                    usage=None,
                    raw={},
                ),
                LLMResponse(
                    model="mock",
                    content="已落地 foo、bar、baz 三个工具骨架。交付完成。",
                    tool_calls=[],
                    finish_reason="stop",
                    usage=None,
                    raw={},
                ),
            ]
        )

        agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")
        result = agent.run_turn("造 foo、bar、baz 三个 coding 工具", spawn_explore=False)

        assert result.turn_intent == "execute"
        assert result.execute_segments == 2
        assert result.total_tool_rounds == 3
        assert is_delivery_complete(result.assistant_text)
        assert any("segment 2" in notice for notice in result.notices)
        assert not result.tool_loop_exceeded

        evolved_calls = [
            msg
            for msg in session.messages
            if msg.get("role") == "assistant"
            and isinstance(msg.get("tool_calls"), list)
            for tc in msg["tool_calls"]
            if tc.get("function", {}).get("name") == "run_evolved"
        ]
        assert len(evolved_calls) == 3

        kernel_continues = [
            msg
            for msg in session.messages
            if msg.get("role") == "user"
            and isinstance(msg.get("content"), str)
            and "[内核] 继续 execute segment 2" in msg["content"]
        ]
        assert kernel_continues
        print("[PASS] T-705: multi-segment execute (2 segments, 3 write_evolved, 交付完成)")
    finally:
        if prev_segment is None:
            os.environ.pop("PARENT_EXECUTE_SEGMENT_MAX", None)
        else:
            os.environ["PARENT_EXECUTE_SEGMENT_MAX"] = prev_segment
        if prev_total is None:
            os.environ.pop("PARENT_EXECUTE_TOTAL_MAX", None)
        else:
            os.environ["PARENT_EXECUTE_TOTAL_MAX"] = prev_total
        if prev_auto is None:
            os.environ.pop("MY_AGENT_AUTO_CONTINUE", None)
        else:
            os.environ["MY_AGENT_AUTO_CONTINUE"] = prev_auto


def _demo_t704(paths: AgentPaths) -> None:
    """T-704: qa short loop injects soft reminder at max-1 tool rounds."""
    prev_short = os.environ.get("PARENT_SHORT_MAX")
    os.environ["PARENT_SHORT_MAX"] = "3"

    try:
        list_args = json.dumps({"path": "docs"}, ensure_ascii=False)
        tool_response = LLMResponse(
            model="mock",
            content=None,
            tool_calls=[
                {
                    "id": "call_list",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": list_args},
                }
            ],
            finish_reason="tool_calls",
            usage=None,
            raw={},
        )
        answer_response = LLMResponse(
            model="mock",
            content="The sky appears blue due to Rayleigh scattering.",
            tool_calls=[],
            finish_reason="stop",
            usage=None,
            raw={},
        )

        qa_session = Session(
            conversation_id="_agent_t704_qa",
            session_dir=paths.data / "sessions" / "_agent_t704_qa",
            goal="qa reminder",
            meta=SessionMeta(
                topics=[],
                llm_model=resolve_session_model([]),
                updated_at=utc_now_iso(),
                phase="S4",
                turn_mode="ask",
            ),
            messages=[],
            paths=paths,
        )
        qa_session.save()
        qa_mock = _MockLLM(responses=[tool_response, tool_response, answer_response])
        qa_agent = Agent.create(qa_session, llm=qa_mock, confirm_fn=lambda _p, _a: "y")
        qa_result = qa_agent.run_turn("Why is the sky blue?", spawn_explore=False)

        assert qa_result.turn_intent == "qa"
        assert qa_result.qa_soft_reminder_injected
        assert qa_result.tool_rounds == 2
        reminder_msgs = [
            msg
            for msg in qa_session.messages
            if msg.get("content") == QA_SOFT_REMINDER_MESSAGE
        ]
        assert len(reminder_msgs) == 1
        reminder_index = qa_session.messages.index(reminder_msgs[0])
        assert qa_session.messages[reminder_index - 1]["role"] == "tool"
        print("[PASS] T-704: qa soft reminder at max-1 tool round")

        plan_session = Session(
            conversation_id="_agent_t704_plan",
            session_dir=paths.data / "sessions" / "_agent_t704_plan",
            goal="plan no reminder",
            meta=SessionMeta(
                topics=[],
                llm_model=resolve_session_model([]),
                updated_at=utc_now_iso(),
                phase="S4",
                turn_mode="ask",
            ),
            messages=[],
            paths=paths,
        )
        plan_session.save()
        plan_mock = _MockLLM(responses=[tool_response, tool_response, answer_response])
        plan_agent = Agent.create(plan_session, llm=plan_mock, confirm_fn=lambda _p, _a: "y")
        plan_result = plan_agent.run_turn("帮我列个 Phase 7 实施计划", spawn_explore=False)

        assert plan_result.turn_intent == "plan"
        assert not plan_result.qa_soft_reminder_injected
        plan_reminders = [
            msg
            for msg in plan_session.messages
            if msg.get("content") == QA_SOFT_REMINDER_MESSAGE
        ]
        assert not plan_reminders
        print("[PASS] T-704: plan intent skips soft reminder")
    finally:
        if prev_short is None:
            os.environ.pop("PARENT_SHORT_MAX", None)
        else:
            os.environ["PARENT_SHORT_MAX"] = prev_short


def _demo_t907(paths: AgentPaths) -> None:
    """T-907: turn_mode drives budget; turn_intent does not cap agent-mode rounds."""
    from turn_intent import classify_turn

    prev_short = os.environ.get("PARENT_SHORT_MAX")
    prev_segment = os.environ.get("PARENT_EXECUTE_SEGMENT_MAX")
    os.environ["PARENT_SHORT_MAX"] = "2"
    os.environ["PARENT_EXECUTE_SEGMENT_MAX"] = "10"

    list_args = json.dumps({"path": "docs"}, ensure_ascii=False)
    tool_response = LLMResponse(
        model="mock",
        content=None,
        tool_calls=[
            {
                "id": "call_list",
                "type": "function",
                "function": {"name": "list_dir", "arguments": list_args},
            }
        ],
        finish_reason="tool_calls",
        usage=None,
        raw={},
    )
    answer_response = LLMResponse(
        model="mock",
        content="推送完成。",
        tool_calls=[],
        finish_reason="stop",
        usage=None,
        raw={},
    )

    try:
        assert classify_turn("推过去") == "qa"

        agent_session = Session(
            conversation_id="_agent_t907_agent",
            session_dir=paths.data / "sessions" / "_agent_t907_agent",
            goal="repl tool",
            meta=SessionMeta(
                topics=["coding"],
                llm_model=resolve_session_model(["coding"]),
                updated_at=utc_now_iso(),
                phase="S4",
                turn_mode="agent",
            ),
            messages=[],
            paths=paths,
        )
        agent_session.save()
        agent_mock = _MockLLM(
            responses=[tool_response, tool_response, tool_response, answer_response]
        )
        agent_obj = Agent.create(agent_session, llm=agent_mock, confirm_fn=lambda _p, _a: "y")
        agent_result = agent_obj.run_turn("推过去", spawn_explore=False)
        assert agent_result.turn_intent == "qa"
        assert agent_result.tool_rounds == 3
        assert not agent_result.tool_loop_exceeded
        assert "推送完成" in agent_result.assistant_text
        print("[PASS] T-907a: agent + qa intent not capped at PARENT_SHORT_MAX")

        ask_session = Session(
            conversation_id="_agent_t907_ask",
            session_dir=paths.data / "sessions" / "_agent_t907_ask",
            goal="qa short",
            meta=SessionMeta(
                topics=[],
                llm_model=resolve_session_model([]),
                updated_at=utc_now_iso(),
                phase="S4",
                turn_mode="ask",
            ),
            messages=[],
            paths=paths,
        )
        ask_session.save()
        ask_mock = _MockLLM(
            responses=[tool_response, tool_response, tool_response, answer_response]
        )
        ask_agent = Agent.create(ask_session, llm=ask_mock, confirm_fn=lambda _p, _a: "y")
        ask_result = ask_agent.run_turn("Why is the sky blue?", spawn_explore=False)
        assert ask_result.tool_loop_exceeded
        assert ask_result.tool_rounds == 2
        print("[PASS] T-907b: ask + qa capped at PARENT_SHORT_MAX")

        recall_session = Session(
            conversation_id="_agent_t907_recall",
            session_dir=paths.data / "sessions" / "_agent_t907_recall",
            goal="recall",
            meta=SessionMeta(
                topics=[],
                llm_model=resolve_session_model([]),
                updated_at=utc_now_iso(),
                phase="S4",
                turn_mode="agent",
            ),
            messages=[],
            paths=paths,
        )
        recall_session.save()
        recall_mock = _MockLLM(
            responses=[
                LLMResponse(
                    model="mock",
                    content="我们刚刚在讨论 repl 工具。",
                    tool_calls=[],
                    finish_reason="stop",
                    usage=None,
                    raw={},
                )
            ]
        )
        recall_agent = Agent.create(
            recall_session, llm=recall_mock, confirm_fn=lambda _p, _a: "y"
        )
        recall_result = recall_agent.run_turn("刚刚我们说了什么", spawn_explore=False)
        assert recall_result.turn_intent == "recall"
        assert recall_result.tool_rounds == 0
        print("[PASS] T-907c: recall + agent still uses no tools")
    finally:
        if prev_short is None:
            os.environ.pop("PARENT_SHORT_MAX", None)
        else:
            os.environ["PARENT_SHORT_MAX"] = prev_short
        if prev_segment is None:
            os.environ.pop("PARENT_EXECUTE_SEGMENT_MAX", None)
        else:
            os.environ["PARENT_EXECUTE_SEGMENT_MAX"] = prev_segment


def _demo_t905(paths: AgentPaths) -> None:
    """T-905: recall intent — no tools, turn.start, soft reminder."""
    from turn_intent import classify_turn

    session_dir = paths.data / "sessions" / "_agent_t905"
    session_dir.mkdir(parents=True, exist_ok=True)
    recall_session = Session(
        conversation_id="_agent_t905",
        session_dir=session_dir,
        goal="recall",
        meta=SessionMeta(
            topics=[],
            llm_model=resolve_session_model([]),
            updated_at=utc_now_iso(),
            phase="S4",
        ),
        messages=[],
        paths=paths,
    )
    recall_session.save()

    @dataclass
    class _TrackingMockLLM(_MockLLM):
        tools_seen: list[list[dict[str, Any]] | None] = field(default_factory=list)

        def chat(
            self,
            messages: list[dict[str, Any]],
            *,
            model: str | None = None,
            tools: list[dict[str, Any]] | None = None,
            temperature: float = 0.0,
            reasoning_effort: str | None = None,
            stream: StreamHandlers | None = None,
        ) -> LLMResponse:
            self.tools_seen.append(tools)
            return super().chat(
                messages,
                model=model,
                tools=tools,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
                stream=stream,
            )

    events: list[dict[str, Any]] = []
    mock = _TrackingMockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content="我们刚才讨论了工具推荐和 code_scaffold。",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
        ]
    )
    agent = Agent.create(recall_session, llm=mock, confirm_fn=lambda _p, _a: "y")
    agent.on_turn_event = events.append

    assert classify_turn("刚刚我们说了什么") == "recall"
    result = agent.run_turn("刚刚我们说了什么", spawn_explore=None)
    assert result.turn_intent == "recall"
    assert result.tool_rounds == 0
    assert mock.tools_seen == [None]
    assert any(event.get("type") == "turn.start" and event.get("intent") == "recall" for event in events)
    assert any(event.get("type") == "session.memory" for event in events)
    recall_msgs = [
        msg for msg in recall_session.messages if msg.get("content") == RECALL_SOFT_REMINDER_MESSAGE
    ]
    assert len(recall_msgs) == 1
    print("[PASS] T-905: recall → no tools, turn.start, recall soft reminder")


def _demo() -> None:
    _demo_tools()
    print()
    _demo_loop()
    print()
    paths = AgentPaths.discover()
    _demo_t706(paths)
    print()
    _demo_t702(paths)
    print()
    _demo_t703(paths)
    print()
    _demo_t705(paths)
    print()
    _demo_t704(paths)
    print()
    _demo_t907(paths)
    print()
    _demo_t905(paths)
    print()
    _demo_tool_call_json_error()
    print()
    _demo_write_evolve_scaffold(paths)


def _demo_tool_call_json_error() -> None:
    """Malformed tool_call arguments must not silently become {}."""
    broken = {
        "id": "call_bad",
        "type": "function",
        "function": {
            "name": "run_evolved",
            "arguments": '{"tool_name":"write_evolve","arguments":{"path":"x","content":"broken quote "}',
        },
    }
    try:
        _parse_tool_call(broken)
        raise AssertionError("expected ToolCallArgumentError")
    except ToolCallArgumentError as exc:
        assert exc.tool_name == "run_evolved"
        assert "content_base64" in str(exc)
        result = _tool_result_for_argument_error(exc)
        assert not result.ok
        assert result.error is not None
        assert result.error.code == ToolErrorCode.VALIDATION_ERROR
    print("[PASS] malformed tool_call JSON → validation error (not silent empty args)")


def _demo_write_evolve_scaffold(paths: AgentPaths) -> None:
    """End-to-end: write_evolve + content_base64 creates a draft tool under evolve/tools/."""
    import base64

    from tools.builtin.run_evolved import run as run_evolved_builtin
    from tools.registry import ToolRegistry

    registry = ToolRegistry.load(paths)
    scope_dir = paths.evolve / "tools" / "common" / "scaffold_demo"
    try:
        main_py = '"""scaffold demo"""\n\nif __name__ == "__main__":\n    print("ok")\n'
        main_b64 = base64.b64encode(main_py.encode("utf-8")).decode("ascii")
        main_result = run_evolved_builtin(
            {
                "tool_name": "write_evolve",
                "path": "evolve/tools/common/scaffold_demo/main.py",
                "content_base64": main_b64,
                "on_conflict": "overwrite",
                "arguments": {},
            },
            registry=registry,
            paths=paths,
        )
        assert main_result.ok, main_result.error

        toml_body = (
            '[tool]\n'
            'name = "scaffold_demo"\n'
            'description = "demo tool with \\"quotes\\""\n'
            'version = "1.0.0"\n'
            'status = "draft"\n'
            'topics = ["common"]\n\n'
            '[entry]\n'
            'type = "python"\n'
            'path = "main.py"\n\n'
            '[schema.input]\n'
            'type = "object"\n\n'
            '[schema.output]\n'
            'type = "object"\n\n'
            '[policy]\n'
            'confirm = true\n'
            'dry_run_supported = true\n'
            'workspace_only = false\n'
            'timeout_sec = 60\n'
        )
        toml_b64 = base64.b64encode(toml_body.encode("utf-8")).decode("ascii")
        toml_result = run_evolved_builtin(
            {
                "tool_name": "write_evolve",
                "path": "evolve/tools/common/scaffold_demo/tool.toml",
                "content_base64": toml_b64,
                "on_conflict": "overwrite",
                "arguments": {},
            },
            registry=registry,
            paths=paths,
        )
        assert toml_result.ok, toml_result.error
        assert (scope_dir / "main.py").is_file()
        assert (scope_dir / "tool.toml").is_file()
        print("[PASS] write_evolve content_base64 scaffolds main.py + tool.toml")
    finally:
        if scope_dir.is_dir():
            for child in scope_dir.iterdir():
                child.unlink()
            scope_dir.rmdir()


if __name__ == "__main__":
    _demo()
