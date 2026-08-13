"""Terminal auto Plan-and-Execute state (TERMINAL-MODE §5.5 · T-5730)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from file_guard import atomic_write_text
from paths import AgentPaths
from session import Session

TERMINAL_PLAN_FILENAME = "terminal-plan.json"

TerminalPlanPhase = Literal[
    "idle",
    "planning",
    "executing",
    "validating",
    "replanning",
    "done",
    "stopped",
]

StepStatus = Literal["pending", "running", "passed", "failed"]

_SKIP_PLAN_MARKERS = (
    "直接改",
    "别计划",
    "不要计划",
    "不用计划",
    "skip plan",
    "no plan",
)
_CONTINUE_MARKERS = (
    "继续",
    "下一步",
    "按方案做",
    "按方案",
    "continue",
    "next step",
)
_EXPLICIT_REPLAN_MARKERS = (
    "重新规划",
    "重新计划",
    "replan",
)
_COMPLEX_MARKERS = (
    "新功能",
    "多文件",
    "多个文件",
    "重构",
    "架构",
    "模块",
    "接口",
    "scaffold",
    "implement ",
    "roadmap",
    "拆分",
    "分阶段",
)
_PLAN_REQUEST_MARKERS = (
    "先设计",
    "先规划",
    "规划一下",
    "拆步骤",
    "列计划",
    "plan first",
    "plan模式",
    "plan 模式",
    "plan mode",
    "测试plan",
    "auto-plan",
    "auto plan",
    "自动规划",
    "自动计划",
)
_SIMPLE_PATCH_MARKERS = ("修", "fix", "patch", "改一行", "小改", "typo")
_PATH_LIKE_RE = re.compile(
    r"(?:[\w.-]+/)+[\w.-]+(?:\.\w+)?|[\w.-]+\.(?:py|ts|tsx|js|java|md|json|toml|yml)"
)
_MAX_STEPS = 8
_DEFAULT_STEP_RETRY_MAX = 2
_DEFAULT_REPLAN_MAX = 1
_DEFAULT_STEP_TOOL_MAX = 16
_PLANNING_DEGRADED_EFFORT = "high"

AutoPlanGate = Literal["skip", "yes", "classify"]


@dataclass(frozen=True, slots=True)
class TerminalPlanGateDecision:
    """Result of auto-plan gate (rules and/or classifier · TM-24 §5.5.1)."""

    needs_plan: bool
    source: str
    reason: str = ""


_TERMINAL_PLAN_CLASSIFY_PROMPT = """你是 Terminal Plan-and-Execute 触发分类器（TM-24 · 仅分类，不执行）。

判断用户本条 coding 请求是否应先自动规划、再分步执行（类似 Claude Code 对复杂任务自动进入 plan）。

needs_plan=true 示例：
- 从零写游戏/应用/工具/新模块（即使用户没说「plan」）
- 多文件实现、接口+实现+测试、脚手架
- 重构、架构调整、分阶段交付、较大新功能

needs_plan=false 示例：
- 单文件 typo、一行修补、明确极小改动
- 纯问答、解释代码、查资料
- 用户明确「直接改」「别计划」

