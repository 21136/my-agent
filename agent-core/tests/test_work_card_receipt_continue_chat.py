from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CORE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent as agent_module
import server
from test_work_card_recall_agent import setup_case
from work_card_presentations import WorkCardPresentations


@pytest.mark.parametrize('text', ['继续恢复工作卡片', '请继续恢复工作卡', '帮我继续恢复工作卡片吧', '继续恢复当前会话的工作卡片'])
def test_explicit_continue_recognized(text):
    assert agent_module._is_explicit_work_card_receipt_continue(text)


@pytest.mark.parametrize('text', [
    '不要继续恢复工作卡片', '“继续恢复工作卡片”是什么意思', '继续恢复其他会话的工作卡片',
    '继续恢复工作卡片然后删除文件', '继续恢复工作卡片并继续工作', '恢复工作卡片',
    '还有哪些工作卡片', '当前工作整体概况', '找回之前的工作卡', '继续恢复工作卡片\n创建文件',
])
def test_non_explicit_continue_is_not_recognized(text):
    assert not agent_module._is_explicit_work_card_receipt_continue(text)


def test_agent_continues_one_batch_reports_real_result_and_leaves_legacy(tmp_path, monkeypatch):
    agent, session, store, ledger, card_id = setup_case(tmp_path, monkeypatch)
    presentations = WorkCardPresentations(session, store, ledger)
    for _ in range(7):
        run_id = str(uuid.uuid4())
        pid = server._work_card_presentation_id(session.conversation_id, run_id, card_id)
        assert presentations.create(pid, card_id, run_id=run_id).status == 'created'
        session.append_sent_work_card_reference(pid, card_id, run_id=run_id)
    assert presentations.create(str(uuid.uuid4()), card_id).status == 'created'
    events = []
    bridge = server.WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=agent)
    server._bind_work_card_reference_intent(repl, bridge)
    assert bridge.begin_turn(mode='ordinary')
    bridge.lifecycle_conversation_id = session.conversation_id
    result = agent.run_turn('继续恢复工作卡片')
    assert len([event for event in events if event.get('type') == 'work-card.present']) == 5
    assert '排队 5' in result.assistant_text and '尚有' in result.assistant_text
    assert '旧' in result.assistant_text and '不自动重发' in result.assistant_text
    assert '不代表' in result.assistant_text and '已接收' in result.assistant_text
    assert all(fact.received_at is None for fact in presentations.list_retryable_receipts())
    assert session.messages[-1]['content'] == result.assistant_text


def test_agent_refuses_unbound_or_cancelled_continue(tmp_path, monkeypatch):
    agent, session, _store, _ledger, _card = setup_case(tmp_path, monkeypatch)
    events = []
    bridge = server.WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=agent)
    server._bind_work_card_reference_intent(repl, bridge)
    result = agent.run_turn('继续恢复工作卡片')
    assert '未能安全' in result.assistant_text
    assert not any(event.get('type') == 'work-card.present' for event in events)
    assert bridge.begin_turn(mode='ordinary')
    bridge.lifecycle_conversation_id = session.conversation_id
    bridge.cancel_event.set()
    result = agent.run_turn('继续恢复工作卡片')
    assert '未能安全' in result.assistant_text
    assert not any(event.get('type') == 'work-card.present' for event in events)


def test_continue_partial_when_cancel_arrives_and_no_second_card(tmp_path, monkeypatch):
    agent, session, store, ledger, card_id = setup_case(tmp_path, monkeypatch)
    presentations = WorkCardPresentations(session, store, ledger)
    for _ in range(3):
        run_id = str(uuid.uuid4())
        pid = server._work_card_presentation_id(session.conversation_id, run_id, card_id)
        assert presentations.create(pid, card_id, run_id=run_id).status == 'created'
        session.append_sent_work_card_reference(pid, card_id, run_id=run_id)
    events = []
    bridge = server.WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=agent)
    server._bind_work_card_reference_intent(repl, bridge)
    assert bridge.begin_turn(mode='ordinary')
    bridge.lifecycle_conversation_id = session.conversation_id
    original = server._emit_work_card_reference
    def cancel_after_first(*args, **kwargs):
        result = original(*args, **kwargs)
        bridge.cancel_event.set()
        return result
    monkeypatch.setattr(server, '_emit_work_card_reference', cancel_after_first)
    result = agent.run_turn('继续恢复工作卡片')
    assert len([event for event in events if event.get('type') == 'work-card.present']) == 1
    assert '排队 1' in result.assistant_text and '部分操作未完成或已取消' in result.assistant_text
    assert '尚未确认接收' in result.assistant_text


