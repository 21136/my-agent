from __future__ import annotations
import asyncio
import sys
import uuid
import pytest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from session import Session, SessionMeta
from server import TURN_LOCK, WsBridge, _run_line
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from tools.schema import tool_ok


def make_fixture(tmp_path, perform_tool):
    paths = AgentPaths(tmp_path, tmp_path / 'workspace', tmp_path / 'data', tmp_path / 'evolve')
    session_dir = paths.data / 'sessions' / 'synthetic-session'
    session_dir.mkdir(parents=True)
    session = Session('synthetic-session', session_dir, '', SessionMeta(), [], paths)
    executor_session = ExecutorSession(session_dir=session_dir)
    executor = ToolExecutor(registry=ToolRegistry(agent_paths=paths, evolved=[]), session=executor_session, confirm_fn=lambda *_a, **_k:'y')
    executor._execute_builtin = lambda name, args, **kwargs: tool_ok(name, {'synthetic': True})
    agent = SimpleNamespace(session=session, executor=executor, work_card_reference_intent=None)
    def bind(run_id, _authorization=''):
        executor_session.host_run_id = run_id
    agent.bind_host_run_context = bind
    def handle(line):
        executor.begin_turn()
        if perform_tool:
            assert executor.run('read_file', {'path':'synthetic'}).ok
            session.messages.append({'role':'tool', 'content':'synthetic'})
            if perform_tool == 'incomplete':
                session.messages.append({'role':'tool', 'content':'extra synthetic'})
            if perform_tool == 'cancel':
                bridge.request_cancel()
        return 'continue'
    repl = SimpleNamespace(session=session, agent=agent, handle_line=handle, last_turn_finish_reason=None)
    events=[]
    bridge=WsBridge(emit=events.append, paths=paths)
    bridge._session_provider=lambda: repl.session
    return paths, session, repl, bridge, events


def test_run_line_freezes_real_executor_receipt_and_host_terminal(tmp_path):
    paths, session, repl, bridge, events = make_fixture(tmp_path, True)
    request_id = str(uuid.uuid4())
    async def run():
        async with TURN_LOCK:
            return await _run_line(repl, bridge, 'synthetic task', paths, lock_held=True,
                                   post_turn_request_id=request_id)
    candidate=asyncio.run(run())
    assert candidate is not None
    assert candidate.session is session and candidate.request_id == request_id
    assert candidate.run_id == bridge.lifecycle.snapshot().run_id
    assert candidate.sequence == bridge.lifecycle.snapshot().sequence
    assert candidate.finish_reason == 'completed'
    assert len(candidate.receipts) == 1 and candidate.receipts[0][2] == 'read_file'
    assert any(event.get('type') == 'turn.end' for event in events)


def test_text_only_candidate_preserves_unknown_work_outcome(tmp_path):
    paths, session, repl, bridge, events = make_fixture(tmp_path, False)
    async def run():
        async with TURN_LOCK:
            return await _run_line(repl, bridge, 'synthetic resume advice', paths, lock_held=True,
                                   post_turn_request_id=str(uuid.uuid4()))
    candidate = asyncio.run(run())
    assert candidate is not None
    assert candidate.receipts == ()
    assert candidate.tool_receipt_status == 'complete'
    assert candidate.finish_reason == 'completed'


def test_incomplete_receipts_are_unknown_and_cancelled_turn_is_not_candidate(tmp_path):
    for mode in ('incomplete', 'cancel'):
        paths, session, repl, bridge, events = make_fixture(tmp_path / mode, mode)
        async def run():
            async with TURN_LOCK:
                return await _run_line(repl, bridge, 'synthetic task', paths, lock_held=True,
                                       post_turn_request_id=str(uuid.uuid4()))
        candidate = asyncio.run(run())
        if mode == 'cancel':
            assert candidate is None
        else:
            assert candidate is not None and candidate.tool_receipt_status == 'unknown'
            assert candidate.receipts == ()


