"""write_text — write UTF-8 text under agent root (TOOLS.md §5, TASKS T-111)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_VALID_CONFLICTS = frozenset({"skip", "rename", "overwrite"})


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
    from paths import AgentPaths, PathDeniedForWriteError, PathOutOfBoundsError

    return AgentPaths, PathDeniedForWriteError, PathOutOfBoundsError


def run_write(payload: dict[str, Any]) -> dict[str, Any]:
    """Core logic: resolve path under agent root and write or simulate."""
    AgentPaths, PathDeniedForWriteError, PathOutOfBoundsError = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    path_arg = payload.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return {"ok": False, "error": "path is required"}

    content = payload.get("content")
    if not isinstance(content, str):
        return {"ok": False, "error": "content is required"}

    on_conflict = payload.get("on_conflict", "skip")
    if not isinstance(on_conflict, str):
        return {"ok": False, "error": "on_conflict must be a string"}
    on_conflict = on_conflict.strip().lower()
    if on_conflict not in _VALID_CONFLICTS:
        return {"ok": False, "error": f"on_conflict must be one of {sorted(_VALID_CONFLICTS)}"}

    dry_run = bool(payload.get("dry_run", False))

    try:
        target = paths.resolve_under_agent_for_write(path_arg, must_exist=False)
    except (PathOutOfBoundsError, PathDeniedForWriteError) as exc:
        return {"ok": False, "error": str(exc)}
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}

    if target.exists() and on_conflict == "skip":
        rel = paths.to_agent_relative(target)
        if dry_run:
            return {"ok": True, "dry_run": True, "skipped": True, "path": rel}
        return {"ok": True, "skipped": True, "path": rel}

    if target.exists() and on_conflict == "rename":
        target = _renamed_target(target)

    rel_written = paths.to_agent_relative(target)

    if dry_run:
        return {"ok": True, "dry_run": True, "would_write": rel_written, "skipped": False}

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    return {"ok": True, "written": rel_written, "skipped": False}


def _renamed_target(target: Path) -> Path:
    parent = target.parent
    stem = target.stem
    suffix = target.suffix
    index = 1
    while True:
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def main() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_write)


def _demo() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from paths import AgentPaths
    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    paths = AgentPaths.discover()
    registry = ToolRegistry.load(paths)
    tool = registry.get_evolved("write_text")
    assert tool is not None and tool.status == "active"
    print("[PASS] registry loads write_text (active)")

    rel = "workspace/_write_text_demo.txt"
    target = paths.agent_root / rel
    if target.exists():
        target.unlink()

    dry = run(
        {
            "tool_name": "write_text",
            "arguments": {"path": rel, "content": "hello"},
            "dry_run": True,
        },
        registry=registry,
    )
    assert dry.ok and dry.data.get("dry_run") is True and not target.exists()
    print("[PASS] dry_run does not write")

    live = run(
        {
            "tool_name": "write_text",
            "arguments": {"path": rel, "content": "hello"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert live.ok and target.read_text(encoding="utf-8") == "hello"
    print("[PASS] live write")

    skip = run(
        {
            "tool_name": "write_text",
            "arguments": {"path": rel, "content": "other", "on_conflict": "skip"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert skip.ok and skip.data.get("skipped") is True and target.read_text(encoding="utf-8") == "hello"
    print("[PASS] on_conflict=skip")

    overwrite = run(
        {
            "tool_name": "write_text",
            "arguments": {"path": rel, "content": "updated", "on_conflict": "overwrite"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert overwrite.ok and target.read_text(encoding="utf-8") == "updated"
    print("[PASS] on_conflict=overwrite")

    rename_path = paths.agent_root / "workspace/_write_text_demo-1.txt"
    if rename_path.exists():
        rename_path.unlink()
    rename = run(
        {
            "tool_name": "write_text",
            "arguments": {"path": rel, "content": "renamed", "on_conflict": "rename"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert rename.ok and rename_path.is_file() and rename_path.read_text(encoding="utf-8") == "renamed"
    print("[PASS] on_conflict=rename")

    bad = run(
        {
            "tool_name": "write_text",
            "arguments": {"path": "../outside.txt", "content": "x"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert not bad.ok
    print("[PASS] path_out_of_bounds rejected")

    deny = run(
        {
            "tool_name": "write_text",
            "arguments": {"path": ".env", "content": "SECRET=bad"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert not deny.ok
    print("[PASS] .env write denied")

    target.unlink(missing_ok=True)
    rename_path.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
