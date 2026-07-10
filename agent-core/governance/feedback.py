"""Exit feedback protocol (RUNTIME §10, GOVERNANCE §6.5, TASKS T-602b)."""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from governance.suspect import process_feedback_suspect
from paths import AgentPaths
from session import Session, create_new
from tools.logging import (
    EVENT_FEEDBACK_NEGATIVE,
    EVENT_FEEDBACK_POSITIVE,
    EVENT_MARKED_SUSPECT,
    EvolveLog,
    read_events,
    utc_now_iso,
)

FeedbackVerdict = Literal["positive", "negative", "skip"]
_LEVEL_RANK = {"L4": 4, "L3": 3, "L2": 2, "L1": 1}
_MIN_FEEDBACK_LEVEL = 2
_ENV_FEEDBACK_ON_EXIT = "MY_AGENT_FEEDBACK_ON_EXIT"


def feedback_on_exit_enabled() -> bool:
    raw = os.environ.get(_ENV_FEEDBACK_ON_EXIT, "").strip().casefold()
    return raw in {"1", "true", "yes", "on"}


def pick_pending_feedback_entity(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick one pending entity: L4 > L3 > L2; same layer → latest ``used_at``."""
    eligible: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        level = str(entry.get("level", "")).strip().upper()
        if _LEVEL_RANK.get(level, 0) < _MIN_FEEDBACK_LEVEL:
            continue
        entity_id = str(entry.get("entity_id", "")).strip()
        if not entity_id:
            continue
        eligible.append(entry)
    if not eligible:
        return None

    max_rank = max(_LEVEL_RANK.get(str(entry.get("level", "")).strip().upper(), 0) for entry in eligible)
    top = [
        entry
        for entry in eligible
        if _LEVEL_RANK.get(str(entry.get("level", "")).strip().upper(), 0) == max_rank
    ]
    top.sort(key=lambda entry: str(entry.get("used_at", "")), reverse=True)
    return top[0]


def feedback_entity_ids_in_session(log_path: Path, conversation_id: str) -> set[str]:
    """Entity ids that already received feedback in this conversation."""
    answered: set[str] = set()
    for event in read_events(log_path):
        if event.get("conversation_id") != conversation_id:
            continue
        if event.get("event") not in {EVENT_FEEDBACK_POSITIVE, EVENT_FEEDBACK_NEGATIVE}:
            continue
        entity_id = event.get("entity_id")
        if isinstance(entity_id, str) and entity_id.strip():
            answered.add(entity_id.strip())
    return answered


def format_entity_label(entry: dict[str, Any]) -> str:
    entity_type = str(entry.get("type", "?")).strip() or "?"
    entity_id = str(entry.get("entity_id", "?")).strip() or "?"
    return f"{entity_type}:{entity_id}"


def format_exit_feedback_prompt(entry: dict[str, Any]) -> str:
    label = format_entity_label(entry)
    return "\n".join(
        [
            f"本次会话用到了 evolve 条目：{label}",
            "这次用得对吗？(y/n，回车跳过)",
            ">",
        ]
    )


def parse_feedback_response(line: str) -> FeedbackVerdict:
    text = line.strip().casefold()
    if not text or text == "skip":
        return "skip"
    if text in {"y", "yes", "对", "是", "ok", "好"}:
        return "positive"
    if text in {"n", "no", "不对", "否", "不好"}:
        return "negative"
    return "skip"


def remove_pending_feedback_entry(session: Session, entity_id: str) -> None:
    needle = entity_id.strip()
    session.meta.pending_feedback = [
        entry
        for entry in session.meta.pending_feedback
        if isinstance(entry, dict) and str(entry.get("entity_id", "")).strip() != needle
    ]


def apply_exit_feedback(
    session: Session,
    *,
    entry: dict[str, Any],
    verdict: FeedbackVerdict,
    evolve_log: EvolveLog,
) -> None:
    if verdict == "skip":
        return
    entity_id = str(entry.get("entity_id", "")).strip()
    if not entity_id:
        return
    if verdict == "positive":
        evolve_log.log_feedback_positive(
            entity_id=entity_id,
            conversation_id=session.conversation_id,
        )
    else:
        evolve_log.log_feedback_negative(
            entity_id=entity_id,
            conversation_id=session.conversation_id,
        )
        process_feedback_suspect(
            session.paths,
            entity_id,
            evolve_log=evolve_log,
        )
    remove_pending_feedback_entry(session, entity_id)


def maybe_run_exit_feedback(
    session: Session,
    *,
    paths: AgentPaths,
    input_fn: Callable[[], str],
    output_fn: Callable[[str], None],
    evolve_log: EvolveLog | None = None,
) -> FeedbackVerdict | None:
    """Prompt on exit when enabled and a L2+ pending entity exists."""
    if not feedback_on_exit_enabled():
        return None

    session.refresh_pending_feedback_from_disk()
    pending = [entry for entry in session.meta.pending_feedback if isinstance(entry, dict)]
    if not pending:
        return None

    log = evolve_log if evolve_log is not None else EvolveLog.for_agent(paths)
    answered = feedback_entity_ids_in_session(log.path, session.conversation_id)
    pending = [
        entry
        for entry in pending
        if str(entry.get("entity_id", "")).strip() not in answered
    ]
    entry = pick_pending_feedback_entity(pending)
    if entry is None:
        return None

    output_fn(format_exit_feedback_prompt(entry))
    try:
        line = input_fn("> ")
    except EOFError:
        line = ""
    verdict = parse_feedback_response(line)
    apply_exit_feedback(session, entry=entry, verdict=verdict, evolve_log=log)
    if verdict != "skip":
        session.save()
    return verdict


def _demo() -> None:
    entries = [
        {"entity_id": "mem-old", "type": "memory", "level": "L2", "used_at": "2026-07-09T10:00:00Z"},
        {"entity_id": "tool-new", "type": "tool", "level": "L3", "used_at": "2026-07-10T09:00:00Z"},
        {"entity_id": "tool-old", "type": "tool", "level": "L3", "used_at": "2026-07-09T12:00:00Z"},
    ]
    picked = pick_pending_feedback_entity(entries)
    assert picked is not None and picked["entity_id"] == "tool-new"
    print("[PASS] T-602b: pick L3 over L2; newest used_at within layer")

    assert parse_feedback_response("y") == "positive"
    assert parse_feedback_response("不对") == "negative"
    assert parse_feedback_response("") == "skip"
    print("[PASS] T-602b: parse_feedback_response y/n/skip")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "evolve").mkdir()
        (root / "evolve" / "_index.toml").write_text("[[topic]]\nid = \"workflow\"\n", encoding="utf-8")
        (root / "workspace").mkdir()
        (root / "data").mkdir()
        paths = AgentPaths.from_root(root)
        log_path = paths.data / "evolve_log.jsonl"

        session = create_new(paths, conversation_id="_t602b")
        session.meta.pending_feedback = [
            {
                "entity_id": "downloads-sort",
                "type": "memory",
                "level": "L2",
                "used_at": utc_now_iso(),
            }
        ]
        session.save()

        os.environ[_ENV_FEEDBACK_ON_EXIT] = "1"
        outputs: list[str] = []
        verdict = maybe_run_exit_feedback(
            session,
            paths=paths,
            input_fn=lambda _p: "n",
            output_fn=outputs.append,
            evolve_log=EvolveLog(log_path),
        )
        assert verdict == "negative"
        assert any("downloads-sort" in line for line in outputs)
        events = read_events(log_path)
        assert any(event.get("event") == EVENT_FEEDBACK_NEGATIVE for event in events)
        session = Session.load(paths, "_t602b")
        assert session.meta.pending_feedback == []
        print("[PASS] T-602b: exit feedback negative logs + clears pending_feedback")

        session2 = create_new(paths, conversation_id="_t602b_skip")
        session2.meta.pending_feedback = [
            {
                "entity_id": "project-my-agent",
                "type": "memory",
                "level": "L2",
                "used_at": utc_now_iso(),
            }
        ]
        session2.save()
        log2 = EvolveLog(log_path)
        verdict_skip = maybe_run_exit_feedback(
            session2,
            paths=paths,
            input_fn=lambda _p: "",
            output_fn=lambda _text: None,
            evolve_log=log2,
        )
        assert verdict_skip == "skip"
        reloaded = Session.load(paths, "_t602b_skip")
        assert len(reloaded.meta.pending_feedback) == 1
        assert not any(
            event.get("event") in {EVENT_FEEDBACK_POSITIVE, EVENT_FEEDBACK_NEGATIVE}
            and event.get("conversation_id") == "_t602b_skip"
            for event in read_events(log_path)
        )
        print("[PASS] T-602b: skip leaves pending_feedback and writes no feedback event")

        os.environ.pop(_ENV_FEEDBACK_ON_EXIT, None)
        session3 = create_new(paths, conversation_id="_t602b_off")
        session3.meta.pending_feedback = [
            {"entity_id": "x", "type": "memory", "level": "L2", "used_at": utc_now_iso()}
        ]
        session3.save()
        assert maybe_run_exit_feedback(
            session3,
            paths=paths,
            input_fn=lambda _p: "y",
            output_fn=lambda _text: None,
            evolve_log=EvolveLog(log_path),
        ) is None
        print("[PASS] T-602b: feedback disabled when env unset")

        mem_t602c = root / "evolve" / "memories" / "workflow" / "t602c-int.md"
        mem_t602c.parent.mkdir(parents=True, exist_ok=True)
        mem_t602c.write_text(
            "---\n"
            "id: t602c-int\n"
            "topics: [workflow]\n"
            "status: active\n"
            "summary: T-602c integration memory\n"
            "---\n\n",
            encoding="utf-8",
        )
        entry_t602c = {
            "entity_id": "t602c-int",
            "type": "memory",
            "level": "L2",
            "used_at": utc_now_iso(),
        }
        log_t602c = EvolveLog(log_path)
        for cid in ("_t602c_a", "_t602c_b", "_t602c_c"):
            session_neg = create_new(paths, conversation_id=cid)
            apply_exit_feedback(
                session_neg,
                entry=entry_t602c,
                verdict="negative",
                evolve_log=log_t602c,
            )
        assert "status: suspect" in mem_t602c.read_text(encoding="utf-8")
        marked_t602c = [
            event
            for event in read_events(log_path)
            if event.get("event") == EVENT_MARKED_SUSPECT and event.get("entity_id") == "t602c-int"
        ]
        assert len(marked_t602c) == 1 and marked_t602c[0]["failure_streak"] == 3
        print("[PASS] T-602c: apply_exit_feedback x3 negative → suspect + marked_suspect")


if __name__ == "__main__":
    _demo()
