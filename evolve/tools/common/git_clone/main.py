"""git_clone — shallow clone a public https git repo into workspace or evolve/tools/."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_MAX_OUTPUT = 32000
_VALID_TARGETS = frozenset({"workspace", "evolve_tools"})
_VALID_CONFLICTS = frozenset({"skip", "rename", "overwrite"})
_HOST_SUFFIXES = (
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "codeberg.org",
)
_EVOLVE_PREFIX_RE = re.compile(
    r"^evolve/tools/(?P<scope>[a-z][a-z0-9_]*)/(?P<name>[a-z][a-z0-9_]*)(?:/.*)?$"
)


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


def _truncate(text: str, limit: int = _MAX_OUTPUT) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n…(truncated)", True


def _scopes_from_index_payload(payload: dict[str, Any]) -> set[str]:
    scopes: set[str] = set()
    topics = payload.get("topic")
    if not isinstance(topics, list):
        return scopes
    for item in topics:
        if not isinstance(item, dict):
            continue
        tool_dirs = item.get("tool_dirs")
        if not isinstance(tool_dirs, list):
            continue
        for entry in tool_dirs:
            if not isinstance(entry, str):
                continue
            text = entry.strip().replace("\\", "/").strip("/")
            if text.startswith("tools/"):
                scopes.add(text.removeprefix("tools/").split("/")[0])
    return scopes


def _allowed_scopes(evolve_dir: Path) -> frozenset[str]:
    scopes: set[str] = {"common"}
    index_files: list[Path] = []
    core_path = evolve_dir / "_index.core.toml"
    user_path = evolve_dir / "_index.user.toml"
    legacy_path = evolve_dir / "_index.toml"
    if core_path.is_file():
        index_files.append(core_path)
        if user_path.is_file():
            index_files.append(user_path)
    elif legacy_path.is_file():
        index_files.append(legacy_path)
    for index_path in index_files:
        try:
            payload = tomllib.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        scopes |= _scopes_from_index_payload(payload)
    return frozenset(scopes)


def _host_allowed(hostname: str) -> bool:
    host = hostname.lower().strip(".")
    if not host:
        return False
    for suffix in _HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return True
    return False


def _validate_git_url(url: str) -> str:
    text = url.strip()
    if not text:
        raise ValueError("url is required")
    parsed = urlparse(text)
    if parsed.scheme != "https":
        raise ValueError("only https:// repository URLs are allowed")
    if not _host_allowed(parsed.hostname or ""):
        raise ValueError(
            "git host not allowed (supported: github.com, gitlab.com, bitbucket.org, codeberg.org)"
        )
    path = (parsed.path or "").strip("/")
    if not path:
        raise ValueError("url must include a repository path")
    return text


def _renamed_target(target: Path) -> Path:
    parent = target.parent
    stem = target.name
    index = 1
    while True:
        candidate = parent / f"{stem}-{index}"
        if not candidate.exists():
            return candidate
        index += 1


def _resolve_workspace_dest(paths, dest_arg: str) -> tuple[Path, str]:
    text = dest_arg.strip().replace("\\", "/").lstrip("/")
    if not text:
        raise ValueError("dest is required")
    if ".." in text.split("/"):
        raise ValueError("dest must not contain ..")
    if text.startswith("workspace/"):
        text = text.removeprefix("workspace/")
    resolved = paths.resolve_under_workspace(text, must_exist=False)
    rel = paths.to_agent_relative(resolved)
    return resolved, rel


def _resolve_evolve_dest(root: Path, dest_arg: str, allowed_scopes: frozenset[str]) -> tuple[Path, str]:
    text = dest_arg.strip().replace("\\", "/").lstrip("/")
    if not text:
        raise ValueError("dest is required")
    if ".." in text.split("/"):
        raise ValueError("dest must not contain ..")
    if not text.startswith("evolve/tools/"):
        raise ValueError("evolve_tools dest must start with evolve/tools/")
    match = _EVOLVE_PREFIX_RE.match(text)
    if not match:
        raise ValueError(
            "dest must match evolve/tools/<scope>/<name>/… "
            "(scope/name: lowercase letters, digits, underscore)"
        )
    scope = match.group("scope")
    if scope not in allowed_scopes:
        raise ValueError(f"scope {scope!r} is not allowed (known: {sorted(allowed_scopes)})")
    resolved = (root / text).resolve()
    tools_root = (root / "evolve" / "tools").resolve()
    try:
        resolved.relative_to(tools_root)
    except ValueError as exc:
        raise ValueError("dest must stay under evolve/tools/") from exc
    rel = text
    return resolved, rel


def _prepare_dest(dest: Path, on_conflict: str) -> tuple[Path, bool]:
    if not dest.exists():
        return dest, False
    if dest.is_file():
        raise ValueError(f"dest exists and is a file: {dest}")
    if not any(dest.iterdir()):
        return dest, False
    if on_conflict == "skip":
        raise ValueError(f"dest already exists and is not empty: {dest}")
    if on_conflict == "rename":
        renamed = _renamed_target(dest)
        return renamed, True
    if on_conflict == "overwrite":
        shutil.rmtree(dest)
        return dest, False
    raise ValueError(f"on_conflict must be one of {sorted(_VALID_CONFLICTS)}")


def run_git_clone(payload: dict[str, Any]) -> dict[str, Any]:
    url_raw = payload.get("url")
    dest_raw = payload.get("dest")
    target = payload.get("target")
    branch = payload.get("branch")
    tag = payload.get("tag")
    depth = int(payload.get("depth", 1))
    on_conflict = str(payload.get("on_conflict", "skip")).strip().lower() or "skip"
    dry_run = bool(payload.get("dry_run", False))

    if not isinstance(url_raw, str):
        return {"ok": False, "error": "url is required"}
    if not isinstance(dest_raw, str):
        return {"ok": False, "error": "dest is required"}
    if not isinstance(target, str) or target not in _VALID_TARGETS:
        return {"ok": False, "error": "target must be workspace or evolve_tools"}
    if on_conflict not in _VALID_CONFLICTS:
        return {"ok": False, "error": f"on_conflict must be one of {sorted(_VALID_CONFLICTS)}"}
    if depth < 1:
        depth = 1
    if branch is not None and not isinstance(branch, str):
        return {"ok": False, "error": "branch must be a string"}
    if tag is not None and not isinstance(tag, str):
        return {"ok": False, "error": "tag must be a string"}
    if branch and tag:
        return {"ok": False, "error": "give only one of branch or tag"}

    try:
        url = _validate_git_url(url_raw)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    root = _agent_root()
    paths = _load_paths().discover(start=root)

    try:
        if target == "workspace":
            dest_path, dest_rel = _resolve_workspace_dest(paths, dest_raw)
        else:
            allowed = _allowed_scopes(root / "evolve")
            dest_path, dest_rel = _resolve_evolve_dest(root, dest_raw, allowed)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}

    ref = (tag or branch or "").strip() or None
    cmd = ["git", "clone", f"--depth={depth}"]
    if ref:
        cmd.extend(["--branch", ref, "--single-branch"])
    cmd.extend([url, str(dest_path)])

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "target": target,
            "url": url,
            "dest": dest_rel,
            "command": cmd,
        }

    try:
        final_dest, renamed = _prepare_dest(dest_path, on_conflict)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "dest": dest_rel}

    if final_dest != dest_path:
        cmd[-1] = str(final_dest)
        dest_rel = paths.to_agent_relative(final_dest) if target == "workspace" else str(
            final_dest.relative_to(root)
        ).replace("\\", "/")

    final_dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=int(payload.get("timeout_sec", 300)),
            check=False,
            cwd=str(root),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "git clone timed out", "command": cmd, "dest": dest_rel}
    except OSError as exc:
        return {"ok": False, "error": str(exc), "command": cmd, "dest": dest_rel}

    stdout, stdout_trunc = _truncate(completed.stdout or "")
    stderr, stderr_trunc = _truncate(completed.stderr or "")
    result: dict[str, Any] = {
        "ok": completed.returncode == 0,
        "target": target,
        "url": url,
        "dest": dest_rel,
        "command": cmd,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }
    if renamed:
        result["renamed"] = True
    if stdout_trunc or stderr_trunc:
        result["truncated"] = True
    if completed.returncode != 0:
        result["error"] = stderr.strip() or f"git clone exited with code {completed.returncode}"
    return result


def main() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main

    run_tool_main(run_git_clone)


def _demo() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from paths import AgentPaths
    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    paths = AgentPaths.discover()
    registry = ToolRegistry.load(paths)
    tool = registry.get_evolved("git_clone")
    assert tool is not None and tool.status == "active"
    print("[PASS] registry loads git_clone (common, active)")

    dry_ws = run(
        {
            "tool_name": "git_clone",
            "arguments": {
                "url": "https://github.com/github/gitignore.git",
                "target": "workspace",
                "dest": "_git_clone_demo_ws",
                "dry_run": True,
            },
            "dry_run": True,
        },
        registry=registry,
    )
    assert dry_ws.ok and dry_ws.data.get("dry_run") is True
    assert dry_ws.data.get("target") == "workspace"
    print("[PASS] dry_run workspace")

    dry_ev = run(
        {
            "tool_name": "git_clone",
            "arguments": {
                "url": "https://github.com/github/gitignore.git",
                "target": "evolve_tools",
                "dest": "evolve/tools/common/git_clone_demo_ref",
                "dry_run": True,
            },
            "dry_run": True,
        },
        registry=registry,
    )
    assert dry_ev.ok and dry_ev.data.get("target") == "evolve_tools"
    print("[PASS] dry_run evolve_tools")

    bad_host = run(
        {
            "tool_name": "git_clone",
            "arguments": {
                "url": "https://evil.example.com/foo.git",
                "target": "workspace",
                "dest": "_bad",
            },
        },
        registry=registry,
    )
    assert not bad_host.ok and "not allowed" in (bad_host.error.message or "").lower()
    print("[PASS] host allowlist rejects unknown host")

    bad_scope = run(
        {
            "tool_name": "git_clone",
            "arguments": {
                "url": "https://github.com/github/gitignore.git",
                "target": "evolve_tools",
                "dest": "evolve/tools/unknown_scope/foo",
            },
        },
        registry=registry,
    )
    assert not bad_scope.ok
    print("[PASS] evolve scope allowlist")

    demo_dest = paths.workspace / "_git_clone_demo_ws"
    if demo_dest.is_dir():
        shutil.rmtree(demo_dest, ignore_errors=True)
    live = run(
        {
            "tool_name": "git_clone",
            "arguments": {
                "url": "https://github.com/github/gitignore.git",
                "target": "workspace",
                "dest": "_git_clone_demo_ws",
                "depth": 1,
            },
            "dry_run": False,
        },
        registry=registry,
    )
    assert live.ok, live.error.message if not live.ok else live.data
    assert (demo_dest / ".git").exists() or demo_dest.is_dir()
    print("[PASS] live shallow clone into workspace")
    shutil.rmtree(demo_dest, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
