from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import server
from day_journal import DayJournal
from file_stage import FileStageStore
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore


def _seed_session_boundary(journal, at, session_id="synthetic-session", project_id="synthetic-project"):
    return journal.record_segment(
        at, project_id, None, segment_id="boundary:" + session_id,
        source={"kind": "session", "session_id": session_id},
    )


def _synthetic_session(tmp_path):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    for directory in (paths.workspace, paths.data, paths.evolve, paths.data / "sessions" / "synthetic-session"):
        directory.mkdir(parents=True, exist_ok=True)
    session = Session("synthetic-session", paths.data / "sessions" / "synthetic-session", "synthetic",
                      SessionMeta(project_id="synthetic-project"), [], paths)
    return paths, session


def _bare_handler(paths, journal):
    """Initialize the fields __init__ normally provides to _dispatch fixtures."""
    handler = server.WsSessionHandler.__new__(server.WsSessionHandler)
    handler.paths = paths
    handler.managed_readonly_context = None
    handler._file_stage = FileStageStore()
    handler.project_boundary_journal = journal
    return handler


class _Bridge:
    def __init__(self, choices=("y", "y")):
        self.lifecycle = SimpleNamespace(snapshot=lambda: None)
        self._turn_busy = threading.Event()
        self.cancel_event = threading.Event()
        self.choices = list(choices)
        self.previews = []
        self.responses = []
        self.events = []

    def begin_turn(self, *, mode): return True
    def confirm_with_receipt(self, preview, allow_approve_all):
        self.previews.append(preview)
        return self.choices.pop(0), "synthetic-receipt"
    def emit_assistant(self, text): self.responses.append(text)
    def end_turn(self): pass
    def finish_execution(self, *args, **kwargs): return True
    def emit_turn_event(self, event): self.events.append(event)


class _FakeClient:
    def __init__(self, paths):
        self.paths = paths
        self.calls = 0
    def chat(self, messages, **kwargs):
        self.calls += 1
        request = json.loads(messages[-1]["content"])
        source_id = request["sources"][0]["source_id"]
        return json.dumps({"summary": "合成：记录了一项需求", "source_ids": [source_id], "insufficient": False})


def test_explicit_summary_uses_one_synthetic_source_two_confirmations_and_unverified_save(tmp_path, monkeypatch):
    paths, session = _synthetic_session(tmp_path)
    now = datetime.now(timezone(timedelta(hours=8), "Asia/Shanghai"))
    journal = DayJournal(tmp_path / "journal")
    journal.save_day(now, None, ["synthetic-project"])
    _seed_session_boundary(journal, now)
    store = WorkRecordStore(session)
    item = store.create_item(title="合成事项", scope={"kind": "project", "project_id": "synthetic-project"})
    store.add_requirement(item["item_id"], "合成要求，不代表完成", event_id="synthetic-requirement-event")
    bridge = _Bridge()
    fake_client = _FakeClient(paths)
    entry = SimpleNamespace(id="0x567-flash", vendor="0x567", provider_model="gpt-6-luna", max_input_tokens=32000)
    monkeypatch.setattr("day_journal_summary.get_registry", lambda *args: SimpleNamespace(get=lambda model_id: entry))
    monkeypatch.setattr("llm_client.LLMClient", lambda paths: SimpleNamespace(
        paths=paths, set_cancel_event=lambda event: None, chat=fake_client.chat
    ))

    handler = _bare_handler(paths, journal)
    repl = SimpleNamespace(session=session)
    import asyncio
    asyncio.run(handler._dispatch({"type": "user.message", "text": "总结今天的项目日记"}, repl, bridge))

    assert fake_client.calls == 1
    assert len(bridge.previews) == 2
    assert "0x567-flash" in bridge.previews[0]
    assert "synthetic-requirement-event" in bridge.previews[0]
    saved = journal.read_day(now.date().isoformat())
    assert saved["revision"] == 3
    segment = next(row for row in saved["segments"] if row.get("summary_metadata", {}).get("kind") == "model_summary_candidate")
    assert segment["summary"] == "合成：记录了一项需求"
    assert segment["summary_metadata"]["verified"] is False
    assert "未经核实" in bridge.responses[0]
    assert "记录时间不代表实际发生时间" in bridge.responses[0]
    assert "记录了需求" in bridge.responses[0]


