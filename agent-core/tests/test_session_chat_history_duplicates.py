from __future__ import annotations

import json
import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from session import Session, SessionMeta, build_session_chat_history, build_session_chat_history_from_path


def source_messages():
    return [
        {"role": "user", "content": "[本次会议上下文] internal anchor"},
        {"role": "user", "content": "重复但独立的用户消息"},
        {"role": "user", "content": "重复但独立的用户消息"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1"}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "tool-only output"},
        {"role": "user", "content": "[内核] internal marker"},
        {"role": "assistant", "content": "正常答复"},
    ]


def expected_history():
    return [
        {"role": "user", "text": "重复但独立的用户消息"},
        {"role": "user", "text": "重复但独立的用户消息"},
        {"role": "assistant", "text": "正常答复"},
    ]


def test_in_memory_chat_history_keeps_consecutive_identical_user_turns_and_existing_filters(tmp_path):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    session = Session("synthetic-session", tmp_path, "", SessionMeta(), source_messages(), paths)

    items = build_session_chat_history(session)

    assert items == expected_history()
    assert len(items) == len(expected_history())


def test_jsonl_chat_history_keeps_consecutive_identical_user_turns_and_existing_filters(tmp_path):
    messages_path = tmp_path / "messages.jsonl"
    messages_path.write_text(
        "".join(json.dumps(message, ensure_ascii=False) + "\n" for message in source_messages()),
        encoding="utf-8",
    )

    items, total = build_session_chat_history_from_path(messages_path)

    assert items == expected_history()
    assert total == len(expected_history())


