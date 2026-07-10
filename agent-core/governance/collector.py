"""Collect deterministic governance findings (GOVERNANCE.md §5)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from governance.entities import (
    MemoryRecord,
    PromptRecord,
    ToolRecord,
    entity_matches_topics,
    scan_memory_records,
    scan_prompt_records,
    scan_tool_records,
)
from governance.report import (
    OBSERVATION_DAYS,
    EntityRef,
    HardConflict,
    PendingImplementation,
    ReviewReport,
    ReviewScope,
    ReviewSummary,
    REVIEW_SCHEMA_VERSION,
    SoftConflict,
)
from governance.usage import (
    UsageIndex,
    build_usage_index,
    effective_last_used_at,
    effective_use_count,
)
from paths import AgentPaths
from tools.logging import utc_now_iso
from tools.registry import ToolRegistry

_SOFT_TOKEN_RE = re.compile(r"\S+")
_MIN_TOKEN_LEN = 3
_MIN_SHARED_TOKENS = 3


@dataclass(frozen=True, slots=True)
class ReviewOptions:
    topics: tuple[str, ...] = ()
    log_window_days: int = 90
    include_observation_period: bool = True


class ReviewCollector:
    """Build a ReviewReport from evolve tree + evolve_log."""

    def __init__(self, paths: AgentPaths) -> None:
        self._paths = paths

    def collect(self, options: ReviewOptions | None = None) -> ReviewReport:
        opts = options or ReviewOptions()
        evolve_dir = self._paths.evolve
        log_path = self._paths.data / "evolve_log.jsonl"

        memories = scan_memory_records(evolve_dir)
        prompts = scan_prompt_records(evolve_dir)
        tools = scan_tool_records(evolve_dir)

        memory_id_by_path = {record.path: record.memory_id for record in memories}
        prompt_path_by_topic = {record.topic: record.path for record in prompts}

        usage = build_usage_index(
            log_path,
            memory_id_by_path=memory_id_by_path,
            prompt_path_by_topic=prompt_path_by_topic,
            log_window_days=opts.log_window_days,
        )

        scope_topics = opts.topics
        filtered_memories = [m for m in memories if entity_matches_topics(m.topics, scope_topics)]
        filtered_prompts = [p for p in prompts if not scope_topics or p.topic in scope_topics]
        filtered_tools = [t for t in tools if entity_matches_topics(t.topics, scope_topics)]

        now = datetime.now(UTC)
        observation_cutoff = now - timedelta(days=OBSERVATION_DAYS)

        never_used: list[EntityRef] = []
        observation: list[EntityRef] = []
        suspect: list[EntityRef] = []
        pending: list[PendingImplementation] = []

        active_memory_by_id = {
            record.memory_id: record for record in filtered_memories if record.status == "active"
        }

        for record in filtered_memories:
            ref = _memory_entity_ref(record, usage)
            if record.status == "suspect":
                suspect.append(ref)
                continue
            if record.status != "active":
                continue
            use_count = effective_use_count(
                record.use_count,
                usage.memories.get(record.memory_id),
                min_level="L2",
            )
            created = _parse_created_at(record.created_at)
            if use_count > 0:
                continue
            if _is_observation(created, observation_cutoff):
                if opts.include_observation_period:
                    observation.append(ref)
                continue
            never_used.append(ref)

        for record in filtered_prompts:
            if record.status != "active":
                continue
            ref = _prompt_entity_ref(record, usage)
            use_count = effective_use_count(
                record.use_count,
                usage.prompts.get(record.topic),
                min_level="L1",
            )
            created = _parse_created_at(record.created_at)
            if use_count > 0:
                continue
            if _is_observation(created, observation_cutoff):
                if opts.include_observation_period:
                    observation.append(ref)
                continue
            never_used.append(ref)

        for record in filtered_tools:
            ref = _tool_entity_ref(record, usage)
            if record.status == "suspect":
                suspect.append(ref)
                continue
            if record.status == "staged":
                pending.append(
                    PendingImplementation(
                        tool_name=record.name,
                        status="staged",
                        reason="tool.toml status=staged",
                        path=record.path,
                    )
                )
                continue
            if record.status != "active":
                continue
            use_count = effective_use_count(
                record.use_count,
                usage.tools.get(record.name),
                min_level="L3",
            )
            created = _parse_created_at(record.created_at)
            if use_count > 0:
                continue
            if _is_observation(created, observation_cutoff):
                if opts.include_observation_period:
                    observation.append(ref)
                continue
            never_used.append(ref)

        registry = ToolRegistry.load(self._paths)
        active_tool_names = {tool.name for tool in registry.evolved() if tool.status == "active"}
        for tool_name, proposal_id in usage.pending_tool_specs.items():
            if tool_name in active_tool_names:
                continue
            if scope_topics and not _tool_name_in_scope(tool_name, filtered_tools):
                continue
            pending.append(
                PendingImplementation(
                    tool_name=tool_name,
                    status="accepted_spec",
                    reason="tool_spec_accepted without active registration",
                    proposal_id=proposal_id or None,
                )
            )

        conflicts_hard = _collect_hard_conflicts(active_memory_by_id)
        conflicts_soft = _collect_soft_conflicts(active_memory_by_id)

        summary = ReviewSummary(
            memories=len(filtered_memories),
            prompts=len(filtered_prompts),
            tools=len(filtered_tools),
            skills=0,
            never_used_count=len(never_used),
            suspect_count=len(suspect),
            conflict_hard_count=len(conflicts_hard),
            conflict_soft_count=len(conflicts_soft),
            llm_findings_count=0,
        )
        scope = ReviewScope(
            log_window_days=opts.log_window_days,
            topics=scope_topics,
            include_observation_period=opts.include_observation_period,
            audit_ran=False,
        )
        return ReviewReport(
            schema_version=REVIEW_SCHEMA_VERSION,
            generated_at=utc_now_iso(),
            scope=scope,
            summary=summary,
            never_used=tuple(never_used),
            observation_period=tuple(observation),
            pending_implementation=tuple(pending),
            conflicts_hard=tuple(conflicts_hard),
            conflicts_soft=tuple(conflicts_soft),
            suspect=tuple(suspect),
            llm_findings=(),
        )


def _parse_created_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _is_observation(created: datetime | None, cutoff: datetime) -> bool:
    if created is None:
        return False
    return created >= cutoff


def _memory_entity_ref(record: MemoryRecord, usage: UsageIndex) -> EntityRef:
    stats = usage.memories.get(record.memory_id)
    return EntityRef(
        type="memory",
        id=record.memory_id,
        topics=record.topics,
        summary=record.summary,
        path=record.path,
        status=record.status,
        created_at=record.created_at,
        use_count=effective_use_count(record.use_count, stats, min_level="L2"),
        last_used_at=effective_last_used_at(record.last_used_at, stats),
    )


def _prompt_entity_ref(record: PromptRecord, usage: UsageIndex) -> EntityRef:
    stats = usage.prompts.get(record.topic)
    return EntityRef(
        type="prompt",
        id=record.topic,
        topics=(record.topic,),
        summary=f"topic prompt {record.path}",
        path=record.path,
        status=record.status,
        created_at=record.created_at,
        use_count=effective_use_count(record.use_count, stats, min_level="L1"),
        last_used_at=effective_last_used_at(record.last_used_at, stats),
    )


def _tool_entity_ref(record: ToolRecord, usage: UsageIndex) -> EntityRef:
    stats = usage.tools.get(record.name)
    return EntityRef(
        type="tool",
        id=record.name,
        topics=record.topics,
        summary=record.description,
        path=record.path,
        status=record.status,
        created_at=record.created_at,
        use_count=effective_use_count(record.use_count, stats, min_level="L3"),
        last_used_at=effective_last_used_at(record.last_used_at, stats),
    )


def _tool_name_in_scope(tool_name: str, tools: list[ToolRecord]) -> bool:
    return any(tool.name == tool_name for tool in tools)


def _summary_tokens(summary: str) -> set[str]:
    tokens: set[str] = set()
    for match in _SOFT_TOKEN_RE.findall(summary.lower()):
        if len(match) >= _MIN_TOKEN_LEN:
            tokens.add(match)
    return tokens


def _collect_hard_conflicts(active_by_id: dict[str, MemoryRecord]) -> list[HardConflict]:
    seen: set[tuple[str, str]] = set()
    results: list[HardConflict] = []
    for record in active_by_id.values():
        for other_id in record.conflicts_with:
            if other_id not in active_by_id:
                continue
            other = active_by_id[other_id]
            if record.memory_id not in other.conflicts_with:
                continue
            pair = tuple(sorted((record.memory_id, other_id)))
            if pair in seen:
                continue
            seen.add(pair)
            shared_topics = tuple(sorted(set(record.topics) & set(other.topics)))
            results.append(
                HardConflict(
                    id_a=pair[0],
                    id_b=pair[1],
                    topics=shared_topics,
                    path_a=active_by_id[pair[0]].path,
                    path_b=active_by_id[pair[1]].path,
                )
            )
    results.sort(key=lambda item: (item.id_a, item.id_b))
    return results


def _collect_soft_conflicts(active_by_id: dict[str, MemoryRecord]) -> list[SoftConflict]:
    results: list[SoftConflict] = []
    ids = sorted(active_by_id)
    for index, id_a in enumerate(ids):
        record_a = active_by_id[id_a]
        tokens_a = _summary_tokens(record_a.summary)
        if not tokens_a:
            continue
        for id_b in ids[index + 1 :]:
            record_b = active_by_id[id_b]
            shared_topics = set(record_a.topics) & set(record_b.topics)
            if not shared_topics:
                continue
            shared_tokens = sorted(tokens_a & _summary_tokens(record_b.summary))
            if len(shared_tokens) < _MIN_SHARED_TOKENS:
                continue
            topic = sorted(shared_topics)[0]
            results.append(
                SoftConflict(
                    id_a=id_a,
                    id_b=id_b,
                    topic=topic,
                    shared_tokens=tuple(shared_tokens),
                    path_a=record_a.path,
                    path_b=record_b.path,
                )
            )
    return results