只输出 JSON：{"needs_plan": true|false, "reason": "简短中文"}"""


@dataclass(frozen=True)
class TerminalPlanningProfile:
    """Resolved planner model for Terminal auto-plan (TM-26 · weak-API fallback)."""

    model_id: str
    reasoning_effort: str
    degraded: bool


def resolve_terminal_planning_profile(
    session: Session,
    *,
    registry: Any | None = None,
    paths: AgentPaths | None = None,
) -> TerminalPlanningProfile:
    """Pick planner model: same-vendor configured pro + max, else main_turn + high."""
    from llm_models import get_registry
    from llm_routing import resolve_model_id_for_role

    agent_paths = paths or session.paths
    reg = registry or get_registry(agent_paths)
    main_id = resolve_model_id_for_role("main_turn", session.meta, registry=reg)
    main_entry = reg.resolve(main_id)

    if main_entry is None:
        return TerminalPlanningProfile(
            model_id=main_id,
            reasoning_effort=_PLANNING_DEGRADED_EFFORT,
            degraded=True,
        )

    vendor_key = main_entry.vendor.casefold()

    def _configured_pro_same_vendor() -> list[str]:
        ids: list[str] = []
        for entry in reg.models:
            if entry.tier != "pro":
                continue
            if entry.vendor.casefold() != vendor_key:
                continue
            if not entry.resolve_api_key(agent_paths):
                continue
            ids.append(entry.id)
        return ids

    candidate_ids: list[str] = []
    planning_raw = (session.meta.planning_model or "").strip()
    if planning_raw:
        candidate_ids.append(planning_raw)
    for env_name in ("PLAN_PARTNER_MODEL", "PLAN_AGENT_MODEL"):
        raw = os.environ.get(env_name, "").strip()
        if raw:
            candidate_ids.append(raw)
    if reg.default_pro_id:
        candidate_ids.append(reg.default_pro_id)
    candidate_ids.extend(_configured_pro_same_vendor())

    seen: set[str] = set()
    for raw in candidate_ids:
        key = raw.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        entry = reg.resolve(key)
        if entry is None:
            continue
        if entry.tier != "pro":
            continue
        if entry.vendor.casefold() != vendor_key:
            continue
        if not entry.resolve_api_key(agent_paths):
            continue
        return TerminalPlanningProfile(
            model_id=entry.id,
            reasoning_effort="max",
            degraded=False,
        )

    if main_entry.tier == "pro" and main_entry.resolve_api_key(agent_paths):
        return TerminalPlanningProfile(
            model_id=main_id,
            reasoning_effort="max",
            degraded=False,
        )

    return TerminalPlanningProfile(
        model_id=main_id,
        reasoning_effort=_PLANNING_DEGRADED_EFFORT,
        degraded=True,
    )


def terminal_plan_path(session: Session) -> Path:
    return session.session_dir / TERMINAL_PLAN_FILENAME


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def goal_fingerprint(session: Session) -> str:
    """Stable hash for the current session goal (TM-25)."""
    goal = (session.goal or "").strip()
    if not goal:
        for msg in session.messages:
            if isinstance(msg, dict) and msg.get("role") == "user":
                goal = str(msg.get("content") or "").strip()
                break
    if not goal:
        goal = session.conversation_id
    digest = hashlib.sha256(goal.encode("utf-8")).hexdigest()
    return digest[:16]


@dataclass
class TerminalPlanStep:
    id: str
    title: str
    scope: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    verify: list[str] = field(default_factory=list)
    status: StepStatus = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "scope": list(self.scope),
            "depends_on": list(self.depends_on),
            "verify": list(self.verify),
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TerminalPlanStep:
        status = str(raw.get("status") or "pending")
        if status not in {"pending", "running", "passed", "failed"}:
            status = "pending"
        scope = raw.get("scope") or []
        depends = raw.get("depends_on") or []
        verify = raw.get("verify") or []
        return cls(
            id=str(raw.get("id") or "step"),
            title=str(raw.get("title") or "").strip() or "未命名步骤",
            scope=[str(x) for x in scope if str(x).strip()],
            depends_on=[str(x) for x in depends if str(x).strip()],
            verify=[str(x) for x in verify if str(x).strip()],
            status=status,  # type: ignore[arg-type]
        )


@dataclass
class TerminalPlanArtifact:
    goal_fingerprint: str
    phase: TerminalPlanPhase = "idle"
    steps: list[TerminalPlanStep] = field(default_factory=list)
    current_step: int = 0
    retry_count: int = 0
    replan_count: int = 0
    initial_plan_done: bool = False
    planner_summary: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_fingerprint": self.goal_fingerprint,
            "phase": self.phase,
            "steps": [step.to_dict() for step in self.steps],
            "current_step": self.current_step,
            "retry_count": self.retry_count,
            "replan_count": self.replan_count,
            "initial_plan_done": self.initial_plan_done,
            "planner_summary": self.planner_summary,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TerminalPlanArtifact:
        steps_raw = raw.get("steps") or []
        steps = [
            TerminalPlanStep.from_dict(item)
            for item in steps_raw
            if isinstance(item, dict)
        ]
        phase = str(raw.get("phase") or "idle")
        if phase not in {
            "idle",
            "planning",
            "executing",
            "validating",
            "replanning",
            "done",
            "stopped",
        }:
            phase = "idle"
        return cls(
            goal_fingerprint=str(raw.get("goal_fingerprint") or ""),
            phase=phase,  # type: ignore[arg-type]
            steps=steps,
            current_step=max(0, int(raw.get("current_step") or 0)),
            retry_count=max(0, int(raw.get("retry_count") or 0)),
            replan_count=max(0, int(raw.get("replan_count") or 0)),
            initial_plan_done=bool(raw.get("initial_plan_done")),
            planner_summary=str(raw.get("planner_summary") or ""),
            created_at=str(raw.get("created_at") or ""),
            updated_at=str(raw.get("updated_at") or ""),
        )

    def current(self) -> TerminalPlanStep | None:
        if self.current_step < 0 or self.current_step >= len(self.steps):
            return None
        return self.steps[self.current_step]

    def all_passed(self) -> bool:
        return bool(self.steps) and all(step.status == "passed" for step in self.steps)

    def can_retry_step(self) -> bool:
        return self.retry_count < step_retry_max()

    def can_replan(self) -> bool:
        return self.replan_count < replan_max()

    def touch(self) -> None:
        self.updated_at = utc_now_iso()
        if not self.created_at:
            self.created_at = self.updated_at


def step_retry_max() -> int:
    raw = os.environ.get("TERMINAL_PLAN_STEP_RETRY_MAX", str(_DEFAULT_STEP_RETRY_MAX))
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_STEP_RETRY_MAX


def replan_max() -> int:
    raw = os.environ.get("TERMINAL_PLAN_REPLAN_MAX", str(_DEFAULT_REPLAN_MAX))
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_REPLAN_MAX


def terminal_step_tool_max() -> int:
    raw = os.environ.get("TERMINAL_PLAN_STEP_TOOL_MAX", str(_DEFAULT_STEP_TOOL_MAX))
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_STEP_TOOL_MAX


def load_artifact(session: Session) -> TerminalPlanArtifact | None:
    path = terminal_plan_path(session)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return TerminalPlanArtifact.from_dict(payload)


def save_artifact(session: Session, artifact: TerminalPlanArtifact) -> None:
    artifact.touch()
    session.session_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        terminal_plan_path(session),
        json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2) + "\n",
        agent_root=session.paths.agent_root,
    )


def clear_artifact(session: Session) -> None:
    path = terminal_plan_path(session)
    if path.is_file():
        path.unlink(missing_ok=True)


def is_continue_turn(user_text: str) -> bool:
    text = user_text.strip()
    lower = text.casefold()
    if not text:
        return False
    if lower in {m.casefold() for m in _CONTINUE_MARKERS}:
        return True
    return any(lower == marker.casefold() for marker in _CONTINUE_MARKERS)


def is_explicit_replan_turn(user_text: str) -> bool:
    lower = user_text.strip().casefold()
    return any(marker.casefold() in lower for marker in _EXPLICIT_REPLAN_MARKERS)


def is_skip_plan_turn(user_text: str) -> bool:
    lower = user_text.strip().casefold()
    return any(marker.casefold() in lower for marker in _SKIP_PLAN_MARKERS)


def _looks_like_simple_single_fix(user_text: str) -> bool:
    text = user_text.strip()
    lower = text.casefold()
    if any(marker in text or marker in lower for marker in _COMPLEX_MARKERS):
        return False
    if any(marker in lower for marker in _PLAN_REQUEST_MARKERS):
        return False
    paths = _PATH_LIKE_RE.findall(text)
    if len(paths) == 1 and any(m in lower for m in _SIMPLE_PATCH_MARKERS):
        return True
    if len(paths) <= 1 and len(text) < 80 and any(m in lower for m in _SIMPLE_PATCH_MARKERS):
        return True
    return False


def should_auto_plan_turn(
    user_text: str,
    session: Session,
    intent: str,
    artifact: TerminalPlanArtifact | None,
) -> bool:
    """Rules-only fast yes (tests + backward compat). Ambiguous → false; use resolve_auto_plan_turn."""
    return auto_plan_gate_rules(user_text, session, intent, artifact) == "yes"


def auto_plan_gate_rules(
    user_text: str,
    session: Session,
    intent: str,
    artifact: TerminalPlanArtifact | None,
) -> AutoPlanGate:
    """Deterministic auto-plan gate: skip / yes / classify (TM-24 · §5.5.1)."""
    if intent != "execute":
        return "skip"
    if is_skip_plan_turn(user_text):
        return "skip"
    if _looks_like_simple_single_fix(user_text):
        return "skip"

    fp = goal_fingerprint(session)
    if artifact is not None and artifact.goal_fingerprint == fp:
        if artifact.phase in {"executing", "validating"}:
            return "skip"
        if artifact.initial_plan_done:
            return "skip"

    text = user_text.strip()
    lower = text.casefold()
    if any(marker in text or marker in lower for marker in _COMPLEX_MARKERS):
        return "yes"
    if any(marker in text or marker in lower for marker in _PLAN_REQUEST_MARKERS):
        return "yes"
    if artifact is None or artifact.goal_fingerprint != fp:
        paths = _PATH_LIKE_RE.findall(text)
        if len(paths) >= 2:
            return "yes"
        if len(text) >= 120 and any(
            kw in lower for kw in ("实现", "添加", "创建", "implement", "add ", "create ")
        ):
            return "yes"
    return "classify"


def _parse_terminal_plan_classify_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[^{}]*\"needs_plan\"[^{}]*\}", text, flags=re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {}


def classify_terminal_plan_need(
    text: str,
    *,
    llm: Any,
    meta: Any | None = None,
) -> TerminalPlanGateDecision:
    """Constrained classifier for ambiguous execute turns (TM-24 §5.5.1)."""
    from session import SessionMeta

    cleaned = (text or "").strip()
    if not cleaned:
        return TerminalPlanGateDecision(False, "classifier", "empty")
    if os.environ.get("TERMINAL_PLAN_CLASSIFY", "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return TerminalPlanGateDecision(False, "classifier_disabled", "disabled")

    from llm_routing import resolve_model_id_for_role

    model = resolve_model_id_for_role("topic_routing", meta or SessionMeta())
    try:
        response = llm.chat(
            [
                {"role": "system", "content": _TERMINAL_PLAN_CLASSIFY_PROMPT},
                {"role": "user", "content": cleaned},
            ],
            model=model,
            temperature=0.0,
        )
        data = _parse_terminal_plan_classify_json(response.content or "")
        needs_plan = bool(data.get("needs_plan"))
        reason = str(data.get("reason") or "").strip() or (
            "multi_step_execute" if needs_plan else "direct_execute"
        )
        return TerminalPlanGateDecision(needs_plan, "classifier", reason)
    except Exception as exc:
        return TerminalPlanGateDecision(False, "classifier_failed", str(exc))


def resolve_auto_plan_turn(
    user_text: str,
    session: Session,
    intent: str,
    artifact: TerminalPlanArtifact | None,
    *,
    llm: Any | None = None,
) -> TerminalPlanGateDecision:
    """Rules fast-path + optional classifier for ambiguous turns."""
    gate = auto_plan_gate_rules(user_text, session, intent, artifact)
    if gate == "skip":
        return TerminalPlanGateDecision(False, "rule_skip")
    if gate == "yes":
        return TerminalPlanGateDecision(True, "rule_yes")
    if llm is None:
        return TerminalPlanGateDecision(False, "no_llm")
    return classify_terminal_plan_need(user_text, llm=llm, meta=session.meta)


def should_handle_terminal_plan_turn(
    user_text: str,
    session: Session,
    intent: str,
    artifact: TerminalPlanArtifact | None,
) -> bool:
    """Whether run_turn should enter the terminal plan state machine."""
    if intent != "execute":
        return False
    if is_explicit_replan_turn(user_text) and artifact is not None:
        return True
    if should_resume_step_execution(artifact):
        return True
    gate = auto_plan_gate_rules(user_text, session, intent, artifact)
    return gate in {"yes", "classify"}


def should_resume_step_execution(artifact: TerminalPlanArtifact | None) -> bool:
    if artifact is None:
        return False
    if artifact.phase not in {"executing", "validating"}:
        return False
    if artifact.current_step >= len(artifact.steps):
        return False
    return True


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse planner JSON from raw assistant text."""
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("empty planner output")
    try:
        payload = json.loads(stripped)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped, re.IGNORECASE)
    if fence:
        payload = json.loads(fence.group(1).strip())
        if isinstance(payload, dict):
            return payload
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        payload = json.loads(stripped[start : end + 1])
        if isinstance(payload, dict):
            return payload
    raise ValueError("planner output is not valid JSON object")


