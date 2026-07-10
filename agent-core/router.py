"""Topic routing: S2 JSON proposal, CLI shortcuts, replace vs append (RUNTIME.md §6.4, TASKS T-205)."""

from __future__ import annotations

import json
import re
import sys
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_client import LLMClient, load_config
from loader import build_system_prompt, load_topic_index
from paths import AgentPaths
from session import Session, SessionMeta, utc_now_iso

TopicApplyMode = Literal["replace", "append"]

ROUTE_USER_MESSAGE_TEMPLATE = """本次会议目标：
{goal}

请根据 system 中的主题索引，提议应加载的 topics。只输出 JSON：{{"topics":[],"reason":""}}"""

TOPIC_CONFIRM_PROMPT_TEMPLATE = "确认主题 {hint}？(y/n/或直接输入 id，逗号分隔)"


class TopicRoutingError(ValueError):
    """Invalid topic command or proposal."""


class TopicCommandKind(StrEnum):
    REPLACE = "replace"
    APPEND = "append"
    RE_ROUTE = "re_route"


@dataclass(frozen=True, slots=True)
class ParsedTopicCommand:
    kind: TopicCommandKind
    topics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TopicProposal:
    topics: tuple[str, ...]
    reason: str
    rejected: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TopicConfirmation:
    """S3 user response to an S2 proposal (MEMORY §4.2)."""

    topics: tuple[str, ...]
    action: Literal["accept", "reject", "override", "empty"]


def build_topic_confirm_prompt(proposal: TopicProposal) -> str:
    """CLI prompt for S3 topic confirmation (RUNTIME §3 S3, MEMORY §4.2)."""
    hint = ", ".join(proposal.topics) if proposal.topics else "(none)"
    return TOPIC_CONFIRM_PROMPT_TEMPLATE.format(hint=hint)


def format_proposal_banner(proposal: TopicProposal, *, header: str) -> str:
    """Human-readable S2 proposal line for REPL."""
    if proposal.topics:
        line = f"{header} — 建议主题: {', '.join(proposal.topics)}"
        if proposal.reason:
            line += f" ({proposal.reason})"
    else:
        line = f"{header} — 无 LLM 主题建议，请手动输入。"
    if proposal.rejected:
        line += f" [已忽略未注册: {', '.join(proposal.rejected)}]"
    return line


def parse_manual_topic_ids(
    text: str,
    *,
    valid_topic_ids: frozenset[str],
) -> list[str]:
    """Parse user-typed topic ids (override path)."""
    ids: list[str] = []
    for part in text.replace("，", ",").split(","):
        for token in part.split():
            topic = token.strip()
            if not topic:
                continue
            if topic not in valid_topic_ids:
                raise TopicRoutingError(f"unknown topic id: {topic}")
            if topic not in ids:
                ids.append(topic)
    return ids


def resolve_topic_confirmation(
    answer: str,
    proposal: TopicProposal,
    *,
    valid_topic_ids: frozenset[str],
) -> TopicConfirmation:
    """Map S3 user input to accept / reject / override (T-304)."""
    stripped = answer.strip()
    lower = stripped.casefold()
    if lower in {"n", "no", "否"}:
        return TopicConfirmation(topics=(), action="reject")
    if lower in {"y", "yes", "是", ""} and proposal.topics:
        return TopicConfirmation(topics=proposal.topics, action="accept")
    if not stripped:
        return TopicConfirmation(topics=(), action="empty")
    return TopicConfirmation(
        topics=tuple(parse_manual_topic_ids(stripped, valid_topic_ids=valid_topic_ids)),
        action="override",
    )


def run_topic_routing_s2(
    session: Session,
    *,
    client: LLMClient | None = None,
    paths: AgentPaths | None = None,
) -> TopicProposal:
    """S2: LLM proposes topics from goal + index (RUNTIME §6.4)."""
    session.meta.phase = "S2"
    session.save()
    return propose_topics_with_llm(session.goal, session=session, client=client, paths=paths)


