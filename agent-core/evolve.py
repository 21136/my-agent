"""Evolution proposals: checkpoint + generation + review (EVOLVE.md §2–7, TASKS T-402/T-404/T-405/T-407)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_client import LLMClient, LLMResponse, load_config, resolve_session_model
from loader import (
    TopicIndexEntry,
    load_topic_index,
    scan_memory_index,
)
from paths import AgentPaths
from router import registered_topic_ids
from session import Session, SessionMeta, utc_now_iso
from tools.logging import (
    EVENT_CHECKPOINT_OPENED,
    EVENT_EVOLVE_ACCEPTED,
    EVENT_EVOLVE_REJECTED,
    EVENT_PROPOSAL_CREATED,
    EVENT_PROPOSAL_SUPERSEDED,
    EVENT_TOOL_SPEC_ACCEPTED,
    EvolveLog,
    read_events,
)
from tools.registry import ToolRegistry

PROPOSALS_DIRNAME = "proposals"
PROPOSALS_ARCHIVE_DIRNAME = "archive"
MAX_PROPOSALS_PER_CHECKPOINT = 2
MAX_EVIDENCE_PER_PROPOSAL = 2
RECENT_MESSAGE_TURNS = 12
VALID_PROPOSAL_TYPES = frozenset({"memory", "prompt_patch", "tool_suggestion"})
VALID_MODES = frozenset({"create", "update"})
TriggeredBy = Literal["explicit", "llm_offer"]

CHECKPOINT_USER_TEMPLATE = """用户触发词：{trigger_phrase}
用户原句：{user_line}

请根据上下文生成进化 proposal（JSON）。最多 {max_proposals} 条。
topics 只能来自：{valid_topics}
默认 topic 优先用本次会议 topics：{session_topics}

输出 JSON 对象（不要 markdown fence）：
{{
  "proposals": [
    {{
      "type": "memory | prompt_patch | tool_suggestion",
      "mode": "create | update",
      "topics": ["coding"],
      "summary": "一行摘要",
      "proposed_markdown": "写入 ## Proposed 段的 markdown（type 模板见 EVOLVE §4.4）",
      "memory_id": "memory create/update 时必填",
      "anchor": "prompt_patch 时 ## 标题",
      "tool_name": "tool_suggestion 时必填",
      "evidence": [
        {{"role": "user", "quote": "对话原文摘录", "ref": "messages.jsonl#行号"}}
      ]
    }}
  ],
  "user_message": "给用户的一句话；若已有条目覆盖则说明已有 id、proposals 可为空"
}}

