"""git_diff — read-only git diff / name-status (coding)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_DEFAULT_MAX = 120_000
_BRANCH_SAFE = re.compile(r"^[^\x00\r\n]+$")


def _agent_root() -> Path:
    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        evolve_marker = directory / "evolve"
        if (evolve_marker / "_index.core.toml").is_file() or (evolve_marker / "_index.toml").is_file():
            return directory
    raise RuntimeError("could not locate agent root")


def _agent_core_dir() -> Path:
    return _agent_root() / "agent-core"


def _load_paths():
    core = _agent_core_dir()
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


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars] + "\n…(truncated)", True


def _git_toplevel(cwd: Path) -> Path | None:
    code, out, _err = _run_git(["rev-parse", "--show-toplevel"], cwd=cwd)
    if code != 0:
        return None
    text = out.strip()
    return Path(text).resolve() if text else None


def run_git_diff(payload: dict[str, Any]) -> dict[str, Any]:
    paths = _load_paths().discover(start=_agent_root())
    try:
        cwd = _resolve_cwd(paths, _coalesce_working_dir(payload))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    toplevel = _git_toplevel(cwd)
    if toplevel is None:
        return {"ok": False, "error": "not a git repository"}

    path_specs = payload.get("paths", [])
    if path_specs is None:
        path_specs = []
    if not isinstance(path_specs, list) or not all(isinstance(p, str) for p in path_specs):
        return {"ok": False, "error": "paths must be an array of strings"}

    staged = bool(payload.get("staged", False))
    name_status = bool(payload.get("name_status", False))
    stat_only = bool(payload.get("stat_only", False))
    if name_status and stat_only:
        return {"ok": False, "error": "name_status and stat_only are mutually exclusive"}

    base_ref = payload.get("base_ref")
    if base_ref is not None:
        if not isinstance(base_ref, str) or not base_ref.strip():
            return {"ok": False, "error": "base_ref must be a non-empty git ref"}
        base_ref = base_ref.strip()
        if base_ref.startswith("-") or "\x00" in base_ref or not _BRANCH_SAFE.match(base_ref):
            return {"ok": False, "error": "base_ref looks unsafe"}

    max_chars = payload.get("max_chars", _DEFAULT_MAX)
    if not isinstance(max_chars, int) or max_chars < 1000 or max_chars > 500_000:
        return {"ok": False, "error": "max_chars must be an int in [1000, 500000]"}

    dry_run = bool(payload.get("dry_run", False))

    args: list[str] = ["diff"]
    if staged:
        args.append("--cached")
    if name_status:
        args.append("--name-status")
    elif stat_only:
        args.append("--stat")
    if base_ref:
        args.append(base_ref)
    if path_specs:
        args.append("--")
        args.extend(path_specs)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "repo_root": str(toplevel),
            "cwd": paths.to_agent_relative(cwd) if hasattr(paths, "to_agent_relative") else str(cwd),
            "git_args": args,
            "staged": staged,
            "base_ref": base_ref,
            "paths": path_specs,
            "name_status": name_status,
            "stat_only": stat_only,
            "max_chars": max_chars,
        }

    code, out, err = _run_git(args, cwd=cwd)
    if code != 0:
        return {"ok": False, "error": err.strip() or "git diff failed", "git_args": args}

    body, truncated = _truncate(out, max_chars)
    branch_code, branch_out, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    result: dict[str, Any] = {
        "ok": True,
        "branch": branch_out.strip() if branch_code == 0 else "",
        "staged": staged,
        "base_ref": base_ref,
        "paths": path_specs,
        "name_status": name_status,
        "stat_only": stat_only,
        "diff": body,
        "empty": not body.strip(),
    }
    if truncated:
        result["truncated"] = True
        result["max_chars"] = max_chars
    return result


def main() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main

    run_tool_main(run_git_diff)


def _demo() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    registry = ToolRegistry.load()
    tool = registry.get_evolved("git_diff")
    assert tool is not None and tool.scope == "coding"
    assert tool.policy.confirm is False
    print("[PASS] registry loads git_diff (coding, active, confirm=false)")

    dry = run(
        {"tool_name": "git_diff", "arguments": {"stat_only": True}, "dry_run": True},
        registry=registry,
    )
    assert dry.ok and dry.data.get("dry_run") is True
    print("[PASS] dry_run reports git args")

    live = run(
        {"tool_name": "git_diff", "arguments": {"stat_only": True, "paths": ["evolve/prompts/coding.md"]}},
        registry=registry,
    )
    assert live.ok and "diff" in live.data
    print("[PASS] live git_diff stat_only")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
