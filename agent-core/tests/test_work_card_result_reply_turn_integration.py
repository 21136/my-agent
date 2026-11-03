from __future__ import annotations

import asyncio
import sys
import threading
import time
import uuid
import pytest
from pathlib import Path
from typing import Any

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import server
from llm_client import LLMResponse
from paths import AgentPaths
from project_cli import bind_project_session
from project_mode import create_project
from session import Session, SessionMeta
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations
from work_record_cards import build_independent_work_record_card
from work_records import WorkRecordStore


class _OrdinaryReplyLLM:
    def set_cancel_event(self, _event: Any) -> None:
        pass

    def chat(self, *_args: Any, **_kwargs: Any) -> LLMResponse:
        return LLMResponse(model="synthetic", content="普通回答继续。", tool_calls=[],
                           finish_reason="stop", usage=None, raw={})


def _presented_result(tmp_path: Path, *, title="现有工作结果", requirement_text="修复登录超时"):
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace",
                       data=tmp_path / "data", evolve=tmp_path / "evolve")
    paths.workspace.mkdir(parents=True)
    create_project(paths, "p")
    conversation_id = "20261031-f0a10001"
    session_dir = paths.data / "sessions" / conversation_id
    session_dir.mkdir(parents=True)
    session = Session(conversation_id, session_dir, "synthetic", SessionMeta(), [], paths)
    bind_project_session(session, "p", plan_status="draft")
    session.save()
    store = WorkRecordStore(session)
    item = store.create_item(title=title, scope={"kind": "independent"})
    requirement = store.add_requirement(item["item_id"], requirement_text, event_id="reply-view-requirement")
    store.begin_requirement_run(item["item_id"], requirement["requirement_id"], run_id="reply-view-run",
                                expected_current_run_id=None)
    store.record_result_if_current_run(
        item["item_id"], requirement["requirement_id"], run_id="reply-view-run",
        result_id="reply-view-result", status="blocked", summary="仍有一个边界情况待验证。",
        verification={"status": "not_verified", "evidence_refs": []}, sources=[],
    )
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(str(uuid.uuid4()), build_independent_work_record_card(store, item["item_id"]))
    presentation_id = str(uuid.uuid4())
    presentation_run_id = str(uuid.uuid4())
    presentations = WorkCardPresentations(session, store, ledger)
    assert presentations.create(presentation_id, saved.card_id, run_id=presentation_run_id).status == "created"
    session.append_sent_work_card_reference(presentation_id, saved.card_id, run_id=presentation_run_id)
    assert presentations.mark_transport_offer(presentation_id, saved.card_id).status == "offered"
    assert presentations.mark_received(presentation_id, saved.card_id, session.conversation_id,
                                       presentation_run_id).status == "received"
    return paths, session, store, ledger, presentations, saved.card_id, presentation_id, requirement


def test_result_reply_ack_lease_and_cancel_commit_are_linearized(tmp_path, monkeypatch):
    import work_card_presentations as presentation_module
    import work_card_result_reply_recognition as recognition

    paths, session, _store, _ledger, presentations, _card_id, presentation_id, _requirement = _presented_result(tmp_path)
    bridge = server.WsBridge(emit=lambda _event: None, paths=paths)
    repl = server._build_repl(session, paths, bridge)
    repl.agent.llm = _OrdinaryReplyLLM()
    monkeypatch.setattr(recognition, "recognize_result_reply", lambda prepared, *_a, **_k:
                        recognition.ResultReplyRecognition("match", prepared.candidate_ids[0]))

    entered_ack = threading.Event()
    release_ack = threading.Event()
    original_ack = presentation_module.WorkCardPresentations.acknowledge_result
    def blocked_ack(self, *args, **kwargs):
        assert any(bound is session for bound in bridge._active_result_view_leases.values())
        entered_ack.set()
        assert release_ack.wait(3)
        return original_ack(self, *args, **kwargs)
    monkeypatch.setattr(presentation_module.WorkCardPresentations, "acknowledge_result", blocked_ack)

    handler = server.WsSessionHandler(paths)
    turn = threading.Thread(target=lambda: asyncio.run(handler._handle_incoming(
        {"type": "user.message", "text": "解释一下刚才的结果？"}, repl, bridge,
    )))
    turn.start()
    assert entered_ack.wait(3), "the ordinary turn did not reach the result-view write"

    cancel_started = threading.Event()
    cancel_done = threading.Event()
    def cancel_turn():
        cancel_started.set()
        bridge.request_cancel()
        cancel_done.set()
    cancel = threading.Thread(target=cancel_turn)
    cancel.start()

    switch_started = threading.Event()
    switch_done = threading.Event()
    def request_session_switch():
        switch_started.set()
        bridge.begin_session_binding_change()
        released = bridge.wait_for_result_view_leases(3)
        if released:
            bridge.end_session_binding_change()
        switch_done.set()
    switch = threading.Thread(target=request_session_switch)
    switch.start()

    assert cancel_started.wait(1) and switch_started.wait(1)
    time.sleep(0.05)
    assert not cancel_done.is_set(), "cancel must serialize behind the result commit guard"
    assert not switch_done.is_set(), "session rebinding must wait for the held result-view lease"
    release_ack.set()
    turn.join(4)
    cancel.join(4)
    switch.join(4)
    assert not turn.is_alive() and not cancel.is_alive() and not switch.is_alive()
    assert cancel_done.is_set() and switch_done.is_set()
    assert [(view.result_id, view.action) for view in presentations.get(presentation_id).fact.result_views] == [
        ("reply-view-result", "reliable_reply"),
    ]

