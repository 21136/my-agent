"""Builtin read_file (TOOLS.md §7.1, TASKS T-103)."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[2]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths, PathOutOfBoundsError
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok, to_json

TOOL_NAME = "read_file"
MAX_BYTES = 512 * 1024  # TOOLS.md §7.1
DEFAULT_LINE_LIMIT = 500
MAX_LINE_LIMIT = 2000


def _slice_lines(
    content: str,
    *,
    offset: int | None,
    limit: int | None,
    with_line_numbers: bool,
) -> dict[str, Any]:
    lines = content.splitlines(keepends=True)
    total_lines = len(lines)
    if total_lines == 0:
        return {
            "content": "",
            "total_lines": 0,
            "start_line": 1,
            "end_line": 0,
            "truncated_lines": False,
        }
    start = offset if offset is not None else 1
    if start < 0:
        start = max(1, total_lines + start + 1)
    else:
        start = max(1, start)
    if start > total_lines:
        return {
            "content": "",
            "total_lines": total_lines,
            "start_line": start,
            "end_line": total_lines,
            "truncated_lines": False,
            "hint": f"offset {start} exceeds total_lines ({total_lines})",
        }

    if limit is not None:
        effective_limit = max(1, min(limit, MAX_LINE_LIMIT))
    elif offset is not None or total_lines > DEFAULT_LINE_LIMIT:
        effective_limit = DEFAULT_LINE_LIMIT
    else:
        effective_limit = total_lines

    end_idx = min(total_lines, start - 1 + effective_limit)
    chunk_lines = lines[start - 1 : end_idx]
    truncated_lines = end_idx < total_lines

    if with_line_numbers:
        numbered: list[str] = []
        for idx, line in enumerate(chunk_lines, start=start):
            numbered.append(f"{idx:6d}|{line}")
        body = "".join(numbered)
    else:
        body = "".join(chunk_lines)

    out: dict[str, Any] = {
        "content": body,
        "total_lines": total_lines,
        "start_line": start,
        "end_line": end_idx,
        "truncated_lines": truncated_lines,
    }
    if end_idx < total_lines:
        next_offset = end_idx + 1
        out["hint"] = (
            f"Showing lines {start}-{end_idx} of {total_lines}. "
            f"To read more, call read_file with offset={next_offset}, limit={DEFAULT_LINE_LIMIT}."
        )
    return out


def run(arguments: dict[str, Any], *, paths: AgentPaths | None = None) -> ToolResult:
    """Read a text file under agent root (≤512KB, UTF-8, no binary), with optional offset/limit."""
    started = time.perf_counter()
    paths = paths or AgentPaths.discover()

    path_arg = arguments.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return _fail("path is required", ToolErrorCode.VALIDATION_ERROR, started)

    raw_offset = arguments.get("offset")
    if raw_offset is not None and (isinstance(raw_offset, bool) or not isinstance(raw_offset, int)):
        return _fail("offset must be an integer", ToolErrorCode.VALIDATION_ERROR, started, path=path_arg)
    raw_limit = arguments.get("limit")
    if raw_limit is not None and (isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or raw_limit < 1):
        return _fail("limit must be a positive integer", ToolErrorCode.VALIDATION_ERROR, started, path=path_arg)
    with_line_numbers = bool(arguments.get("line_numbers", False))

    try:
        resolved = resolve_read_path(paths, path_arg)
    except PathOutOfBoundsError as exc:
        return _fail(str(exc), exc.code, started, path=path_arg)
    except (TypeError, ValueError) as exc:
        return _fail(str(exc), ToolErrorCode.VALIDATION_ERROR, started, path=path_arg)
    except FileNotFoundError:
        # A probe before create is information: the file is not there yet.
        return tool_ok(
            TOOL_NAME,
            {
                "path": path_arg.strip(),
                "content": "",
                "size": 0,
                "exists": False,
            },
            duration_ms=_elapsed_ms(started),
        )

    if not resolved.is_file():
        return _fail(f"not a file: {path_arg}", ToolErrorCode.NOT_FOUND, started, path=path_arg)

    try:
        size = resolved.stat().st_size
    except OSError as exc:
        return _fail(str(exc), ToolErrorCode.PERMISSION_DENIED, started, path=path_arg)

    if size > MAX_BYTES:
        return _fail(
            f"file exceeds limit of {MAX_BYTES} bytes",
            ToolErrorCode.FILE_TOO_LARGE,
            started,
            path=path_arg,
            details={"size": size, "limit": MAX_BYTES},
        )

    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        return _fail(str(exc), ToolErrorCode.PERMISSION_DENIED, started, path=path_arg)

    if b"\0" in raw:
        return _fail("binary file rejected", ToolErrorCode.BINARY_FILE, started, path=path_arg)

    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _fail("file is not valid UTF-8 text", ToolErrorCode.BINARY_FILE, started, path=path_arg)

    rel_path = paths.to_agent_relative(resolved)
    sliced = _slice_lines(
        content,
        offset=raw_offset,
        limit=raw_limit,
        with_line_numbers=with_line_numbers,
    )
    payload: dict[str, Any] = {
        "path": rel_path,
        "size": size,
        **sliced,
    }
    return tool_ok(
        TOOL_NAME,
        payload,
        truncated=bool(sliced.get("truncated_lines")),
        duration_ms=_elapsed_ms(started),
    )


def resolve_read_path(paths: AgentPaths, raw: str) -> Path:
    """Resolve *raw* per TOOLS §7.1 (agent root, workspace, or host: prefix)."""
    stripped = raw.strip()

    if stripped.lower().startswith("host:"):
        from host_scope import load_host_scope, resolve_host_path

        config = load_host_scope(paths)
        resolved = resolve_host_path(stripped, config=config, must_exist=True)
        return resolved.absolute

    agent_path = paths.resolve_under_agent(stripped, must_exist=False)
    if agent_path.exists():
        return agent_path

    if Path(stripped).is_absolute():
        raise FileNotFoundError(stripped)

    workspace_path = paths.resolve_under_workspace(stripped, must_exist=False)
    if workspace_path.exists():
        return workspace_path

    raise FileNotFoundError(stripped)


def _fail(
    message: str,
    code: str,
    started: float,
    *,
    path: str | None = None,
    details: dict[str, Any] | None = None,
) -> ToolResult:
    merged = dict(details or {})
    if path is not None:
        merged.setdefault("path", path)
    return tool_fail(
        TOOL_NAME,
        code,
        message,
        duration_ms=_elapsed_ms(started),
        details=merged or None,
    )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _demo() -> None:
    paths = AgentPaths.discover()
    paths.workspace.mkdir(parents=True, exist_ok=True)

    sample = paths.workspace / "_read_file_demo.txt"
    sample.write_text("hello workspace\n", encoding="utf-8")
    binary = paths.workspace / "_read_file_demo.bin"
    binary.write_bytes(b"\x00binary")
    huge = paths.workspace / "_read_file_demo_huge.txt"
    huge.write_bytes(b"x" * (MAX_BYTES + 1))

    cases: list[tuple[dict[str, Any], bool, str | None]] = [
        ({"path": "docs/PROJECT.md"}, True, "agent-relative doc"),
        ({"path": "_read_file_demo.txt"}, True, "workspace bare name"),
        ({"path": "workspace/_read_file_demo.txt"}, True, "workspace via agent path"),
        ({"path": "../outside-agent"}, False, "out of bounds"),
        ({"path": "_read_file_demo.bin"}, False, "binary"),
        ({"path": "_read_file_demo_huge.txt"}, False, "too large"),
    ]

    for arguments, should_ok, label in cases:
        result = run(arguments, paths=paths)
        if result.ok != should_ok:
            print(f"[FAIL] {label}: expected ok={should_ok}, got {result.to_dict()}")
            raise SystemExit(1)
        status = "ok" if result.ok else result.error.code if result.error else "?"
        print(f"[PASS] {label}: {status}")

    missing = run({"path": "missing-file-xyz.txt"}, paths=paths)
    assert missing.ok and missing.data and missing.data.get("exists") is False
    assert missing.data.get("content") == ""
    print("[PASS] not found: exists=false")

    preview = run({"path": "docs/PROJECT.md"}, paths=paths)
    assert preview.ok and preview.data
    print(f"[PASS] content length: {len(preview.data['content'])} chars")
    print(to_json(preview, indent=2)[:400] + "...")

    for temp in (sample, binary, huge):
        temp.unlink(missing_ok=True)


if __name__ == "__main__":
    _demo()
