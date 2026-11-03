"""Opt-in live 0x567 check for one bounded two-project Agent worker batch."""
from __future__ import annotations

import ctypes
import json
import os
import re
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from llm_client import LLMClient, load_config
from llm_models import ModelRegistry
from paths import AgentPaths
from project_mode import create_project
from project_worker_host import ProjectWorkerHost, ProjectWorkerHostRequest
from server import WsBridge, _build_repl
from session import build_anchor_message, create_new
from tests.isolation_helpers import temporary_agent_paths

OPT_IN = "MY_AGENT_PROJECT_WORKERS_LIVE_TWO_PROJECTS_OPTIN"
MODEL_ID = "0x567-flash"
PROVIDER_MODEL = "gpt-6-luna"
MAIN_MODEL_ID = "0x567-pro"
MAIN_PROVIDER_MODEL = "gpt-6.1-terra"
ENDPOINT = "https://api-cdn.0x567.com/v1/chat/completions"
PARENT_RUN_ID = "synthetic-two-project-parent-run"
TURN_EPOCH = 1
PROJECTS = ("synthetic-alpha-842", "synthetic-beta-842")
OUTPUTS = {
    PROJECTS[0]: "SYNTHETIC_ALPHA_RESULT_842: fixed harmless content.\n",
    PROJECTS[1]: "SYNTHETIC_BETA_RESULT_842: fixed harmless content.\n",
}
TASKS = {
    project_id: (
        f"Use write_project_file exactly once to create only result.txt in {project_id}. "
        f"Set its full content exactly to {content!r}. Do not call any other tool."
    )
    for project_id, content in OUTPUTS.items()
}
_PRIVATE_MARKERS = ("PARENT_PRIVATE_SENTINEL_842", "F:\\my-agent-main", "C:\\Users\\")
NATURAL_REQUEST = (
    "开始执行一次严格限定的合成任务。请分别让 synthetic-alpha-842 与 synthetic-beta-842 "
    "各自独立处理以下任务；不要读取项目文件或会话，也不要触碰其他路径：\n"
    + "\n".join(f"{pid}: {TASKS[pid]}" for pid in PROJECTS)
)


def _safe_failure_summary(result, state: dict, approvals: dict[str, set[str]], *, secrets: tuple[str, ...] = ()) -> dict:
    """Expose only bounded identifiers and counters; never include payload/result text."""
    error = getattr(result, "error", None)
    details = getattr(error, "details", None)
    data = result.data if isinstance(getattr(result, "data", None), dict) else {}
    workers = data.get("workers")
    if not isinstance(workers, list) and isinstance(details, dict):
        workers = details.get("workers")
    workers = workers if isinstance(workers, list) else []
    safe_workers = []
    for row in workers:
        if not isinstance(row, dict):
            continue
        project_id = row.get("project_id")
        if project_id not in PROJECTS:
            project_id = "unknown"
        safe_workers.append({
            "project_id": project_id,
            "status": (row.get("status") if row.get("status") in {
                "blocked", "failed", "cancelled", "limit_reached", "model_turn_finished",
            } else "unknown"),
            "model_turn_status": (row.get("model_turn_status") if row.get("model_turn_status") in {
                "blocked", "failed", "cancelled", "limit_reached", "model_turn_finished",
            } else "unknown"),
            "requests": row.get("requests") if type(row.get("requests")) is int else 0,
        })
    error_code = _safe_diagnostic_text(getattr(error, "code", None), limit=100, secrets=secrets)
    error_message = _safe_diagnostic_text(getattr(error, "message", None), secrets=secrets)
    return {
        "ok": bool(getattr(result, "ok", False)),
        "error_code": error_code,
        "error_message": error_message,
        "request_count": state.get("requests", 0) if type(state.get("requests", 0)) is int else 0,
        "http_statuses": [status for status in state.get("http_statuses", []) if type(status) is int],
        "request_projects": sorted(project for project in state.get("projects", set()) if project in PROJECTS),
        "workers": safe_workers,
        "approvals": {kind: sorted(project for project in projects if project in PROJECTS)
                      for kind, projects in approvals.items()},
    }




_SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[^\s,;]+"),
    re.compile(r"(?i)\b(?:api[_ -]?key|credential|authorization)\b[^\n]{0,160}"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\b[A-Z]:\\[^\s\"']+"),
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_./+=-]{48,}(?![A-Za-z0-9])"),
)


def _safe_diagnostic_text(value: object, *, limit: int = 600, secrets: tuple[str, ...] = ()) -> str | None:
    if type(value) is not str:
        return None
    text = value[:limit]
    lowered = text.casefold()
    if any(secret and secret in text for secret in secrets):
        return "<redacted provider credential>"
    if any(marker.casefold() in lowered for marker in _PRIVATE_MARKERS):
        return "<redacted private marker>"
    if any(term in lowered for term in ("bearer", "credential", "api_key", "authorization")):
        return "<redacted sensitive diagnostic>"
    for pattern in _SENSITIVE_TEXT_PATTERNS:
        text = pattern.sub("<redacted>", text)
    return text


def _safe_worker_args(arguments: object) -> dict:
    """Capture bounded arguments while printing only known synthetic identifiers/text."""
    if type(arguments) is not dict or set(arguments) != {"workers"}:
        return {"shape": "unexpected", "worker_count": 0}
    workers = arguments.get("workers")
    if type(workers) is not list:
        return {"shape": "unexpected workers value", "worker_count": 0}
    safe_rows = []
    for row in workers[:25]:
        if type(row) is not dict:
            safe_rows.append({"shape": "non-object"})
            continue
        project_id = row.get("project_id")
        if project_id not in PROJECTS:
            safe_rows.append({"project_id": "unknown", "shape": "unexpected project"})
            continue
        task = row.get("task")
        safe_task = task if type(task) is str and task == TASKS[project_id] else None
        if safe_task is None:
            safe_task = "<withheld non-exact task>"
        safe_rows.append({
            "project_id": project_id,
            "task": safe_task,
            "read_targets": [] if row.get("read_targets") == [] else "<unexpected>",
            "write_targets": ["result.txt"] if row.get("write_targets") == ["result.txt"] else "<unexpected>",
            "unexpected_key_count": len(set(row) - {"project_id", "task", "read_targets", "write_targets"}),
        })
    return {"worker_count": len(workers), "workers": safe_rows}


def _safe_executor_tool_result(result: object, *, secrets: tuple[str, ...] = ()) -> dict:
    error = getattr(result, "error", None)
    code = getattr(error, "code", None)
    safe_code = _safe_diagnostic_text(code, limit=100) if type(code) is str else None
    message = _safe_diagnostic_text(getattr(error, "message", None), secrets=secrets)
    data = getattr(result, "data", None)
    rows = data.get("workers", []) if type(data) is dict else []
    safe_workers = []
    for row in rows[:25] if type(rows) is list else []:
        if type(row) is not dict:
            continue
        project_id = row.get("project_id")
        model_turn = row.get("model_turn")
        safe_workers.append({
            "project_id": project_id if project_id in PROJECTS else "unknown",
            "status": row.get("status") if row.get("status") in {
                "blocked", "failed", "cancelled", "limit_reached", "model_turn_finished"} else "unknown",
            "error_code": _safe_diagnostic_text(getattr(model_turn, "error_code", None), limit=100, secrets=secrets),
            "requests": min(5, max(0, getattr(model_turn, "requests", 0)))
                        if type(getattr(model_turn, "requests", 0)) is int else 0,
        })
    return {
        "ok": getattr(result, "ok", False) is True,
        "status": "ok" if getattr(result, "ok", False) is True else "error",
        "error_code": safe_code,
        "message": message,
        "workers": safe_workers,
    }



def _confirmation_category(preview: str) -> str:
    if "PROJECT_WORKER_UNLINKED_EXECUTION_CONFIRM" in preview:
        return "parent_unlinked"
    if "项目 worker 范围确认" in preview:
        return "worker_scope"
    if "即将向 0x567 gpt-6-luna 外发" in preview:
        return "worker_outbound"
    if "项目 worker 文件写入确认" in preview:
        return "worker_write"
    if "CURRENT_DIALOGUE_WORK_RECORD_BATCH_CONFIRM" in preview:
        return "work_record_batch"
    return "unknown"


def test_confirmation_categories_cover_all_known_worker_gates_and_unknown():
    assert _confirmation_category("PROJECT_WORKER_UNLINKED_EXECUTION_CONFIRM {}") == "parent_unlinked"
    assert _confirmation_category("项目 worker 范围确认：{}") == "worker_scope"
    assert _confirmation_category("即将向 0x567 gpt-6-luna 外发 {}") == "worker_outbound"
    assert _confirmation_category("项目 worker 文件写入确认：{}") == "worker_write"
    assert _confirmation_category("CURRENT_DIALOGUE_WORK_RECORD_BATCH_CONFIRM") == "work_record_batch"
    assert _confirmation_category("unrecognized preview") == "unknown"


_PARENT_UNLINKED_REASON_CODES = frozenset({
    "approved_bounded_synthetic_batch", "approve_all_not_allowed", "invalid_preview",
    "wrong_task_count", "project_set_mismatch", "task_scope_mismatch", "private_marker_present",
})


_TASK_SCOPE_DIAGNOSTIC_CODES = frozenset({
    "none", "read_targets_mismatch", "write_targets_mismatch", "invalid_task",
    "project_mismatch", "private_marker_present", "fixed_content_missing",
    "path_or_file_target", "read_intent", "write_intent_missing", "unavailable",
})


def _synthetic_task_scope_diagnostic(task: object, project_id: object) -> str:
    """Return a fixed enum while preserving project, payload, path, and read guards."""
    if type(task) is not str or not task or len(task) > 1200:
        return "invalid_task"
    if type(project_id) is not str or project_id not in PROJECTS:
        return "project_mismatch"
    folded_raw = task.casefold()
    if (_contains_private_marker(task)
            or any(marker.casefold() in folded_raw for marker in _PRIVATE_MARKERS)):
        return "private_marker_present"

    text = " ".join(task.split())
    folded = text.casefold()
    mentioned_projects = [candidate for candidate in PROJECTS if candidate in text]
    if mentioned_projects and mentioned_projects != [project_id]:
        return "project_mismatch"

    expected_content = OUTPUTS[project_id].rstrip("\n")
    content_forms = (repr(OUTPUTS[project_id]), expected_content)
    present_forms = [form for form in content_forms if form in text]
    if not present_forms:
        return "fixed_content_missing"
    content_form = max(present_forms, key=len)
    task_intent = text.replace(content_form, " ").replace(project_id, " ")
    if ("result.txt" not in folded
            or not re.search(r"(?i)\b(?:write|create|save|put)\b|写入|创建", text)):
        return "write_intent_missing" if "result.txt" in folded else "path_or_file_target"

    named_text_files = re.findall(r"(?i)(?<![\w.-])[\w.-]+\.(?:txt|md|py|json|ya?ml|csv|log)\b", text)
    if set(name.casefold() for name in named_text_files) != {"result.txt"}:
        return "path_or_file_target"
    path_text = text.replace(r"\n", "")
    if re.search(r"(?i)(?:[A-Z]:\\|\\\\|(?:^|\s)(?:\.{1,2}[\\/]|/)|\b[\w.-]+[\\/][\w.-]+)", path_text):
        return "path_or_file_target"
    read_verbs = list(re.finditer(
        r"(?i)\b(?:read|open|inspect|list|search|browse|scan)\b|读取|读|查看|打开|搜索|浏览|扫描",
        task_intent,
    ))
    for match in read_verbs:
        prefix = task_intent[max(0, match.start() - 16):match.start()]
        if not re.search(r"(?i)\b(?:do not|don't|never|without)\s+$|(?:不(?:要|得|可|必)|禁止|无需|勿)\s*$", prefix):
            return "read_intent"
    return "none"


def _parent_unlinked_task_scope_diagnostic(row: object) -> str:
    if type(row) is not dict:
        return "invalid_task"
    if row.get("read_targets") != []:
        return "read_targets_mismatch"
    if row.get("write_targets") != ["result.txt"]:
        return "write_targets_mismatch"
    return _synthetic_task_scope_diagnostic(row.get("task"), row.get("project_id"))


def _parent_unlinked_preview_scope_diagnostic(preview: object) -> str:
    try:
        doc = json.loads(preview[preview.find("{"):])
        tasks = doc.get("tasks")
    except Exception:
        return "unavailable"
    if type(tasks) is not list:
        return "unavailable"
    for row in tasks:
        diagnostic = _parent_unlinked_task_scope_diagnostic(row)
        if diagnostic != "none":
            return diagnostic
    return "none"


def _synthetic_task_project_id(task: object, project_id: object) -> str | None:
    """Accept bounded synthetic content; host fields, not prose, grant file authority."""
    return project_id if _synthetic_task_scope_diagnostic(task, project_id) == "none" else None


def _matches_approved_worker_task(project_id: object, task: object, approved_tasks: dict) -> bool:
    return (type(project_id) is str and type(approved_tasks) is dict
            and approved_tasks.get(project_id) == task
            and _synthetic_task_project_id(task, project_id) == project_id)


