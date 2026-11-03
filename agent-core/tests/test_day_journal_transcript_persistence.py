from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import server
from day_journal import DayJournal
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore


class _Bridge:
    def __init__(self, choices=()):
        self.lifecycle = SimpleNamespace(snapshot=lambda: None)
        self._turn_busy = threading.Event()
        self.cancel_event = threading.Event()
        self.choices = list(choices)
        self.responses = []
        self.events = []
        self.previews = []
        self.session = None
        self.require_persisted_before_display = False

    def begin_turn(self, *, mode):
        self.events.append({"type": "turn.start", "mode": mode})
        return True

    def confirm_with_receipt(self, preview, _allow_approve_all):
        self.previews.append(preview)
        return self.choices.pop(0), "fixture-receipt"

    def emit_assistant(self, text):
        if self.require_persisted_before_display:
            rows = _rows(self.session)
            assert rows and rows[-1] == {"role": "assistant", "content": text}
        self.responses.append(text)

    def emit_turn_event(self, event):
        self.events.append(event)

    def end_turn(self):
        self.events.append({"type": "turn.closed"})

    def finish_execution(self, reason, *, ok):
        self.events.append({"type": "execution.finished", "reason": reason, "ok": ok})
        return True


def _session(tmp_path: Path, project_id="journal-project"):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    for path in (paths.workspace, paths.data, paths.evolve, paths.data / "sessions" / "journal-session"):
        path.mkdir(parents=True, exist_ok=True)
    session = Session(
        "journal-session", paths.data / "sessions" / "journal-session", "fixture",
        SessionMeta(project_id=project_id), [], paths,
    )
    return paths, session


def _rows(session: Session):
    path = session.messages_path
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _handler(paths, journal=None):
    handler = server.WsSessionHandler.__new__(server.WsSessionHandler)
    handler.paths = paths
    handler.project_boundary_journal = journal
    return handler


def _seed_summary(paths, session, tmp_path, monkeypatch):
    now = datetime.now(timezone(timedelta(hours=8), "Asia/Shanghai"))
    journal = DayJournal(tmp_path / "journal")
    journal.save_day(now, None, [session.meta.project_id])
    journal.record_segment(
        now, session.meta.project_id, None, segment_id="boundary:" + session.conversation_id,
        source={"kind": "session", "session_id": session.conversation_id},
    )
    records = WorkRecordStore(session)
    item = records.create_item(title="fixture requirement", scope={
        "kind": "project", "project_id": session.meta.project_id,
    })
    records.add_requirement(item["item_id"], "synthetic requirement", event_id="fixture-event")

    class Client:
        def set_cancel_event(self, _event):
            pass

        def chat(self, messages, **_kwargs):
            request = json.loads(messages[-1]["content"])
            source_id = request["sources"][0]["source_id"]
            return json.dumps({"summary": "synthetic summary", "source_ids": [source_id], "insufficient": False})

    entry = SimpleNamespace(id="0x567-flash", vendor="0x567", provider_model="gpt-6-luna", max_input_tokens=32000)
    monkeypatch.setattr("day_journal_summary.get_registry", lambda *_args: SimpleNamespace(get=lambda _model: entry))
    monkeypatch.setattr("llm_client.LLMClient", lambda paths: Client())
    return journal, now


def test_summary_records_question_before_processing_and_assistant_before_display(tmp_path, monkeypatch):
    paths, session = _session(tmp_path)
    journal, _now = _seed_summary(paths, session, tmp_path, monkeypatch)
    original_read = journal.read_day
    question = "总结今天的项目日记"

    def read_after_question(day):
        assert _rows(session) == [{"role": "user", "content": question}]
        return original_read(day)

    monkeypatch.setattr(journal, "read_day", read_after_question)
    bridge = _Bridge(choices=("y", "y"))
    bridge.session = session
    bridge.require_persisted_before_display = True
    repl = SimpleNamespace(session=session)

    asyncio.run(_handler(paths, journal)._handle_day_journal_summary(question, repl, bridge))

    rows = _rows(session)
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert rows[0]["content"] == question
    assert "synthetic summary" in rows[1]["content"]
    assert bridge.responses == [rows[1]["content"]]
    assert any(event.get("type") == "turn.end" and event.get("ok") is True for event in bridge.events)


def test_summary_processing_exception_records_question_and_safe_answer(tmp_path, monkeypatch):
    paths, session = _session(tmp_path)
    journal = DayJournal(tmp_path / "journal")
    journal.read_day = lambda _day: (_ for _ in ()).throw(RuntimeError("private fixture exception"))
    bridge = _Bridge()
    bridge.session = session
    bridge.require_persisted_before_display = True
    question = "总结今天的项目日记"

    asyncio.run(_handler(paths, journal)._handle_day_journal_summary(
        question, SimpleNamespace(session=session), bridge,
    ))

    rows = _rows(session)
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert rows[0]["content"] == question
    assert "未能安全处理" in rows[1]["content"]
    assert "private fixture exception" not in rows[1]["content"]
    assert bridge.responses == [rows[1]["content"]]


