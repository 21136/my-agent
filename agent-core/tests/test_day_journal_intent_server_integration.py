from __future__ import annotations

import asyncio
import sys
import threading
from datetime import date
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from server import WsSessionHandler


class FakeSession:
    conversation_id = "synthetic-conversation"
    meta = type("Meta", (), {"llm_model": "unused"})()

    def __init__(self):
        self.messages = []
        self.saved = False

    def append_message(self, message):
        self.messages.append(message)

    def save(self):
        self.saved = True


class FakeBridge:
    def __init__(self, answer="y"):
        self.cancel_event = threading.Event()
        self.lifecycle = type("Lifecycle", (), {"snapshot": lambda _self: None})()
        self._turn_busy = threading.Event()
        self.events = []
        self.answer = answer
        self.context_switch = False

    def try_route_input(self, _text):
        return False

    def begin_turn(self, *, mode):
        self.events.append({"type": "turn.start", "mode": mode})
        return True

    def confirm_with_receipt(self, preview, allow_approve_all=False):
        self.events.append({"type": "confirm.request", "preview": preview})
        return self.answer, "synthetic-confirmation"

    def emit_assistant(self, text):
        self.events.append({"type": "assistant.done", "text": text})

    def emit(self, event):
        self.events.append(event)

    def end_turn(self):
        pass

    def finish_execution(self, *_args, **_kwargs):
        return True

    def emit_turn_event(self, event):
        self.events.append(event)


class FakeIntentService:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self.confirm = None

    def classify(self, **kwargs):
        self.calls.append(kwargs)
        preview = {"registry_id": "0x567-flash", "vendor": "0x567",
                   "provider_model": "gpt-6-luna",
                   "messages": [{"role": "system", "content": "synthetic classifier"},
                                {"role": "user", "content": kwargs["text"]}],
                   "tools": [], "temperature": 0.0,
                   "response_format": {"type": "json_object"}}
        if not self.confirm(preview):
            return {"action": "uncertain", "start_date": None, "end_date": None}
        return self.result


def make_handler(tmp_path, service, query_result):
    from day_journal import DayJournal

    session = FakeSession()
    repl = type("Repl", (), {"session": session})()
    bridge = FakeBridge()
    handler = WsSessionHandler.__new__(WsSessionHandler)
    handler.paths = type("Paths", (), {"data": tmp_path / "data", "_fixed_code_root": None})()
    def service_factory(**kwargs):
        service.confirm = kwargs["confirm"]
        return service
    handler.day_journal_intent_service_factory = service_factory
    handler.query_calls = []
    def local_query(*args, **kwargs):
        handler.query_calls.append((args, kwargs))
        return query_result
    handler.day_journal_query = local_query
    handler.project_boundary_journal = DayJournal(tmp_path / "same-root")
    handler._file_stage = type("FileStage", (), {"take": lambda *_args: []})()
    handler._dispatch_inline = lambda *_args: False
    return handler, repl, bridge, session


def test_unsupported_diary_phrase_confirms_full_payload_then_queries_only_authorized_range(tmp_path):
    result = {"action": "recall", "start_date": "2026-10-28", "end_date": "2026-10-29"}
    service = FakeIntentService(result)
    handler, repl, bridge, session = make_handler(tmp_path, service, {
        "status": "no_record", "records": [], "issues": [],
        "message": "没有可读取记录；不表示当日没有发生相关事件。",
    })

    asyncio.run(handler._dispatch({"type": "user.message", "text": "帮我查查这两天的日记"}, repl, bridge))

    assert len(service.calls) == 1
    preview = next(event["preview"] for event in bridge.events if event["type"] == "confirm.request")
    assert "帮我查查这两天的日记" in str(preview)
    assert "system" in str(preview) and "user" in str(preview)
    assert "0x567-flash / gpt-6-luna" in preview
    assert handler.query_calls[0][1]["start_date"] == "2026-10-28"
    assert handler.query_calls[0][1]["end_date"] == "2026-10-29"
    assert handler.query_calls[0][1]["timezone"] == "Asia/Shanghai"
    assert handler.query_calls[0][0][0] == handler.project_boundary_journal.root
    assert session.saved
    assistant = session.messages[-1]["content"]
    assert "不表示当日没有发生" in assistant
    assert "后续模型对话可能发送" in assistant
    assert any(event.get("type") == "turn.end" for event in bridge.events)


def test_model_summary_recall_discloses_unverified_status_and_only_source_ids():
    from server import _format_day_journal_result

    result = {
        "message": "记录已读取。",
        "records": [{
            "date": "2026-10-29", "projects": ["proj-1"],
            "summary": "合成模型摘要",
            "summary_metadata": {"kind": "model_summary_candidate", "verified": False,
                                 "source_ids": ["source-1"],
                                 "source_snapshot": [{"text": "PRIVATE-SNAPSHOT"}]},
            "segments": [{"project_id": "proj-1", "summary": "分段模型摘要",
                          "summary_metadata": {"kind": "model_summary_candidate", "verified": False,
                                               "source_ids": ["source-2"],
                                               "source_snapshot": [{"text": "PRIVATE-SEGMENT"}]}}],
        }],
        "issues": [],
    }

    rendered = _format_day_journal_result(result)

    assert rendered.count("\u6a21\u578b\u6458\u8981\uff0c\u672a\u7ecf\u6838\u5b9e") == 2
    assert "source-1" in rendered and "source-2" in rendered
    assert "PRIVATE-SNAPSHOT" not in rendered and "PRIVATE-SEGMENT" not in rendered
    assert "source_snapshot" not in rendered


def test_declined_intent_confirmation_never_reads_journal(tmp_path):
    result = {"action": "recall", "start_date": "2026-10-28", "end_date": "2026-10-29"}
    service = FakeIntentService(result)
    handler, repl, bridge, _session = make_handler(tmp_path, service, {"status": "ok"})
    bridge.answer = "n"

    asyncio.run(handler._dispatch({"type": "user.message", "text": "帮我查查这两天的日记"}, repl, bridge))

    assert len(service.calls) == 1
    assert handler.query_calls == []
    assert any(event.get("type") == "assistant.done" and "不会猜测" in event.get("text", "")
               for event in bridge.events)


def test_local_gate_does_not_route_ordinary_chat_to_intent_provider():
    from server import _looks_like_unsupported_day_journal_query

    assert not _looks_like_unsupported_day_journal_query("今天工作很忙，帮我总结一下")
    assert _looks_like_unsupported_day_journal_query("帮我查查这两天的日记")