def test_non_reply_and_explicit_refusal_do_not_start_result_recognition_or_ack(tmp_path, monkeypatch):
    import work_card_result_reply_recognition as recognition

    paths, session, _store, _ledger, presentations, _card_id, presentation_id, _requirement = _presented_result(tmp_path)
    bridge = server.WsBridge(emit=lambda _event: None, paths=paths)
    repl = server._build_repl(session, paths, bridge)
    repl.agent.llm = _OrdinaryReplyLLM()
    recognized = []

    def unexpected_recognition(*_args, **_kwargs):
        recognized.append(1)
        return recognition.ResultReplyRecognition("uncertain")

    monkeypatch.setattr(recognition, "recognize_result_reply", unexpected_recognition)
    handler = server.WsSessionHandler(paths)
    for text in ("今天天气不错", "今天晚餐吃什么？", "取消，不要标记这个结果"):
        asyncio.run(handler._handle_incoming({"type": "user.message", "text": text}, repl, bridge))

    assert recognized == []
    assert presentations.get(presentation_id).fact.result_views == ()
    assert all(any(m.get("role") == "assistant" and m.get("content") == "普通回答继续。"
                   for m in session.messages) for _ in (0,))

def test_normal_turn_reliably_marks_one_exact_displayed_result_without_duplicate_user_append(
    tmp_path, monkeypatch,
):
    import work_card_result_reply_recognition as recognition

    paths, session, _store, _ledger, presentations, _card_id, presentation_id, requirement = _presented_result(tmp_path)
    bridge = server.WsBridge(emit=lambda _event: None, paths=paths)
    repl = server._build_repl(session, paths, bridge)
    repl.agent.llm = _OrdinaryReplyLLM()
    text = "能解释一下刚才修复登录超时的结果吗？"

    def synthetic_match(prepared, _client, **_kwargs):
        assert prepared.status == "ready"
        return recognition.ResultReplyRecognition("match", candidate_id=prepared.candidate_ids[0])

    monkeypatch.setattr(recognition, "recognize_result_reply", synthetic_match)
    before_user_count = sum(m.get("role") == "user" and m.get("content") == text for m in session.messages)

    asyncio.run(server.WsSessionHandler(paths)._handle_incoming(
        {"type": "user.message", "text": text}, repl, bridge,
    ))

    assert sum(m.get("role") == "user" and m.get("content") == text for m in session.messages) == before_user_count + 1
    fact = presentations.get(presentation_id).fact
    assert [(view.result_id, view.requirement_id, view.action) for view in fact.result_views] == [
        ("reply-view-result", requirement["requirement_id"], "reliable_reply"),
    ]
    assert any(m.get("role") == "assistant" and m.get("content") == "普通回答继续。" for m in session.messages)




def _run_recording_controller_fallback(paths, session, bridge, repl, text):
    from types import SimpleNamespace
    class FallbackController:
        def run_turn(self, actual_text, *, execute):
            assert actual_text == text
            return SimpleNamespace(turn=None, reply="合成 controller 未能确认；继续普通答复。", storage_error=False)
    repl.agent.enable_work_record_recording = True
    repl.agent.work_record_recording_controller = FallbackController()
    asyncio.run(server.WsSessionHandler(paths)._handle_incoming(
        {"type": "user.message", "text": text}, repl, bridge,
    ))


