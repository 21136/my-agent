"""git_branch — list / create / switch (Phase 32 Track E).

No force checkout, no delete, no git config.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_MAX_OUT = 16_000
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


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


def _coalesce_working_dir(payload: dict[str, Any]) -> str:
    working = payload.get("working_dir", "")
    if isinstance(working, str) and working.strip():
        return working.strip()
    cwd = payload.get("cwd", "")
    if isinstance(cwd, str) and cwd.strip():
        return cwd.strip()
    return ""


def _resolve_cwd(paths, path_arg: str) -> Path:
    if not path_arg:
        return paths.agent_root
    text = path_arg.strip().replace("\\", "/").lstrip("/")
    try:
        return paths.resolve_under_agent(text, must_exist=True)
    except Exception:
        if not text.startswith("workspace/"):
            try:
                return paths.resolve_under_agent(f"workspace/{text}", must_exist=True)
            except Exception:
                pass
        raise ValueError(f"working_dir 不存在或越界: {path_arg}")


def _run_git(args: list[str], *, cwd: Path) -> tuple[int, str, str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _truncate(text: str) -> str:
    if len(text) <= _MAX_OUT:
        return text
    return text[:_MAX_OUT] + "\n…(truncated)"


def _git_toplevel(cwd: Path) -> Path | None:
    code, out, _err = _run_git(["rev-parse", "--show-toplevel"], cwd=cwd)
    if code != 0:
        return None
    text = out.strip()
    if not text:
        return None
    return Path(text).resolve()


def _assert_repo_in_agent(paths, toplevel: Path) -> None:
    root = paths.agent_root.resolve()
    try:
        toplevel.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"git toplevel outside agent root: {toplevel}") from exc


def _repo_rel(paths, toplevel: Path) -> str:
    return str(toplevel.relative_to(paths.agent_root.resolve())).replace("\\", "/")


def _validate_branch_name(name: str) -> str | None:
    text = name.strip()
    if not text:
        return "name is required"
    if text.startswith("-") or text.endswith("/") or text.endswith(".lock"):
        return "invalid branch name"
    if ".." in text or "//" in text or "@{" in text:
        return "invalid branch name"
    if not _BRANCH_RE.match(text):
        return "name must match /^[A-Za-z0-9][A-Za-z0-9._\\/-]{0,127}$/"
    return None


def _current_branch(toplevel: Path) -> str:
    code, out, _ = _run_git(["branch", "--show-current"], cwd=toplevel)
    return out.strip() if code == 0 else ""


def _action_list(paths, toplevel: Path) -> dict[str, Any]:
    code, out, err = _run_git(["branch", "--list", "--format=%(refname:short)"], cwd=toplevel)
    if code != 0:
        return {"ok": False, "error": err.strip() or "git branch --list failed"}
    branches = [line.strip() for line in out.splitlines() if line.strip()]
    return {
        "ok": True,
        "action": "list",
        "repo": _repo_rel(paths, toplevel),
        "current": _current_branch(toplevel),
        "branches": branches,
    }


def _action_create(
    paths,
    toplevel: Path,
    *,
    name: str,
    switch: bool,
    dry_run: bool,
) -> dict[str, Any]:
    current = _current_branch(toplevel)
    if dry_run:
        cmd = ["git", "checkout", "-b", name] if switch else ["git", "branch", name]
        return {
            "ok": True,
            "dry_run": True,
            "action": "create",
            "repo": _repo_rel(paths, toplevel),
            "name": name,
            "switch": switch,
            "current": current,
            "command": cmd,
        }

    if switch:
        code, out, err = _run_git(["checkout", "-b", name], cwd=toplevel)
    else:
        code, out, err = _run_git(["branch", name], cwd=toplevel)
    if code != 0:
        return {
            "ok": False,
            "action": "create",
            "error": (err or out).strip() or "git create branch failed",
            "repo": _repo_rel(paths, toplevel),
            "stdout": _truncate(out),
            "stderr": _truncate(err),
        }
    return {
        "ok": True,
        "action": "create",
        "repo": _repo_rel(paths, toplevel),
        "name": name,
        "switch": switch,
        "current": _current_branch(toplevel),
        "stdout": _truncate(out or err),
    }


def _action_switch(paths, toplevel: Path, *, name: str, dry_run: bool) -> dict[str, Any]:
    current = _current_branch(toplevel)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "action": "switch",
            "repo": _repo_rel(paths, toplevel),
            "name": name,
            "current": current,
            "command": ["git", "checkout", name],
            "note": "no --force; dirty tree may fail",
        }

    # Never pass -f / --force (would discard local changes).
    code, out, err = _run_git(["checkout", name], cwd=toplevel)
    if code != 0:
        return {
            "ok": False,
            "action": "switch",
            "error": (err or out).strip() or "git checkout failed",
            "repo": _repo_rel(paths, toplevel),
            "hint": "refusing force checkout; commit or stash first",
            "stdout": _truncate(out),
            "stderr": _truncate(err),
        }
    return {
        "ok": True,
        "action": "switch",
        "repo": _repo_rel(paths, toplevel),
        "name": name,
        "previous": current,
        "current": _current_branch(toplevel),
        "stdout": _truncate(out or err),
    }


def git_branch(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip().lower()
    if action not in {"list", "create", "switch"}:
        return {
            "ok": False,
            "error": "action must be list|create|switch",
        }

    dry_run = bool(payload.get("dry_run", False))
    switch_after = bool(payload.get("switch", False))

    AgentPaths = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    try:
        cwd = _resolve_cwd(paths, _coalesce_working_dir(payload))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    toplevel = _git_toplevel(cwd)
    if toplevel is None:
        return {"ok": False, "error": f"not a git repository: {cwd}"}
    try:
        _assert_repo_in_agent(paths, toplevel)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if action == "list":
        return _action_list(paths, toplevel)

    name_raw = payload.get("name", "")
    if not isinstance(name_raw, str):
        return {"ok": False, "error": "name must be a string"}
    name_err = _validate_branch_name(name_raw)
    if name_err:
        return {"ok": False, "error": name_err}
    name = name_raw.strip()

    if action == "create":
        return _action_create(
            paths, toplevel, name=name, switch=switch_after, dry_run=dry_run
        )
    return _action_switch(paths, toplevel, name=name, dry_run=dry_run)


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main

    run_tool_main(git_branch)


if __name__ == "__main__":
    main()
