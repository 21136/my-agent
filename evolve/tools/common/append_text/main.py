"""append_text — append UTF-8 text under agent root (P1 common)."""

from __future__ import annotations

import json
import sys
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
    from paths import AgentPaths, PathDeniedForWriteError, PathOutOfBoundsError

    return AgentPaths, PathDeniedForWriteError, PathOutOfBoundsError


def run_append(payload: dict[str, Any]) -> dict[str, Any]:
    """Append *content* to a text file under agent root."""
    AgentPaths, PathDeniedForWriteError, PathOutOfBoundsError = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    path_arg = payload.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return {"ok": False, "error": "path is required"}

    content = payload.get("content")
    if not isinstance(content, str):
        return {"ok": False, "error": "content is required"}

    create_if_missing = bool(payload.get("create_if_missing", True))
    separator = payload.get("separator", "\n")
    if not isinstance(separator, str):
        return {"ok": False, "error": "separator must be a string"}

    dry_run = bool(payload.get("dry_run", False))

    try:
        target = paths.resolve_under_agent_for_write(path_arg, must_exist=False)
    except (PathOutOfBoundsError, PathDeniedForWriteError) as exc:
        return {"ok": False, "error": str(exc)}
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}

    rel = paths.to_agent_relative(target)
    created = not target.exists()

    if created and not create_if_missing:
        return {"ok": False, "error": f"file does not exist: {rel}"}

    if target.exists() and target.is_dir():
        return {"ok": False, "error": f"not a file: {rel}"}

    if dry_run:
        result: dict[str, Any] = {
            "ok": True,
            "dry_run": True,
            "path": rel,
            "created": created,
            "bytes_appended": len(content.encode("utf-8")),
        }
        if not created and separator and target.read_text(encoding="utf-8"):
            result["bytes_appended"] += len(separator.encode("utf-8"))
        return result

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if created:
            target.write_text(content, encoding="utf-8")
            written = content
        else:
            existing = target.read_text(encoding="utf-8")
            prefix = separator if existing and separator else ""
            written = existing + prefix + content
            target.write_text(written, encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "path": rel,
        "created": created,
        "bytes_appended": len(content.encode("utf-8")),
    }


def main() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_append)


def _demo() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from paths import AgentPaths
    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    paths = AgentPaths.discover()
    registry = ToolRegistry.load(paths)
    tool = registry.get_evolved("append_text")
    assert tool is not None and tool.status == "active"
    print("[PASS] registry loads append_text (active)")

    rel = "workspace/_append_text_demo.txt"
    target = paths.agent_root / rel
    target.unlink(missing_ok=True)

    create = run(
        {
            "tool_name": "append_text",
            "arguments": {"path": rel, "content": "line1"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert create.ok and target.read_text(encoding="utf-8") == "line1"
    print("[PASS] append creates file")

    append = run(
        {
            "tool_name": "append_text",
            "arguments": {"path": rel, "content": "line2"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert append.ok and target.read_text(encoding="utf-8") == "line1\nline2"
    print("[PASS] append with default separator")

    dry = run(
        {
            "tool_name": "append_text",
            "arguments": {"path": rel, "content": "line3"},
            "dry_run": True,
        },
        registry=registry,
    )
    assert dry.ok and target.read_text(encoding="utf-8") == "line1\nline2"
    print("[PASS] dry_run does not write")

    missing = run(
        {
            "tool_name": "append_text",
            "arguments": {
                "path": "workspace/_append_missing.txt",
                "content": "x",
                "create_if_missing": False,
            },
            "dry_run": False,
        },
        registry=registry,
    )
    assert not missing.ok
    print("[PASS] create_if_missing=false rejects missing file")

    bad = run(
        {
            "tool_name": "append_text",
            "arguments": {"path": "../outside.txt", "content": "x"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert not bad.ok
    print("[PASS] path_out_of_bounds rejected")

    deny = run(
        {
            "tool_name": "append_text",
            "arguments": {"path": ".git/config", "content": "bad"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert not deny.ok
    print("[PASS] .git/ write denied")

    target.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