def _approved_worker_payload_project(payload: object, approved_tasks: dict) -> str | None:
    if type(payload) is not dict or type(approved_tasks) is not dict:
        return None
    messages = payload.get("messages")
    if type(messages) is not list:
        return None
    user_rows = [row.get("content") for row in messages
                 if type(row) is dict and row.get("role") == "user"]
    if len(user_rows) != 1:
        return None
    matches = [project_id for project_id in PROJECTS
               if _matches_approved_worker_task(project_id, user_rows[0], approved_tasks)]
    return matches[0] if len(matches) == 1 else None


def _approve_worker_outbound_document(
    doc: object,
    *,
    scope_approvals: dict[str, str],
    outbound_approvals: dict[str, dict],
    outbound_counts: dict[str, int],
    max_worker_requests: int,
    allow_all: bool,
) -> tuple[bool, str | None]:
    """Approve one exact worker preview and retain it as a one-shot payload grant."""
    if (allow_all is not False or type(doc) is not dict
            or type(max_worker_requests) is not int or not 1 <= max_worker_requests <= 2):
        return False, None
    try:
        payload = doc["provider_payload"]
        project_id = _approved_worker_payload_project(payload, scope_approvals)
        count = outbound_counts.get(project_id, 0) if project_id is not None else 0
        approved = (
            project_id in scope_approvals and project_id not in outbound_approvals
            and type(count) is int and count < max_worker_requests
            and doc.get("model_id") == MODEL_ID
            and doc.get("vendor") == "0x567"
            and doc.get("endpoint") == ENDPOINT
            and type(doc.get("headers")) is dict
            and doc["headers"].get("Authorization") == "[redacted]"
            and payload.get("model") == PROVIDER_MODEL
            and not _contains_private_marker(payload.get("messages"))
        )
    except Exception:
        return False, None
    if not approved:
        return False, project_id if project_id in PROJECTS else None
    outbound_approvals[project_id] = doc
    outbound_counts[project_id] = count + 1
    return True, project_id


def _parent_unlinked_confirmation_decision(preview: str, *, allow_all: bool) -> tuple[bool, str]:
    """Mirror the existing fail-closed fixture gate and return only an enum reason."""
    if allow_all is not False:
        return False, "approve_all_not_allowed"
    try:
        doc = json.loads(preview[preview.find("{"):])
        tasks = doc.get("tasks", [])
    except Exception:
        return False, "invalid_preview"
    if type(tasks) is not list or len(tasks) != len(PROJECTS):
        return False, "wrong_task_count"
    try:
        project_ids = {row.get("project_id") for row in tasks if type(row) is dict}
    except Exception:
        return False, "project_set_mismatch"
    if len(project_ids) != len(PROJECTS) or project_ids != set(PROJECTS):
        return False, "project_set_mismatch"
    if _contains_private_marker(doc):
        return False, "private_marker_present"
    if not all(
        type(row) is dict
        and row.get("read_targets") == []
        and row.get("write_targets") == ["result.txt"]
        and _synthetic_task_project_id(row.get("task"), row.get("project_id")) == row.get("project_id")
        for row in tasks
    ):
        return False, "task_scope_mismatch"
    return True, "approved_bounded_synthetic_batch"


@pytest.mark.parametrize(("case", "expected"), [
    ("exact", (True, "approved_bounded_synthetic_batch")),
    ("allow_all", (False, "approve_all_not_allowed")),
    ("wrong_count", (False, "wrong_task_count")),
    ("wrong_project", (False, "project_set_mismatch")),
    ("wrong_task", (False, "task_scope_mismatch")),
    ("private_marker", (False, "private_marker_present")),
    ("invalid_preview", (False, "invalid_preview")),
])
def test_offline_parent_unlinked_confirmation_uses_fixed_decision_reasons(case, expected):
    doc = {
        "reason": "synthetic offline reason",
        "tasks": [{
            "project_id": project_id, "task": TASKS[project_id],
            "read_targets": [], "write_targets": ["result.txt"],
        } for project_id in PROJECTS],
        "record_access": "none; do not read or write work-record sidecars",
        "record_effect": "unlinked; no task-to-requirement binding or lifecycle tracking",
        "execution_gates": ["scope", "outbound", "write"],
        "acceptance": "not_run",
    }
    preview = "PROJECT_WORKER_UNLINKED_EXECUTION_CONFIRM\n" + json.dumps(doc, ensure_ascii=False)
    if case == "allow_all":
        result = _parent_unlinked_confirmation_decision(preview, allow_all=True)
    elif case == "wrong_count":
        doc["tasks"].pop()
        result = _parent_unlinked_confirmation_decision(
            "PROJECT_WORKER_UNLINKED_EXECUTION_CONFIRM\n" + json.dumps(doc), allow_all=False,
        )
    elif case == "wrong_project":
        doc["tasks"][0]["project_id"] = "synthetic-other-842"
        result = _parent_unlinked_confirmation_decision(
            "PROJECT_WORKER_UNLINKED_EXECUTION_CONFIRM\n" + json.dumps(doc), allow_all=False,
        )
    elif case == "wrong_task":
        doc["tasks"][0]["task"] = doc["tasks"][0]["task"].replace(
            OUTPUTS[PROJECTS[0]].rstrip("\n"), "SYNTHETIC_ALPHA_RESULT_842: altered content."
        )
        result = _parent_unlinked_confirmation_decision(
            "PROJECT_WORKER_UNLINKED_EXECUTION_CONFIRM\n" + json.dumps(doc), allow_all=False,
        )
    elif case == "private_marker":
        doc["reason"] = "synthetic marker F:\\my-agent-main\\private"
        result = _parent_unlinked_confirmation_decision(
            "PROJECT_WORKER_UNLINKED_EXECUTION_CONFIRM\n" + json.dumps(doc), allow_all=False,
        )
    elif case == "invalid_preview":
        result = _parent_unlinked_confirmation_decision(
            "PROJECT_WORKER_UNLINKED_EXECUTION_CONFIRM not-json", allow_all=False,
        )
    else:
        result = _parent_unlinked_confirmation_decision(preview, allow_all=False)
    assert result == expected
    assert result[1] in _PARENT_UNLINKED_REASON_CODES


def test_offline_parent_unlinked_summary_emits_only_fixed_decision_fields():
    secret = "synthetic-private-task-text-do-not-print"
    diagnostics = _new_live_probe_diagnostics()
    diagnostics["parent_unlinked_decisions"] = [
        {"decision": "rejected", "reason": "task_scope_mismatch",
         "task_scope_diagnostic": "read_targets_mismatch", "task": secret},
        {"decision": secret, "reason": secret},
        {"decision": "rejected", "reason": "task_scope_mismatch",
         "task_scope_diagnostic": secret, "task": r"C:\Users\private\task.txt"},
    ]
    summary = _safe_live_probe_summary(diagnostics, RuntimeError("offline"))
    assert summary["parent_unlinked_decisions"] == [
        {"decision": "rejected", "reason": "task_scope_mismatch",
         "task_scope_diagnostic": "read_targets_mismatch"},
        {"decision": "rejected", "reason": "task_scope_mismatch"},
    ]
    rendered = json.dumps(summary)
    assert secret not in rendered and r"C:\Users\private\task.txt" not in rendered

@pytest.mark.parametrize(("case", "expected"), [
    ("read_targets", "read_targets_mismatch"),
    ("write_targets", "write_targets_mismatch"),
    ("missing_content", "fixed_content_missing"),
    ("project_mismatch", "project_mismatch"),
    ("non_ascii", "none"),
    ("wordlist", "none"),
    ("path", "path_or_file_target"),
    ("extra_file", "path_or_file_target"),
    ("read_intent", "read_intent"),
])
def test_offline_task_scope_mismatch_has_fixed_fine_grained_diagnostic(case, expected):
    project_id = PROJECTS[0]
    content = OUTPUTS[project_id].rstrip("\n")
    row = {
        "project_id": project_id,
        "task": f"Write exactly the following content to result.txt: {content}",
        "read_targets": [],
        "write_targets": ["result.txt"],
    }
    if case == "read_targets":
        row["read_targets"] = ["README.md"]
    elif case == "write_targets":
        row["write_targets"] = ["result.txt", "notes.txt"]
    elif case == "missing_content":
        row["task"] = "Write exactly to result.txt"
    elif case == "project_mismatch":
        row["task"] += f" in {PROJECTS[1]}"
    elif case == "non_ascii":
        row["task"] += "caf\u00e9"
    elif case == "wordlist":
        row["task"] += " then summarize"
    elif case == "path":
        row["task"] += " ../private.txt"
    elif case == "extra_file":
        row["task"] += " Also create notes.txt"
    elif case == "read_intent":
        row["task"] += " Read the project files"

    diagnostic = _parent_unlinked_task_scope_diagnostic(row)
    assert diagnostic == expected
    assert diagnostic in _TASK_SCOPE_DIAGNOSTIC_CODES
    preview = "PROJECT_WORKER_UNLINKED_EXECUTION_CONFIRM\n" + json.dumps({
        "tasks": [row, {
            "project_id": PROJECTS[1], "task": TASKS[PROJECTS[1]],
            "read_targets": [], "write_targets": ["result.txt"],
        }],
    })
    assert _parent_unlinked_preview_scope_diagnostic(preview) == expected
    expected_decision = (
        (True, "approved_bounded_synthetic_batch")
        if case in {"non_ascii", "wordlist"}
        else (False, "task_scope_mismatch")
    )
    assert _parent_unlinked_confirmation_decision(preview, allow_all=False) == expected_decision

def _provider_visible_messages(messages: list[dict]) -> list[dict]:
    # Use the same metadata projection as LLMClient before checking synthetic text.
    from llm_client import _provider_messages
    projected = _provider_messages(messages)
    if projected != messages:
        raise RuntimeError("synthetic guard rejected unprojected host metadata")
    return projected


_SAFE_PROBE_PHASES = {
    "preflight", "main_profile", "worker_profile", "credential", "fixture_setup",
    "project_seed", "session_setup", "agent_setup", "guard_armed", "pre_send_guard",
    "main_request_validated", "worker_request_validated", "main_http_send", "worker_http_send",
    "confirmation", "executor_tool", "agent_turn", "result_validation", "file_readback", "complete",
}
_SAFE_PROBE_ERROR_CODES = {
    "main_input_not_synthetic", "worker_input_not_synthetic", "worker_payload_not_approved",
    "worker_body_altered", "endpoint_not_allowlisted", "request_options_invalid",
    "headers_invalid", "payload_invalid", "model_not_allowlisted", "capture_not_armed",
    "request_limit_reached", "duplicate_project_request", "stream_method_invalid",
    "post_transport_error", "stream_transport_error", "stream_setup_error",
    "executor_tool_failed", "test_assertion_failed", "runtime_error", "timeout",
    "permission_error", "import_error", "os_error", "value_error", "type_error",
    "preflight_skipped", "unexpected_exception",
}
_SAFE_CONFIRMATION_CATEGORIES = (
    "parent_unlinked", "worker_scope", "worker_outbound", "worker_write", "work_record_batch", "unknown",
)
_SAFE_TOOL_ERROR_CODES = {"permission_denied", "validation_error", "confirm_rejected", "tool_not_found"}


def _new_live_probe_diagnostics() -> dict:
    return {
        "phase": "preflight", "failure_code": "", "transport_attempts": 0,
        "validated_requests": 0, "dispatch_starts": 0, "guard_rejections": 0,
        "main_requests": 0, "worker_requests": 0, "http_statuses": [],
        "confirmations": {category: 0 for category in _SAFE_CONFIRMATION_CATEGORIES},
        "parent_unlinked_decisions": [],
        "executor_tool_calls": 0, "executor_result": "unseen", "executor_error_code": "none",
        "main_tool_available": None,
    }


def _safe_count(value: object, limit: int = 1000) -> int:
    return min(limit, max(0, value)) if type(value) is int else 0


def _safe_live_probe_summary(diagnostics: dict, error: BaseException | None = None) -> dict:
    phase = diagnostics.get("phase")
    phase = phase if phase in _SAFE_PROBE_PHASES else "unknown"
    code = diagnostics.get("failure_code")
    if code not in _SAFE_PROBE_ERROR_CODES:
        error_name = type(error).__name__ if error is not None else ""
        code = {
            "Skipped": "preflight_skipped", "AssertionError": "test_assertion_failed",
            "Failed": "test_assertion_failed", "TimeoutError": "timeout",
            "PermissionError": "permission_error", "ImportError": "import_error",
            "OSError": "os_error", "ValueError": "value_error", "TypeError": "type_error",
            "RuntimeError": "runtime_error",
        }.get(error_name, "unexpected_exception" if error is not None else "none")
    statuses = diagnostics.get("http_statuses")
    statuses = [status for status in statuses if type(status) is int and 100 <= status <= 599][:5] if type(statuses) is list else []
    confirmations = diagnostics.get("confirmations")
    confirmations = {category: _safe_count(confirmations.get(category)) for category in _SAFE_CONFIRMATION_CATEGORIES} if type(confirmations) is dict else {category: 0 for category in _SAFE_CONFIRMATION_CATEGORIES}
    raw_parent_decisions = diagnostics.get("parent_unlinked_decisions")
    parent_decisions = []
    for row in raw_parent_decisions[:5] if type(raw_parent_decisions) is list else []:
        if type(row) is not dict:
            continue
        decision = row.get("decision")
        reason = row.get("reason")
        if (type(decision) is not str or decision not in {"approved", "rejected"}
                or type(reason) is not str or reason not in _PARENT_UNLINKED_REASON_CODES):
            continue
        task_scope_diagnostic = row.get("task_scope_diagnostic")
        safe_row = {"decision": decision, "reason": reason}
        if task_scope_diagnostic in _TASK_SCOPE_DIAGNOSTIC_CODES:
            safe_row["task_scope_diagnostic"] = task_scope_diagnostic
        parent_decisions.append(safe_row)
    executor_result = diagnostics.get("executor_result")
    executor_result = executor_result if executor_result in {"unseen", "ok", "error", "raised"} else "unknown"
    executor_code = diagnostics.get("executor_error_code")
    executor_code = executor_code if executor_code in _SAFE_TOOL_ERROR_CODES else ("other" if executor_code not in {None, "", "none"} else "none")
    main_tool = diagnostics.get("main_tool_available")
    return {
        "outcome": "skipped" if error is not None and type(error).__name__ == "Skipped" else "failed" if error is not None else "passed",
        "phase": phase, "error_code": code,
        "transport_attempts": _safe_count(diagnostics.get("transport_attempts")),
        "validated_requests": _safe_count(diagnostics.get("validated_requests"), 6),
        "dispatch_starts": _safe_count(diagnostics.get("dispatch_starts"), 6),
        "guard_rejections": _safe_count(diagnostics.get("guard_rejections")),
        "main_requests": _safe_count(diagnostics.get("main_requests"), 6),
        "worker_requests": _safe_count(diagnostics.get("worker_requests"), 6),
        "http_statuses": statuses, "confirmations": confirmations,
        "parent_unlinked_decisions": parent_decisions,
        "executor_tool_calls": _safe_count(diagnostics.get("executor_tool_calls"), 25),
        "executor_result": executor_result, "executor_error_code": executor_code,
        "main_tool_available": main_tool if type(main_tool) is bool else None,
    }


