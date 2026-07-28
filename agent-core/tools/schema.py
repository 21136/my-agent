"""Unified tool result / error JSON (TOOLS.md §6.6).

All builtin and evolved tool invocations surface the same outer envelope to
callers (executor, CLI, LLM). Tool-specific payloads live under ``data``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ToolErrorCode(StrEnum):
    """Documented error codes (TOOLS.md §7.4, §7.5). Callers may use other strings."""

    # web_search
    MISSING_API_KEY = "missing_api_key"

    # fetch_url
    INVALID_URL = "invalid_url"
    BLOCKED_HOST = "blocked_host"
    TIMEOUT = "timeout"
    TOO_LARGE = "too_large"
    UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
    HTTP_ERROR = "http_error"
    NETWORK_ERROR = "network_error"

    # paths / filesystem (Phase 1 builtins)
    PATH_OUT_OF_BOUNDS = "path_out_of_bounds"
    PATH_DENIED = "path_denied"
    FILE_TOO_LARGE = "file_too_large"
    BINARY_FILE = "binary_file"
    NOT_FOUND = "not_found"
    PERMISSION_DENIED = "permission_denied"

    # executor / registry
    VALIDATION_ERROR = "validation_error"
    TOOL_NOT_FOUND = "tool_not_found"
    CONFIRM_REJECTED = "confirm_rejected"


@dataclass(frozen=True, slots=True)
class ToolError:
    code: str
    message: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            out["details"] = self.details
        return out

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ToolError:
        if "code" not in payload or "message" not in payload:
            raise ValueError("error object requires 'code' and 'message'")
        details = payload.get("details")
        if details is not None and not isinstance(details, dict):
            raise ValueError("error.details must be an object")
        return cls(code=str(payload["code"]), message=str(payload["message"]), details=details)


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Outer envelope returned for every tool call."""

    ok: bool
    tool: str
    data: Any | None = None
    truncated: bool = False
    error: ToolError | None = None
    duration_ms: int = 0
    output_path: str | None = None  # TOOLS.md §6.4 spill

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": self.ok,
            "tool": self.tool,
            "data": self.data,
            "truncated": self.truncated,
            "error": self.error.to_dict() if self.error is not None else None,
            "duration_ms": self.duration_ms,
        }
        if self.output_path is not None:
            out["output_path"] = self.output_path
        return out


def tool_ok(
    tool: str,
    data: Any,
    *,
    truncated: bool = False,
    duration_ms: int = 0,
    output_path: str | None = None,
) -> ToolResult:
    """Build a successful tool result."""
    if duration_ms < 0:
        raise ValueError("duration_ms must be non-negative")
    return ToolResult(
        ok=True,
        tool=tool,
        data=data,
        truncated=truncated,
        error=None,
        duration_ms=duration_ms,
        output_path=output_path,
    )


def tool_fail(
    tool: str,
    code: str,
    message: str,
    *,
    duration_ms: int = 0,
    details: dict[str, Any] | None = None,
) -> ToolResult:
    """Build a failed tool result (ok=false, data=null)."""
    if duration_ms < 0:
        raise ValueError("duration_ms must be non-negative")
    return ToolResult(
        ok=False,
        tool=tool,
        data=None,
        truncated=False,
        error=ToolError(code=code, message=message, details=details),
        duration_ms=duration_ms,
        output_path=None,
    )


def to_dict(result: ToolResult) -> dict[str, Any]:
    return result.to_dict()


def from_dict(payload: dict[str, Any]) -> ToolResult:
    """Parse a tool result dict. Raises ValueError on invalid shape."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")

    for key in ("ok", "tool"):
        if key not in payload:
            raise ValueError(f"missing required field: {key}")

    ok = payload["ok"]
    if not isinstance(ok, bool):
        raise ValueError("ok must be a boolean")

    tool = payload["tool"]
    if not isinstance(tool, str) or not tool:
        raise ValueError("tool must be a non-empty string")

    truncated = payload.get("truncated", False)
    if not isinstance(truncated, bool):
        raise ValueError("truncated must be a boolean")

    duration_ms = payload.get("duration_ms", 0)
    if not isinstance(duration_ms, int) or duration_ms < 0:
        raise ValueError("duration_ms must be a non-negative integer")

    raw_error = payload.get("error")
    error: ToolError | None
    if raw_error is None:
        error = None
    elif isinstance(raw_error, dict):
        error = ToolError.from_dict(raw_error)
    else:
        raise ValueError("error must be null or an object")

    output_path = payload.get("output_path")
    if output_path is not None and not isinstance(output_path, str):
        raise ValueError("output_path must be a string")

    data = payload.get("data")
    if "data" not in payload:
        data = None

    if ok and error is not None:
        raise ValueError("ok=true must not include error")
    if not ok and error is None:
        raise ValueError("ok=false requires error")

    return ToolResult(
        ok=ok,
        tool=tool,
        data=data,
        truncated=truncated,
        error=error,
        duration_ms=duration_ms,
        output_path=output_path,
    )


def to_json(result: ToolResult, *, indent: int | None = None) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=indent)


def from_json(text: str) -> ToolResult:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")
    return from_dict(payload)


def _demo() -> None:
    """Print §6.6 / §7.5 examples for manual verification."""
    examples: list[ToolResult] = [
        tool_ok(
            "grep",
            {
                "matches": [
                    {"path": "workspace/foo.txt", "line": 1, "text": "hello"},
                ],
            },
            duration_ms=12,
        ),
        tool_ok(
            "fetch_url",
            {
                "url": "https://example.com/page",
                "final_url": "https://example.com/page",
                "content": "正文纯文本…",
                "content_type": "text/html",
            },
            truncated=False,
            duration_ms=890,
        ),
        tool_fail(
            "web_search",
            ToolErrorCode.MISSING_API_KEY,
            "LLM_API_KEY is not set (deepseek provider)",
            duration_ms=1,
        ),
        tool_ok(
            "grep",
            {"preview": "x" * 2000},
            truncated=True,
            output_path="data/sessions/demo/tool_outputs/abc.txt",
            duration_ms=5,
        ),
    ]

    for result in examples:
        print(to_json(result, indent=2))
        print("---")
        roundtrip = from_dict(result.to_dict())
        assert roundtrip == result, (roundtrip, result)


if __name__ == "__main__":
    _demo()
