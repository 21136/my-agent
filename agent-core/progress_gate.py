"""Phase 24 · Progress Gate — turn-scoped evidence for report_progress (PROGRESS-GATE.md)."""

from __future__ import annotations

import re
from typing import Any, Literal

EvidenceKind = Literal["write", "compile", "test", "build_fe", "verify_db", "unknown"]

_WRITE_EVIDENCE_TOOLS = frozenset(
    {"write_text", "patch_file", "copy_move", "write_evolve"}
)
_COMPILE_EVIDENCE_TOOLS = frozenset({"run_command"})
_TEST_EVIDENCE_TOOLS = frozenset(
    {"run_tests", "run_project_tests", "run_command"}
)
_BUILD_FE_EVIDENCE_TOOLS = frozenset({"run_command"})
_VERIFY_DB_EVIDENCE_TOOLS = frozenset(
    {"http_request", "db_query", "run_command"}
)

# Old sessions may still record archived tool names as evidence; map to current tools.
_LEGACY_EVIDENCE_ALIASES: dict[str, str] = {
    "append_text": "write_text",
    "mvn_exec": "run_command",
    "npm_exec": "run_command",
    "repl": "run_command",
    "run_python": "run_command",
    "jshell_exec": "run_command",
    "pip_install": "run_command",
}

_EVIDENCE_TAG_RE = re.compile(
    r"\[evidence:\s*(write|compile|test|build_fe|verify_db)\s*\]",
    re.IGNORECASE,
)

_KNOWN_KINDS = frozenset({"write", "compile", "test", "build_fe", "verify_db"})


def _explicit_evidence_tag(task_text: str) -> EvidenceKind | None:
    """TASKS row tag `[evidence:write]` etc. — highest priority (PROGRESS-GATE §3.2)."""
    m = _EVIDENCE_TAG_RE.search(task_text)
    if not m:
        return None
    kind = m.group(1).strip().lower()
    if kind in _KNOWN_KINDS:
        return kind  # type: ignore[return-value]
    return None


def classify_task_evidence_kind(task_text: str | None) -> EvidenceKind:
    """Map TASKS checkbox body → evidence kind (PROGRESS-GATE §3.2)."""
    raw = (task_text or "").strip()
    if not raw:
        return "unknown"

    tagged = _explicit_evidence_tag(raw)
    if tagged is not None:
        return tagged

    lower = raw.lower()

    # Explicit test /验收 phase lines (Chinese + Phase N 测试)
    if "测试" in raw and any(
        k in raw for k in ("联调", "验收", "集成", "Phase", "阶段", "模块")
    ):
        return "test"
    if raw.startswith("Phase") and "测试" in raw:
        return "test"

    if any(s in raw for s in ("可编译", "编译通过", "后端可编译")) or "mvn" in lower:
        if "测试" not in raw:
            return "compile"
        return "test"

    if any(s in raw for s in ("前端可构建", "可构建通过")) or "npm build" in lower:
        return "build_fe"

    if any(s in raw for s in ("数据库", "联通", "对接数据库")) or (
        "连接" in raw and "验证" in raw
    ):
        return "verify_db"

    # Layer / file signals (original table)
    write_hits = (
        "Entity",
        "Mapper",
        "Service",
        "Controller",
        "页面",
        ".vue",
        ".java",
        "XML",
        "前端页面",
        "API模块",
    )
    if any(s in raw or s.lower() in lower for s in write_hits):
        return "write"
    if any(s in lower for s in ("entity", "mapper", "service", "controller")):
        return "write"

    # Colloquial coding-task signals (huiyi Phase 7: 「写 SysMenu 菜单列表接口」)
    # Keep after specialized kinds so 「…接口…联调测试」 still → test.
    write_colloquial = (
        "写",
        "接口",
        "CRUD",
        "crud",
        "新增",
        "删除",
        "改 ",
        "路由",
    )
    if any(s in raw for s in write_colloquial) or "crud" in lower:
        return "write"

    return "unknown"


def _tool_name_from_entry(entry: dict[str, Any]) -> str:
    evolved = entry.get("evolved_name") or entry.get("tool") or ""
    return str(evolved).strip()