def test_missing_existing_day_item_never_constructs_or_calls_summary_service(tmp_path):
    paths, session = _synthetic_session(tmp_path)
    bridge = _Bridge()
    handler = _bare_handler(paths, None)
    handler.day_journal_summary_service_factory = lambda **kwargs: pytest.fail("must not call model without existing day entry")
    import asyncio
    asyncio.run(handler._dispatch({"type": "user.message", "text": "总结今天的项目日记"},
                                  SimpleNamespace(session=session), bridge))
    assert "未调用模型" in bridge.responses[0]
    assert not bridge.previews



def test_multiple_current_session_sources_are_eligible_as_distinct_requirements(tmp_path, monkeypatch):
    paths, session = _synthetic_session(tmp_path)
    now = datetime.now(timezone(timedelta(hours=8), "Asia/Shanghai"))
    journal = DayJournal(tmp_path / "journal")
    journal.save_day(now, None, ["synthetic-project"])
    _seed_session_boundary(journal, now)
    store = WorkRecordStore(session)
    ids = []
    for idx in range(2):
        item = store.create_item(title=f"item-{idx}", scope={"kind": "project", "project_id": "synthetic-project"})
        store.add_requirement(item["item_id"], f"req-{idx}", event_id=f"event-{idx}")
        ids.append(f"work-record:{item['item_id']}:event-{idx}")
    bridge = _Bridge()
    fake = _FakeClient(paths)
    def respond(messages, **kwargs):
        fake.calls += 1
        supplied = json.loads(messages[-1]["content"])["sources"]
        selected_ids = [source["source_id"] for source in supplied]
        return json.dumps({"summary": "multi", "source_ids": selected_ids, "insufficient": False})
    fake.chat = respond
    entry = SimpleNamespace(id="0x567-flash", vendor="0x567", provider_model="gpt-6-luna", max_input_tokens=32000)
    monkeypatch.setattr("day_journal_summary.get_registry", lambda *args: SimpleNamespace(get=lambda model_id: entry))
    monkeypatch.setattr("llm_client.LLMClient", lambda paths: SimpleNamespace(paths=paths, set_cancel_event=lambda event: None, chat=fake.chat))
    handler = _bare_handler(paths, journal)
    import asyncio
    asyncio.run(handler._dispatch({"type": "user.message", "text": "\u603b\u7ed3\u4eca\u5929\u7684\u9879\u76ee\u65e5\u8bb0"}, SimpleNamespace(session=session), bridge))
    assert fake.calls == 1 and len(bridge.previews) == 2
    assert all(source_id in bridge.previews[0] for source_id in ids)


def test_refuses_persistence_when_second_confirmation_is_declined(tmp_path, monkeypatch):
    paths, session = _synthetic_session(tmp_path)
    now = datetime.now(timezone(timedelta(hours=8), "Asia/Shanghai"))
    journal = DayJournal(tmp_path / "journal")
    journal.save_day(now, None, ["synthetic-project"])
    _seed_session_boundary(journal, now)
    original = journal.read_day(now.date().isoformat())
    store = WorkRecordStore(session)
    item = store.create_item(title="合成事项", scope={"kind": "project", "project_id": "synthetic-project"})
    store.add_requirement(item["item_id"], "合成要求", event_id="synthetic-requirement-event")
    bridge = _Bridge(choices=("y", "n"))
    fake_client = _FakeClient(paths)
    entry = SimpleNamespace(id="0x567-flash", vendor="0x567", provider_model="gpt-6-luna", max_input_tokens=32000)
    monkeypatch.setattr("day_journal_summary.get_registry", lambda *args: SimpleNamespace(get=lambda model_id: entry))
    monkeypatch.setattr("llm_client.LLMClient", lambda paths: SimpleNamespace(
        paths=paths, set_cancel_event=lambda event: None, chat=fake_client.chat
    ))
    handler = _bare_handler(paths, journal)
    import asyncio
    asyncio.run(handler._dispatch({"type": "user.message", "text": "总结今天的项目日记"},
                                  SimpleNamespace(session=session), bridge))
    assert fake_client.calls == 1
    assert len(bridge.previews) == 2
    assert journal.read_day(now.date().isoformat()) == original
    assert "未批准" in bridge.responses[0]
    assert "未写入日记" in bridge.responses[0]


