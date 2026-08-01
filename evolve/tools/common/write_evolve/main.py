"""write_evolve — write UTF-8 text under evolve/tools/ only (TOOLS.md §8.1, T-508)."""

from __future__ import annotations

import base64
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

_VALID_CONFLICTS = frozenset({"skip", "rename", "overwrite"})
_MAX_BYTES = 512 * 1024
_ALLOWED_FILES = frozenset({"tool.toml", "main.py", "README.md"})
_PATH_RE = re.compile(
    r"^evolve/tools/(?P<scope>[a-z][a-z0-9_]*)/(?P<tool>[a-z][a-z0-9_]*)/(?P<file>[a-zA-Z0-9_.-]+)$"
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
    from paths import AgentPaths, PathOutOfBoundsError

    return AgentPaths, PathOutOfBoundsError


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


def _validate_relative_path(path_arg: str, *, allowed_scopes: frozenset[str]) -> tuple[str, str, str, str]:
    text = path_arg.strip().replace("\\", "/").lstrip("/")
    if not text:
        raise ValueError("path is required")
    if ".." in text.split("/"):
        raise ValueError("path must not contain ..")

    match = _PATH_RE.match(text)
    if not match:
        raise ValueError(
            "path must match evolve/tools/<scope>/<tool_name>/<file> "
            "(scope/tool: lowercase letters, digits, underscore; file: tool.toml | main.py | README.md)"
        )

    scope = match.group("scope")
    tool_name = match.group("tool")
    filename = match.group("file")

    if scope not in allowed_scopes:
        raise ValueError(f"scope {scope!r} is not allowed (known: {sorted(allowed_scopes)})")
    if filename not in _ALLOWED_FILES:
        raise ValueError(f"file must be one of {sorted(_ALLOWED_FILES)}")

    return text, scope, tool_name, filename


def _renamed_target(target: Path) -> Path:
    parent = target.parent
    stem = target.stem
    suffix = target.suffix
    index = 1
    while True:
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _resolve_content(
    payload: dict[str, Any],
    *,
    paths: Any,
    PathOutOfBoundsError: type[Exception],
) -> tuple[str | None, str | None]:
    """Return (content, error_message). Accept plain content, base64, or workspace file."""
    workspace_path = payload.get("content_workspace_path")
    if workspace_path is not None:
        if not isinstance(workspace_path, str) or not workspace_path.strip():
            return None, "content_workspace_path must be a non-empty string"
        rel = workspace_path.strip().replace("\\", "/").lstrip("/")
        if not rel.startswith("workspace/"):
            # Accept bare workspace-relative paths (e.g. _staging.toml) — LLMs often omit the prefix.
            rel = f"workspace/{rel}"
        try:
            source = paths.resolve_under_agent(rel, must_exist=True)
        except (PathOutOfBoundsError, TypeError, FileNotFoundError) as exc:
            return None, str(exc)
        if not source.is_file():
            return None, f"content_workspace_path is not a file: {rel}"
        try:
            return source.read_text(encoding="utf-8"), None
        except OSError as exc:
            return None, str(exc)

    content_b64 = payload.get("content_base64")
    if content_b64 is not None:
        if not isinstance(content_b64, str) or not content_b64.strip():
            return None, "content_base64 must be a non-empty string"
        try:
            decoded = base64.b64decode(content_b64, validate=True)
        except ValueError as exc:
            return None, f"content_base64 decode failed: {exc}"
        try:
            return decoded.decode("utf-8"), None
        except UnicodeDecodeError as exc:
            return None, f"content_base64 is not valid UTF-8: {exc}"

    content = payload.get("content")
    if isinstance(content, str):
        return content, None
    if "content" in payload:
        return None, "content must be a string"
    return None, "content, content_base64, or content_workspace_path is required"


def _validate_tool_manifest_content(
    content: str,
    *,
    target: Path,
    evolve_dir: Path,
) -> str | None:
    """Validate tool.toml body with registry rules; return error message or None."""
    from tools.registry import ToolManifestError, parse_tool_manifest

    probe = target.parent / f".{target.name}.probe"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text(content, encoding="utf-8")
        parse_tool_manifest(probe, evolve_dir=evolve_dir.resolve())
    except ToolManifestError as exc:
        return str(exc)
    except OSError as exc:
        return str(exc)
    finally:
        probe.unlink(missing_ok=True)
    return None


def _active_staged_without_main(
    content: str,
    *,
    path_arg: str,
    paths: Any,
) -> str | None:
    """Return error if active/staged tool.toml would be written before main.py exists."""
    try:
        manifest = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        return f"invalid tool.toml: {exc}"
    if not isinstance(manifest, dict):
        return "tool.toml must be a TOML table"
    tool_section = manifest.get("tool")
    if not isinstance(tool_section, dict):
        return None
    manifest_status = str(tool_section.get("status", "")).strip().lower()
    if manifest_status not in {"active", "staged"}:
        return None
    tool_dir_rel = path_arg.strip().replace("\\", "/").rsplit("/", 1)[0]
    main_rel = f"{tool_dir_rel}/main.py"
    main_target = paths.resolve_under_agent(main_rel, must_exist=False)
    if not main_target.is_file():
        return (
            f"cannot write active/staged tool.toml before main.py exists "
            f"({main_rel}); write main.py first or use status: draft"
        )
    return None


def run_write_evolve(payload: dict[str, Any]) -> dict[str, Any]:
    AgentPaths, PathOutOfBoundsError = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    path_arg = payload.get("path")
    if not isinstance(path_arg, str):
        return {"ok": False, "error": "path is required"}

    content, content_error = _resolve_content(
        payload,
        paths=paths,
        PathOutOfBoundsError=PathOutOfBoundsError,
    )
    if content_error is not None:
        return {"ok": False, "error": content_error}
    assert content is not None

    encoded = content.encode("utf-8")
    if len(encoded) > _MAX_BYTES:
        return {"ok": False, "error": f"content exceeds limit of {_MAX_BYTES} bytes"}

    on_conflict = payload.get("on_conflict", "skip")
    if not isinstance(on_conflict, str):
        return {"ok": False, "error": "on_conflict must be a string"}
    on_conflict = on_conflict.strip().lower()
    if on_conflict not in _VALID_CONFLICTS:
        return {"ok": False, "error": f"on_conflict must be one of {sorted(_VALID_CONFLICTS)}"}

    dry_run = bool(payload.get("dry_run", False))

    try:
        rel, _scope, _tool, filename = _validate_relative_path(
            path_arg,
            allowed_scopes=_allowed_scopes(paths.evolve),
        )
        target = paths.resolve_under_agent(rel, must_exist=False)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except PathOutOfBoundsError as exc:
        return {"ok": False, "error": str(exc)}
    except (TypeError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}

    try:
        target.resolve().relative_to(paths.evolve.resolve() / "tools")
    except ValueError:
        return {"ok": False, "error": "path must stay under evolve/tools/"}

    if filename == "tool.toml":
        manifest_error = _validate_tool_manifest_content(
            content,
            target=target,
            evolve_dir=paths.evolve,
        )
        if manifest_error is not None:
            return {"ok": False, "error": f"invalid tool.toml manifest: {manifest_error}"}
        if not dry_run:
            ordering_error = _active_staged_without_main(content, path_arg=rel, paths=paths)
            if ordering_error is not None:
                return {"ok": False, "error": ordering_error}

    if target.exists() and on_conflict == "skip":
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "skipped": True,
                "path": rel,
                "hint": "file exists; use on_conflict=overwrite to replace",
            }
        return {
            "ok": False,
            "error": f"file already exists: {rel}; use on_conflict=overwrite or rename",
            "skipped": True,
            "path": rel,
        }

    if target.exists() and on_conflict == "rename":
        target = _renamed_target(target)
        rel = paths.to_agent_relative(target)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "would_write": rel,
            "skipped": False,
            "bytes_written": len(encoded),
        }

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "written": rel,
        "skipped": False,
        "bytes_written": len(encoded),
    }


