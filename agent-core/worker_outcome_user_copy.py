"""Conservative user-facing lines for project worker tool results (no internal paths)."""
from __future__ import annotations

from typing import Any

_PROVIDER_FAILURE_HINTS = {
    "provider_request_failed": "模型外发请求未完成；请检查网络、模型配置与 API 密钥后重试。",
    "outbound_request_denied": "外发模型请求未被批准，worker 未继续。",
    "confirmed_request_failed": "已批准的外发请求执行失败。",
    "response_model_mismatch": "模型返回与授权端点不一致。",
}


def _hint_from_scope_detail(detail: dict[str, Any] | None) -> str:
    if not detail or not isinstance(detail, dict):
        return ""
    hint = detail.get("hint")
    if isinstance(hint, str) and hint.strip():
        return hint.strip()
    operation = detail.get("operation")
    requested = detail.get("requested_path") or detail.get("path_redacted")
    match = detail.get("match_result")
    error = detail.get("error")
    parts: list[str] = []
    if isinstance(operation, str) and operation:
        parts.append(f"操作：{operation}")
    if isinstance(requested, str) and requested:
        parts.append(f"路径：{requested}")
    if isinstance(error, str) and error:
        parts.append(f"原因：{error}")
    elif isinstance(match, str) and match:
        parts.append(f"原因：{match}")
    return "；".join(parts)


def format_worker_tool_row_failure_notice(row: dict[str, Any]) -> str | None:
    """One line per failed worker row from run_project_workers tool details."""
    if not isinstance(row, dict):
        return None
    project_id = row.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        return None
    status = row.get("status")
    model_status = row.get("model_turn_status")
    failed = status in {"failed", "blocked", "cancelled"} or model_status in {"failed", "blocked", "cancelled"}
    if not failed:
        return None
    code = row.get("model_error_code") or row.get("error_code") or ""
    if not isinstance(code, str):
        code = ""
    error_type = row.get("model_error_type") or ""
    error_message = row.get("model_error_message") or ""
    if not isinstance(error_type, str):
        error_type = ""
    if not isinstance(error_message, str):
        error_message = ""
    scope_hint = ""
    if row.get("scope_error_available") and isinstance(row.get("scope_error_detail"), dict):
        scope_hint = _hint_from_scope_detail(row["scope_error_detail"])
    headline = _PROVIDER_FAILURE_HINTS.get(code, "")
    if not headline and code:
        headline = f"worker 结束状态：{code}"
    if error_message.strip() and code == "provider_request_failed":
        headline = f"{headline}（{error_message.strip()[:160]}）"
    elif error_type and not headline:
        headline = f"worker 异常：{error_type}"
    if scope_hint:
        headline = f"{headline}；{scope_hint}" if headline else scope_hint
    if not headline:
        headline = "worker 未成功完成，原因未能完整核对。"
    return f"项目 {project_id}：{headline}"


def format_worker_batch_failure_notice(rows: object) -> str | None:
    if not isinstance(rows, list):
        return None
    lines = [line for row in rows if (line := format_worker_tool_row_failure_notice(row))]
    if not lines:
        return None
    return "各项目 worker 结果摘要：\n" + "\n".join(f"· {line}" for line in lines)
