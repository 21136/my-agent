"""git_commit — controlled add+commit (Phase 26 M1 · D3=A).

No push, force, amend, or git config changes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

_MAX_OUT = 16_000
_FORBIDDEN_MSG_FLAGS = ("--amend", "--no-verify", "-n", "--allow-empty")


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


def _validate_message(message: str) -> str | None:
    text = message.strip()
    if not text:
        return "message is required"
    if "\x00" in text:
        return "message contains null byte"
    lowered = text.lower()
    for flag in _FORBIDDEN_MSG_FLAGS:
        if flag in lowered.split():
            return f"message must not contain {flag}"
    return None


def git_commit(payload: dict[str, Any]) -> dict[str, Any]:
    message = payload.get("message", "")
    if not isinstance(message, str):
        return {"ok": False, "error": "message must be a string"}
    msg_err = _validate_message(message)
    if msg_err:
        return {"ok": False, "error": msg_err}
    message = message.strip()

    dry_run = bool(payload.get("dry_run", False))
    path_specs = payload.get("paths")
    if path_specs is None:
        path_specs = []
    if not isinstance(path_specs, list) or not all(isinstance(p, str) for p in path_specs):
        return {"ok": False, "error": "paths must be an array of strings"}

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

    # Resolve path specs under cwd / agent root; keep them inside toplevel
    add_paths: list[str] = []
    for raw in path_specs:
        text = raw.strip().replace("\\", "/")
        if not text:
            continue
        try:
            resolved = paths.resolve_under_agent(text, must_exist=False)
        except Exception:
            resolved = (cwd / text).resolve()
        try:
            resolved.relative_to(toplevel)
        except ValueError:
            return {"ok": False, "error": f"path outside git toplevel: {raw}"}
        add_paths.append(str(resolved.relative_to(toplevel)).replace("\\", "/"))

    status_code, status_out, status_err = _run_git(
        ["status", "--porcelain"], cwd=toplevel
    )
    if status_code != 0:
        return {
            "ok": False,
            "error": status_err.strip() or "git status failed",
            "repo": str(toplevel),
        }

    branch_code, branch_out, _ = _run_git(["branch", "--show-current"], cwd=toplevel)
    branch = branch_out.strip() if branch_code == 0 else ""

    if dry_run:
        # Preview what add -u or add paths would stage
        if add_paths:
            preview_cmd = ["git", "add", "--", *add_paths]
            staged_preview = add_paths
        else:
            preview_cmd = ["git", "add", "-u"]
            # tracked modifications/deletes from porcelain
            staged_preview = []
            for line in status_out.splitlines():
                if len(line) < 4:
                    continue
                xy, path = line[:2], line[3:]
                if " -> " in path:
                    path = path.split(" -> ", 1)[-1]
                # untracked ?? skipped for -u
                if xy == "??":
                    continue
                staged_preview.append(path)

        return {
            "ok": True,
            "dry_run": True,
            "repo": str(toplevel.relative_to(paths.agent_root.resolve())).replace("\\", "/"),
            "branch": branch,
            "message": message,
            "add_command": preview_cmd,
            "would_stage": staged_preview,
            "status_porcelain": _truncate(status_out),
        }

    if add_paths:
        code, _out, err = _run_git(["add", "--", *add_paths], cwd=toplevel)
    else:
        code, _out, err = _run_git(["add", "-u"], cwd=toplevel)
    if code != 0:
        return {"ok": False, "error": err.strip() or "git add failed", "repo": str(toplevel)}

    # Nothing to commit?
    diff_code, _diff_out, _ = _run_git(["diff", "--cached", "--quiet"], cwd=toplevel)
    if diff_code == 0:
        return {
            "ok": False,
            "error": "nothing to commit (empty index after add)",
            "repo": str(toplevel.relative_to(paths.agent_root.resolve())).replace("\\", "/"),
            "branch": branch,
            "status_porcelain": _truncate(status_out),
        }

    # Use -m only; never pass user message as extra flags
    code, out, err = _run_git(["commit", "-m", message], cwd=toplevel)
    if code != 0:
        return {
            "ok": False,
            "error": (err or out).strip() or "git commit failed",
            "repo": str(toplevel),
            "stdout": _truncate(out),
            "stderr": _truncate(err),
        }

    head_code, head_out, _ = _run_git(["rev-parse", "--short", "HEAD"], cwd=toplevel)
    return {
        "ok": True,
        "repo": str(toplevel.relative_to(paths.agent_root.resolve())).replace("\\", "/"),
        "branch": branch,
        "commit": head_out.strip() if head_code == 0 else "",
        "message": message,
        "stdout": _truncate(out),
    }


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main

    run_tool_main(git_commit)


if __name__ == "__main__":
    main()
