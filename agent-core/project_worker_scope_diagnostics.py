"""Content-safe diagnostics for worker tool path, scope, and permission failures."""
from __future__ import annotations

from pathlib import PurePosixPath
import re
from typing import Any, Literal

_TARGET_OPERATIONS = frozenset({
    "read_project_file", "write_project_file", "patch_project_file",
})
_TOOL_SCOPE_ERROR_CODES = frozenset({
    "read_target_not_declared",
    "write_target_not_declared",
    "invalid_read_arguments",
    "invalid_write_arguments",
    "patch_requires_prior_authorized_read",
    "patch_source_read_failed",
    "write_scope_denied",
    "write_already_attempted",
    "path_denied",
    "path_out_of_bounds",
    "permission_denied",
    "project_read_failed",
})
_MATCH_RESULTS = frozenset({
    "not_declared",
    "case_mismatch",
    "leading_dot_segment",
    "backslash_separator",
    "parent_traversal",
    "absolute_or_rooted",
    "cross_project_path",
    "declared_for_write_only",
    "declared_for_read_only",
    "invalid_path_shape",
    "invalid_requested_path",
    "missing_prior_read_snapshot",
    "read_not_declared_for_patch",
    "executor_path_denied",
    "executor_path_out_of_bounds",
    "executor_permission_denied",
    "write_binding_changed",
    "host_binding_changed",
    "write_identity_changed",
    "non_retryable_read_failure",
})
_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_WINDOWS_RESERVED = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?$",
    re.IGNORECASE,
)
_MAX_PATH_CHARS = 256
_MAX_TARGETS = 32
_MAX_RECOVERED_SCOPE_CORRECTIONS = 8
_SCOPE_ERROR_DETAIL_KEYS = frozenset({
    "error", "operation", "project_id", "requested_path", "path_redacted",
    "declared_read_targets", "declared_write_targets", "allowed_targets",
    "match_result", "normalized_path", "closest_declared_target", "hint",
})
_SCOPE_ERROR_DETAIL_PUBLIC_KEYS = _SCOPE_ERROR_DETAIL_KEYS | frozenset({
    "recovered", "recovered_corrections",
})


def _is_safe_relative_path(path: str) -> bool:
    if not path or len(path.encode("utf-8")) > 1024:
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in path):
        return False
    if "\\" in path or ":" in path or path.startswith("/"):
        return False
    try:
        pure = PurePosixPath(path)
    except Exception:
        return False
    if pure.is_absolute() or pure.as_posix() != path:
        return False
    return all(
        part not in ("", ".", "..")
        and not part.endswith((".", " "))
        and _WINDOWS_RESERVED.fullmatch(part) is None
        for part in pure.parts
    )


def is_safe_worker_relative_path(path: str) -> bool:
    return _is_safe_relative_path(path)


def _strip_leading_dot_segments(path: str) -> str | None:
    if type(path) is not str or not path.strip():
        return None
    if "\\" in path or ":" in path or path.startswith("/"):
        return None
    try:
        pure = PurePosixPath(path)
    except Exception:
        return None
    if any(part == ".." for part in pure.parts):
        return None
    parts = list(pure.parts)
    while parts and parts[0] == ".":
        parts.pop(0)
    if not parts:
        return None
    candidate = "/".join(parts)
    return candidate if _is_safe_relative_path(candidate) else None


def _cross_project_path(requested: str, project_id: str) -> bool:
    if type(requested) is not str or type(project_id) is not str:
        return False
    if requested.startswith("workspace/") or requested.startswith("workspace\\"):
        return True
    try:
        parts = PurePosixPath(requested).parts
    except Exception:
        return False
    if not parts:
        return False
    first = parts[0]
    return (
        first != project_id
        and _PROJECT_ID.fullmatch(first) is not None
        and len(parts) > 1
    )


