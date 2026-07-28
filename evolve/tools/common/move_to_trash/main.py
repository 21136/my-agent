"""move_to_trash — move workspace paths into a trash folder (P1 common)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

_DEFAULT_TRASH = "_trash"


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


def _renamed_target(target: Path) -> Path:
    parent = target.parent
    stem = target.stem if target.suffix else target.name
    suffix = target.suffix
    index = 1
    while True:
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _trash_destination(trash_root: Path, source: Path) -> Path:
    """Place under trash_root preserving basename; rename on conflict."""
    candidate = trash_root / source.name
    if not candidate.exists():
        return candidate
    return _renamed_target(candidate)


def run_trash(payload: dict[str, Any]) -> dict[str, Any]:
    """Move *path* under workspace into trash_dir."""
    AgentPaths, PathOutOfBoundsError = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    path_arg = payload.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return {"ok": False, "error": "path is required"}

    trash_dir_arg = payload.get("trash_dir", _DEFAULT_TRASH)
    if not isinstance(trash_dir_arg, str) or not trash_dir_arg.strip():
        return {"ok": False, "error": "trash_dir must be a non-empty string"}

    dry_run = bool(payload.get("dry_run", False))

    try:
        source = paths.resolve_under_workspace(path_arg, must_exist=True)
        trash_root = paths.resolve_under_workspace(trash_dir_arg, must_exist=False)
    except PathOutOfBoundsError as exc:
        return {"ok": False, "error": str(exc)}
    except (TypeError, ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}

    rel_source = paths.to_workspace_relative(source)

    if source.resolve() == trash_root.resolve():
        return {"ok": False, "error": "cannot trash the trash directory itself"}

    try:
        source.resolve().relative_to(trash_root.resolve())
        return {"ok": False, "error": "path is already under trash_dir"}
    except ValueError:
        pass

    target = _trash_destination(trash_root, source)
    rel_trash = paths.to_workspace_relative(target)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "path": rel_source,
            "trash_path": rel_trash,
            "skipped": False,
        }

    try:
        trash_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "path": rel_source,
        "trash_path": rel_trash,
        "skipped": False,
    }


def main() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_trash)


def _demo() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from paths import AgentPaths
    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    paths = AgentPaths.discover()
    registry = ToolRegistry.load(paths)
    tool = registry.get_evolved("move_to_trash")
    assert tool is not None and tool.status == "active"
    print("[PASS] registry loads move_to_trash (active)")

    rel = "_trash_demo.txt"
    source = paths.workspace / rel
    source.write_text("delete me", encoding="utf-8")
    trash_file = paths.workspace / "_trash" / rel

    dry = run(
        {
            "tool_name": "move_to_trash",
            "arguments": {"path": rel},
            "dry_run": True,
        },
        registry=registry,
    )
    assert dry.ok and source.is_file()
    print("[PASS] dry_run does not move")

    live = run(
        {
            "tool_name": "move_to_trash",
            "arguments": {"path": rel},
            "dry_run": False,
        },
        registry=registry,
    )
    assert live.ok and not source.exists() and trash_file.is_file()
    assert trash_file.read_text(encoding="utf-8") == "delete me"
    print("[PASS] live move to _trash")

    trash_file.unlink()
    (paths.workspace / "_trash").rmdir()

    nested = paths.workspace / "_trash_demo_dir"
    nested.mkdir(exist_ok=True)
    (nested / "a.txt").write_text("a", encoding="utf-8")

    dir_live = run(
        {
            "tool_name": "move_to_trash",
            "arguments": {"path": "_trash_demo_dir"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert dir_live.ok and not nested.exists()
    assert (paths.workspace / "_trash" / "_trash_demo_dir" / "a.txt").is_file()
    print("[PASS] directory moved to trash")

    shutil.rmtree(paths.workspace / "_trash")

    bad = run(
        {
            "tool_name": "move_to_trash",
            "arguments": {"path": "../outside.txt"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert not bad.ok
    print("[PASS] path_out_of_bounds rejected")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