def artifact_from_planner_payload(
    payload: dict[str, Any],
    *,
    goal_fp: str,
    summary: str = "",
) -> TerminalPlanArtifact:
    steps_raw = payload.get("steps") or []
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ValueError("planner returned no steps")
    steps = [
        TerminalPlanStep.from_dict(item)
        for item in steps_raw
        if isinstance(item, dict)
    ]
    if not steps:
        raise ValueError("planner steps invalid")
    if len(steps) > _MAX_STEPS:
        steps = steps[:_MAX_STEPS]
    for index, step in enumerate(steps, start=1):
        if not step.id or step.id == "step":
            step.id = f"step-{index}"
    planner_summary = str(payload.get("summary") or summary or "").strip()
    now = utc_now_iso()
    return TerminalPlanArtifact(
        goal_fingerprint=goal_fp,
        phase="executing",
        steps=steps,
        current_step=0,
        retry_count=0,
        replan_count=0,
        initial_plan_done=True,
        planner_summary=planner_summary,
        created_at=now,
        updated_at=now,
    )


def format_step_overlay(artifact: TerminalPlanArtifact, *, user_text: str = "") -> str:
    step = artifact.current()
    if step is None:
        return ""
    total = len(artifact.steps)
    index = artifact.current_step + 1
    scope = ", ".join(step.scope) if step.scope else "(见步骤说明)"
    verify = "\n".join(f"- {item}" for item in step.verify) if step.verify else "- (无自动验证命令)"
    lines = [
        "[Terminal Plan · 当前步骤]",
        f"进度: {index}/{total} · step_id={step.id} · retry={artifact.retry_count}",
        f"标题: {step.title}",
        f"范围: {scope}",
        "验证命令:",
        verify,
        "纪律: 只完成本步骤；完成后停止，等待用户「继续」再执行下一步。",
    ]
    if user_text.strip() and not is_continue_turn(user_text):
        lines.append(f"用户补充: {user_text.strip()}")
    if artifact.planner_summary:
        lines.append(f"计划摘要: {artifact.planner_summary}")
    return "\n".join(lines)