def _safe_live_failure_notice(diagnostics: dict, error: BaseException | None = None) -> str:
    # This renders only the strict summary; exception text and traceback are never inspected.
    return "SYNTHETIC_NATURAL_AGENT_FAILURE " + json.dumps(
        _safe_live_probe_summary(diagnostics, error), ensure_ascii=False, sort_keys=True,
    )


def _capture_live_probe(run, *, diagnostics: dict, discard_output, clear_logs) -> tuple[bool, object]:
    try:
        return True, run()
    except BaseException as error:
        try:
            discard_output()
        except BaseException:
            pass
        try:
            clear_logs()
        except BaseException:
            pass
        return False, _safe_live_probe_summary(diagnostics, error)

def _report_live_probe_failure(summary: dict) -> None:
    """Print the already-sanitized capture summary, then fail or skip without exception text."""
    print("SYNTHETIC_NATURAL_AGENT_FAILURE " + json.dumps(
        summary, ensure_ascii=False, sort_keys=True,
    ), flush=True)
    if summary.get("outcome") == "skipped":
        pytest.skip("synthetic live probe skipped; safe diagnostic printed above")
    pytest.fail("synthetic live probe failed; safe diagnostic printed above", pytrace=False)

def _request_payload(kwargs: dict) -> dict | None:
    payload = kwargs.get("json")
    if type(payload) is dict:
        return payload
    content = kwargs.get("content")
    try:
        if isinstance(content, bytes):
            payload = json.loads(content.decode("utf-8"))
        elif isinstance(content, str):
            payload = json.loads(content)
    except (UnicodeError, ValueError, TypeError):
        return None
    return payload if type(payload) is dict else None


def _make_live_request_guards(real_post, real_stream, *, state: dict, state_lock,
                              outbound_approvals: dict, both_requests_entered=None, diagnostics: dict | None = None,
                              expected_main_user_rows: list[str] | None = None,
                              expected_worker_tasks: dict[str, str] | None = None,
                              max_worker_requests_per_project: int = 1,
                              max_main_requests: int = 5,
                              consume_worker_approval: bool = False):
    """Wrap both HTTPX transports; validation and the total cap run before send."""
    def set_phase(value: str) -> None:
        if diagnostics is not None and value in _SAFE_PROBE_PHASES:
            diagnostics["phase"] = value

    def reject(code: str):
        safe_code = code if code in _SAFE_PROBE_ERROR_CODES else "unexpected_exception"
        with state_lock:
            state["guard_rejections"] = state.get("guard_rejections", 0) + 1
            if diagnostics is not None:
                diagnostics["guard_rejections"] = _safe_count(diagnostics.get("guard_rejections")) + 1
                diagnostics["failure_code"] = safe_code
                diagnostics["phase"] = "pre_send_guard"
        raise RuntimeError("synthetic live request rejected") from None

    def reserve(client, url, kwargs: dict):
        with state_lock:
            state["transport_attempts"] = state.get("transport_attempts", 0) + 1
            if diagnostics is not None:
                diagnostics["transport_attempts"] = _safe_count(diagnostics.get("transport_attempts")) + 1
                diagnostics["phase"] = "pre_send_guard"
        payload = _request_payload(kwargs)
        headers = kwargs.get("headers") or {}
        messages = payload.get("messages") if type(payload) is dict else None
        model = payload.get("model") if type(payload) is dict else None
        header_names = {str(name).casefold() for name in headers} if type(headers) is dict else set()
        if (type(max_worker_requests_per_project) is not int
                or not 1 <= max_worker_requests_per_project <= 2
                or type(max_main_requests) is not int or not 1 <= max_main_requests <= 5):
            reject("request_options_invalid")
        if not state.get("tool_result_capture_armed"):
            reject("capture_not_armed")
        if str(url) != ENDPOINT:
            reject("endpoint_not_allowlisted")
        if kwargs.get("params") not in (None, {}, []) or kwargs.get("auth") is not None:
            reject("request_options_invalid")
        if (header_names != {"content-type", "authorization"}
                or headers.get("Content-Type") != "application/json"
                or not str(headers.get("Authorization", "")).startswith("Bearer ")):
            reject("headers_invalid")
        if (kwargs.get("follow_redirects", False) is not False
                or getattr(client, "follow_redirects", False) is not False):
            reject("request_options_invalid")
        if type(payload) is not dict or type(messages) is not list or _contains_private_marker(messages):
            reject("payload_invalid")

        try:
            provider_messages = _provider_visible_messages(messages)
        except Exception:
            reject("payload_invalid")
        user_rows = [row.get("content") for row in provider_messages
                     if type(row) is dict and row.get("role") == "user"]
        project_id = None
        if model == MAIN_PROVIDER_MODEL:
            expected_rows = expected_main_user_rows if expected_main_user_rows is not None else [NATURAL_REQUEST]
            if (type(expected_rows) is not list or not expected_rows
                    or any(type(content) is not str for content in expected_rows)
                    or _contains_private_marker(expected_rows)):
                reject("main_input_not_synthetic")
            expected_provider_rows = [row["content"] for row in _provider_visible_messages(
                [{"role": "user", "content": content} for content in expected_rows]
            )]
            if user_rows != expected_provider_rows:
                reject("main_input_not_synthetic")
            tools = payload.get("tools", [])
            available = bool(type(tools) is list and any(
                type(tool) is dict and type(tool.get("function")) is dict
                and tool["function"].get("name") == "run_project_workers" for tool in tools
            ))
            state["main_tool_available"] = available
            if diagnostics is not None:
                diagnostics["main_tool_available"] = available
            model_id = MAIN_MODEL_ID
        elif model == PROVIDER_MODEL:
            worker_tasks = TASKS if expected_worker_tasks is None else expected_worker_tasks
            if (type(worker_tasks) is not dict or set(worker_tasks) != set(PROJECTS)
                    or any(type(task) is not str for task in worker_tasks.values())):
                reject("worker_input_not_synthetic")
            matching = [pid for pid in PROJECTS if user_rows == [worker_tasks[pid]]]
            if len(matching) != 1:
                reject("worker_input_not_synthetic")
            project_id = matching[0]
            approved = outbound_approvals.get(project_id)
            approved_payload = approved.get("provider_payload") if type(approved) is dict else None
            if type(approved_payload) is not dict or payload != approved_payload:
                reject("worker_payload_not_approved")
            if kwargs.get("content") is not None:
                expected_body = json.dumps(approved_payload, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":"), allow_nan=False).encode("utf-8")
                actual_body = kwargs["content"]
                actual_body = actual_body.encode("utf-8") if isinstance(actual_body, str) else actual_body
                if actual_body != expected_body:
                    reject("worker_body_altered")
            model_id = MODEL_ID
        else:
            reject("model_not_allowlisted")

        rejected = None
        with state_lock:
            limit = state.get("request_limit", 6)
            if type(limit) is not int or not 1 <= limit <= 6:
                rejected = "request_options_invalid"
            elif state["requests"] >= limit:
                state["over_cap_attempts"] = state.get("over_cap_attempts", 0) + 1
                rejected = "request_limit_reached"
            elif project_id is None and state.get("main_request_count", 0) >= max_main_requests:
                rejected = "request_limit_reached"
            elif project_id is not None and state.get("project_request_counts", {}).get(project_id, 0) >= max_worker_requests_per_project:
                rejected = "duplicate_project_request" if max_worker_requests_per_project == 1 else "request_limit_reached"
            elif (project_id is not None and consume_worker_approval
                  and outbound_approvals.get(project_id) != approved):
                rejected = "worker_payload_not_approved"
            else:
                state["requests"] += 1
                state.setdefault("request_models", []).append(model_id)
                state["validated_requests"] = state.get("validated_requests", 0) + 1
                if diagnostics is not None:
                    diagnostics["validated_requests"] = _safe_count(diagnostics.get("validated_requests")) + 1
                    diagnostics["main_requests" if project_id is None else "worker_requests"] += 1
                    diagnostics["phase"] = "main_request_validated" if project_id is None else "worker_request_validated"
                if project_id is None:
                    state["main_request_count"] = state.get("main_request_count", 0) + 1
                else:
                    state["projects"].add(project_id)
                    counts = state.setdefault("project_request_counts", {})
                    counts[project_id] = counts.get(project_id, 0) + 1
                    if consume_worker_approval:
                        outbound_approvals.pop(project_id, None)
                    state["active"] += 1
                    state["max_active"] = max(state["max_active"], state["active"])
        if rejected:
            reject(rejected)
        return project_id, model_id

    def start_dispatch(model_id: str) -> None:
        with state_lock:
            state["dispatch_starts"] = state.get("dispatch_starts", 0) + 1
            if diagnostics is not None:
                diagnostics["dispatch_starts"] = _safe_count(diagnostics.get("dispatch_starts")) + 1
                diagnostics["phase"] = "main_http_send" if model_id == MAIN_MODEL_ID else "worker_http_send"

    def append_status(response):
        status = getattr(response, "status_code", None)
        if type(status) is int:
            with state_lock:
                state["http_statuses"].append(status)
                if diagnostics is not None:
                    diagnostics["http_statuses"].append(status)

    def guarded_post(client, url, *args, **kwargs):
        project_id, model_id = reserve(client, url, kwargs)
        try:
            if project_id is not None and both_requests_entered is not None:
                both_requests_entered.wait(timeout=15)
            start_dispatch(model_id)
            response = real_post(client, url, *args, **kwargs)
            append_status(response)
            return response
        except Exception:
            if diagnostics is not None:
                diagnostics["failure_code"] = "post_transport_error"
                diagnostics["phase"] = "main_http_send" if model_id == MAIN_MODEL_ID else "worker_http_send"
            raise RuntimeError("synthetic live POST failed; details withheld") from None
        finally:
            if project_id is not None:
                with state_lock:
                    state["active"] -= 1

    class StatusContext:
        def __init__(self, context, project_id, model_id):
            self.context, self.project_id, self.model_id = context, project_id, model_id

        def __enter__(self):
            try:
                if self.project_id is not None and both_requests_entered is not None:
                    both_requests_entered.wait(timeout=15)
                start_dispatch(self.model_id)
                response = self.context.__enter__()
            except Exception:
                if diagnostics is not None:
                    diagnostics["failure_code"] = "stream_transport_error"
                    diagnostics["phase"] = "main_http_send" if self.model_id == MAIN_MODEL_ID else "worker_http_send"
                if self.project_id is not None:
                    with state_lock:
                        state["active"] -= 1
                raise RuntimeError("synthetic live stream failed; details withheld") from None
            append_status(response)
            return response

        def __exit__(self, exc_type, exc, tb):
            try:
                return self.context.__exit__(exc_type, exc, tb)
            finally:
                if self.project_id is not None:
                    with state_lock:
                        state["active"] -= 1

    def guarded_stream(client, method, url, *args, **kwargs):
        if method != "POST":
            reject("stream_method_invalid")
        project_id, model_id = reserve(client, url, kwargs)
        try:
            context = real_stream(client, method, url, *args, **kwargs)
        except Exception:
            if diagnostics is not None:
                diagnostics["failure_code"] = "stream_setup_error"
                diagnostics["phase"] = "main_http_send" if model_id == MAIN_MODEL_ID else "worker_http_send"
            if project_id is not None:
                with state_lock:
                    state["active"] -= 1
            raise RuntimeError("synthetic live stream setup failed; details withheld") from None
        return StatusContext(context, project_id, model_id)

    return guarded_post, guarded_stream


def _contains_private_marker(value: object) -> bool:
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return True

    def contains(value: object) -> bool:
        if type(value) is str:
            return any(marker in value for marker in _PRIVATE_MARKERS)
        if type(value) is dict:
            return any(contains(key) or contains(item) for key, item in value.items())
        if type(value) in (list, tuple):
            return any(contains(item) for item in value)
        return False

    return contains(value)