def _classify_mismatch(
    requested: str,
    declared: tuple[str, ...],
    *,
    project_id: str,
    other_kind: tuple[str, ...] | None,
    other_kind_name: Literal["write", "read"] | None,
) -> tuple[str, str | None, str | None]:
    """Return (match_result, normalized_path, closest_declared)."""
    if type(requested) is not str:
        return "invalid_requested_path", None, None
    if not requested.strip():
        return "invalid_requested_path", None, None
    if "\\" in requested or requested.startswith("\\"):
        return "backslash_separator", None, None
    if ":" in requested or requested.startswith("/"):
        return "absolute_or_rooted", None, None
    if ".." in PurePosixPath(requested).parts:
        return "parent_traversal", None, None
    if _cross_project_path(requested, project_id):
        return "cross_project_path", None, None
    normalized = _strip_leading_dot_segments(requested)
    if normalized is not None and normalized != requested and normalized in declared:
        return "leading_dot_segment", normalized, normalized
    if not _is_safe_relative_path(requested):
        return "invalid_path_shape", None, None
    if requested in declared:
        return "not_declared", None, None
    for item in declared:
        if item.casefold() == requested.casefold():
            return "case_mismatch", None, item
    if other_kind is not None and other_kind_name is not None and requested in other_kind:
        return (
            "declared_for_write_only" if other_kind_name == "write" else "declared_for_read_only",
            None,
            None,
        )
    return "not_declared", None, None


def _hint_for(match_result: str, *, target_kind: Literal["read", "write"]) -> str:
    if match_result == "case_mismatch":
        return "Repeat the allowed target path with exact spelling and casing."
    if match_result == "leading_dot_segment":
        return "Drop leading ./ segments; use the exact declared relative path."
    if match_result == "backslash_separator":
        return "Use forward slashes and project-relative paths, not Windows drive or UNC paths."
    if match_result in {"absolute_or_rooted", "parent_traversal"}:
        return "Request only a project-relative path from the host-declared list; no .. or absolute paths."
    if match_result == "cross_project_path":
        return "Stay inside the bound project; do not prefix workspace/ or another project_id."
    if match_result == "declared_for_write_only":
        return "This path is write-authorized only; pick a declared read target to read it first."
    if match_result == "declared_for_read_only":
        return "This path is read-authorized only; pick a declared write target to modify it."
    if match_result == "missing_prior_read_snapshot":
        return "Call read_project_file on this path in the same run before patch_project_file."
    if match_result == "read_not_declared_for_patch":
        return "Add the path to declared read targets, or patch a write target that is also read-authorized."
    if match_result == "executor_path_out_of_bounds":
        return "The executor rejected the resolved path; use an exact declared target with no escape segments."
    if match_result in {"executor_path_denied", "executor_permission_denied"}:
        return "The host executor denied this path; narrow to declared targets for this project only."
    if match_result == "write_binding_changed":
        return "Host project binding changed; stop and wait for host re-authorization."
    if match_result == "host_binding_changed":
        return "Session or project binding changed during the write; do not retry without fresh host scope."
    if match_result == "write_identity_changed":
        return "The on-disk target changed after scope was granted; host must re-issue the worker lease."
    if match_result in {"invalid_path_shape", "invalid_requested_path"}:
        return "Pass a non-empty string path exactly as listed in the host authorization."
    return (
        "Pick one path from declared targets exactly as declared for "
        f"{target_kind}; do not substitute a different file."
    )


def _target_kind_for_error(error_code: str) -> Literal["read", "write"]:
    if error_code in {
        "read_target_not_declared", "invalid_read_arguments",
        "patch_requires_prior_authorized_read", "patch_source_read_failed",
        "project_read_failed",
    }:
        return "read"
    return "write"


def _primary_declared(
    error_code: str,
    read_targets: tuple[str, ...],
    write_targets: tuple[str, ...],
) -> tuple[str, ...]:
    return read_targets if _target_kind_for_error(error_code) == "read" else write_targets


