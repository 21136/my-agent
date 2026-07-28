"""copy_move — copy or move paths within workspace (P1 common)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

_VALID_CONFLICTS = frozenset({"skip", "overwrite", "rename"})
_VALID_OPS = frozenset({"copy", "move"})


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
    stem = target.stem
    suffix = target.suffix
    index = 1
    while True:
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _resolve_dest(
    source: Path,
    dest: Path,
    *,
    on_conflict: str,
) -> tuple[Path | None, bool]:
    """Return (resolved_dest, skipped). skipped=True means no-op."""
    if not dest.exists():
        return dest, False

    if source.is_dir() and dest.is_dir():
        if on_conflict == "skip":
            return None, True
        if on_conflict == "overwrite":
            return dest, False
        return _renamed_target(dest.parent / source.name), False

    if dest.is_dir():
        candidate = dest / source.name
        if not candidate.exists():
            return candidate, False
        dest = candidate

    if on_conflict == "skip":
        return None, True
    if on_conflict == "rename":
        return _renamed_target(dest), False
    return dest, False


def run_copy_move(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy or move *source* to *dest* under workspace."""
    AgentPaths, PathOutOfBoundsError = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    operation = payload.get("operation")
    if not isinstance(operation, str) or operation.strip().lower() not in _VALID_OPS:
        return {"ok": False, "error": f"operation must be one of {sorted(_VALID_OPS)}"}
    operation = operation.strip().lower()

    source_arg = payload.get("source")
    dest_arg = payload.get("dest")
    if not isinstance(source_arg, str) or not source_arg.strip():
        return {"ok": False, "error": "source is required"}
    if not isinstance(dest_arg, str) or not dest_arg.strip():
        return {"ok": False, "error": "dest is required"}

    on_conflict = payload.get("on_conflict", "skip")
    if not isinstance(on_conflict, str):
        return {"ok": False, "error": "on_conflict must be a string"}
    on_conflict = on_conflict.strip().lower()
    if on_conflict not in _VALID_CONFLICTS:
        return {"ok": False, "error": f"on_conflict must be one of {sorted(_VALID_CONFLICTS)}"}

    dry_run = bool(payload.get("dry_run", False))

    try:
        source = paths.resolve_under_workspace(source_arg, must_exist=True)
        dest = paths.resolve_under_workspace(dest_arg, must_exist=False)
    except PathOutOfBoundsError as exc:
        return {"ok": False, "error": str(exc)}
    except (TypeError, ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}

    if source.resolve() == dest.resolve():
        return {"ok": False, "error": "source and dest must differ"}

    # Prevent moving/copying a directory into itself
    if source.is_dir():
        try:
            dest.resolve().relative_to(source.resolve())
            return {"ok": False, "error": "dest cannot be inside source"}
        except ValueError:
            pass

    resolved_dest, skipped = _resolve_dest(source, dest, on_conflict=on_conflict)
    rel_source = paths.to_workspace_relative(source)
    if skipped or resolved_dest is None:
        return {
            "ok": True,
            "operation": operation,
            "source": rel_source,
            "dest": paths.to_workspace_relative(dest),
            "skipped": True,
        }

    rel_dest = paths.to_workspace_relative(resolved_dest)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "operation": operation,
            "source": rel_source,
            "dest": rel_dest,
            "skipped": False,
        }

    try:
        resolved_dest.parent.mkdir(parents=True, exist_ok=True)
        if operation == "copy":
            if source.is_dir():
                if resolved_dest.exists() and on_conflict == "overwrite":
                    shutil.rmtree(resolved_dest)
                shutil.copytree(source, resolved_dest)
            else:
                shutil.copy2(source, resolved_dest)
        else:
            if resolved_dest.exists() and on_conflict == "overwrite":
                if resolved_dest.is_dir():
                    shutil.rmtree(resolved_dest)
                else:
                    resolved_dest.unlink()
            shutil.move(str(source), str(resolved_dest))
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "operation": operation,
        "source": rel_source,
        "dest": rel_dest,
        "skipped": False,
    }


def main() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_copy_move)


def _demo() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from paths import AgentPaths
    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    paths = AgentPaths.discover()
    registry = ToolRegistry.load(paths)
    tool = registry.get_evolved("copy_move")
    assert tool is not None and tool.status == "active"
    print("[PASS] registry loads copy_move (active)")

    src = paths.workspace / "_copy_move_src.txt"
    dst = paths.workspace / "_copy_move_dst.txt"
    src.write_text("hello", encoding="utf-8")
    dst.unlink(missing_ok=True)

    dry = run(
        {
            "tool_name": "copy_move",
            "arguments": {
                "operation": "copy",
                "source": "_copy_move_src.txt",
                "dest": "_copy_move_dst.txt",
            },
            "dry_run": True,
        },
        registry=registry,
    )
    assert dry.ok and not dst.exists()
    print("[PASS] dry_run copy does not write")

    copied = run(
        {
            "tool_name": "copy_move",
            "arguments": {
                "operation": "copy",
                "source": "_copy_move_src.txt",
                "dest": "_copy_move_dst.txt",
            },
            "dry_run": False,
        },
        registry=registry,
    )
    assert copied.ok and dst.read_text(encoding="utf-8") == "hello"
    print("[PASS] live copy")

    skip = run(
        {
            "tool_name": "copy_move",
            "arguments": {
                "operation": "copy",
                "source": "_copy_move_src.txt",
                "dest": "_copy_move_dst.txt",
                "on_conflict": "skip",
            },
            "dry_run": False,
        },
        registry=registry,
    )
    assert skip.ok and skip.data.get("skipped") is True
    print("[PASS] on_conflict=skip")

    moved = run(
        {
            "tool_name": "copy_move",
            "arguments": {
                "operation": "move",
                "source": "_copy_move_src.txt",
                "dest": "_copy_move_moved.txt",
            },
            "dry_run": False,
        },
        registry=registry,
    )
    assert moved.ok and not src.exists()
    assert (paths.workspace / "_copy_move_moved.txt").read_text(encoding="utf-8") == "hello"
    print("[PASS] live move")

    bad = run(
        {
            "tool_name": "copy_move",
            "arguments": {
                "operation": "copy",
                "source": "../outside.txt",
                "dest": "_x.txt",
            },
            "dry_run": False,
        },
        registry=registry,
    )
    assert not bad.ok
    print("[PASS] path_out_of_bounds rejected")

    dst.unlink(missing_ok=True)
    (paths.workspace / "_copy_move_moved.txt").unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
