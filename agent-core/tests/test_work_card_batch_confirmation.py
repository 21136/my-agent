import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import server
from tests.isolation_helpers import temporary_agent_paths


def setup_bridge(paths):
    events=[]; bridge=server.WsBridge(emit=events.append,paths=paths)
    session=SimpleNamespace(conversation_id='session-a'); current=[session]
    bridge._session_provider=lambda:current[0]
    return bridge,events,session,current


def wait_request(events, start=0):
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        rows=[x for x in events[start:] if x['type']=='confirm.request']
        if rows: return rows[-1]['request_id']
        time.sleep(.005)
    raise AssertionError('confirmation not emitted')


def start(bridge,session,results):
    t=threading.Thread(target=lambda:results.append(bridge.confirm_work_card_batch('preview',False,session=session)))
    t.start(); return t


def test_batch_approval_session_and_stale_id():
    with temporary_agent_paths() as paths:
        bridge,events,session,current=setup_bridge(paths); results=[]
        t=start(bridge,session,results); rid=wait_request(events)
        assert not bridge.deliver_confirm('stale-id','y')
        assert bridge.deliver_confirm(rid,'y')
        t.join(2); assert results==['y']
        start_index=len(events); t=start(bridge,session,results); rid2=wait_request(events,start_index)
        assert rid2!=rid and not bridge.deliver_confirm(rid,'y')
        current[0]=SimpleNamespace(conversation_id='session-a')
        assert bridge.deliver_confirm(rid2,'y')
        t.join(2); assert results==['y','n']


def test_cancel_and_disconnect_deny():
    for stop in ('cancel','disconnect'):
        with temporary_agent_paths() as paths:
            bridge,events,session,_=setup_bridge(paths); result=[]; t=start(bridge,session,result); rid=wait_request(events)
            if stop=='cancel': bridge.cancel_event.set()
            else: bridge._connection_closed.set()
            assert not bridge.deliver_confirm(rid,'y')
            t.join(2); assert result==['n']


def test_timeout_late_response_and_default_unchanged():
    with temporary_agent_paths() as paths:
        bridge,events,session,_=setup_bridge(paths); result=[]
        bridge.batch_confirm_timeout=.05
        t=start(bridge,session,result); rid=wait_request(events); t.join(2)
        assert result==['n'] and any(x.get('choice')=='timeout' for x in events)
        bridge.deliver_confirm(rid,'y')
        start_index=len(events); t=start(bridge,session,result); rid2=wait_request(events,start_index)
        assert rid2!=rid; assert bridge.deliver_confirm(rid2,'n'); t.join(2); assert result==['n','n']
        assert bridge.confirm_timeout==90.0


def test_configuration_bounds_and_wrong_binding():
    with patch.dict('os.environ',{'WORK_CARD_BATCH_CONFIRM_TIMEOUT_SEC':'inf'}):
        assert server.work_card_batch_confirm_timeout_sec()==1800.0
    with patch.dict('os.environ',{'WORK_CARD_BATCH_CONFIRM_TIMEOUT_SEC':'100000'}):
        assert server.work_card_batch_confirm_timeout_sec()==3600.0
    with temporary_agent_paths() as paths:
        bridge,events,session,current=setup_bridge(paths)
        current[0]=SimpleNamespace(conversation_id='session-a')
        assert bridge.confirm_work_card_batch('preview',False,session=session)=='n'
        assert not events


def test_controlled_clock_expiry_and_stale_never_authorize():
    with temporary_agent_paths() as paths:
        bridge,events,session,_=setup_bridge(paths); result=[]; clock=[100.0]
        with patch.object(server,'time',SimpleNamespace(monotonic=lambda:clock[0])):
            bridge.batch_confirm_timeout=100.0
            t=start(bridge,session,result); rid=wait_request(events)
            clock[0]=201.0
            assert not bridge.deliver_confirm(rid,'y')
            t.join(2); assert result==['n']
            assert any(x.get('request_id')==rid and x.get('choice')=='timeout' for x in events)


def test_regular_confirm_timeout_unchanged():
    with temporary_agent_paths() as paths:
        bridge,events,_,_=setup_bridge(paths); bridge.confirm_timeout=.04
        assert bridge.confirm_fn('ordinary tool preview',False)=='n'
        assert any(x.get('choice')=='timeout' for x in events)


def test_watchdog_paused_and_restored_for_approval_rejection_and_timeout():
    for choice in ('y','n','timeout'):
        with temporary_agent_paths() as paths:
            bridge,events,session,_=setup_bridge(paths); result=[]
            watchdog=bridge.turn_watchdog
            watchdog.pause_wall()  # existing outer pause must survive the nested confirmation
            bridge.batch_confirm_timeout=.15
            thread=start(bridge,session,result); rid=wait_request(events)
            assert watchdog._wall_pause_depth==2
            if choice!='timeout': assert bridge.deliver_confirm(rid,choice)
            thread.join(2); assert not thread.is_alive()
            assert result==(['y'] if choice=='y' else ['n'])
            assert watchdog._wall_pause_depth==1
            watchdog.resume_wall(); assert watchdog._wall_pause_depth==0


def test_watchdog_restored_on_emit_exception():
    with temporary_agent_paths() as paths:
        bridge,events,session,_=setup_bridge(paths)
        def fail(event):
            if event['type']=='confirm.request': raise RuntimeError('synthetic')
            events.append(event)
        bridge.emit=fail
        assert bridge.confirm_work_card_batch('preview',False,session=session)=='n'
        assert bridge.turn_watchdog._wall_pause_depth==0
        assert bridge._batch_confirm_pending is None


def test_watchdog_wall_does_not_expire_while_waiting():
    with temporary_agent_paths() as paths:
        bridge,events,session,_=setup_bridge(paths); result=[]
        bridge.turn_watchdog.wall_sec=.12
        bridge.turn_watchdog.begin()
        try:
            thread=start(bridge,session,result); rid=wait_request(events)
            time.sleep(.25)
            assert not bridge.cancel_event.is_set()
            assert bridge.deliver_confirm(rid,'y')
            thread.join(2); assert result==['y']
            assert bridge.turn_watchdog._wall_pause_depth==0
        finally:
            bridge.turn_watchdog.end()
