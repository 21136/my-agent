"""Validation and content-free serialization for host-observed worker read attempts."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from project_worker_execution import ProjectReadAttempt

_ALLOWED_RESULTS = {"success", "retrying", "failed", "cancelled"}
_WINDOWS_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?$", re.IGNORECASE)
_ALLOWED_ERRORS = {
    "", "sharing_violation", "file_lock_conflict", "lock_not_granted",
    "lock_violation", "read_retry_exhausted", "retry_cancel_event_unavailable",
    "non_retryable",
}


def validate_project_worker_read_attempts(available: Any, attempts: Any) -> bool:
    if type(available) is not bool or type(attempts) is not list or len(attempts) > 256:
        return False
    if not available and attempts:
        return False
    last_by_read: dict[int, tuple[str, int, str]] = {}
    for row in attempts:
        if type(row) is not dict or set(row) != {"read_id", "path", "attempt", "result", "error_code"}:
            return False
        read_id, path, attempt, result, error = (
            row["read_id"], row["path"], row["attempt"], row["result"], row["error_code"],
        )
        if (type(read_id) is not int or not 1 <= read_id <= 64
                or type(path) is not str or not path
                or type(attempt) is not int or not 1 <= attempt <= 6
                or type(result) is not str or result not in _ALLOWED_RESULTS
                or type(error) is not str or error not in _ALLOWED_ERRORS):
            return False
        try:
            if len(path.encode("utf-8")) > 1024:
                return False
        except UnicodeError:
            return False
        parts = path.split("/")
        if (Path(path).is_absolute()
                or any(ord(char) < 32 or ord(char) == 127 or char in '\\:<">|?*~' for char in path)
                or any(part in ("", ".", "..", "host") or part[-1] in " ."
                       or _WINDOWS_RESERVED.fullmatch(part) is not None for part in parts)):
            return False
        previous = last_by_read.get(read_id)
        if previous is None:
            if attempt != 1:
                return False
        elif previous[0] != path or not (
            attempt == previous[1] + 1 and previous[2] == "retrying"
            or attempt == previous[1] and previous[2] == "retrying" and result == "cancelled"
        ):
            return False
        last_by_read[read_id] = (path, attempt, result)
    return True


def serialize_project_worker_read_attempts(model_turn: Any) -> tuple[bool, list[dict[str, Any]]]:
    """Return only bounded identity/path/attempt/result/error-code evidence."""
    try:
        attempts = getattr(model_turn, "read_attempts", None)
        if type(attempts) is not tuple or len(attempts) > 256:
            return False, []
        result: list[dict[str, Any]] = []
        for item in attempts:
            if type(item) is not ProjectReadAttempt:
                return False, []
            result.append({
                "read_id": item.read_id, "path": item.path, "attempt": item.attempt,
                "result": item.result, "error_code": item.error_code,
            })
        if not validate_project_worker_read_attempts(True, result):
            return False, []
        return True, result
    except Exception:
        return False, []
