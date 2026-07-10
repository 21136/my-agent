"""sort_by_extension — organize workspace files into extension subfolders (T-502)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_NO_EXT_FOLDER = "_no_ext"


def _agent_root() -> Path:
    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        if (directory / "evolve" / "_index.toml").is_file():
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


def _extension_folder(path: Path) -> str:
    suffix = path.suffix
    if not suffix:
        return _NO_EXT_FOLDER
    return suffix.lstrip(".").lower() or _NO_EXT_FOLDER


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


def run_sort(payload: dict[str, Any]) -> dict[str, Any]:
    """Move files directly under ``path`` into ``<ext>/`` subfolders."""
    AgentPaths, PathOutOfBoundsError = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    path_arg = payload.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return {"ok": False, "error": "path is required"}

    include_hidden = bool(payload.get("include_hidden", False))
    dry_run = bool(payload.get("dry_run", False))

    try:
        source_dir = paths.resolve_under_workspace(path_arg, must_exist=True)
    except PathOutOfBoundsError as exc:
        return {"ok": False, "error": str(exc)}
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}

    if not source_dir.is_dir():
        return {"ok": False, "error": f"not a directory: {paths.to_workspace_relative(source_dir)}"}

    rel_source = paths.to_workspace_relative(source_dir)
    moved: list[dict[str, str]] = []

    for item in sorted(source_dir.iterdir(), key=lambda p: p.name.lower()):
        if not item.is_file():
            continue
        if not include_hidden and item.name.startswith("."):
            continue

        ext_folder = _extension_folder(item)
        target_dir = source_dir / ext_folder
        target_path = _unique_target(target_dir / item.name)
        rel_from = paths.to_workspace_relative(item)
        rel_to = paths.to_workspace_relative(target_path)

        if rel_from == rel_to:
            continue

        moved.append({"from": rel_from, "to": rel_to})

        if dry_run:
            continue

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            item.rename(target_path)
        except OSError as exc:
            return {"ok": False, "error": str(exc), "partial": moved}

    result: dict[str, Any] = {
        "ok": True,
        "source_dir": rel_source,
        "count": len(moved),
        "moved": moved,
    }
    if dry_run:
        result["dry_run"] = True
    return result


def main() -> None:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        print(json.dumps({"ok": False, "error": "stdin must be a JSON object"}, ensure_ascii=False))
        raise SystemExit(1)
    result = run_sort(payload)
    print(json.dumps(result, ensure_ascii=False))
    if result.get("ok") is False:
        raise SystemExit(1)


def _demo() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from paths import AgentPaths
    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    paths = AgentPaths.discover()
    registry = ToolRegistry.load(paths)
    tool = registry.get_evolved("sort_by_extension")
    assert tool is not None and tool.status == "active"
    assert tool.scope == "workflow"
    print("[PASS] registry loads sort_by_extension (workflow, active)")

    demo_dir = paths.workspace / "_sort_by_ext_demo"
    if demo_dir.exists():
        for child in sorted(demo_dir.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
    demo_dir.mkdir(parents=True)
    (demo_dir / "report.pdf").write_text("pdf", encoding="utf-8")
    (demo_dir / "notes.txt").write_text("txt", encoding="utf-8")
    (demo_dir / "README").write_text("no ext", encoding="utf-8")
    (demo_dir / ".hidden").write_text("skip", encoding="utf-8")

    rel = paths.to_workspace_relative(demo_dir)

    dry = run(
        {
            "tool_name": "sort_by_extension",
            "arguments": {"path": rel},
            "dry_run": True,
        },
        registry=registry,
    )
    assert dry.ok and dry.data.get("dry_run") is True and dry.data.get("count") == 3
    assert (demo_dir / "report.pdf").is_file()
    print("[PASS] dry_run plans moves without writing")

    live = run(
        {
            "tool_name": "sort_by_extension",
            "arguments": {"path": rel},
            "dry_run": False,
        },
        registry=registry,
    )
    assert live.ok and live.data.get("count") == 3
    assert (demo_dir / "pdf" / "report.pdf").read_text(encoding="utf-8") == "pdf"
    assert (demo_dir / "txt" / "notes.txt").read_text(encoding="utf-8") == "txt"
    assert (demo_dir / _NO_EXT_FOLDER / "README").read_text(encoding="utf-8") == "no ext"
    assert (demo_dir / ".hidden").is_file()
    print("[PASS] live sort by extension")

    hidden = run(
        {
            "tool_name": "sort_by_extension",
            "arguments": {"path": rel, "include_hidden": True},
            "dry_run": False,
        },
        registry=registry,
    )
    assert hidden.ok and hidden.data.get("count") == 1
    assert (demo_dir / _NO_EXT_FOLDER / ".hidden").is_file()
    print("[PASS] include_hidden moves dotfiles")

    bad = run(
        {
            "tool_name": "sort_by_extension",
            "arguments": {"path": "../outside"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert not bad.ok
    print("[PASS] path_out_of_bounds rejected")

    for child in sorted(demo_dir.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    demo_dir.rmdir()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
