from __future__ import annotations
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from server import WsBridge, WsSessionHandler


def fixture(tmp_path):
    paths = AgentPaths(tmp_path, tmp_path / 'workspace', tmp_path / 'data', tmp_path / 'evolve')
    session = SimpleNamespace(conversation_id='synthetic-a')
    events = []
    bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=.5)
    bridge._session_provider = lambda: session
    bridge.init_post_turn_send_gate(session)
    status, ticket = bridge.create_post_turn_ticket(session, 'request-uuid', 'trusted-run')
    assert status == 'created'
    return bridge, session, ticket, events


def wait_request(events):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        rows = [e for e in events if e['type'] == 'confirm.request']
        if rows:
            return rows[-1]
        time.sleep(.005)
    raise AssertionError('missing post-turn request')


def start_confirmation(bridge, session, ticket):
    output = []
    worker = threading.Thread(target=lambda: output.append(bridge.confirm_post_turn(
        '工作记录单次外发确认\n精确外发消息：\n[{\"role\":\"user\",\"content\":\"synthetic\"}]', session=session, ticket=ticket, approve_outbound=True)))
    worker.start()
    return worker, output


def test_post_turn_and_ordinary_confirm_are_independently_routed(tmp_path):
    bridge, session, ticket, events = fixture(tmp_path)
    bridge._pending_confirm_id = 'ordinary-id'
    worker, result = start_confirmation(bridge, session, ticket)
    request = wait_request(events)
    assert request['session_id'] == session.conversation_id
    assert request['confirm_kind'] == 'work_record_post_turn'
    handler = object.__new__(WsSessionHandler)
    assert handler._dispatch_inline({'type':'confirm.response', 'request_id':request['request_id'], 'choice':'y'}, bridge)
    worker.join(2)
    assert result == [('y', request['request_id'])]
    assert bridge.try_start_post_turn_send(ticket, session)
    assert handler._dispatch_inline({'type':'confirm.response', 'request_id':request['request_id'], 'choice':'y'}, bridge)
    assert bridge._confirm_queue.empty()
    assert bridge._pending_confirm_id == 'ordinary-id'


def test_post_turn_confirmation_cancelled_and_rejected_cannot_permit_send(tmp_path):
    for mode in ('cancel', 'reject'):
        bridge, session, ticket, events = fixture(tmp_path)
        worker, result = start_confirmation(bridge, session, ticket)
        request = wait_request(events)
        if mode == 'cancel':
            bridge.invalidate_post_turn()
        else:
            assert bridge.deliver_post_turn_confirm(request['request_id'], 'n')
        worker.join(2)
        assert not worker.is_alive()
        assert result[0][0] == 'n'
        assert not bridge.try_start_post_turn_send(ticket, session)
        assert any(e.get('type') == 'confirm.done' and e.get('confirm_kind') == 'work_record_post_turn' for e in events)


def test_provider_only_starts_once_for_exact_approved_messages(tmp_path):
    from server import _PostTurnApprovedClient
    bridge, session, ticket, events = fixture(tmp_path)
    worker, result = start_confirmation(bridge, session, ticket)
    request = wait_request(events)
    assert bridge.deliver_post_turn_confirm(request['request_id'], 'y')
    worker.join(2)
    assert result[0][0] == 'y'
    class Provider:
        def __init__(self): self.calls = []
        def chat(self, messages, **kwargs):
            self.calls.append((messages, kwargs)); return 'synthetic'
    provider=Provider()
    messages=[{'role':'user', 'content':'synthetic'}]
    guarded=_PostTurnApprovedClient(provider, bridge, session, ticket, messages)
    try:
        guarded.chat([{'role':'user','content':'different'}], model='0x567-flash', tools=[], temperature=0.0, response_format={'type':'json_object'})
        raise AssertionError('changed preview was accepted')
    except ValueError:
        pass
    assert provider.calls == []
    assert guarded.chat(messages, model='0x567-flash', tools=[], temperature=0.0, response_format={'type':'json_object'}) == 'synthetic'
    assert len(provider.calls) == 1
    try:
        guarded.chat(messages, model='0x567-flash', tools=[], temperature=0.0, response_format={'type':'json_object'})
        raise AssertionError('duplicate send accepted')
    except RuntimeError:
        pass
    assert len(provider.calls) == 1


def test_provider_blocks_send_if_session_switch_wins(tmp_path):
    from server import _PostTurnApprovedClient
    bridge, session, ticket, events = fixture(tmp_path)
    worker, result = start_confirmation(bridge, session, ticket)
    request = wait_request(events)
    assert bridge.deliver_post_turn_confirm(request['request_id'], 'y')
    worker.join(2)
    assert result[0][0] == 'y'
    class Provider:
        calls=0
        def chat(self, *args, **kwargs): self.calls += 1
    provider=Provider()
    guarded=_PostTurnApprovedClient(provider, bridge, session, ticket, [{'role':'user','content':'synthetic'}])
    bridge.begin_session_binding_change()
    try:
        try:
            guarded.chat([{'role':'user','content':'synthetic'}], model='0x567-flash', tools=[], temperature=0.0, response_format={'type':'json_object'})
            raise AssertionError('send after session switch')
        except RuntimeError:
            pass
        assert provider.calls == 0
    finally:
        bridge.end_session_binding_change()


def test_switch_while_waiting_closes_confirmation(tmp_path):
    bridge, session, ticket, events = fixture(tmp_path)
    worker, result = start_confirmation(bridge, session, ticket)
    wait_request(events)
    bridge.begin_session_binding_change()
    try:
        worker.join(2)
        assert result and result[0][0] == 'n'
        assert not bridge.try_start_post_turn_send(ticket, session)
    finally:
        bridge.end_session_binding_change()