def _new_live_request_state() -> tuple[dict, list[int]]:
    response_statuses: list[int] = []
    state = {
        "requests": 0,
        "active": 0,
        "max_active": 0,
        "projects": set(),
        "http_statuses": response_statuses,
        "request_models": [],
        "over_cap_attempts": 0,
        "request_limit": 6,
        "main_request_count": 0,
        "tool_result_capture_armed": False,
        "transport_attempts": 0,
        "validated_requests": 0,
        "dispatch_starts": 0,
        "guard_rejections": 0,
    }
    return state, response_statuses


def _assert_worker_tool_result_budget(result: object, *, max_worker_requests: int) -> list[dict]:
    assert type(max_worker_requests) is int and max_worker_requests in {1, 2}
    data = getattr(result, "data", None)
    rows = data.get("workers") if type(data) is dict else None
    assert type(rows) is list
    expected_status = "limit_reached" if max_worker_requests == 1 else "model_turn_finished"
    assert all(
        type(row) is dict
        and row.get("status") == expected_status
        and row.get("model_turn_status") == expected_status
        and type(row.get("requests")) is int
        and 1 <= row["requests"] <= max_worker_requests
        for row in rows
    )
    return rows


def test_offline_worker_tool_result_uses_flat_serialized_budget_fields():
    from tools.schema import tool_ok

    rows = [{
        "project_id": project_id,
        "status": "model_turn_finished",
        "model_turn_status": "model_turn_finished",
        "requests": 2,
        "writes": [{"path": "result.txt", "status": "written", "verification": "content_match"}],
    } for project_id in PROJECTS]
    result = tool_ok("run_project_workers", {"workers": rows})

    assert result.to_dict()["data"]["workers"] == rows
    assert _assert_worker_tool_result_budget(result, max_worker_requests=2) == rows

    rows[0]["model_turn_status"] = "failed"
    with pytest.raises(AssertionError):
        _assert_worker_tool_result_budget(
            tool_ok("run_project_workers", {"workers": rows}), max_worker_requests=2,
        )


def test_live_request_state_constructs_status_list_before_state_binding():
    state, response_statuses = _new_live_request_state()

    assert state["http_statuses"] is response_statuses
    assert state["http_statuses"] == []
    assert state["requests"] == 0
    assert state["projects"] == set()


def test_safe_failure_summary_omits_model_and_request_content():
    from types import SimpleNamespace

    secret = "NEVER_EMIT_MODEL_OR_CREDENTIAL_CONTENT"
    result = SimpleNamespace(
        ok=False,
        data=None,
        error=SimpleNamespace(code=secret, details={"workers": [{
            "project_id": PROJECTS[0], "status": "failed", "model_turn_status": "failed",
            "requests": 1, "error_code": secret, "model_summary": secret,
            "writes": [{"path": secret}],
        }]}),
    )
    summary = _safe_failure_summary(
        result,
        {"requests": 1, "http_statuses": [503], "projects": {PROJECTS[0]}, "body": secret},
        {"scope": {PROJECTS[0]}, "outbound": {PROJECTS[0]}},
    )

    assert summary["request_count"] == 1
    assert summary["http_statuses"] == [503]
    assert summary["workers"] == [{
        "project_id": PROJECTS[0], "status": "failed", "model_turn_status": "failed",
        "requests": 1,
    }]
    assert secret not in json.dumps(summary)
def test_new_project_result_file_does_not_require_second_write_confirmation():
    from write_policy import write_requires_confirm

    needs_confirm, reason = write_requires_confirm(
        tool="write_text", path=f"workspace/{PROJECTS[0]}/result.txt",
        project_root=f"workspace/{PROJECTS[0]}", active_shell="project",
        on_conflict="overwrite", file_exists=False,
    )

    assert (needs_confirm, reason) == (False, "skip:project_new_file")


@contextmanager
def _two_request_mutex():
    """Fail closed unless this process exclusively owns the cross-process live-test cap."""
    if os.name != "nt":
        raise RuntimeError("live request cap requires the Windows named mutex")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.ReleaseMutex.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel32.CreateMutexW(None, False, "Local\\MyAgentProjectWorkersLiveFiveRequestCap842")
    if not handle:
        raise RuntimeError("could not establish live request-cap mutex")
    acquired = False
    try:
        wait_result = kernel32.WaitForSingleObject(handle, 0)
        if wait_result not in (0x00000000, 0x00000080):
            raise RuntimeError("live request-cap mutex is already held")
        acquired = True
        yield
    finally:
        if acquired:
            kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


def _profile(provider_paths: AgentPaths):
    provider = ModelRegistry.load(provider_paths).get(MODEL_ID)
    if provider is None or (provider.id, provider.vendor, provider.provider_model) != (
        MODEL_ID, "0x567", PROVIDER_MODEL
    ):
        pytest.fail("configured model profile mismatch; no request was sent", pytrace=False)
    endpoint = provider.chat_completions_url()
    parsed = urlsplit(endpoint)
    if (endpoint != ENDPOINT or parsed.scheme != "https" or parsed.hostname != "api-cdn.0x567.com"
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.port not in (None, 443)):
        pytest.fail("configured endpoint outside exact allowlist; no request was sent", pytrace=False)
    try:
        credential = provider.resolve_api_key(provider_paths)
    except Exception:
        pytest.fail("configured provider credential unavailable; no request was sent", pytrace=False)
    if not credential:
        pytest.skip("configured provider credential unavailable; no request was sent")
    return credential


