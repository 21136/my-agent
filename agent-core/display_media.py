"""Resolve local files for Desktop inline display (ask → show).

Display is broader than host-scope registration: agent-relative, host:, ~/, and
absolute files are allowed. Secrets and Windows system directories stay denied.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from host_scope import HostPathDeniedError, is_system_denied_path
from paths import AgentPaths, PathOutOfBoundsError
from tools.builtin.read_file import resolve_read_path

LOCAL_DISPLAY_PREFIX = "local:"
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})
VIDEO_SUFFIXES = frozenset({".mp4", ".webm", ".mov"})
MEDIA_SUFFIXES = IMAGE_SUFFIXES | VIDEO_SUFFIXES
TEXT_SUFFIXES = frozenset({
    ".txt", ".md", ".markdown", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".jsx",
    ".json", ".py", ".toml", ".css", ".html", ".htm", ".yml", ".yaml", ".xml",
    ".sh", ".bash", ".bat", ".ps1", ".rs", ".go", ".java", ".kt", ".sql",
    ".csv", ".log", ".ini", ".cfg", ".vue", ".svelte",
})
SHOWABLE_SUFFIXES = MEDIA_SUFFIXES | TEXT_SUFFIXES
_SECRET_DIR_NAMES = frozenset({".ssh", ".gnupg"})
_SECRET_FILE_NAMES = frozenset({".env", "id_rsa", "id_rsa.pub"})
_SKIP_DIR_NAMES = frozenset({
    ".git", ".svn", "node_modules", "AppData", "Application Data",
    "__pycache__", ".Trash", "$Recycle.Bin",
})
_MAX_SEARCH_DEPTH = 1
_MAX_SEARCH_FILES = 400
_MAX_CANDIDATES = 8
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]{2,}")
_WORD_RE = re.compile(r"[a-z0-9]{2,}", re.IGNORECASE)
_STOP_TERMS = frozenset({
    "图片", "照片", "图像", "视频", "影片", "文件", "那个", "那张", "那份",
    "这个", "这张", "看看", "打开", "展示", "显示", "拿出来", "给我", "一下",
    "电脑", "本地", "上面", "里头", "里面", "里的", "上的",
    "image", "video", "photo", "file", "show", "open",
})
_LOCATION_FOLDERS: dict[str, tuple[str, ...]] = {
    "desktop": ("Desktop", "桌面"),
    "downloads": ("Downloads", "下载"),
    "documents": ("Documents", "文档"),
    "pictures": ("Pictures", "图片", "照片"),
    "videos": ("Videos", "视频"),
}
_LOCATION_HINTS: dict[str, str] = {
    "桌面": "desktop",
    "desktop": "desktop",
    "下载": "downloads",
    "downloads": "downloads",
    "文档": "documents",
    "documents": "documents",
    "pictures": "pictures",
    "视频文件夹": "videos",
    "videos": "videos",
}


def is_image_suffix(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_SUFFIXES


def is_video_suffix(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_SUFFIXES


def is_media_suffix(path: Path) -> bool:
    return path.suffix.lower() in MEDIA_SUFFIXES


def is_text_suffix(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES


def is_showable_suffix(path: Path) -> bool:
    return path.suffix.lower() in SHOWABLE_SUFFIXES


def is_display_denied(resolved: Path) -> bool:
    """True for credentials, env files, and Windows system trees."""
    target = resolved.resolve()
    if is_system_denied_path(target):
        return True
    for part in target.parts:
        name = part.lower()
        if name in _SECRET_DIR_NAMES:
            return True
        if name in _SECRET_FILE_NAMES or name.startswith(".env."):
            return True
        if "credentials" in name:
            return True
    return False


def to_display_ref(paths: AgentPaths, resolved: Path) -> str:
    """Agent-relative when possible; otherwise ``local:<posix absolute>``."""
    try:
        return paths.to_agent_relative(resolved)
    except PathOutOfBoundsError:
        return f"{LOCAL_DISPLAY_PREFIX}{resolved.resolve().as_posix()}"


def resolve_display_path(paths: AgentPaths, raw: str) -> Path:
    """Resolve a user/agent path for read-only chat display."""
    stripped = raw.strip()
    if not stripped:
        raise FileNotFoundError(raw)
    if stripped.lower().startswith("host:"):
        resolved = resolve_read_path(paths, stripped)
        if is_display_denied(resolved):
            raise HostPathDeniedError(
                f"path denied for display: {raw!r}",
                path=raw,
                reason="denylist",
            )
        return resolved
    try:
        resolved = resolve_read_path(paths, stripped)
        if is_display_denied(resolved):
            raise HostPathDeniedError(
                f"path denied for display: {raw!r}",
                path=raw,
                reason="denylist",
            )
        return resolved
    except HostPathDeniedError:
        raise
    except (FileNotFoundError, PathOutOfBoundsError, OSError):
        candidate = Path(stripped).expanduser()
        if not candidate.is_absolute():
            raise FileNotFoundError(stripped) from None
        resolved = candidate.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(stripped) from None
        if is_display_denied(resolved):
            raise HostPathDeniedError(
                f"path denied for display: {raw!r}",
                path=raw,
                reason="denylist",
            )
        return resolved


def user_home() -> Path:
    raw = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser()
    return Path.home()


def user_media_roots(*, home: Path | None = None, location: str | None = None) -> list[tuple[str, Path]]:
    """Shallow personal folders: Desktop, Downloads, Documents, Pictures, Videos."""
    base = (home or user_home()).expanduser()
    names = _LOCATION_FOLDERS.get(location, ()) if location else ()
    extras: tuple[Path, ...] = ()
    if not names:
        names = tuple(name for group in _LOCATION_FOLDERS.values() for name in group)
        extras = (
            base / "OneDrive" / "Desktop",
            base / "OneDrive" / "Documents",
            base / "OneDrive" / "Pictures",
        )
    elif location == "desktop":
        extras = (base / "OneDrive" / "Desktop",)
    elif location == "documents":
        extras = (base / "OneDrive" / "Documents",)
    elif location == "pictures":
        extras = (base / "OneDrive" / "Pictures",)
    seen: set[Path] = set()
    roots: list[tuple[str, Path]] = []
    for name in names:
        resolved = _existing_dir(base / name)
        if resolved is None or resolved in seen:
            continue
        seen.add(resolved)
        roots.append((name, resolved))
    for extra in extras:
        resolved = _existing_dir(extra)
        if resolved is None or resolved in seen:
            continue
        seen.add(resolved)
        roots.append((extra.name, resolved))
    return roots


@dataclass(frozen=True, slots=True)
class UserMediaHit:
    path: Path
    folder: str
    score: float

    def to_public_dict(self) -> dict[str, str]:
        return {
            "path": self.path.resolve().as_posix(),
            "filename": self.path.name,
            "folder": self.folder,
        }


@dataclass(frozen=True, slots=True)
class UserMediaSearch:
    status: Literal["selected", "ambiguous", "not_found", "too_broad"]
    selected: UserMediaHit | None = None
    candidates: tuple[UserMediaHit, ...] = ()


def search_user_media(query: str, *, home: Path | None = None) -> UserMediaSearch:
    """Find image/video files in personal folders from a short user phrase."""
    location, tokens = _parse_media_query(query)
    if not tokens:
        return UserMediaSearch(status="too_broad")
    hits: list[UserMediaHit] = []
    scanned = 0
    for folder, root in user_media_roots(home=home, location=location):
        for path in _iter_media_files(root):
            scanned += 1
            if scanned > _MAX_SEARCH_FILES:
                break
            score = _filename_score(path.name, tokens)
            if score <= 0:
                continue
            hits.append(UserMediaHit(path=path, folder=folder, score=score))
        if scanned > _MAX_SEARCH_FILES:
            break
    hits.sort(key=lambda item: (-item.score, item.path.name.lower()))
    if not hits:
        return UserMediaSearch(status="not_found")
    top = hits[0]
    unique = [item for item in hits if item.score == top.score]
    if len(unique) == 1 and (len(hits) == 1 or top.score >= hits[1].score * 2 or top.score >= 90):
        return UserMediaSearch(status="selected", selected=top, candidates=(top,))
    return UserMediaSearch(
        status="ambiguous",
        candidates=tuple((unique[:_MAX_CANDIDATES] if unique else hits)[:_MAX_CANDIDATES]),
    )


def _existing_dir(path: Path) -> Path | None:
    try:
        if not path.is_dir():
            return None
        resolved = path.resolve()
    except OSError:
        return None
    if is_display_denied(resolved):
        return None
    return resolved


def _iter_media_files(root: Path):
    try:
        walker = os.walk(root, topdown=True, followlinks=False)
    except OSError:
        return
    for dirpath, dirnames, filenames in walker:
        current = Path(dirpath)
        try:
            depth = len(current.resolve().relative_to(root.resolve()).parts)
        except ValueError:
            dirnames[:] = []
            continue
        dirnames[:] = [
            name for name in dirnames
            if name not in _SKIP_DIR_NAMES and not name.startswith(".")
        ]
        if depth >= _MAX_SEARCH_DEPTH:
            dirnames[:] = []
        if is_display_denied(current):
            dirnames[:] = []
            continue
        for name in filenames:
            path = current / name
            if not is_showable_suffix(path) or is_display_denied(path):
                continue
            if not path.is_file():
                continue
            yield path


def _parse_media_query(query: str) -> tuple[str | None, tuple[str, ...]]:
    text = query.strip()
    if not text:
        return None, ()
    location: str | None = None
    lowered = text.casefold()
    for hint, key in sorted(_LOCATION_HINTS.items(), key=lambda item: len(item[0]), reverse=True):
        if hint.casefold() in lowered or hint in text:
            location = key
            text = text.replace(hint, " ")
            lowered = text.casefold()
            break
    for stop in sorted(_STOP_TERMS, key=len, reverse=True):
        text = re.sub(re.escape(stop), " ", text, flags=re.IGNORECASE)
    tokens: list[str] = []
    for term in (*_CJK_RUN_RE.findall(text), *_WORD_RE.findall(text)):
        normalized = term.casefold()
        if normalized in _STOP_TERMS or term in _STOP_TERMS:
            continue
        if normalized not in tokens:
            tokens.append(normalized)
    return location, tuple(tokens)


def _filename_score(filename: str, tokens: tuple[str, ...]) -> float:
    name = filename.casefold()
    stem = Path(filename).stem.casefold()
    joined = " ".join(tokens)
    if name == joined or stem == joined:
        return 100.0
    if all(token in name for token in tokens):
        if len(tokens) == 1 and (name.startswith(tokens[0]) or stem == tokens[0]):
            return 90.0
        return 50.0 + min(20.0, 4.0 * len(tokens))
    hits = sum(1 for token in tokens if token in name)
    if hits == 0:
        return 0.0
    return 10.0 * hits