def _evidence_tool_names(turn_evidence: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for entry in turn_evidence:
        if not entry.get("ok"):
            continue
        name = _tool_name_from_entry(entry)
        if not name:
            continue
        names.add(name)
        legacy = _LEGACY_EVIDENCE_ALIASES.get(name)
        if legacy:
            names.add(legacy)
    return names


def evidence_satisfies(
    kind: EvidenceKind,
    turn_evidence: list[dict[str, Any]],
) -> tuple[bool, str]:
    """Whether this-turn successful tools cover *kind* (G1/G2)."""
    names = _evidence_tool_names(turn_evidence)

    if kind == "unknown":
        return (
            False,
            "evidence_kind=unknown：无法对口归类，禁止自动勾选；请改任务文案或补证据规则",
        )
    if kind == "write":
        hit = names & _WRITE_EVIDENCE_TOOLS
        if hit:
            return True, f"write evidence via {sorted(hit)[0]}"
        return False, "缺少本回合写入类成功证据（write_text/patch_file 等）"
    if kind == "compile":
        hit = names & _COMPILE_EVIDENCE_TOOLS
        if hit:
            return True, f"compile evidence via {sorted(hit)[0]}"
        return False, "缺少本回合编译成功证据（run_command）"
    if kind == "test":
        hit = names & _TEST_EVIDENCE_TOOLS
        if hit:
            return True, f"test evidence via {sorted(hit)[0]}"
        return False, "缺少本回合测试成功证据（run_project_tests/run_tests/run_command）"
    if kind == "build_fe":
        hit = names & _BUILD_FE_EVIDENCE_TOOLS
        if hit:
            return True, f"build_fe evidence via {sorted(hit)[0]}"
        return False, "缺少本回合前端构建成功证据（run_command）"
    if kind == "verify_db":
        hit = names & _VERIFY_DB_EVIDENCE_TOOLS
        if hit:
            return True, f"verify_db evidence via {sorted(hit)[0]}"
        return False, "缺少本回合数据库核验成功证据（run_command/db_query/http_request）"
    return False, f"unknown evidence kind: {kind}"


def make_evidence_entry(
    *,
    tool_name: str,
    evolved_name: str = "",
    ok: bool,
    paths: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "tool": tool_name,
        "evolved_name": evolved_name,
        "ok": bool(ok),
        "paths": list(paths or []),
    }


def report_progress_evidence_block_reason(
    *,
    active_shell: str,
    armed_task_text: str,
    turn_evidence: list[dict[str, Any]],
) -> str | None:
    """G1/G2: block report_progress when this-turn matched evidence is missing."""
    if active_shell != "project":
        return None
    kind = classify_task_evidence_kind(armed_task_text)
    ok, note = evidence_satisfies(kind, turn_evidence)
    if ok:
        return None
    return (
        "[progress_gate] 无本回合对口工具成功证据，禁止勾选。"
        f" 任务证据类={kind}；{note}。"
        " 请先跑通对口工具或修 bug，勿口头上报。"
    )


def build_progress_gate_notice(
    *,
    armed_task_id: str,
    armed_task_text: str,
    reason: str,
    turn_evidence: list[dict[str, Any]],
) -> str:
    """G6: structured sidebar notice when report_progress is blocked (no force-check)."""
    kind = classify_task_evidence_kind(armed_task_text)
    lines = [
        "进度闸门：禁止勾选",
        f"任务：{(armed_task_id or '').strip() or '—'}",
    ]
    task_preview = (armed_task_text or "").strip()
    if task_preview:
        short = task_preview if len(task_preview) <= 72 else task_preview[:69] + "…"
        lines.append(f"文案：{short}")
    lines.append(f"证据类：{kind}")
    core = (reason or "").strip()
    if core.startswith("[progress_gate]"):
        core = core[len("[progress_gate]") :].strip()
    if core:
        lines.append(core)
    failed = [
        _tool_name_from_entry(e)
        for e in turn_evidence
        if not e.get("ok") and _tool_name_from_entry(e)
    ]
    if failed:
        tail = ", ".join(failed[-3:])
        lines.append(f"本回合失败工具：{tail}")
    lines.append("须先跑通对口工具；侧栏无强制勾选入口。")
    return "\n".join(lines)


def report_progress_repeat_block_reason(
    *,
    active_shell: str,
    task_stop_armed: bool,
    tool_name: str,
    arguments: dict[str, object],
) -> str | None:
    """G5: after a successful checkbox this turn, ban another report_progress."""
    if not task_stop_armed or active_shell != "project":
        return None
    if tool_name != "run_evolved":
        return None
    evolved = arguments.get("tool_name")
    if not isinstance(evolved, str) or evolved.strip() != "report_progress":
        return None
    return (
        "[progress_gate] 本轮已完成一条 TASKS 勾选；"
        "禁止再次 report_progress。请结束回合，用户「继续」后再报下一项。"
    )