def test_normal_run_line_emits_reply_after_single_batch_without_model(tmp_path, monkeypatch):
    import asyncio
    from main import ConversationRepl
    agent, session, store, ledger, card_id = setup_case(tmp_path, monkeypatch)
    presentations = WorkCardPresentations(session, store, ledger)
    run_id = str(uuid.uuid4())
    pid = server._work_card_presentation_id(session.conversation_id, run_id, card_id)
    assert presentations.create(pid, card_id, run_id=run_id).status == 'created'
    session.append_sent_work_card_reference(pid, card_id, run_id=run_id)
    events = []
    bridge = server.WsBridge(emit=events.append, paths=session.paths)
    repl = ConversationRepl(paths=session.paths, session=session, agent=agent,
                            input_fn=lambda _prompt: '', output_fn=lambda _text: None,
                            assistant_output_fn=bridge.emit_assistant)
    server._bind_work_card_reference_intent(repl, bridge)
    monkeypatch.setattr(agent.llm, 'chat', lambda *args, **kwargs: pytest.fail('model must not run'))
    asyncio.run(server.WsSessionHandler(session.paths)._handle_incoming(
        {'type': 'user.message', 'text': '继续恢复工作卡片'}, repl, bridge))
    present = [e for e in events if e.get('type') == 'work-card.present']
    replies = [e for e in events if e.get('type') == 'assistant.done']
    assert len(present) == len(replies) == 1
    assert events.index(present[0]) < events.index(replies[0])
    assert '排队 1' in replies[0]['text'] and '不代表客户端已接收' in replies[0]['text']
    assert presentations.get(pid).fact.received_at is None


def test_continue_reports_deferred_without_resending(tmp_path, monkeypatch):
    agent, session, store, ledger, card_id = setup_case(tmp_path, monkeypatch)
    presentations = WorkCardPresentations(session, store, ledger)
    run_id = str(uuid.uuid4())
    pid = server._work_card_presentation_id(session.conversation_id, run_id, card_id)
    assert presentations.create(pid, card_id, run_id=run_id).status == 'created'
    session.append_sent_work_card_reference(pid, card_id, run_id=run_id)
    for _ in range(3):
        presentations.mark_transport_offer(pid, card_id)
    events = []
    bridge = server.WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=agent)
    server._bind_work_card_reference_intent(repl, bridge)
    assert bridge.begin_turn(mode='ordinary')
    bridge.lifecycle_conversation_id = session.conversation_id
    result = agent.run_turn('继续恢复工作卡片')
    assert '排队 0' in result.assistant_text and '已延后 1' in result.assistant_text
    assert '不自动重发' in result.assistant_text
    assert not any(event.get('type') == 'work-card.present' for event in events)


def test_stale_agent_binding_cannot_continue_in_new_session(tmp_path, monkeypatch):
    agent, session, _store, _ledger, _card = setup_case(tmp_path, monkeypatch)
    events = []
    bridge = server.WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=agent)
    server._bind_work_card_reference_intent(repl, bridge)
    assert bridge.begin_turn(mode='ordinary')
    bridge.lifecycle_conversation_id = session.conversation_id
    repl.agent = SimpleNamespace(session=session)
    result = agent.run_turn('继续恢复工作卡片')
    assert '未能安全' in result.assistant_text
    assert not any(event.get('type') == 'work-card.present' for event in events)


def test_enqueue_failure_does_not_claim_success(tmp_path, monkeypatch):
    agent, session, store, ledger, card_id = setup_case(tmp_path, monkeypatch)
    presentations = WorkCardPresentations(session, store, ledger)
    run_id = str(uuid.uuid4())
    pid = server._work_card_presentation_id(session.conversation_id, run_id, card_id)
    assert presentations.create(pid, card_id, run_id=run_id).status == 'created'
    session.append_sent_work_card_reference(pid, card_id, run_id=run_id)
    events = []
    bridge = server.WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=agent)
    server._bind_work_card_reference_intent(repl, bridge)
    assert bridge.begin_turn(mode='ordinary')
    bridge.lifecycle_conversation_id = session.conversation_id
    monkeypatch.setattr(server, '_emit_work_card_reference', lambda *args, **kwargs: False)
    result = agent.run_turn('继续恢复工作卡片')
    assert '排队 0' in result.assistant_text and '部分操作未完成' in result.assistant_text
    assert '尚有' in result.assistant_text
    assert not any(event.get('type') == 'work-card.present' for event in events)
    assert presentations.get(pid).fact.received_at is None


def test_rebinding_during_batch_stops_further_enqueue(tmp_path, monkeypatch):
    agent, session, store, ledger, card_id = setup_case(tmp_path, monkeypatch)
    presentations = WorkCardPresentations(session, store, ledger)
    for _ in range(3):
        run_id = str(uuid.uuid4())
        pid = server._work_card_presentation_id(session.conversation_id, run_id, card_id)
        assert presentations.create(pid, card_id, run_id=run_id).status == 'created'
        session.append_sent_work_card_reference(pid, card_id, run_id=run_id)
    events = []
    bridge = server.WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=agent)
    server._bind_work_card_reference_intent(repl, bridge)
    assert bridge.begin_turn(mode='ordinary')
    bridge.lifecycle_conversation_id = session.conversation_id
    original = server._emit_work_card_reference
    def rebind_after_first(*args, **kwargs):
        result = original(*args, **kwargs)
        repl.agent = SimpleNamespace(session=session)
        return result
    monkeypatch.setattr(server, '_emit_work_card_reference', rebind_after_first)
    result = agent.run_turn('继续恢复工作卡片')
    assert len([event for event in events if event.get('type') == 'work-card.present']) == 1
    assert '排队 1' in result.assistant_text and '部分操作未完成' in result.assistant_text