def _attach_path_fields(payload: dict[str, Any], requested_path: object) -> None:
    req = requested_path if type(requested_path) is str else ""
    if _is_safe_relative_path(req):
        payload["requested_path"] = req if len(req) <= _MAX_PATH_CHARS else req[:_MAX_PATH_CHARS]
    else:
        payload["path_redacted"] = True


def build_worker_tool_scope_error(
    *,
    error_code: str,
    operation: str,
    requested_path: object,
    project_id: str,
    read_targets: tuple[str, ...],
    write_targets: tuple[str, ...],
    match_result: str | None = None,
    hint: str | None = None,
    normalized_path: str | None = None,
    closest_declared_target: str | None = None,
) -> dict[str, Any]:
    if error_code not in _TOOL_SCOPE_ERROR_CODES:
        raise ValueError("unsupported tool scope error code")
    if operation not in _TARGET_OPERATIONS:
        raise ValueError("unsupported operation")
    if (type(read_targets) is not tuple or type(write_targets) is not tuple
            or len(read_targets) > _MAX_TARGETS or len(write_targets) > _MAX_TARGETS
            or any(type(item) is not str for item in (*read_targets, *write_targets))
            or type(project_id) is not str or _PROJECT_ID.fullmatch(project_id) is None):
        raise ValueError("invalid declared scope")
    target_kind = _target_kind_for_error(error_code)
    primary = _primary_declared(error_code, read_targets, write_targets)
    other_kind: Literal["write", "read"] | None = "write" if target_kind == "read" else "read"
    other = write_targets if target_kind == "read" else read_targets
    req = requested_path if type(requested_path) is str else ""
    resolved_match = match_result
    resolved_norm = normalized_path
    resolved_closest = closest_declared_target
    if resolved_match is None:
        resolved_match, resolved_norm, resolved_closest = _classify_mismatch(
            req, primary,
            project_id=project_id,
            other_kind=other,
            other_kind_name=other_kind,
        )
    payload: dict[str, Any] = {
        "ok": False,
        "error": error_code,
        "operation": operation,
        "project_id": project_id,
        "declared_read_targets": list(read_targets),
        "declared_write_targets": list(write_targets),
        "allowed_targets": list(primary),
        "match_result": resolved_match,
        "hint": hint or _hint_for(resolved_match, target_kind=target_kind),
    }
    _attach_path_fields(payload, requested_path)
    if resolved_norm is not None:
        payload["normalized_path"] = resolved_norm
    if resolved_closest is not None:
        payload["closest_declared_target"] = resolved_closest
    if not validate_scope_tool_error(payload):
        raise ValueError("scope tool error failed validation")
    return payload


def build_target_scope_tool_error(
    error_code: str,
    operation: str,
    requested_path: object,
    declared_targets: tuple[str, ...],
    *,
    project_id: str,
    other_kind_targets: tuple[str, ...] = (),
    other_kind: Literal["write", "read"] | None = None,
) -> dict[str, Any]:
    """Backward-compatible wrapper when only one declared list is available."""
    if other_kind == "write":
        read_targets, write_targets = declared_targets, other_kind_targets
    elif other_kind == "read":
        read_targets, write_targets = other_kind_targets, declared_targets
    else:
        read_targets = declared_targets if "read" in error_code else ()
        write_targets = declared_targets if "write" in error_code else ()
    return build_worker_tool_scope_error(
        error_code=error_code,
        operation=operation,
        requested_path=requested_path,
        project_id=project_id,
        read_targets=read_targets,
        write_targets=write_targets,
    )


