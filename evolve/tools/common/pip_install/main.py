""" pip_install -- install python packages with pip, capture output/exit_code."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_MAX_OUTPUT = 32000


def _agent_root() -> Path:
    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        evolve_marker = directory / "evolve"
        if (evolve_marker / "_index.core.toml").is_file() or (evolve_marker / "_index.toml").is_file():
            return directory
    raise RuntimeError("could not locate agent root")


def _truncate(text: str, limit: int = _MAX_OUTPUT) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n... (truncated)", True


def run_pip_install(payload: dict[str, Any]) -> dict[str, Any]:
    packages = payload.get("packages")
    requirements = payload.get("requirements")

    if not packages and not requirements:
        return {"ok": False, "error": "packages or requirements is required"}

    if packages and requirements:
        return {"ok": False, "error": "give only one of packages or requirements"}

    dry_run = bool(payload.get("dry_run", False))
    upgrade = bool(payload.get("upgrade", False))
    timeout_sec = int(payload.get("timeout_sec", 120))

    cmd = [sys.executable, "-m", "pip", "install"]
    if upgrade:
        cmd.append("--upgrade")

    if packages:
        if not isinstance(packages, list) or not all(isinstance(p, str) for p in packages):
            return {"ok": False, "error": "packages must be an array of strings"}
        cmd.extend(packages)
    elif requirements:
        if not isinstance(requirements, str) or not requirements.strip():
            return {"ok": False, "error": "requirements must be a string path"}
        # Resolve requirements file path
        req_path = Path(requirements)
        if req_path.is_absolute():
            return {"ok": False, "error": "absolute path not allowed", "command": cmd}
        # Check relative to agent root/agent-core/workspace
        for base in (_agent_root(), _agent_root() / "workspace"):
            candidate = base / req_path
            if candidate.is_file():
                req_path = candidate
                break
        else:
            return {"ok": False, "error": "file not found: " + requirements}
        cmd.append("-r", str(req_path))

    if dry_run:
        return {"ok": True, "dry_run": True, "command": cmd}

    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
        stdout, stdout_trunc = _truncate(r.stdout)
        stderr, stderr_trunc = _truncate(r.stderr)
        result: dict = {
            "ok": True,
            "exit_code": r.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
        if stdout_trunc or stderr_trunc:
            result["truncated"] = True
        return result
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Timed out after {}s".format(timeout_sec), "command": cmd}
    except OSError as e:
        return {"ok": False, "error": str(e), "command": cmd}


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_pip_install)


if __name__ == "__main__":
    main()