def format_plan_notice(
    *,
    model_label: str,
    effort: str,
    step_count: int,
    replan: bool = False,
    degraded: bool = False,
) -> str:
    prefix = "auto-replan" if replan else "auto-plan"
    suffix = " · 降级" if degraded else ""
    return f"[Terminal] {prefix} · {model_label} · {effort}{suffix} · {step_count} steps"


def format_planning_notice(
    *,
    model_label: str,
    effort: str,
    replan: bool = False,
    degraded: bool = False,
) -> str:
    """In-progress planning line for Terminal transcript (before JSON plan is ready)."""
    prefix = "auto-replan 规划中" if replan else "auto-plan 规划中"
    suffix = " · 降级" if degraded else ""
    return f"[Terminal] {prefix} · {model_label} · {effort}{suffix}…"


def terminal_plan_state_event(
    *,
    mode: str,
    model: str = "",
    effort: str = "",
    step: int = 0,
    total: int = 0,
    retry: int = 0,
    replan: int = 0,
    title: str = "",
) -> dict[str, Any]:
    """Structured status payload for Terminal TUI (T-5733)."""
    payload: dict[str, Any] = {
        "type": "terminal.plan.state",
        "mode": (mode or "").strip(),
        "model": (model or "").strip(),
        "effort": (effort or "").strip(),
        "step": max(0, int(step)),
        "total": max(0, int(total)),
        "retry": max(0, int(retry)),
        "replan": max(0, int(replan)),
    }
    if title.strip():
        payload["title"] = title.strip()
    return payload