def test_work_record_controller_early_reply_reuses_result_hook_once(tmp_path, monkeypatch):
    import work_card_result_reply_recognition as recognition

    paths, session, _store, _ledger, presentations, _card_id, presentation_id, requirement = _presented_result(tmp_path)
    bridge = server.WsBridge(emit=lambda _event: None, paths=paths)
    repl = server._build_repl(session, paths, bridge)
    repl.agent.llm = _OrdinaryReplyLLM()
    text = "登录超时修复怎么做的？"
    calls = []
    def synthetic_match(prepared, *_args, **_kwargs):
        calls.append(1)
        return recognition.ResultReplyRecognition("match", prepared.candidate_ids[0])
    monkeypatch.setattr(recognition, "recognize_result_reply", synthetic_match)
    before = len(session.messages)

    _run_recording_controller_fallback(paths, session, bridge, repl, text)

    assert calls == [1]
    assert sum(m.get("role") == "user" and m.get("content") == text for m in session.messages) == 1
    assert len(session.messages) == before + 2
    assert [(v.result_id, v.requirement_id, v.action)
            for v in presentations.get(presentation_id).fact.result_views] == [
        ("reply-view-result", requirement["requirement_id"], "reliable_reply"),
    ]


@pytest.mark.parametrize("blocked_state", ["unknown", "cancelled", "session_transition"])
def test_work_record_controller_early_reply_fail_closed_on_unknown_cancel_or_switch(
    tmp_path, monkeypatch, blocked_state,
):
    import work_card_result_reply_recognition as recognition

    paths, session, _store, _ledger, presentations, _card_id, presentation_id, _requirement = _presented_result(tmp_path)
    bridge = server.WsBridge(emit=lambda _event: None, paths=paths)
    repl = server._build_repl(session, paths, bridge)
    repl.agent.llm = _OrdinaryReplyLLM()
    text = "登录超时修复怎么做的？"
    def synthetic_block(prepared, *_args, **_kwargs):
        if blocked_state == "cancelled":
            bridge.cancel_event.set()
        elif blocked_state == "session_transition":
            bridge.begin_session_binding_change()
        if blocked_state == "unknown":
            return recognition.ResultReplyRecognition("uncertain")
        return recognition.ResultReplyRecognition("match", prepared.candidate_ids[0])
    monkeypatch.setattr(recognition, "recognize_result_reply", synthetic_block)
    before_users = sum(m.get("role") == "user" and m.get("content") == text for m in session.messages)

    try:
        _run_recording_controller_fallback(paths, session, bridge, repl, text)
    finally:
        if blocked_state == "session_transition":
            bridge.end_session_binding_change()

    assert sum(m.get("role") == "user" and m.get("content") == text for m in session.messages) == before_users + 1
    assert presentations.get(presentation_id).fact.result_views == ()

@pytest.mark.parametrize(("text", "title", "requirement_text"), [
    ("简历当时改了什么？", "简历修改记录", "将简历内容精简为两页"),
    ("登录超时修复怎么做的？", "接口响应优化", "修复登录超时问题"),
])
def test_candidate_named_natural_followup_reaches_llm_recognizer_and_marks_exact_result(
    tmp_path, monkeypatch, text, title, requirement_text,
):
    import work_card_result_reply_recognition as recognition

    paths, session, _store, _ledger, presentations, _card_id, presentation_id, requirement = _presented_result(
        tmp_path, title=title, requirement_text=requirement_text,
    )
    bridge = server.WsBridge(emit=lambda _event: None, paths=paths)
    repl = server._build_repl(session, paths, bridge)
    repl.agent.llm = _OrdinaryReplyLLM()
    recognized = []

    def synthetic_match(prepared, _client, **_kwargs):
        recognized.append(prepared)
        return recognition.ResultReplyRecognition("match", prepared.candidate_ids[0])

    monkeypatch.setattr(recognition, "recognize_result_reply", synthetic_match)
    asyncio.run(server.WsSessionHandler(paths)._handle_incoming(
        {"type": "user.message", "text": text}, repl, bridge,
    ))

    assert len(recognized) == 1, "candidate-specific natural question should reach semantic recognition"
    fact = presentations.get(presentation_id).fact
    assert [(view.result_id, view.requirement_id, view.action) for view in fact.result_views] == [
        ("reply-view-result", requirement["requirement_id"], "reliable_reply"),
    ]
    assert any(m.get("role") == "assistant" and m.get("content") == "普通回答继续。" for m in session.messages)