规则：
- 每条 proposal 的 evidence 最多 {max_evidence} 条，**必须逐字摘自**下方 [最近对话] 或 digest.md 原文
- evidence 的 quote **禁止**改写、总结、翻译；禁止「用户表示…」等自评式表述
- ref 须为匹配到的 messages.jsonl#行号 或 digest.md#行号
- 若久远记忆或 prompt 已覆盖，proposals 留空并在 user_message 指向已有 id
- memory_id 建议格式 {{topic}}-{{slug}}（如 workflow-agent-birthday），勿用 memory- 前缀
- user_message **不得**声称「已记住」「已写入 evolve」；应说明已生成 **pending** proposal、待用户审阅接受（T-404）
- memory create 的 proposed_markdown 须含 YAML frontmatter（id/topics/status/summary）+ ## 背景
- prompt_patch 仅 append_section；proposed_markdown 以 ## 标题 开头
- tool_suggestion 无代码，用 §4.4 模板（意图/输入输出/步骤/放置）
"""


class EvolveError(ValueError):
    """Invalid evolve operation or LLM payload."""


class ChatClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse: ...


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    role: str
    quote: str
    ref: str


@dataclass(frozen=True, slots=True)
class DialogueLine:
    role: str
    text: str
    ref: str
    source: Literal["messages", "digest"]


_SELF_EVAL_QUOTE_RE = re.compile(
    r"^(用户(表示|希望|要求|想要|认为|说)|"
    r"the user (said|wants|asked|mentioned)|"
    r"助手(建议|认为|回复)|"
    r"assistant (said|suggested|replied)|"
    r"我(理解|认为)为|总结[:：])",
    re.IGNORECASE,
)
_ANCHOR_PREFIX = "[本次会议上下文]"


@dataclass(frozen=True, slots=True)
class PendingProposalIndex:
    proposal_id: str
    summary: str
    target_path: str
    status: str


@dataclass(frozen=True, slots=True)
class ProposalDraft:
    proposal_id: str
    seq: int
    date_prefix: str
    type: str
    mode: str
    topics: tuple[str, ...]
    summary: str
    proposed_markdown: str
    target: dict[str, Any]
    evidence: tuple[EvidenceItem, ...]
    fingerprint: str
    evidence_fingerprints: tuple[str, ...]
    conversation_id: str
    checkpoint_at: str
    triggered_by: TriggeredBy
    trigger_phrase: str


@dataclass(frozen=True, slots=True)
class CheckpointResult:
    written_paths: tuple[Path, ...]
    proposal_ids: tuple[str, ...]
    user_message: str


@dataclass(frozen=True, slots=True)
class ProposalRecord:
    path: Path
    proposal_id: str
    status: str
    type: str
    mode: str
    topics: tuple[str, ...]
    target: dict[str, Any]
    summary: str
    proposed_markdown: str
    conversation_id: str
    raw_text: str
    fingerprint: str = ""
    evidence_fingerprints: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DedupGateResult:
    allow: bool
    dedup: str
    reason: str = ""
    supersede: tuple[ProposalRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class ReviewResult:
    proposal_id: str
    status: str
    message: str
    routed_path: str | None = None


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
    ) -> LLMResponse:
        if not self.responses:
            raise RuntimeError("mock LLM has no scripted responses left")
        return self.responses.pop(0)


def proposals_dir(paths: AgentPaths) -> Path:
    return paths.evolve / PROPOSALS_DIRNAME


def normalize_fingerprint_text(text: str) -> str:
    """EVOLVE §6.2: lowercase, strip punctuation/whitespace, first 120 chars."""
    cleaned = re.sub(r"[\s\W_]+", "", text.casefold(), flags=re.UNICODE)
    return cleaned[:120]


def content_fingerprint(text: str) -> str:
    norm = normalize_fingerprint_text(text)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def evidence_fingerprint(quote: str) -> str:
    return hashlib.sha256(quote.strip().encode("utf-8")).hexdigest()[:16]


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _quote_matches_message(message_text: str, quote: str) -> bool:
    if not quote.strip():
        return False
    if quote in message_text:
        return True
    return _collapse_ws(quote) in _collapse_ws(message_text)


def _canonical_verbatim_quote(message_text: str, quote: str) -> str:
    if quote in message_text:
        return quote
    words = quote.split()
    if not words:
        return quote
    pattern = r"\s*".join(re.escape(word) for word in words)
    match = re.search(pattern, message_text)
    if match:
        return match.group(0)
    return quote


def _looks_like_self_eval_quote(quote: str) -> bool:
    text = quote.strip()
    if not text:
        return True
    if _SELF_EVAL_QUOTE_RE.search(text):
        return True
    return len(text) > 240


def build_dialogue_corpus(session: Session) -> tuple[DialogueLine, ...]:
    """Verbatim evidence pool from messages.jsonl + digest.md (EVOLVE §5)."""
    lines: list[DialogueLine] = []
    for index, message in enumerate(session.messages, start=1):
        role = str(message.get("role", "")).strip() or "?"
        content = message.get("content")
        if not isinstance(content, str):
            continue
        text = content.strip()
        if not text or text.startswith(_ANCHOR_PREFIX):
            continue
        lines.append(
            DialogueLine(
                role=role,
                text=text,
                ref=f"messages.jsonl#{index}",
                source="messages",
            )
        )

    digest_path = session.digest_path
    if digest_path.is_file():
        try:
            digest_text = digest_path.read_text(encoding="utf-8")
        except OSError:
            digest_text = ""
        for index, raw_line in enumerate(digest_text.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            lines.append(
                DialogueLine(
                    role="user",
                    text=line,
                    ref=f"digest.md#{index}",
                    source="digest",
                )
            )
    return tuple(lines)


def match_dialogue_quote(quote: str, corpus: tuple[DialogueLine, ...]) -> DialogueLine | None:
    """Return the best corpus line containing *quote* verbatim (prefer latest user)."""
    matches = [line for line in corpus if _quote_matches_message(line.text, quote)]
    if not matches:
        return None
    user_matches = [line for line in matches if line.role == "user"]
    pool = user_matches or [line for line in matches if line.role == "assistant"]
    if not pool:
        return None
    return pool[-1]


def resolve_evidence_for_proposal(
    session: Session,
    raw_evidence: Any,
    *,
    user_line: str,
    trigger_phrase: str,
) -> tuple[EvidenceItem, ...]:
    """Validate LLM evidence against dialogue corpus; fallback to trigger/user line (T-405)."""
    corpus = build_dialogue_corpus(session)
    validated: list[EvidenceItem] = []
    seen_quotes: set[str] = set()

    if isinstance(raw_evidence, list):
        for item in raw_evidence:
            if len(validated) >= MAX_EVIDENCE_PER_PROPOSAL:
                break
            parsed = _normalize_evidence(item, fallback_ref="messages.jsonl#?")
            if parsed is None or _looks_like_self_eval_quote(parsed.quote):
                continue
            matched = match_dialogue_quote(parsed.quote, corpus)
            if matched is None:
                continue
            quote = _canonical_verbatim_quote(matched.text, parsed.quote)
            if quote in seen_quotes:
                continue
            seen_quotes.add(quote)
            validated.append(
                EvidenceItem(role=matched.role, quote=quote, ref=matched.ref)
            )

    if not validated:
        fallback = _fallback_evidence(
            corpus,
            user_line=user_line,
            trigger_phrase=trigger_phrase,
        )
        validated.extend(fallback)

    return tuple(validated[:MAX_EVIDENCE_PER_PROPOSAL])


def _fallback_evidence(
    corpus: tuple[DialogueLine, ...],
    *,
    user_line: str,
    trigger_phrase: str,
) -> list[EvidenceItem]:
    """Derive evidence from user_line / trigger when LLM quotes fail validation."""
    candidates: list[str] = []
    stripped = user_line.strip()
    if trigger_phrase and stripped.casefold().startswith(trigger_phrase.casefold()):
        remainder = stripped[len(trigger_phrase) :].strip(" ：:，,")
        if remainder:
            candidates.append(remainder)
    if stripped:
        candidates.append(stripped)
    if trigger_phrase.strip():
        candidates.append(trigger_phrase.strip())

    for candidate in candidates:
        matched = match_dialogue_quote(candidate, corpus)
        if matched is not None:
            quote = _canonical_verbatim_quote(matched.text, candidate)
            return [EvidenceItem(role=matched.role, quote=quote, ref=matched.ref)]

    for line in reversed(corpus):
        if line.role == "user" and line.source == "messages":
            return [EvidenceItem(role=line.role, quote=line.text, ref=line.ref)]

    if corpus:
        last = corpus[-1]
        return [EvidenceItem(role=last.role, quote=last.text, ref=last.ref)]
    return [
        EvidenceItem(
            role="user",
            quote=trigger_phrase or user_line.strip() or "(empty)",
            ref="messages.jsonl#?",
        )
    ]


def slugify(text: str, *, max_len: int = 48) -> str:
    slug = re.sub(r"[^\w\-]+", "-", text.casefold())
    slug = re.sub(r"-+", "-", slug).strip("-")
    if not slug:
        return "item"
    return slug[:max_len].strip("-")


_ESCALATION_OFFER_RE = re.compile(
    r"(要写进|写进\s*(?:evolve|prompt|记忆|memories?))"
    r"|(?:沉淀|固化).{0,24}(?:吗|？|\?)"
    r"|要不要.{0,32}(?:记住|沉淀|写进)",
    re.IGNORECASE,
)


def detect_escalation_offer(assistant_text: str) -> bool:
    """Detect oral evolve offer in assistant reply (EVOLVE §3.2 hop 1)."""
    text = assistant_text.strip()
    if not text:
        return False
    return _ESCALATION_OFFER_RE.search(text) is not None


def note_escalation_offer(session: Session) -> None:
    """Mark session waiting for weak confirm; consumes the one-per-session offer slot."""
    session.meta.evolve_offer_pending = True
    session.meta.evolve_offer_used = True
    session.save()


def clear_escalation_offer(session: Session) -> None:
    session.meta.evolve_offer_pending = False
    session.save()


def reset_evolve_escalation(session: Session) -> None:
    """Reset on 新会话 (EVOLVE §3.2 frequency counter)."""
    session.meta.evolve_offer_pending = False
    session.meta.evolve_offer_used = False


def dedupe_drafts_by_fingerprint(
    drafts: tuple[ProposalDraft, ...],
) -> tuple[ProposalDraft, ...]:
    """Same-checkpoint near-duplicate summaries → keep one (EVOLVE §3.4)."""
    seen: set[str] = set()
    kept: list[ProposalDraft] = []
    for draft in drafts:
        if draft.fingerprint in seen:
            continue
        seen.add(draft.fingerprint)
        kept.append(draft)
        if len(kept) >= MAX_PROPOSALS_PER_CHECKPOINT:
            break
    return tuple(kept)


def proposal_slug(draft: ProposalDraft) -> str:
    """Filename slug; avoid repeating type prefix (e.g. memory-memory-…)."""
    if draft.type == "memory":
        memory_id = str(draft.target.get("memory_id", "")).strip()
        if memory_id:
            topic = str(draft.target.get("topic", "")).strip()
            if topic and memory_id.casefold().startswith(f"{topic}-"):
                return slugify(memory_id[len(topic) + 1 :])
            for prefix in ("memory-", "mem-"):
                if memory_id.casefold().startswith(prefix):
                    return slugify(memory_id[len(prefix) :])
            return slugify(memory_id)
    if draft.type == "tool_suggestion":
        tool_name = str(draft.target.get("tool_name", "")).strip()
        if tool_name:
            return slugify(tool_name)
    if draft.type == "prompt_patch":
        anchor = str(draft.target.get("anchor", "")).strip()
        if anchor:
            return slugify(anchor.lstrip("#").strip())
    return slugify(draft.summary)


def _strip_yaml_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text


def _parse_yaml_like_block(block: str) -> dict[str, Any]:
    """Minimal YAML-like parser for proposal frontmatter (nested target/source)."""
    result: dict[str, Any] = {}
    current_key: str | None = None
    current_nested: dict[str, str] | None = None

    for line in block.splitlines():
        if not line.strip():
            continue
        if line.startswith("  ") and current_nested is not None and ":" in line:
            nested_key, nested_value = line.strip().split(":", 1)
            current_nested[nested_key.strip()] = _strip_yaml_quotes(nested_value.strip())
            continue
        if ":" not in line:
            continue
        if current_key and current_nested is not None:
            result[current_key] = current_nested
            current_nested = None
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if not value:
            current_key = key
            current_nested = {}
            continue
        current_key = None
        current_nested = None
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            result[key] = [item.strip() for item in inner.split(",") if item.strip()] if inner else []
        else:
            result[key] = _strip_yaml_quotes(value)
    if current_key and current_nested is not None:
        result[current_key] = current_nested
    return result


def _parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, flags=re.DOTALL)
    if not match:
        return {}
    return _parse_yaml_like_block(match.group(1))


def scan_pending_proposals(evolve_dir: Path) -> list[PendingProposalIndex]:
    root = evolve_dir / PROPOSALS_DIRNAME
    if not root.is_dir():
        return []
    items: list[PendingProposalIndex] = []
    for path in sorted(root.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta = _parse_frontmatter(text)
        status = str(meta.get("status", "pending")).strip().lower()
        if status != "pending":
            continue
        proposal_id = str(meta.get("id", path.stem)).strip()
        summary = _extract_summary_section(text)
        target = meta.get("target", {})
        target_path = ""
        if isinstance(target, dict):
            target_path = str(target.get("path", "")).strip()
        items.append(
            PendingProposalIndex(
                proposal_id=proposal_id,
                summary=summary,
                target_path=target_path,
                status=status,
            )
        )
    return items


def _extract_summary_section(text: str) -> str:
    body = _extract_markdown_section(text, "Summary", until="Proposed")
    if not body:
        return ""
    return body.strip().splitlines()[0].strip()


def _extract_markdown_section(text: str, heading: str, *, until: str | None = None) -> str:
    if until:
        pattern = (
            rf"^## {re.escape(heading)}\s*\n+"
            rf"(.+?)"
            rf"(?=\n## {re.escape(until)}\s*\n|\Z)"
        )
    elif heading == "Proposed":
        pattern = r"^## Proposed\s*\n+(.+?)(?=\n## Evidence\s*\n|\Z)"
    elif heading == "Summary":
        pattern = r"^## Summary\s*\n+(.+?)(?=\n## Proposed\s*\n|\Z)"
    else:
        pattern = rf"^## {re.escape(heading)}\s*\n+(.+?)(?:\n## |\Z)"
    match = re.search(pattern, text, flags=re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    return match.group(1).strip()


def list_prompt_section_headers(evolve_dir: Path, topic_index: list[TopicIndexEntry]) -> dict[str, list[str]]:
    headers: dict[str, list[str]] = {}
    for entry in topic_index:
        path = evolve_dir / Path(entry.prompt)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        found = re.findall(r"^## (.+)$", text, flags=re.MULTILINE)
        headers[entry.id] = [line.strip() for line in found if line.strip()]
    return headers


def build_evolve_index_block(paths: AgentPaths) -> str:
    """EVOLVE §3.4: memories, prompt headers, pending proposals."""
    evolve_dir = paths.evolve
    lines = ["[已有 evolve 索引]"]

    memories = scan_memory_index(evolve_dir)
    lines.append("memory (active):")
    if memories:
        for entry in memories:
            lines.append(f"- {entry.memory_id}: {entry.summary}")
    else:
        lines.append("- (none)")

    topic_index = load_topic_index(evolve_dir)
    headers = list_prompt_section_headers(evolve_dir, topic_index)
    lines.append("prompt (## 标题):")
    if headers:
        for topic_id, titles in headers.items():
            label = ", ".join(titles) if titles else "(none)"
            lines.append(f"- {topic_id}: {label}")
    else:
        lines.append("- (none)")

    pending = scan_pending_proposals(evolve_dir)
    lines.append("pending proposals:")
    if pending:
        for item in pending:
            lines.append(f"- {item.proposal_id}: {item.summary} → {item.target_path or '?'}")
    else:
        lines.append("- (none)")

    return "\n".join(lines)


def format_recent_dialogue(session: Session, *, max_messages: int = RECENT_MESSAGE_TURNS) -> str:
    """Recent messages for checkpoint context (skip anchor-only prefix when possible)."""
    messages = session.messages
    if not messages:
        return "(no messages yet)"
    slice_start = max(0, len(messages) - max_messages)
    lines: list[str] = []
    for index, message in enumerate(messages[slice_start:], start=slice_start + 1):
        role = str(message.get("role", "?"))
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            tool_calls = message.get("tool_calls")
            if tool_calls:
                content = "[tool_calls]"
            else:
                continue
        preview = content.strip()
        if len(preview) > 600:
            preview = preview[:599] + "…"
        lines.append(f"- messages.jsonl#{index} role={role}: {preview}")
    return "\n".join(lines) if lines else "(no text messages)"


def build_checkpoint_messages(
    session: Session,
    *,
    trigger_phrase: str,
    user_line: str,
    paths: AgentPaths | None = None,
) -> list[dict[str, str]]:
    agent_paths = paths or session.paths
    valid_topics = sorted(registered_topic_ids(agent_paths))
    session_topics = ", ".join(session.meta.topics) if session.meta.topics else "(none)"
    index_block = build_evolve_index_block(agent_paths)
    dialogue = format_recent_dialogue(session)
    goal = session.goal.strip() or "(unset)"

    system = "\n\n".join(
        [
            "你是 my-agent 进化写入助手。只输出一个 JSON 对象，用于生成 evolve proposal 文件。",
            index_block,
            f"本次会议 goal: {goal}",
            f"本次会议 topics: {session_topics}",
            f"conversation_id: {session.conversation_id}",
        ]
    )
    user = CHECKPOINT_USER_TEMPLATE.format(
        trigger_phrase=trigger_phrase,
        user_line=user_line.strip() or trigger_phrase,
        max_proposals=MAX_PROPOSALS_PER_CHECKPOINT,
        valid_topics=", ".join(valid_topics),
        session_topics=session_topics,
        max_evidence=MAX_EVIDENCE_PER_PROPOSAL,
    )
    user += f"\n\n[最近对话]\n{dialogue}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise EvolveError("empty LLM response")

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL)
    if fence:
        stripped = fence.group(1)

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise EvolveError("response does not contain a JSON object")

    try:
        payload = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as exc:
        raise EvolveError(f"invalid JSON in proposal batch: {exc}") from exc

    if not isinstance(payload, dict):
        raise EvolveError("proposal batch JSON must be an object")
    return payload


def _normalize_evidence(raw: Any, *, fallback_ref: str) -> EvidenceItem | None:
    if not isinstance(raw, dict):
        return None
    role = str(raw.get("role", "user")).strip() or "user"
    quote = raw.get("quote")
    if not isinstance(quote, str) or not quote.strip():
        return None
    ref = raw.get("ref")
    ref_text = ref.strip() if isinstance(ref, str) and ref.strip() else fallback_ref
    return EvidenceItem(role=role, quote=quote.strip(), ref=ref_text)


def _default_topic(session: Session, valid_ids: frozenset[str]) -> str:
    for topic in session.meta.topics:
        if topic in valid_ids:
            return topic
    if "workflow" in valid_ids:
        return "workflow"
    if valid_ids:
        return sorted(valid_ids)[0]
    raise EvolveError("no registered topics in _index.toml")


def _build_target(
    *,
    proposal_type: str,
    mode: str,
    topics: tuple[str, ...],
    memory_id: str,
    anchor: str,
    tool_name: str,
) -> dict[str, Any]:
    topic = topics[0]
    if proposal_type == "memory":
        mid = memory_id.strip()
        if not mid:
            raise EvolveError("memory proposal requires memory_id")
        rel = f"memories/{topic}/{mid}.md"
        return {"topic": topic, "memory_id": mid, "path": rel}
    if proposal_type == "prompt_patch":
        if not anchor.strip():
            raise EvolveError("prompt_patch requires anchor (## title)")
        return {
            "topic": topic,
            "path": f"prompts/{topic}.md",
            "mode": "append_section",
            "anchor": anchor.strip(),
        }
    if proposal_type == "tool_suggestion":
        tname = tool_name.strip()
        if not tname:
            raise EvolveError("tool_suggestion requires tool_name")
        return {
            "topic": topic,
            "tool_name": tname,
            "path": f"tools/{topic}/{tname}/",
        }
    raise EvolveError(f"unknown proposal type: {proposal_type}")


def parse_proposal_batch(
    content: str,
    *,
    session: Session,
    valid_topic_ids: frozenset[str],
    checkpoint_at: str,
    triggered_by: TriggeredBy,
    trigger_phrase: str,
    user_line: str = "",
) -> tuple[tuple[ProposalDraft, ...], str]:
    payload = _extract_json_object(content)
    user_message = payload.get("user_message", "")
    if not isinstance(user_message, str):
        user_message = str(user_message)
    user_message = user_message.strip()

    raw_list = payload.get("proposals", [])
    if raw_list is None:
        raw_list = []
    if not isinstance(raw_list, list):
        raise EvolveError("proposals must be an array")

    default_topic = _default_topic(session, valid_topic_ids)
    date_prefix = datetime.now(UTC).strftime("%Y%m%d")
    seq_start = _next_seq(session.paths.evolve, date_prefix)
    drafts: list[ProposalDraft] = []

    for offset, raw in enumerate(raw_list[:MAX_PROPOSALS_PER_CHECKPOINT]):
        if not isinstance(raw, dict):
            continue
        proposal_type = str(raw.get("type", "")).strip()
        if proposal_type not in VALID_PROPOSAL_TYPES:
            continue
        mode = str(raw.get("mode", "create")).strip()
        if mode not in VALID_MODES:
            mode = "create"

        topics_raw = raw.get("topics", [default_topic])
        topics_list: list[str] = []
        if isinstance(topics_raw, list):
            for item in topics_raw:
                topic = str(item).strip()
                if topic in valid_topic_ids and topic not in topics_list:
                    topics_list.append(topic)
        if not topics_list:
            topics_list = [default_topic]

        summary = raw.get("summary", "")
        if not isinstance(summary, str) or not summary.strip():
            continue
        summary = summary.strip()

        proposed = raw.get("proposed_markdown", "")
        if not isinstance(proposed, str) or not proposed.strip():
            continue
        proposed = proposed.strip()

        memory_id = str(raw.get("memory_id", "")).strip()
        anchor = str(raw.get("anchor", "")).strip()
        tool_name = str(raw.get("tool_name", "")).strip()

        if proposal_type == "memory" and mode == "create" and not memory_id:
            memory_id = f"{topics_list[0]}-{slugify(summary)}"
        if proposal_type == "tool_suggestion" and not tool_name:
            tool_name = slugify(summary)

        try:
            target = _build_target(
                proposal_type=proposal_type,
                mode=mode,
                topics=tuple(topics_list),
                memory_id=memory_id,
                anchor=anchor,
                tool_name=tool_name,
            )
        except EvolveError:
            continue

        evidence_items = list(
            resolve_evidence_for_proposal(
                session,
                raw.get("evidence", []),
                user_line=user_line,
                trigger_phrase=trigger_phrase,
            )
        )

        seq = seq_start + offset
        proposal_id = f"prop-{date_prefix}-{seq:03d}"
        slug = slugify(memory_id or tool_name or anchor or summary)
        drafts.append(
            ProposalDraft(
                proposal_id=proposal_id,
                seq=seq,
                date_prefix=date_prefix,
                type=proposal_type,
                mode=mode,
                topics=tuple(topics_list),
                summary=summary,
                proposed_markdown=proposed,
                target=target,
                evidence=tuple(evidence_items),
                fingerprint=content_fingerprint(summary),
                evidence_fingerprints=tuple(
                    evidence_fingerprint(item.quote) for item in evidence_items
                ),
                conversation_id=session.conversation_id,
                checkpoint_at=checkpoint_at,
                triggered_by=triggered_by,
                trigger_phrase=trigger_phrase,
            )
        )

    return tuple(drafts), user_message


def _next_seq(evolve_dir: Path, date_prefix: str) -> int:
    root = evolve_dir / PROPOSALS_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    max_seq = 0
    pattern = re.compile(rf"^{re.escape(date_prefix)}-(\d{{3}})-")
    for path in root.glob(f"{date_prefix}-*.md"):
        match = pattern.match(path.name)
        if match:
            max_seq = max(max_seq, int(match.group(1)))
    return max_seq + 1


def _yaml_quote(value: str) -> str:
    if not value:
        return '""'
    if any(ch in value for ch in ':"\'\n#'):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _yaml_list(items: list[str]) -> str:
    inner = ", ".join(items)
    return f"[{inner}]"


def render_proposal_file(draft: ProposalDraft) -> str:
    """EVOLVE §4.2–4.3: frontmatter + Summary / Proposed / Evidence."""
    _ = proposal_slug(draft)
    topics_yaml = _yaml_list(list(draft.topics))
    target_lines = ["target:"]
    for key, value in draft.target.items():
        if isinstance(value, str):
            target_lines.append(f"  {key}: {_yaml_quote(value)}")

    source_lines = [
        "source:",
        f"  conversation_id: {_yaml_quote(draft.conversation_id)}",
        f"  checkpoint_at: {_yaml_quote(draft.checkpoint_at)}",
        f"  triggered_by: {_yaml_quote(draft.triggered_by)}",
        f"  trigger_phrase: {_yaml_quote(draft.trigger_phrase)}",
    ]

    fp_list = ", ".join(_yaml_quote(fp) for fp in draft.evidence_fingerprints)
    frontmatter = "\n".join(
        [
            "---",
            f"id: {_yaml_quote(draft.proposal_id)}",
            "status: pending",
            f"type: {_yaml_quote(draft.type)}",
            f"mode: {_yaml_quote(draft.mode)}",
            f"topics: {topics_yaml}",
            *target_lines,
            *source_lines,
            f"fingerprint: {_yaml_quote(draft.fingerprint)}",
            f"evidence_fingerprints: [{fp_list}]",
            f"created_at: {_yaml_quote(draft.checkpoint_at)}",
            "---",
        ]
    )

    evidence_body = "\n".join(
        f"- role: {item.role}\n  quote: {_yaml_quote(item.quote)}\n  ref: {_yaml_quote(item.ref)}"
        for item in draft.evidence
    )

    return (
        f"{frontmatter}\n\n"
        f"## Summary\n{draft.summary}\n\n"
        f"## Proposed\n{draft.proposed_markdown}\n\n"
        f"## Evidence\n{evidence_body}\n"
    )


def proposal_file_path(draft: ProposalDraft) -> Path:
    slug = proposal_slug(draft)
    name = f"{draft.date_prefix}-{draft.seq:03d}-{draft.type}-{slug}.md"
    return Path(PROPOSALS_DIRNAME) / name


def write_proposal(draft: ProposalDraft, *, evolve_dir: Path) -> Path:
    out_dir = evolve_dir / PROPOSALS_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)
    rel = proposal_file_path(draft)
    path = evolve_dir / rel
    path.write_text(render_proposal_file(draft), encoding="utf-8")
    return path


def _parse_meta_string_list(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, list):
        return tuple(str(item).strip().strip('"').strip("'") for item in raw if str(item).strip())
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("[") and text.endswith("]"):
            inner = text[1:-1].strip()
            if not inner:
                return ()
            return tuple(
                part.strip().strip('"').strip("'")
                for part in inner.split(",")
                if part.strip()
            )
    return ()


def _memory_file_exists(evolve_dir: Path, topic: str, memory_id: str) -> bool:
    if not topic or not memory_id:
        return False
    return (evolve_dir / "memories" / topic / f"{memory_id}.md").is_file()


def _prompt_has_anchor(evolve_dir: Path, topic: str, anchor: str) -> bool:
    anchor_title = anchor.lstrip("#").strip()
    if not topic or not anchor_title:
        return False
    path = evolve_dir / "prompts" / f"{topic}.md"
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return (
        re.search(rf"^##\s+{re.escape(anchor_title)}\s*$", text, flags=re.MULTILINE)
        is not None
    )


def _tool_name_registered(paths: AgentPaths, tool_name: str) -> bool:
    name = tool_name.strip()
    if not name:
        return False
    try:
        registry = ToolRegistry.load(paths)
    except Exception:
        return False
    return registry.get_evolved(name) is not None


def collect_accepted_evidence_fingerprints(evolve_dir: Path) -> frozenset[str]:
    """Global accepted evidence fingerprints (EVOLVE §6.2.3)."""
    fingerprints: set[str] = set()
    for path in iter_proposal_files(evolve_dir, include_archive=True):
        try:
            record = load_proposal_file(path)
        except OSError:
            continue
        if record.status != "accepted":
            continue
        fingerprints.update(record.evidence_fingerprints)
    return frozenset(fingerprints)


def list_pending_proposal_records(evolve_dir: Path) -> list[ProposalRecord]:
    records: list[ProposalRecord] = []
    for path in iter_proposal_files(evolve_dir):
        try:
            record = load_proposal_file(path)
        except OSError:
            continue
        if record.status == "pending":
            records.append(record)
    return records


def find_pending_by_target_path(
    evolve_dir: Path,
    target_path: str,
    *,
    exclude_paths: frozenset[Path] = frozenset(),
) -> ProposalRecord | None:
    rel = target_path.strip()
    if not rel:
        return None
    for record in list_pending_proposal_records(evolve_dir):
        if record.path in exclude_paths:
            continue
        pending_target = str(record.target.get("path", "")).strip()
        if pending_target == rel:
            return record
    return None


def find_pending_by_evidence_fingerprint(
    evolve_dir: Path,
    fingerprint: str,
    *,
    exclude_paths: frozenset[Path] = frozenset(),
) -> ProposalRecord | None:
    needle = fingerprint.strip()
    if not needle:
        return None
    for record in list_pending_proposal_records(evolve_dir):
        if record.path in exclude_paths:
            continue
        if needle in record.evidence_fingerprints:
            return record
    return None


def _identity_block_reason(draft: ProposalDraft, *, evolve_dir: Path, paths: AgentPaths) -> str:
    """Hard block on stable identity keys (EVOLVE §6.2.1)."""
    topic = str(draft.target.get("topic", "")).strip() or (draft.topics[0] if draft.topics else "")
    if draft.type == "memory" and draft.mode == "create":
        memory_id = str(draft.target.get("memory_id", "")).strip()
        if memory_id and _memory_file_exists(evolve_dir, topic, memory_id):
            return f"memory id already exists: {memory_id}"
    if draft.type == "prompt_patch":
        anchor = str(draft.target.get("anchor", "")).strip()
        if anchor and _prompt_has_anchor(evolve_dir, topic, anchor):
            return f"prompt anchor already exists: {anchor.lstrip('#').strip()}"
    if draft.type == "tool_suggestion":
        tool_name = str(draft.target.get("tool_name", "")).strip()
        if tool_name and _tool_name_registered(paths, tool_name):
            return f"tool name already registered: {tool_name}"
    return ""


def evaluate_dedup_gate(
    draft: ProposalDraft,
    *,
    paths: AgentPaths,
    accepted_evidence_fingerprints: frozenset[str],
    exclude_pending_paths: frozenset[Path] = frozenset(),
) -> DedupGateResult:
    """Pre-write dedup: identity hard block, evidence_fp block, pending supersede (§6)."""
    evolve_dir = paths.evolve
    identity_reason = _identity_block_reason(draft, evolve_dir=evolve_dir, paths=paths)
    if identity_reason:
        return DedupGateResult(allow=False, dedup="blocked", reason=identity_reason)

    for fp in draft.evidence_fingerprints:
        if fp in accepted_evidence_fingerprints:
            return DedupGateResult(
                allow=False,
                dedup="blocked",
                reason=f"evidence already accepted (fingerprint {fp})",
            )

    supersede_map: dict[Path, ProposalRecord] = {}
    target_path = str(draft.target.get("path", "")).strip()
    if target_path:
        pending_target = find_pending_by_target_path(
            evolve_dir,
            target_path,
            exclude_paths=exclude_pending_paths,
        )
        if pending_target is not None:
            supersede_map[pending_target.path] = pending_target

    for fp in draft.evidence_fingerprints:
        pending_fp = find_pending_by_evidence_fingerprint(
            evolve_dir,
            fp,
            exclude_paths=exclude_pending_paths,
        )
        if pending_fp is not None:
            supersede_map[pending_fp.path] = pending_fp

    if supersede_map:
        return DedupGateResult(
            allow=True,
            dedup="superseded",
            supersede=tuple(supersede_map.values()),
        )
    return DedupGateResult(allow=True, dedup="ok")


def _update_frontmatter_fields(text: str, **fields: str) -> str:
    match = re.match(r"^(---\s*\n)(.*?)(\n---\s*\n)", text, flags=re.DOTALL)
    if not match:
        return text
    block = match.group(2)
    for key, value in fields.items():
        line = f"{key}: {_yaml_quote(value)}"
        if re.search(rf"^{re.escape(key)}:\s*", block, flags=re.MULTILINE):
            block = re.sub(
                rf"^{re.escape(key)}:\s*.+$",
                line,
                block,
                count=1,
                flags=re.MULTILINE,
            )
        else:
            block = block.rstrip() + "\n" + line
    return f"{match.group(1)}{block}{match.group(3)}{text[match.end() :]}"


def supersede_proposal_record(record: ProposalRecord, *, superseded_by: str) -> None:
    """Mark pending proposal superseded (EVOLVE §6.3)."""
    updated = _update_frontmatter_fields(
        _set_frontmatter_status(record.raw_text, "superseded"),
        superseded_by=superseded_by,
    )
    record.path.write_text(updated, encoding="utf-8")


def _assert_accept_identity_allowed(
    record: ProposalRecord,
    *,
    paths: AgentPaths,
) -> None:
    """Hard identity check at accept time (EVOLVE §6.3)."""
    evolve_dir = paths.evolve
    topic = str(record.target.get("topic", "")).strip() or (record.topics[0] if record.topics else "")
    if record.type == "memory" and record.mode == "create":
        memory_id = str(record.target.get("memory_id", "")).strip()
        if memory_id and _memory_file_exists(evolve_dir, topic, memory_id):
            raise EvolveError(f"memory id already exists: {memory_id}")
    if record.type == "prompt_patch":
        anchor = str(record.target.get("anchor", "")).strip()
        if anchor and _prompt_has_anchor(evolve_dir, topic, anchor):
            raise EvolveError(f"prompt anchor already exists: {anchor.lstrip('#').strip()}")
    if record.type == "tool_suggestion":
        tool_name = str(record.target.get("tool_name", "")).strip()
        if tool_name and _tool_name_registered(paths, tool_name):
            raise EvolveError(f"tool name already registered: {tool_name}")


def run_explicit_checkpoint(
    session: Session,
    *,
    trigger_phrase: str,
    user_line: str = "",
    client: ChatClient | None = None,
    paths: AgentPaths | None = None,
    triggered_by: TriggeredBy = "explicit",
    evolve_log: EvolveLog | None = None,
) -> CheckpointResult:
    """Open checkpoint and generate ≤2 proposal files (EVOLVE §2–4)."""
    agent_paths = paths or session.paths
    valid_ids = registered_topic_ids(agent_paths)
    checkpoint_at = utc_now_iso()
    log = evolve_log or EvolveLog.for_agent(agent_paths)

    log.log_checkpoint_opened(
        conversation_id=session.conversation_id,
        triggered_by=triggered_by,
        trigger_phrase=trigger_phrase,
    )

    llm = client or LLMClient()
    messages = build_checkpoint_messages(
        session,
        trigger_phrase=trigger_phrase,
        user_line=user_line,
        paths=agent_paths,
    )
    model = resolve_session_model(session.meta.topics)
    response = llm.chat(messages, model=model, temperature=0)

    drafts, user_message = parse_proposal_batch(
        response.content or "",
        session=session,
        valid_topic_ids=valid_ids,
        checkpoint_at=checkpoint_at,
        triggered_by=triggered_by,
        trigger_phrase=trigger_phrase,
        user_line=user_line,
    )
    drafts = dedupe_drafts_by_fingerprint(drafts)

    written: list[Path] = []
    proposal_ids: list[str] = []
    blocked_notes: list[str] = []
    evolve_dir = agent_paths.evolve
    accepted_efps = collect_accepted_evidence_fingerprints(evolve_dir)
    excluded_pending: set[Path] = set()

    for draft in drafts:
        gate = evaluate_dedup_gate(
            draft,
            paths=agent_paths,
            accepted_evidence_fingerprints=accepted_efps,
            exclude_pending_paths=frozenset(excluded_pending),
        )
        if not gate.allow:
            blocked_notes.append(gate.reason)
            log.log_proposal_created(
                conversation_id=session.conversation_id,
                proposal_id=draft.proposal_id,
                proposal_type=draft.type,
                fingerprint=draft.fingerprint,
                dedup="blocked",
            )
            continue

        for old_record in gate.supersede:
            supersede_proposal_record(old_record, superseded_by=draft.proposal_id)
            excluded_pending.add(old_record.path)
            log.log_proposal_superseded(
                conversation_id=session.conversation_id,
                old_id=old_record.proposal_id,
                new_id=draft.proposal_id,
            )

        path = write_proposal(draft, evolve_dir=evolve_dir)
        written.append(path)
        proposal_ids.append(draft.proposal_id)
        log.log_proposal_created(
            conversation_id=session.conversation_id,
            proposal_id=draft.proposal_id,
            proposal_type=draft.type,
            fingerprint=draft.fingerprint,
            dedup=gate.dedup,
            path=path.relative_to(agent_paths.evolve).as_posix(),
        )

    if not user_message:
        if written:
            user_message = (
                f"已生成 {len(written)} 条 pending proposal，待审阅接受（尚未写入 evolve）。"
            )
        else:
            user_message = "未生成 proposal（可能已被已有 evolve 条目覆盖，或 LLM 返回为空）。"
    elif blocked_notes and not written:
        user_message = f"未生成 proposal：{'；'.join(blocked_notes)}"
    elif blocked_notes and written:
        user_message = f"{user_message}\n（部分提议被防重复拦截：{'；'.join(blocked_notes)}）"
    elif written and "pending" not in user_message.casefold() and "审阅" not in user_message:
        user_message = f"{user_message}\n（proposal 为 pending，接受后才会写入 evolve。）"

    return CheckpointResult(
        written_paths=tuple(written),
        proposal_ids=tuple(proposal_ids),
        user_message=user_message,
    )


def proposals_archive_dir(evolve_dir: Path) -> Path:
    return evolve_dir / PROPOSALS_DIRNAME / PROPOSALS_ARCHIVE_DIRNAME


def _set_frontmatter_status(text: str, status: str) -> str:
    if not text.startswith("---"):
        return text
    match = re.match(r"^(---\s*\n)(.*?)(\n---\s*\n)", text, flags=re.DOTALL)
    if not match:
        return text
    block = match.group(2)
    if re.search(r"^status:\s*", block, flags=re.MULTILINE):
        block = re.sub(
            r"^status:\s*.+$",
            f"status: {status}",
            block,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        block = f"status: {status}\n{block}"
    return f"{match.group(1)}{block}{match.group(3)}{text[match.end() :]}"


def load_proposal_file(path: Path) -> ProposalRecord:
    text = path.read_text(encoding="utf-8")
    meta = _parse_frontmatter(text)
    proposal_id = str(meta.get("id", path.stem)).strip()
    status = str(meta.get("status", "pending")).strip().lower()
    proposal_type = str(meta.get("type", "")).strip()
    mode = str(meta.get("mode", "create")).strip()
    topics_raw = meta.get("topics", [])
    topics: tuple[str, ...]
    if isinstance(topics_raw, list):
        topics = tuple(str(item).strip() for item in topics_raw if str(item).strip())
    else:
        topics = ()
    target = meta.get("target", {})
    if not isinstance(target, dict):
        target = {}
    source = meta.get("source", {})
    conversation_id = ""
    if isinstance(source, dict):
        conversation_id = str(source.get("conversation_id", "")).strip()
    fingerprint = str(meta.get("fingerprint", "")).strip()
    evidence_fps = _parse_meta_string_list(meta.get("evidence_fingerprints", []))
    return ProposalRecord(
        path=path,
        proposal_id=proposal_id,
        status=status,
        type=proposal_type,
        mode=mode,
        topics=topics,
        target=target,
        summary=_extract_summary_section(text),
        proposed_markdown=_extract_markdown_section(text, "Proposed"),
        conversation_id=conversation_id,
        raw_text=text,
        fingerprint=fingerprint,
        evidence_fingerprints=evidence_fps,
    )


def iter_proposal_files(evolve_dir: Path, *, include_archive: bool = False) -> list[Path]:
    root = evolve_dir / PROPOSALS_DIRNAME
    if not root.is_dir():
        return []
    paths = sorted(root.glob("*.md"))
    if include_archive:
        archive = proposals_archive_dir(evolve_dir)
        if archive.is_dir():
            paths.extend(sorted(archive.glob("*.md")))
    return paths


def find_proposal_path(evolve_dir: Path, proposal_id: str) -> Path | None:
    needle = proposal_id.strip()
    if not needle:
        return None
    candidates: list[ProposalRecord] = []
    for path in iter_proposal_files(evolve_dir, include_archive=True):
        try:
            record = load_proposal_file(path)
        except OSError:
            continue
        candidates.append(record)
    exact = [record for record in candidates if record.proposal_id == needle]
    if len(exact) == 1:
        return exact[0].path
    if len(exact) > 1:
        return _pick_preferred_proposal(exact).path
    suffix = [record for record in candidates if record.proposal_id.endswith(needle)]
    if suffix:
        return _pick_preferred_proposal(suffix).path
    stem = [record for record in candidates if record.path.stem.endswith(needle)]
    if stem:
        return _pick_preferred_proposal(stem).path
    return None


def _pick_preferred_proposal(records: list[ProposalRecord]) -> ProposalRecord:
    """Disambiguate duplicate proposal ids: pending in proposals/ first, then newest."""
    if len(records) == 1:
        return records[0]
    pending_active = [
        record
        for record in records
        if record.status == "pending"
        and PROPOSALS_ARCHIVE_DIRNAME not in record.path.parts
    ]
    pool = pending_active or records
    if len(pool) == 1:
        return pool[0]
    return max(pool, key=lambda record: record.path.stat().st_mtime)


def list_pending_proposals(paths: AgentPaths) -> list[ProposalRecord]:
    items: list[ProposalRecord] = []
    for path in iter_proposal_files(paths.evolve):
        try:
            record = load_proposal_file(path)
        except OSError:
            continue
        if record.status == "pending":
            items.append(record)
    return items


def format_pending_proposals_list(paths: AgentPaths) -> str:
    pending = list_pending_proposals(paths)
    if not pending:
        return "pending proposals: (none)"
    lines = ["pending proposals:"]
    for record in pending:
        target_path = str(record.target.get("path", "")).strip() or "?"
        lines.append(f"- {record.proposal_id} [{record.type}/{record.mode}] {record.summary}")
        lines.append(f"  → {target_path}")
    return "\n".join(lines)


def _memory_target_path(evolve_dir: Path, record: ProposalRecord) -> Path:
    rel = str(record.target.get("path", "")).strip()
    if not rel:
        topic = str(record.target.get("topic", "")).strip() or (record.topics[0] if record.topics else "")
        memory_id = str(record.target.get("memory_id", "")).strip()
        if not topic or not memory_id:
            raise EvolveError("memory proposal missing target.path or memory_id")
        rel = f"memories/{topic}/{memory_id}.md"
    return evolve_dir / rel


def _prompt_target_path(evolve_dir: Path, record: ProposalRecord) -> Path:
    rel = str(record.target.get("path", "")).strip()
    if not rel:
        topic = str(record.target.get("topic", "")).strip() or (record.topics[0] if record.topics else "")
        if not topic:
            raise EvolveError("prompt_patch proposal missing topic")
        rel = f"prompts/{topic}.md"
    return evolve_dir / rel


def _route_memory_create(evolve_dir: Path, record: ProposalRecord) -> Path:
    target = _memory_target_path(evolve_dir, record)
    if target.is_file():
        raise EvolveError(f"memory already exists: {target.relative_to(evolve_dir).as_posix()}")
    proposed = record.proposed_markdown.strip()
    if not proposed:
        raise EvolveError("memory proposal has empty ## Proposed section")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(proposed + "\n", encoding="utf-8")
    return target


def _route_memory_update(evolve_dir: Path, record: ProposalRecord) -> Path:
    target = _memory_target_path(evolve_dir, record)
    if not target.is_file():
        raise EvolveError(
            f"memory update target not found: {target.relative_to(evolve_dir).as_posix()}"
        )
    proposed = record.proposed_markdown.strip()
    if not proposed:
        raise EvolveError("memory update proposal has empty ## Proposed section")
    revision_date = datetime.now(UTC).strftime("%Y-%m-%d")
    heading = f"## 修订 {revision_date}"
    existing = target.read_text(encoding="utf-8")
    if heading in existing:
        raise EvolveError(f"revision section already exists for {revision_date}")
    body = proposed
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + 4 :].lstrip("\n")
    append = f"\n\n{heading}\n{body.strip()}\n"
    target.write_text(existing.rstrip() + append, encoding="utf-8")
    return target


def _route_prompt_patch(evolve_dir: Path, record: ProposalRecord) -> Path:
    target = _prompt_target_path(evolve_dir, record)
    if not target.is_file():
        raise EvolveError(
            f"prompt target not found: {target.relative_to(evolve_dir).as_posix()}"
        )
    proposed = record.proposed_markdown.strip()
    if not proposed:
        raise EvolveError("prompt_patch proposal has empty ## Proposed section")
    anchor = str(record.target.get("anchor", "")).strip()
    if not anchor:
        first_line = proposed.splitlines()[0].strip() if proposed else ""
        if first_line.startswith("##"):
            anchor = first_line
    if anchor:
        anchor_title = anchor.lstrip("#").strip()
        existing = target.read_text(encoding="utf-8")
        if re.search(rf"^##\s+{re.escape(anchor_title)}\s*$", existing, flags=re.MULTILINE):
            raise EvolveError(f"prompt anchor already exists: {anchor_title}")
    existing = target.read_text(encoding="utf-8")
    separator = "\n" if existing.endswith("\n") else "\n\n"
    target.write_text(existing.rstrip() + separator + proposed + "\n", encoding="utf-8")
    return target


def _mark_proposal_status(
    record: ProposalRecord,
    status: str,
    *,
    evolve_dir: Path,
    archive: bool = False,
) -> Path:
    updated = _set_frontmatter_status(record.raw_text, status)
    if archive and status == "rejected":
        archive_dir = proposals_archive_dir(evolve_dir)
        archive_dir.mkdir(parents=True, exist_ok=True)
        dest = archive_dir / record.path.name
        dest.write_text(updated, encoding="utf-8")
        if record.path.is_file():
            record.path.unlink()
        return dest
    record.path.write_text(updated, encoding="utf-8")
    return record.path


def accept_proposal_at_path(
    proposal_path: Path,
    *,
    paths: AgentPaths,
    evolve_log: EvolveLog | None = None,
    conversation_id: str | None = None,
) -> ReviewResult:
    """Accept a specific proposal file (used for same-turn review after checkpoint)."""
    _assert_accept_identity_allowed(record := load_proposal_file(proposal_path), paths=paths)
    if record.status != "pending":
        raise EvolveError(f"proposal {record.proposal_id} is not pending (status={record.status})")

    evolve_dir = paths.evolve
    log = evolve_log or EvolveLog.for_agent(paths)
    conv_id = conversation_id or record.conversation_id or None
    routed: Path | None = None
    action = record.mode

    if record.type == "memory":
        if record.mode == "create":
            routed = _route_memory_create(evolve_dir, record)
            action = "create"
        elif record.mode == "update":
            routed = _route_memory_update(evolve_dir, record)
            action = "update"
        else:
            raise EvolveError(f"unsupported memory mode: {record.mode}")
    elif record.type == "prompt_patch":
        routed = _route_prompt_patch(evolve_dir, record)
        action = "append_section"
    elif record.type == "tool_suggestion":
        action = "create"
        tool_name = str(record.target.get("tool_name", "")).strip()
        if not tool_name:
            raise EvolveError("tool_suggestion missing tool_name")
        log.log_tool_spec_accepted(
            tool_name=tool_name,
            proposal_id=record.proposal_id,
            note="pending_implementation",
            conversation_id=conv_id,
        )
    else:
        raise EvolveError(f"unsupported proposal type: {record.type}")

    rel_path = (
        routed.relative_to(evolve_dir).as_posix()
        if routed is not None
        else str(record.target.get("path", "")).strip()
    )
    _mark_proposal_status(record, "accepted", evolve_dir=evolve_dir)
    log.log_evolve_accepted(
        proposal_id=record.proposal_id,
        proposal_type=record.type,
        path=rel_path,
        action=action,
        conversation_id=conv_id,
    )
    if record.type == "tool_suggestion":
        message = (
            f"已接受 tool spec「{record.target.get('tool_name', '?')}」；"
            f"待手写实现到 evolve/{rel_path}（未自动生成代码）。"
        )
    else:
        message = f"已接受 {record.proposal_id} → evolve/{rel_path}"
    from governance.git_hints import format_accept_commit_hint

    message = f"{message}\n{format_accept_commit_hint(record.proposal_id)}"
    return ReviewResult(
        proposal_id=record.proposal_id,
        status="accepted",
        message=message,
        routed_path=rel_path,
    )


def accept_proposal(
    proposal_id: str,
    *,
    paths: AgentPaths,
    evolve_log: EvolveLog | None = None,
    conversation_id: str | None = None,
) -> ReviewResult:
    """Accept pending proposal and route per EVOLVE §7 (T-404)."""
    path = find_proposal_path(paths.evolve, proposal_id)
    if path is None:
        raise EvolveError(f"proposal not found: {proposal_id}")
    return accept_proposal_at_path(
        path,
        paths=paths,
        evolve_log=evolve_log,
        conversation_id=conversation_id,
    )


def reject_proposal_at_path(
    proposal_path: Path,
    *,
    paths: AgentPaths,
    evolve_log: EvolveLog | None = None,
    conversation_id: str | None = None,
    archive: bool = True,
) -> ReviewResult:
    record = load_proposal_file(proposal_path)
    if record.status != "pending":
        raise EvolveError(f"proposal {record.proposal_id} is not pending (status={record.status})")

    log = evolve_log or EvolveLog.for_agent(paths)
    conv_id = conversation_id or record.conversation_id or None
    dest = _mark_proposal_status(
        record,
        "rejected",
        evolve_dir=paths.evolve,
        archive=archive,
    )
    log.log_evolve_rejected(proposal_id=record.proposal_id, conversation_id=conv_id)
    archive_note = f" → proposals/{PROPOSALS_ARCHIVE_DIRNAME}/{dest.name}" if archive else ""
    return ReviewResult(
        proposal_id=record.proposal_id,
        status="rejected",
        message=f"已拒绝 {record.proposal_id}{archive_note}",
    )


def reject_proposal(
    proposal_id: str,
    *,
    paths: AgentPaths,
    evolve_log: EvolveLog | None = None,
    conversation_id: str | None = None,
    archive: bool = True,
) -> ReviewResult:
    """Reject pending proposal (EVOLVE §7)."""
    path = find_proposal_path(paths.evolve, proposal_id)
    if path is None:
        raise EvolveError(f"proposal not found: {proposal_id}")
    return reject_proposal_at_path(
        path,
        paths=paths,
        evolve_log=evolve_log,
        conversation_id=conversation_id,
        archive=archive,
    )


def accept_proposals_at_paths(
    proposal_paths: tuple[Path, ...],
    *,
    paths: AgentPaths,
    evolve_log: EvolveLog | None = None,
    conversation_id: str | None = None,
) -> list[ReviewResult]:
    log = evolve_log or EvolveLog.for_agent(paths)
    results: list[ReviewResult] = []
    for proposal_path in proposal_paths:
        results.append(
            accept_proposal_at_path(
                proposal_path,
                paths=paths,
                evolve_log=log,
                conversation_id=conversation_id,
            )
        )
    return results


def reject_proposals_at_paths(
    proposal_paths: tuple[Path, ...],
    *,
    paths: AgentPaths,
    evolve_log: EvolveLog | None = None,
    conversation_id: str | None = None,
    archive: bool = True,
) -> list[ReviewResult]:
    log = evolve_log or EvolveLog.for_agent(paths)
    results: list[ReviewResult] = []
    for proposal_path in proposal_paths:
        results.append(
            reject_proposal_at_path(
                proposal_path,
                paths=paths,
                evolve_log=log,
                conversation_id=conversation_id,
                archive=archive,
            )
        )
    return results


def accept_proposals_batch(
    proposal_ids: tuple[str, ...],
    *,
    paths: AgentPaths,
    evolve_log: EvolveLog | None = None,
    conversation_id: str | None = None,
) -> list[ReviewResult]:
    results: list[ReviewResult] = []
    for proposal_id in proposal_ids:
        results.append(
            accept_proposal(
                proposal_id,
                paths=paths,
                evolve_log=evolve_log,
                conversation_id=conversation_id,
            )
        )
    return results


def reject_proposals_batch(
    proposal_ids: tuple[str, ...],
    *,
    paths: AgentPaths,
    evolve_log: EvolveLog | None = None,
    conversation_id: str | None = None,
    archive: bool = True,
) -> list[ReviewResult]:
    results: list[ReviewResult] = []
    for proposal_id in proposal_ids:
        results.append(
            reject_proposal(
                proposal_id,
                paths=paths,
                evolve_log=evolve_log,
                conversation_id=conversation_id,
                archive=archive,
            )
        )
    return results


def _demo() -> None:
    paths = AgentPaths.discover()
    demo_proposals = paths.evolve / PROPOSALS_DIRNAME
    if demo_proposals.is_dir():
        for path in demo_proposals.glob("_evolve_demo*.md"):
            path.unlink()

    assert content_fingerprint("Hello World") == content_fingerprint("hello  world!")
    assert evidence_fingerprint("quote a") != evidence_fingerprint("quote b")
    print("[PASS] fingerprint helpers")

    ev_session = Session(
        conversation_id="_evolve_evidence",
        session_dir=paths.data / "sessions" / "_evolve_evidence",
        goal="docs",
        meta=SessionMeta(topics=["coding"], llm_model="pro", updated_at=utc_now_iso()),
        messages=[
            {"role": "user", "content": "[本次会议上下文]\nanchor"},
            {"role": "user", "content": "以后改 docs 都先更新 CHANGELOG"},
            {"role": "assistant", "content": "好的。"},
        ],
        paths=paths,
    )
    corpus = build_dialogue_corpus(ev_session)
    assert any(line.ref == "messages.jsonl#2" for line in corpus)
    assert match_dialogue_quote("以后改 docs 都先更新 CHANGELOG", corpus) is not None
    assert match_dialogue_quote("用户表示希望改 docs", corpus) is None
    assert match_dialogue_quote("用户想先更新 CHANGELOG", corpus) is None
    print("[PASS] T-405: build_dialogue_corpus + verbatim match")

    bad_evidence = resolve_evidence_for_proposal(
        ev_session,
        [
            {"role": "user", "quote": "用户表示希望先更新 CHANGELOG", "ref": "messages.jsonl#2"},
            {"role": "user", "quote": "用户想先更新 CHANGELOG", "ref": "messages.jsonl#2"},
        ],
        user_line="记住 以后改 docs 都先更新 CHANGELOG",
        trigger_phrase="记住",
    )
    assert len(bad_evidence) == 1
    assert bad_evidence[0].quote == "以后改 docs 都先更新 CHANGELOG"
    assert bad_evidence[0].ref == "messages.jsonl#2"
    print("[PASS] T-405: reject self-eval/paraphrase; fallback to verbatim user line")

    good_evidence = resolve_evidence_for_proposal(
        ev_session,
        [
            {"role": "user", "quote": "以后改 docs 都先更新 CHANGELOG", "ref": "messages.jsonl#99"},
            {"role": "user", "quote": "以后改 docs 都先更新 CHANGELOG", "ref": "messages.jsonl#2"},
            {"role": "user", "quote": "好的。", "ref": "messages.jsonl#3"},
        ],
        user_line="记住",
        trigger_phrase="记住",
    )
    assert len(good_evidence) == 2
    assert good_evidence[0].ref == "messages.jsonl#2"
    assert good_evidence[1].ref == "messages.jsonl#3"
    print("[PASS] T-405: <=2 evidence; ref corrected from corpus")

    digest_dir = ev_session.session_dir
    digest_dir.mkdir(parents=True, exist_ok=True)
    (digest_dir / "digest.md").write_text(
        "## 已做\n以前改 docs 都先更新 CHANGELOG\n",
        encoding="utf-8",
    )
    digest_corpus = build_dialogue_corpus(ev_session)
    digest_match = match_dialogue_quote("以前改 docs 都先更新 CHANGELOG", digest_corpus)
    assert digest_match is not None and digest_match.source == "digest"
    digest_evidence = resolve_evidence_for_proposal(
        ev_session,
        [{"role": "user", "quote": "以前改 docs 都先更新 CHANGELOG", "ref": "messages.jsonl#1"}],
        user_line="记住",
        trigger_phrase="记住",
    )
    assert digest_evidence[0].ref.startswith("digest.md#")
    print("[PASS] T-405: digest.md verbatim evidence")

    batch_json = json.dumps(
        {
            "proposals": [
                {
                    "type": "memory",
                    "mode": "create",
                    "topics": ["coding"],
                    "summary": "改 docs 前先更新 CHANGELOG",
                    "memory_id": "coding-evolve-demo-changelog",
                    "proposed_markdown": "## 背景\nx",
                    "evidence": [
                        {
                            "role": "user",
                            "quote": "用户总结：改 docs 须先 CHANGELOG",
                            "ref": "messages.jsonl#2",
                        }
                    ],
                }
            ],
            "user_message": "proposal pending",
        },
        ensure_ascii=False,
    )
    drafts, _ = parse_proposal_batch(
        batch_json,
        session=ev_session,
        valid_topic_ids=registered_topic_ids(paths),
        checkpoint_at=utc_now_iso(),
        triggered_by="explicit",
        trigger_phrase="记住",
        user_line="记住 以后改 docs 都先更新 CHANGELOG",
    )
    assert len(drafts) == 1
    assert drafts[0].evidence[0].quote == "以后改 docs 都先更新 CHANGELOG"
    print("[PASS] T-405: parse_proposal_batch validates evidence")

    slug_draft = ProposalDraft(
        proposal_id="prop-test",
        seq=1,
        date_prefix="20260710",
        type="memory",
        mode="create",
        topics=("workflow",),
        summary="x",
        proposed_markdown="y",
        target={"topic": "workflow", "memory_id": "memory-20260710-001", "path": "p"},
        evidence=(),
        fingerprint="fp",
        evidence_fingerprints=(),
        conversation_id="c",
        checkpoint_at="t",
        triggered_by="explicit",
        trigger_phrase="记住",
    )
    assert proposal_slug(slug_draft) == "20260710-001"
    assert "memory-memory" not in proposal_file_path(slug_draft).name
    print("[PASS] proposal_slug avoids type duplication in filename")

    dup_a = slug_draft
    dup_b = ProposalDraft(
        proposal_id="prop-test-2",
        seq=2,
        date_prefix="20260710",
        type="memory",
        mode="create",
        topics=("workflow",),
        summary="x",
        proposed_markdown="y2",
        target={"topic": "workflow", "memory_id": "other", "path": "p2"},
        evidence=(),
        fingerprint="fp",
        evidence_fingerprints=(),
        conversation_id="c",
        checkpoint_at="t",
        triggered_by="explicit",
        trigger_phrase="记住",
    )
    deduped = dedupe_drafts_by_fingerprint((dup_a, dup_b))
    assert len(deduped) == 1
    print("[PASS] T-403: dedupe_drafts_by_fingerprint keeps one per fingerprint")

    assert detect_escalation_offer("这条更像 coding 规则，要写进 prompt 吗？")
    assert not detect_escalation_offer("好的，已完成。")
    print("[PASS] T-403: detect_escalation_offer")

    session = Session(
        conversation_id="_evolve_demo",
        session_dir=paths.data / "sessions" / "_evolve_demo",
        goal="docs 习惯",
        meta=SessionMeta(topics=["coding"], llm_model="pro", updated_at=utc_now_iso()),
        messages=[
            {"role": "user", "content": "[本次会议上下文]\nanchor"},
            {"role": "user", "content": "以后改 docs 都先更新 CHANGELOG"},
            {"role": "assistant", "content": "好的，记住了。"},
        ],
        paths=paths,
    )

    index_block = build_evolve_index_block(paths)
    assert "project-my-agent" in index_block or "memory (active)" in index_block
    assert "pending proposals" in index_block
    print("[PASS] build_evolve_index_block")

    batch_json = json.dumps(
        {
            "proposals": [
                {
                    "type": "memory",
                    "mode": "create",
                    "topics": ["coding"],
                    "summary": "改 docs 前先更新 CHANGELOG",
                    "memory_id": "coding-evolve-demo-changelog",
                    "proposed_markdown": (
                        "---\nid: coding-evolve-demo-changelog\ntopics: [coding]\n"
                        "status: active\nsummary: 改 docs 前先更新 CHANGELOG\n---\n\n"
                        "## 背景\n用户要求以后改 docs 都先更新 CHANGELOG。"
                    ),
                    "evidence": [
                        {
                            "role": "user",
                            "quote": "以后改 docs 都先更新 CHANGELOG",
                            "ref": "messages.jsonl#2",
                        }
                    ],
                }
            ],
            "user_message": "已生成 1 条 memory proposal。",
        },
        ensure_ascii=False,
    )

    mock = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content=batch_json,
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            )
        ]
    )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        log_path = tmp_path / "evolve_log.jsonl"
        log = EvolveLog(log_path)

        # Copy evolve index context into temp agent root for isolated proposal write
        evolve_copy = tmp_path / "evolve"
        shutil.copytree(paths.evolve, evolve_copy, ignore=shutil.ignore_patterns("proposals"))
        agent_tmp = AgentPaths.from_root(tmp_path)
        session_tmp = Session(
            conversation_id="_evolve_demo",
            session_dir=tmp_path / "data" / "sessions" / "_evolve_demo",
            goal=session.goal,
            meta=session.meta,
            messages=session.messages,
            paths=agent_tmp,
        )

        result = run_explicit_checkpoint(
            session_tmp,
            trigger_phrase="记住",
            user_line="记住 以后改 docs 都先更新 CHANGELOG",
            client=mock,
            paths=agent_tmp,
            evolve_log=log,
        )

        assert len(result.written_paths) == 1
        proposal_path = result.written_paths[0]
        assert proposal_path.is_file()
        text = proposal_path.read_text(encoding="utf-8")
        assert "status: pending" in text
        assert "## Summary" in text
        assert "## Proposed" in text
        assert "## Evidence" in text
        assert "以后改 docs 都先更新 CHANGELOG" in text
        assert proposal_path.parent.name == PROPOSALS_DIRNAME
        print("[PASS] run_explicit_checkpoint writes evolve/proposals/*.md")

        events = read_events(log_path)
        assert any(e.get("event") == EVENT_CHECKPOINT_OPENED for e in events)
        created = [e for e in events if e.get("event") == EVENT_PROPOSAL_CREATED]
        assert len(created) == 1
        assert created[0]["proposal_id"].startswith("prop-")
        print("[PASS] evolve_log checkpoint_opened + proposal_created")

    # T-404: accept/reject routing
    with tempfile.TemporaryDirectory() as tmp_review:
        review_root = Path(tmp_review)
        review_evolve = review_root / "evolve"
        shutil.copytree(
            paths.evolve,
            review_evolve,
            ignore=shutil.ignore_patterns("proposals"),
        )
        (review_evolve / PROPOSALS_DIRNAME).mkdir(parents=True, exist_ok=True)
        review_paths = AgentPaths.from_root(review_root)
        review_log_path = review_root / "data" / "evolve_log.jsonl"
        review_log = EvolveLog(review_log_path)

        memory_create_body = render_proposal_file(
            ProposalDraft(
                proposal_id="prop-20260710-901",
                seq=901,
                date_prefix="20260710",
                type="memory",
                mode="create",
                topics=("workflow",),
                summary="demo memory create",
                proposed_markdown=(
                    "---\nid: workflow-demo-create\ntopics: [workflow]\n"
                    "status: active\nsummary: demo memory create\n---\n\n"
                    "## 背景\nT-404 demo create."
                ),
                target={
                    "topic": "workflow",
                    "memory_id": "workflow-demo-create",
                    "path": "memories/workflow/workflow-demo-create.md",
                },
                evidence=(
                    EvidenceItem(role="user", quote="demo quote", ref="messages.jsonl#1"),
                ),
                fingerprint="fp901",
                evidence_fingerprints=("ef901",),
                conversation_id="_t404",
                checkpoint_at="2026-07-10T00:00:00Z",
                triggered_by="explicit",
                trigger_phrase="记住",
            )
        )
        mem_create_path = review_evolve / PROPOSALS_DIRNAME / "20260710-901-memory-demo-create.md"
        mem_create_path.write_text(memory_create_body, encoding="utf-8")

        mem_target = review_evolve / "memories" / "workflow" / "workflow-demo-update.md"
        mem_target.parent.mkdir(parents=True, exist_ok=True)
        mem_target.write_text(
            "---\nid: workflow-demo-update\ntopics: [workflow]\n"
            "status: active\nsummary: before\n---\n\n## 背景\nOriginal.\n",
            encoding="utf-8",
        )
        memory_update_body = render_proposal_file(
            ProposalDraft(
                proposal_id="prop-20260710-902",
                seq=902,
                date_prefix="20260710",
                type="memory",
                mode="update",
                topics=("workflow",),
                summary="demo memory update",
                proposed_markdown="补充：T-404 update revision body。",
                target={
                    "topic": "workflow",
                    "memory_id": "workflow-demo-update",
                    "path": "memories/workflow/workflow-demo-update.md",
                },
                evidence=(
                    EvidenceItem(role="user", quote="update quote", ref="messages.jsonl#2"),
                ),
                fingerprint="fp902",
                evidence_fingerprints=("ef902",),
                conversation_id="_t404",
                checkpoint_at="2026-07-10T00:00:00Z",
                triggered_by="explicit",
                trigger_phrase="记住",
            )
        )
        mem_update_path = review_evolve / PROPOSALS_DIRNAME / "20260710-902-memory-demo-update.md"
        mem_update_path.write_text(memory_update_body, encoding="utf-8")

        prompt_patch_body = render_proposal_file(
            ProposalDraft(
                proposal_id="prop-20260710-903",
                seq=903,
                date_prefix="20260710",
                type="prompt_patch",
                mode="create",
                topics=("coding",),
                summary="demo prompt patch",
                proposed_markdown="## T-404 验收段\n- append_section demo rule",
                target={
                    "topic": "coding",
                    "path": "prompts/coding.md",
                    "mode": "append_section",
                    "anchor": "## T-404 验收段",
                },
                evidence=(
                    EvidenceItem(role="user", quote="patch quote", ref="messages.jsonl#3"),
                ),
                fingerprint="fp903",
                evidence_fingerprints=("ef903",),
                conversation_id="_t404",
                checkpoint_at="2026-07-10T00:00:00Z",
                triggered_by="explicit",
                trigger_phrase="记住",
            )
        )
        prompt_patch_path = review_evolve / PROPOSALS_DIRNAME / "20260710-903-prompt_patch-t404.md"
        prompt_patch_path.write_text(prompt_patch_body, encoding="utf-8")

        tool_body = render_proposal_file(
            ProposalDraft(
                proposal_id="prop-20260710-904",
                seq=904,
                date_prefix="20260710",
                type="tool_suggestion",
                mode="create",
                topics=("workflow",),
                summary="demo tool spec",
                proposed_markdown=(
                    "### 意图\nSort downloads.\n\n### 放置\n"
                    "- 目录: `evolve/tools/workflow/sort_downloads/`"
                ),
                target={
                    "topic": "workflow",
                    "tool_name": "sort_downloads",
                    "path": "tools/workflow/sort_downloads/",
                },
                evidence=(
                    EvidenceItem(role="user", quote="tool quote", ref="messages.jsonl#4"),
                ),
                fingerprint="fp904",
                evidence_fingerprints=("ef904",),
                conversation_id="_t404",
                checkpoint_at="2026-07-10T00:00:00Z",
                triggered_by="explicit",
                trigger_phrase="记住",
            )
        )
        tool_path = review_evolve / PROPOSALS_DIRNAME / "20260710-904-tool_suggestion-sort-downloads.md"
        tool_path.write_text(tool_body, encoding="utf-8")

        reject_body = render_proposal_file(
            ProposalDraft(
                proposal_id="prop-20260710-905",
                seq=905,
                date_prefix="20260710",
                type="memory",
                mode="create",
                topics=("workflow",),
                summary="demo reject",
                proposed_markdown="## 背景\nreject me",
                target={
                    "topic": "workflow",
                    "memory_id": "workflow-demo-reject",
                    "path": "memories/workflow/workflow-demo-reject.md",
                },
                evidence=(
                    EvidenceItem(role="user", quote="reject quote", ref="messages.jsonl#5"),
                ),
                fingerprint="fp905",
                evidence_fingerprints=("ef905",),
                conversation_id="_t404",
                checkpoint_at="2026-07-10T00:00:00Z",
                triggered_by="explicit",
                trigger_phrase="记住",
            )
        )
        reject_path = review_evolve / PROPOSALS_DIRNAME / "20260710-905-memory-demo-reject.md"
        reject_path.write_text(reject_body, encoding="utf-8")

        create_result = accept_proposal(
            "prop-20260710-901",
            paths=review_paths,
            evolve_log=review_log,
        )
        created_mem = review_evolve / "memories" / "workflow" / "workflow-demo-create.md"
        assert created_mem.is_file()
        assert "T-404 demo create" in created_mem.read_text(encoding="utf-8")
        assert create_result.status == "accepted"
        print("[PASS] T-404: memory create → evolve/memories/<topic>/<id>.md")

        update_result = accept_proposal(
            "prop-20260710-902",
            paths=review_paths,
            evolve_log=review_log,
        )
        updated_text = mem_target.read_text(encoding="utf-8")
        assert "## 修订" in updated_text
        assert "T-404 update revision body" in updated_text
        assert "Original." in updated_text
        assert update_result.status == "accepted"
        print("[PASS] T-404: memory update appends ## 修订 YYYY-MM-DD")

        coding_prompt = review_evolve / "prompts" / "coding.md"
        before_prompt = coding_prompt.read_text(encoding="utf-8")
        accept_proposal("prop-20260710-903", paths=review_paths, evolve_log=review_log)
        after_prompt = coding_prompt.read_text(encoding="utf-8")
        assert len(after_prompt) > len(before_prompt)
        assert "## T-404 验收段" in after_prompt
        print("[PASS] T-404: prompt_patch append_section")

        tool_dir = review_evolve / "tools" / "workflow" / "sort_downloads"
        assert not tool_dir.exists()
        accept_proposal("prop-20260710-904", paths=review_paths, evolve_log=review_log)
        assert not tool_dir.exists()
        print("[PASS] T-404: tool_suggestion accepted without code generation")

        reject_result = reject_proposal(
            "prop-20260710-905",
            paths=review_paths,
            evolve_log=review_log,
        )
        archived = proposals_archive_dir(review_evolve) / reject_path.name
        assert archived.is_file()
        assert not reject_path.is_file()
        assert "status: rejected" in archived.read_text(encoding="utf-8")
        assert reject_result.status == "rejected"
        print("[PASS] T-404: reject → archive + status=rejected")

        review_events = read_events(review_log_path)
        assert any(e.get("event") == EVENT_EVOLVE_ACCEPTED for e in review_events)
        assert any(e.get("event") == EVENT_EVOLVE_REJECTED for e in review_events)
        assert any(e.get("event") == EVENT_TOOL_SPEC_ACCEPTED for e in review_events)
        print("[PASS] T-404: evolve_log evolve_accepted / evolve_rejected / tool_spec_accepted")

        pending_lines = format_pending_proposals_list(review_paths)
        assert "prop-20260710-901" not in pending_lines
        assert "prop-20260710-905" not in pending_lines
        print("[PASS] T-404: format_pending_proposals_list")

    # T-407 dedup: evidence_fp global block + pending supersede + identity hard block
    with tempfile.TemporaryDirectory() as tmp_dedup:
        dedup_root = Path(tmp_dedup)
        dedup_evolve = dedup_root / "evolve"
        shutil.copytree(
            paths.evolve,
            dedup_evolve,
            ignore=shutil.ignore_patterns("proposals"),
        )
        (dedup_evolve / PROPOSALS_DIRNAME).mkdir(parents=True, exist_ok=True)
        dedup_paths = AgentPaths.from_root(dedup_root)
        dedup_log_path = dedup_root / "data" / "evolve_log.jsonl"
        dedup_log = EvolveLog(dedup_log_path)

        quote = "以后改 docs 都先更新 CHANGELOG"
        ef = evidence_fingerprint(quote)
        accepted_old = render_proposal_file(
            ProposalDraft(
                proposal_id="prop-20990707-001",
                seq=1,
                date_prefix="20990707",
                type="memory",
                mode="create",
                topics=("coding",),
                summary="accepted dedup sample",
                proposed_markdown="## 背景\nold",
                target={
                    "topic": "coding",
                    "memory_id": "coding-dedup-accepted",
                    "path": "memories/coding/coding-dedup-accepted.md",
                },
                evidence=(EvidenceItem(role="user", quote=quote, ref="messages.jsonl#2"),),
                fingerprint="fp-acc",
                evidence_fingerprints=(ef,),
                conversation_id="_t407",
                checkpoint_at="2026-07-07T00:00:00Z",
                triggered_by="explicit",
                trigger_phrase="记住",
            )
        )
        accepted_path = dedup_evolve / PROPOSALS_DIRNAME / "20990707-001-memory-dedup-accepted.md"
        accepted_path.write_text(
            _set_frontmatter_status(accepted_old, "accepted"),
            encoding="utf-8",
        )
        assert ef in collect_accepted_evidence_fingerprints(dedup_evolve)
        blocked_gate = evaluate_dedup_gate(
            ProposalDraft(
                proposal_id="prop-20990707-002",
                seq=2,
                date_prefix="20990707",
                type="memory",
                mode="create",
                topics=("coding",),
                summary="duplicate evidence",
                proposed_markdown="## 背景\nnew",
                target={
                    "topic": "coding",
                    "memory_id": "coding-dedup-new",
                    "path": "memories/coding/coding-dedup-new.md",
                },
                evidence=(EvidenceItem(role="user", quote=quote, ref="messages.jsonl#2"),),
                fingerprint="fp-new",
                evidence_fingerprints=(ef,),
                conversation_id="_t407",
                checkpoint_at="2026-07-07T00:00:00Z",
                triggered_by="explicit",
                trigger_phrase="记住",
            ),
            paths=dedup_paths,
            accepted_evidence_fingerprints=collect_accepted_evidence_fingerprints(dedup_evolve),
        )
        assert not blocked_gate.allow and blocked_gate.dedup == "blocked"
        print("[PASS] T-407: accepted evidence_fingerprint hard block")

        pending_old = render_proposal_file(
            ProposalDraft(
                proposal_id="prop-20990707-010",
                seq=10,
                date_prefix="20990707",
                type="memory",
                mode="create",
                topics=("workflow",),
                summary="pending to supersede",
                proposed_markdown="## 背景\npending",
                target={
                    "topic": "workflow",
                    "memory_id": "workflow-dedup-target",
                    "path": "memories/workflow/workflow-dedup-target.md",
                },
                evidence=(EvidenceItem(role="user", quote="pending quote unique", ref="m#1"),),
                fingerprint="fp-pend",
                evidence_fingerprints=(evidence_fingerprint("pending quote unique"),),
                conversation_id="_t407",
                checkpoint_at="2026-07-07T00:00:00Z",
                triggered_by="explicit",
                trigger_phrase="记住",
            )
        )
        pending_path = dedup_evolve / PROPOSALS_DIRNAME / "20990707-010-memory-pending-old.md"
        pending_path.write_text(pending_old, encoding="utf-8")

        supersede_gate = evaluate_dedup_gate(
            ProposalDraft(
                proposal_id="prop-20990707-011",
                seq=11,
                date_prefix="20990707",
                type="memory",
                mode="create",
                topics=("workflow",),
                summary="pending replacement",
                proposed_markdown="## 背景\nreplacement",
                target={
                    "topic": "workflow",
                    "memory_id": "workflow-dedup-target",
                    "path": "memories/workflow/workflow-dedup-target.md",
                },
                evidence=(EvidenceItem(role="user", quote="replacement quote unique", ref="m#2"),),
                fingerprint="fp-repl",
                evidence_fingerprints=(evidence_fingerprint("replacement quote unique"),),
                conversation_id="_t407",
                checkpoint_at="2026-07-07T00:00:00Z",
                triggered_by="explicit",
                trigger_phrase="记住",
            ),
            paths=dedup_paths,
            accepted_evidence_fingerprints=collect_accepted_evidence_fingerprints(dedup_evolve),
        )
        assert supersede_gate.allow and supersede_gate.dedup == "superseded"
        assert len(supersede_gate.supersede) == 1
        supersede_proposal_record(supersede_gate.supersede[0], superseded_by="prop-20990707-011")
        assert "status: superseded" in pending_path.read_text(encoding="utf-8")
        assert "superseded_by: prop-20990707-011" in pending_path.read_text(encoding="utf-8")
        print("[PASS] T-407: pending same target superseded")

        (dedup_evolve / "memories" / "coding" / "coding-existing.md").parent.mkdir(
            parents=True, exist_ok=True
        )
        (dedup_evolve / "memories" / "coding" / "coding-existing.md").write_text(
            "---\nid: coding-existing\nstatus: active\n---\n",
            encoding="utf-8",
        )
        identity_gate = evaluate_dedup_gate(
            ProposalDraft(
                proposal_id="prop-20990707-020",
                seq=20,
                date_prefix="20990707",
                type="memory",
                mode="create",
                topics=("coding",),
                summary="dup id",
                proposed_markdown="## 背景\nx",
                target={
                    "topic": "coding",
                    "memory_id": "coding-existing",
                    "path": "memories/coding/coding-existing.md",
                },
                evidence=(EvidenceItem(role="user", quote="another unique quote", ref="m#3"),),
                fingerprint="fp-id",
                evidence_fingerprints=(evidence_fingerprint("another unique quote"),),
                conversation_id="_t407",
                checkpoint_at="2026-07-07T00:00:00Z",
                triggered_by="explicit",
                trigger_phrase="记住",
            ),
            paths=dedup_paths,
            accepted_evidence_fingerprints=collect_accepted_evidence_fingerprints(dedup_evolve),
        )
        assert not identity_gate.allow
        assert "memory id already exists" in identity_gate.reason
        print("[PASS] T-407: memory create blocked when id exists")

        tool_gate = evaluate_dedup_gate(
            ProposalDraft(
                proposal_id="prop-20990707-021",
                seq=21,
                date_prefix="20990707",
                type="tool_suggestion",
                mode="create",
                topics=("workflow",),
                summary="dup tool",
                proposed_markdown="### 意图\nx",
                target={
                    "topic": "workflow",
                    "tool_name": "write_text",
                    "path": "tools/workflow/write_text/",
                },
                evidence=(EvidenceItem(role="user", quote="tool quote unique", ref="m#4"),),
                fingerprint="fp-tool",
                evidence_fingerprints=(evidence_fingerprint("tool quote unique"),),
                conversation_id="_t407",
                checkpoint_at="2026-07-07T00:00:00Z",
                triggered_by="explicit",
                trigger_phrase="记住",
            ),
            paths=dedup_paths,
            accepted_evidence_fingerprints=collect_accepted_evidence_fingerprints(dedup_evolve),
        )
        assert not tool_gate.allow
        assert "write_text" in tool_gate.reason
        print("[PASS] T-407: tool_suggestion blocked when name registered")

        dedup_session = Session(
            conversation_id="_t407_cp",
            session_dir=dedup_root / "data" / "sessions" / "_t407_cp",
            goal="docs",
            meta=SessionMeta(topics=["coding"], llm_model="pro", updated_at=utc_now_iso()),
            messages=[
                {"role": "user", "content": "[本次会议上下文]\nanchor"},
                {"role": "user", "content": quote},
            ],
            paths=dedup_paths,
        )
        dedup_batch = json.dumps(
            {
                "proposals": [
                    {
                        "type": "memory",
                        "mode": "create",
                        "topics": ["coding"],
                        "summary": "blocked by accepted evidence",
                        "memory_id": "coding-dedup-runtime",
                        "proposed_markdown": "## 背景\nx",
                        "evidence": [{"role": "user", "quote": quote, "ref": "messages.jsonl#2"}],
                    }
                ],
                "user_message": "test",
            },
            ensure_ascii=False,
        )
        cp_result = run_explicit_checkpoint(
            dedup_session,
            trigger_phrase="记住",
            user_line=f"记住 {quote}",
            client=_MockLLM(
                responses=[
                    LLMResponse(
                        model="mock",
                        content=dedup_batch,
                        tool_calls=[],
                        finish_reason="stop",
                        usage=None,
                        raw={},
                    )
                ]
            ),
            paths=dedup_paths,
            evolve_log=dedup_log,
        )
        assert len(cp_result.written_paths) == 0
        dedup_events = read_events(dedup_log_path)
        assert any(
            e.get("event") == EVENT_PROPOSAL_CREATED and e.get("dedup") == "blocked"
            for e in dedup_events
        )
        print("[PASS] T-407: run_explicit_checkpoint logs blocked dedup")

    if load_config().api_key:
        print("[SKIP] live checkpoint LLM (optional)")
    else:
        print("[SKIP] live checkpoint: LLM_API_KEY not set")

    return None


if __name__ == "__main__":
    _demo()