def apply_topic_confirmation(
    session: Session,
    confirmation: TopicConfirmation,
    *,
    mode: TopicApplyMode,
    valid_topic_ids: frozenset[str] | None = None,
    save: bool = True,
) -> Session | None:
    """S3→S4: persist confirmed topics; ``reject`` / ``empty`` leaves meta unchanged."""
    if confirmation.action in {"reject", "empty"} or not confirmation.topics:
        return None
    session.meta.phase = "S3"
    return apply_confirmed_topics(
        session,
        list(confirmation.topics),
        mode=mode,
        valid_topic_ids=valid_topic_ids,
        save=save,
    )


def registered_topic_ids(paths: AgentPaths) -> frozenset[str]:
    return frozenset(entry.id for entry in load_topic_index(paths.evolve))


def build_route_user_message(goal: str) -> str:
    """User message for S2 topic routing (RUNTIME.md §6.4)."""
    return ROUTE_USER_MESSAGE_TEMPLATE.format(goal=goal.strip() or "(unset)")


def parse_topic_command(text: str) -> ParsedTopicCommand | None:
    """Parse REPL shortcuts: ``主题 …`` / ``加主题 …`` / ``换主题``."""
    stripped = text.strip()
    if not stripped:
        return None

    lowered = stripped.casefold()
    if lowered in {"换主题", "change topic", "change topics"}:
        return ParsedTopicCommand(kind=TopicCommandKind.RE_ROUTE, topics=())

    if stripped.startswith("加主题"):
        payload = stripped[len("加主题") :].strip()
        if not payload:
            raise TopicRoutingError("加主题 requires at least one topic id")
        return ParsedTopicCommand(
            kind=TopicCommandKind.APPEND,
            topics=tuple(_split_topic_tokens(payload)),
        )

    if stripped.startswith("主题"):
        payload = stripped[len("主题") :].strip()
        if not payload:
            raise TopicRoutingError("主题 requires at least one topic id")
        return ParsedTopicCommand(
            kind=TopicCommandKind.REPLACE,
            topics=tuple(_split_topic_tokens(payload)),
        )

    if lowered.startswith("topic "):
        payload = stripped[6:].strip()
        if not payload:
            raise TopicRoutingError("topic requires at least one topic id")
        return ParsedTopicCommand(
            kind=TopicCommandKind.REPLACE,
            topics=tuple(_split_topic_tokens(payload)),
        )

    return None


def parse_topic_proposal(
    content: str,
    *,
    valid_topic_ids: frozenset[str],
) -> TopicProposal:
    """Parse S2 LLM JSON output; drop unknown topic ids (EVOLVE.md)."""
    payload = _extract_json_object(content)
    topics_raw = payload.get("topics")
    if not isinstance(topics_raw, list):
        raise TopicRoutingError("proposal JSON missing topics array")

    reason_raw = payload.get("reason", "")
    reason = reason_raw.strip() if isinstance(reason_raw, str) else str(reason_raw)

    requested = [_normalize_topic_id(item) for item in topics_raw]
    requested = [topic for topic in requested if topic]

    accepted: list[str] = []
    rejected: list[str] = []
    for topic in requested:
        if topic in valid_topic_ids:
            if topic not in accepted:
                accepted.append(topic)
        else:
            rejected.append(topic)

    return TopicProposal(
        topics=tuple(accepted),
        reason=reason,
        rejected=tuple(rejected),
    )


def propose_topics_with_llm(
    goal: str,
    *,
    session: Session,
    client: LLMClient | None = None,
    paths: AgentPaths | None = None,
) -> TopicProposal:
    """S2: one flash LLM call, temperature 0, no tools (RUNTIME.md §6.2)."""
    agent_paths = paths or session.paths
    valid_ids = registered_topic_ids(agent_paths)
    llm = client or LLMClient()

    system_text = build_system_prompt(session, paths=agent_paths, include_overlay=False).prompt
    user_message = build_route_user_message(goal)
    response = llm.chat(
        [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_message},
        ],
        model=llm.config.model,
        temperature=0,
    )
    return parse_topic_proposal(response.content or "", valid_topic_ids=valid_ids)


