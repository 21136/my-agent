"""Protect agent source + session files from accidental truncation (FILE-GUARD)."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from evolve_tool_io import normalize_newlines

MIN_PROTECTED_BYTES = 8
_BACKUP_ROOT_NAME = ".file-guard"
_SESSION_BACKUP_ROOT = ".session-guard"

_PROTECTED_REL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^agent-core/"),
    re.compile(r"^evolve/tools/"),
    re.compile(r"^evolve/tool-catalog/"),
    re.compile(r"^evolve/prompts/"),
    re.compile(r"^evolve/_index"),
    re.compile(r"^data/sessions/"),
    re.compile(r"^data/llm_models\.json$"),
    re.compile(r"^data/state\.json$"),
    re.compile(r"^\.cursor/rules/"),
    re.compile(r"^docs/(MAP|TASKS|RUNTIME|TOOLS)\.md$"),
    re.compile(r"^desktop/src/"),
    re.compile(r"^desktop/electron/"),
    re.compile(r"^terminal-ui/src/"),
)


class ProtectedFileTruncateError(RuntimeError):
    """Refuse to wipe a protected file to empty / near-empty."""


def is_protected_agent_rel(rel_posix: str) -> bool:
    key = rel_posix.strip().replace("\\", "/").lstrip("/")
    if not key:
        return False
    return any(pattern.search(key) for pattern in _PROTECTED_REL_PATTERNS)


def is_protected_path(path: Path, agent_root: Path) -> bool:
    try:
        rel = path.resolve().relative_to(agent_root.resolve()).as_posix()
    except ValueError:
        return False
    return is_protected_agent_rel(rel)


def _backup_dir(agent_root: Path, path: Path) -> Path:
    digest = hashlib.sha256(
        path.resolve().as_posix().encode("utf-8"),
    ).hexdigest()[:16]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = agent_root / "data" / _BACKUP_ROOT_NAME / digest
    root.mkdir(parents=True, exist_ok=True)
    return root / stamp


def _snapshot_file(path: Path, agent_root: Path) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        return
    backup = _backup_dir(agent_root, path)
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup)
    _prune_backups(backup.parent, keep=20)


def _prune_backups(directory: Path, *, keep: int) -> None:
    files = sorted(directory.iterdir(), key=lambda item: item.name, reverse=True)
    for stale in files[keep:]:
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            pass


def guard_protected_truncate(
    path: Path,
    new_content: str,
    *,
    agent_root: Path,
    allow_truncate_to_empty: bool = False,
) -> None:
    if allow_truncate_to_empty or not is_protected_path(path, agent_root):
        return
    if not path.is_file():
        return
    old_size = path.stat().st_size
    new_size = len(normalize_newlines(new_content).encode("utf-8"))
    if old_size > MIN_PROTECTED_BYTES and new_size <= MIN_PROTECTED_BYTES:
        raise ProtectedFileTruncateError(
            f"refuse to truncate protected file to {new_size} bytes "
            f"(was {old_size}): {path}"
        )


def atomic_write_text(
    path: Path,
    content: str,
    *,
    agent_root: Path | None = None,
    allow_truncate_to_empty: bool = False,
    backup: bool = True,
) -> None:
    """LF-normalized atomic write with optional protected-path truncate guard."""
    normalized = normalize_newlines(content)
    root = agent_root
    if root is None:
        root = _discover_agent_root(path)
    if root is not None:
        guard_protected_truncate(
            path,
            normalized,
            agent_root=root,
            allow_truncate_to_empty=allow_truncate_to_empty,
        )
        if backup and path.is_file() and path.stat().st_size > 0:
            _snapshot_file(path, root)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(normalized)
        tmp.replace(path)
    finally:
        if tmp.is_file():
            try:
                tmp.unlink()
            except OSError:
                pass


def backup_session_files(session_dir: Path, agent_root: Path) -> None:
    """Keep a rolling on-disk copy of session meta + messages after each save."""
    if not session_dir.is_dir():
        return
    rel = session_dir.resolve().relative_to(agent_root.resolve()).as_posix()
    if not rel.startswith("data/sessions/"):
        return
    backup_root = agent_root / "data" / _SESSION_BACKUP_ROOT / session_dir.name
    latest = backup_root / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    for name in ("meta.json", "messages.jsonl", "goal.md"):
        src = session_dir / name
        if src.is_file() and src.stat().st_size > 0:
            shutil.copy2(src, latest / name)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    snap = backup_root / stamp
    if latest.is_dir():
        shutil.copytree(latest, snap, dirs_exist_ok=True)
    _prune_backups(backup_root, keep=12)


def _discover_agent_root(path: Path) -> Path | None:
    current = path.resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        evolve = directory / "evolve"
        if (evolve / "_index.core.toml").is_file() or (evolve / "_index.toml").is_file():
            return directory
    return None
