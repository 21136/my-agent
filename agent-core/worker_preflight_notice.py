"""User-safe notices for current-dialogue worker preflight failures."""
from __future__ import annotations

from typing import TypedDict


class WorkerPreflightReport(TypedDict):
    category: str
    message: str
    severe: bool


_SEVERE_NOTICES = {
    "worker_dispatch_submit_failed": "当前 worker 任务未能提交，暂时无法开始处理。",
    "agent_dispatch_pool_submit_failed": "当前 worker 调度未能提交，暂时无法开始处理。",
    "worker_run_start_record_failed": "当前 worker 启动记录未能保存，暂时无法开始处理。",
    "project_binding_unavailable": "当前项目连接暂不可用，worker 未能启动。",
}

_SCOPE_DENIED_NOTICE = "当前操作未获授权，worker 未启动。"
_UNKNOWN_NOTICE = "当前 worker 暂未能启动，具体原因尚未确认。"


def report_worker_preflight_error(error_code: object) -> WorkerPreflightReport:
    """Map a host error code without exposing the code or other internal details."""
    if isinstance(error_code, str) and error_code in _SEVERE_NOTICES:
        return {
            "category": "system_error",
            "message": _SEVERE_NOTICES[error_code],
            "severe": True,
        }
    if isinstance(error_code, str) and error_code == "scope_denied":
        return {
            "category": "authorization_denied",
            "message": _SCOPE_DENIED_NOTICE,
            "severe": False,
        }
    return {
        "category": "unknown",
        "message": _UNKNOWN_NOTICE,
        "severe": False,
    }


def format_worker_preflight_notice(error_code: object, project_display_name: object) -> str | None:
    """Format known severe errors, never guessing or echoing an unverifiable identity."""
    report = report_worker_preflight_error(error_code)
    if not report["severe"]:
        return None

    name = ""
    if type(project_display_name) is str:
        candidate = project_display_name.strip()
        if (candidate and len(candidate) <= 80
                and not any(char in candidate for char in "/\\")
                and not any(ord(char) < 32 or ord(char) == 127 for char in candidate)):
            name = candidate
    if name:
        return f"\u9879\u76ee\u300c{name}\u300d\uff1a{report['message']}"
    return f"\u67d0\u4e2a\u9879\u76ee\uff08\u540d\u79f0\u6682\u4e0d\u53ef\u786e\u8ba4\uff09\uff1a{report['message']}"