def test_summary_trigger_requires_explicit_wording():
    assert server._is_day_journal_summary_request("总结今天的项目日记")
    assert server._is_day_journal_summary_request("  总结今天项目日记。 ")
    assert not server._is_day_journal_summary_request("总结一下今天做了什么")
    assert not server._is_day_journal_summary_request("帮我总结项目日记")



def test_same_project_boundary_from_another_session_is_rejected(tmp_path):
    paths, session = _synthetic_session(tmp_path)
    now = datetime.now(timezone(timedelta(hours=8), "Asia/Shanghai"))
    journal = DayJournal(tmp_path / "journal")
    journal.save_day(now, None, ["synthetic-project"])
    _seed_session_boundary(journal, now, session_id="another-session")
    store = WorkRecordStore(session)
    item = store.create_item(title="当前会话事项", scope={"kind": "project", "project_id": "synthetic-project"})
    store.add_requirement(item["item_id"], "当前会话合成要求", event_id="current-session-event")
    bridge = _Bridge()
    handler = _bare_handler(paths, journal)
    handler.day_journal_summary_service_factory = lambda **kwargs: pytest.fail(
        "same-project boundary from another session must not be externalized"
    )
    import asyncio
    asyncio.run(handler._dispatch({"type": "user.message", "text": "总结今天的项目日记"},
                                  SimpleNamespace(session=session), bridge))
    assert "明确边界记录" in bridge.responses[0]
    assert not bridge.previews


def test_multiple_current_session_sources_are_summarized_with_exact_ordered_preview(tmp_path, monkeypatch):
    paths, session = _synthetic_session(tmp_path)
    now = datetime.now(timezone(timedelta(hours=8), "Asia/Shanghai"))
    journal = DayJournal(tmp_path / "journal")
    journal.save_day(now, None, ["synthetic-project"])
    _seed_session_boundary(journal, now)
    store = WorkRecordStore(session)
    source_ids = []
    for idx, text in enumerate(("同一事项的要求一", "同一事项的要求二", "另一事项的要求三")):
        item = store.create_item(title=f"合成事项{idx // 2}", scope={"kind": "project", "project_id": "synthetic-project"})
        store.add_requirement(item["item_id"], text, event_id=f"synthetic-event-{idx}")
        source_ids.append(f"work-record:{item['item_id']}:synthetic-event-{idx}")
    bridge = _Bridge()
    fake_client = _FakeClient(paths)
    def multi_source_response(messages, **kwargs):
        fake_client.calls += 1
        request = json.loads(messages[-1]["content"])
        assert [source["source_id"] for source in request["sources"]] == sorted(source_ids)
        return json.dumps({"summary": "合成多来源摘要", "source_ids": source_ids, "insufficient": False})
    fake_client.chat = multi_source_response
    entry = SimpleNamespace(id="0x567-flash", vendor="0x567", provider_model="gpt-6-luna", max_input_tokens=32000)
    monkeypatch.setattr("day_journal_summary.get_registry", lambda *args: SimpleNamespace(get=lambda model_id: entry))
    monkeypatch.setattr("llm_client.LLMClient", lambda paths: SimpleNamespace(
        paths=paths, set_cancel_event=lambda event: None, chat=fake_client.chat
    ))
    handler = _bare_handler(paths, journal)
    import asyncio
    asyncio.run(handler._dispatch({"type": "user.message", "text": "总结今天的项目日记"},
                                  SimpleNamespace(session=session), bridge))
    assert fake_client.calls == 1
    assert len(bridge.previews) == 2
    assert all(source_id in bridge.previews[0] for source_id in source_ids)
    saved = journal.read_day(now.date().isoformat())
    segment = next(row for row in saved["segments"] if row.get("summary_metadata", {}).get("kind") == "model_summary_candidate")
    assert set(segment["summary_metadata"]["source_ids"]) == set(source_ids)
    assert "\u8bb0\u5f55\u4e86\u9700\u6c42" in bridge.responses[0]
    assert "\u4e0d\u4ee3\u8868\u9700\u6c42\u5df2\u5b8c\u6210\u6216\u9a8c\u8bc1" in bridge.responses[0]