def apply_confirmed_topics(
    session: Session,
    topics: list[str] | tuple[str, ...],
    *,
    mode: TopicApplyMode,
    valid_topic_ids: frozenset[str] | None = None,
    save: bool = True,
) -> Session:
    """Write topics + resolved ``llm_model`` to session meta (§6.1)."""
    valid = valid_topic_ids or registered_topic_ids(session.paths)
    cleaned, rejected = _partition_topics(topics, valid)
    if rejected:
        raise TopicRoutingError(f"unknown topic id(s): {', '.join(rejected)}")

    if mode == "replace":
        merged = cleaned
    else:
        merged = list(dict.fromkeys([*session.meta.topics, *cleaned]))

    session.set_topics(merged, phase="S4")
    if save:
        session.save()
    return session


def apply_topic_command(
    session: Session,
    command: ParsedTopicCommand,
    *,
    valid_topic_ids: frozenset[str] | None = None,
    save: bool = True,
) -> Session | None:
    """Apply shortcut commands; ``RE_ROUTE`` returns None (caller runs S2)."""
    if command.kind == TopicCommandKind.RE_ROUTE:
        return None

    mode: TopicApplyMode = "append" if command.kind == TopicCommandKind.APPEND else "replace"
    return apply_confirmed_topics(
        session,
        list(command.topics),
        mode=mode,
        valid_topic_ids=valid_topic_ids,
        save=save,
    )


def _split_topic_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for part in re.split(r"[\s,，、]+", text):
        topic = part.strip()
        if topic:
            tokens.append(topic)
    return tokens


def _normalize_topic_id(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _partition_topics(
    topics: list[str] | tuple[str, ...],
    valid_ids: frozenset[str],
) -> tuple[list[str], list[str]]:
    cleaned: list[str] = []
    rejected: list[str] = []
    for raw in topics:
        topic = raw.strip()
        if not topic:
            continue
        if topic in valid_ids:
            if topic not in cleaned:
                cleaned.append(topic)
        else:
            rejected.append(topic)
    return cleaned, rejected


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise TopicRoutingError("empty LLM response")

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL)
    if fence:
        stripped = fence.group(1)

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise TopicRoutingError("response does not contain a JSON object")

    try:
        payload = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as exc:
        raise TopicRoutingError(f"invalid JSON in proposal: {exc}") from exc

    if not isinstance(payload, dict):
        raise TopicRoutingError("proposal JSON must be an object")
    return payload