def test_timeout_does_not_leave_unresponsive_pending_confirmation(tmp_path):
    bridge, session, ticket, events = fixture(tmp_path)
    bridge.confirm_timeout = .05
    worker, result = start_confirmation(bridge, session, ticket)
    request = wait_request(events)
    worker.join(2)
    assert result == [('n', request['request_id'])]
    assert bridge._post_turn_confirmation is None
    assert not bridge.deliver_post_turn_confirm(request['request_id'], 'y')
    assert not bridge.try_start_post_turn_send(ticket, session)
    assert any(e.get('choice') == 'timeout' for e in events)


def test_emit_failure_does_not_leave_confirmation_pending(tmp_path):
    bridge, session, ticket, events = fixture(tmp_path)
    def fail(event):
        raise OSError('synthetic transport failure')
    bridge.emit = fail
    assert bridge.confirm_post_turn('工作记录单次外发确认\n精确外发消息：\n[]', session=session, ticket=ticket, approve_outbound=True) == ('n', '')
    assert bridge._post_turn_confirmation is None
    assert not bridge.try_start_post_turn_send(ticket, session)


def test_adapter_cannot_substitute_a_different_approved_preview(tmp_path):
    from server import _PostTurnApprovedClient
    bridge, session, ticket, events = fixture(tmp_path)
    worker, result = start_confirmation(bridge, session, ticket)
    request = wait_request(events)
    assert bridge.deliver_post_turn_confirm(request['request_id'], 'y')
    worker.join(2)
    assert result[0][0] == 'y'
    class Provider:
        calls = 0
        def chat(self, *args, **kwargs): self.calls += 1
    provider = Provider()
    different = [{'role':'user', 'content':'not the approved text'}]
    guarded = _PostTurnApprovedClient(provider, bridge, session, ticket, different)
    try:
        guarded.chat(different, model='0x567-flash', tools=[], temperature=0.0, response_format={'type':'json_object'})
        raise AssertionError('unapproved payload was sent')
    except RuntimeError:
        pass
    assert provider.calls == 0


def test_real_capture_service_uses_preview_gated_client_and_separate_write_rejection(tmp_path, monkeypatch):
    import uuid
    from session import Session, SessionMeta
    from server import _PostTurnApprovedClient
    from work_records_service import WorkRecordCaptureService
    paths = AgentPaths(tmp_path, tmp_path / 'workspace', tmp_path / 'data', tmp_path / 'evolve')
    session_dir = paths.data / 'sessions' / 'synthetic'
    session_dir.mkdir(parents=True)
    session = Session('synthetic', session_dir, '', SessionMeta(), [], paths)
    events=[]
    bridge=WsBridge(emit=events.append, paths=paths, confirm_timeout=1)
    bridge._session_provider=lambda: session
    bridge.init_post_turn_send_gate(session)
    request_id = str(uuid.uuid4())
    status, ticket = bridge.create_post_turn_ticket(session, request_id, 'host-run')
    assert status == 'created'
    class Registry:
        def get(self, _id):
            return SimpleNamespace(id='0x567-flash', vendor='0x567', provider_model='gpt-6-luna', max_input_tokens=96000)
    monkeypatch.setattr('work_records_service.get_registry', lambda *a, **k: Registry())
    monkeypatch.setattr('work_records_luna_intent.get_registry', lambda *a, **k: Registry())
    class Provider:
        calls=0
        def chat(self, messages, **kwargs):
            self.calls += 1
            return '{"action":"record","kind":"requirement","title":"synthetic","requirement":"synthetic result","scope":"independent","item_id":null}'
    provider=Provider()
    guarded=_PostTurnApprovedClient(provider, bridge, session, ticket)
    def confirm(preview):
        return bridge.confirm_post_turn(preview, session=session, ticket=ticket,
                                        approve_outbound=preview.startswith('工作记录单次外发确认\n'))
    service=WorkRecordCaptureService(session=session, client=guarded, confirm=confirm,
        host_binding_is_valid=lambda: bridge.post_turn_binding_is_valid(ticket, session), write_policy='create_only')
    result=[]
    worker=threading.Thread(target=lambda: result.append(service.capture('synthetic request', request_id=request_id, track_run=True)))
    worker.start()
    first=wait_request(events)
    assert bridge.deliver_post_turn_confirm(first['request_id'], 'y')
    deadline=time.monotonic()+2
    while time.monotonic()<deadline:
        pending=[e for e in events if e.get('type')=='confirm.request' and e['request_id'] != first['request_id']]
        if pending: break
        time.sleep(.005)
    assert pending and '本地落盘确认' in pending[-1]['preview']
    assert bridge.deliver_post_turn_confirm(pending[-1]['request_id'], 'n')
    worker.join(2)
    assert not worker.is_alive() and result[0].status == 'storage_not_authorized'
    assert provider.calls == 1 and not (session_dir / 'work-records.v1.json').exists()


def test_finish_releases_live_slot_but_retains_replay_tombstone(tmp_path):
    bridge, session, ticket, events = fixture(tmp_path)
    assert bridge.finish_post_turn(ticket, session)
    assert not bridge.finish_post_turn(ticket, session)
    assert bridge.create_post_turn_ticket(session, 'request-uuid', 'trusted-run') == ('replay', None)
    status, next_ticket = bridge.create_post_turn_ticket(session, 'request-next', 'next-run')
    assert status == 'created' and next_ticket is not None
    assert not bridge.finish_post_turn(ticket, session)
    assert bridge.post_turn_binding_is_valid(next_ticket, session)
