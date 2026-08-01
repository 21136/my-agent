"""archive_by_date — move top-level files into YYYY-MM folders by date (P2 workflow)."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_VALID_DATE_FIELDS = frozenset({"mtime", "ctime"})


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


def _file_timestamp(path: Path, *, date_field: str) -> float:
    stat = path.stat()
    if date_field == "ctime":
        return stat.st_ctime
    return stat.st_mtime


def run_archive(payload: dict[str, Any]) -> dict[str, Any]:
    AgentPaths, PathOutOfBoundsError = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    path_arg = payload.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return {"ok": False, "error": "path is required"}

    date_field = payload.get("date_field", "mtime")
    if not isinstance(date_field, str) or date_field.strip().lower() not in _VALID_DATE_FIELDS:
        return {"ok": False, "error": f"date_field must be one of {sorted(_VALID_DATE_FIELDS)}"}
    date_field = date_field.strip().lower()

    folder_format = payload.get("folder_format", "%Y-%m")
    if not isinstance(folder_format, str) or not folder_format.strip():
        return {"ok": False, "error": "folder_format must be a non-empty string"}

    include_hidden = bool(payload.get("include_hidden", False))
    dry_run = bool(payload.get("dry_run", False))

    try:
        source_dir = paths.resolve_under_agent(path_arg, must_exist=True)
    except PathOutOfBoundsError as exc:
        return {"ok": False, "error": str(exc)}
    except (TypeError, ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}

    if not source_dir.is_dir():
        return {"ok": False, "error": f"not a directory: {paths.to_agent_relative(source_dir)}"}

    rel_source = paths.to_agent_relative(source_dir)
    moved: list[dict[str, str]] = []

    for item in sorted(source_dir.iterdir(), key=lambda p: p.name.lower()):
        if not item.is_file():
            continue
        if not include_hidden and item.name.startswith("."):
            continue

        try:
            stamp = _file_timestamp(item, date_field=date_field)
            folder_name = datetime.fromtimestamp(stamp).strftime(folder_format)
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

        target_dir = source_dir / folder_name
        target_path = _unique_target(target_dir / item.name)
        rel_from = paths.to_agent_relative(item)
        rel_to = paths.to_agent_relative(target_path)
        if rel_from == rel_to:
            continue

        moved.append({"from": rel_from, "to": rel_to, "folder": folder_name})

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
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_archive)


def _demo() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from paths import AgentPaths
    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    paths = AgentPaths.discover()
    registry = ToolRegistry.load(paths)
    tool = registry.get_evolved("archive_by_date")
    assert tool is not None and tool.scope == "workflow"
    print("[PASS] registry loads archive_by_date (workflow, active)")

    demo_dir = paths.workspace / "_archive_demo"
    demo_dir.mkdir(parents=True, exist_ok=True)
    for child in demo_dir.rglob("*"):
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    sample = demo_dir / "report.pdf"
    sample.write_text("pdf", encoding="utf-8")
    rel = paths.to_agent_relative(demo_dir)

    dry = run(
        {
            "tool_name": "archive_by_date",
            "arguments": {"path": rel},
            "dry_run": True,
        },
        registry=registry,
    )
    assert dry.ok and dry.data.get("count") == 1
    assert sample.is_file()
    print("[PASS] dry_run plans archive")

    live = run(
        {"tool_name": "archive_by_date", "arguments": {"path": rel}, "dry_run": False},
        registry=registry,
    )
    assert live.ok and not sample.is_file()
    archived = next(demo_dir.glob("*/*.pdf"))
    assert archived.is_file()
    print("[PASS] live archive by mtime")

    bad = run(
        {"tool_name": "archive_by_date", "arguments": {"path": "../outside"}, "dry_run": False},
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