def _demo() -> None:
    paths = AgentPaths.discover()
    valid = registered_topic_ids(paths)
    assert "coding" in valid
    assert "workflow" in valid
    print(f"[PASS] registered topics: {len(valid)} from _index.toml")

    replace_cmd = parse_topic_command("主题 coding workflow")
    assert replace_cmd is not None
    assert replace_cmd.kind == TopicCommandKind.REPLACE
    assert replace_cmd.topics == ("coding", "workflow")
    print("[PASS] parse 主题 … → replace")

    append_cmd = parse_topic_command("加主题 workflow")
    assert append_cmd is not None
    assert append_cmd.kind == TopicCommandKind.APPEND
    assert append_cmd.topics == ("workflow",)
    print("[PASS] parse 加主题 … → append")

    reroute = parse_topic_command("换主题")
    assert reroute is not None
    assert reroute.kind == TopicCommandKind.RE_ROUTE
    print("[PASS] parse 换主题 → re_route")

    proposal = parse_topic_proposal(
        '{"topics": ["coding", "workflow", "unknown"], "reason": "dev docs"}',
        valid_topic_ids=valid,
    )
    assert proposal.topics == ("coding", "workflow")
    assert proposal.rejected == ("unknown",)
    assert proposal.reason == "dev docs"
    print("[PASS] parse_topic_proposal filters unknown ids")

    fenced = parse_topic_proposal(
        '```json\n{"topics":["writing"],"reason":"copy"}\n```',
        valid_topic_ids=valid,
    )
    assert fenced.topics == ("writing",)
    print("[PASS] parse_topic_proposal handles markdown fence")

    user_msg = build_route_user_message("Write MEMORY.md")
    assert "Write MEMORY.md" in user_msg
    assert "只输出 JSON" in user_msg
    print("[PASS] build_route_user_message template")

    proposal_s2 = TopicProposal(
        topics=("coding", "workflow"),
        reason="dev docs",
        rejected=("unknown",),
    )
    assert "coding, workflow" in build_topic_confirm_prompt(proposal_s2)
    banner = format_proposal_banner(proposal_s2, header="新会话")
    assert "建议主题: coding, workflow" in banner
    assert "已忽略未注册: unknown" in banner
    print("[PASS] T-304: S2 proposal banner + S3 confirm prompt")

    accept = resolve_topic_confirmation("y", proposal_s2, valid_topic_ids=valid)
    assert accept.action == "accept"
    assert accept.topics == ("coding", "workflow")
    reject = resolve_topic_confirmation("n", proposal_s2, valid_topic_ids=valid)
    assert reject.action == "reject"
    assert reject.topics == ()
    override = resolve_topic_confirmation("writing", proposal_s2, valid_topic_ids=valid)
    assert override.action == "override"
    assert override.topics == ("writing",)
    print("[PASS] T-304: resolve accept / reject / override")

    with tempfile.TemporaryDirectory() as tmp:
        session = Session(
            conversation_id="_router_demo",
            session_dir=Path(tmp) / "sessions" / "_router_demo",
            goal="Router demo",
            meta=SessionMeta(
                topics=["writing"],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S2",
            ),
            messages=[],
            paths=paths,
        )
        session.session_dir.mkdir(parents=True, exist_ok=True)

        apply_confirmed_topics(session, ["coding"], mode="replace", save=False)
        assert session.meta.topics == ["coding"]
        cfg = load_config()
        assert session.meta.llm_model == cfg.model_coding
        print("[PASS] replace sets llm_model to pro when coding selected")

        apply_confirmed_topics(session, ["workflow"], mode="append", save=False)
        assert session.meta.topics == ["coding", "workflow"]
        assert session.meta.llm_model == cfg.model_coding
        print("[PASS] append unions topics; keeps pro model")

        shortcut = parse_topic_command("主题 writing")
        assert shortcut is not None
        applied = apply_topic_command(session, shortcut, save=False)
        assert applied is not None
        assert session.meta.topics == ["writing"]
        assert session.meta.llm_model == cfg.model
        print("[PASS] 主题 shortcut replaces topics + flash model")

        reroute_result = apply_topic_command(session, reroute, save=False)
        assert reroute_result is None
        print("[PASS] 换主题 returns None for S2 re-route")

        confirm = resolve_topic_confirmation("y", proposal_s2, valid_topic_ids=valid)
        applied = apply_topic_confirmation(session, confirm, mode="replace", save=False)
        assert applied is not None
        assert session.meta.topics == ["coding", "workflow"]
        assert session.meta.phase == "S4"
        print("[PASS] T-304: apply_topic_confirmation S3→S4")

        veto = resolve_topic_confirmation("否", proposal_s2, valid_topic_ids=valid)
        before_topics = list(session.meta.topics)
        veto_result = apply_topic_confirmation(session, veto, mode="replace", save=False)
        assert veto_result is None
        assert session.meta.topics == before_topics
        print("[PASS] T-304: reject leaves topics unchanged")

    if load_config().api_key:
        live_session = Session(
            conversation_id="_router_live",
            session_dir=paths.data / "sessions" / "_router_live",
            goal="Implement session persistence",
            meta=SessionMeta(
                topics=[],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S2",
            ),
            messages=[],
            paths=paths,
        )
        live_session.session_dir.mkdir(parents=True, exist_ok=True)
        live = propose_topics_with_llm("Implement session persistence", session=live_session)
        assert live.topics, "expected at least one topic from live API"
        print(f"[PASS] live S2 proposal: {list(live.topics)!r} ({live.reason[:60]!r})")
    else:
        print("[SKIP] live S2 proposal: LLM_API_KEY not set")


if __name__ == "__main__":
    _demo()
