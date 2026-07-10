"""ReviewReport canonical schema v1.0 (GOVERNANCE.md §8)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

REVIEW_SCHEMA_VERSION = "1.0"
OBSERVATION_DAYS = 14
DEFAULT_LOG_WINDOW_DAYS = 90


@dataclass(frozen=True, slots=True)
class ReviewScope:
    log_window_days: int = DEFAULT_LOG_WINDOW_DAYS
    topics: tuple[str, ...] = ()
    include_observation_period: bool = True
    audit_ran: bool = False


@dataclass(frozen=True, slots=True)
class ReviewSummary:
    memories: int = 0
    prompts: int = 0
    tools: int = 0
    skills: int = 0
    never_used_count: int = 0
    suspect_count: int = 0
    conflict_hard_count: int = 0
    conflict_soft_count: int = 0
    llm_findings_count: int = 0


@dataclass(frozen=True, slots=True)
class EntityRef:
    type: str
    id: str
    topics: tuple[str, ...] = ()
    summary: str = ""
    path: str = ""
    status: str = "active"
    created_at: str | None = None
    use_count: int = 0
    last_used_at: str | None = None


@dataclass(frozen=True, slots=True)
class HardConflict:
    id_a: str
    id_b: str
    topics: tuple[str, ...]
    path_a: str = ""
    path_b: str = ""


@dataclass(frozen=True, slots=True)
class SoftConflict:
    id_a: str
    id_b: str
    topic: str
    shared_tokens: tuple[str, ...]
    path_a: str = ""
    path_b: str = ""


@dataclass(frozen=True, slots=True)
class PendingImplementation:
    tool_name: str
    status: str
    reason: str
    proposal_id: str | None = None
    path: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewReport:
    schema_version: str
    generated_at: str
    scope: ReviewScope
    summary: ReviewSummary
    never_used: tuple[EntityRef, ...] = ()
    observation_period: tuple[EntityRef, ...] = ()
    pending_implementation: tuple[PendingImplementation, ...] = ()
    conflicts_hard: tuple[HardConflict, ...] = ()
    conflicts_soft: tuple[SoftConflict, ...] = ()
    suspect: tuple[EntityRef, ...] = ()
    llm_findings: tuple[dict[str, Any], ...] = ()


def report_to_dict(report: ReviewReport) -> dict[str, Any]:
    """Serialize ReviewReport to JSON-compatible dict."""

    def _convert(value: Any) -> Any:
        if isinstance(value, tuple):
            return [_convert(item) for item in value]
        if hasattr(value, "__dataclass_fields__"):
            return {key: _convert(item) for key, item in asdict(value).items()}
        return value

    return _convert(report)