def test_question_append_failure_skips_summary_processing_and_reports_record_failure(tmp_path):
    paths, session = _session(tmp_path)
    journal = DayJournal(tmp_path / "journal")
    journal.read_day = lambda _day: pytest.fail("must not process when the question cannot be recorded")
    original_append = session._append_message_line

    def fail_user_append(message, **kwargs):
        if message.get("role") == "user":
            raise OSError("private question storage failure")
        return original_append(message, **kwargs)

    session._append_message_line = fail_user_append
    bridge = _Bridge()
    bridge.session = session
    handler = _handler(paths, journal)

    asyncio.run(handler._handle_day_journal_summary(
        "summary input", SimpleNamespace(session=session), bridge,
    ))

    assert _rows(session) == []
    assert len(bridge.responses) == 1
    assert "写入失败" in bridge.responses[0]
    assert "未执行" in bridge.responses[0]
    assert "private question storage failure" not in bridge.responses[0]


def test_recall_records_question_before_query_and_answer_before_display(tmp_path):
    paths, session = _session(tmp_path)
    bridge = _Bridge()
    bridge.session = session
    bridge.require_persisted_before_display = True
    question = "查2026-10-29的项目日记"
    handler = _handler(paths)

    def query(_root, **kwargs):
        assert kwargs["authorized"] is True
        assert _rows(session) == [{"role": "user", "content": question}]
        return {"status": "ok", "message": "synthetic local journal result", "records": [], "issues": []}

    handler.day_journal_query = query
    asyncio.run(handler._handle_day_journal_recall(
        question, "2026-10-29", SimpleNamespace(session=session), bridge,
    ))

    rows = _rows(session)
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert rows[0]["content"] == question
    assert "synthetic local journal result" in rows[1]["content"]
    assert bridge.responses == [rows[1]["content"]]


def test_recall_processing_exception_records_question_and_safe_answer(tmp_path):
    paths, session = _session(tmp_path)
    bridge = _Bridge()
    bridge.session = session
    bridge.require_persisted_before_display = True
    handler = _handler(paths)

    def fail_query(*_args, **_kwargs):
        raise RuntimeError("private query processing exception")

    handler.day_journal_query = fail_query
    question = "recall input"
    asyncio.run(handler._handle_day_journal_recall(
        question, "2026-10-29", SimpleNamespace(session=session), bridge,
    ))

    rows = _rows(session)
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert rows[0]["content"] == question
    assert "未能安全完成" in rows[1]["content"]
    assert "private query processing exception" not in rows[1]["content"]
    assert bridge.responses == [rows[1]["content"]]


def test_recall_assistant_append_failure_does_not_display_unrecorded_result(tmp_path):
    paths, session = _session(tmp_path)
    original_append = session._append_message_line

    def fail_assistant_append(message, **kwargs):
        if message.get("role") == "assistant":
            raise OSError("private assistant storage failure")
        return original_append(message, **kwargs)

    session._append_message_line = fail_assistant_append
    bridge = _Bridge()
    bridge.session = session
    handler = _handler(paths)
    handler.day_journal_query = lambda *_args, **_kwargs: {
        "status": "ok", "message": "sensitive synthetic result", "records": [], "issues": [],
    }

    asyncio.run(handler._handle_day_journal_recall(
        "查2026-10-29的项目日记", "2026-10-29", SimpleNamespace(session=session), bridge,
    ))

    assert _rows(session) == [{"role": "user", "content": "查2026-10-29的项目日记"}]
    assert len(bridge.responses) == 1
    assert "记录写入失败" in bridge.responses[0]
    assert "sensitive synthetic result" not in bridge.responses[0]
    assert "private assistant storage failure" not in bridge.responses[0]


def test_recall_cancellation_records_safe_notice_and_keeps_turn_lifecycle(tmp_path):
    paths, session = _session(tmp_path)
    bridge = _Bridge()
    bridge.session = session
    bridge.require_persisted_before_display = True
    handler = _handler(paths)

    def cancel_after_query(*_args, **_kwargs):
        bridge.cancel_event.set()
        return {"status": "ok", "message": "must not surface after cancellation", "records": [], "issues": []}

    handler.day_journal_query = cancel_after_query
    question = "查2026-10-29的项目日记"
    asyncio.run(handler._handle_day_journal_recall(
        question, "2026-10-29", SimpleNamespace(session=session), bridge,
    ))

    rows = _rows(session)
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert rows[0]["content"] == question
    assert "已取消" in rows[1]["content"]
    assert "must not surface after cancellation" not in rows[1]["content"]
    assert bridge.responses == [rows[1]["content"]]
    assert any(event.get("type") == "turn.end" and event.get("finish_reason") == "cancelled"
               for event in bridge.events)
