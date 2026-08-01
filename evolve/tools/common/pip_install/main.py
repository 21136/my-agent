"""pip_install — install packages via python -m pip (Phase 26 M2 · active)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_MAX_OUTPUT = 32000
_MAX_TIMEOUT = 300
_PKG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-\[\]<>=!~,]*$")


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


def _truncate(text: str, limit: int = _MAX_OUTPUT) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n…(truncated)", True


def _coalesce_working_dir(payload: dict[str, Any]) -> str:
    working = payload.get("working_dir", "")
    if isinstance(working, str) and working.strip():
        return working.strip()
    cwd = payload.get("cwd", "")
    if isinstance(cwd, str) and cwd.strip():
        return cwd.strip()
    return ""


def run_pip_install(payload: dict[str, Any]) -> dict[str, Any]:
    packages = payload.get("packages")
    requirements = payload.get("requirements")

    if not packages and not requirements:
        return {"ok": False, "error": "packages or requirements is required"}
    if packages and requirements:
        return {"ok": False, "error": "give only one of packages or requirements"}

    dry_run = bool(payload.get("dry_run", False))
    upgrade = bool(payload.get("upgrade", False))
    try:
        timeout_sec = int(payload.get("timeout_sec", 120))
    except (TypeError, ValueError):
        return {"ok": False, "error": "timeout_sec must be an integer"}
    timeout_sec = max(1, min(timeout_sec, _MAX_TIMEOUT))

    cmd = [sys.executable, "-m", "pip", "install"]
    if upgrade:
        cmd.append("--upgrade")

    AgentPaths = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    if packages is not None:
        if not isinstance(packages, list) or not packages or not all(isinstance(p, str) for p in packages):
            return {"ok": False, "error": "packages must be a non-empty array of strings"}
        for pkg in packages:
            name = pkg.strip()
            if not name or not _PKG_RE.match(name):
                return {
                    "ok": False,
                    "error": f"invalid package spec: {pkg!r} (allowed: letters/digits/._-[]<>=!~,)",
                }
            if name.startswith("-"):
                return {"ok": False, "error": f"package must not look like a flag: {pkg!r}"}
            cmd.append(name)
    else:
        if not isinstance(requirements, str) or not requirements.strip():
            return {"ok": False, "error": "requirements must be a string path"}
        text = requirements.strip().replace("\\", "/").lstrip("/")
        req_path: Path | None = None
        try:
            req_path = paths.resolve_under_agent(text, must_exist=True)
        except Exception:
            if not text.startswith("workspace/"):
                try:
                    req_path = paths.resolve_under_agent(f"workspace/{text}", must_exist=True)
                except Exception:
                    req_path = None
        if req_path is None or not req_path.is_file():
            return {"ok": False, "error": f"requirements file not found under agent root: {requirements}"}
        # Optional: ensure near working_dir if provided
        _ = _coalesce_working_dir(payload)
        cmd.extend(["-r", str(req_path)])

    if dry_run:
        return {"ok": True, "dry_run": True, "command": cmd}

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            cwd=str(paths.agent_root),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timed out after {timeout_sec}s", "command": cmd}
    except OSError as exc:
        return {"ok": False, "error": str(exc), "command": cmd}

    stdout, stdout_trunc = _truncate(completed.stdout or "")
    stderr, stderr_trunc = _truncate(completed.stderr or "")
    result: dict[str, Any] = {
        "ok": completed.returncode == 0,
        "command": cmd,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }
    if completed.returncode != 0:
        result["error"] = stderr.strip() or f"pip exited {completed.returncode}"
    if stdout_trunc or stderr_trunc:
        result["truncated"] = True
    return result


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main

    run_tool_main(run_pip_install)


if __name__ == "__main__":
    main()