def _run_live_agent_dispatches_two_project_workers(monkeypatch, diagnostics, *, max_worker_requests=1):
    # Do not acquire, print, persist, or return the real credential beyond process memory.
    diagnostics["phase"] = "main_profile"
    provider_paths = AgentPaths.discover(start=CORE)
    main_profile = ModelRegistry.load(provider_paths).get(MAIN_MODEL_ID)
    if (main_profile is None or (main_profile.id, main_profile.vendor, main_profile.provider_model) !=
            (MAIN_MODEL_ID, "0x567", MAIN_PROVIDER_MODEL)
            or main_profile.chat_completions_url() != ENDPOINT):
        pytest.fail("configured main profile is not exact gpt-6.1-terra; no request was sent", pytrace=False)
    diagnostics["phase"] = "worker_profile"
    credential = _profile(provider_paths)
    diagnostics["phase"] = "credential"
    with _two_request_mutex():
        monkeypatch.setenv("OX567_API_KEY", credential)
        secret_for_redaction = credential
        del credential

        diagnostics["phase"] = "fixture_setup"
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",),
                                   prefix="synthetic-two-live-workers-") as paths:
            diagnostics["phase"] = "project_seed"
            for project_id in PROJECTS:
                create_project(paths, project_id, project_template="light")
            roots = {project_id: paths.workspace / project_id for project_id in PROJECTS}
            originals = {
                project_id: {p.relative_to(root).as_posix(): p.read_bytes()
                             for p in root.rglob("*") if p.is_file()}
                for project_id, root in roots.items()
            }

            # Synthetic parent authority; no prior/private conversation content is provided.
            diagnostics["phase"] = "session_setup"
            parent = create_new(paths, conversation_id="20261101-c0ffee842")
            parent.meta.turn_mode = "agent"
            parent.meta.execution_model = MAIN_MODEL_ID
            parent.meta.harness = "desktop"
            parent.meta.active_shell = "project"
            parent.meta.project_id = PROJECTS[0]
            parent.meta.project_root = f"workspace/{PROJECTS[0]}"
            expected_main_user_rows = [build_anchor_message(parent)["content"], NATURAL_REQUEST]
            parent.save()

            isolated = ModelRegistry.load(paths).get(MODEL_ID)
            if isolated is None or (isolated.id, isolated.vendor, isolated.provider_model) != (
                MODEL_ID, "0x567", PROVIDER_MODEL
            ):
                pytest.fail("isolated worker model profile mismatch; no request was sent", pytrace=False)
            isolated_endpoint = isolated.chat_completions_url()
            parsed = urlsplit(isolated_endpoint)
            if (isolated_endpoint != ENDPOINT or parsed.scheme != "https"
                    or parsed.hostname != "api-cdn.0x567.com" or parsed.username or parsed.password
                    or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
                pytest.fail("isolated endpoint outside exact allowlist; no request was sent", pytrace=False)

            diagnostics["phase"] = "agent_setup"
            repl = _build_repl(parent, paths, WsBridge(emit=lambda _event: None, paths=paths),
                               work_record_controller_factory=lambda *_args: None)
            if diagnostics is not None:
                diagnostics["phase"] = "agent_setup"
            host = ProjectWorkerHost(lambda: LLMClient(load_config(), paths=paths))
            repl.agent.project_worker_host = host
            run_id, epoch = PARENT_RUN_ID, TURN_EPOCH
            repl.agent.bind_project_worker_live_context(
                run_id, epoch,
                lambda bound, active_run, active_epoch: (
                    bound is parent and active_run == run_id and active_epoch == epoch
                ),
            )

            # One real model turn per worker; the coordinator remains responsible for dispatch.
            real_run_many = host.run_many

            def worker_request_budget(*args, **kwargs):
                kwargs["max_requests"] = max_worker_requests
                return real_run_many(*args, **kwargs)

            host.run_many = worker_request_budget
            state_lock = threading.Lock()
            state, response_statuses = _new_live_request_state()
            state["request_models"] = []
            state["tool_result_capture_armed"] = False
            outbound_approvals: dict[str, dict] = {}
            outbound_counts = {project_id: 0 for project_id in PROJECTS}
            state["request_limit"] = 6
            approved_batch_tasks: dict[str, str] = {}
            scope_approvals: dict[str, str] = {}
            write_approvals: set[str] = set()
            both_requests_entered = threading.Barrier(2)
            real_post = httpx.Client.post
            real_stream = httpx.Client.stream
            confirmations = {
                category: {"count": 0, "approved": [], "rejected": []}
                for category in ("parent_unlinked", "worker_scope", "worker_outbound",
                                 "worker_write", "work_record_batch", "unknown")
            }

            def record_confirmation(category: str, approved: bool, project_id: str | None = None) -> str:
                row = confirmations[category]
                diagnostics["phase"] = "confirmation"
                diagnostics["confirmations"][category] = _safe_count(diagnostics["confirmations"].get(category)) + 1
                row["count"] += 1
                label = project_id if project_id in PROJECTS else (
                    "two-project-batch" if category == "parent_unlinked" and approved else "")
                row["approved" if approved else "rejected"].append(label or "no-project")
                return "y" if approved else "n"

            def confirm(preview: str, allow_all: bool = False) -> str:
                category = _confirmation_category(preview)
                project_id = None
                approved = allow_all is False
                if category == "parent_unlinked":
                    approved, reason = _parent_unlinked_confirmation_decision(preview, allow_all=allow_all)
                    diagnostics["parent_unlinked_decisions"].append({
                        "decision": "approved" if approved else "rejected", "reason": reason,
                        "task_scope_diagnostic": _parent_unlinked_preview_scope_diagnostic(preview),
                    })
                    if approved:
                        doc = json.loads(preview[preview.find("{"):])
                        approved_batch_tasks.update({row["project_id"]: row["task"] for row in doc["tasks"]})
                    return record_confirmation(category, approved)
                if category == "worker_scope":
                    try:
                        doc = json.loads(preview.split("项目 worker 范围确认：\n", 1)[1])
                        project_id = doc.get("project_id")
                        approved = (approved and project_id in PROJECTS
                                    and project_id not in scope_approvals
                                    and _matches_approved_worker_task(
                                        project_id, doc.get("task"), approved_batch_tasks,
                                    )
                                    and doc.get("read_targets") == []
                                    and doc.get("write_targets") == ["result.txt"]
                                    and doc.get("parent_run_id") == PARENT_RUN_ID
                                    and doc.get("turn_epoch") == TURN_EPOCH
                                    and not _contains_private_marker(doc))
                    except Exception:
                        approved = False
                    if approved:
                        scope_approvals[project_id] = doc["task"]
                    return record_confirmation(category, approved, project_id)
                if category == "worker_outbound":
                    try:
                        doc = json.loads(preview.split("外发以下精确请求：\n", 1)[1])
                        approved, project_id = _approve_worker_outbound_document(
                            doc,
                            scope_approvals=scope_approvals,
                            outbound_approvals=outbound_approvals,
                            outbound_counts=outbound_counts,
                            max_worker_requests=max_worker_requests,
                            allow_all=allow_all,
                        )
                    except Exception:
                        approved, project_id = False, None
                    return record_confirmation(category, approved, project_id)
                if category == "worker_write":
                    project_id = next((pid for pid in PROJECTS if pid in preview), None)
                    approved = (approved and project_id in scope_approvals
                                and project_id not in write_approvals
                                and "result.txt" in preview and not _contains_private_marker(preview))
                    if approved:
                        write_approvals.add(project_id)
                    return record_confirmation(category, approved, project_id)
                # Storage/record confirmation and all unknown prompts fail closed.
                return record_confirmation(category, False)

            repl.agent.executor.confirm_fn = confirm

            guarded_post, guarded_stream = _make_live_request_guards(
                real_post, real_stream, state=state, state_lock=state_lock,
                outbound_approvals=outbound_approvals,
                both_requests_entered=both_requests_entered, diagnostics=diagnostics,
                expected_main_user_rows=expected_main_user_rows,
                expected_worker_tasks=scope_approvals,
                max_worker_requests_per_project=max_worker_requests,
                max_main_requests=2,
                consume_worker_approval=True,
            )

            observed = {"calls": []}
            original_executor_run = repl.agent.executor.run

            def capture_executor_result(tool_name, arguments=None):
                captured = None
                if tool_name == "run_project_workers":
                    diagnostics["phase"] = "executor_tool"
                    diagnostics["executor_tool_calls"] = _safe_count(diagnostics.get("executor_tool_calls")) + 1
                    captured = {"arguments": _safe_worker_args(arguments), "object": None, "result": None}
                    observed["calls"].append(captured)
                try:
                    result = original_executor_run(tool_name, arguments)
                except Exception as exc:
                    if captured is not None:
                        captured["result"] = {"status": "raised"}
                        diagnostics["executor_result"] = "raised"
                    raise
                if captured is not None:
                    captured["object"] = result
                    captured["result"] = _safe_executor_tool_result(result, secrets=(secret_for_redaction,))
                    diagnostics["executor_result"] = "ok" if result.ok else "error"
                    error_code = getattr(getattr(result, "error", None), "code", None)
                    diagnostics["executor_error_code"] = error_code if error_code in _SAFE_TOOL_ERROR_CODES else ("other" if error_code else "none")
                return result

            repl.agent.executor.run = capture_executor_result
            repl.agent.tool_loop_max = 3
            state["tool_result_capture_armed"] = True
            diagnostics["phase"] = "guard_armed"
            monkeypatch.setattr(httpx.Client, "post", guarded_post)
            monkeypatch.setattr(httpx.Client, "stream", guarded_stream)
            diagnostics["phase"] = "agent_turn"
            turn = repl.agent.run_turn(NATURAL_REQUEST, spawn_explore=False, force_skip_plan_spawn=True)
            diagnostics["phase"] = "result_validation"
            captured_calls = observed["calls"]
            result = captured_calls[-1]["object"] if captured_calls else None
            diagnostic = {
                "main_tool_available": state.get("main_tool_available", False),
                "tool_rounds": turn.tool_rounds,
                "request_count": state["requests"], "request_models": state["request_models"],
                "over_cap_attempts": state["over_cap_attempts"],
                "http_statuses": response_statuses,
                "confirmations": confirmations,
                "tool_calls": [{"arguments": call["arguments"], "executor_tool_result": call["result"]}
                               for call in captured_calls],
            }
            if result is None:
                pytest.fail("natural-language Agent did not return run_project_workers ToolResult; diagnostic printed above",
                            pytrace=False)
            rows = result.data.get("workers", []) if isinstance(getattr(result, "data", None), dict) else []
            if not result.ok or len(rows) != 2:
                summary = _safe_failure_summary(
                    result, state,
                    {"scope": scope_approvals,
                     "outbound": {pid for pid, count in outbound_counts.items() if count},
                     "write": write_approvals}, secrets=(secret_for_redaction,),
                )
                pytest.fail(
                    "real Agent worker-batch dispatch did not return exactly two outcomes; "
                    f"safe_summary={json.dumps(summary, ensure_ascii=False, sort_keys=True)}",
                    pytrace=False,
                )
            assert {row.get("project_id") for row in rows} == set(PROJECTS)
            assert all(row.get("parent_run_id") == PARENT_RUN_ID and row.get("turn_epoch") == TURN_EPOCH
                       for row in rows), "worker outcomes did not share the bound parent run"
            assert len({row.get("worker_run_id") for row in rows}) == 2
            assert len({row.get("worker_id") for row in rows}) == 2
            expected_status = "limit_reached" if max_worker_requests == 1 else "model_turn_finished"
            assert all(row.get("status") == expected_status for row in rows), (
                "worker model turn did not match the separately selected request budget"
            )
            _assert_worker_tool_result_budget(result, max_worker_requests=max_worker_requests)
            assert all(row.get("acceptance_status") == "not_run" for row in rows)
            diagnostics["phase"] = "file_readback"
            for row in rows:
                project_id = row["project_id"]
                writes = row.get("writes", [])
                assert len(writes) == 1 and writes[0].get("status") == "written"
                assert writes[0].get("path") == "result.txt"
                assert writes[0].get("verification") == "content_match"
                assert (roots[project_id] / "result.txt").read_text(encoding="utf-8") == OUTPUTS[project_id]
                actual = {p.relative_to(roots[project_id]).as_posix(): p.read_bytes()
                          for p in roots[project_id].rglob("*") if p.is_file()}
                assert set(actual) == set(originals[project_id]) | {"result.txt"}
                assert all(actual[name] == data for name, data in originals[project_id].items())
            expected_worker_requests = len(PROJECTS) * max_worker_requests
            expected_total_requests = 2 + expected_worker_requests
            assert state["requests"] == expected_total_requests <= 6
            assert state["main_request_count"] == 2 and state["over_cap_attempts"] == 0
            assert state["request_models"][0] == state["request_models"][-1] == MAIN_MODEL_ID
            assert state["request_models"][1:-1] == [MODEL_ID] * expected_worker_requests
            assert state["request_models"].count(MODEL_ID) == expected_worker_requests
            assert state["project_request_counts"] == {
                project_id: max_worker_requests for project_id in PROJECTS
            }
            assert state["projects"] == set(PROJECTS)
            assert state["max_active"] == 2, "coordinator did not run both projects concurrently"
            assert len(response_statuses) == state["requests"] and all(status == 200 for status in response_statuses)
            assert set(scope_approvals) == set(PROJECTS)
            assert outbound_counts == {project_id: max_worker_requests for project_id in PROJECTS}
            assert outbound_approvals == {}
            assert confirmations["worker_outbound"]["count"] == expected_worker_requests
            assert sorted(confirmations["worker_outbound"]["approved"]) == sorted(
                PROJECTS * max_worker_requests
            )
            assert confirmations["worker_outbound"]["rejected"] == []
            # Safe creation of a new in-project result.txt is explicitly exempt from
            # a second write prompt by write_requires_confirm; scope + outbound gates
            # still require one approval for each project above.
            assert write_approvals == set()
            diagnostics["phase"] = "complete"
            return diagnostic


@pytest.mark.skipif(os.getenv(OPT_IN) != "1", reason="explicit two-project synthetic live-model opt-in")
def test_live_agent_dispatches_two_project_workers_in_one_bounded_batch(monkeypatch, capfd, caplog):
    diagnostics = _new_live_probe_diagnostics()
    ok, result = _capture_live_probe(
        lambda: _run_live_agent_dispatches_two_project_workers(monkeypatch, diagnostics),
        diagnostics=diagnostics, discard_output=capfd.readouterr, clear_logs=caplog.clear,
    )
    if not ok:
        _report_live_probe_failure(result)
    # Discard incidental output/logs; emit only a strict whitelist summary.
    capfd.readouterr()
    caplog.clear()
    print("SYNTHETIC_NATURAL_AGENT_DIAGNOSTIC " + json.dumps(
        _safe_live_probe_summary(diagnostics), ensure_ascii=False, sort_keys=True,
    ))


DIRECT_RUN_MANY_OPT_IN = "MY_AGENT_PROJECT_WORKERS_DIRECT_RUN_MANY_TWO_PROJECTS_OPTIN"
DIRECT_TASKS = {
    project_id: (
        f'Use write_project_file exactly once with path=result.txt and content exactly {OUTPUTS[project_id]!r}. '
        "Do not read files or use any other tool. Then finish."
    )
    for project_id in PROJECTS
}


def _run_direct_live_two_project_rounds(monkeypatch, diagnostics=None):
    """Real Flash-only host acceptance; the test-level opt-in is deliberately separate."""
    if diagnostics is not None:
        diagnostics["phase"] = "worker_profile"
    provider_paths = AgentPaths.discover(start=CORE)
    credential = _profile(provider_paths)
    if diagnostics is not None:
        diagnostics["phase"] = "credential"
    with _two_request_mutex():
        monkeypatch.setenv("OX567_API_KEY", credential)
        del credential
        if diagnostics is not None:
            diagnostics["phase"] = "fixture_setup"
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",),
                                   prefix="synthetic-direct-run-many-") as paths:
            if diagnostics is not None:
                diagnostics["phase"] = "project_seed"
            for project_id in PROJECTS:
                create_project(paths, project_id, project_template="light")
            roots = {project_id: paths.workspace / project_id for project_id in PROJECTS}
            originals = {
                project_id: {item.relative_to(root).as_posix(): item.read_bytes()
                             for item in root.rglob("*") if item.is_file()}
                for project_id, root in roots.items()
            }
            if diagnostics is not None:
                diagnostics["phase"] = "session_setup"
            parent = create_new(paths, conversation_id="20261101-d1ec8420")
            parent._ordinary_work_intent = "execute"
            parent.meta.turn_mode = "agent"
            parent.meta.execution_model = MODEL_ID
            parent.meta.harness = "desktop"
            parent.meta.active_shell = "project"
            parent.meta.project_id = PROJECTS[0]
            parent.meta.project_root = f"workspace/{PROJECTS[0]}"
            parent.save()

            requests = [ProjectWorkerHostRequest(
                project_id=project_id,
                task_id=f"synthetic-direct-{index}",
                task=DIRECT_TASKS[project_id],
                read_targets=(),
                write_targets=("result.txt",),
            ) for index, project_id in enumerate(PROJECTS, start=1)]
            host = ProjectWorkerHost(lambda: LLMClient(load_config(), paths=paths))
            run_id, epoch = PARENT_RUN_ID, TURN_EPOCH
            live_binding_check = lambda bound, active_run, active_epoch: (
                bound is parent and active_run == run_id and active_epoch == epoch
            )
            state_lock = threading.Lock()
            state, statuses = _new_live_request_state()
            state.update(request_models=[], request_limit=4, tool_result_capture_armed=True)
            approvals: dict[str, dict] = {}
            scope_approvals: set[str] = set()
            outbound_counts = {project_id: 0 for project_id in PROJECTS}
            write_confirmations = []

            def approve_scope(preview: str) -> str:
                if diagnostics is not None:
                    diagnostics["phase"] = "confirmation"
                    diagnostics["confirmations"]["worker_scope"] += 1
                try:
                    doc = json.loads(preview)
                    project_id = doc.get("project_id")
                    approved = (
                        project_id in PROJECTS and project_id not in scope_approvals
                        and doc.get("task") == DIRECT_TASKS[project_id]
                        and doc.get("read_targets") == []
                        and doc.get("write_targets") == ["result.txt"]
                        and doc.get("parent_run_id") == run_id
                        and doc.get("turn_epoch") == epoch
                        and not _contains_private_marker(doc)
                    )
                except Exception:
                    return "n"
                if not approved:
                    return "n"
                scope_approvals.add(project_id)
                return "y"

            def approve_outbound(preview: str) -> str:
                if diagnostics is not None:
                    diagnostics["phase"] = "confirmation"
                    diagnostics["confirmations"]["worker_outbound"] += 1
                try:
                    doc = json.loads(preview)
                    payload = doc["provider_payload"]
                    user_rows = [row.get("content") for row in payload.get("messages", [])
                                 if type(row) is dict and row.get("role") == "user"]
                    matches = [project_id for project_id in PROJECTS
                               if user_rows == [DIRECT_TASKS[project_id]]]
                    project_id = matches[0] if len(matches) == 1 else None
                    with state_lock:
                        already_sent = state.get("project_request_counts", {}).get(project_id, 0)
                    approved = (
                        project_id in scope_approvals and project_id is not None
                        and already_sent < 2 and outbound_counts[project_id] < 2
                        and project_id not in approvals
                        and doc.get("model_id") == MODEL_ID and doc.get("vendor") == "0x567"
                        and doc.get("endpoint") == ENDPOINT
                        and doc.get("headers", {}).get("Authorization") == "[redacted]"
                        and payload.get("model") == PROVIDER_MODEL
                        and not _contains_private_marker(payload.get("messages"))
                    )
                except Exception:
                    return "n"
                if not approved:
                    return "n"
                approvals[project_id] = doc
                outbound_counts[project_id] += 1
                return "y"

            def reject_write_confirmation(preview: str, allow_all: bool) -> str:
                write_confirmations.append((preview, allow_all))
                return "n"

            guarded_post, guarded_stream = _make_live_request_guards(
                httpx.Client.post, httpx.Client.stream,
                state=state, state_lock=state_lock, outbound_approvals=approvals,
                diagnostics=diagnostics, expected_worker_tasks=DIRECT_TASKS,
                max_worker_requests_per_project=2, consume_worker_approval=True,
            )
            monkeypatch.setattr(httpx.Client, "post", guarded_post)
            monkeypatch.setattr(httpx.Client, "stream", guarded_stream)
            if diagnostics is not None:
                diagnostics["phase"] = "guard_armed"
                diagnostics["phase"] = "agent_turn"
            outcomes = host.run_many(
                parent, requests,
                scope_confirm_fn=approve_scope,
                outbound_request_confirm_fn=approve_outbound,
                write_confirm_fn=reject_write_confirmation,
                cancel_event=threading.Event(),
                parent_run_id=run_id,
                turn_epoch=epoch,
                live_binding_check=live_binding_check,
                max_requests=2,
            )

            if diagnostics is not None:
                diagnostics["phase"] = "result_validation"
            if len(outcomes) != 2 or {row.project_id for row in outcomes} != set(PROJECTS):
                pytest.fail("direct run_many did not return exactly the two synthetic project outcomes", pytrace=False)
            if diagnostics is not None:
                diagnostics["phase"] = "file_readback"
            for outcome in outcomes:
                if (outcome.status != "model_turn_finished" or outcome.model_turn is None
                        or outcome.model_turn.status != "model_turn_finished"
                        or outcome.model_turn.requests != 2 or outcome.model_turn.tool_calls != 1
                        or outcome.acceptance_status != "not_run"):
                    pytest.fail("a direct worker did not finish its bounded two-request round", pytrace=False)
                writes = outcome.model_turn.writes
                if (len(writes) != 1 or writes[0].status != "written"
                        or writes[0].verification != "content_match"
                        or writes[0].readback_succeeded is not True):
                    pytest.fail("a direct worker write did not pass host read-back verification", pytrace=False)
                project_id = outcome.project_id
                result_path = roots[project_id] / "result.txt"
                if not result_path.is_file() or result_path.read_text(encoding="utf-8") != OUTPUTS[project_id]:
                    pytest.fail("synthetic result read-back differed from its fixed expected content", pytrace=False)
                actual = {item.relative_to(roots[project_id]).as_posix(): item.read_bytes()
                          for item in roots[project_id].rglob("*") if item.is_file()}
                if (set(actual) != set(originals[project_id]) | {"result.txt"}
                        or any(actual[name] != content for name, content in originals[project_id].items())):
                    pytest.fail("worker changed a file outside its exact synthetic result target", pytrace=False)

            if (state["requests"] != 4 or state["over_cap_attempts"] != 0
                    or state["request_models"] != [MODEL_ID] * 4
                    or state["projects"] != set(PROJECTS)
                    or state["project_request_counts"] != {project_id: 2 for project_id in PROJECTS}
                    or len(statuses) != 4 or any(status != 200 for status in statuses)
                    or scope_approvals != set(PROJECTS)
                    or outbound_counts != {project_id: 2 for project_id in PROJECTS}
                    or approvals or write_confirmations):
                pytest.fail("direct run_many exceeded an allowlist, confirmation, or four-request invariant", pytrace=False)
            if diagnostics is not None:
                diagnostics["phase"] = "complete"
            return {"worker_count": 2, "flash_requests": 4, "requests_per_worker": 2,
                    "writes_verified": 2, "http_statuses": list(statuses)}


