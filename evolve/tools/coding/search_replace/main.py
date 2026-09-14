"""search_replace — literal multi-file find/replace under agent root."""

from __future__ import annotations

import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".tox", ".mypy_cache", ".pytest_cache",
    "data",
}
_TEXT_SUFFIXES = {
    ".py", ".pyi", ".md", ".txt", ".toml", ".json", ".yaml", ".yml",
    ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".rs", ".go",
    ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cs", ".sh", ".bat",
    ".ps1", ".ini", ".cfg", ".xml", ".sql", ".graphql", ".vue", ".svelte",
}


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


def _is_textish(path: Path) -> bool:
    if path.suffix.lower() in _TEXT_SUFFIXES:
        return True
    return path.name in {"Dockerfile", "Makefile", "LICENSE", "README", "README.md"}


def _glob_ok(path: Path, base: Path, glob_pat: str) -> bool:
    if not glob_pat or glob_pat in {"*", "**/*"}:
        return True
    name = path.name
    try:
        rel = path.relative_to(base).as_posix()
    except ValueError:
        rel = path.as_posix()
    patterns = [glob_pat]
    if not glob_pat.startswith("**/"):
        patterns.append(f"**/{glob_pat}")
    return any(fnmatch(name, p) or fnmatch(rel, p) or path.match(p) for p in patterns)


def _iter_files(bases: list[Path], glob_pat: str, max_files: int) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for base in bases:
        if base.is_file():
            if base not in seen and _is_textish(base):
                found.append(base)
                seen.add(base)
            if len(found) >= max_files:
                return found
            continue
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            if not _is_textish(p):
                continue
            if not _glob_ok(p, base, glob_pat):
                continue
            if p in seen:
                continue
            found.append(p)
            seen.add(p)
            if len(found) >= max_files:
                return found
    return found


def _default_scan_roots(agent_root: Path) -> list[Path]:
    candidates = [
        agent_root / "agent-core",
        agent_root / "evolve",
        agent_root / "desktop",
        agent_root / "docs",
    ]
    return [p for p in candidates if p.exists()] or [agent_root]


def run_search_replace(payload: dict[str, Any]) -> dict[str, Any]:
    find = payload.get("find")
    replace = payload.get("replace")
    if not isinstance(find, str) or find == "":
        return {"ok": False, "error": "find must be a non-empty string"}
    if not isinstance(replace, str):
        return {"ok": False, "error": "replace must be a string"}

    dry_run = bool(payload.get("dry_run", True))
    glob_pat = payload.get("glob") if isinstance(payload.get("glob"), str) else "**/*"
    encoding = payload.get("encoding") if isinstance(payload.get("encoding"), str) else "utf-8"
    max_files = payload.get("max_files", 200)
    max_repl = payload.get("max_replacements", 5000)
    if not isinstance(max_files, int) or max_files < 1 or max_files > 2000:
        return {"ok": False, "error": "max_files must be int in [1,2000]"}
    if not isinstance(max_repl, int) or max_repl < 1 or max_repl > 100000:
        return {"ok": False, "error": "max_replacements must be int in [1,100000]"}

    paths_mod = _load_paths().discover(start=_agent_root())
    agent_root = paths_mod.agent_root

    raw_paths = payload.get("paths")
    resolved: list[Path] = []
    if isinstance(raw_paths, list) and raw_paths:
        for item in raw_paths:
            if not isinstance(item, str) or not item.strip():
                return {"ok": False, "error": "paths entries must be non-empty strings"}
            text = item.strip().replace("\\", "/").lstrip("/")
            try:
                resolved.append(paths_mod.resolve_under_agent(text, must_exist=True))
            except Exception as exc:
                return {"ok": False, "error": f"path invalid or out of agent root: {item} ({exc})"}
    else:
        resolved = _default_scan_roots(agent_root)

    files = _iter_files(resolved, glob_pat or "**/*", max_files)
    hits: list[dict[str, Any]] = []
    total_repl = 0
    truncated = False

    for fp in files:
        try:
            text = fp.read_text(encoding=encoding)
        except (UnicodeDecodeError, OSError):
            continue
        count = text.count(find)
        if count == 0:
            continue
        apply_n = count
        if total_repl + apply_n > max_repl:
            truncated = True
            apply_n = max_repl - total_repl
            if apply_n <= 0:
                break
        new_text = text.replace(find, replace, apply_n)
        total_repl += apply_n
        try:
            rel = str(fp.relative_to(agent_root)).replace("\\", "/")
        except ValueError:
            rel = str(fp)
        entry: dict[str, Any] = {"path": rel, "replacements": apply_n}
        if dry_run:
            idx = text.find(find)
            start = max(0, idx - 40)
            end = min(len(text), idx + len(find) + 40)
            entry["preview"] = text[start:end].replace("\n", "\\n")
        else:
            fp.write_text(new_text, encoding=encoding, newline="")
        hits.append(entry)
        if truncated:
            break

    return {
        "ok": True,
        "dry_run": dry_run,
        "find_len": len(find),
        "replace_len": len(replace),
        "files_touched": len(hits),
        "total_replacements": total_repl,
        "truncated": truncated,
        "changes": hits[:100],
        "changes_omitted": max(0, len(hits) - 100),
    }


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main

    run_tool_main(run_search_replace)


if __name__ == "__main__":
    main()
