"""LLM semantic audit for evolve governance (GOVERNANCE.md §7, TASKS T-603)."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Protocol

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from governance.collector import ReviewCollector, ReviewOptions
from governance.entities import (
    MemoryRecord,
    entity_matches_topics,
    scan_memory_records,
    scan_prompt_records,
)
from governance.report import ReviewReport, ReviewScope, ReviewSummary
from governance.renderer import ReviewRenderer, ReviewSink
from llm_client import LLMClient, LLMResponse, load_config
from paths import AgentPaths
from tools.logging import EvolveLog, utc_now_iso

AuditScopeKind = Literal["all", "prompts"]
AuditFormat = Literal["cli", "json", "markdown"]
_MAX_MEMORY_BODY_CHARS = 6000
_MAX_PROMPT_CHARS = 12000
_MAX_FINDINGS = 12
_VALID_SEVERITIES = frozenset({"high", "medium", "low"})
_VALID_CONFIDENCE = frozenset({"high", "medium", "low"})


class ChatClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse: ...


@dataclass(frozen=True, slots=True)
class AuditOptions:
    scope: AuditScopeKind = "all"
    topics: tuple[str, ...] = ()
    log_window_days: int = 90
    include_observation_period: bool = True
    only_llm: bool = False
    output_format: AuditFormat = "cli"
    output_path: str | None = None


@dataclass
class _MockLLM:
    """Scripted chat responses for demos."""

    responses: list[LLMResponse] = field(default_factory=list)

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        del messages, model, tools, temperature, response_format
        if not self.responses:
            raise RuntimeError("mock LLM has no scripted responses left")
        return self.responses.pop(0)


def build_audit_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="my-agent audit", description="LLM evolve audit (T-603)")
    parser.add_argument(
        "scope",
        nargs="?",
        choices=["prompts"],
        default=None,
        help="Limit audit to prompt files only (default: prompts + same-topic memories)",
    )
    parser.add_argument(
        "--topic",
        action="append",
        dest="topics",
        default=[],
        help="Limit scope to topic id (repeatable)",
    )
    parser.add_argument(
        "--log-window-days",
        type=int,
        default=90,
        help="Deterministic review log window in days (default: 90)",
    )
    parser.add_argument(
        "--no-observation",
        action="store_true",
        help="Omit observation-period block from deterministic collect()",
    )
    parser.add_argument(
        "--only-llm",
        action="store_true",
        help="CLI/markdown: print llm_findings only (skip deterministic blocks)",
    )
    parser.add_argument(
        "--format",
        choices=["cli", "json", "markdown"],
        default="cli",
        help="Output format (default: cli)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Write report to file (default: stdout; use - for stdout)",
    )
    return parser


def namespace_to_audit_options(args: argparse.Namespace) -> AuditOptions:
    scope: AuditScopeKind = "prompts" if args.scope == "prompts" else "all"
    return AuditOptions(
        scope=scope,
        topics=tuple(topic.strip() for topic in args.topics if topic.strip()),
        log_window_days=max(0, int(args.log_window_days)),
        include_observation_period=not args.no_observation,
        only_llm=bool(args.only_llm),
        output_format=args.format,
        output_path=args.output,
    )


def run_audit_from_namespace(
    args: argparse.Namespace,
    paths: AgentPaths | None = None,
    *,
    client: ChatClient | None = None,
) -> int:
    agent_paths = paths or AgentPaths.discover()
    options = namespace_to_audit_options(args)
    report = run_audit(agent_paths, options, client=client)
    content = ReviewRenderer.render(report, options.output_format, only_llm=options.only_llm)
    target = options.output_path
    if target == "-":
        target = None
    ReviewSink.emit(content, target)
    return 0


def run_audit(
    paths: AgentPaths,
    options: AuditOptions,
    *,
    client: ChatClient | None = None,
) -> ReviewReport:
    review_options = ReviewOptions(
        topics=options.topics,
        log_window_days=options.log_window_days,
        include_observation_period=options.include_observation_period,
    )
    base_report = ReviewCollector(paths).collect(review_options)
    llm_client = client if client is not None else LLMClient()
    findings = run_llm_audit(
        paths,
        base_report,
        options=options,
        client=llm_client,
    )
    report = merge_audit_findings(base_report, findings)
    EvolveLog.for_agent(paths).log_audit_completed(
        findings_count=len(findings),
        scope=_audit_scope_label(options),
        topics=list(options.topics),
    )
    return report


def merge_audit_findings(report: ReviewReport, findings: tuple[dict[str, Any], ...]) -> ReviewReport:
    summary = replace(report.summary, llm_findings_count=len(findings))
    scope = replace(report.scope, audit_ran=True)
    return replace(report, summary=summary, scope=scope, llm_findings=findings)


def run_llm_audit(
    paths: AgentPaths,
    report: ReviewReport,
    *,
    options: AuditOptions,
    client: ChatClient,
) -> tuple[dict[str, Any], ...]:
    corpus = build_audit_corpus(paths, report, options=options)
    if not corpus.strip():
        return ()

    messages = [
        {"role": "system", "content": _AUDIT_SYSTEM_PROMPT},
        {"role": "user", "content": corpus},
    ]
    model = load_config().model
    response = client.chat(
        messages,
        model=model,
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    content = response.content or ""
    return _parse_llm_findings_with_recovery(content, client=client, model=model)


def build_audit_corpus(
    paths: AgentPaths,
    report: ReviewReport,
    *,
    options: AuditOptions,
) -> str:
    evolve = paths.evolve
    scope_topics = options.topics

    prompts = [
        record
        for record in scan_prompt_records(evolve)
        if record.status == "active" and entity_matches_topics((record.topic,), scope_topics)
    ]
    memories: list[MemoryRecord] = []
    if options.scope == "all":
        memories = [
            record
            for record in scan_memory_records(evolve)
            if record.status == "active" and entity_matches_topics(record.topics, scope_topics)
        ]

    if not prompts and not memories:
        return ""

    lines: list[str] = [
        "请审阅以下 evolve 条目，找出规则矛盾、过时内容、该归档或该升格的问题。",
        "只输出 JSON：{\"findings\": [...]}，无问题时 findings 为空数组。",
        "",
        "## 确定性 review 摘要（上下文）",
        f"- suspect: {', '.join(item.id for item in report.suspect) or '(none)'}",
        f"- never-used: {', '.join(item.id for item in report.never_used) or '(none)'}",
        f"- hard conflicts: {len(report.conflicts_hard)}",
        f"- soft conflicts: {len(report.conflicts_soft)}",
        "",
    ]

    lines.append("## Prompt 文件")
    for record in prompts:
        path = evolve / record.path
        body = _read_bounded_text(path, _MAX_PROMPT_CHARS)
        lines.extend(
            [
                f"### topic={record.topic} path={record.path}",
                body or "(missing file)",
                "",
            ]
        )

    if memories:
        lines.append("## Active memories（同 scope topic）")
        for record in memories:
            path = evolve / record.path
            body = _memory_body(path)
            if len(body) > _MAX_MEMORY_BODY_CHARS:
                body = body[:_MAX_MEMORY_BODY_CHARS] + f"\n…(+{len(body) - _MAX_MEMORY_BODY_CHARS} chars)"
            lines.extend(
                [
                    f"### id={record.memory_id} topics={','.join(record.topics)}",
                    f"summary: {record.summary}",
                    f"path: {record.path}",
                    f"use_count: {record.use_count} last_used_at: {record.last_used_at or '(none)'}",
                    body or "(empty body)",
                    "",
                ]
            )

    return "\n".join(lines).strip()


def _parse_llm_findings_with_recovery(
    content: str,
    *,
    client: ChatClient,
    model: str,
) -> tuple[dict[str, Any], ...]:
    try:
        return parse_llm_findings(content)
    except AuditError as first_error:
        repair = client.chat(
            [
                {"role": "system", "content": _AUDIT_JSON_REPAIR_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"JSON parse error: {first_error}\n\n"
                        "Rewrite as one valid JSON object with the same findings:\n"
                        f"{content}"
                    ),
                },
            ],
            model=model,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        try:
            return parse_llm_findings(repair.content or "")
        except AuditError as second_error:
            print(
                f"warning: audit LLM returned invalid JSON ({second_error}); "
                "treating as no findings",
                file=sys.stderr,
            )
            return ()


def parse_llm_findings(content: str) -> tuple[dict[str, Any], ...]:
    payload = _extract_json_object(content)
    raw_findings = payload.get("findings", [])
    if raw_findings is None:
        return ()
    if not isinstance(raw_findings, list):
        raise AuditError("findings must be an array")

    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_findings[:_MAX_FINDINGS]):
        if not isinstance(raw, dict):
            continue
        finding = _normalize_finding(raw, index=index)
        if finding is not None:
            normalized.append(finding)
    return tuple(normalized)


def _normalize_finding(raw: dict[str, Any], *, index: int) -> dict[str, Any] | None:
    summary = raw.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None

    finding_id = raw.get("finding_id")
    if not isinstance(finding_id, str) or not finding_id.strip():
        finding_id = f"lf-{index + 1:03d}"

    kind = str(raw.get("kind", "other")).strip() or "other"
    severity = str(raw.get("severity", "medium")).strip().lower()
    if severity not in _VALID_SEVERITIES:
        severity = "medium"

    confidence = str(raw.get("confidence", "medium")).strip().lower()
    if confidence not in _VALID_CONFIDENCE:
        confidence = "medium"

    entities_raw = raw.get("entities", [])
    entities: list[dict[str, str]] = []
    if isinstance(entities_raw, list):
        for item in entities_raw:
            if not isinstance(item, dict):
                continue
            entity_type = str(item.get("type", "")).strip()
            if not entity_type:
                continue
            entity: dict[str, str] = {"type": entity_type}
            for key in ("id", "topic", "anchor"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    entity[key] = value.strip()
            entities.append(entity)

    evidence_raw = raw.get("evidence", [])
    evidence: list[str] = []
    if isinstance(evidence_raw, list):
        for item in evidence_raw:
            if isinstance(item, str) and item.strip():
                evidence.append(item.strip())

    suggested = raw.get("suggested_action", "")
    if not isinstance(suggested, str):
        suggested = str(suggested)

    return {
        "finding_id": finding_id.strip(),
        "kind": kind,
        "severity": severity,
        "entities": entities,
        "summary": summary.strip(),
        "evidence": evidence,
        "suggested_action": suggested.strip(),
        "confidence": confidence,
    }


def _unwrap_markdown_json(text: str) -> str:
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*", stripped, flags=re.IGNORECASE)
    if not fence:
        return stripped
    after = stripped[fence.end() :]
    close = after.rfind("```")
    if close != -1:
        return after[:close].strip()
    return after.strip()


def _balance_brace_slice(text: str, start: int) -> str | None:
    if start < 0 or start >= len(text) or text[start] != "{":
        return None

    depth = 0
    in_string = False
    escape = False
    index = start
    while index < len(text):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
        index += 1
    return None


def _repair_json_text(text: str) -> str:
    repaired = text.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace(
        "\u2019", "'"
    )
    repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
    return repaired


def _json_loads_object(text: str) -> dict[str, Any]:
    last_error: json.JSONDecodeError | None = None
    for candidate in (text, _repair_json_text(text)):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if not isinstance(payload, dict):
            raise AuditError("audit JSON must be an object")
        return payload
    if last_error is not None:
        raise AuditError(f"invalid JSON in audit response: {last_error}") from last_error
    raise AuditError("invalid JSON in audit response")


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = _unwrap_markdown_json(text)
    if not stripped:
        raise AuditError("empty LLM response")

    start = stripped.find("{")
    if start == -1:
        raise AuditError("response does not contain a JSON object")

    balanced = _balance_brace_slice(stripped, start)
    if balanced is not None:
        return _json_loads_object(balanced)

    end = stripped.rfind("}")
    if end <= start:
        raise AuditError("response does not contain a JSON object")
    return _json_loads_object(stripped[start : end + 1])


def _read_bounded_text(path: Path, limit: int) -> str:
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…(+{len(text) - limit} chars)"


def _memory_body(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.match(r"^---\s*\n.*?\n---\s*\n", text, flags=re.DOTALL)
    if match:
        return text[match.end() :].strip()
    return text.strip()


def _audit_scope_label(options: AuditOptions) -> str:
    if options.scope == "prompts":
        return "prompts"
    if options.topics:
        return "topic"
    return "all"


class AuditError(Exception):
    """Invalid audit operation or LLM payload."""


_AUDIT_SYSTEM_PROMPT = """你是 my-agent 进化层治理审计员（GOVERNANCE audit）。