def patch_read_prerequisite_error(
    *,
    operation: str,
    path: str,
    project_id: str,
    read_targets: tuple[str, ...],
    write_targets: tuple[str, ...],
    read_snapshots: set[str],
) -> dict[str, Any]:
    if path not in write_targets:
        return build_worker_tool_scope_error(
            error_code="write_target_not_declared",
            operation=operation,
            requested_path=path,
            project_id=project_id,
            read_targets=read_targets,
            write_targets=write_targets,
        )
    if path not in read_targets:
        return build_worker_tool_scope_error(
            error_code="patch_requires_prior_authorized_read",
            operation=operation,
            requested_path=path,
            project_id=project_id,
            read_targets=read_targets,
            write_targets=write_targets,
            match_result="read_not_declared_for_patch",
        )
    if path not in read_snapshots:
        return build_worker_tool_scope_error(
            error_code="patch_requires_prior_authorized_read",
            operation=operation,
            requested_path=path,
            project_id=project_id,
            read_targets=read_targets,
            write_targets=write_targets,
            match_result="missing_prior_read_snapshot",
        )
    raise ValueError("patch prerequisite satisfied")


def permission_error_scope_payload(
    *,
    operation: str,
    path: str,
    project_id: str,
    read_targets: tuple[str, ...],
    write_targets: tuple[str, ...],
    permission_message: str,
) -> dict[str, Any]:
    message = permission_message.casefold()
    match_override: str | None = None
    if "not declared" in message and "read" in message:
        error_code = "read_target_not_declared"
    elif "not declared" in message:
        error_code = "write_target_not_declared"
    elif "write_already_attempted" in message:
        error_code = "write_already_attempted"
    elif "binding changed" in message:
        error_code = "write_scope_denied"
        match_override = "host_binding_changed"
    elif "identity changed" in message:
        error_code = "write_scope_denied"
        match_override = "write_identity_changed"
    else:
        error_code = "write_scope_denied"
        match_override = "executor_permission_denied"
    return build_worker_tool_scope_error(
        error_code=error_code,
        operation=operation,
        requested_path=path,
        project_id=project_id,
        read_targets=read_targets,
        write_targets=write_targets,
        match_result=match_override,
    )


def executor_scope_payload(
    *,
    operation: str,
    path: str,
    project_id: str,
    read_targets: tuple[str, ...],
    write_targets: tuple[str, ...],
    executor_error_code: str,
) -> dict[str, Any]:
    match_map = {
        "path_denied": "executor_path_denied",
        "path_out_of_bounds": "executor_path_out_of_bounds",
        "permission_denied": "executor_permission_denied",
        "confirm_rejected": "executor_permission_denied",
    }
    error_code = executor_error_code if executor_error_code in match_map else "permission_denied"
    return build_worker_tool_scope_error(
        error_code=error_code,
        operation=operation,
        requested_path=path,
        project_id=project_id,
        read_targets=read_targets,
        write_targets=write_targets,
        match_result=match_map.get(executor_error_code, "executor_permission_denied"),
    )


def build_scope_error_detail(tool_error: dict[str, Any]) -> dict[str, Any]:
    if not validate_scope_tool_error(tool_error):
        raise ValueError("invalid scope tool error")
    detail = {
        "error": tool_error["error"],
        "operation": tool_error["operation"],
        "project_id": tool_error["project_id"],
        "match_result": tool_error["match_result"],
        "declared_read_targets": list(tool_error["declared_read_targets"]),
        "declared_write_targets": list(tool_error["declared_write_targets"]),
        "allowed_targets": list(tool_error["allowed_targets"]),
        "hint": tool_error["hint"],
    }
    if "requested_path" in tool_error:
        detail["requested_path"] = tool_error["requested_path"]
    if tool_error.get("path_redacted") is True:
        detail["path_redacted"] = True
    for optional in ("normalized_path", "closest_declared_target"):
        if optional in tool_error:
            detail[optional] = tool_error[optional]
    if not _validate_scope_error_detail_core(detail):
        raise ValueError("scope error detail failed validation")
    return detail


def build_recovered_scope_correction_detail(tool_error: dict[str, Any]) -> dict[str, Any]:
    detail = build_scope_error_detail(tool_error)
    recovered = {**detail, "recovered": True}
    if not validate_scope_recovery_entry(recovered):
        raise ValueError("recovered scope correction failed validation")
    return recovered


