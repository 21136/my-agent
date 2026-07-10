"""ReviewReport renderers and output sink (GOVERNANCE.md §8.2, T-601a)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Literal

from governance.report import (
    EntityRef,
    HardConflict,
    PendingImplementation,
    ReviewReport,
    SoftConflict,
    report_to_dict,
)
from governance.git_hints import append_governance_git_footer

ReviewFormat = Literal["cli", "json", "markdown"]
VALID_FORMATS: frozenset[str] = frozenset({"cli", "json", "markdown"})


class ReviewRenderer:
    """Render a ReviewReport to cli, json, or markdown text."""

    @staticmethod
    def render(
        report: ReviewReport,
        mode: ReviewFormat | str = "cli",
        *,
        only_llm: bool = False,
    ) -> str:
        normalized = str(mode).strip().lower()
        if normalized not in VALID_FORMATS:
            raise ValueError(f"unsupported review format: {mode}")
        if normalized == "json":
            return render_json(report)
        if normalized == "markdown":
            return render_markdown(report, only_llm=only_llm)
        return render_cli(report, only_llm=only_llm)


class ReviewSink:
    """Write rendered review content to stdout or a file."""

    @staticmethod
    def emit(content: str, target: Path | str | None = None) -> None:
        if target is None:
            sys.stdout.write(content)
            return
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def render_json(report: ReviewReport, *, indent: int = 2) -> str:
    payload = report_to_dict(report)
    if indent:
        return json.dumps(payload, ensure_ascii=False, indent=indent) + "\n"
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def render_cli(report: ReviewReport, *, only_llm: bool = False) -> str:
    if only_llm:
        return _render_cli_llm_findings(report)

    lines: list[str] = []
    summary = report.summary
    scope = report.scope

    title = "my-agent audit" if scope.audit_ran else "my-agent review"
    lines.append(title)
    lines.append(f"schema: {report.schema_version}  generated: {report.generated_at}")
    if scope.topics:
        lines.append(f"scope topics: {', '.join(scope.topics)}")
    else:
        lines.append("scope topics: (all)")
    lines.append(
        "log window: "
        f"{scope.log_window_days}d  "
        f"entities: memories={summary.memories} prompts={summary.prompts} tools={summary.tools}"
    )
    lines.append("")

    lines.append("== Summary ==")
    lines.append(f"never-used: {summary.never_used_count}")
    lines.append(f"observation (<14d, unused): {len(report.observation_period)}")
    lines.append(f"suspect: {summary.suspect_count}")
    lines.append(f"conflicts (hard): {summary.conflict_hard_count}")
    lines.append(f"conflicts (soft): {summary.conflict_soft_count}")
    lines.append(f"pending implementation: {len(report.pending_implementation)}")
    if scope.audit_ran:
        lines.append(f"llm findings: {summary.llm_findings_count}")
    lines.append("")

    _render_cli_entity_section(lines, "Never-used", report.never_used)
    if scope.include_observation_period:
        _render_cli_entity_section(lines, "Observation", report.observation_period)
    _render_cli_pending(lines, report.pending_implementation)
    _render_cli_hard_conflicts(lines, report.conflicts_hard)
    _render_cli_soft_conflicts(lines, report.conflicts_soft)
    _render_cli_entity_section(lines, "Suspect", report.suspect)
    _render_cli_llm_findings_section(lines, report.llm_findings)

    if _report_is_empty(report):
        lines.append("(no findings)")

    append_governance_git_footer(lines, audit=scope.audit_ran)
    return "\n".join(lines).rstrip() + "\n"


def _render_cli_llm_findings(report: ReviewReport) -> str:
    lines: list[str] = ["my-agent audit", f"schema: {report.schema_version}  generated: {report.generated_at}", ""]
    _render_cli_llm_findings_section(lines, report.llm_findings)
    if not report.llm_findings:
        lines.append("(no llm findings)")
    append_governance_git_footer(lines, audit=True)
    return "\n".join(lines).rstrip() + "\n"


def render_markdown(report: ReviewReport, *, only_llm: bool = False) -> str:
    if only_llm:
        return _render_md_llm_findings(report)

    lines: list[str] = []
    summary = report.summary
    scope = report.scope

    title = "my-agent audit" if scope.audit_ran else "my-agent review"
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- schema: `{report.schema_version}`")
    lines.append(f"- generated: `{report.generated_at}`")
    if scope.topics:
        lines.append(f"- scope topics: {', '.join(scope.topics)}")
    else:
        lines.append("- scope topics: (all)")
    lines.append(f"- log window: {scope.log_window_days}d")
    lines.append(
        f"- entities: memories={summary.memories}, prompts={summary.prompts}, tools={summary.tools}"
    )
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- never-used: {summary.never_used_count}")
    lines.append(f"- observation (<14d, unused): {len(report.observation_period)}")
    lines.append(f"- suspect: {summary.suspect_count}")
    lines.append(f"- conflicts (hard): {summary.conflict_hard_count}")
    lines.append(f"- conflicts (soft): {summary.conflict_soft_count}")
    lines.append(f"- pending implementation: {len(report.pending_implementation)}")
    lines.append("")

    _render_md_entity_section(lines, "Never-used", report.never_used)
    if scope.include_observation_period:
        _render_md_entity_section(lines, "Observation", report.observation_period)
    _render_md_pending(lines, report.pending_implementation)
    _render_md_hard_conflicts(lines, report.conflicts_hard)
    _render_md_soft_conflicts(lines, report.conflicts_soft)
    _render_md_entity_section(lines, "Suspect", report.suspect)
    _render_md_llm_findings_section(lines, report.llm_findings)

    if _report_is_empty(report):
        lines.append("_No findings._")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_md_llm_findings(report: ReviewReport) -> str:
    lines: list[str] = [
        "# my-agent audit",
        "",
        f"- schema: `{report.schema_version}`",
        f"- generated: `{report.generated_at}`",
        "",
    ]
    _render_md_llm_findings_section(lines, report.llm_findings)
    if not report.llm_findings:
        lines.append("_No llm findings._")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _report_is_empty(report: ReviewReport) -> bool:
    return (
        not report.never_used
        and not report.observation_period
        and not report.pending_implementation
        and not report.conflicts_hard
        and not report.conflicts_soft
        and not report.suspect
        and not report.llm_findings
    )


def _render_cli_entity_section(lines: list[str], title: str, items: tuple[EntityRef, ...]) -> None:
    lines.append(f"== {title} ==")
    if not items:
        lines.append("(none)")
        lines.append("")
        return
    for item in items:
        topics = ", ".join(item.topics) if item.topics else "?"
        lines.append(f"- [{item.type}] {item.id} ({topics})")
        if item.summary:
            lines.append(f"  summary: {item.summary}")
        if item.path:
            lines.append(f"  path: {item.path}")
        if item.created_at:
            lines.append(f"  created: {item.created_at}")
        lines.append(f"  use_count: {item.use_count}")
    lines.append("")


def _render_cli_pending(lines: list[str], items: tuple[PendingImplementation, ...]) -> None:
    lines.append("== Pending implementation ==")
    if not items:
        lines.append("(none)")
        lines.append("")
        return
    for item in items:
        suffix = f" proposal={item.proposal_id}" if item.proposal_id else ""
        path = f" path={item.path}" if item.path else ""
        lines.append(f"- {item.tool_name} [{item.status}] — {item.reason}{suffix}{path}")
    lines.append("")


def _render_cli_hard_conflicts(lines: list[str], items: tuple[HardConflict, ...]) -> None:
    lines.append("== Conflicts (hard) ==")
    if not items:
        lines.append("(none)")
        lines.append("")
        return
    for item in items:
        topics = ", ".join(item.topics) if item.topics else "?"
        lines.append(f"- {item.id_a} <-> {item.id_b} ({topics})")
        lines.append(f"  {item.path_a}")
        lines.append(f"  {item.path_b}")
    lines.append("")


def _render_cli_soft_conflicts(lines: list[str], items: tuple[SoftConflict, ...]) -> None:
    lines.append("== Conflicts (soft) ==")
    if not items:
        lines.append("(none)")
        lines.append("")
        return
    for item in items:
        tokens = ", ".join(item.shared_tokens)
        lines.append(f"- {item.id_a} <-> {item.id_b} (topic={item.topic}; shared≥3: {tokens})")
    lines.append("")


def _render_cli_llm_findings_section(lines: list[str], items: tuple[dict[str, Any], ...]) -> None:
    lines.append("== LLM findings ==")
    if not items:
        lines.append("(none)")
        lines.append("")
        return
    for item in items:
        finding_id = item.get("finding_id", "?")
        kind = item.get("kind", "?")
        severity = item.get("severity", "?")
        confidence = item.get("confidence", "?")
        lines.append(f"- [{severity}/{confidence}] {finding_id} ({kind})")
        summary = item.get("summary")
        if isinstance(summary, str) and summary:
            lines.append(f"  summary: {summary}")
        suggested = item.get("suggested_action")
        if isinstance(suggested, str) and suggested:
            lines.append(f"  action: {suggested}")
        entities = item.get("entities")
        if isinstance(entities, list) and entities:
            labels = []
            for entity in entities:
                if not isinstance(entity, dict):
                    continue
                entity_type = entity.get("type", "?")
                label = str(entity.get("id") or entity.get("topic") or entity.get("anchor") or "?")
                labels.append(f"{entity_type}:{label}")
            lines.append(f"  entities: {', '.join(labels)}")
        evidence = item.get("evidence")
        if isinstance(evidence, list) and evidence:
            for quote in evidence[:2]:
                if isinstance(quote, str) and quote:
                    lines.append(f"  evidence: {quote}")
    lines.append("")


def _render_md_entity_section(lines: list[str], title: str, items: tuple[EntityRef, ...]) -> None:
    lines.append(f"## {title}")
    lines.append("")
    if not items:
        lines.append("(none)")
        lines.append("")
        return
    for item in items:
        topics = ", ".join(item.topics) if item.topics else "?"
        lines.append(f"### `{item.id}` ({item.type}, {topics})")
        lines.append("")
        if item.summary:
            lines.append(f"- summary: {item.summary}")
        if item.path:
            lines.append(f"- path: `{item.path}`")
        if item.created_at:
            lines.append(f"- created: `{item.created_at}`")
        lines.append(f"- use_count: {item.use_count}")
        lines.append("")


def _render_md_pending(lines: list[str], items: tuple[PendingImplementation, ...]) -> None:
    lines.append("## Pending implementation")
    lines.append("")
    if not items:
        lines.append("(none)")
        lines.append("")
        return
    for item in items:
        lines.append(f"- **{item.tool_name}** [{item.status}] — {item.reason}")
        if item.proposal_id:
            lines.append(f"  - proposal: `{item.proposal_id}`")
        if item.path:
            lines.append(f"  - path: `{item.path}`")
    lines.append("")


def _render_md_hard_conflicts(lines: list[str], items: tuple[HardConflict, ...]) -> None:
    lines.append("## Conflicts (hard)")
    lines.append("")
    if not items:
        lines.append("(none)")
        lines.append("")
        return
    for item in items:
        topics = ", ".join(item.topics) if item.topics else "?"
        lines.append(f"- `{item.id_a}` <-> `{item.id_b}` ({topics})")
        lines.append(f"  - `{item.path_a}`")
        lines.append(f"  - `{item.path_b}`")
    lines.append("")


def _render_md_soft_conflicts(lines: list[str], items: tuple[SoftConflict, ...]) -> None:
    lines.append("## Conflicts (soft)")
    lines.append("")
    if not items:
        lines.append("(none)")
        lines.append("")
        return
    for item in items:
        tokens = ", ".join(item.shared_tokens)
        lines.append(
            f"- `{item.id_a}` <-> `{item.id_b}` (topic={item.topic}; shared≥3: {tokens})"
        )
    lines.append("")


def _render_md_llm_findings_section(lines: list[str], items: tuple[dict[str, Any], ...]) -> None:
    lines.append("## LLM findings")
    lines.append("")
    if not items:
        lines.append("(none)")
        lines.append("")
        return
    for item in items:
        finding_id = item.get("finding_id", "?")
        kind = item.get("kind", "?")
        severity = item.get("severity", "?")
        confidence = item.get("confidence", "?")
        lines.append(f"### `{finding_id}` ({kind}, {severity}, confidence={confidence})")
        lines.append("")
        summary = item.get("summary")
        if isinstance(summary, str) and summary:
            lines.append(f"- summary: {summary}")
        suggested = item.get("suggested_action")
        if isinstance(suggested, str) and suggested:
            lines.append(f"- suggested_action: {suggested}")
        entities = item.get("entities")
        if isinstance(entities, list) and entities:
            for entity in entities:
                if not isinstance(entity, dict):
                    continue
                parts = [f"type={entity.get('type', '?')}"]
                for key in ("id", "topic", "anchor"):
                    value = entity.get(key)
                    if isinstance(value, str) and value:
                        parts.append(f"{key}={value}")
                lines.append(f"  - entity: {', '.join(parts)}")
        evidence = item.get("evidence")
        if isinstance(evidence, list) and evidence:
            for quote in evidence[:2]:
                if isinstance(quote, str) and quote:
                    lines.append(f"  - evidence: {quote}")
        lines.append("")
