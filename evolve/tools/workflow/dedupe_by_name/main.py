"""dedupe_by_name — report duplicate filenames under workspace (P2 workflow)."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


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


def _iter_files(root: Path, *, recursive: bool, include_hidden: bool) -> list[Path]:
    if recursive:
        candidates = sorted(root.rglob("*"))
    else:
        candidates = sorted(root.iterdir())

    files: list[Path] = []
    for path in candidates:
        if not path.is_file():
            continue
        if not include_hidden and path.name.startswith("."):
            continue
        if not include_hidden and any(
            part.startswith(".") for part in path.relative_to(root).parts[:-1]
        ):
            continue
        files.append(path)
    return files


def run_dedupe(payload: dict[str, Any]) -> dict[str, Any]:
    AgentPaths, PathOutOfBoundsError = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    path_arg = payload.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return {"ok": False, "error": "path is required"}

    recursive = bool(payload.get("recursive", True))
    include_hidden = bool(payload.get("include_hidden", False))
    dry_run = bool(payload.get("dry_run", False))

    try:
        root = paths.resolve_under_workspace(path_arg, must_exist=True)
    except PathOutOfBoundsError as exc:
        return {"ok": False, "error": str(exc)}
    except (TypeError, ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}

    if not root.is_dir():
        return {"ok": False, "error": f"not a directory: {paths.to_workspace_relative(root)}"}

    by_name: dict[str, list[str]] = defaultdict(list)
    for file_path in _iter_files(root, recursive=recursive, include_hidden=include_hidden):
        by_name[file_path.name].append(paths.to_workspace_relative(file_path))

    duplicates = [
        {"name": name, "paths": sorted(paths_list)}
        for name, paths_list in sorted(by_name.items())
        if len(paths_list) > 1
    ]

    result: dict[str, Any] = {
        "ok": True,
        "source_dir": paths.to_workspace_relative(root),
        "duplicate_groups": len(duplicates),
        "duplicates": duplicates,
    }
    if dry_run:
        result["dry_run"] = True
    return result


def main() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_dedupe)


def _demo() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from paths import AgentPaths
    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    paths = AgentPaths.discover()
    registry = ToolRegistry.load(paths)
    tool = registry.get_evolved("dedupe_by_name")
    assert tool is not None and tool.scope == "workflow"
    print("[PASS] registry loads dedupe_by_name (workflow, active)")

    demo_dir = paths.workspace / "_dedupe_demo"
    if demo_dir.exists():
        for child in sorted(demo_dir.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
    demo_dir.mkdir(parents=True, exist_ok=True)
    (demo_dir / "dup.txt").write_text("a", encoding="utf-8")
    nested = demo_dir / "sub"
    nested.mkdir(parents=True)
    (nested / "dup.txt").write_text("b", encoding="utf-8")
    (nested / "unique.txt").write_text("c", encoding="utf-8")
    rel = paths.to_workspace_relative(demo_dir)

    report = run(
        {"tool_name": "dedupe_by_name", "arguments": {"path": rel}, "dry_run": False},
        registry=registry,
    )
    assert report.ok and report.data.get("duplicate_groups") == 1
    group = report.data["duplicates"][0]
    assert group["name"] == "dup.txt" and len(group["paths"]) == 2
    print("[PASS] finds duplicate basename groups")

    none = run(
        {
            "tool_name": "dedupe_by_name",
            "arguments": {"path": rel, "recursive": False},
            "dry_run": False,
        },
        registry=registry,
    )
    assert none.ok and none.data.get("duplicate_groups") == 0
    print("[PASS] recursive=false scans top level only")

    bad = run(
        {"tool_name": "dedupe_by_name", "arguments": {"path": "../outside"}},
        registry=registry,
    )
    assert not bad.ok
    print("[PASS] path_out_of_bounds rejected")

    for child in sorted(demo_dir.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            child.rmdir()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
