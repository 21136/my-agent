"""Controlled local deployment wrapper over run_service."""

from __future__ import annotations

import importlib.util
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
    from release_manager import verify_artifact
    return AgentPaths, verify_artifact


def _load_run_service():
    main_path = _agent_root() / "evolve" / "tools" / "common" / "run_service" / "main.py"
    spec = importlib.util.spec_from_file_location("run_service_for_deploy", main_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load run_service")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_service


def run_deploy(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip().lower()
    if action not in {"plan", "start", "status", "health", "stop", "rollback"}:
        return {"ok": False, "error": "action must be plan|start|status|health|stop|rollback"}
    name = str(payload.get("service_name") or "").strip()
    if not name:
        return {"ok": False, "error": "service_name is required"}
    dry_run = bool(payload.get("dry_run", True))
    artifact_path = payload.get("artifact_path")
    artifact_digest = payload.get("artifact_digest")
    artifact_verified = None
    if artifact_path or artifact_digest:
        if not isinstance(artifact_path, str) or not artifact_path.strip() or not isinstance(artifact_digest, str) or len(artifact_digest.strip()) != 64:
            return {"ok": False, "error": "artifact_path and 64-character artifact_digest are required together"}
        AgentPaths, verify_artifact = _load_core()
        paths = AgentPaths.discover(start=_agent_root())
        try:
            candidate = paths.resolve_under_agent(artifact_path.replace("\\", "/"), must_exist=True)
        except Exception:
            candidate = (paths.data / "releases" / artifact_path).resolve()
        try:
            candidate.relative_to(paths.agent_root.resolve())
        except ValueError:
            return {"ok": False, "error": "artifact_path is outside Agent root"}
        artifact_verified = verify_artifact(candidate, artifact_digest)
        if not artifact_verified:
            return {"ok": False, "error": "artifact digest mismatch"}
    command = payload.get("command")
    if command is not None and (not isinstance(command, list) or not all(isinstance(item, str) for item in command)):
        return {"ok": False, "error": "command must be an array of strings"}
    if action == "plan":
        return {"ok": True, "action": action, "dry_run": True, "service_name": name, "version": payload.get("version"), "command": command, "working_dir": payload.get("working_dir"), "artifact_verified": artifact_verified}
    if action in {"start", "rollback"} and dry_run:
        selected = payload.get("previous_command") if action == "rollback" else command
        return {"ok": True, "action": action, "dry_run": True, "service_name": name, "command": selected, "working_dir": payload.get("working_dir")}
    service = _load_run_service()
    if action == "start":
        if not command:
            return {"ok": False, "error": "start requires command"}
        service_payload = {"action": "start", "name": name, "command": command, "working_dir": payload.get("working_dir"), "ready_port": payload.get("ready_port"), "ready_regex": payload.get("ready_regex"), "ready_timeout_sec": payload.get("ready_timeout_sec", 90)}
    elif action == "rollback":
        previous = payload.get("previous_command")
        if not isinstance(previous, list) or not previous:
            return {"ok": False, "error": "rollback requires previous_command"}
        service_payload = {"action": "restart", "name": name, "command": previous, "working_dir": payload.get("working_dir"), "ready_port": payload.get("ready_port"), "ready_regex": payload.get("ready_regex"), "ready_timeout_sec": payload.get("ready_timeout_sec", 90)}
    elif action == "health":
        service_payload = {"action": "wait", "name": name, "ready_port": payload.get("ready_port"), "ready_regex": payload.get("ready_regex"), "timeout_sec": payload.get("ready_timeout_sec", 90)}
    else:
        service_payload = {"action": action, "name": name}
    return service(service_payload)


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_deploy)


if __name__ == "__main__":
    main()
