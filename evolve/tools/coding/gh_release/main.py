"""Controlled GitHub Release operations via gh CLI."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


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


def run_gh_release(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip().lower()
    if action not in {"create", "view"}:
        return {"ok": False, "error": "action must be create or view"}
    missing = _require_gh()
    if missing:
        return {"ok": False, "error": missing}
    paths = _load_paths().discover(start=_agent_root())
    try:
        cwd = _resolve_cwd(paths, payload.get("working_dir", payload.get("cwd")))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if action == "view":
        args = ["gh", "release", "view"]
        number = payload.get("number")
        if number is not None:
            args.append(str(number))
        args += ["--json", "name,tagName,isDraft,isPrerelease,url,publishedAt"]
    else:
        tag = str(payload.get("tag") or "").strip()
        if not _TAG_RE.fullmatch(tag) or tag.startswith("-"):
            return {"ok": False, "error": "tag is invalid"}
        title = str(payload.get("title") or tag).strip()
        notes = payload.get("notes") if isinstance(payload.get("notes"), str) else ""
        args = ["gh", "release", "create", tag, "--title", title, "--notes", notes]
        if bool(payload.get("draft", False)):
            args.append("--draft")
        if bool(payload.get("prerelease", False)):
            args.append("--prerelease")
        files = payload.get("files") or []
        if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
            return {"ok": False, "error": "files must be an array of strings"}
        args.extend(files)
    if bool(payload.get("dry_run", False)):
        return {"ok": True, "action": action, "dry_run": True, "args": args}
    code, out, err = _run(args, cwd=cwd)
    if code != 0:
        return {"ok": False, "action": action, "error": err.strip() or out.strip() or "gh release command failed"}
    return {"ok": True, "action": action, "output": out.strip()}


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_gh_release)


if __name__ == "__main__":
    main()