def test_more_than_twenty_current_day_sources_fail_closed(tmp_path):
    paths, session = _synthetic_session(tmp_path)
    now = datetime.now(timezone(timedelta(hours=8), "Asia/Shanghai"))
    journal = DayJournal(tmp_path / "journal")
    journal.save_day(now, None, ["synthetic-project"])
    _seed_session_boundary(journal, now)
    store = WorkRecordStore(session)
    item = store.create_item(title="bounded", scope={"kind": "project", "project_id": "synthetic-project"})
    for idx in range(21):
        store.add_requirement(item["item_id"], f"req-{idx}", event_id=f"over-limit-{idx}")
    bridge = _Bridge()
    handler = _bare_handler(paths, journal)
    handler.day_journal_summary_service_factory = lambda **kwargs: pytest.fail("must reject over-limit candidates before provider")
    import asyncio
    asyncio.run(handler._dispatch({"type": "user.message", "text": "\u603b\u7ed3\u4eca\u5929\u7684\u9879\u76ee\u65e5\u8bb0"}, SimpleNamespace(session=session), bridge))
    assert "20" in bridge.responses[0]
    assert not bridge.previews


def test_other_project_requirement_is_not_a_current_project_source(tmp_path):
    paths, session = _synthetic_session(tmp_path)
    now = datetime.now(timezone(timedelta(hours=8), "Asia/Shanghai"))
    journal = DayJournal(tmp_path / "journal")
    journal.save_day(now, None, ["synthetic-project", "other-project"])
    _seed_session_boundary(journal, now)
    journal.record_segment(now, "other-project", None, segment_id="boundary-other-project", source={"kind": "session", "session_id": "synthetic-session"})
    bridge = _Bridge()
    handler = _bare_handler(paths, journal)
    handler.day_journal_summary_service_factory = lambda **kwargs: pytest.fail("other project source must not be selected")
    import asyncio
    asyncio.run(handler._dispatch({"type": "user.message", "text": "\u603b\u7ed3\u4eca\u5929\u7684\u9879\u76ee\u65e5\u8bb0"}, SimpleNamespace(session=session), bridge))
    assert not bridge.previews
    assert "\u672a\u8c03\u7528\u6a21\u578b" in bridge.responses[0]