def aggregate_recovered_scope_error_detail(
    corrections: list[dict[str, Any]],
) -> dict[str, Any]:
    if (type(corrections) is not list or not corrections
            or len(corrections) > _MAX_RECOVERED_SCOPE_CORRECTIONS):
        raise ValueError("invalid recovered scope corrections")
    normalized: list[dict[str, Any]] = []
    for entry in corrections:
        if type(entry) is not dict or not validate_scope_recovery_entry(entry):
            raise ValueError("invalid recovered scope correction entry")
        normalized.append(dict(entry))
    primary = {key: value for key, value in normalized[0].items() if key != "recovered"}
    aggregated = {
        **primary,
        "recovered": True,
        "recovered_corrections": normalized,
    }
    if not validate_scope_error_detail(aggregated):
        raise ValueError("aggregated scope error detail failed validation")
    return aggregated


def _paths_valid(targets: list[str]) -> bool:
    return len(targets) <= _MAX_TARGETS and all(
        type(item) is str and _is_safe_relative_path(item) for item in targets
    )


_BATCH_OUTBOUND_SCOPE_TOOL_ERROR_KEYS = frozenset({
    "ok",
    "error",
    "operation",
    "project_id",
    "declared_read_targets",
    "declared_write_targets",
    "allowed_targets",
    "match_result",
    "hint",
    "requested_path",
    "path_redacted",
    "normalized_path",
    "closest_declared_target",
})
_IN_LOOP_UNDECLARED_READ_SCOPE_ERRORS = frozenset({"read_target_not_declared"})
_IN_LOOP_UNDECLARED_WRITE_SCOPE_ERRORS = frozenset({"write_target_not_declared"})


def batch_outbound_undeclared_read_correction_matches(
    *,
    attempted_path: str,
    tool_result: Any,
    project_id: str,
    read_targets: tuple[str, ...],
    write_targets: tuple[str, ...],
) -> bool:
    """True when a tool result is the in-loop undeclared-read correction the model loop appends."""
    if (type(attempted_path) is not str or attempted_path in read_targets
            or not _is_safe_relative_path(attempted_path)):
        return False
    if (type(tool_result) is not dict or set(tool_result) - _BATCH_OUTBOUND_SCOPE_TOOL_ERROR_KEYS
            or not validate_scope_tool_error(tool_result)):
        return False
    if tool_result.get("error") not in _IN_LOOP_UNDECLARED_READ_SCOPE_ERRORS:
        return False
    if (tool_result.get("operation") != "read_project_file"
            or tool_result.get("project_id") != project_id
            or tool_result.get("requested_path") != attempted_path):
        return False
    if (list(tool_result.get("declared_read_targets", [])) != list(read_targets)
            or list(tool_result.get("declared_write_targets", [])) != list(write_targets)
            or list(tool_result.get("allowed_targets", [])) != list(read_targets)):
        return False
    return True


def batch_outbound_undeclared_write_correction_matches(
    *,
    attempted_path: str,
    tool_result: Any,
    project_id: str,
    read_targets: tuple[str, ...],
    write_targets: tuple[str, ...],
) -> bool:
    """True when a tool result is the in-loop undeclared-write correction the model loop appends."""
    if (type(attempted_path) is not str or attempted_path in write_targets
            or not _is_safe_relative_path(attempted_path)):
        return False
    if (type(tool_result) is not dict or set(tool_result) - _BATCH_OUTBOUND_SCOPE_TOOL_ERROR_KEYS
            or not validate_scope_tool_error(tool_result)):
        return False
    if tool_result.get("error") not in _IN_LOOP_UNDECLARED_WRITE_SCOPE_ERRORS:
        return False
    if (tool_result.get("operation") != "write_project_file"
            or tool_result.get("project_id") != project_id
            or tool_result.get("requested_path") != attempted_path):
        return False
    if (list(tool_result.get("declared_read_targets", [])) != list(read_targets)
            or list(tool_result.get("declared_write_targets", [])) != list(write_targets)
            or list(tool_result.get("allowed_targets", [])) != list(write_targets)):
        return False
    return True


