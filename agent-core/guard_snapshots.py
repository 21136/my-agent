"""Strict, non-following discovery for the two existing guard snapshot layouts."""
from __future__ import annotations

import re
import stat
from pathlib import Path

GUARDS = frozenset({".file-guard", ".session-guard"})
STAMP = re.compile(r"\d{8}T\d{6}Z\Z")
SESSION_FILES = frozenset({"meta.json", "messages.jsonl", "goal.md"})


def is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
    )


def safe_container(directory: Path) -> bool:
    """Only <repo>/data/<known-guard>/<key>, without linked ancestors."""
    directory = directory.absolute()
    if directory.parent.name not in GUARDS or directory.parent.parent.name != "data":
        return False
    try:
        return directory.is_dir() and not any(
            is_link(p) for p in (directory, *directory.parents)
        )
    except OSError:
        return False


def snapshot_payload(item: Path, directory: Path) -> list[Path] | None:
    """None means unknown/unsafe: preserve it, do not count it for pruning."""
    if not STAMP.fullmatch(item.name):
        return None
    try:
        if is_link(item) or item.resolve().parent != directory.resolve():
            return None
        if directory.parent.name == ".file-guard":
            return [item] if item.is_file() else None
        if not item.is_dir():
            return None
        files = list(item.iterdir())
        if all(
            p.name in SESSION_FILES and not is_link(p) and p.is_file()
            and p.resolve().parent == item.resolve()
            for p in files
        ):
            return files
    except OSError:
        pass
    return None
