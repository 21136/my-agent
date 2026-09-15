"""Execution reliability helpers (EXEC-RELIABILITY / G14 · Phase 35).

M0: postcondition success-claim gate + call-fingerprint circuit breaker.
M1: failure class A–F + playbook nudges (P-npm-corrupt / P-sql-missing / P-port-dead).
"""

from __future__ import annotations

import json
import os
import re
import re
from dataclasses import dataclass
from typing import Any, Literal

FailureClass = Literal["A", "B", "C", "D", "E", "F"]

# --- Success-claim gate (postcondition) ---------------------------------------

SERVICE_SUCCESS_MARKERS: tuple[str, ...] = (
    "已启动",
    "正在运行",
    "前端 OK",
    "前端OK",
    "可以打开",
    "可访问",
    "started successfully",
    "is running",
    "is up",
)
SERVICE_SUCCESS_REPLACEMENT = "〔未满足后置条件·已拦截〕"

_LOCALHOST_URL_RE = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1):\d+\S*",
    re.IGNORECASE,
)

# --- Circuit breaker ----------------------------------------------------------

_DEFAULT_CIRCUIT_THRESHOLD = 3

EXEC_CIRCUIT_NUDGE_MESSAGE = (
    "[内核] 同类失败已熔断：请换策略或停下来说明根因，勿重复同一命令。"
)

EXEC_SEGMENT_FAILURE_NUDGE_MESSAGE = (
    "[内核] 本段已多次失败：请停下说明根因或换策略，勿继续盲试。"
)

_DEFAULT_INLINE_WRITE_GUARD_MAX = 2

EXEC_INLINE_WRITE_NUDGE_MESSAGE = (
    "[内核] 内联正文已两次超过 WRITE_INLINE_MAX_CHARS（8192）。\n"
    "禁止再用 write_text 的 content/content_base64 写大文件。\n"
    "请：write_text → workspace/_staging/<name>（仅 staging 小文件）→ "
    "再 run_evolved write_text 带 content_workspace_path。\n"
    "或改用 patch_file 小范围修改。本段请先文字说明再让用户继续。"
)

_DEFAULT_SEGMENT_FAILURE_BUDGET = 3
_DEFAULT_RUNAWAY_SEGMENT_FAILURE_BUDGET = 8
_DEFAULT_RUNAWAY_LLM_TRANSPORT_RETRIES = 4
_DEFAULT_RUNAWAY_POOL_EXHAUSTED_RETRIES = 2
_DEFAULT_RUNAWAY_LLM_COOLDOWN_SEC = 1.5
_DEFAULT_RUNAWAY_PLAN_PARTNER_MAX = 2
_DEFAULT_RUNAWAY_PLAN_PARTNER_MAX_BUG_FIX = 0
_DEFAULT_RUNAWAY_PLAN_GATEWAY_FAIL_MAX = 3

EXEC_PATCH_ANCHOR_NUDGE_MESSAGE = (
    "[内核] patch_file 锚点不唯一或未找到：请先 read_file 确认上下文，"
    "改用更长 find 锚点、line_range，或 run_evolved write_text 小范围重写；"
    "狂奔模式下此类参数错误不计入段失败预算，但请勿重复同一锚点。"
)

EXEC_RUNAWAY_PLAN_GATEWAY_NUDGE_MESSAGE = (
    "[Harness] plan_partner 网关连续失败，本回合 plan 预算已耗尽或不可用。"
    "请改用 run_evolved write_text 或 patch_file 直接修改项目文件（尤其 PROJECT.md 的验收命令章节），"
    "不要继续调用 plan_partner；修复后重新运行对口测试与验收命令。"
)


def runaway_kernel_plan_spawn_blocked(
    *,
    runaway_enabled: bool,
    workflow_stage: str,
    checkpoint: str,
) -> bool:
    """T-3905 kernel pre-spawn must respect the same gates as tool surface (UI-5969+)."""
    return runaway_plan_partner_blocked(
        runaway_enabled=runaway_enabled,
        workflow_stage=workflow_stage,
        checkpoint=checkpoint,
    )


