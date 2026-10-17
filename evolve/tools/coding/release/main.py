"""Controlled local release artifact tool."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def _agent_root() -> Path:
    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        marker = directory / "evolve"
        if (marker / "_index.core.toml").is_file() or (marker / "_index.toml").is_file():
            return directory
    raise RuntimeError("could not locate agent root")


def _load_core():
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from paths import AgentPaths
    from release_manager import build_artifact
    return AgentPaths, build_artifact


def run_release(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip().lower()
    if action != "build":
        return {"ok": False, "error": "action must be build"}
    version = str(payload.get("version") or "").strip()
    if not version:
        return {"ok": False, "error": "version is required"}
    AgentPaths, build_artifact = _load_core()
    paths = AgentPaths.discover(start=_agent_root())
    try:
        root = paths.resolve_under_agent(str(payload.get("working_dir") or "").replace("\\", "/"), must_exist=True) if payload.get("working_dir") else paths.agent_root
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    output_raw = str(payload.get("output") or f"{version}.zip").strip()
    output = (paths.data / "releases" / output_raw).resolve()
    try:
        output.relative_to((paths.data / "releases").resolve())
    except ValueError:
        return {"ok": False, "error": "output must stay under data/releases"}
    if bool(payload.get("dry_run", False)):
        files = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())
        return {"ok": True, "action": action, "dry_run": True, "root": str(root), "output": str(output), "version": version, "files": files}
    try:
        artifact = build_artifact(root, output, version=version)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "action": action, "version": artifact.version, "path": artifact.path, "digest": artifact.digest, "files": list(artifact.files)}


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_release)


if __name__ == "__main__":
    main()
