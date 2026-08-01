"""flatten_dir — hoist nested files to the top of a workspace directory (P2 workflow)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_VALID_CONFLICTS = frozenset({"skip", "rename"})


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


def _unique_target(target: Path) -> Path:
    if not target.exists():
        return target
    parent = target.parent
    stem = target.stem
    suffix = target.suffix
    index = 1
    while True:
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _iter_nested_files(root: Path, *, include_hidden: bool) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.parent.resolve() == root.resolve():
            continue
        if not include_hidden and path.name.startswith("."):
            continue
        if any(part.startswith(".") and part != "." for part in path.relative_to(root).parts[:-1]):
            if not include_hidden:
                continue
        files.append(path)
    return files


def _remove_empty_dirs(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir() and path.resolve() != root.resolve():
            try:
                path.rmdir()
            except OSError:
                pass


def run_flatten(payload: dict[str, Any]) -> dict[str, Any]:
    AgentPaths, PathOutOfBoundsError = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    path_arg = payload.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return {"ok": False, "error": "path is required"}

    on_conflict = payload.get("on_conflict", "rename")
    if not isinstance(on_conflict, str):
        return {"ok": False, "error": "on_conflict must be a string"}
    on_conflict = on_conflict.strip().lower()
    if on_conflict not in _VALID_CONFLICTS:
        return {"ok": False, "error": f"on_conflict must be one of {sorted(_VALID_CONFLICTS)}"}

    include_hidden = bool(payload.get("include_hidden", False))
    remove_empty = bool(payload.get("remove_empty_dirs", True))
    dry_run = bool(payload.get("dry_run", False))

    try:
        root = paths.resolve_under_agent(path_arg, must_exist=True)
    except PathOutOfBoundsError as exc:
        return {"ok": False, "error": str(exc)}
    except (TypeError, ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}

    if not root.is_dir():
        return {"ok": False, "error": f"not a directory: {paths.to_agent_relative(root)}"}

    moved: list[dict[str, str]] = []
    for source in _iter_nested_files(root, include_hidden=include_hidden):
        target = root / source.name
        if target.resolve() == source.resolve():
            continue
        if target.exists():
            if on_conflict == "skip":
                continue
            target = _unique_target(target)

        rel_from = paths.to_agent_relative(source)
        rel_to = paths.to_agent_relative(target)
        moved.append({"from": rel_from, "to": rel_to})

        if dry_run:
            continue

        try:
            source.rename(target)
        except OSError as exc:
            return {"ok": False, "error": str(exc), "partial": moved}

    if not dry_run and remove_empty:
        _remove_empty_dirs(root)

    result: dict[str, Any] = {
        "ok": True,
        "source_dir": paths.to_agent_relative(root),
        "count": len(moved),
        "moved": moved,
    }
    if dry_run:
        result["dry_run"] = True
    return result


def main() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_flatten)


def _demo() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from paths import AgentPaths
    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    paths = AgentPaths.discover()
    registry = ToolRegistry.load(paths)
    tool = registry.get_evolved("flatten_dir")
    assert tool is not None and tool.scope == "workflow"
    print("[PASS] registry loads flatten_dir (workflow, active)")

    demo_dir = paths.workspace / "_flatten_demo"
    nested = demo_dir / "sub" / "deep"
    nested.mkdir(parents=True, exist_ok=True)
    for child in demo_dir.rglob("*"):
        if child.is_file():
            child.unlink()
    (nested / "a.txt").write_text("a", encoding="utf-8")
    (nested / "b.txt").write_text("b", encoding="utf-8")
    rel = paths.to_agent_relative(demo_dir)

    dry = run(
        {
            "tool_name": "flatten_dir",
            "arguments": {"path": rel},
            "dry_run": True,
        },
        registry=registry,
    )
    assert dry.ok and dry.data.get("count") == 2
    assert (nested / "a.txt").is_file()
    print("[PASS] dry_run plans flatten")

    live = run(
        {"tool_name": "flatten_dir", "arguments": {"path": rel}, "dry_run": False},
        registry=registry,
    )
    assert live.ok and (demo_dir / "a.txt").is_file() and (demo_dir / "b.txt").is_file()
    assert not nested.exists()
    print("[PASS] live flatten + remove empty dirs")

    bad = run(
        {"tool_name": "flatten_dir", "arguments": {"path": "../outside"}, "dry_run": False},
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
