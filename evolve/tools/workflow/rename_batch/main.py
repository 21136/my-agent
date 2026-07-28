"""rename_batch — batch rename top-level files in a workspace directory (P2 workflow)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_VALID_MODES = frozenset({"prefix", "suffix", "replace", "number"})


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


def _new_name(item: Path, *, mode: str, payload: dict[str, Any], index: int) -> str | None:
    name = item.name
    stem = item.stem
    suffix = item.suffix

    if mode == "prefix":
        prefix = payload.get("prefix")
        if not isinstance(prefix, str):
            return None
        return f"{prefix}{name}"

    if mode == "suffix":
        suffix_add = payload.get("suffix")
        if not isinstance(suffix_add, str):
            return None
        return f"{stem}{suffix_add}{suffix}"

    if mode == "replace":
        find = payload.get("find")
        replace = payload.get("replace", "")
        if not isinstance(find, str) or not find:
            return None
        if not isinstance(replace, str):
            return None
        return name.replace(find, replace)

    if mode == "number":
        number_stem = payload.get("number_stem")
        start = int(payload.get("number_start", 1))
        pad = int(payload.get("number_pad", 3))
        if pad < 1:
            pad = 1
        base = number_stem if isinstance(number_stem, str) and number_stem else stem
        return f"{base}{str(start + index).zfill(pad)}{suffix}"

    return None


def _load_workflow():
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from host_scope import (
        HostPathDeniedError,
        HostRootNotFoundError,
        HostScopeConfigError,
        HostScopePermissionError,
    )
    from host_tools import resolve_workflow_dir
    from paths import AgentPaths, PathOutOfBoundsError

    host_errors = (
        HostPathDeniedError,
        HostRootNotFoundError,
        HostScopeConfigError,
        HostScopePermissionError,
    )
    return AgentPaths, PathOutOfBoundsError, resolve_workflow_dir, host_errors


def run_rename(payload: dict[str, Any]) -> dict[str, Any]:
    AgentPaths, PathOutOfBoundsError, resolve_workflow_dir, host_errors = _load_workflow()
    paths = AgentPaths.discover(start=_agent_root())

    path_arg = payload.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return {"ok": False, "error": "path is required"}

    mode = payload.get("mode")
    if not isinstance(mode, str) or mode.strip().lower() not in _VALID_MODES:
        return {"ok": False, "error": f"mode must be one of {sorted(_VALID_MODES)}"}
    mode = mode.strip().lower()

    include_hidden = bool(payload.get("include_hidden", False))
    dry_run = bool(payload.get("dry_run", False))

    try:
        wf = resolve_workflow_dir(paths, path_arg, write=True)
    except PathOutOfBoundsError as exc:
        return {"ok": False, "error": str(exc)}
    except host_errors as exc:
        return {"ok": False, "error": str(exc)}
    except (TypeError, ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}

    source_dir = wf.absolute

    files = [
        item
        for item in sorted(source_dir.iterdir(), key=lambda p: p.name.lower())
        if item.is_file() and (include_hidden or not item.name.startswith("."))
    ]

    renamed: list[dict[str, str]] = []
    planned_targets: set[str] = set()

    for index, item in enumerate(files):
        new_name = _new_name(item, mode=mode, payload=payload, index=index)
        if new_name is None:
            return {"ok": False, "error": f"invalid arguments for mode={mode}"}
        if new_name == item.name:
            continue

        target = _unique_target(source_dir / new_name)
        if target.name in planned_targets:
            target = _unique_target(target)
        planned_targets.add(target.name)

        rel_from = wf.display_path(item)
        rel_to = wf.display_path(target)
        if rel_from == rel_to:
            continue

        renamed.append({"from": rel_from, "to": rel_to})

        if dry_run:
            continue

        try:
            item.rename(target)
        except OSError as exc:
            return {"ok": False, "error": str(exc), "partial": renamed}

    result: dict[str, Any] = {
        "ok": True,
        "source_dir": wf.label,
        "count": len(renamed),
        "renamed": renamed,
    }
    result.update(wf.log_fields())
    if dry_run:
        result["dry_run"] = True
    return result


def main() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_rename)


def _demo() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from paths import AgentPaths
    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    paths = AgentPaths.discover()
    registry = ToolRegistry.load(paths)
    tool = registry.get_evolved("rename_batch")
    assert tool is not None and tool.status == "active" and tool.scope == "workflow"
    print("[PASS] registry loads rename_batch (workflow, active)")

    demo_dir = paths.workspace / "_rename_batch_demo"
    demo_dir.mkdir(parents=True, exist_ok=True)
    for child in demo_dir.iterdir():
        if child.is_file():
            child.unlink()
    (demo_dir / "photo.jpg").write_text("a", encoding="utf-8")
    (demo_dir / "photo2.jpg").write_text("b", encoding="utf-8")
    rel = paths.to_workspace_relative(demo_dir)

    dry = run(
        {
            "tool_name": "rename_batch",
            "arguments": {"path": rel, "mode": "prefix", "prefix": "vacation_"},
            "dry_run": True,
        },
        registry=registry,
    )
    assert dry.ok and dry.data.get("count") == 2
    assert (demo_dir / "photo.jpg").is_file()
    print("[PASS] dry_run plans renames")

    live = run(
        {
            "tool_name": "rename_batch",
            "arguments": {"path": rel, "mode": "prefix", "prefix": "vacation_"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert live.ok and (demo_dir / "vacation_photo.jpg").is_file()
    print("[PASS] prefix rename")

    number = run(
        {
            "tool_name": "rename_batch",
            "arguments": {
                "path": rel,
                "mode": "number",
                "number_stem": "img_",
                "number_start": 1,
                "number_pad": 2,
            },
            "dry_run": False,
        },
        registry=registry,
    )
    assert number.ok and (demo_dir / "img_01.jpg").is_file()
    print("[PASS] number rename")

    bad = run(
        {
            "tool_name": "rename_batch",
            "arguments": {"path": "../outside", "mode": "prefix", "prefix": "x_"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert not bad.ok
    print("[PASS] path_out_of_bounds rejected")

    for child in demo_dir.iterdir():
        child.unlink(missing_ok=True)
    demo_dir.rmdir()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
