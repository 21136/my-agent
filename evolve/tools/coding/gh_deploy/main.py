"""Controlled GitHub Actions production deployment target."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

_ACTIONS = frozenset({"plan", "dispatch", "wait", "view"})
_ENV_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


def _agent_root() -> Path:
    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        marker = directory / "evolve"
        if (marker / "_index.core.toml").is_file() or (marker / "_index.toml").is_file():
            return directory
    raise RuntimeError("could not locate agent root")


def _require_gh() -> str | None:
    if not shutil.which("gh"):
        return "gh CLI not found on PATH; install GitHub CLI and run gh auth login"
    return None


def _run(args: list[str], *, cwd: Path, timeout_sec: int = 900) -> tuple[int, str, str]:
    completed = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(1, timeout_sec),
        check=False,
    )
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _required_text(payload: dict[str, Any], key: str, pattern: re.Pattern[str]) -> tuple[str | None, str | None]:
    value = str(payload.get(key) or "").strip()
    if not value or not pattern.fullmatch(value):
        return None, f"{key} is invalid"
    return value, None


def _workflow_args(payload: dict[str, Any]) -> tuple[list[str] | None, str | None]:
    repository = str(payload.get("repository") or "").strip()
    if not repository or "/" not in repository or repository.startswith("/"):
        return None, "repository must be owner/name"
    workflow = str(payload.get("workflow") or "self-hosting-deploy.yml").strip()
    if not workflow or any(char in workflow for char in "\r\n"):
        return None, "workflow is invalid"
    environment, error = _required_text({**payload, "environment": payload.get("environment") or "production"}, "environment", _ENV_RE)
    if error:
        return None, error
    version, error = _required_text(payload, "version", _VERSION_RE)
    if error:
        return None, error
    ref = str(payload.get("ref") or version).strip()
    if not ref or any(char in ref for char in "\r\n"):
        return None, "ref is invalid"
    args = [
        "gh",
        "workflow",
        "run",
        workflow,
        "--repo",
        repository,
        "--ref",
        ref,
        "-f",
        f"environment={environment}",
        "-f",
        f"version={version}",
    ]
    artifact_path = str(payload.get("artifact_path") or "").strip()
    artifact_digest = str(payload.get("artifact_digest") or "").strip()
    if artifact_path or artifact_digest:
        if not artifact_path or not re.fullmatch(r"[0-9a-fA-F]{64}", artifact_digest):
            return None, "artifact_path and 64-character artifact_digest are required together"
        args += ["-f", f"artifact_path={artifact_path}", "-f", f"artifact_digest={artifact_digest}"]
    return args, None


def run_gh_deploy(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        return {"ok": False, "error": "action must be plan|dispatch|wait|view"}
    missing = _require_gh()
    if missing:
        return {"ok": False, "error": missing}
    root = _agent_root()
    timeout_sec = int(payload.get("timeout_sec", 900) or 900)

    if action in {"plan", "dispatch"}:
        args, error = _workflow_args(payload)
        if error:
            return {"ok": False, "error": error}
        assert args is not None
        if action == "dispatch" and payload.get("confirmed") is not True:
            return {"ok": False, "error": "production dispatch requires confirmed=true"}
        if bool(payload.get("dry_run", False)) or action == "plan":
            return {"ok": True, "action": action, "dry_run": True, "args": args}
    elif action == "wait":
        run_id = payload.get("run_id")
        if not isinstance(run_id, int) or run_id < 1:
            return {"ok": False, "error": "run_id must be a positive int"}
        repository = str(payload.get("repository") or "").strip()
        if not repository or "/" not in repository:
            return {"ok": False, "error": "repository must be owner/name"}
        args = ["gh", "run", "watch", str(run_id), "--repo", repository, "--exit-status"]
    else:
        repository = str(payload.get("repository") or "").strip()
        if not repository or "/" not in repository:
            return {"ok": False, "error": "repository must be owner/name"}
        workflow = str(payload.get("workflow") or "self-hosting-deploy.yml").strip()
        args = ["gh", "run", "list", "--repo", repository, "--workflow", workflow, "--limit", "10", "--json", "databaseId,status,conclusion,headBranch,createdAt"]

    try:
        code, stdout, stderr = _run(args, cwd=root, timeout_sec=timeout_sec)
    except subprocess.TimeoutExpired:
        return {"ok": False, "action": action, "error": "GitHub deployment command timed out", "args": args}
    if code != 0:
        return {"ok": False, "action": action, "error": stderr.strip() or stdout.strip() or "GitHub deployment command failed", "args": args}
    return {"ok": True, "action": action, "args": args, "output": stdout.strip(), "status": "completed"}


def main() -> None:
    core = _agent_root() / "agent-core"
    import sys
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_gh_deploy)


if __name__ == "__main__":
    main()