def main() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_write_evolve)


_MINIMAL_DRAFT_TOML = """[tool]
name = "write_evolve_demo"
description = "demo tool for write_evolve tests"
version = "1.0.0"
status = "draft"
topics = ["common"]

[entry]
type = "python"
path = "main.py"

[schema.input]
type = "object"

[schema.output]
type = "object"

[policy]
confirm = true
dry_run_supported = true
allow_approve_all = false
timeout_sec = 60
"""


def _demo() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from paths import AgentPaths
    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    paths = AgentPaths.discover()
    registry = ToolRegistry.load()
    tool = registry.get_evolved("write_evolve")
    assert tool is not None and tool.status == "active" and tool.policy.allow_approve_all is False
    print("[PASS] registry loads write_evolve (common, active, allow_approve_all=false)")

    rel_toml = "evolve/tools/common/write_evolve_demo/tool.toml"
    rel_py = "evolve/tools/common/write_evolve_demo/main.py"
    demo_toml = _MINIMAL_DRAFT_TOML
    demo_py = 'print("demo")\n'

    dry = run(
        {
            "tool_name": "write_evolve",
            "arguments": {"path": rel_toml, "content": demo_toml, "on_conflict": "overwrite"},
            "dry_run": True,
        },
        registry=registry,
    )
    assert dry.ok and dry.data.get("dry_run") is True
    print("[PASS] dry_run does not write")

    live = run(
        {
            "tool_name": "write_evolve",
            "arguments": {"path": rel_toml, "content": demo_toml, "on_conflict": "overwrite"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert live.ok
    print("[PASS] live write tool.toml under evolve/tools/")

    live_py = run(
        {
            "tool_name": "write_evolve",
            "arguments": {"path": rel_py, "content": demo_py, "on_conflict": "overwrite"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert live_py.ok
    print("[PASS] live write main.py")

    bad = run(
        {
            "tool_name": "write_evolve",
            "arguments": {"path": "workspace/hack.txt", "content": "x"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert not bad.ok
    print("[PASS] workspace path rejected")

    bad2 = run(
        {
            "tool_name": "write_evolve",
            "arguments": {"path": "evolve/prompts/hack.md", "content": "x"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert not bad2.ok
    print("[PASS] evolve outside tools/ rejected")

    import base64

    quoted_toml = _MINIMAL_DRAFT_TOML.replace('name = "write_evolve_demo"', 'name = "quoted_demo"').replace(
        "demo tool for write_evolve tests", "has single-quoted style description"
    )
    b64_live = run(
        {
            "tool_name": "write_evolve",
            "arguments": {
                "path": "evolve/tools/common/write_evolve_demo/main.py",
                "content_base64": base64.b64encode(b'print("quoted")\n').decode("ascii"),
                "on_conflict": "overwrite",
            },
            "dry_run": False,
        },
        registry=registry,
    )
    assert b64_live.ok
    b64_toml = run(
        {
            "tool_name": "write_evolve",
            "arguments": {
                "path": "evolve/tools/common/write_evolve_demo/tool.toml",
                "content_base64": base64.b64encode(quoted_toml.encode("utf-8")).decode("ascii"),
                "on_conflict": "overwrite",
            },
            "dry_run": False,
        },
        registry=registry,
    )
    assert b64_toml.ok
    print("[PASS] content_base64 writes main.py + tool.toml with quotes")

    # Bare workspace-relative path (no workspace/ prefix)
    staging = paths.workspace / "_write_evolve_staging.toml"
    staging.write_text(_MINIMAL_DRAFT_TOML.replace('name = "write_evolve_demo"', 'name = "staging_demo"'), encoding="utf-8")
    ws_live = run(
        {
            "tool_name": "write_evolve",
            "arguments": {
                "path": "evolve/tools/common/write_evolve_demo/tool.toml",
                "content_workspace_path": "_write_evolve_staging.toml",
                "on_conflict": "overwrite",
            },
            "dry_run": False,
        },
        registry=registry,
    )
    assert ws_live.ok, ws_live.error.message if not ws_live.ok else ""
    staging.unlink(missing_ok=True)
    print("[PASS] content_workspace_path accepts bare workspace-relative path")

    skip_second = run(
        {
            "tool_name": "write_evolve",
            "arguments": {"path": rel_toml, "content": demo_toml, "on_conflict": "skip"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert not skip_second.ok and "already exists" in (skip_second.error.message or "")
    print("[PASS] on_conflict=skip returns ok=false when file exists")

    bad_manifest = run(
        {
            "tool_name": "write_evolve",
            "arguments": {
                "path": "evolve/tools/common/write_evolve_demo/tool.toml",
                "content": '[tool]\nname = "broken"\n',
                "on_conflict": "overwrite",
            },
            "dry_run": False,
        },
        registry=registry,
    )
    assert not bad_manifest.ok and "invalid tool.toml manifest" in (bad_manifest.error.message or "")
    print("[PASS] invalid tool.toml rejected before write")

    demo_dir = _agent_root() / "evolve" / "tools" / "common" / "write_evolve_demo"
    if demo_dir.is_dir():
        for child in demo_dir.iterdir():
            child.unlink()
        demo_dir.rmdir()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
