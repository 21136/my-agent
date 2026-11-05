from __future__ import annotations

import asyncio
import sys
import threading
import json
from datetime import datetime
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from server import WsSessionHandler, _format_day_journal_result, _parse_day_journal_request
from day_journal import DayJournal
from paths import AgentPaths
from project_session_store import append_segment_pointer


@pytest.fixture
def shanghai_timezone(tmp_path):
    # Use the same tzdata-missing Windows fallback as the production journal.
    return DayJournal(tmp_path / "timezone-fixture")._zone


def test_explicit_calendar_date_and_today_yesterday_are_supported(shanghai_timezone):
    now = datetime(2026, 10, 29, 0, 10, tzinfo=shanghai_timezone)

    assert _parse_day_journal_request("查2026-10-29的项目日记", now=now) == "2026-10-29"
    assert _parse_day_journal_request("查今天的项目日记", now=now) == "2026-10-29"
    assert _parse_day_journal_request("查昨天项目日记", now=now) == "2026-10-28"


@pytest.mark.parametrize("text", ["查上周的项目日记", "项目日记", "查某天日记"])
def test_ambiguous_or_unsupported_journal_queries_do_not_select_a_date(text, shanghai_timezone):
    assert _parse_day_journal_request(text, now=datetime(2026, 10, 29, tzinfo=shanghai_timezone)) is None


def test_result_text_reports_absence_and_empty_summary_without_claiming_nothing_happened():
    missing = _format_day_journal_result({
        "status": "no_record", "records": [], "issues": [],
        "message": "请求日期范围内没有可读取的日记记录；这不表示当日没有发生相关事件。",
    })
    empty = _format_day_journal_result({
        "status": "summary_empty", "records": [{"date": "2026-10-29", "summary": "", "projects": [], "segments": []}],
        "issues": [], "message": "记录存在，但至少一条摘要为空；不据此补全或推断内容。",
    })

    assert "不表示当日没有发生" in missing
    assert "摘要为空" in empty
    assert "session-" not in empty
    assert "path" not in empty.lower()


def test_result_text_includes_project_store_pointers_without_opening_sessions():
    rendered = _format_day_journal_result({
        "status": "ok",
        "records": [{
            "date": "2026-11-05",
            "summary": "聊了项目",
            "projects": ["proj-a"],
            "segments": [{
                "project_id": "proj-a",
                "segment_id": "boundary:one",
                "summary": "做了什么：接口草案",
                "source": {"kind": "session", "session_id": "sess-one"},
            }],
            "project_store": [{
                "project_id": "proj-a",
                "date": "2026-11-05",
                "segments": [{
                    "segment_id": "boundary:one",
                    "session_id": "sess-one",
                    "occurred_at": "2026-11-05T10:00:00+08:00",
                }],
            }],
        }],
        "issues": [],
        "message": "仅返回本地日记中明确记载的信息。",
    })
    assert "做了什么：接口草案" in rendered
    assert "boundary:one" in rendered
    assert "sess-one" in rendered
    assert "项目按天会话指针" in rendered
    assert "请走记忆" in rendered
    assert "path" not in rendered.lower()


class FakeBridge:
    def __init__(self):
        self.lifecycle = type("Lifecycle", (), {"snapshot": lambda _self: None})()
        self._turn_busy = threading.Event()
        self.events = []
        self.conversation_id = "conversation-test"

    def begin_turn(self, *, mode):
        self.events.append({"type": "turn.start", "mode": mode})
        return True

    def emit_assistant(self, text):
        self.events.append({"type": "assistant.done", "text": text})

    def end_turn(self):
        self.events.append({"type": "turn.closed"})

    def finish_execution(self, reason, *, ok):
        self.events.append({"type": "turn.finished", "reason": reason, "ok": ok})
        return True

    def emit_turn_event(self, event):
        self.events.append(event)


class FakeSession:
    conversation_id = "conversation-test"

    def __init__(self):
        self.messages = []
        self.saved = False

    def append_message(self, message):
        self.messages.append(message)

    def save(self):
        self.saved = True