@pytest.mark.parametrize('perform_tool', [True, False])
def test_frozen_candidate_can_be_classified_and_atomically_committed_with_two_confirms(tmp_path, monkeypatch, perform_tool):
    import threading, time
    from server import _execute_post_turn_candidate
    from work_records import WorkRecordStore
    paths, session, repl, bridge, events = make_fixture(tmp_path, perform_tool)
    bridge.init_post_turn_send_gate(session)
    request_id = str(uuid.uuid4())
    async def run():
        async with TURN_LOCK:
            return await _run_line(repl, bridge, 'synthetic task', paths, lock_held=True,
                                   post_turn_request_id=request_id)
    candidate=asyncio.run(run())
    assert candidate is not None
    class Registry:
        def get(self, _id):
            return SimpleNamespace(id='0x567-flash', vendor='0x567', provider_model='gpt-6-luna', max_input_tokens=96000)
    monkeypatch.setattr('work_records_service.get_registry', lambda *a, **k: Registry())
    monkeypatch.setattr('work_records_luna_intent.get_registry', lambda *a, **k: Registry())
    class Provider:
        calls=0
        def chat(self, messages, **kwargs):
            self.calls += 1
            return '{"action":"record","kind":"requirement","title":"synthetic item","requirement":"synthetic requirement","scope":"independent","item_id":null}'
    provider=Provider(); results=[]; errors=[]
    original_create = WorkRecordStore.create_item_with_requirement
    def trace_create(self, **kwargs):
        try:
            return original_create(self, **kwargs)
        except Exception as exc:
            errors.append(repr(exc))
            raise
    monkeypatch.setattr(WorkRecordStore, 'create_item_with_requirement', trace_create)
    worker=threading.Thread(target=lambda: results.append(_execute_post_turn_candidate(candidate, bridge, provider)))
    worker.start()
    answered=set(); deadline=time.monotonic()+4
    while worker.is_alive() and time.monotonic()<deadline:
        for event in list(events):
            if event.get('type') == 'confirm.request' and event.get('confirm_kind') == 'work_record_post_turn' and event['request_id'] not in answered:
                answered.add(event['request_id'])
                assert bridge.deliver_post_turn_confirm(event['request_id'], 'y')
        time.sleep(.005)
    worker.join(1)
    assert not worker.is_alive() and len(answered) == 2 and provider.calls == 1
    assert results[0].status == 'recorded', errors
    items=WorkRecordStore(session).list_items()
    assert len(items) == 1 and items[0]['requirements'][0]['status'] == 'unknown'
    assert uuid.UUID(items[0]['runs'][0]['run_id']) == uuid.UUID(candidate.run_id)
    assert items[0]['runs'][0]['verification']['host_execution']['sequence'] == candidate.sequence
    assert items[0]['runs'][0]['verification']['status'] == 'not_verified'


@pytest.mark.parametrize('action', ['reject', 'switch'])
def test_candidate_outbound_refusal_or_switch_never_calls_provider(tmp_path, action):
    import threading, time
    from server import _execute_post_turn_candidate
    from work_records import WorkRecordStore
    paths, session, repl, bridge, events = make_fixture(tmp_path, True)
    bridge.init_post_turn_send_gate(session)
    async def run():
        async with TURN_LOCK:
            return await _run_line(repl, bridge, 'synthetic task', paths, lock_held=True,
                                   post_turn_request_id=str(uuid.uuid4()))
    candidate = asyncio.run(run())
    class Provider:
        calls = 0
        def chat(self, *args, **kwargs): self.calls += 1
    provider = Provider(); outcomes=[]
    worker = threading.Thread(target=lambda: outcomes.append(_execute_post_turn_candidate(candidate, bridge, provider)))
    worker.start()
    deadline=time.monotonic()+2
    while time.monotonic()<deadline:
        requests=[e for e in events if e.get('confirm_kind')=='work_record_post_turn' and e.get('type')=='confirm.request']
        if requests: break
        time.sleep(.005)
    assert requests
    if action == 'reject':
        assert bridge.deliver_post_turn_confirm(requests[-1]['request_id'], 'n')
    else:
        bridge.begin_session_binding_change()
    try:
        worker.join(2)
        assert not worker.is_alive() and outcomes[0].status == 'not_authorized'
        assert provider.calls == 0 and WorkRecordStore(session).list_items() == []
    finally:
        if action == 'switch':
            bridge.end_session_binding_change()
