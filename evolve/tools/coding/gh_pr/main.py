"""gh_pr — controlled GitHub PR helpers via gh CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_READ_ACTIONS = frozenset({"view", "checks", "list"})
_WRITE_ACTIONS = frozenset({"create"})


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
    for key in ("working_dir", "cwd"):
        raw = payload.get(key, "")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
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


def _run(args: list[str], *, cwd: Path) -> tuple[int, str, str]:
    completed = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _require_gh() -> str | None:
    if not shutil.which("gh"):
        return "gh CLI not found on PATH; install GitHub CLI and run gh auth login"
    return None


def run_gh_pr(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip().lower()
    if action not in _READ_ACTIONS | _WRITE_ACTIONS:
        return {"ok": False, "error": "action must be create|view|checks|list"}

    missing = _require_gh()
    if missing:
        return {"ok": False, "error": missing}

    paths = _load_paths().discover(start=_agent_root())
    try:
        cwd = _resolve_cwd(paths, _coalesce_working_dir(payload))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    dry_run = bool(payload.get("dry_run", False))

    if action == "list":
        limit = payload.get("limit", 10)
        if not isinstance(limit, int) or limit < 1 or limit > 50:
            return {"ok": False, "error": "limit must be int in [1,50]"}
        args = [
            "gh", "pr", "list", "--limit", str(limit),
            "--json", "number,title,url,state,headRefName,baseRefName,isDraft",
        ]
        if dry_run:
            return {"ok": True, "dry_run": True, "action": action, "args": args}
        code, out, err = _run(args, cwd=cwd)
        if code != 0:
            return {"ok": False, "error": err.strip() or out.strip() or "gh pr list failed"}
        try:
            items = json.loads(out)
        except json.JSONDecodeError:
            return {"ok": False, "error": "gh pr list returned non-JSON", "raw": out[:2000]}
        return {"ok": True, "action": action, "prs": items}

    if action in {"view", "checks"}:
        number = payload.get("number")
        if number is not None and (not isinstance(number, int) or number < 1):
            return {"ok": False, "error": "number must be a positive int"}
        if action == "view":
            args = ["gh", "pr", "view"]
            if number is not None:
                args.append(str(number))
            args += ["--json", "number,title,url,state,body,headRefName,baseRefName,isDraft,statusCheckRollup"]
        else:
            args = ["gh", "pr", "checks"]
            if number is not None:
                args.append(str(number))
        if dry_run:
            return {"ok": True, "dry_run": True, "action": action, "args": args}
        code, out, err = _run(args, cwd=cwd)
        if code != 0:
            return {"ok": False, "error": err.strip() or out.strip() or f"gh pr {action} failed"}
        if action == "view":
            try:
                data = json.loads(out)
            except json.JSONDecodeError:
                return {"ok": False, "error": "gh pr view returned non-JSON", "raw": out[:2000]}
            return {"ok": True, "action": action, "pr": data}
        return {"ok": True, "action": action, "checks_text": out.strip()}

    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        return {"ok": False, "error": "create requires non-empty title"}
    body = payload.get("body") if isinstance(payload.get("body"), str) else ""
    base = payload.get("base") if isinstance(payload.get("base"), str) else ""
    head = payload.get("head") if isinstance(payload.get("head"), str) else ""
    draft = bool(payload.get("draft", False))

    args = ["gh", "pr", "create", "--title", title.strip(), "--body", body]
    if base.strip():
        args += ["--base", base.strip()]
    if head.strip():
        args += ["--head", head.strip()]
    if draft:
        args.append("--draft")

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "action": "create",
            "args": args,
            "title": title.strip(),
            "base": base.strip() or None,
            "head": head.strip() or None,
            "draft": draft,
        }

    code, out, err = _run(args, cwd=cwd)
    if code != 0:
        return {"ok": False, "error": err.strip() or out.strip() or "gh pr create failed"}
    return {"ok": True, "action": "create", "url_or_output": out.strip()}


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main

    run_tool_main(run_gh_pr)


if __name__ == "__main__":
    main()