def runaway_plan_gateway_nudge_message(
    *,
    checkpoint: str,
    workflow_stage: str,
    acceptance_passed: bool = False,
) -> str:
    """Checkpoint-aware nudge — avoid contradicting verification exit / bug-fix lane."""
    from runaway_flow import normalize_checkpoint

    cp = normalize_checkpoint(checkpoint)
    stage = (workflow_stage or "").strip()
    if cp in _RUNAWAY_EXIT_CHECKPOINTS and stage in _RUNAWAY_VERIFICATION_STAGES:
        return (
            "[Harness] plan_partner 在 verification 出口已禁用。"
            "Harness 硬验收已通过，勿再改 PROJECT/TASKS 或调用 plan_partner / deliverable_review。"
        )
    if cp == "repairing":
        return (
            "[Harness] plan_partner 不可用。"
            "repairing 由 bug-fix 子代理补 PROJECT 验收命令、ENV quality.commands 与 VERIFY 证据；"
            "勿再调用 plan_partner。"
        )
    if acceptance_passed and stage in _RUNAWAY_VERIFICATION_STAGES:
        return (
            "[Harness] plan_partner 不可用。"
            "验收已通过，勿再调用 plan_partner；仅修复代码或 VERIFY 证据后重跑验收命令。"
        )
    return EXEC_RUNAWAY_PLAN_GATEWAY_NUDGE_MESSAGE

_CALL_FP_KEYS: tuple[str, ...] = (
    "command",
    "cmd",
    "path",
    "url",
    "working_dir",
    "cwd",
    "action",
    "service_id",
    "name",
)

# --- Failure class + playbooks (M1) -------------------------------------------

_NPM_CORRUPT_RE = re.compile(
    r"(?:"
    r"unexpected\s+end\s+of\s+file"
    r"|unexpected\s+eof"
    r"|eintegrity"
    r"|npm\s+err!.*(?:enoent|eintegrity)"
    r"|(?:esbuild|rollup|vite).{0,80}(?:eof|unexpected end)"
    r"|cannot\s+find\s+module\s+['\"]?@?esbuild"
    r"|broken\s+symlink.*node_modules"
    r")",
    re.IGNORECASE | re.DOTALL,
)
_NPM_CONTEXT_RE = re.compile(
    r"(?:node_modules|esbuild|vite|npm|pnpm|yarn)",
    re.IGNORECASE,
)

_SQL_MISSING_RE = re.compile(
    r"(?:"
    r"table\s+['\"`]?[\w.]+['\"`]?\s+doesn'?t\s+exist"
    r"|no\s+such\s+table"
    r"|relation\s+['\"`]?[\w.]+['\"`]?\s+does\s+not\s+exist"
    r"|unknown\s+table\s+['\"`]?[\w.]+"
    r"|1146\s*\("  # MySQL ER_NO_SUCH_TABLE
    r")",
    re.IGNORECASE,
)

_AUTH_RE = re.compile(
    r"(?:"
    r"\b401\b"
    r"|unauthorized"
    r"|forbidden|\b403\b"
    r"|invalid\s+token"
    r"|jwt.{0,40}(?:too\s+short|malformed|expired)"
    r"|access\s+denied"
    r"|bad\s+credentials"
    r")",
    re.IGNORECASE | re.DOTALL,
)

_PORT_DEAD_RE = re.compile(
    r"(?:"
    r"econnrefused"
    r"|connection\s+refused"
    r"|actively\s+refused"
    r"|ready\s+criteria\s+not\s+met"
    r"|port\s+\d+\s+(?:not|isn'?t)\s+(?:open|listening)"
    r")",
    re.IGNORECASE,
)

_CANCEL_RE = re.compile(r"(?:cancelled|canceled|timed?\s*out|timeout)", re.IGNORECASE)

PLAYBOOK_NPM_CORRUPT = "P-npm-corrupt"
PLAYBOOK_SQL_MISSING = "P-sql-missing"
PLAYBOOK_PORT_DEAD = "P-port-dead"

PLAYBOOK_NUDGES: dict[str, str] = {
    PLAYBOOK_NPM_CORRUPT: (
        "[内核] 剧本 P-npm-corrupt：依赖可能截断/损坏（esbuild·node_modules EOF）。\n"
        "请：1) 向用户确认后删除 node_modules（可选 package-lock/pnpm-lock）；"
        "2) 在前端目录 `run_command` 执行 npm/pnpm install；"
        "3) 再用 `run_service` 启动并确认 ready+alive。禁止反复 npm run dev。"
    ),
    PLAYBOOK_SQL_MISSING: (
        "[内核] 剧本 P-sql-missing：表不存在。\n"
        "请：定位并执行项目 `database/init.sql`（或等价建表脚本）；"
        "禁止只 UPDATE/盲重试登录。建表后再验业务接口。"
    ),
    PLAYBOOK_PORT_DEAD: (
        "[内核] 剧本 P-port-dead：服务名义运行但端口不可用。\n"
        "请：`run_service` stop（或 kill_port）后再 start，并以 ready+alive 为成功标准。"
    ),
}


