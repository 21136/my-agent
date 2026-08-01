"""dev_start — thin orchestrator over run_service (Phase 26 M0 · D4).

One-click start/stop for a project's backend+frontend. Does **not** spawn
processes itself; delegates to ``run_service`` so lifecycle/logs live in
``data/services/``. Prefer documenting ``run_service`` as the primary path;
``dev_start`` is convenience sugar.
"""

from __future__ import annotations

import importlib.util
import shlex
import sys
from pathlib import Path
from typing import Any

_DEFAULT_BACKEND = {"dir": "backend", "command": ["mvn", "spring-boot:run"], "port": 8080}
_DEFAULT_FRONTEND = {"dir": "frontend", "command": ["npm", "run", "dev"], "port": 3000}


def _agent_root() -> Path:
    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        evolve_marker = directory / "evolve"
        if (evolve_marker / "_index.core.toml").is_file() or (evolve_marker / "_index.toml").is_file():
            return directory
    raise RuntimeError("could not locate agent root")


def _load_paths():
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from paths import AgentPaths

    return AgentPaths


def _load_run_service():
    main_py = _agent_root() / "evolve" / "tools" / "common" / "run_service" / "main.py"
    spec = importlib.util.spec_from_file_location("run_service_for_dev_start", main_py)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load run_service: {main_py}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _resolve_project(paths, raw: str) -> Path:
    text = raw.strip().replace("\\", "/").lstrip("/")
    try:
        return paths.resolve_under_agent(text, must_exist=True)
    except Exception:
        if not text.startswith("workspace/"):
            try:
                return paths.resolve_under_agent(f"workspace/{text}", must_exist=True)
            except Exception:
                pass
        raise ValueError(f"project_dir 不存在或越界: {raw}")


def _normalize_command(command: Any) -> list[str]:
    if isinstance(command, str):
        command = shlex.split(command)
    if not isinstance(command, list) or not all(isinstance(x, str) for x in command):
        raise ValueError("command 须为字符串或字符串数组")
    if not command:
        raise ValueError("command 不能为空")
    return list(command)


def _side_config(payload: dict[str, Any], key: str, defaults: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get(key)
    if raw is None:
        raw = {}
    if isinstance(raw, str):
        raw = {"dir": raw}
    if not isinstance(raw, dict):
        raise ValueError(f"{key} 须为对象 {{dir, command, port}} 或目录字符串")
    command = _normalize_command(raw.get("command", defaults["command"]))
    return {
        "dir": str(raw.get("dir", defaults["dir"])),
        "command": command,
        "port": int(raw.get("port", defaults["port"])),
    }


def _service_name(project: Path, side: str) -> str:
    # run_service name: /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/
    slug = project.name.replace(" ", "-")
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in slug)
    slug = slug.strip("-_") or "project"
    return f"{slug}-{side}"[:64]


def _working_dir_rel(paths, project: Path, sub: str) -> str:
    target = (project / sub).resolve()
    rel = target.relative_to(paths.agent_root.resolve())
    return str(rel).replace("\\", "/")


def _cmd_string(command: list[str]) -> str:
    if sys.platform == "win32":
        return subprocess_list2cmdline(command)
    return shlex.join(command)


def subprocess_list2cmdline(cmd: list[str]) -> str:
    """Windows-friendly join without importing subprocess for quoting alone."""
    import subprocess

    return subprocess.list2cmdline(cmd)


def dev_start(payload: dict[str, Any]) -> dict[str, Any]:
    stop = bool(payload.get("stop", False))
    dry_run = bool(payload.get("dry_run", False))
    backend_only = bool(payload.get("backend_only", False))
    frontend_only = bool(payload.get("frontend_only", False))

    AgentPaths = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    raw_project = payload.get("project_dir", "")
    if not isinstance(raw_project, str) or not raw_project.strip():
        return {"ok": False, "error": "project_dir is required"}

    try:
        project = _resolve_project(paths, raw_project)
        backend = _side_config(payload, "backend", _DEFAULT_BACKEND)
        frontend = _side_config(payload, "frontend", _DEFAULT_FRONTEND)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}

    sides: list[tuple[str, dict[str, Any]]] = []
    if not frontend_only:
        sides.append(("backend", backend))
    if not backend_only:
        sides.append(("frontend", frontend))
    if not sides:
        return {"ok": False, "error": "backend_only and frontend_only both set — nothing to do"}

    rs = _load_run_service()
    results: dict[str, Any] = {}
    notes = [
        "dev_start delegates to run_service (Phase 26 D4); prefer run_service for single services."
    ]

    if stop:
        for side, _cfg in sides:
            name = _service_name(project, side)
            if dry_run:
                results[side] = {"ok": True, "dry_run": True, "action": "stop", "name": name}
                continue
            results[side] = rs.run_service({"action": "stop", "name": name})
        ok = all(bool(r.get("ok")) for r in results.values())
        return {
            "ok": ok,
            "action": "stop",
            "via": "run_service",
            "project_dir": str(project),
            "results": results,
            "notes": notes,
        }

    planned: list[dict[str, Any]] = []
    for side, cfg in sides:
        name = _service_name(project, side)
        try:
            working = _working_dir_rel(paths, project, cfg["dir"])
        except ValueError:
            return {"ok": False, "error": f"{side} dir outside agent root: {cfg['dir']}"}
        side_path = project / cfg["dir"]
        if not side_path.is_dir():
            return {"ok": False, "error": f"{side} directory missing: {side_path}"}
        command = _cmd_string(cfg["command"])
        plan = {
            "name": name,
            "command": command,
            "working_dir": working,
            "ready_port": cfg["port"],
            "ready_timeout_sec": int(payload.get("ready_timeout_sec", 90)),
        }
        planned.append({"side": side, **plan})
        if dry_run:
            results[side] = {"ok": True, "dry_run": True, "action": "start", **plan}
            continue
        results[side] = rs.run_service(
            {
                "action": "start",
                "name": name,
                "command": command,
                "working_dir": working,
                "ready_port": cfg["port"],
                "ready_timeout_sec": plan["ready_timeout_sec"],
            }
        )

    ok = all(bool(r.get("ok")) for r in results.values()) if results else False
    out: dict[str, Any] = {
        "ok": ok if not dry_run else True,
        "action": "start",
        "via": "run_service",
        "project_dir": str(project),
        "planned": planned,
        "results": results,
        "notes": notes,
        "urls": {
            side: f"http://127.0.0.1:{cfg['port']}"
            for side, cfg in sides
        },
    }
    if dry_run:
        out["dry_run"] = True
    return out


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main

    run_tool_main(dev_start)


if __name__ == "__main__":
    main()
