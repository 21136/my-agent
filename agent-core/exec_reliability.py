"""Execution reliability helpers (EXEC-RELIABILITY / G14 · Phase 35).

M0: postcondition success-claim gate + call-fingerprint circuit breaker.
M1: failure class A–F + playbook nudges (P-npm-corrupt / P-sql-missing / P-port-dead).
"""

from __future__ import annotations

import json
import os
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


def segment_failure_budget() -> int:
    """Max countable failures per execute segment (AGENT-HARNESS P5)."""
    raw = os.environ.get(
        "MY_AGENT_SEGMENT_FAILURE_BUDGET",
        str(_DEFAULT_SEGMENT_FAILURE_BUDGET),
    )
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_SEGMENT_FAILURE_BUDGET


def record_segment_failure(session: Any) -> bool:
    """Bump segment-wide failure count. Return True if budget just reached."""
    count = int(getattr(session, "segment_failure_count", 0) or 0) + 1
    session.segment_failure_count = count
    if count >= segment_failure_budget() and not getattr(
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