def plan_status_segment(
    *,
    mode: str,
    model: str = "",
    effort: str = "",
    step: int = 0,
    total: int = 0,
    retry: int = 0,
    replan: int = 0,
    degraded: bool = False,
) -> str:
    """Short status-bar segment for legacy TUI + Ink."""
    key = (mode or "").strip().casefold()
    short_model = (model or "").rsplit("-", 1)[-1] if model else ""
    if key == "planning":
        eff = effort or (_PLANNING_DEGRADED_EFFORT if degraded else "max")
        suffix = " · 降级" if degraded else ""
        return f"auto-plan · {short_model or 'pro'} · {eff}{suffix}"
    if key == "replanning":
        eff = effort or (_PLANNING_DEGRADED_EFFORT if degraded else "max")
        suffix = " · 降级" if degraded else ""
        replan_suffix = f" · r{replan}" if replan else ""
        return f"replan · {short_model or 'pro'} · {eff}{suffix}{replan_suffix}"
    if key in {"executing", "validating"}:
        step_text = f"{step}/{total}" if total > 0 else str(step or "?")
        effort_text = effort or "medium"
        retry_text = f" · retry {retry}" if retry else ""
        return f"step {step_text} · execute · {effort_text}{retry_text}"
    if key == "done":
        return "plan done"
    if key == "stopped":
        return "plan stopped"
    return ""