@dataclass(frozen=True, slots=True)
class FailureInsight:
    failure_class: FailureClass
    playbook_id: str | None = None
    blob_preview: str = ""


def circuit_threshold() -> int:
    raw = os.environ.get("MY_AGENT_EXEC_CIRCUIT_N", str(_DEFAULT_CIRCUIT_THRESHOLD))
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_CIRCUIT_THRESHOLD
    return max(2, value)


def segment_failure_budget(session: Any = None) -> int:
    """Max countable failures per execute segment (AGENT-HARNESS P5)."""
    raw = os.environ.get(
        "MY_AGENT_SEGMENT_FAILURE_BUDGET",
        str(_DEFAULT_SEGMENT_FAILURE_BUDGET),
    )
    try:
        base = max(1, int(raw))
    except ValueError:
        base = _DEFAULT_SEGMENT_FAILURE_BUDGET
    if session is not None and getattr(session, "runaway_enabled", False):
        runaway_raw = os.environ.get(
            "MY_AGENT_RUNAWAY_SEGMENT_FAILURE_BUDGET",
            str(_DEFAULT_RUNAWAY_SEGMENT_FAILURE_BUDGET),
        )
        try:
            return max(base, int(runaway_raw))
        except ValueError:
            return max(base, _DEFAULT_RUNAWAY_SEGMENT_FAILURE_BUDGET)
    return base


def runaway_llm_transport_retries() -> int:
    """Extra LLM transport retries while runaway is active (504 / pool exhausted)."""
    raw = os.environ.get(
        "MY_AGENT_RUNAWAY_LLM_RETRIES",
        str(_DEFAULT_RUNAWAY_LLM_TRANSPORT_RETRIES),
    )
    try:
        return max(2, int(raw))
    except ValueError:
        return _DEFAULT_RUNAWAY_LLM_TRANSPORT_RETRIES


def runaway_pool_exhausted_retries() -> int:
    """Fewer retries when provider reports pool exhausted / gateway timeout."""
    raw = os.environ.get(
        "MY_AGENT_RUNAWAY_POOL_RETRIES",
        str(_DEFAULT_RUNAWAY_POOL_EXHAUSTED_RETRIES),
    )
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_RUNAWAY_POOL_EXHAUSTED_RETRIES


def is_pool_exhausted_transport_error(exc: BaseException) -> bool:
    """True for pool exhausted / gateway timeout / balance misreport / Tokeness channel class errors."""
    message = str(exc or "").lower()
    return (
        "pool exhausted" in message
        or "gateway timeout" in message
        or "insufficient balance" in message
        or "temporarily_unavailable" in message
        or "temporarily unavailable" in message
        or "可用渠道" in message
        or "503" in message
        or "504" in message
        or "upstream" in message
    )


def is_tokeness_reasoning_tools_error(exc: BaseException) -> bool:
    message = str(exc or "").lower()
    return "reasoning_effort" in message and "function tools" in message


def llm_transport_user_hint(exc: BaseException) -> str:
    """Short user-facing hint; keep raw exception in logs elsewhere."""
    if is_tokeness_reasoning_tools_error(exc):
        return (
            "Tokeness 拒绝 tools+reasoning_effort：请完全退出 Desktop 后重启"
            "（需加载最新 llm_client 修复）"
        )
    message = str(exc or "")
    lower = message.lower()
    if "temporarily_unavailable" in lower or "可用渠道" in message:
        return "Tokeness Luna 上游渠道暂时不可用，可稍后重试或切换 0x567-flash"
    if is_pool_exhausted_transport_error(exc):
        return "模型网关繁忙或超时"
    return message[:240]


def is_plan_gateway_failure_summary(summary: str) -> bool:
    """Plan subagent returned an LLM gateway failure without valid operations."""
    text = str(summary or "").strip()
    if not text.startswith("LLM 调用失败"):
        return False
    lower = text.lower()
    return is_pool_exhausted_transport_error(RuntimeError(lower))


def is_plan_gateway_tool_failure(result: Any) -> bool:
    """True when plan_partner failed with retryable gateway / upstream error."""
    if getattr(result, "ok", None) is not False:
        return False
    error = getattr(result, "error", None)
    if error is None:
        return False
    code = str(getattr(error, "code", "") or "")
    if code == "upstream_error":
        return True
    details = getattr(error, "details", None)
    if isinstance(details, dict) and details.get("plan_gateway_failure"):
        return True
    message = str(getattr(error, "message", "") or "")
    return is_plan_gateway_failure_summary(message)


