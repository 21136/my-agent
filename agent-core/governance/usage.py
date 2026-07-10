"""Derive entity usage from evolve_log (GOVERNANCE.md §3, §9.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tools.logging import read_events

MEMORIES_PREFIX = "memories/"


@dataclass
class UsageStats:
    use_count: int = 0
    last_used_at: str | None = None
    levels: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class UsageIndex:
    memories: dict[str, UsageStats]
    tools: dict[str, UsageStats]
    prompts: dict[str, UsageStats]
    pending_tool_specs: dict[str, str]


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _within_window(ts: datetime | None, *, cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    if ts is None:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts >= cutoff


def _record_usage(
    bucket: dict[str, UsageStats],
    entity_id: str,
    *,
    level: str,
    ts: str | None,
) -> None:
    stats = bucket.setdefault(entity_id, UsageStats())
    stats.use_count += 1
    stats.levels.add(level)
    if ts and (stats.last_used_at is None or ts > stats.last_used_at):
        stats.last_used_at = ts


def _memory_id_from_read_path(path_value: str, memory_id_by_path: dict[str, str]) -> str | None:
    normalized = path_value.strip().replace("\\", "/")
    for prefix in ("evolve/", "./evolve/"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    if not normalized.startswith(MEMORIES_PREFIX):
        return None
    if normalized in memory_id_by_path:
        return memory_id_by_path[normalized]
    if not normalized.endswith(".md"):
        return None
    return memory_id_by_path.get(normalized)


def _prompt_topic_from_loaded(path_value: str, prompt_path_by_topic: dict[str, str]) -> str | None:
    normalized = path_value.strip().replace("\\", "/")
    for topic, rel in prompt_path_by_topic.items():
        if normalized == rel or normalized.endswith("/" + rel):
            return topic
    return None


def build_usage_index(
    log_path: Path,
    *,
    memory_id_by_path: dict[str, str],
    prompt_path_by_topic: dict[str, str],
    log_window_days: int | None = 90,
) -> UsageIndex:
    cutoff: datetime | None = None
    if log_window_days is not None and log_window_days > 0:
        cutoff = datetime.now(UTC) - timedelta(days=log_window_days)

    memories: dict[str, UsageStats] = {}
    tools: dict[str, UsageStats] = {}
    prompts: dict[str, UsageStats] = {}
    pending_tool_specs: dict[str, str] = {}

    for event in read_events(log_path):
        event_type = str(event.get("event", ""))
        ts_raw = event.get("ts")
        if not isinstance(ts_raw, str):
            ts_raw = None
        ts_dt = _parse_ts(ts_raw)
        if not _within_window(ts_dt, cutoff=cutoff):
            continue

        if event_type == "entity_used":
            entity_id = str(event.get("entity_id", "")).strip()
            entity_type = str(event.get("type", "")).strip().lower()
            level = str(event.get("level", "")).strip().upper()
            if not entity_id:
                continue
            if entity_type == "memory":
                _record_usage(memories, entity_id, level=level or "L2", ts=ts_raw)
            elif entity_type == "tool":
                _record_usage(tools, entity_id, level=level or "L3", ts=ts_raw)
            elif entity_type == "prompt":
                _record_usage(prompts, entity_id, level=level or "L1", ts=ts_raw)
            continue

        if event_type == "topics_confirmed":
            loaded = event.get("prompt_files_loaded")
            if isinstance(loaded, list):
                for item in loaded:
                    if not isinstance(item, str):
                        continue
                    topic = _prompt_topic_from_loaded(item, prompt_path_by_topic)
                    if topic:
                        _record_usage(prompts, topic, level="L1", ts=ts_raw)
            listed = event.get("evolved_tools_listed")
            if isinstance(listed, list):
                for item in listed:
                    if isinstance(item, str) and item.strip():
                        _record_usage(tools, item.strip(), level="L3", ts=ts_raw)
            continue

        if event_type == "tool_call":
            if event.get("ok") is not True:
                continue
            evolved_tool = event.get("evolved_tool")
            if isinstance(evolved_tool, str) and evolved_tool.strip():
                _record_usage(tools, evolved_tool.strip(), level="L3", ts=ts_raw)
                continue
            if event.get("tool") != "read_file":
                continue
            arguments = event.get("arguments")
            if not isinstance(arguments, dict):
                continue
            path_value = arguments.get("path")
            if not isinstance(path_value, str):
                continue
            memory_id = _memory_id_from_read_path(path_value, memory_id_by_path)
            if memory_id:
                _record_usage(memories, memory_id, level="L2", ts=ts_raw)
            continue

        if event_type == "tool_spec_accepted":
            tool_name = str(event.get("tool_name", "")).strip()
            proposal_id = str(event.get("proposal_id", "")).strip()
            if tool_name:
                pending_tool_specs[tool_name] = proposal_id or "accepted"

    return UsageIndex(
        memories=memories,
        tools=tools,
        prompts=prompts,
        pending_tool_specs=pending_tool_specs,
    )


def effective_use_count(file_count: int, log_stats: UsageStats | None, *, min_level: str) -> int:
    log_count = 0
    if log_stats is not None:
        if min_level == "L1" and log_stats.levels.intersection({"L1", "L2", "L3", "L4"}):
            log_count = log_stats.use_count
        elif min_level == "L2" and log_stats.levels.intersection({"L2", "L3", "L4"}):
            log_count = log_stats.use_count
        elif min_level == "L3" and log_stats.levels.intersection({"L3", "L4"}):
            log_count = log_stats.use_count
    return max(file_count, log_count)


def effective_last_used_at(
    file_value: str | None,
    log_stats: UsageStats | None,
) -> str | None:
    if file_value and log_stats and log_stats.last_used_at:
        return max(file_value, log_stats.last_used_at)
    return file_value or (log_stats.last_used_at if log_stats else None)