@pytest.mark.skipif(os.getenv(DIRECT_RUN_MANY_OPT_IN) != "1",
                    reason="separate explicit direct ProjectWorkerHost.run_many live opt-in")
def test_live_direct_run_many_finishes_two_synthetic_project_rounds(monkeypatch, capfd, caplog):
    diagnostics = _new_live_probe_diagnostics()
    ok, result = _capture_live_probe(
        lambda: _run_direct_live_two_project_rounds(monkeypatch, diagnostics),
        diagnostics=diagnostics, discard_output=capfd.readouterr, clear_logs=caplog.clear,
    )
    capfd.readouterr()
    caplog.clear()
    if not ok:
        print("SYNTHETIC_DIRECT_RUN_MANY_FAILURE " + json.dumps(result, ensure_ascii=False, sort_keys=True))
        if result.get("outcome") == "skipped":
            pytest.skip("direct run_many live probe skipped; safe summary printed above")
        pytest.fail("direct run_many live probe failed; safe summary printed above", pytrace=False)
    print("SYNTHETIC_DIRECT_RUN_MANY_DIAGNOSTIC " + json.dumps(result, ensure_ascii=False, sort_keys=True))


COMPLETE_ROUND_OPT_IN = "MY_AGENT_PROJECT_WORKERS_LIVE_TWO_PROJECTS_COMPLETE_OPTIN"


@pytest.mark.skipif(os.getenv(COMPLETE_ROUND_OPT_IN) != "1", reason="separate explicit complete-worker-round opt-in")
def test_live_agent_finishes_two_project_worker_rounds_within_two_requests(monkeypatch, capfd, caplog):
    diagnostics = _new_live_probe_diagnostics()
    ok, result = _capture_live_probe(
        lambda: _run_live_agent_dispatches_two_project_workers(
            monkeypatch, diagnostics, max_worker_requests=2,
        ),
        diagnostics=diagnostics, discard_output=capfd.readouterr, clear_logs=caplog.clear,
    )
    if not ok:
        _report_live_probe_failure(result)
    capfd.readouterr()
    caplog.clear()
    print("SYNTHETIC_NATURAL_AGENT_DIAGNOSTIC " + json.dumps(
        _safe_live_probe_summary(diagnostics), ensure_ascii=False, sort_keys=True,
    ))

def test_offline_live_failure_report_prints_only_captured_whitelist_and_preserves_outcome(capsys):
    fake_header = "Authorization: Bearer offline-secret-value-842"
    diagnostics = _new_live_probe_diagnostics()
    diagnostics["phase"] = "pre_send_guard"
    cleanup = []
    ok, summary = _capture_live_probe(
        lambda: (_ for _ in ()).throw(RuntimeError(fake_header)),
        diagnostics=diagnostics,
        discard_output=lambda: cleanup.append("stdout"),
        clear_logs=lambda: cleanup.append("logs"),
    )

    assert not ok and summary["outcome"] == "failed"
    assert cleanup == ["stdout", "logs"]
    with pytest.raises(pytest.fail.Exception, match="synthetic live probe failed; safe diagnostic printed above"):
        _report_live_probe_failure(summary)

    captured = capsys.readouterr()
    assert captured.err == ""
    label, rendered = captured.out.strip().split(" ", 1)
    assert label == "SYNTHETIC_NATURAL_AGENT_FAILURE"
    diagnostic = json.loads(rendered)
    assert diagnostic == summary and diagnostic["outcome"] == "failed"
    assert set(diagnostic) == {
        "outcome", "phase", "error_code", "transport_attempts", "validated_requests",
        "dispatch_starts", "guard_rejections", "main_requests", "worker_requests",
        "http_statuses", "confirmations", "parent_unlinked_decisions", "executor_tool_calls", "executor_result",
        "executor_error_code", "main_tool_available",
    }
    assert fake_header not in captured.out and "offline-secret-value-842" not in captured.out


def test_offline_live_failure_report_keeps_skip_distinct(capsys):
    diagnostics = _new_live_probe_diagnostics()
    ok, summary = _capture_live_probe(
        lambda: pytest.skip("synthetic preflight skip"),
        diagnostics=diagnostics, discard_output=lambda: None, clear_logs=lambda: None,
    )

    assert not ok and summary["outcome"] == "skipped"
    with pytest.raises(pytest.skip.Exception):
        _report_live_probe_failure(summary)
    printed = capsys.readouterr().out
    assert json.loads(printed.split(" ", 1)[1]) == summary
    assert '"outcome": "skipped"' in printed

def test_offline_live_failure_capture_discards_header_and_secret_output():
    fake_header = "Authorization: Bearer offline-secret-value-842"
    emitted = [fake_header]
    log_records = [fake_header]
    diagnostics = _new_live_probe_diagnostics()
    diagnostics["phase"] = "pre_send_guard"
    ok, summary = _capture_live_probe(
        lambda: (_ for _ in ()).throw(RuntimeError(fake_header)),
        diagnostics=diagnostics,
        discard_output=lambda: emitted.clear(), clear_logs=lambda: log_records.clear(),
    )
    assert not ok
    assert emitted == [] and log_records == []
    assert summary["phase"] == "pre_send_guard"
    rendered = json.dumps(summary, ensure_ascii=False)
    assert fake_header not in rendered and "offline-secret-value-842" not in rendered
    assert "SYNTHETIC_NATURAL_AGENT_FAILURE" not in rendered


def test_offline_failure_summary_keeps_allowlisted_phase_error_and_counts_only():
    fake_header = "Authorization: Bearer offline-secret-value-842"
    diagnostics = _new_live_probe_diagnostics()
    diagnostics.update({
        "phase": "pre_send_guard", "transport_attempts": 1,
        "guard_rejections": 1, "failure_code": "main_input_not_synthetic",
    })
    ok, summary = _capture_live_probe(
        lambda: (_ for _ in ()).throw(RuntimeError(fake_header)),
        diagnostics=diagnostics,
        discard_output=lambda: None, clear_logs=lambda: None,
    )
    assert not ok
    assert summary["phase"] == "pre_send_guard"
    assert summary["error_code"] == "main_input_not_synthetic"
    assert summary["transport_attempts"] == 1
    assert summary["dispatch_starts"] == 0
    rendered = json.dumps(summary, ensure_ascii=False)
    assert fake_header not in rendered and "offline-secret-value-842" not in rendered
    assert set(summary) == {
        "outcome", "phase", "error_code", "transport_attempts", "validated_requests",
        "dispatch_starts", "guard_rejections", "main_requests", "worker_requests",
        "http_statuses", "confirmations", "parent_unlinked_decisions", "executor_tool_calls", "executor_result",
        "executor_error_code", "main_tool_available",
    }


def test_safe_live_failure_notice_never_echoes_headers_or_credentials():
    fake_header = "Authorization: Bearer offline-secret-value-842"
    diagnostics = _new_live_probe_diagnostics()
    notice = _safe_live_failure_notice(diagnostics, RuntimeError(fake_header))
    assert "Authorization" not in notice and "offline-secret-value-842" not in notice
    parsed = json.loads(notice.split(" ", 1)[1])
    assert parsed["phase"] == "preflight" and parsed["error_code"] == "runtime_error"


def _offline_first_agent_provider_messages(monkeypatch) -> tuple[list[dict], list[str], dict]:
    """Run a real synthetic Agent turn while HTTPX is replaced by a bounded fake."""
    fake_key = "synthetic-offline-transport-key-842"
    captured: list[dict] = []
    transport = {"attempts": 0, "max_attempts": 5, "endpoint_ok": True, "credential_ok": True}

    def fake_post(_client, url, *args, **kwargs):
        transport["attempts"] += 1
        if transport["attempts"] > transport["max_attempts"]:
            raise RuntimeError("offline synthetic transport cap exceeded")
        transport["endpoint_ok"] &= str(url) == ENDPOINT
        headers = kwargs.get("headers") or {}
        transport["credential_ok"] &= headers.get("Authorization") == f"Bearer {fake_key}"
        payload = kwargs.get("json")
        if type(payload) is dict:
            captured.append(payload)
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "SYNTHETIC_OFFLINE_ACK"},
                         "finish_reason": "stop"}],
            "model": MAIN_PROVIDER_MODEL,
        })

    def forbidden_stream(*_args, **_kwargs):
        raise RuntimeError("unexpected stream transport in offline first-request fixture")

    monkeypatch.setenv("OX567_API_KEY", fake_key)
    monkeypatch.setattr(httpx.Client, "stream", forbidden_stream)
    with temporary_agent_paths(prefix="synthetic-first-request-") as paths:
        parent = create_new(paths, conversation_id="20261101-first-request-synthetic")
        parent.meta.turn_mode = "agent"
        parent.meta.execution_model = MAIN_MODEL_ID
        parent.meta.harness = "desktop"
        parent.meta.active_shell = "project"
        parent.meta.project_id = PROJECTS[0]
        parent.meta.project_root = f"workspace/{PROJECTS[0]}"
        parent.save()
        expected_users = [build_anchor_message(parent)["content"], NATURAL_REQUEST]
        state, _ = _new_live_request_state()
        state["tool_result_capture_armed"] = True
        guarded_post, guarded_stream = _make_live_request_guards(
            fake_post, forbidden_stream, state=state, state_lock=threading.Lock(),
            outbound_approvals={}, expected_main_user_rows=expected_users,
        )
        monkeypatch.setattr(httpx.Client, "post", guarded_post)
        monkeypatch.setattr(httpx.Client, "stream", guarded_stream)
        repl = _build_repl(parent, paths, WsBridge(emit=lambda _event: None, paths=paths),
                           work_record_controller_factory=lambda *_args: None)
        repl.agent.stream_handlers = None
        repl.agent.run_turn(NATURAL_REQUEST, spawn_explore=False, force_skip_plan_spawn=True)

    return (captured[0]["messages"] if len(captured) == 1 else []), expected_users, {
        "attempts": transport["attempts"],
        "endpoint_ok": bool(transport["endpoint_ok"]),
        "credential_ok": bool(transport["credential_ok"]),
        "within_cap": transport["attempts"] <= transport["max_attempts"],
        "payload_count": len(captured),
        "model_ok": len(captured) == 1 and captured[0].get("model") == MAIN_PROVIDER_MODEL,
        "guard_requests": state["requests"],
        "guard_rejections": state["guard_rejections"],
    }


def test_offline_real_agent_first_provider_projection_is_synthetic_and_bounded(monkeypatch):
    from llm_client import HOST_INTERNAL_METADATA_KEY

    messages, expected_users, transport = _offline_first_agent_provider_messages(monkeypatch)
    user_rows = [row for row in messages if type(row) is dict and row.get("role") == "user"]
    contents = [row.get("content") for row in user_rows]
    assert transport == {
        "attempts": 1, "endpoint_ok": True, "credential_ok": True, "within_cap": True,
        "payload_count": 1, "model_ok": True, "guard_requests": 1, "guard_rejections": 0,
    }
    assert contents == expected_users and contents[-1] == NATURAL_REQUEST
    assert len(contents) == 2 and contents[0].startswith("[本次会议上下文]\n")
    assert all(HOST_INTERNAL_METADATA_KEY not in row for row in messages if type(row) is dict)