@pytest.mark.parametrize("query,expected", [("2026-10-29", "日记中明确记载的工作")])
def test_server_recall_runs_as_visible_local_chat_turn_without_model(tmp_path, query, expected):
    root = tmp_path / "enabled-journal"
    root.mkdir(parents=True)
    day = query
    (root / f"{day}.json").write_text(json.dumps({
        "date": day, "summary": expected, "projects": ["proj-1"], "segments": [], "revision": 1,
    }, ensure_ascii=False), encoding="utf-8")
    session = FakeSession()
    repl = type("Repl", (), {"session": session})()
    bridge = FakeBridge()
    handler = WsSessionHandler.__new__(WsSessionHandler)
    handler.paths = type("Paths", (), {"data": tmp_path / "data"})()
    handler.project_boundary_journal = DayJournal(root)

    asyncio.run(handler._handle_day_journal_recall("查2026-10-29的项目日记", day, repl, bridge))

    assert session.saved
    assert [message["role"] for message in session.messages] == ["user", "assistant"]
    assert bridge.events[1]["text"].startswith(
        "本次查询仅本地处理；结果保存在对话中，后续模型对话可能发送该内容。"
    )
    assert expected in bridge.events[1]["text"]
    assert any(event.get("type") == "turn.end" and event["ok"] for event in bridge.events)
    assert not any(event.get("type", "").startswith("tool.") for event in bridge.events)


def test_server_recall_attaches_project_store_pointers(tmp_path):
    root = tmp_path / "enabled-journal"
    root.mkdir(parents=True)
    day = "2026-11-05"
    (root / f"{day}.json").write_text(json.dumps({
        "date": day, "summary": "日摘要", "projects": ["proj-a"], "segments": [], "revision": 1,
    }, ensure_ascii=False), encoding="utf-8")
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    append_segment_pointer(
        paths,
        project_id="proj-a",
        day_key=day,
        segment_id="boundary:one",
        session_id="sess-one",
        occurred_at="2026-11-05T02:00:00+00:00",
    )
    session_dir = paths.data / "sessions" / "sess-one"
    session_dir.mkdir(parents=True)
    (session_dir / "messages.jsonl").write_text(
        json.dumps({"role": "user", "content": "把召回接到项目库"}, ensure_ascii=False) + "\n"
        + json.dumps({"role": "assistant", "content": "已按坐标写入指针"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    session = FakeSession()
    repl = type("Repl", (), {"session": session})()
    bridge = FakeBridge()
    handler = WsSessionHandler.__new__(WsSessionHandler)
    handler.paths = paths
    handler.project_boundary_journal = DayJournal(root)

    asyncio.run(handler._handle_day_journal_recall("查2026-11-05的项目日记", day, repl, bridge))

    text = bridge.events[1]["text"]
    assert "日摘要" in text
    assert "proj-a 2026-11-05 boundary:one → sess-one" in text
    assert "会话原文摘录（sess-one" in text
    assert "已按坐标写入指针" in text
    assert "请走记忆" in text


def test_server_unsupported_date_phrase_does_not_read_or_enumerate(tmp_path):
    session = FakeSession()
    repl = type("Repl", (), {"session": session})()
    bridge = FakeBridge()
    handler = WsSessionHandler.__new__(WsSessionHandler)
    handler.paths = type("Paths", (), {"data": tmp_path / "data"})()

    asyncio.run(handler._handle_day_journal_recall("查上周的项目日记", None, repl, bridge))

    assert not (tmp_path / "data").exists()
    assert "只支持明确查询" in bridge.events[1]["text"]
    assert session.saved


def test_server_reports_corrupt_record_and_permission_denial_without_paths(tmp_path, monkeypatch):
    root = tmp_path / "data" / "day-journal"
    root.mkdir(parents=True)
    day = "2026-10-29"
    (root / f"{day}.json").write_text("{broken", encoding="utf-8")
    session = FakeSession()
    repl = type("Repl", (), {"session": session})()
    bridge = FakeBridge()
    handler = WsSessionHandler.__new__(WsSessionHandler)
    handler.paths = type("Paths", (), {"data": tmp_path / "data"})()

    asyncio.run(handler._handle_day_journal_recall("查2026-10-29的项目日记", day, repl, bridge))
    assert "损坏" in bridge.events[1]["text"]
    assert str(tmp_path) not in bridge.events[1]["text"]

    import day_journal_recall

    def denied(*_args, **_kwargs):
        raise PermissionError("synthetic permission denial")

    monkeypatch.setattr(day_journal_recall, "query_day_journal", denied)
    bridge = FakeBridge()
    asyncio.run(handler._handle_day_journal_recall("查2026-10-29的项目日记", day, repl, bridge))
    assert "权限受限" in bridge.events[1]["text"]
    assert "synthetic permission denial" not in bridge.events[1]["text"]
    assert str(tmp_path) not in bridge.events[1]["text"]