任务：
1. 发现 prompt 段内、prompt↔memory、多 topic（若在输入内）的互相矛盾规则
2. 标记可能过时、不再适用（可对照 use_count / last_used_at）
3. 判断 memory 是否像硬规则应升格到 prompt，或 prompt 中的软事实应归档为 memory

约束：
- 只基于用户消息中的原文摘录 evidence，不要编造文件内容
- 无问题时返回 {"findings": []}
- 每条 finding 使用 GOVERNANCE §7.5 字段：finding_id, kind, severity, entities, summary, evidence, suggested_action, confidence
- kind 示例：contradiction, outdated, misplaced, overlap
- severity / confidence：high | medium | low
- entities 元素：{"type":"prompt|memory|tool","topic"?:...,"id"?:...,"anchor"?:...}
- 只输出**一个合法 JSON 对象**（不要用 markdown 代码块）；字符串内双引号写成 \\"
- evidence 优先短摘录；含反斜杠或引号时用单引号描述或省略特殊字符
- 不要改文件
"""

_AUDIT_JSON_REPAIR_PROMPT = """将用户给出的 audit 输出改写为**唯一**合法 JSON 对象。
格式：{"findings":[...]}，字段同 GOVERNANCE audit。
只输出 JSON，不要解释，不要 markdown 代码块。
"""


def run_audit_cli(argv: list[str] | None = None) -> int:
    parser = build_audit_parser()
    args = parser.parse_args(argv)
    return run_audit_from_namespace(args)


def _demo() -> None:
    paths = AgentPaths.discover()
    mock_response = json.dumps(
        {
            "findings": [
                {
                    "finding_id": "lf-demo-001",
                    "kind": "contradiction",
                    "severity": "high",
                    "entities": [
                        {"type": "prompt", "topic": "coding", "anchor": "默认编码"},
                        {"type": "memory", "id": "encoding-pref-demo"},
                    ],
                    "summary": "prompt 要求 UTF-8，memory 写 GBK",
                    "evidence": ["Python 3.12+", "默认使用 GBK 编码"],
                    "suggested_action": "archive memory | edit prompt",
                    "confidence": "medium",
                }
            ]
        },
        ensure_ascii=False,
    )

    with tempfile.TemporaryDirectory() as tmp:
        evolve = Path(tmp) / "evolve"
        data = Path(tmp) / "data"
        evolve.mkdir()
        data.mkdir()
        shutil.copy2(paths.evolve / "_index.toml", evolve / "_index.toml")
        shutil.copytree(paths.evolve / "prompts", evolve / "prompts")
        (evolve / "memories" / "coding").mkdir(parents=True)
        (evolve / "memories" / "coding" / "encoding-pref-demo.md").write_text(
            "---\n"
            "id: encoding-pref-demo\n"
            "topics: [coding]\n"
            "status: active\n"
            "summary: legacy encoding preference for demo audit\n"
            "---\n\n"
            "## 编码\n\n"
            "历史项目默认使用 GBK 编码保存文本文件。\n",
            encoding="utf-8",
        )
        demo_paths = AgentPaths(
            agent_root=Path(tmp),
            evolve=evolve,
            workspace=Path(tmp) / "workspace",
            data=data,
        )
        demo_paths.workspace.mkdir(parents=True, exist_ok=True)

        mock = _MockLLM(
            responses=[
                LLMResponse(
                    model="mock",
                    content=mock_response,
                    tool_calls=[],
                    finish_reason="stop",
                    usage=None,
                    raw={},
                )
            ]
        )
        options = AuditOptions(topics=("coding",))
        report = run_audit(demo_paths, options, client=mock)
        assert report.scope.audit_ran is True
        assert report.summary.llm_findings_count == 1
        assert report.llm_findings[0]["finding_id"] == "lf-demo-001"
        print("[PASS] T-603: audit merges llm_findings into ReviewReport")

        from tools.logging import EVENT_AUDIT_COMPLETED, read_events

        events = read_events(data / "evolve_log.jsonl")
        assert any(event.get("event") == EVENT_AUDIT_COMPLETED for event in events)
        print("[PASS] T-603: audit_completed logged")

        cli_text = ReviewRenderer.render(report, "cli")
        assert "== LLM findings ==" in cli_text
        assert "UTF-8" in cli_text or "GBK" in cli_text
        print("[PASS] T-603: render_cli includes llm_findings")

        only_llm = ReviewRenderer.render(report, "cli", only_llm=True)
        assert "== Never-used ==" not in only_llm
        assert "== LLM findings ==" in only_llm
        print("[PASS] T-603: --only-llm skips deterministic blocks")

        prompts_only = run_audit(
            demo_paths,
            AuditOptions(scope="prompts", topics=("coding",)),
            client=_MockLLM(
                responses=[
                    LLMResponse(
                        model="mock",
                        content='{"findings": []}',
                        tool_calls=[],
                        finish_reason="stop",
                        usage=None,
                        raw={},
                    )
                ]
            ),
        )
        assert prompts_only.scope.audit_ran is True
        print("[PASS] T-603: audit prompts scope runs with empty findings")

    parsed = parse_llm_findings(mock_response)
    assert len(parsed) == 1 and parsed[0]["severity"] == "high"
    print("[PASS] T-603: parse_llm_findings normalizes schema")

    messy = (
        "```json\n"
        '{"findings": [{"finding_id": "lf-x", "kind": "overlap", "severity": "low", '
        '"entities": [], "summary": "demo", "evidence": ["line with \\"quotes\\""], '
        '"suggested_action": "review", "confidence": "low",},],}\n'
        "```"
    )
    messy_parsed = parse_llm_findings(messy)
    assert len(messy_parsed) == 1
    print("[PASS] T-603: parse_llm_findings repairs fenced JSON with trailing commas")

    nested_fence = (
        "Here is the audit:\n```json\n"
        + json.dumps(
            {
                "findings": [
                    {
                        "finding_id": "lf-nested",
                        "kind": "contradiction",
                        "severity": "medium",
                        "entities": [{"type": "prompt", "topic": "coding"}],
                        "summary": "nested braces ok",
                        "evidence": ["a", "b"],
                        "suggested_action": "edit prompt",
                        "confidence": "medium",
                    }
                ]
            },
            ensure_ascii=False,
        )
        + "\n```\n"
    )
    assert len(parse_llm_findings(nested_fence)) == 1
    print("[PASS] T-603: balanced-brace extraction for markdown fenced JSON")

    if load_config().api_key:
        print("[SKIP] T-603: live LLM audit (optional: python my-agent audit --topic coding)")
    else:
        print("[SKIP] T-603: live LLM audit (LLM_API_KEY not set)")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        raise SystemExit(run_audit_cli())
