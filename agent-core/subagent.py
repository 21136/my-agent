"""Subagents: explore (T-706) and checker (T-1610) in isolated context."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import ChatClient, ToolCallArgumentError, _parse_tool_call, _tool_result_for_argument_error, build_builtin_tool_definition
from llm_client import LLMCancelledError, LLMResponse, load_config, resolve_session_model
from paths import AgentPaths
from session import Session, SessionMeta, utc_now_iso
from tools.executor import ToolExecutor
from tools.logging import EvolveLog
from tools.registry import ToolManifestError, ToolRegistry, parse_tool_manifest
from tools.schema import to_json
from turn_intent import classify_turn, should_spawn_explore

SubagentKind = Literal["explore", "checker", "plan"]
CheckerKind = Literal["evolve_tool_scaffold"]
CheckStatus = Literal["pass", "fail", "warn"]
Verdict = Literal["pass", "fail", "warn"]

EXPLORE_TOOL_NAMES: tuple[str, ...] = (
    "read_file",
    "list_dir",
    "grep",
    "web_search",
    "fetch_url",
)

CHECKER_TOOL_NAMES: tuple[str, ...] = (
    "read_file",
    "list_dir",
    "grep",
)

_DEFAULT_EXPLORE_MAX = 8
_DEFAULT_CHECKER_MAX = 5
_DEFAULT_SUMMARY_MAX = 4000
_DEFAULT_PLAN_SUMMARY_MAX = 2000
_DEFAULT_CHECKER_SUMMARY_MAX = 3000
_PLAN_PARTNER_MAX_PER_TURN = 2
_CAP_SUMMARY_PROMPT = "请根据已读内容输出摘要（含已读路径与结论）。"
_CHECKER_CLOSE_PROMPT = (
    "请输出验收结论。末行必须是 CHECKER_VERDICT: pass|fail|warn。"
    "若有语义偏差（对照 reference），在正文中说明；纯风格差异标为 warn。"
)


def subagent_explore_max() -> int:
    raw = os.environ.get("SUBAGENT_EXPLORE_MAX", str(_DEFAULT_EXPLORE_MAX))
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_EXPLORE_MAX
    return max(1, value)


def subagent_checker_max() -> int:
    raw = os.environ.get("SUBAGENT_CHECKER_MAX", str(_DEFAULT_CHECKER_MAX))
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_CHECKER_MAX
    return max(1, value)


def subagent_summary_max_chars() -> int:
    raw = os.environ.get("SUBAGENT_SUMMARY_MAX_CHARS", str(_DEFAULT_SUMMARY_MAX))
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_SUMMARY_MAX
    return max(256, value)


def plan_subagent_summary_max_chars() -> int:
    raw = os.environ.get("PLAN_SUBAGENT_SUMMARY_MAX_CHARS", str(_DEFAULT_PLAN_SUMMARY_MAX))
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_PLAN_SUMMARY_MAX
    return max(256, value)


def plan_partner_max_per_turn() -> int:
    raw = os.environ.get("PLAN_PARTNER_MAX_PER_TURN", str(_PLAN_PARTNER_MAX_PER_TURN))
    try:
        value = int(raw)
    except ValueError:
        value = _PLAN_PARTNER_MAX_PER_TURN
    return max(1, value)


def checker_summary_max_chars() -> int:
    raw = os.environ.get("CHECKER_SUMMARY_MAX_CHARS", str(_DEFAULT_CHECKER_SUMMARY_MAX))
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_CHECKER_SUMMARY_MAX
    return max(256, value)


def resolve_checker_model(session_model: str) -> str:
    override = os.environ.get("CHECKER_MODEL", "").strip()
    return override or session_model


def checker_task_from_demo_record(
    record: Any,
    *,
    reference_tool: str | None = None,
) -> CheckerTask:
    """Build CheckerTask from Phase 16 scaffold demo record (T-1622)."""
    demo = record.demo_result if isinstance(getattr(record, "demo_result", None), dict) else {}
    return CheckerTask(
        tool_name=str(getattr(record, "tool_name", "") or "").strip(),
        tool_dir=str(getattr(record, "tool_dir", "") or "").strip(),
        demo_result=dict(demo),
        reference_tool=reference_tool,
    )


def find_auto_checker_target(session: Any) -> Any | None:
    """Latest auto-demo scaffold record eligible for M1 checker spawn."""
    tools = getattr(session, "segment_scaffold_tools", None)
    if not isinstance(tools, dict) or not tools:
        return None
    candidates = [
        record
        for record in tools.values()
        if getattr(record, "auto_demo", False) and record.demo_result.get("attempted")
    ]
    return candidates[-1] if candidates else None


def format_checker_verdict_notice(tool_name: str, verdict: Verdict | str) -> str:
    label = {"pass": "通过", "warn": "警告", "fail": "失败"}.get(str(verdict), str(verdict))
    return f"验收 {tool_name}：{label}"


def build_explore_tools(*, registry: ToolRegistry | None = None) -> list[dict[str, Any]]:
    """Read-only builtin subset for explore subagent (no run_evolved)."""
    reg = registry or ToolRegistry.load()
    for name in EXPLORE_TOOL_NAMES:
        if reg.get_builtin(name) is None:
            raise RuntimeError(f"missing explore builtin: {name!r}")
    return [build_builtin_tool_definition(name) for name in EXPLORE_TOOL_NAMES]


def build_checker_tools(*, registry: ToolRegistry | None = None) -> list[dict[str, Any]]:
    """Read-only builtin subset for checker (no web, no run_evolved)."""
    reg = registry or ToolRegistry.load()
    for name in CHECKER_TOOL_NAMES:
        if reg.get_builtin(name) is None:
            raise RuntimeError(f"missing checker builtin: {name!r}")
    return [build_builtin_tool_definition(name) for name in CHECKER_TOOL_NAMES]


classify_turn_intent = classify_turn


def parse_explore_command(line: str) -> str | None:
    """Return task text for 探索/调研/explore commands, or None."""
    stripped = line.strip()
    if not stripped:
        return None
    lower = stripped.casefold()
    for prefix in ("探索", "调研"):
        if lower.startswith(prefix):
            task = stripped[len(prefix) :].strip()
            return task or None
    if lower.startswith("explore"):
        task = stripped[7:].strip()
        return task or None
    return None


def parse_checker_command(line: str) -> CheckerTask | None:
    """Return CheckerTask for 验收/check commands, or None."""
    stripped = line.strip()
    if not stripped:
        return None
    lower = stripped.casefold()
    tool_name: str | None = None
    reference_tool: str | None = None
    if lower.startswith("验收"):
        rest = stripped[2:].strip()
        if not rest:
            return None
        parts = rest.split()
        tool_name = parts[0].strip()
        if len(parts) >= 3 and parts[1] in {"对照", "参考", "vs"}:
            reference_tool = parts[2].strip()
        elif len(parts) >= 2 and parts[1].startswith("对照"):
            reference_tool = parts[1][2:].strip() or (parts[2].strip() if len(parts) > 2 else None)
    elif lower.startswith("check"):
        rest = stripped[5:].strip()
        if not rest:
            return None
        parts = rest.split()
        tool_name = parts[0].strip()
        if len(parts) >= 3 and parts[1] in {"vs", "ref"}:
            reference_tool = parts[2].strip()
    if not tool_name:
        return None
    return CheckerTask(tool_name=tool_name, reference_tool=reference_tool or None)


@dataclass(frozen=True, slots=True)
class ChecklistItem:
    id: str
    status: CheckStatus
    note: str
    evidence: str = ""


@dataclass(frozen=True, slots=True)
class CheckerTask:
    kind: CheckerKind = "evolve_tool_scaffold"
    tool_name: str = ""
    tool_dir: str = ""
    reference_tool: str | None = None
    demo_result: dict[str, Any] | None = None
    user_checklist: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class SubagentResult:
    kind: SubagentKind
    summary: str
    paths_cited: list[str]
    tool_rounds: int
    truncated: bool
    task: str
    verdict: Verdict | None = None
    checklist: tuple[ChecklistItem, ...] | None = None
    tool_name: str | None = None
    proposal_ids: tuple[str, ...] = ()
    partner_notices: tuple[str, ...] = ()
    adopt_pending: bool = False


def merge_checklist_verdict(checklist: list[ChecklistItem]) -> Verdict:
    """CHECKER-SUBAGENT §3.4 verdict merge."""
    if any(item.status == "fail" for item in checklist):
        return "fail"
    if any(item.status == "warn" for item in checklist):
        return "warn"
    return "pass"


def build_hard_checklist(
    task: CheckerTask,
    *,
    paths: AgentPaths,
    registry: ToolRegistry | None = None,
) -> list[ChecklistItem]:
    """Deterministic scaffold checks before LLM semantic audit (T-1611)."""
    reg = registry or ToolRegistry.load(paths)
    name = task.tool_name.strip()
    items: list[ChecklistItem] = []
    evolved = reg.get_evolved(name)
    tool_dir = task.tool_dir.strip()
    if evolved is not None:
        tool_dir = tool_dir or evolved.relative_dir

    if evolved is None:
        items.append(
            ChecklistItem(
                "registry",
                "fail",
                f"tool {name!r} not in registry",
                "",
            )
        )
        items.append(ChecklistItem("files", "fail", "cannot resolve tool directory", tool_dir))
        return items

    main_py = evolved.entry.script_path
    manifest_path = evolved.manifest_path
    if main_py.is_file() and manifest_path.is_file():
        items.append(
            ChecklistItem(
                "files",
                "pass",
                "main.py and tool.toml present",
                tool_dir,
            )
        )
    else:
        missing = []
        if not main_py.is_file():
            missing.append("main.py")
        if not manifest_path.is_file():
            missing.append("tool.toml")
        items.append(
            ChecklistItem(
                "files",
                "fail",
                f"missing: {', '.join(missing)}",
                tool_dir,
            )
        )

    try:
        manifest = parse_tool_manifest(manifest_path, evolve_dir=paths.evolve)
        status = str(getattr(manifest, "status", "") or "").strip().casefold()
        topics = list(getattr(manifest, "topics", []) or [])
        if status == "active" and topics:
            items.append(
                ChecklistItem(
                    "manifest_status",
                    "pass",
                    f"status=active; topics={topics}",
                    str(manifest_path.relative_to(paths.agent_root)).replace("\\", "/"),
                )
            )
        else:
            items.append(
                ChecklistItem(
                    "manifest_status",
                    "fail",
                    f"status={status!r}; topics={topics}",
                    str(manifest_path.relative_to(paths.agent_root)).replace("\\", "/"),
                )
            )
    except ToolManifestError as exc:
        items.append(
            ChecklistItem(
                "manifest_schema",
                "fail",
                f"tool.toml invalid: {exc}",
                str(manifest_path.relative_to(paths.agent_root)).replace("\\", "/"),
            )
        )
    else:
        items.append(
            ChecklistItem(
                "manifest_schema",
                "pass",
                "tool.toml parses via registry",
                str(manifest_path.relative_to(paths.agent_root)).replace("\\", "/"),
            )
        )

    items.append(
        ChecklistItem(
            "registry",
            "pass",
            "registry loads tool",
            name,
        )
    )

    demo = dict(task.demo_result or {})
    if not demo.get("attempted"):
        reason = str(demo.get("skipped_reason") or "demo probe not run")
        items.append(ChecklistItem("demo_probe", "fail", reason, ""))
    elif demo.get("cancelled"):
        items.append(ChecklistItem("demo_probe", "fail", "demo cancelled", ""))
    elif demo.get("exit_code") == 0:
        items.append(ChecklistItem("demo_probe", "pass", "demo exit_code=0", demo.get("stdout", "")[:200]))
    else:
        skip = str(demo.get("skipped_reason") or "")
        exit_code = demo.get("exit_code")
        if skip and exit_code is None:
            items.append(
                ChecklistItem(
                    "demo_probe",
                    "warn",
                    f"demo skipped: {skip}",
                    demo.get("stderr", "")[:200],
                )
            )
        else:
            items.append(
                ChecklistItem(
                    "demo_probe",
                    "fail",
                    f"demo exit_code={exit_code}",
                    demo.get("stderr", "")[:200],
                )
            )

    return items


def format_checklist_lines(checklist: tuple[ChecklistItem, ...] | list[ChecklistItem]) -> list[str]:
    lines: list[str] = []
    for item in checklist:
        mark = {"pass": "[x]", "fail": "[!]", "warn": "[~]"}[item.status]
        line = f"- {mark} {item.id}: {item.note}"
        if item.evidence:
            line += f" ({item.evidence})"
        lines.append(line)
    return lines


def format_subagent_overlay(result: SubagentResult) -> str:
    """Inject into parent system overlay (ORCHESTRATION §4.3 / CHECKER §3)."""
    if result.kind == "plan":
        lines = [
            "[子代理摘要 · plan]",
            f"任务: {result.task}",
            f"结论: {result.summary}",
        ]
        if result.proposal_ids:
            lines.append(f"提案: {len(result.proposal_ids)} 条待侧栏采纳")
        if result.adopt_pending:
            lines.append("（侧栏采纳后才落盘；向用户简短说明并提醒采纳/忽略）")
        if result.truncated:
            lines.append("（摘要已截断）")
        return "\n".join(lines)

    if result.kind == "checker":
        verdict = (result.verdict or "fail").upper()
        lines = [
            "[子代理摘要 · checker]",
            f"工具: {result.tool_name or result.task}",
            f"验收: {verdict}",
        ]
        if result.checklist:
            lines.append("checklist:")
            lines.extend(format_checklist_lines(result.checklist))
        paths_line = ", ".join(result.paths_cited) if result.paths_cited else "(none)"
        lines.append(f"已读: {paths_line}")
        lines.append(f"结论: {result.summary}")
        cap = subagent_checker_max()
        lines.append(f"（checker 已用 {result.tool_rounds}/{cap} 轮）")
        return "\n".join(lines)

    paths_line = ", ".join(result.paths_cited) if result.paths_cited else "(none)"
    cap_note = ""
    if result.truncated:
        cap_note = "（摘要已截断；父代理可补读关键文件）"
    rounds_note = f"子代理已用 {result.tool_rounds}/{subagent_explore_max()} 轮"
    if result.tool_rounds >= subagent_explore_max():
        rounds_note += "；已达 explore 上限"
    return "\n".join(
        [
            "[子代理摘要 · explore]",
            f"任务: {result.task}",
            f"已读: {paths_line}",
            f"结论: {result.summary}",
            f"（{rounds_note}{cap_note}）",
        ]
    )


def truncate_summary(text: str, *, max_chars: int | None = None) -> tuple[str, bool]:
    limit = max_chars if max_chars is not None else subagent_summary_max_chars()
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned, False
    return cleaned[:limit] + f"\n…(+{len(cleaned) - limit} chars)", True


def _explore_system_prompt() -> str:
    return "\n".join(
        [
            "你是 my-agent 的 **explore 子代理**（只读调研）。",
            "可用工具：read_file、list_dir、grep、web_search、fetch_url。",
            "**禁止** run_evolved 或任何写入。",
            "读完必要文件后，用自然语言输出摘要：已读路径、关键发现、给父代理的行动建议。",
            "父代理将收到你的摘要，不应重复读取相同文件。",
        ]
    )


def _checker_system_prompt() -> str:
    return "\n".join(
        [
            "你是 my-agent 的 **checker 子代理**（只读验收 / 监工）。",
            "可用工具：read_file、list_dir、grep。",
            "**禁止** run_evolved、web_search、fetch_url 或任何写入。",
            "内核已注入 demo probe 硬事实；你负责对照 tool.toml / main.py 做结构与语义审计。",
            "若提供 reference_tool，仅核对任务要求的关键字段/行为；纯风格差异最多 warn。",
            "输出末行必须是：CHECKER_VERDICT: pass|fail|warn",
        ]
    )


def _format_checker_user_message(task: CheckerTask, hard_checklist: list[ChecklistItem]) -> str:
    lines = [
        f"验收 evolved 工具: {task.tool_name}",
        f"目录: {task.tool_dir or f'evolve/tools/common/{task.tool_name}/'}",
    ]
    if task.reference_tool:
        lines.append(f"对照参考: {task.reference_tool}")
    if task.demo_result:
        demo_json = json.dumps(task.demo_result, ensure_ascii=False, indent=2)
        if len(demo_json) > 2500:
            demo_json = demo_json[:2500] + "\n…(truncated)"
        lines.append("demo_result (Phase 16 硬事实):")
        lines.append(demo_json)
    lines.append("hard_checklist (内核预检，不可推翻 fail):")
    lines.extend(format_checklist_lines(hard_checklist))
    if task.user_checklist:
        lines.append("user_checklist:")
        for entry in task.user_checklist:
            lines.append(f"- {entry}")
    lines.append("请读取必要文件，补充语义项（如 reference 对比），并给出 CHECKER_VERDICT。")
    return "\n".join(lines)


def _parse_checker_verdict_from_text(text: str) -> Verdict | None:
    match = re.search(r"CHECKER_VERDICT:\s*(pass|fail|warn)\b", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).casefold()  # type: ignore[return-value]
    upper = text.upper()
    for token, verdict in (("FAIL", "fail"), ("WARN", "warn"), ("PASS", "pass")):
        if f"验收: {token}" in upper or f"VERDICT: {token}" in upper:
            return verdict  # type: ignore[return-value]
    return None


def _parse_semantic_checklist_from_text(text: str) -> list[ChecklistItem]:
    items: list[ChecklistItem] = []
    for match in re.finditer(
        r"^-\s*\[(x|!|~)\]\s*([a-zA-Z0-9_.-]+):\s*(.+)$",
        text,
        flags=re.MULTILINE,
    ):
        status = {"x": "pass", "!": "fail", "~": "warn"}[match.group(1)]
        items.append(
            ChecklistItem(
                id=match.group(2),
                status=status,  # type: ignore[arg-type]
                note=match.group(3).strip(),
            )
        )
    return items


def merge_checker_results(
    hard: list[ChecklistItem],
    semantic: list[ChecklistItem],
    *,
    llm_verdict: Verdict | None,
    llm_summary: str,
) -> tuple[Verdict, tuple[ChecklistItem, ...]]:
    """Merge hard facts, LLM semantic items, and parsed verdict (T-1611)."""
    by_id: dict[str, ChecklistItem] = {item.id: item for item in hard}
    for item in semantic:
        if item.id in by_id and by_id[item.id].status == "fail":
            continue
        by_id[item.id] = item
    merged = list(by_id.values())
    hard_verdict = merge_checklist_verdict(hard)
    if hard_verdict == "fail":
        return "fail", tuple(merged)
    merged_verdict = merge_checklist_verdict(merged)
    if llm_verdict is None:
        return merged_verdict, tuple(merged)
    if hard_verdict == "warn" or merged_verdict == "warn" or llm_verdict == "warn":
        if merged_verdict == "fail" or llm_verdict == "fail":
            return "fail", tuple(merged)
        return "warn", tuple(merged)
    if llm_verdict == "fail":
        return "fail", tuple(merged)
    if not llm_summary.strip():
        return merged_verdict if merged_verdict != "pass" else "fail", tuple(merged)
    return llm_verdict, tuple(merged)


def _extract_paths_from_arguments(tool_name: str, arguments: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    if tool_name in {"read_file", "list_dir", "grep"}:
        raw = arguments.get("path")
        if isinstance(raw, str) and raw.strip():
            paths.append(raw.strip())
    if tool_name == "fetch_url":
        raw = arguments.get("url")
        if isinstance(raw, str) and raw.strip():
            paths.append(raw.strip())
    return paths


def _collect_paths_cited(working_messages: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for msg in working_messages:
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            try:
                name, args = _parse_tool_call(tool_call)
            except ValueError:
                continue
            for path in _extract_paths_from_arguments(name, args):
                if path not in seen:
                    seen.add(path)
                    ordered.append(path)
    return ordered


def _bind_llm_cancel(llm: ChatClient, cancel_event: threading.Event | None) -> None:
    if cancel_event is None:
        return
    setter = getattr(llm, "set_cancel_event", None)
    if callable(setter):
        setter(cancel_event)


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise LLMCancelledError("subagent cancelled")


@dataclass
class SubagentRunner:
    """Run read-only subagents with isolated messages (not persisted)."""

    paths: AgentPaths
    evolve_log: EvolveLog | None = None

    def run_explore(
        self,
        task: str,
        *,
        session: Session,
        llm: ChatClient,
        max_rounds: int | None = None,
        confirm_fn: Any | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SubagentResult:
        task_text = task.strip()
        if not task_text:
            raise ValueError("explore task is empty")

        cap = max_rounds if max_rounds is not None else subagent_explore_max()
        registry = ToolRegistry.load(self.paths)
        executor = ToolExecutor.create(
            paths=self.paths,
            session_dir=None,
            allowed_evolved=set(),
            confirm_fn=confirm_fn,
            evolve_log=self.evolve_log,
        )
        executor.session.blocked_tools = frozenset({"run_evolved"})
        executor.cancel_event = cancel_event
        _bind_llm_cancel(llm, cancel_event)

        tools = build_explore_tools(registry=registry)
        model = session.meta.llm_model or resolve_session_model(session.meta.topics)

        working: list[dict[str, Any]] = [
            {"role": "system", "content": _explore_system_prompt()},
            {"role": "user", "content": task_text},
        ]

        tool_rounds = 0
        final_text = ""
        hit_cap = False

        for round_index in range(cap):
            _raise_if_cancelled(cancel_event)
            response = llm.chat(
                working,
                model=model,
                tools=tools,
                temperature=0.2,
            )

            if not response.tool_calls:
                final_text = (response.content or "").strip()
                if final_text:
                    working.append({"role": "assistant", "content": final_text})
                break

            tool_rounds += 1
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": response.content,
                "tool_calls": response.tool_calls,
            }
            working.append(assistant_msg)

            for tool_call in response.tool_calls:
                _raise_if_cancelled(cancel_event)
                try:
                    tool_name, arguments = _parse_tool_call(tool_call)
                except ToolCallArgumentError as exc:
                    result = _tool_result_for_argument_error(exc)
                else:
                    result = executor.run(tool_name, arguments)
                working.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", ""),
                        "content": to_json(result),
                    }
                )

            if round_index == cap - 1:
                hit_cap = True
        else:
            hit_cap = True

        if hit_cap and not final_text:
            _raise_if_cancelled(cancel_event)
            working.append({"role": "user", "content": _CAP_SUMMARY_PROMPT})
            summary_response = llm.chat(working, model=model, tools=None, temperature=0.2)
            final_text = (summary_response.content or "").strip()
            if final_text:
                working.append({"role": "assistant", "content": final_text})

        if not final_text:
            final_text = "（子代理未产出文字摘要）"

        paths_cited = _collect_paths_cited(working)
        summary, truncated = truncate_summary(final_text)

        result = SubagentResult(
            kind="explore",
            summary=summary,
            paths_cited=paths_cited,
            tool_rounds=tool_rounds,
            truncated=truncated,
            task=task_text,
        )

        if self.evolve_log is not None:
            self.evolve_log.log_subagent_run(
                kind=result.kind,
                tool_rounds=result.tool_rounds,
                truncated=result.truncated,
                paths_cited=result.paths_cited,
                conversation_id=session.conversation_id,
            )

        return result

    def run_checker(
        self,
        task: CheckerTask,
        *,
        session: Session,
        llm: ChatClient,
        max_rounds: int | None = None,
        confirm_fn: Any | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SubagentResult:
        tool_name = task.tool_name.strip()
        if not tool_name:
            raise ValueError("checker tool_name is empty")

        registry = ToolRegistry.load(self.paths)
        evolved = registry.get_evolved(tool_name)
        tool_dir = task.tool_dir.strip()
        if evolved is not None:
            tool_dir = tool_dir or evolved.relative_dir
        elif not tool_dir:
            tool_dir = f"evolve/tools/common/{tool_name}/"

        resolved_task = CheckerTask(
            kind=task.kind,
            tool_name=tool_name,
            tool_dir=tool_dir,
            reference_tool=task.reference_tool,
            demo_result=task.demo_result,
            user_checklist=task.user_checklist,
        )

        hard_checklist = build_hard_checklist(resolved_task, paths=self.paths, registry=registry)
        cap = max_rounds if max_rounds is not None else subagent_checker_max()

        executor = ToolExecutor.create(
            paths=self.paths,
            session_dir=None,
            allowed_evolved=set(),
            confirm_fn=confirm_fn,
            evolve_log=None,
        )
        executor.session.blocked_tools = frozenset({"run_evolved"})
        executor.cancel_event = cancel_event
        _bind_llm_cancel(llm, cancel_event)

        tools = build_checker_tools(registry=registry)
        session_model = session.meta.llm_model or resolve_session_model(session.meta.topics)
        model = resolve_checker_model(session_model)

        user_content = _format_checker_user_message(resolved_task, hard_checklist)
        working: list[dict[str, Any]] = [
            {"role": "system", "content": _checker_system_prompt()},
            {"role": "user", "content": user_content},
        ]

        tool_rounds = 0
        final_text = ""
        hit_cap = False

        for round_index in range(cap):
            _raise_if_cancelled(cancel_event)
            response = llm.chat(
                working,
                model=model,
                tools=tools,
                temperature=0.1,
            )

            if not response.tool_calls:
                final_text = (response.content or "").strip()
                if final_text:
                    working.append({"role": "assistant", "content": final_text})
                break

            tool_rounds += 1
            working.append(
                {
                    "role": "assistant",
                    "content": response.content,
                    "tool_calls": response.tool_calls,
                }
            )

            for tool_call in response.tool_calls:
                _raise_if_cancelled(cancel_event)
                try:
                    tname, arguments = _parse_tool_call(tool_call)
                except ToolCallArgumentError as exc:
                    result = _tool_result_for_argument_error(exc)
                else:
                    if tname not in CHECKER_TOOL_NAMES:
                        result = _tool_result_for_argument_error(
                            ToolCallArgumentError(
                                tname,
                                f"tool {tname!r} not allowed for checker",
                            )
                        )
                    else:
                        result = executor.run(tname, arguments)
                working.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", ""),
                        "content": to_json(result),
                    }
                )

            if round_index == cap - 1:
                hit_cap = True
        else:
            hit_cap = True

        if hit_cap and not final_text:
            _raise_if_cancelled(cancel_event)
            working.append({"role": "user", "content": _CHECKER_CLOSE_PROMPT})
            summary_response = llm.chat(working, model=model, tools=None, temperature=0.1)
            final_text = (summary_response.content or "").strip()

        if not final_text:
            final_text = "（checker 未产出文字报告）"

        paths_cited = _collect_paths_cited(working)
        semantic_items = _parse_semantic_checklist_from_text(final_text)
        llm_verdict = _parse_checker_verdict_from_text(final_text)
        verdict, checklist = merge_checker_results(
            hard_checklist,
            semantic_items,
            llm_verdict=llm_verdict,
            llm_summary=final_text,
        )
        summary, truncated = truncate_summary(final_text, max_chars=checker_summary_max_chars())

        result = SubagentResult(
            kind="checker",
            summary=summary,
            paths_cited=paths_cited,
            tool_rounds=tool_rounds,
            truncated=truncated,
            task=tool_name,
            verdict=verdict,
            checklist=checklist,
            tool_name=tool_name,
        )

        if self.evolve_log is not None:
            self.evolve_log.log_subagent_run(
                kind=result.kind,
                tool_rounds=result.tool_rounds,
                truncated=result.truncated,
                paths_cited=result.paths_cited,
                conversation_id=session.conversation_id,
                verdict=result.verdict,
                tool_name=result.tool_name,
            )

        return result

    def run_plan(
        self,
        task: str,
        *,
        session: Session,
        include_recent_user_lines: int = 2,
        confirm_fn: Any | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SubagentResult:
        """Plan subagent: PlanAgent.reason_about_intent in isolated context (Phase 39 B1/B3)."""
        del confirm_fn  # Plan tools use auto-approve inside PlanAgent
        _raise_if_cancelled(cancel_event)

        task_text = task.strip()
        if not task_text:
            raise ValueError("plan task is empty")

        project_id = (getattr(session.meta, "project_id", None) or "").strip()
        if not project_id:
            raise ValueError("plan_partner requires a bound project_id")

        from plan_agent import get_plan_agent

        agent = get_plan_agent(self.paths, project_id)

        last_user_hint: str | None = None
        n = max(0, min(int(include_recent_user_lines or 0), 5))
        if n > 0:
            lines: list[str] = []
            for msg in reversed(list(getattr(session, "messages", []) or [])):
                if not isinstance(msg, dict) or msg.get("role") != "user":
                    continue
                text = str(msg.get("content") or "").strip()
                if text:
                    lines.append(text)
                if len(lines) >= n:
                    break
            if lines:
                lines.reverse()
                last_user_hint = "\n".join(lines)

        summary_raw = agent.reason_about_intent(
            task_text,
            include_last_user=last_user_hint,
        )
        proposal_ids = tuple(sorted(agent._pending_gated.keys()))
        notices = tuple(agent._last_partner_notices or ())
        adopt_pending = bool(proposal_ids)

        summary, truncated = truncate_summary(
            summary_raw,
            max_chars=plan_subagent_summary_max_chars(),
        )

        result = SubagentResult(
            kind="plan",
            summary=summary,
            paths_cited=[],
            tool_rounds=1,
            truncated=truncated,
            task=task_text,
            proposal_ids=proposal_ids,
            partner_notices=notices,
            adopt_pending=adopt_pending,
        )

        if self.evolve_log is not None:
            self.evolve_log.log_subagent_run(
                kind=result.kind,
                tool_rounds=result.tool_rounds,
                truncated=result.truncated,
                paths_cited=result.paths_cited,
                conversation_id=session.conversation_id,
            )

        return result


@dataclass
class _MockLLM:
    responses: list[LLMResponse] = field(default_factory=list)

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        if not self.responses:
            raise RuntimeError("mock LLM has no scripted responses left")
        return self.responses.pop(0)

    def set_cancel_event(self, event: threading.Event) -> None:
        self._cancel_event = event


def _demo() -> None:
    paths = AgentPaths.discover()
    session_dir = paths.data / "sessions" / "_subagent_demo"
    session_dir.mkdir(parents=True, exist_ok=True)
    session = Session(
        conversation_id="_subagent_demo",
        session_dir=session_dir,
        goal="subagent demo",
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
    blocked_args = json.dumps(
        {
            "tool_name": "write_evolve",
            "arguments": {"scope": "coding", "name": "bar"},
        },
        ensure_ascii=False,
    )

    mock = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": "call_read_1",
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
                content=None,
                tool_calls=[
                    {
                        "id": "call_blocked",
                        "type": "function",
                        "function": {"name": "run_evolved", "arguments": blocked_args},
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            ),
            LLMResponse(
                model="mock",
                content=(
                    "run_demo 是 coding 主题验收脚本工具：在 agent-core/ 下执行目标脚本的 demo。"
                    "tool.toml 定义 topics=[coding]，entry main.py。"
                ),
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
        ]
    )

    log_path = paths.data / "evolve_log.jsonl"
    from tools.logging import EVENT_SUBAGENT_RUN, read_events

    before = len(read_events(log_path))
    runner = SubagentRunner(paths=paths, evolve_log=EvolveLog.for_agent(paths))
    result = runner.run_explore(
        "读 evolve/tools/coding/run_demo/tool.toml 并总结",
        session=session,
        llm=mock,
        max_rounds=8,
        confirm_fn=lambda _p, _a: "y",
    )

    assert result.kind == "explore"
    assert result.tool_rounds <= 8
    assert result.summary
    assert "run_demo" in result.summary or "验收" in result.summary
    assert any("run_demo" in p for p in result.paths_cited)
    print(f"[PASS] explore ≤8 rounds ({result.tool_rounds} tool round(s))")

    explore_tools = build_explore_tools()
    names = [item["function"]["name"] for item in explore_tools]
    assert names == list(EXPLORE_TOOL_NAMES)
    assert "run_evolved" not in names
    print("[PASS] explore tools exclude run_evolved")

    overlay = format_subagent_overlay(result)
    assert "[子代理摘要 · explore]" in overlay
    assert "结论:" in overlay
    print("[PASS] format_subagent_overlay explore")

    new_events = [
        e
        for e in read_events(log_path)[before:]
        if e.get("event") == EVENT_SUBAGENT_RUN
    ]
    assert new_events
    assert new_events[-1].get("kind") == "explore"
    print("[PASS] evolve_log subagent_run explore")

    assert classify_turn("探索 docs 结构") == "research"
    assert should_spawn_explore("按 run_demo 模式造 bar 工具")
    assert not should_spawn_explore("1+1 等于几")
    print("[PASS] should_spawn_explore heuristics")

    assert parse_explore_command("探索 evolve/tools/coding") == "evolve/tools/coding"
    assert parse_explore_command("explore docs/MAP.md") == "docs/MAP.md"
    print("[PASS] parse_explore_command")

    long_summary = "x" * (subagent_summary_max_chars() + 100)
    truncated_text, was_truncated = truncate_summary(long_summary)
    assert was_truncated
    print("[PASS] summary truncation")

    # T-1610–T-1614: checker subagent
    assert parse_checker_command("验收 write_text") is not None
    assert parse_checker_command("验收 write_text").tool_name == "write_text"
    assert parse_checker_command("check npm_exec vs mvn_exec") is not None
    assert parse_checker_command("check npm_exec vs mvn_exec").reference_tool == "mvn_exec"
    print("[PASS] T-1612: parse_checker_command")

    checker_tools = build_checker_tools()
    checker_names = [item["function"]["name"] for item in checker_tools]
    assert checker_names == list(CHECKER_TOOL_NAMES)
    assert "web_search" not in checker_names
    print("[PASS] T-1610: checker tools read-only subset")

    from tools.builtin.run_evolved import run_scaffold_demo

    registry = ToolRegistry.load(paths)
    write_text = registry.get_evolved("write_text")
    assert write_text is not None
    demo_result = run_scaffold_demo(write_text)
    hard = build_hard_checklist(
        CheckerTask(tool_name="write_text", demo_result=demo_result),
        paths=paths,
        registry=registry,
    )
    assert any(item.id == "demo_probe" and item.status == "pass" for item in hard)
    print("[PASS] T-1611: build_hard_checklist + demo_probe")

    wt_read_args = json.dumps(
        {"path": "evolve/tools/common/write_text/tool.toml"},
        ensure_ascii=False,
    )
    checker_mock = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": "chk_read",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": wt_read_args},
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            ),
            LLMResponse(
                model="mock",
                content=(
                    "write_text 结构完整，topics 含 common，与 demo 一致。\n"
                    "CHECKER_VERDICT: pass"
                ),
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
        ]
    )
    checker_before = len(read_events(log_path))
    checker_result = runner.run_checker(
        CheckerTask(tool_name="write_text", demo_result=demo_result),
        session=session,
        llm=checker_mock,
        confirm_fn=lambda _p, _a: "y",
    )
    assert checker_result.kind == "checker"
    assert checker_result.verdict == "pass"
    assert checker_result.checklist
    checker_overlay = format_subagent_overlay(checker_result)
    assert "[子代理摘要 · checker]" in checker_overlay
    assert "验收: PASS" in checker_overlay
    print("[PASS] T-1610/T-1613: run_checker + checker overlay")

    checker_events = [
        e
        for e in read_events(log_path)[checker_before:]
        if e.get("event") == EVENT_SUBAGENT_RUN
    ]
    assert checker_events
    assert checker_events[-1].get("kind") == "checker"
    assert checker_events[-1].get("verdict") == "pass"
    print("[PASS] T-1613: evolve_log subagent_run kind=checker")

    cancel = threading.Event()
    cancel.set()

    def _cancelled_chat(*_a, **_k):
        raise LLMCancelledError("cancelled")

    cancel_mock = _MockLLM()
    cancel_mock.chat = _cancelled_chat  # type: ignore[method-assign]
    try:
        runner.run_checker(
            CheckerTask(tool_name="write_text", demo_result=demo_result),
            session=session,
            llm=cancel_mock,
            cancel_event=cancel,
        )
        raise AssertionError("expected LLMCancelledError")
    except LLMCancelledError:
        pass
    print("[PASS] T-1610: checker honours cancel_event")

    if load_config().api_key:
        print("[SKIP] live explore/checker: use REPL")
    else:
        print("[SKIP] live explore/checker: LLM_API_KEY not set")


if __name__ == "__main__":
    _demo()
