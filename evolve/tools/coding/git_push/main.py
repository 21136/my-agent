"""git_push — push current branch only (Phase 32 Track E).

Always confirm (except dry_run). No --force / --force-with-lease.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_MAX_OUT = 16_000
_REMOTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_FORCE_HINT = re.compile(r"(?i)--force|--force-with-lease|-f\b")


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


def _validate_remote(remote: str) -> str | None:
    text = remote.strip()
    if not text:
        return "remote is required"
    if _FORCE_HINT.search(text) or text.startswith("-"):
        return "remote must not look like a force flag"
    if not _REMOTE_RE.match(text):
        return "remote must match /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/"
    return None


def git_push(payload: dict[str, Any]) -> dict[str, Any]:
    dry_run = bool(payload.get("dry_run", False))
    set_upstream = bool(payload.get("set_upstream", False))

    remote_raw = payload.get("remote", "origin")
    if not isinstance(remote_raw, str):
        return {"ok": False, "error": "remote must be a string"}
    remote_err = _validate_remote(remote_raw)
    if remote_err:
        return {"ok": False, "error": remote_err}
    remote = remote_raw.strip()

    # Hard reject any sneaky force-related keys/args
    for key in ("force", "force_with_lease", "lease"):
        if payload.get(key):
            return {"ok": False, "error": f"{key} is forbidden (Phase 32 E-Q3)"}

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

    branch_code, branch_out, _ = _run_git(["branch", "--show-current"], cwd=toplevel)
    branch = branch_out.strip() if branch_code == 0 else ""
    if not branch:
        return {
            "ok": False,
            "error": "detached HEAD or no current branch; refuse push",
            "repo": _repo_rel(paths, toplevel),
        }

    # Push only current branch tip by name (never --all / --mirror / --force*).
    cmd = ["push"]
    if dry_run:
        cmd.append("--dry-run")
    if set_upstream:
        cmd.append("-u")
    cmd.extend([remote, branch])

    # Safety scan of argv we built
    joined = " ".join(cmd)
    if _FORCE_HINT.search(joined):
        return {"ok": False, "error": "internal refuse: force flag in push argv"}

    if dry_run:
        # Still execute git push --dry-run so preview is accurate (no network write).
        code, out, err = _run_git(cmd, cwd=toplevel)
        return {
            "ok": code == 0,
            "dry_run": True,
            "repo": _repo_rel(paths, toplevel),
            "branch": branch,
            "remote": remote,
            "set_upstream": set_upstream,
            "command": ["git", *cmd],
            "stdout": _truncate(out),
            "stderr": _truncate(err),
            "error": None if code == 0 else ((err or out).strip() or "git push --dry-run failed"),
        }

    code, out, err = _run_git(cmd, cwd=toplevel)
    if code != 0:
        return {
            "ok": False,
            "error": (err or out).strip() or "git push failed",
            "repo": _repo_rel(paths, toplevel),
            "branch": branch,
            "remote": remote,
            "command": ["git", *cmd],
            "stdout": _truncate(out),
            "stderr": _truncate(err),
        }
    return {
        "ok": True,
        "repo": _repo_rel(paths, toplevel),
        "branch": branch,
        "remote": remote,
        "set_upstream": set_upstream,
        "command": ["git", *cmd],
        "stdout": _truncate(out),
        "stderr": _truncate(err),
    }


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main

    run_tool_main(git_push)


if __name__ == "__main__":
    main()
