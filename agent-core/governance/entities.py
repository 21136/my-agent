"""Scan evolve entities for governance review."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loader import load_topic_index
from tools.registry import scan_evolved_tools

MEMORIES_DIRNAME = "memories"
PROMPTS_DIRNAME = "prompts"
_ARCHIVED = frozenset({"archived"})


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    memory_id: str
    topics: tuple[str, ...]
    summary: str
    path: str
    status: str
    created_at: str | None
    use_count: int
    last_used_at: str | None
    conflicts_with: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PromptRecord:
    topic: str
    path: str
    status: str = "active"
    created_at: str | None = None
    use_count: int = 0
    last_used_at: str | None = None


@dataclass(frozen=True, slots=True)
class ToolRecord:
    name: str
    topics: tuple[str, ...]
    description: str
    status: str
    path: str
    created_at: str | None = None
    use_count: int = 0
    last_used_at: str | None = None


def _parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, flags=re.DOTALL)
    if not match:
        return {}
    block = match.group(1)
    result: dict[str, Any] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            result[key] = [item.strip() for item in inner.split(",") if item.strip()] if inner else []
        else:
            result[key] = value
    return result


def _frontmatter_topics(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    return ()


def _frontmatter_id_list(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    return ()


def _parse_int(value: Any, default: int = 0) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return default


def _iso_from_mtime(path: Path) -> str:
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=UTC).isoformat().replace("+00:00", "Z")


def _resolve_created_at(frontmatter: dict[str, Any], path: Path) -> str:
    raw = frontmatter.get("created_at")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return _iso_from_mtime(path)


def scan_memory_records(evolve_dir: Path) -> list[MemoryRecord]:
    root = evolve_dir / MEMORIES_DIRNAME
    if not root.is_dir():
        return []

    records: list[MemoryRecord] = []
    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        frontmatter = _parse_frontmatter(text)
        if not frontmatter:
            continue
        status = str(frontmatter.get("status", "active")).strip().lower()
        if status in _ARCHIVED:
            continue
        memory_id = frontmatter.get("id")
        summary = frontmatter.get("summary")
        if not isinstance(memory_id, str) or not memory_id.strip():
            continue
        if not isinstance(summary, str) or not summary.strip():
            continue
        rel_path = path.relative_to(evolve_dir).as_posix()
        records.append(
            MemoryRecord(
                memory_id=memory_id.strip(),
                topics=_frontmatter_topics(frontmatter.get("topics")),
                summary=summary.strip(),
                path=rel_path,
                status=status,
                created_at=_resolve_created_at(frontmatter, path),
                use_count=_parse_int(frontmatter.get("use_count")),
                last_used_at=(
                    str(frontmatter["last_used_at"]).strip()
                    if isinstance(frontmatter.get("last_used_at"), str)
                    and str(frontmatter["last_used_at"]).strip()
                    else None
                ),
                conflicts_with=_frontmatter_id_list(frontmatter.get("conflicts_with")),
            )
        )
    records.sort(key=lambda item: (item.memory_id, item.path))
    return records


def scan_prompt_records(evolve_dir: Path) -> list[PromptRecord]:
    records: list[PromptRecord] = []
    for entry in load_topic_index(evolve_dir):
        rel = entry.prompt.strip()
        if not rel:
            continue
        path = evolve_dir / rel
        created_at = _iso_from_mtime(path) if path.is_file() else None
        records.append(
            PromptRecord(
                topic=entry.id,
                path=rel,
                status="active" if path.is_file() else "missing",
                created_at=created_at,
            )
        )
    records.sort(key=lambda item: item.topic)
    return records


def scan_tool_records(evolve_dir: Path) -> list[ToolRecord]:
    tools = scan_evolved_tools(evolve_dir)
    records: list[ToolRecord] = []
    for tool in tools:
        if tool.status in _ARCHIVED:
            continue
        manifest_path = tool.manifest_path
        created_at = _iso_from_mtime(manifest_path) if manifest_path.is_file() else None
        records.append(
            ToolRecord(
                name=tool.name,
                topics=tool.topics,
                description=tool.description,
                status=tool.status,
                path=tool.relative_dir,
                created_at=created_at,
            )
        )
    records.sort(key=lambda item: item.name)
    return records


def entity_matches_topics(topics: tuple[str, ...], scope_topics: tuple[str, ...]) -> bool:
    if not scope_topics:
        return True
    if not topics:
        return False
    return any(topic in scope_topics for topic in topics)