def test_main_synthetic_check_uses_provider_messages_projection():
    from llm_client import HOST_INTERNAL_METADATA_KEY, _provider_messages

    private_metadata = {"not_for_provider": "offline-private-test-marker"}
    messages = [
        {"role": "system", "content": "synthetic system", HOST_INTERNAL_METADATA_KEY: private_metadata},
        {"role": "user", "content": NATURAL_REQUEST, HOST_INTERNAL_METADATA_KEY: private_metadata},
    ]
    projected = _provider_messages(messages)
    assert _provider_visible_messages(projected) == projected
    assert [row["content"] for row in projected if row["role"] == "user"] == [NATURAL_REQUEST]
    assert all(HOST_INTERNAL_METADATA_KEY not in row for row in projected)


def test_safe_worker_args_keeps_only_bounded_synthetic_parameters():
    safe = _safe_worker_args({"workers": [
        {"project_id": PROJECTS[0], "task": TASKS[PROJECTS[0]],
         "read_targets": [], "write_targets": ["result.txt"]},
        {"project_id": PROJECTS[1], "task": TASKS[PROJECTS[1]],
         "read_targets": [], "write_targets": ["result.txt"]},
    ]})
    assert safe["worker_count"] == 2
    assert [row["project_id"] for row in safe["workers"]] == list(PROJECTS)
    assert [row["task"] for row in safe["workers"]] == [TASKS[pid] for pid in PROJECTS]


def test_executor_tool_result_diagnostic_redacts_secret_like_content():
    from types import SimpleNamespace

    secret = "Bearer abcdefghijklmnopqrstuvwxyz0123456789"
    result = SimpleNamespace(
        ok=False,
        error=SimpleNamespace(code="fixture_error", message=f"synthetic failure {secret}"),
        data={"workers": [{
            "project_id": PROJECTS[0], "status": "failed",
            "model_turn": SimpleNamespace(error_code="fixture_error", requests=1),
        }]},
    )
    exact_secret = "credential_value_without_a_common_prefix"
    result.error.message = f"synthetic failure {exact_secret}"
    rendered = json.dumps(_safe_executor_tool_result(result, secrets=(exact_secret,)), ensure_ascii=False)
    assert exact_secret not in rendered
    assert secret not in rendered
    assert "fixture_error" in rendered and "redacted" in rendered


def test_shared_post_and_stream_request_guard_uses_fake_transport_and_one_total_cap():
    from contextlib import nullcontext
    from types import SimpleNamespace

    sent = []
    response = SimpleNamespace(status_code=200)
    state, _ = _new_live_request_state()
    state.update(request_models=[], over_cap_attempts=0, tool_result_capture_armed=True, request_limit=5)
    lock = threading.Lock()

    def fake_post(_client, _url, *args, **kwargs):
        sent.append(("post", kwargs.get("json")))
        return response

    def fake_stream(_client, _method, _url, *args, **kwargs):
        sent.append(("stream", kwargs.get("json")))
        return nullcontext(response)

    guarded_post, guarded_stream = _make_live_request_guards(
        fake_post, fake_stream, state=state, state_lock=lock,
        outbound_approvals={}, both_requests_entered=None,
    )
    client = SimpleNamespace(follow_redirects=False)
    headers = {"Content-Type": "application/json", "Authorization": "Bearer synthetic-test-only"}
    payload = {"model": MAIN_PROVIDER_MODEL, "messages": [{"role": "user", "content": NATURAL_REQUEST}]}
    guarded_post(client, ENDPOINT, json=payload, headers=headers, follow_redirects=False)
    with guarded_stream(client, "POST", ENDPOINT, json=payload, headers=headers,
                        follow_redirects=False):
        pass
    for _ in range(3):
        guarded_post(client, ENDPOINT, json=payload, headers=headers, follow_redirects=False)
    with pytest.raises(RuntimeError, match="synthetic live request rejected"):
        with guarded_stream(client, "POST", ENDPOINT, json=payload, headers=headers,
                            follow_redirects=False):
            pass
    assert [kind for kind, _ in sent] == ["post", "stream", "post", "post", "post"]
    assert state["requests"] == 5 and state["over_cap_attempts"] == 1
    assert state["http_statuses"] == [200] * 5


def test_worker_request_guard_requires_exact_scope_approved_payload_on_both_transports():
    from contextlib import nullcontext
    from types import SimpleNamespace

    sent = []
    response = SimpleNamespace(status_code=200)
    client = SimpleNamespace(follow_redirects=False)
    headers = {"Content-Type": "application/json", "Authorization": "Bearer synthetic-test-only"}
    project_id = PROJECTS[0]
    payload = {"model": PROVIDER_MODEL, "messages": [{"role": "user", "content": TASKS[project_id]}]}
    lock = threading.Lock()

    def make_guards(state, approvals):
        return _make_live_request_guards(
            lambda *_a, **_kw: sent.append("post") or response,
            lambda *_a, **_kw: sent.append("stream") or nullcontext(response),
            state=state, state_lock=lock, outbound_approvals=approvals,
        )

    state, _ = _new_live_request_state()
    state["tool_result_capture_armed"] = True
    guarded_post, guarded_stream = make_guards(state, {})
    for send in (
        lambda: guarded_post(client, ENDPOINT, json=payload, headers=headers, follow_redirects=False),
        lambda: guarded_stream(client, "POST", ENDPOINT, json=payload, headers=headers, follow_redirects=False),
    ):
        with pytest.raises(RuntimeError, match="synthetic live request rejected"):
            send()
    assert sent == [] and state["requests"] == 0

    approvals = {project_id: {"provider_payload": payload}}
    for transport in ("post", "stream"):
        state, _ = _new_live_request_state()
        state["tool_result_capture_armed"] = True
        guarded_post, guarded_stream = make_guards(state, approvals)
        if transport == "post":
            guarded_post(client, ENDPOINT, json=payload, headers=headers, follow_redirects=False)
        else:
            with guarded_stream(client, "POST", ENDPOINT, json=payload, headers=headers,
                                follow_redirects=False):
                pass
        assert state["requests"] == 1 and state["projects"] == {project_id}
    assert sent == ["post", "stream"]


def test_shared_request_guard_rejects_non_synthetic_before_either_transport():
    from contextlib import nullcontext
    from types import SimpleNamespace

    sent = []
    response = SimpleNamespace(status_code=200)
    state, _ = _new_live_request_state()
    state.update(request_models=[], over_cap_attempts=0, tool_result_capture_armed=True, request_limit=5)
    lock = threading.Lock()
    guarded_post, guarded_stream = _make_live_request_guards(
        lambda *_a, **_kw: sent.append("post") or response,
        lambda *_a, **_kw: sent.append("stream") or nullcontext(response),
        state=state, state_lock=lock, outbound_approvals={}, both_requests_entered=None,
    )
    client = SimpleNamespace(follow_redirects=False)
    fake_secret = "offline-secret-value-842"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {fake_secret}"}
    payload = {"model": MAIN_PROVIDER_MODEL, "messages": [{"role": "user", "content": "real user data"}]}
    for send in (
        lambda: guarded_post(client, ENDPOINT, json=payload, headers=headers, follow_redirects=False),
        lambda: guarded_stream(client, "POST", ENDPOINT, json=payload, headers=headers, follow_redirects=False),
    ):
        with pytest.raises(RuntimeError, match="synthetic") as caught:
            send()
        assert "Authorization" not in str(caught.value)
        assert fake_secret not in str(caught.value)
    assert sent == [] and state["requests"] == 0



def test_worker_request_guard_consumes_each_approval_and_caps_two_rounds_per_project():
    from types import SimpleNamespace

    sent = []
    response = SimpleNamespace(status_code=200)
    project_id = PROJECTS[0]
    payload = {"model": PROVIDER_MODEL, "messages": [{"role": "user", "content": TASKS[project_id]}]}
    approvals = {project_id: {"provider_payload": payload}}
    state, _ = _new_live_request_state()
    state.update(tool_result_capture_armed=True, request_limit=4)
    guarded_post, _ = _make_live_request_guards(
        lambda *_a, **_kw: sent.append("post") or response,
        lambda *_a, **_kw: None,
        state=state, state_lock=threading.Lock(), outbound_approvals=approvals,
        max_worker_requests_per_project=2, consume_worker_approval=True,
    )
    client = type("Client", (), {"follow_redirects": False})()
    headers = {"Content-Type": "application/json", "Authorization": "Bearer synthetic-test-only"}
    send = lambda: guarded_post(client, ENDPOINT, json=payload, headers=headers, follow_redirects=False)

    send()
    assert project_id not in approvals
    with pytest.raises(RuntimeError, match="synthetic live request rejected"):
        send()
    approvals[project_id] = {"provider_payload": payload}
    send()
    approvals[project_id] = {"provider_payload": payload}
    with pytest.raises(RuntimeError, match="synthetic live request rejected"):
        send()

    assert sent == ["post", "post"]
    assert state["requests"] == 2
    assert state["project_request_counts"] == {project_id: 2}
    assert state["over_cap_attempts"] == 0

@pytest.mark.parametrize("max_worker_requests", [1, 2])
def test_offline_live_request_guard_preserves_one_round_and_caps_two_main_four_workers(max_worker_requests):
    from types import SimpleNamespace

    sent = []
    response = SimpleNamespace(status_code=200)
    state, _ = _new_live_request_state()
    state.update(request_limit=6, tool_result_capture_armed=True)
    approvals = {}
    outbound_counts = {project_id: 0 for project_id in PROJECTS}
    client = SimpleNamespace(follow_redirects=False)
    headers = {"Content-Type": "application/json", "Authorization": "Bearer synthetic-only"}
    guarded_post, _ = _make_live_request_guards(
        lambda _client, _url, **_kwargs: sent.append(_kwargs["json"]) or response,
        lambda *_args, **_kwargs: None,
        state=state,
        state_lock=threading.Lock(),
        outbound_approvals=approvals,
        expected_main_user_rows=[NATURAL_REQUEST],
        expected_worker_tasks=TASKS,
        max_worker_requests_per_project=max_worker_requests,
        max_main_requests=2,
        consume_worker_approval=True,
    )

    main_payload = {"model": MAIN_PROVIDER_MODEL,
                    "messages": [{"role": "user", "content": NATURAL_REQUEST}]}
    guarded_post(client, ENDPOINT, json=main_payload, headers=headers, follow_redirects=False)
    for round_index in range(max_worker_requests):
        for project_id in PROJECTS:
            messages = [{"role": "user", "content": TASKS[project_id]}]
            if round_index:
                messages.extend([
                    {"role": "assistant", "content": None, "tool_calls": []},
                    {"role": "tool", "tool_call_id": f"offline-{project_id}", "content": "ok"},
                ])
            payload = {"model": PROVIDER_MODEL, "messages": messages}
            preview_doc = {
                "provider_payload": payload,
                "model_id": MODEL_ID,
                "vendor": "0x567",
                "endpoint": ENDPOINT,
                "headers": {"Authorization": "[redacted]"},
            }
            approval_args = {
                "scope_approvals": TASKS,
                "outbound_approvals": approvals,
                "outbound_counts": outbound_counts,
                "max_worker_requests": max_worker_requests,
                "allow_all": False,
            }
            assert _approve_worker_outbound_document(preview_doc, **approval_args) == (True, project_id)
            assert _approve_worker_outbound_document(preview_doc, **approval_args) == (False, project_id)
            guarded_post(client, ENDPOINT, json=payload, headers=headers, follow_redirects=False)
            assert project_id not in approvals
    guarded_post(client, ENDPOINT, json=main_payload, headers=headers, follow_redirects=False)

    expected_worker_requests = len(PROJECTS) * max_worker_requests
    expected_total_requests = 2 + expected_worker_requests
    assert state["requests"] == expected_total_requests <= 6
    assert state["main_request_count"] == 2
    assert state["project_request_counts"] == {project_id: max_worker_requests for project_id in PROJECTS}
    assert outbound_counts == {project_id: max_worker_requests for project_id in PROJECTS}
    assert approvals == {}
    assert state["request_models"] == (
        [MAIN_MODEL_ID] + [MODEL_ID] * expected_worker_requests + [MAIN_MODEL_ID]
    )
    assert len(sent) == expected_total_requests and state["over_cap_attempts"] == 0

    over_budget_doc = {
        "provider_payload": {"model": PROVIDER_MODEL,
                             "messages": [{"role": "user", "content": TASKS[PROJECTS[0]]}]},
        "model_id": MODEL_ID, "vendor": "0x567", "endpoint": ENDPOINT,
        "headers": {"Authorization": "[redacted]"},
    }
    assert _approve_worker_outbound_document(
        over_budget_doc, scope_approvals=TASKS, outbound_approvals=approvals,
        outbound_counts=outbound_counts, max_worker_requests=max_worker_requests, allow_all=False,
    ) == (False, PROJECTS[0])
    with pytest.raises(RuntimeError, match="synthetic live request rejected"):
        guarded_post(client, ENDPOINT, json=main_payload, headers=headers, follow_redirects=False)
    assert len(sent) == expected_total_requests and state["requests"] == expected_total_requests
    assert state["guard_rejections"] == 1
    assert state["over_cap_attempts"] == (1 if expected_total_requests == 6 else 0)




