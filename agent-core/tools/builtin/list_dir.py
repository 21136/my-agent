"""Builtin list_dir (TOOLS.md §7.2, TASKS T-104)."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Literal

_AGENT_CORE = Path(__file__).resolve().parents[2]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths, PathOutOfBoundsError
from tools.builtin.read_file import resolve_read_path
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok, to_json

TOOL_NAME = "list_dir"


def run(arguments: dict[str, Any], *, paths: AgentPaths | None = None) -> ToolResult:
    """List directory entries under agent root (optional one-level recursion)."""
    started = time.perf_counter()
    paths = paths or AgentPaths.discover()

    path_arg = arguments.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return _fail("path is required", ToolErrorCode.VALIDATION_ERROR, started)

    recursive = arguments.get("recursive", False)
    if not isinstance(recursive, bool):
        return _fail("recursive must be a boolean", ToolErrorCode.VALIDATION_ERROR, started, path=path_arg)

    try:
        resolved = resolve_read_path(paths, path_arg)
    except PathOutOfBoundsError as exc:
        return _fail(str(exc), exc.code, started, path=path_arg)
    except (TypeError, ValueError) as exc:
        return _fail(str(exc), ToolErrorCode.VALIDATION_ERROR, started, path=path_arg)
    except FileNotFoundError:
        return _fail(f"path does not exist: {path_arg}", ToolErrorCode.NOT_FOUND, started, path=path_arg)

    if not resolved.is_dir():
        return _fail(f"not a directory: {path_arg}", ToolErrorCode.NOT_FOUND, started, path=path_arg)

    try:
        entries = _collect_entries(resolved, recursive=recursive)
    except OSError as exc:
        return _fail(str(exc), ToolErrorCode.PERMISSION_DENIED, started, path=path_arg)

    rel_path = paths.to_agent_relative(resolved)
    return tool_ok(
        TOOL_NAME,
        {"path": rel_path, "entries": entries},
        duration_ms=_elapsed_ms(started),
    )


def _collect_entries(directory: Path, *, recursive: bool) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for child in _sorted_children(directory):
        entries.append(_entry_dict(child))
        if recursive and child.is_dir():
            for grandchild in _sorted_children(child):
                entries.append(_entry_dict(grandchild, name_prefix=f"{child.name}/"))
    return entries


def _sorted_children(directory: Path) -> list[Path]:
    return sorted(directory.iterdir(), key=lambda item: item.name.lower())


def _entry_dict(path: Path, *, name_prefix: str = "") -> dict[str, Any]:
    name = f"{name_prefix}{path.name}"
    entry_type: Literal["file", "directory"] = "directory" if path.is_dir() else "file"
    item: dict[str, Any] = {"name": name, "type": entry_type}
    if entry_type == "file":
        item["size"] = path.stat().st_size
    return item


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

    demo_dir = paths.workspace / "_list_dir_demo"
    sub_dir = demo_dir / "sub"
    sub_dir.mkdir(parents=True, exist_ok=True)
    (demo_dir / "a.txt").write_text("a", encoding="utf-8")
    (sub_dir / "b.txt").write_text("b", encoding="utf-8")

    cases: list[tuple[dict[str, Any], bool, str]] = [
        ({"path": "docs"}, True, "list docs"),
        ({"path": "workspace/_list_dir_demo"}, True, "non-recursive demo dir"),
        ({"path": "workspace/_list_dir_demo", "recursive": True}, True, "recursive one level"),
        ({"path": "../outside-agent"}, False, "out of bounds"),
        ({"path": "docs/PROJECT.md"}, False, "file not directory"),
        ({"path": "missing-dir-xyz"}, False, "not found"),
        ({"path": "workspace/_list_dir_demo", "recursive": "yes"}, False, "bad recursive type"),
    ]

    for arguments, should_ok, label in cases:
        result = run(arguments, paths=paths)
        if result.ok != should_ok:
            print(f"[FAIL] {label}: expected ok={should_ok}, got {result.to_dict()}")
            raise SystemExit(1)
        status = "ok" if result.ok else result.error.code if result.error else "?"
        print(f"[PASS] {label}: {status}")

    flat = run({"path": "workspace/_list_dir_demo"}, paths=paths)
    recursive = run({"path": "workspace/_list_dir_demo", "recursive": True}, paths=paths)
    assert flat.ok and recursive.ok
    flat_names = {e["name"] for e in flat.data["entries"]}
    recursive_names = {e["name"] for e in recursive.data["entries"]}
    assert flat_names == {"a.txt", "sub"}
    assert recursive_names == {"a.txt", "sub", "sub/b.txt"}
    print(f"[PASS] entry names flat={sorted(flat_names)} recursive={sorted(recursive_names)}")
    print(to_json(flat, indent=2))

    import shutil

    shutil.rmtree(demo_dir, ignore_errors=True)


if __name__ == "__main__":
    _demo()