def plan_status_from_artifact(
    artifact: TerminalPlanArtifact | None,
    *,
    session_model: str = "",
    session_effort: str = "",
) -> str:
    """Resume-friendly plan segment from persisted artifact."""
    if artifact is None:
        return ""
    phase = (artifact.phase or "").strip().casefold()
    if phase in {"", "idle", "planning", "replanning"}:
        return ""
    if phase == "done":
        return plan_status_segment(mode="done")
    if phase == "stopped":
        return plan_status_segment(mode="stopped")
    index = artifact.current_step + 1
    total = len(artifact.steps)
    return plan_status_segment(
        mode=phase,
        model=session_model,
        effort=session_effort,
        step=index,
        total=total,
        retry=artifact.retry_count,
        replan=artifact.replan_count,
    )


def format_step_done_notice(artifact: TerminalPlanArtifact, *, passed: bool) -> str:
    step = artifact.current()
    total = len(artifact.steps)
    index = artifact.current_step + 1
    title = step.title if step else "?"
    if passed:
        if index >= total:
            return f"[Terminal] 步骤 {index}/{total} 完成（{title}）。全部步骤已通过。"
        return (
            f"[Terminal] 步骤 {index}/{total} 完成（{title}）。"
            "回复「继续」执行下一步。"
        )
    return f"[Terminal] 步骤 {index}/{total} 未通过（{title}）。"


def merge_replan_payload(
    artifact: TerminalPlanArtifact,
    payload: dict[str, Any],
) -> TerminalPlanArtifact:
    """Bounded replan: replace remaining steps only (TM-27)."""
    steps_raw = payload.get("steps") or []
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ValueError("replan returned no steps")
    new_steps = [
        TerminalPlanStep.from_dict(item)
        for item in steps_raw
        if isinstance(item, dict)
    ]
    if not new_steps:
        raise ValueError("replan steps invalid")
    if len(new_steps) > _MAX_STEPS:
        new_steps = new_steps[:_MAX_STEPS]
    passed = [step for step in artifact.steps if step.status == "passed"]
    merged = passed + new_steps
    for index, step in enumerate(merged, start=1):
        if not step.id or step.id == "step":
            step.id = f"step-{index}"
    artifact.steps = merged
    artifact.current_step = len(passed)
    artifact.retry_count = 0
    artifact.replan_count += 1
    artifact.phase = "executing"
    summary = str(payload.get("summary") or "").strip()
    if summary:
        artifact.planner_summary = summary
    return artifact


def run_step_verification(
    executor: Any,
    step: TerminalPlanStep,
) -> tuple[bool, str]:
    """Run step.verify shell commands via run_command (TM-27 M0)."""
    if not step.verify:
        return True, ""
    from tools.schema import tool_ok

    failures: list[str] = []
    for raw_cmd in step.verify:
        cmd = raw_cmd.strip()
        if not cmd:
            continue
        result = executor.run(
            "run_evolved",
            {
                "tool_name": "run_command",
                "arguments": {
                    "command": cmd,
                    "working_dir": ".",
                },
            },
        )
        if not tool_ok(result):
            msg = getattr(result.error, "message", str(result))
            failures.append(f"{cmd!r} -> {msg}")
            continue
        if isinstance(result.data, dict):
            exit_code = result.data.get("exit_code")
            if exit_code is not None and int(exit_code) != 0:
                stderr = str(result.data.get("stderr") or "")
                failures.append(f"{cmd!r} exit={exit_code} {stderr[:200]}")
    if failures:
        return False, "; ".join(failures)
    return True, ""
