"""patch_file — replace text by line range or unique anchor (P3 coding)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_MAX_BYTES = 512 * 1024


def _agent_root() -> Path:
    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        evolve_marker = directory / "evolve"
        if (evolve_marker / "_index.core.toml").is_file() or (evolve_marker / "_index.toml").is_file():
            return directory
    raise RuntimeError("could not locate agent root")


def _agent_core_dir() -> Path:
    return _agent_root() / "agent-core"


def _load_paths():
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from paths import AgentPaths, PathOutOfBoundsError

    return AgentPaths, PathOutOfBoundsError


def _is_binary(sample: bytes) -> bool:
    return b"\0" in sample


def _apply_line_patch(lines: list[str], start: int, end: int, replacement: str) -> list[str]:
    if start < 1 or end < start or end > len(lines):
        raise ValueError(f"invalid line range {start}-{end} for {len(lines)} lines")
    chunk = replacement if replacement.endswith("\n") else replacement + "\n"
    return lines[: start - 1] + [chunk] + lines[end:]


def _read_text_lines(target: Path) -> list[str]:
    with target.open(encoding="utf-8", newline="") as handle:
        return handle.readlines()


def _write_text_lines(target: Path, lines: list[str]) -> None:
    with target.open("w", encoding="utf-8", newline="") as handle:
        handle.writelines(lines)


def _apply_find_patch(text: str, find: str, replacement: str) -> tuple[str, int]:
    count = text.count(find)
    if count == 0:
        raise ValueError("find anchor not found")
    if count > 1:
        raise ValueError(f"find anchor matched {count} times; must be unique")
    return text.replace(find, replacement, 1), 1


def run_patch(payload: dict[str, Any]) -> dict[str, Any]:
    AgentPaths, PathOutOfBoundsError = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    path_arg = payload.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return {"ok": False, "error": "path is required"}

    replacement = payload.get("replacement")
    if not isinstance(replacement, str):
        return {"ok": False, "error": "replacement is required"}

    start_line = payload.get("start_line")
    end_line = payload.get("end_line")
    find = payload.get("find")
    dry_run = bool(payload.get("dry_run", False))

    try:
        target = paths.resolve_under_agent(path_arg, must_exist=True)
    except PathOutOfBoundsError as exc:
        return {"ok": False, "error": str(exc)}
    except (TypeError, ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}

    if not target.is_file():
        return {"ok": False, "error": f"not a file: {paths.to_agent_relative(target)}"}

    size = target.stat().st_size
    if size > _MAX_BYTES:
        return {"ok": False, "error": f"file exceeds limit of {_MAX_BYTES} bytes"}

    raw = target.read_bytes()
    if _is_binary(raw[:8192]):
        return {"ok": False, "error": "binary file not supported"}

    text = raw.decode("utf-8")
    rel = paths.to_agent_relative(target)

    has_range = start_line is not None or end_line is not None
    if has_range:
        if not isinstance(start_line, int) or not isinstance(end_line, int):
            return {"ok": False, "error": "start_line and end_line are required together"}
        lines = _read_text_lines(target)
        try:
            new_lines = _apply_line_patch(lines, start_line, end_line, replacement)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        new_text = "".join(new_lines)
        mode = "line_range"
        lines_changed = end_line - start_line + 1
    elif isinstance(find, str) and find:
        try:
            new_text, _ = _apply_find_patch(text, find, replacement)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        mode = "find"
        lines_changed = 1
    else:
        return {"ok": False, "error": "provide start_line+end_line or find"}

    if new_text == text:
        return {
            "ok": True,
            "path": rel,
            "mode": mode,
            "lines_changed": 0,
            "skipped": True,
        }

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "path": rel,
            "mode": mode,
            "lines_changed": lines_changed,
        }

    try:
        if has_range:
            _write_text_lines(target, new_lines)
        else:
            target.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "path": rel,
        "mode": mode,
        "lines_changed": lines_changed,
    }


def main() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_patch)


def _demo() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from paths import AgentPaths
    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    paths = AgentPaths.discover()
    registry = ToolRegistry.load(paths)
    tool = registry.get_evolved("patch_file")
    assert tool is not None and tool.scope == "coding"
    print("[PASS] registry loads patch_file (coding, active)")

    rel = "workspace/_patch_demo.txt"
    target = paths.workspace / "_patch_demo.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    dry = run(
        {
            "tool_name": "patch_file",
            "arguments": {
                "path": rel,
                "start_line": 2,
                "end_line": 2,
                "replacement": "BETA\n",
            },
            "dry_run": True,
        },
        registry=registry,
    )
    assert dry.ok and target.read_text(encoding="utf-8") == "alpha\nbeta\ngamma\n"
    print("[PASS] dry_run does not write")

    live = run(
        {
            "tool_name": "patch_file",
            "arguments": {
                "path": rel,
                "start_line": 2,
                "end_line": 2,
                "replacement": "BETA\n",
            },
            "dry_run": False,
        },
        registry=registry,
    )
    assert live.ok and target.read_text(encoding="utf-8") == "alpha\nBETA\ngamma\n"
    print("[PASS] line_range patch")

    anchor = run(
        {
            "tool_name": "patch_file",
            "arguments": {
                "path": rel,
                "find": "gamma",
                "replacement": "GAMMA",
            },
            "dry_run": False,
        },
        registry=registry,
    )
    assert anchor.ok and "GAMMA" in target.read_text(encoding="utf-8")
    print("[PASS] find patch")

    dup = run(
        {
            "tool_name": "patch_file",
            "arguments": {"path": rel, "find": "a", "replacement": "x"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert not dup.ok
    print("[PASS] non-unique find rejected")

    target.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