def test_offline_direct_run_many_fixture_uses_host_callback_shapes_and_outcome_tuple(monkeypatch):
    """Exercise the same direct run_many fixture entirely through a synthetic transport."""
    assert all("write_project_file" in DIRECT_TASKS[project_id] for project_id in PROJECTS)

    import llm_client
    from llm_client import LLMConfig

    real_httpx_client = httpx.Client
    posts_by_project = {project_id: 0 for project_id in PROJECTS}
    lock = threading.Lock()

    def handler(request):
        body = json.loads(request.content.decode("utf-8"))
        messages = body.get("messages", [])
        system = messages[0].get("content", "") if messages else ""
        project_id = next((pid for pid in PROJECTS if f"project_id is '{pid}'" in system), None)
        user_rows = [row.get("content") for row in messages
                     if type(row) is dict and row.get("role") == "user"]
        if (project_id not in PROJECTS or body.get("model") != PROVIDER_MODEL
                or user_rows != [DIRECT_TASKS[project_id]] or _contains_private_marker(messages)):
            raise AssertionError("offline host sent a non-synthetic or out-of-scope request")
        with lock:
            round_index = posts_by_project[project_id]
            posts_by_project[project_id] += 1
        if round_index == 0:
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"offline-write-{project_id}", "type": "function",
                "function": {"name": "write_project_file", "arguments": json.dumps({
                    "path": "result.txt", "content": OUTPUTS[project_id],
                })},
            }]}
            finish_reason = "tool_calls"
        elif round_index == 1:
            message = {"role": "assistant", "content": "Synthetic task completed.", "tool_calls": []}
            finish_reason = "stop"
        else:
            raise AssertionError("offline worker exceeded its two-request budget")
        return httpx.Response(200, request=request, json={
            "model": PROVIDER_MODEL,
            "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
        })

    monkeypatch.setenv("OX567_API_KEY", "offline-only-synthetic-key")
    monkeypatch.setenv("OX567_BASE_URL", "https://api-cdn.0x567.com")
    monkeypatch.setenv("OX567_MODEL_FLASH", PROVIDER_MODEL)
    monkeypatch.setattr(llm_client.httpx, "Client", lambda **kwargs: real_httpx_client(
        transport=httpx.MockTransport(handler), **kwargs,
    ))

    with temporary_agent_paths(copy_tool_dirs=("common/write_text",), prefix="offline-direct-run-many-") as paths:
        for project_id in PROJECTS:
            create_project(paths, project_id, project_template="light")
        parent = create_new(paths, conversation_id="20261101-offline842")
        parent._ordinary_work_intent = "execute"
        parent.meta.turn_mode = "agent"
        parent.meta.execution_model = MODEL_ID
        parent.meta.harness = "desktop"
        parent.meta.active_shell = "project"
        parent.meta.project_id = PROJECTS[0]
        parent.meta.project_root = f"workspace/{PROJECTS[0]}"
        parent.save()
        host = ProjectWorkerHost(lambda: LLMClient(LLMConfig(
            api_key=None, base_url="https://unused.example", model=MODEL_ID,
            model_coding=MAIN_MODEL_ID, timeout_sec=3, context_limit_override=None,
        ), paths=paths))
        requests = [ProjectWorkerHostRequest(
            project_id=project_id, task_id=f"offline-direct-{index}",
            task=DIRECT_TASKS[project_id], read_targets=(), write_targets=("result.txt",),
        ) for index, project_id in enumerate(PROJECTS, start=1)]
        scopes = []
        outbounds = []

        def approve_scope(preview):
            doc = json.loads(preview)
            scopes.append(doc)
            return "y" if (
                doc.get("project_id") in PROJECTS
                and doc.get("task") == DIRECT_TASKS[doc["project_id"]]
                and doc.get("read_targets") == []
                and doc.get("write_targets") == ["result.txt"]
                and doc.get("parent_run_id") == PARENT_RUN_ID
                and doc.get("turn_epoch") == TURN_EPOCH
                and not _contains_private_marker(doc)
            ) else "n"

        def approve_outbound(preview):
            doc = json.loads(preview)
            payload = doc.get("provider_payload", {})
            user_rows = [row.get("content") for row in payload.get("messages", [])
                         if type(row) is dict and row.get("role") == "user"]
            project_id = next((pid for pid in PROJECTS if user_rows == [DIRECT_TASKS[pid]]), None)
            approved = (
                project_id is not None and doc.get("model_id") == MODEL_ID
                and doc.get("vendor") == "0x567" and doc.get("endpoint") == ENDPOINT
                and doc.get("headers", {}).get("Authorization") == "[redacted]"
                and payload.get("model") == PROVIDER_MODEL
                and not _contains_private_marker(payload.get("messages"))
            )
            if approved:
                outbounds.append(project_id)
            return "y" if approved else "n"

        outcomes = host.run_many(
            parent, requests,
            scope_confirm_fn=approve_scope,
            outbound_request_confirm_fn=approve_outbound,
            write_confirm_fn=lambda _preview, _allow: "n",
            cancel_event=threading.Event(),
            parent_run_id=PARENT_RUN_ID,
            turn_epoch=TURN_EPOCH,
            live_binding_check=lambda bound, run_id, epoch: (
                bound is parent and run_id == PARENT_RUN_ID and epoch == TURN_EPOCH
            ),
            max_requests=2,
        )
        assert type(outcomes) is tuple and len(outcomes) == 2
        assert {row.project_id for row in outcomes} == set(PROJECTS)
        assert all(row.status == "model_turn_finished" and row.model_turn is not None
                   and row.model_turn.status == "model_turn_finished"
                   and row.model_turn.requests == 2 and row.model_turn.tool_calls == 1
                   for row in outcomes)
        assert all(row.acceptance_status == "not_run" for row in outcomes)
        assert posts_by_project == {project_id: 2 for project_id in PROJECTS}
        assert {doc["project_id"] for doc in scopes} == set(PROJECTS)
        assert sorted(outbounds) == sorted(PROJECTS * 2)
        for project_id in PROJECTS:
            assert (paths.workspace / project_id / "result.txt").read_text(encoding="utf-8") == OUTPUTS[project_id]

def test_offline_direct_request_guard_uses_direct_task_allowlist_for_both_projects():
    from types import SimpleNamespace

    sent = []
    response = SimpleNamespace(status_code=200)
    state, _ = _new_live_request_state()
    state.update(tool_result_capture_armed=True, request_limit=4)
    guarded_post, _ = _make_live_request_guards(
        lambda *_args, **_kwargs: sent.append("post") or response,
        lambda *_args, **_kwargs: None,
        state=state, state_lock=threading.Lock(),
        outbound_approvals={project_id: {"provider_payload": {
            "model": PROVIDER_MODEL,
            "messages": [{"role": "user", "content": DIRECT_TASKS[project_id]}],
        }} for project_id in PROJECTS},
        expected_worker_tasks=DIRECT_TASKS,
    )
    client = type("Client", (), {"follow_redirects": False})()
    headers = {"Content-Type": "application/json", "Authorization": "Bearer offline-only"}
    for project_id in PROJECTS:
        payload = {"model": PROVIDER_MODEL,
                   "messages": [{"role": "user", "content": DIRECT_TASKS[project_id]}]}
        guarded_post(client, ENDPOINT, json=payload, headers=headers, follow_redirects=False)
    assert sent == ["post", "post"]
    assert state["requests"] == 2 and state["projects"] == set(PROJECTS)

def test_offline_synthetic_batch_accepts_unlisted_and_non_ascii_wording_without_widening_scope():
    tasks = {
        PROJECTS[0]: (
            f"Write exactly {OUTPUTS[PROJECTS[0]].rstrip()} to result.txt; "
            "summarize the synthetic payload using alternate wording."
        ),
        PROJECTS[1]: (
            f"\u8bf7\u5c06\u56fa\u5b9a\u5408\u6210\u5185\u5bb9 {OUTPUTS[PROJECTS[1]].rstrip()} "
            "\u5199\u5165 result.txt\uff1b\u6309\u7ed3\u6784\u5316\u8303\u56f4\u5b8c\u6210\u5373\u53ef\u3002"
        ),
    }
    rows = [{
        "project_id": project_id,
        "task": tasks[project_id],
        "read_targets": [],
        "write_targets": ["result.txt"],
    } for project_id in PROJECTS]
    preview = "PROJECT_WORKER_UNLINKED_EXECUTION_CONFIRM\n" + json.dumps({"tasks": rows}, ensure_ascii=False)

    assert _parent_unlinked_confirmation_decision(preview, allow_all=False) == (
        True, "approved_bounded_synthetic_batch",
    )
    assert _parent_unlinked_preview_scope_diagnostic(preview) == "none"
    from project_worker_model_loop import TOOL_DEFINITIONS
    assert {item["function"]["name"] for item in TOOL_DEFINITIONS} == {
        "read_project_file", "write_project_file", "patch_project_file",
    }
    for project_id in PROJECTS:
        assert _matches_approved_worker_task(project_id, tasks[project_id], tasks)
        payload = {"messages": [{"role": "user", "content": tasks[project_id]}]}
        assert _approved_worker_payload_project(payload, {project_id: tasks[project_id]}) == project_id


@pytest.mark.parametrize("project_id", PROJECTS)
def test_offline_synthetic_task_accepts_safe_model_rewording(project_id):
    content = OUTPUTS[project_id].rstrip("\n")
    rewritten = f"Write exactly the following content to result.txt: {content}"
    assert _synthetic_task_project_id(rewritten, project_id) == project_id


def test_offline_synthetic_task_project_binding_comes_from_structured_field():
    task = f"Create result.txt containing exactly {OUTPUTS[PROJECTS[0]].rstrip()}"
    assert _synthetic_task_project_id(task, PROJECTS[0]) == PROJECTS[0]
    assert _synthetic_task_project_id(task, PROJECTS[1]) is None


def test_offline_synthetic_task_confirmation_and_outbound_share_the_approved_rewrite():
    project_id = PROJECTS[0]
    content = OUTPUTS[project_id].rstrip("\n")
    task = f"Write exactly the following content to result.txt: {content}"
    batch_approval = {project_id: task}
    scope_approval = {project_id: task}
    payload = {"model": PROVIDER_MODEL, "messages": [{"role": "user", "content": task}]}

    assert _matches_approved_worker_task(project_id, task, batch_approval)
    assert _approved_worker_payload_project(payload, scope_approval) == project_id
    assert not _matches_approved_worker_task(project_id, task + " changed", batch_approval)
    changed_payload = {"model": PROVIDER_MODEL, "messages": [
        {"role": "user", "content": TASKS[project_id]},
    ]}
    assert _approved_worker_payload_project(changed_payload, scope_approval) is None


@pytest.mark.parametrize("mutation", [
    "extra_target", "changed_content", "read_request", "private_path", "private_relative_path",
    "private_marker_case_variant", "chinese_read_instruction",
])
def test_offline_synthetic_task_rejects_scope_expansion(mutation):
    project_id = PROJECTS[0]
    content = OUTPUTS[project_id].rstrip("\n")
    task = f"Write exactly the following content to result.txt: {content}"
    if mutation == "extra_target":
        task += " Also create notes.txt."
    elif mutation == "changed_content":
        task = task.replace("fixed harmless content.", "altered content.")
    elif mutation == "read_request":
        task += " Read the project files."
    elif mutation == "private_path":
        task += " F:\\my-agent-main\\private.txt"
    elif mutation == "private_relative_path":
        task += " ..\\private"
    elif mutation == "private_marker_case_variant":
        task += " parent_private_sentinel_842"
    elif mutation == "chinese_read_instruction":
        task += " 写入后读取项目文件。"
    assert _synthetic_task_project_id(task, project_id) is None


def test_offline_worker_request_guard_accepts_only_the_task_bound_by_approval():
    from types import SimpleNamespace

    project_id = PROJECTS[0]
    task = (
        f"In {project_id}, write exactly {OUTPUTS[project_id].rstrip(chr(10))} to result.txt. "
        "Do not read any project files and do not invoke any other tool."
    )
    payload = {"model": PROVIDER_MODEL, "messages": [{"role": "user", "content": task}]}
    sent = []
    state, _ = _new_live_request_state()
    state.update(tool_result_capture_armed=True, request_limit=5)
    task_bindings = {project_id: task, PROJECTS[1]: TASKS[PROJECTS[1]]}
    approval = {project_id: {"provider_payload": payload}}
    guarded_post, _ = _make_live_request_guards(
        lambda *_args, **_kwargs: sent.append("post") or SimpleNamespace(status_code=200),
        lambda *_args, **_kwargs: None,
        state=state, state_lock=threading.Lock(), outbound_approvals=approval,
        expected_worker_tasks=task_bindings, consume_worker_approval=True,
    )
    client = SimpleNamespace(follow_redirects=False)
    headers = {"Content-Type": "application/json", "Authorization": "Bearer offline-only"}
    altered = {"model": PROVIDER_MODEL, "messages": [{"role": "user", "content": TASKS[project_id]}]}

    with pytest.raises(RuntimeError, match="synthetic live request rejected"):
        guarded_post(client, ENDPOINT, json=altered, headers=headers, follow_redirects=False)
    assert sent == [] and state["requests"] == 0 and project_id in approval
    guarded_post(client, ENDPOINT, json=payload, headers=headers, follow_redirects=False)
    assert sent == ["post"] and state["requests"] == 1
    assert state["projects"] == {project_id} and project_id not in approval