def validate_scope_tool_error(payload: Any) -> bool:
    if type(payload) is not dict or payload.get("ok") is not False:
        return False
    if payload.get("error") not in _TOOL_SCOPE_ERROR_CODES:
        return False
    if payload.get("operation") not in _TARGET_OPERATIONS:
        return False
    if _PROJECT_ID.fullmatch(payload.get("project_id") or "") is None:
        return False
    for key in ("declared_read_targets", "declared_write_targets", "allowed_targets"):
        value = payload.get(key)
        if type(value) is not list or not _paths_valid(value):
            return False
    if payload.get("match_result") not in _MATCH_RESULTS:
        return False
    hint = payload.get("hint")
    if type(hint) is not str or not hint or len(hint.encode("utf-8")) > 512:
        return False
    has_path = "requested_path" in payload
    redacted = payload.get("path_redacted") is True
    if has_path == redacted:
        return False
    if has_path and not _is_safe_relative_path(payload["requested_path"]):
        return False
    for optional in ("normalized_path", "closest_declared_target"):
        if optional in payload and not _is_safe_relative_path(payload[optional]):
            return False
    return True


def _validate_scope_error_detail_core(detail: dict[str, Any]) -> bool:
    if not set(detail) <= _SCOPE_ERROR_DETAIL_KEYS:
        return False
    if detail.get("error") not in _TOOL_SCOPE_ERROR_CODES:
        return False
    if detail.get("operation") not in _TARGET_OPERATIONS:
        return False
    if _PROJECT_ID.fullmatch(detail.get("project_id") or "") is None:
        return False
    if detail.get("match_result") not in _MATCH_RESULTS:
        return False
    for key in ("declared_read_targets", "declared_write_targets", "allowed_targets"):
        if not _paths_valid(detail.get(key, [])):
            return False
    hint = detail.get("hint")
    if type(hint) is not str or not hint or len(hint.encode("utf-8")) > 512:
        return False
    has_path = "requested_path" in detail
    redacted = detail.get("path_redacted") is True
    if has_path and redacted:
        return False
    if not has_path and not redacted:
        return False
    if has_path and not _is_safe_relative_path(detail["requested_path"]):
        return False
    for optional in ("normalized_path", "closest_declared_target"):
        if optional in detail and not _is_safe_relative_path(detail[optional]):
            return False
    return True


def validate_scope_recovery_entry(entry: Any) -> bool:
    if type(entry) is not dict or entry.get("recovered") is not True:
        return False
    stripped = {key: value for key, value in entry.items() if key != "recovered"}
    return _validate_scope_error_detail_core(stripped)


def validate_scope_error_detail(detail: Any) -> bool:
    if type(detail) is not dict or not set(detail) <= _SCOPE_ERROR_DETAIL_PUBLIC_KEYS:
        return False
    if detail.get("recovered") is True:
        corrections = detail.get("recovered_corrections")
        if (type(corrections) is not list or not corrections
                or len(corrections) > _MAX_RECOVERED_SCOPE_CORRECTIONS
                or not all(validate_scope_recovery_entry(item) for item in corrections)):
            return False
        primary = {key: value for key, value in corrections[0].items() if key != "recovered"}
        payload = {key: value for key, value in detail.items()
                   if key not in {"recovered", "recovered_corrections"}}
        if payload != primary:
            return False
        return True
    if "recovered" in detail or "recovered_corrections" in detail:
        return False
    return _validate_scope_error_detail_core(detail)


def serialize_scope_error_detail(model_turn: Any) -> tuple[bool, dict[str, Any] | None]:
    try:
        detail = getattr(model_turn, "scope_error_detail", None)
        if detail is None:
            return True, None
        if not validate_scope_error_detail(detail):
            return False, None
        return True, dict(detail)
    except Exception:
        return False, None
