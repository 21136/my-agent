"""Controlled GitHub repository creation via gh CLI."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(/[A-Za-z0-9][A-Za-z0-9_.-]*)?$")


def _agent_root() -> Path:
    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        marker = directory / "evolve"
        if (marker / "_index.core.toml").is_file() or (marker / "_index.toml").is_file():
            return directory
    raise RuntimeError("could not locate agent root")


def _load_paths():
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from paths import AgentPaths
    return AgentPaths


def _require_gh() -> str | None:
    if not shutil.which("gh"):
        return "gh CLI not found on PATH; install GitHub CLI and run gh auth login"
    return None


def _resolve_cwd(paths, raw: Any) -> Path:
    text = str(raw or "").strip()
    if not text:
        return paths.agent_root
    return paths.resolve_under_agent(text.replace("\\", "/"), must_exist=True)


def _run(args: list[str], *, cwd: Path) -> tuple[int, str, str]:
    completed = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def run_gh_repo(payload: dict[str, Any]) -> dict[str, Any]:
    if str(payload.get("action") or "").strip().lower() != "create":
        return {"ok": False, "error": "action must be create"}
    missing = _require_gh()
    if missing:
        return {"ok": False, "error": missing}
    name = str(payload.get("name") or "").strip()
    if not _NAME_RE.fullmatch(name) or name.startswith("-"):
        return {"ok": False, "error": "name must be repository name or owner/name"}
    visibility = str(payload.get("visibility") or "private").strip().lower()
    if visibility not in {"private", "public"}:
        return {"ok": False, "error": "visibility must be private or public"}
    description = payload.get("description") if isinstance(payload.get("description"), str) else ""
    remote = str(payload.get("remote") or "origin").strip()
    paths = _load_paths().discover(start=_agent_root())
    try:
        cwd = _resolve_cwd(paths, payload.get("working_dir", payload.get("cwd")))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    args = ["gh", "repo", "create", name, f"--{visibility}"]
    if description.strip():
        args += ["--description", description.strip()]
    if payload.get("working_dir") or payload.get("cwd"):
        args += ["--source", str(cwd), "--remote", remote]
    if bool(payload.get("dry_run", False)):
        return {"ok": True, "dry_run": True, "action": "create", "args": args}
    code, out, err = _run(args, cwd=cwd)
    if code != 0:
        return {"ok": False, "action": "create", "error": err.strip() or out.strip() or "gh repo create failed"}
    return {"ok": True, "action": "create", "output": out.strip(), "pushed": False}


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_gh_repo)


if __name__ == "__main__":
    main()