def runaway_plan_gateway_fail_max() -> int:
    """Consecutive gateway plan failures before Harness nudge (R7-31b)."""
    raw = os.environ.get(
        "MY_AGENT_RUNAWAY_PLAN_GATEWAY_FAIL_MAX",
        str(_DEFAULT_RUNAWAY_PLAN_GATEWAY_FAIL_MAX),
    )
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_RUNAWAY_PLAN_GATEWAY_FAIL_MAX


def runaway_llm_round_cooldown_seconds() -> float:
    """Pause between tool-loop LLM rounds while runaway is active."""
    raw = os.environ.get(
        "MY_AGENT_RUNAWAY_LLM_COOLDOWN_SEC",
        str(_DEFAULT_RUNAWAY_LLM_COOLDOWN_SEC),
    )
    try:
        value = float(raw)
    except ValueError:
        value = _DEFAULT_RUNAWAY_LLM_COOLDOWN_SEC
    return max(0.0, value)


def runaway_bug_fix_enabled() -> bool:
    """Phase 59 · Harness-owned bug-fix lane (default on)."""
    raw = os.environ.get("MY_AGENT_RUNAWAY_BUG_FIX_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def runaway_plan_partner_max_per_turn() -> int:
    """Cap plan_partner calls per turn during runaway implementation."""
    default = (
        _DEFAULT_RUNAWAY_PLAN_PARTNER_MAX_BUG_FIX
        if runaway_bug_fix_enabled()
        else _DEFAULT_RUNAWAY_PLAN_PARTNER_MAX
    )
    raw = os.environ.get("MY_AGENT_RUNAWAY_PLAN_PARTNER_MAX", str(default))
    try:
        return max(0, int(raw))
    except ValueError:
        return default


_RUNAWAY_VERIFICATION_STAGES = frozenset({"verification", "release"})
_RUNAWAY_EXIT_CHECKPOINTS = frozenset({"verifying", "release_wait"})


def runaway_plan_partner_blocked(
    *,
    runaway_enabled: bool,
    workflow_stage: str,
    checkpoint: str,
    runaway_v2_forbid_plan_partner: bool = False,
) -> bool:
    """UI-5969: repairing + verification 出口禁止 plan_partner（Harness/bug-fix 为真源）。"""
    if runaway_v2_forbid_plan_partner:
        return True
    if not runaway_enabled or not runaway_bug_fix_enabled():
        return False
    from runaway_flow import normalize_checkpoint

    cp = normalize_checkpoint(checkpoint)
    if cp == "repairing":
        return True
    stage = (workflow_stage or "").strip()
    return cp in _RUNAWAY_EXIT_CHECKPOINTS and stage in _RUNAWAY_VERIFICATION_STAGES


def runaway_deliverable_review_blocked(
    *,
    runaway_enabled: bool,
    workflow_stage: str,
    checkpoint: str,
    acceptance_passed: bool,
) -> bool:
    """UI-5969: Harness 已绿后 deliverable_review 仅制造噪声。"""
    if not runaway_enabled or not runaway_bug_fix_enabled():
        return False
    from runaway_flow import normalize_checkpoint

    cp = normalize_checkpoint(checkpoint)
    if cp in {"release_wait", "repairing"}:
        return True
    stage = (workflow_stage or "").strip()
    return (
        cp == "verifying"
        and acceptance_passed
        and stage in _RUNAWAY_VERIFICATION_STAGES
    )


def runaway_verification_tool_suppressed(
    *,
    runaway_enabled: bool,
    workflow_stage: str,
    checkpoint: str,
    acceptance_passed: bool,
) -> frozenset[str]:
    blocked: set[str] = set()
    if runaway_plan_partner_blocked(
        runaway_enabled=runaway_enabled,
        workflow_stage=workflow_stage,
        checkpoint=checkpoint,
    ):
        blocked.add("plan_partner")
    if runaway_deliverable_review_blocked(
        runaway_enabled=runaway_enabled,
        workflow_stage=workflow_stage,
        checkpoint=checkpoint,
        acceptance_passed=acceptance_passed,
    ):
        blocked.add("deliverable_review")
    return frozenset(blocked)


_RUNAWAY_HARNESS_UTTERANCE_RE = re.compile(
    r"^\s*(?:\[Harness\]|狂奔模式[:：])",
    re.IGNORECASE,
)


def is_runaway_harness_utterance(text: str) -> bool:
    """True for Harness-injected continuation lines (not end-user Q&A)."""
    return bool(_RUNAWAY_HARNESS_UTTERANCE_RE.search(text or ""))


def is_internal_harness_chat_line(text: str) -> bool:
    """True for harness/runaway routing lines that must not appear in ordinary chat."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    if is_runaway_harness_utterance(stripped):
        return True
    if stripped.startswith("[狂奔续接]") or "[狂奔续接]" in stripped[:32]:
        return True
    return False


_RUNAWAY_CONTINUE_EXACT = frozenset(
    {
        "继续",
        "继续狂奔",
        "接着",
        "接着做",
        "下一项",
        "下一回合",
        "continue",
        "resume",
        "go on",
        "go",
    }
)


def is_runaway_continue_utterance(text: str) -> bool:
    """Short user nudges that should keep runaway chaining (not qa-only turns)."""
    if is_runaway_harness_utterance(text):
        return True
    stripped = (text or "").strip()
    if not stripped:
        return False
    if stripped.casefold() in _RUNAWAY_CONTINUE_EXACT:
        return True
    lower = stripped.casefold()
    if lower.startswith(("继续", "接着", "下一", "续接")):
        return True
    if lower in {"continue", "resume"}:
        return True
    return False


# UI-6052: these tools are advisory / plan-domain — never checkpoint truth.
RUNAWAY_CHECKPOINT_ADVISORY_TOOLS = frozenset(
    {"deliverable_review", "plan_partner", "report_progress"}
)


def runaway_advisory_tool_may_own_checkpoint(tool_name: str) -> bool:
    """False for tools that must not drive ``project_runaway_checkpoint`` transitions."""
    return (tool_name or "").strip() not in RUNAWAY_CHECKPOINT_ADVISORY_TOOLS


def runaway_verification_exit_short_circuit(
    *,
    runaway_enabled: bool,
    workflow_stage: str,
    checkpoint: str,
    acceptance_passed: bool,
    turn_intent: str = "",
    user_text: str = "",
) -> bool:
    """UI-5970: release_wait + acceptance_passed → skip LLM (avoid stale history narrative)."""
    if not runaway_enabled or not acceptance_passed:
        return False
    intent = (turn_intent or "").strip()
    if intent == "requirements":
        return False
    harness_line = is_runaway_harness_utterance(user_text)
    if intent in {"qa", "recall"} and not harness_line:
        return False
    from runaway_flow import normalize_checkpoint

    cp = normalize_checkpoint(checkpoint)
    if cp != "release_wait":
        return False
    stage = (workflow_stage or "").strip()
    return stage in _RUNAWAY_VERIFICATION_STAGES


def llm_transport_backoff_seconds(attempt: int, *, pool_exhausted: bool = False) -> float:
    """Exponential backoff for provider transport errors."""
    step = max(1, int(attempt or 1))
    if pool_exhausted:
        return min(30.0 * step, 90.0)
    return min(2.0 * (2 ** (step - 1)), 15.0)


def is_patch_anchor_param_failure(result: Any) -> bool:
    """patch_file find anchor not found / ambiguous — param error, not env failure."""
    if getattr(result, "ok", None) is not False:
        return False
    error = getattr(result, "error", None)
    if error is None:
        return False
    code = str(getattr(error, "code", "") or "")
    if code not in {"validation_error", "VALIDATION_ERROR"}:
        return False
    message = str(getattr(error, "message", "") or "").lower()
    return "anchor" in message and (
        "not found" in message or "must be unique" in message or "matched" in message
    )


def should_count_segment_failure(result: Any, session: Any) -> bool:
    """Whether a failure consumes the per-segment failure budget."""
    if not is_circuit_countable_failure(result):
        return False
    if getattr(session, "runaway_enabled", False) and is_patch_anchor_param_failure(result):
        return False
    return True


def record_segment_failure(session: Any) -> bool:
    """Bump segment-wide failure count. Return True if budget just reached."""
    count = int(getattr(session, "segment_failure_count", 0) or 0) + 1
    session.segment_failure_count = count
    if count >= segment_failure_budget(session) and not getattr(
        session, "segment_failure_budget_hit", False
    ):
        session.segment_failure_budget_hit = True
        session.segment_failure_budget_just_hit = True
        return True
    return False


def clear_segment_failure_budget(session: Any) -> None:
    session.segment_failure_count = 0
    session.segment_failure_budget_hit = False
    session.segment_failure_budget_just_hit = False


def inline_write_guard_max() -> int:
    """Repeat inline_write_max guard threshold per execute segment (BUG-024)."""
    raw = os.environ.get(
        "MY_AGENT_INLINE_WRITE_GUARD_MAX",
        str(_DEFAULT_INLINE_WRITE_GUARD_MAX),
    )
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_INLINE_WRITE_GUARD_MAX


def record_inline_write_guard_failure(session: Any) -> bool:
    """Bump inline_write_max streak. Return True if block threshold just reached."""
    streak = int(getattr(session, "inline_write_guard_streak", 0) or 0) + 1
    session.inline_write_guard_streak = streak
    if streak >= inline_write_guard_max() and not getattr(
        session, "inline_write_guard_blocked", False
    ):
        session.inline_write_guard_blocked = True
        session.inline_write_guard_just_blocked = True
        return True
    return False


def clear_inline_write_guard_streak(session: Any) -> None:
    """Reset streak after a successful staging/small write (same segment)."""
    session.inline_write_guard_streak = 0


def clear_inline_write_guard(session: Any) -> None:
    session.inline_write_guard_streak = 0
    session.inline_write_guard_blocked = False
    session.inline_write_guard_just_blocked = False


def claims_service_success(text: str) -> bool:
    """True when assistant text claims a service/page is up (heuristic)."""
    if not isinstance(text, str) or not text.strip():
        return False
    lower = text.lower()
    for marker in SERVICE_SUCCESS_MARKERS:
        if marker.isascii():
            if marker.lower() in lower:
                return True
        elif marker in text:
            return True
    if _LOCALHOST_URL_RE.search(text):
        if any(
            tip in text
            for tip in ("打开", "访问", "可用", "OK", "ok", "成功", "通了", "起来")
        ):
            return True
    return False


def apply_service_success_gate(text: str, *, postcondition_ok: bool) -> str:
    """Rewrite success claims when start/alive postcondition is not met."""
    if postcondition_ok or not claims_service_success(text):
        return text
    cleaned = text
    for marker in SERVICE_SUCCESS_MARKERS:
        if marker.isascii():
            cleaned = re.sub(
                re.escape(marker), SERVICE_SUCCESS_REPLACEMENT, cleaned, flags=re.IGNORECASE
            )
        else:
            cleaned = cleaned.replace(marker, SERVICE_SUCCESS_REPLACEMENT)
    cleaned = _LOCALHOST_URL_RE.sub(SERVICE_SUCCESS_REPLACEMENT, cleaned)
    if SERVICE_SUCCESS_REPLACEMENT not in cleaned:
        cleaned = f"{cleaned.rstrip()}\n{SERVICE_SUCCESS_REPLACEMENT}"
    return cleaned


def _parse_tool_envelope(content: str) -> dict[str, Any] | None:
    try:
        data = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def run_service_postcondition_ok(messages: list[dict[str, Any]]) -> bool:
    """True if this transcript has a run_service result with ready+alive."""
    latest_ok = False
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        envelope = _parse_tool_envelope(content)
        if envelope is None:
            continue
        data = envelope.get("data")
        if not isinstance(data, dict):
            continue
        if data.get("tool_name") != "run_service":
            continue
        action = str(data.get("action") or "").strip().lower()
        if action and action not in {"start", "restart", "status", "wait_ready", ""}:
            continue
        state = data.get("state") if isinstance(data.get("state"), dict) else {}
        ready = data.get("ready")
        alive = state.get("alive") if isinstance(state, dict) else None
        if ready is True and alive is True:
            latest_ok = True
        elif ready is False or alive is False:
            latest_ok = False
        elif envelope.get("ok") is True and alive is True:
            latest_ok = True
        elif envelope.get("ok") is False:
            latest_ok = False
    return latest_ok


def normalize_fp_part(value: Any) -> str:
    text = str(value).strip().replace("\\", "/")
    text = re.sub(r"\s+", " ", text)
    return text[:160]


def call_fingerprint(tool_name: str, arguments: dict[str, Any] | None) -> str:
    """Stable fingerprint for 'same tool + same command' circuit matching."""
    name = (tool_name or "").strip()
    args = dict(arguments or {})
    evolved = ""
    inner: dict[str, Any] = args
    if name == "run_evolved":
        raw_evolved = args.get("tool_name")
        evolved = raw_evolved.strip() if isinstance(raw_evolved, str) else ""
        nested = args.get("arguments")
        if isinstance(nested, dict):
            inner = nested
    parts = [name, evolved]
    for key in _CALL_FP_KEYS:
        value = inner.get(key)
        if value is None and key in args:
            value = args.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            parts.append(f"{key}={normalize_fp_part(value)}")
    return "|".join(parts)[:320]


def extract_failure_blob(result: Any) -> str:
    """Concatenate message / stderr / logs / warning for classification."""
    chunks: list[str] = []
    error = getattr(result, "error", None)
    if error is not None:
        msg = getattr(error, "message", None)
        if isinstance(msg, str) and msg.strip():
            chunks.append(msg)
        details = getattr(error, "details", None) or {}
        if isinstance(details, dict):
            for key in ("stderr", "stdout", "logs_tail", "message", "warning"):
                raw = details.get(key)
                if isinstance(raw, str) and raw.strip():
                    chunks.append(raw)
            start = details.get("start")
            if isinstance(start, dict):
                lt = start.get("logs_tail")
                if isinstance(lt, str) and lt.strip():
                    chunks.append(lt)
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        for key in ("warning", "logs_tail", "stderr", "stdout", "message"):
            raw = data.get(key)
            if isinstance(raw, str) and raw.strip():
                chunks.append(raw)
        state = data.get("state")
        if isinstance(state, dict):
            for key in ("last_error", "status", "logs_tail"):
                raw = state.get(key)
                if isinstance(raw, str) and raw.strip():
                    chunks.append(raw)
        start = data.get("start")
        if isinstance(start, dict):
            lt = start.get("logs_tail")
            if isinstance(lt, str) and lt.strip():
                chunks.append(lt)
    blob = "\n".join(chunks)
    if len(blob) > 8000:
        return blob[-8000:]
    return blob


def match_playbook(blob: str) -> str | None:
    """Return playbook id if blob matches a known destructive pattern."""
    text = blob or ""
    if not text.strip():
        return None
    if _NPM_CORRUPT_RE.search(text) and _NPM_CONTEXT_RE.search(text):
        return PLAYBOOK_NPM_CORRUPT
    if re.search(r"unexpected\s+end\s+of\s+file", text, re.IGNORECASE) and _NPM_CONTEXT_RE.search(
        text
    ):
        return PLAYBOOK_NPM_CORRUPT
    if _SQL_MISSING_RE.search(text):
        return PLAYBOOK_SQL_MISSING
    if _PORT_DEAD_RE.search(text):
        return PLAYBOOK_PORT_DEAD
    return None


def classify_failure(result: Any) -> FailureInsight:
    """Map a tool result to failure class A–F and optional playbook."""
    blob = extract_failure_blob(result)
    preview = blob[:240].replace("\n", " ")
    ok = getattr(result, "ok", None)
    error = getattr(result, "error", None)
    data = getattr(result, "data", None)
    code = (getattr(error, "code", None) or "") if error is not None else ""
    details = getattr(error, "details", None) if error is not None else None
    details = details if isinstance(details, dict) else {}

    if ok is True and isinstance(data, dict) and data.get("tool_name") == "run_service":
        playbook = match_playbook(blob)
        state = data.get("state") if isinstance(data.get("state"), dict) else {}
        if data.get("ready") is False or state.get("alive") is False:
            cls: FailureClass = "B" if playbook == PLAYBOOK_NPM_CORRUPT else "E"
            return FailureInsight(
                failure_class=cls,
                playbook_id=playbook or PLAYBOOK_PORT_DEAD,
                blob_preview=preview,
            )
        return FailureInsight(failure_class="A", blob_preview=preview)

    if ok is not False:
        return FailureInsight(failure_class="A", blob_preview=preview)

    if code in {"confirm_rejected", "CONFIRM_REJECTED"}:
        return FailureInsight(failure_class="F", blob_preview=preview)
    msg_l = (getattr(error, "message", None) or "").lower()
    if _CANCEL_RE.search(blob) or "cancelled" in msg_l or "canceled" in msg_l:
        if "exit_code" not in details:
            return FailureInsight(failure_class="F", blob_preview=preview)

    if details.get("retry") is True or (
        code in {"validation_error", "VALIDATION_ERROR", "tool_not_found", "TOOL_NOT_FOUND"}
        and "exit_code" not in details
        and details.get("guard_type") != "exec_circuit"
    ):
        return FailureInsight(failure_class="A", blob_preview=preview)

    playbook = match_playbook(blob)
    if playbook == PLAYBOOK_NPM_CORRUPT:
        return FailureInsight(failure_class="B", playbook_id=playbook, blob_preview=preview)
    if playbook == PLAYBOOK_SQL_MISSING:
        return FailureInsight(failure_class="C", playbook_id=playbook, blob_preview=preview)
    if playbook == PLAYBOOK_PORT_DEAD:
        return FailureInsight(failure_class="E", playbook_id=playbook, blob_preview=preview)
    if _AUTH_RE.search(blob):
        return FailureInsight(failure_class="D", blob_preview=preview)
    return FailureInsight(failure_class="E", blob_preview=preview)


def playbook_nudge_message(playbook_id: str) -> str | None:
    """Deprecated: playbook auto-nudge abolished (D1). Always None."""
    return None


def playbooks_enabled() -> bool:
    """Auto playbook nudge master switch (default off after D1)."""
    raw = os.environ.get("MY_AGENT_PLAYBOOK_NUDGE", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def is_circuit_countable_failure(result: Any) -> bool:
    """Failures that count toward the circuit (not schema free-retries / cancel)."""
    insight = classify_failure(result)
    if insight.failure_class in {"A", "F"}:
        return False

    ok = getattr(result, "ok", None)
    error = getattr(result, "error", None)
    data = getattr(result, "data", None)

    if ok is True and isinstance(data, dict) and data.get("tool_name") == "run_service":
        action = str(data.get("action") or "").strip().lower()
        if action in {"start", "restart", ""}:
            if data.get("ready") is False:
                return True
            state = data.get("state") if isinstance(data.get("state"), dict) else {}
            if state.get("alive") is False:
                return True
        return False

    if ok is not False:
        return False
    if error is None:
        return True
    code = getattr(error, "code", None) or ""
    if code in {"confirm_rejected", "CONFIRM_REJECTED"}:
        return False
    details = getattr(error, "details", None) or {}
    if isinstance(details, dict):
        if details.get("guard_type") == "exec_circuit":
            return False
        if details.get("retry") is True:
            return False
    message = (getattr(error, "message", None) or "").lower()
    if "cancelled" in message and "exit_code" not in (
        details if isinstance(details, dict) else {}
    ):
        return False
    if code in {"validation_error", "VALIDATION_ERROR"} and "exit_code" not in (
        details if isinstance(details, dict) else {}
    ):
        if isinstance(details, dict) and details.get("retry") is False:
            return insight.failure_class not in {"A", "F"}
        return False
    return True


def record_circuit_failure(session: Any, fingerprint: str) -> bool:
    """Bump consecutive same-fingerprint streak. Return True if circuit just opened."""
    fp = (fingerprint or "").strip()
    if not fp:
        return False
    if getattr(session, "failure_streak_fp", "") == fp:
        session.failure_streak_count = int(getattr(session, "failure_streak_count", 0) or 0) + 1
    else:
        session.failure_streak_fp = fp
        session.failure_streak_count = 1
    open_set = getattr(session, "circuit_open_fingerprints", None)
    if open_set is None:
        session.circuit_open_fingerprints = set()
        open_set = session.circuit_open_fingerprints
    if session.failure_streak_count >= circuit_threshold():
        if fp not in open_set:
            open_set.add(fp)
            session.circuit_just_opened = fp
            return True
    return False


def record_circuit_success(session: Any) -> None:
    session.failure_streak_fp = ""
    session.failure_streak_count = 0
    session.circuit_just_opened = ""


def clear_circuit_state(session: Any) -> None:
    session.failure_streak_fp = ""
    session.failure_streak_count = 0
    open_set = getattr(session, "circuit_open_fingerprints", None)
    if isinstance(open_set, set):
        open_set.clear()
    else:
        session.circuit_open_fingerprints = set()
    session.circuit_just_opened = ""
    nudged = getattr(session, "playbook_nudged", None)
    if isinstance(nudged, set):
        nudged.clear()
    else:
        session.playbook_nudged = set()
    session.pending_playbook_id = ""
    clear_segment_failure_budget(session)
    clear_inline_write_guard(session)
    # Keep last_* for sidebar until begin_turn; clear segment-local soft flags only if present.
    # service_postcondition / claim_blocked / last_* reset in begin_turn.


def circuit_blocks(session: Any, fingerprint: str) -> bool:
    open_set = getattr(session, "circuit_open_fingerprints", None)
    if not open_set:
        return False
    return (fingerprint or "").strip() in open_set


def queue_playbook_nudge(session: Any, playbook_id: str | None) -> bool:
    """No-op after D1: playbook auto-nudge abolished (heuristic misjudgment).

    Set MY_AGENT_PLAYBOOK_NUDGE=1 only for legacy experiments.
    """
    if not playbooks_enabled():
        return False
    if not playbook_id or playbook_id not in PLAYBOOK_NUDGES:
        return False
    nudged = getattr(session, "playbook_nudged", None)
    if not isinstance(nudged, set):
        session.playbook_nudged = set()
        nudged = session.playbook_nudged
    if playbook_id in nudged:
        return False
    nudged.add(playbook_id)
    session.pending_playbook_id = playbook_id
    return True
