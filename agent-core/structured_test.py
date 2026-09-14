"""Normalize existing project test runners to the structured_test v1 protocol."""

from __future__ import annotations

import re
from typing import Any

_TAIL_CHARS = 4000
_TIMEOUT_RE = re.compile(r"\b(?:timed out|timeout|超时)\b", re.IGNORECASE)
_MISSING_DEP_RE = re.compile(
    r"(?:No module named|ModuleNotFoundError|cannot find module|not recognized as an internal|"
    r"command not found|no such file or directory|system cannot find the file|"
    r"winerror.?\s*2|找不到指定的文件|不是内部或外部命令)",
    re.IGNORECASE,
)


def _tail(value: Any) -> str:
    text = str(value or "")
    if len(text) <= _TAIL_CHARS:
        return text
    return "..." + text[-_TAIL_CHARS:]


def _is_missing_dependency(payload: dict[str, Any]) -> bool:
    error = str(payload.get("error") or "")
    stdout = str(payload.get("stdout") or payload.get("stdout_tail") or "")
    stderr = str(payload.get("stderr") or payload.get("stderr_tail") or "")
    return bool(_MISSING_DEP_RE.search("\n".join((error, stdout, stderr))))


def _rerun_hint(failures: list[dict[str, Any]]) -> dict[str, str] | None:
    if not failures:
        return None
    first = failures[0]
    file = first.get("file")
    test = first.get("test")
    if not isinstance(file, str) or not file.strip():
        return None
    hint: dict[str, str] = {"file": file}
    if isinstance(test, str) and test.strip():
        hint["test"] = test
    return hint


def normalize_test_result(payload: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    """Return a stable result while preserving useful runner-specific fields."""
    raw_failures = payload.get("failures")
    failures = [item for item in raw_failures if isinstance(item, dict)] if isinstance(raw_failures, list) else []
    error = str(payload.get("error") or "")
    if dry_run or payload.get("dry_run"):
        status = "blocked"
        reason = "dry_run"
    elif _TIMEOUT_RE.search(error):
        status = "timeout"
        reason = "timeout"
    elif _is_missing_dependency(payload):
        status = "blocked"
        reason = "missing_dependency"
    elif bool(payload.get("ok")) and not failures and payload.get("exit_code", 0) == 0:
        status = "passed"
        reason = None
    else:
        status = "failed"
        reason = None

    stdout = payload.get("stdout_tail", payload.get("stdout", ""))
    stderr = payload.get("stderr_tail", payload.get("stderr", ""))
    if not stdout and not stderr and payload.get("raw_excerpt"):
        stdout = payload.get("raw_excerpt")

    exit_code = payload.get("exit_code")
    if status in {"blocked", "timeout"} and not isinstance(exit_code, int):
        exit_code = None
    result: dict[str, Any] = {
        "status": status,
        "exit_code": exit_code if isinstance(exit_code, int) else None,
        "duration_ms": payload.get("duration_ms"),
        "failed": failures,
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
        "rerun": _rerun_hint(failures),
    }
    for key in ("suite", "working_dir", "command", "summary", "parse_ok"):
        if key in payload:
            result[key] = payload[key]
    if reason:
        result["reason"] = reason
    if error:
        result["error"] = error
    if payload.get("dry_run"):
        result["dry_run"] = True
    return result