def test_new_source_during_outbound_confirmation_invalidates_snapshot(tmp_path, monkeypatch):
    paths, session = _synthetic_session(tmp_path)
    now = datetime.now(timezone(timedelta(hours=8), "Asia/Shanghai"))
    journal = DayJournal(tmp_path / "journal")
    journal.save_day(now, None, ["synthetic-project"])
    _seed_session_boundary(journal, now)
    store = WorkRecordStore(session)
    item = store.create_item(title="stable-item", scope={"kind": "project", "project_id": "synthetic-project"})
    store.add_requirement(item["item_id"], "initial", event_id="initial-event")
    original = journal.read_day(now.date().isoformat())
    bridge = _Bridge()
    add_during_confirm = bridge.confirm_with_receipt
    changed = {"done": False}
    def confirm_then_change(preview, allow_approve_all):
        result = add_during_confirm(preview, allow_approve_all)
        if not changed["done"]:
            changed["done"] = True
            store.add_requirement(item["item_id"], "concurrent", event_id="concurrent-event")
        return result
    bridge.confirm_with_receipt = confirm_then_change
    fake = _FakeClient(paths)
    entry = SimpleNamespace(id="0x567-flash", vendor="0x567", provider_model="gpt-6-luna", max_input_tokens=32000)
    monkeypatch.setattr("day_journal_summary.get_registry", lambda *args: SimpleNamespace(get=lambda model_id: entry))
    monkeypatch.setattr("llm_client.LLMClient", lambda paths: SimpleNamespace(paths=paths, set_cancel_event=lambda event: None, chat=fake.chat))
    handler = _bare_handler(paths, journal)
    import asyncio
    asyncio.run(handler._dispatch({"type": "user.message", "text": "\u603b\u7ed3\u4eca\u5929\u7684\u9879\u76ee\u65e5\u8bb0"}, SimpleNamespace(session=session), bridge))
    assert fake.calls == 0
    assert len(bridge.previews) == 1
    assert journal.read_day(now.date().isoformat()) == original


def test_aggregate_source_text_over_6000_is_rejected_before_outbound_confirmation(tmp_path):
    paths, session = _synthetic_session(tmp_path)
    now = datetime.now(timezone(timedelta(hours=8), "Asia/Shanghai"))
    journal = DayJournal(tmp_path / "journal")
    journal.save_day(now, None, ["synthetic-project"])
    _seed_session_boundary(journal, now)
    store = WorkRecordStore(session)
    for idx in range(2):
        item = store.create_item(title=f"long-{idx}", scope={"kind": "project", "project_id": "synthetic-project"})
        store.add_requirement(item["item_id"], "x" * 3000, event_id=f"long-event-{idx}")
    bridge = _Bridge()
    handler = _bare_handler(paths, journal)
    handler.day_journal_summary_service_factory = lambda **kwargs: pytest.fail("aggregate source limit must be checked before service/confirmation")
    import asyncio
    asyncio.run(handler._dispatch({"type": "user.message", "text": "\u603b\u7ed3\u4eca\u5929\u7684\u9879\u76ee\u65e5\u8bb0"}, SimpleNamespace(session=session), bridge))
    assert not bridge.previews
    assert "6000" in bridge.responses[0]
    assert "\u672a\u5916\u53d1" in bridge.responses[0]
    assert "\u672a\u622a\u65ad" in bridge.responses[0]


def test_oversized_generated_summary_reports_model_call_and_no_save(tmp_path):
    paths, session = _synthetic_session(tmp_path)
    now = datetime.now(timezone(timedelta(hours=8), "Asia/Shanghai"))
    journal = DayJournal(tmp_path / "journal")
    journal.save_day(now, None, ["synthetic-project"])
    _seed_session_boundary(journal, now)
    store = WorkRecordStore(session)
    item = store.create_item(title="short", scope={"kind": "project", "project_id": "synthetic-project"})
    store.add_requirement(item["item_id"], "short request", event_id="short-event")
    bridge = _Bridge()
    call = {"count": 0}
    class OversizedService:
        def generate(self, *, sources, context_token):
            call["count"] += 1
            return {"status": "candidate", "summary": "s" * 6001}
    handler = _bare_handler(paths, journal)
    handler.day_journal_summary_service_factory = lambda **kwargs: OversizedService()
    import asyncio
    asyncio.run(handler._dispatch({"type": "user.message", "text": "\u603b\u7ed3\u4eca\u5929\u7684\u9879\u76ee\u65e5\u8bb0"}, SimpleNamespace(session=session), bridge))
    assert call["count"] == 1
    assert "\u6a21\u578b\u8c03\u7528\u5df2\u53d1\u751f" in bridge.responses[0]
    assert "\u6458\u8981\u65e0\u6548" in bridge.responses[0]
    assert "\u672a\u4fdd\u5b58" in bridge.responses[0]
    assert "\u672a\u5916\u53d1" not in bridge.responses[0]
    assert journal.read_day(now.date().isoformat())["revision"] == 2
