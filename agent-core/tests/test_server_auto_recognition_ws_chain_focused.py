from __future__ import annotations
import asyncio, json, sys, uuid
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server, llm_client
from tests.test_server_post_turn_freeze_focused import make_fixture
from work_records import WorkRecordStore
from llm_models import ModelEntry

SYNTHETIC_ENTRY = ModelEntry(id='0x567-flash', name='synthetic', vendor='0x567',
    base_url='https://api-cdn.0x567.com', provider_model='gpt-6-luna')

def fake_confirmed(provider):
    class Client:
        paths = None
        def set_cancel_event(self, event):
            self.cancel_event = event
        def chat_confirmed_json(self, messages, *, model_id, session_id, expected_preview_json, confirm_fn):
            if confirm_fn(expected_preview_json, False) != 'y':
                raise RuntimeError('permission denied')
            return provider.chat(messages, model=model_id, tools=[], temperature=0.0, response_format={'type':'json_object'})
    return Client()


def test_synthetic_ws_authorized_message_classifies_and_confirms(tmp_path, monkeypatch):
    paths, session, repl, _, _ = make_fixture(tmp_path, False)
    monkeypatch.setattr(server, 'resume_desktop_or_create', lambda _: session)
    def build(_session, _paths, bridge, **kwargs):
        bridge._session_provider = lambda: repl.session
        return repl
    monkeypatch.setattr(server, '_build_repl', build)
    monkeypatch.setattr(server.WsBridge, 'emit_session_state', lambda *a: None)
    monkeypatch.setattr(server, 'emit_corruption_notices', lambda *a: None)
    monkeypatch.setattr(server, '_emit_pending_work_card_references', lambda *a: None)
    class Registry:
        def get(self, _): return SimpleNamespace(id='0x567-flash', vendor='0x567', provider_model='gpt-6-luna', max_input_tokens=96000)
    monkeypatch.setattr('work_records_service.get_registry',lambda *a,**k:Registry())
    monkeypatch.setattr('work_records_luna_intent.get_registry',lambda *a,**k:Registry())
    calls=[]
    class Provider:
        paths=None
        def chat(self,messages,**kwargs):
            calls.append(messages)
            return '{"action":"record","kind":"requirement","title":"synthetic","requirement":"synthetic deliverable","scope":"independent","item_id":null}'
    monkeypatch.setattr(server,'_auto_recognition_target',lambda paths:(SYNTHETIC_ENTRY, SYNTHETIC_ENTRY.chat_completions_url()))
    monkeypatch.setattr(llm_client,'LLMClient',lambda **kwargs: fake_confirmed(Provider()))
    class Socket:
        def __init__(self): self.incoming=asyncio.Queue(); self.events=[]
        def __aiter__(self): return self
        async def __anext__(self):
            x=await self.incoming.get()
            if x is None: raise StopAsyncIteration
            return json.dumps(x)
        async def send(self,raw): self.events.append(json.loads(raw))
    handler=server.WsSessionHandler(paths); request_id=str(uuid.uuid4())
    async def run():
        ws=Socket(); task=asyncio.create_task(handler.handle(ws))
        await ws.incoming.put({'type':'work-record.auto-recognition.set','session_id':session.conversation_id,'request_id':str(uuid.uuid4()),'action':'enable'})
        for _ in range(300):
            if any(e.get('type')=='work-record.auto-recognition.done' and e.get('enabled') for e in ws.events): break
            await asyncio.sleep(.01)
        await ws.incoming.put({'type':'user.message','text':'synthetic task','request_id':request_id})
        approved=set()
        for _ in range(400):
            for e in list(ws.events):
                if e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn' and e['request_id'] not in approved:
                    approved.add(e['request_id']); await ws.incoming.put({'type':'confirm.response','request_id':e['request_id'],'choice':'y'})
            if any(e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==request_id for e in ws.events): break
            await asyncio.sleep(.01)
        await ws.incoming.put(None); await task
        return ws.events, approved
    events,approved=asyncio.run(run())
    result=[e for e in events if e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==request_id]
    assert result and result[-1]['status']=='recorded'
    assert len(approved)==1 and len(WorkRecordStore(session).list_items())==1
    assert len(calls)==1
    payload=json.loads(calls[0][1]['content'])
    assert payload == {'text':'synthetic task','recognition_view':[]}


def harness(tmp_path, monkeypatch, provider):
    from work_record_recognition_queue import RecognitionQueue
    paths, session, repl, _, _ = make_fixture(tmp_path, False)
    held = {}
    monkeypatch.setattr(server, 'resume_desktop_or_create', lambda _: session)
    def build(_, _paths, bridge, **kwargs):
        bridge._session_provider=lambda:repl.session
        held['bridge']=bridge
        return repl
    monkeypatch.setattr(server, '_build_repl', build)
    monkeypatch.setattr(server.WsBridge, 'emit_session_state', lambda *a: None)
    monkeypatch.setattr(server, 'emit_corruption_notices', lambda *a: None)
    monkeypatch.setattr(server, '_emit_pending_work_card_references', lambda *a: None)
    class Registry:
        def get(self, _): return SimpleNamespace(id='0x567-flash', vendor='0x567', provider_model='gpt-6-luna', max_input_tokens=96000)
    monkeypatch.setattr('work_records_service.get_registry',lambda *a,**k:Registry())
    monkeypatch.setattr('work_records_luna_intent.get_registry',lambda *a,**k:Registry())
    monkeypatch.setattr(server,'_auto_recognition_target',lambda paths:(SYNTHETIC_ENTRY, SYNTHETIC_ENTRY.chat_completions_url()))
    monkeypatch.setattr(llm_client,'LLMClient',lambda **kwargs:fake_confirmed(provider))
    class Socket:
        def __init__(self): self.incoming=asyncio.Queue(); self.events=[]
        def __aiter__(self): return self
        async def __anext__(self):
            x=await self.incoming.get()
            if x is None: raise StopAsyncIteration
            return json.dumps(x)
        async def send(self,raw): self.events.append(json.loads(raw))
    handler=server.WsSessionHandler(paths)
    async def wait(ws, predicate):
        for _ in range(500):
            hits=[e for e in ws.events if predicate(e)]
            if hits: return hits[-1]
            await asyncio.sleep(.01)
        raise AssertionError('missing event: '+repr(ws.events[-12:]))
    async def begin(capacity=None):
        ws=Socket(); task=asyncio.create_task(handler.handle(ws))
        await ws.incoming.put({'type':'work-record.auto-recognition.set','session_id':session.conversation_id,'request_id':str(uuid.uuid4()),'action':'enable'})
        await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.done' and e.get('enabled'))
        if capacity:
            bridge=held['bridge']; q=RecognitionQueue(capacity=capacity); q.authorize(bridge,session); bridge._auto_recognition_queue=q
        return ws,task
    async def message(ws,text):
        rid=str(uuid.uuid4())
        await ws.incoming.put({'type':'user.message','text':text,'request_id':rid})
        return rid
    async def finish(ws,task):
        await ws.incoming.put(None); await task
    return session, repl, held, handler, wait, begin, message, finish


def test_idle_chat_and_parallel_candidate_confirmation_do_not_block_following_turn(tmp_path,monkeypatch):
    class Provider:
        paths=None
        def __init__(self): self.calls=[]
        def chat(self,messages,**kwargs):
            text=json.loads(messages[1]['content'])['text'];self.calls.append(text)
            if text=='hello': return '{"action":"none","kind":"qa","title":null,"requirement":null,"scope":null,"item_id":null}'
            return '{"action":"record","kind":"requirement","title":"item","requirement":"deliverable","scope":"independent","item_id":null}'
    provider=Provider(); session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,provider)
    async def run():
        ws,task=await begin()
        chat=await message(ws,'hello')
        assert (await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==chat))['status']=='not_recorded'
        assert not [e for e in ws.events if e.get('confirm_kind')=='work_record_post_turn' and e['type']=='confirm.request']
        one=await message(ws,'first task')
        first=await wait(ws,lambda e:e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn')
        two=await message(ws,'second task')
        second=await wait(ws,lambda e:e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn' and e.get('request_id')!=first['request_id'])
        assert provider.calls==['hello','first task','second task']
        await ws.incoming.put({'type':'confirm.response','request_id':first['request_id'],'choice':'n'})
        assert (await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==one))['status']=='storage_not_authorized'
        await ws.incoming.put({'type':'confirm.response','request_id':second['request_id'],'choice':'y'})
        assert (await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==two))['status']=='recorded'
        assert len(WorkRecordStore(session).list_items())==1
        await finish(ws,task)
    asyncio.run(run())


def test_queue_full_and_revoke_invalidate_pending_confirmation(tmp_path,monkeypatch):
    class Provider:
        paths=None
        def __init__(self):self.calls=[]
        def chat(self,messages,**kwargs):
            self.calls.append(messages)
            return '{"action":"record","kind":"requirement","title":"item","requirement":"deliverable","scope":"independent","item_id":null}'
    provider=Provider(); session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,provider)
    async def run():
        ws,task=await begin(capacity=1)
        first=await message(ws,'first task')
        confirm=await wait(ws,lambda e:e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn')
        second=await message(ws,'second task')
        assert (await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==second))['status']=='queue_full'
        assert len(provider.calls)==1
        await ws.incoming.put({'type':'work-record.auto-recognition.set','session_id':session.conversation_id,'request_id':str(uuid.uuid4()),'action':'revoke'})
        await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.done' and e.get('action')=='revoke')
        await ws.incoming.put({'type':'confirm.response','request_id':confirm['request_id'],'choice':'y'})
        assert (await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==first))['status'] in {'stale_context','storage_not_authorized'}
        assert WorkRecordStore(session).list_items()==[]
        await finish(ws,task)
    asyncio.run(run())


def test_provider_failure_and_missing_terminal_are_observable(tmp_path,monkeypatch):
    class Provider:
        paths=None
        def chat(self,messages,**kwargs):raise RuntimeError('synthetic failure')
    session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,Provider())
    async def run():
        ws,task=await begin()
        rid=await message(ws,'failure synthetic')
        assert (await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid))['status']=='uncertain'
        await ws.incoming.put({'type':'user.message','text':'synthetic no id','request_id':'invalid'})
        assert (await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==''))['status']=='no_terminal'
        assert WorkRecordStore(session).list_items()==[]
        await finish(ws,task)
    asyncio.run(run())


def test_revoke_before_send_permission_never_calls_provider(tmp_path,monkeypatch):
    import threading
    entered, release=threading.Event(),threading.Event()
    class Provider:
        paths=None
        def __init__(self): self.calls=0
        def chat(self,*a,**k): self.calls+=1; return '{}'
    provider=Provider(); session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,provider)
    original=server._AutoRecognitionClient.chat
    def pause(self,*args,**kwargs):
        entered.set(); assert release.wait(3)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(server._AutoRecognitionClient,'chat',pause)
    async def run():
        ws,task=await begin(); rid=await message(ws,'synthetic task')
        assert await asyncio.to_thread(entered.wait,2)
        await ws.incoming.put({'type':'work-record.auto-recognition.set','session_id':session.conversation_id,'request_id':str(uuid.uuid4()),'action':'revoke'})
        await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.done' and e.get('action')=='revoke')
        release.set()
        assert (await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid))['status']=='cancelled'
        assert provider.calls==0 and WorkRecordStore(session).list_items()==[]
        await finish(ws,task)
    try: asyncio.run(run())
    finally: release.set()


def test_switch_after_send_starts_never_commits_or_confirms(tmp_path,monkeypatch):
    import threading
    from session import Session, SessionMeta
    entered, release=threading.Event(),threading.Event()
    class Provider:
        paths=None
        def chat(self,*a,**k):
            entered.set(); assert release.wait(3)
            return '{"action":"record","kind":"requirement","title":"item","requirement":"deliverable","scope":"independent","item_id":null}'
    session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,Provider())
    other_dir=session.session_dir.parent/'synthetic-other';other_dir.mkdir()
    other=Session('synthetic-other',other_dir,'',SessionMeta(),[],session.paths)
    original=handler._dispatch
    async def dispatch(msg,repl,bridge,**kwargs):
        if msg['type']=='session.open':
            repl.session=other
            return None
        return await original(msg,repl,bridge,**kwargs)
    monkeypatch.setattr(handler,'_dispatch',dispatch)
    async def run():
        ws,task=await begin();rid=await message(ws,'synthetic task')
        assert await asyncio.to_thread(entered.wait,2)
        await ws.incoming.put({'type':'session.open','session_id':other.conversation_id})
        for _ in range(200):
            if repl.session is other: break
            await asyncio.sleep(.01)
        assert repl.session is other
        release.set()
        result=await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid)
        assert result['status']=='cancelled' and result['session_id']==session.conversation_id
        assert not [e for e in ws.events if e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn']
        assert WorkRecordStore(session).list_items()==[] and WorkRecordStore(other).list_items()==[]
        await finish(ws,task)
    try: asyncio.run(run())
    finally: release.set()


def test_provider_receives_only_current_text_and_sanitized_session_view(tmp_path,monkeypatch):
    class Provider:
        paths=None
        def __init__(self):self.messages=[]
        def chat(self,messages,**kwargs):
            self.messages.append(messages)
            return '{"action":"none","kind":"qa","title":null,"requirement":null,"scope":null,"item_id":null}'
    provider=Provider(); session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,provider)
    store=WorkRecordStore(session)
    item=store.create_item_with_requirement(title='permitted title',scope={'kind':'independent'},
              requirement='secret old requirement',event_id=str(uuid.uuid4()),idempotency_key='synthetic-old')
    session.messages.append({'role':'assistant','content':'secret earlier assistant text'})
    async def run():
        ws,task=await begin();rid=await message(ws,'synthetic current')
        assert (await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid))['status']=='not_recorded'
        assert len(provider.messages)==1
        payload=json.loads(provider.messages[0][1]['content'])
        assert payload=={'text':'synthetic current','recognition_view':[{'item_id':item['item_id'],'title':'permitted title','scope':{'kind':'independent'}}]}
        assert 'secret' not in json.dumps(provider.messages[0],ensure_ascii=False)
        await finish(ws,task)
    asyncio.run(run())


def test_failure_and_overflow_emit_existing_ui_compatible_session_notice(tmp_path,monkeypatch):
    class Provider:
        paths=None
        def chat(self,*a,**k): raise RuntimeError('private details')
    session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,Provider())
    async def run():
        ws,task=await begin();rid=await message(ws,'synthetic request')
        event=await wait(ws,lambda e:e.get('type')=='work-record.post-turn.result' and e.get('request_id')==rid)
        assert event['session_id']==session.conversation_id and event['outcome']=='uncertain'
        assert 'private details' not in json.dumps(event,ensure_ascii=False)
        await finish(ws,task)
    asyncio.run(run())


def test_disconnect_after_outbound_start_never_saves(tmp_path,monkeypatch):
    import threading
    entered,release=threading.Event(),threading.Event()
    class Provider:
        paths=None
        def chat(self,*a,**k):
            entered.set(); assert release.wait(3)
            return '{"action":"record","kind":"requirement","title":"item","requirement":"deliverable","scope":"independent","item_id":null}'
    session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,Provider())
    async def run():
        ws,task=await begin(); await message(ws,'synthetic task')
        assert await asyncio.to_thread(entered.wait,2)
        await finish(ws,task)
        release.set()
        for _ in range(100):
            if held['bridge']._auto_recognition_active is None: break
            await asyncio.sleep(.01)
        assert WorkRecordStore(session).list_items()==[]
        assert not [e for e in ws.events if e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn']
    try: asyncio.run(run())
    finally: release.set()


def test_saved_record_is_bound_to_actual_terminal_run(tmp_path,monkeypatch):
    class Provider:
        paths=None
        def chat(self,*a,**k):
            return '{"action":"record","kind":"requirement","title":"item","requirement":"deliverable","scope":"independent","item_id":null}'
    session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,Provider())
    async def run():
        ws,task=await begin(); rid=await message(ws,'synthetic work')
        confirm=await wait(ws,lambda e:e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn')
        terminal=held['bridge'].lifecycle.snapshot()
        assert terminal.state=='settled' and terminal.run_id
        assert terminal.run_id in confirm['preview'] and rid in confirm['preview']
        assert 'not_verified' in confirm['preview'] and 'not_stored' in confirm['preview']
        await ws.incoming.put({'type':'confirm.response','request_id':confirm['request_id'],'choice':'y'})
        await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid and e.get('status')=='recorded')
        item=WorkRecordStore(session).list_items()[0]
        run=item['runs'][0]
        assert uuid.UUID(run['run_id'])==uuid.UUID(terminal.run_id) and run['status']=='unknown'
        assert run['verification']['status']=='not_verified'
        assert run['verification']['host_execution']['sequence']==terminal.sequence
        await finish(ws,task)
    asyncio.run(run())



def test_serial_recognition_network_does_not_block_next_turn(tmp_path,monkeypatch):
    import threading
    entered,release=threading.Event(),threading.Event()
    class Provider:
        paths=None
        def __init__(self): self.calls=[]
        def chat(self,messages,**kwargs):
            text=json.loads(messages[1]['content'])['text'];self.calls.append(text)
            if text=='first': entered.set(); assert release.wait(3)
            return '{"action":"none","kind":"qa","title":null,"requirement":null,"scope":null,"item_id":null}'
    provider=Provider();session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,provider)
    async def run():
        ws,task=await begin();first=await message(ws,'first')
        assert await asyncio.to_thread(entered.wait,2)
        first_run=held['bridge'].lifecycle.snapshot().run_id
        second=await message(ws,'second')
        for _ in range(200):
            snapshot=held['bridge'].lifecycle.snapshot()
            if snapshot and snapshot.run_id!=first_run and snapshot.state=='settled': break
            await asyncio.sleep(.01)
        assert snapshot.run_id!=first_run and snapshot.state=='settled'
        assert provider.calls==['first']
        release.set()
        for rid in (first,second):
            assert (await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid))['status']=='not_recorded'
        assert provider.calls==['first','second']
        await finish(ws,task)
    try: asyncio.run(run())
    finally: release.set()


def test_default_session_never_recognizes_ordinary_message(tmp_path,monkeypatch):
    class Provider:
        paths=None
        def chat(self,*a,**k): raise AssertionError('provider should not run')
    session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,Provider())
    async def run():
        class Socket:
            def __init__(self): self.incoming=asyncio.Queue();self.events=[]
            def __aiter__(self):return self
            async def __anext__(self):
                x=await self.incoming.get()
                if x is None:raise StopAsyncIteration
                return json.dumps(x)
            async def send(self,raw):self.events.append(json.loads(raw))
        ws=Socket();task=asyncio.create_task(handler.handle(ws)); await message(ws,'normal chat')
        await wait(ws,lambda e:e.get('type')=='turn.end')
        await finish(ws,task)
        assert not [e for e in ws.events if e.get('type') in {'work-record.auto-recognition.result','confirm.request'}]
    asyncio.run(run())


def test_missing_host_terminal_is_reported_without_provider_call(tmp_path,monkeypatch):
    class Provider:
        paths=None
        def __init__(self):self.calls=0
        def chat(self,*a,**k):self.calls+=1; return '{}'
    provider=Provider();session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,provider)
    original=server._finish_execution_safely
    def no_evidence(*args,**kwargs):
        original(*args,**kwargs)
        return None
    monkeypatch.setattr(server,'_finish_execution_safely',no_evidence)
    async def run():
        ws,task=await begin();rid=await message(ws,'synthetic work')
        result=await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid)
        assert result['status']=='no_terminal' and provider.calls==0
        notice=await wait(ws,lambda e:e.get('type')=='work-record.post-turn.result' and e.get('request_id')==rid)
        assert notice['outcome']=='not_recorded' and '终态' in notice['message']
        assert WorkRecordStore(session).list_items()==[]
        await finish(ws,task)
    asyncio.run(run())


def test_turn_cancel_before_send_permission_invalidates_recognition(tmp_path,monkeypatch):
    import threading
    entered,release=threading.Event(),threading.Event()
    class Provider:
        paths=None
        def __init__(self): self.calls=0
        def chat(self,*a,**k): self.calls+=1;return '{}'
    provider=Provider();session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,provider)
    original=server._AutoRecognitionClient.chat
    def pause(self,*args,**kwargs):
        entered.set();assert release.wait(3)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(server._AutoRecognitionClient,'chat',pause)
    async def run():
        ws,task=await begin();rid=await message(ws,'synthetic work')
        assert await asyncio.to_thread(entered.wait,2)
        await ws.incoming.put({'type':'turn.cancel'})
        await wait(ws,lambda e:e.get('type')=='notice' and '当前无进行中的回合' in e.get('text',''))
        release.set()
        assert (await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid))['status']=='cancelled'
        assert provider.calls==0 and WorkRecordStore(session).list_items()==[]
        await finish(ws,task)
    try:asyncio.run(run())
    finally:release.set()


def test_replay_capacity_skips_recognition_without_stopping_ordinary_turn(tmp_path,monkeypatch):
    class Provider:
        paths=None
        def __init__(self): self.calls=0
        def chat(self,*a,**k):
            self.calls+=1
            return '{"action":"none","kind":"qa","title":null,"requirement":null,"scope":null,"item_id":null}'
    provider=Provider();session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,provider)
    monkeypatch.setattr(server,'_POST_TURN_REQUEST_CAPACITY',1)
    async def run():
        ws,task=await begin();first=await message(ws,'first')
        await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==first)
        first_run=held['bridge'].lifecycle.snapshot().run_id
        second=await message(ws,'second')
        assert (await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==second))['status']=='capacity_exhausted'
        for _ in range(200):
            snapshot=held['bridge'].lifecycle.snapshot()
            if snapshot.run_id!=first_run and snapshot.state=='settled': break
            await asyncio.sleep(.01)
        assert snapshot.run_id!=first_run and snapshot.state=='settled' and provider.calls==1
        await finish(ws,task)
    asyncio.run(run())


def test_real_confirmed_sender_pins_0x567_and_does_not_send_project_boolean(tmp_path,monkeypatch):
    import httpx
    from llm_models import ModelEntry, ModelRegistry
    from llm_client import LLMClient, LLMConfig
    real_target=server._auto_recognition_target
    real_http_client=httpx.Client
    entry=ModelEntry(id='0x567-flash',name='synthetic',vendor='0x567',base_url='https://api-cdn.0x567.com',provider_model='gpt-6-luna',api_key='synthetic-secret')
    registry=ModelRegistry((entry,),entry.id,entry.id,{})
    session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,object())
    monkeypatch.setattr(server,'_auto_recognition_target',real_target)
    monkeypatch.setattr(ModelRegistry,'load',lambda *a,**k:registry)
    config=LLMConfig(api_key=None,base_url='https://unused.invalid',model=entry.id,model_coding=entry.id,timeout_sec=2,context_limit_override=None)
    monkeypatch.setattr(llm_client,'LLMClient',lambda **kwargs:LLMClient(config,paths=session.paths))
    captured=[]
    def responder(req):
        captured.append(req)
        return httpx.Response(200,json={'model':'gpt-6-luna','choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'{"action":"none","kind":"qa","title":null,"requirement":null,"scope":null,"item_id":null}'}}]})
    transport=httpx.MockTransport(responder)
    monkeypatch.setattr(llm_client.httpx,'Client',lambda **kwargs:real_http_client(transport=transport,**kwargs))
    async def run():
        ws,task=await begin();rid=await message(ws,'synthetic private text')
        assert (await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid))['status']=='not_recorded'
        assert len(captured)==1 and str(captured[0].url)=='https://api-cdn.0x567.com/v1/chat/completions'
        payload=json.loads(captured[0].content)
        assert payload['model']=='gpt-6-luna' and 'tools' not in payload
        assert json.loads(payload['messages'][1]['content'])=={'text':'synthetic private text','recognition_view':[]}
        await finish(ws,task)
    asyncio.run(run())


def test_provider_target_rejects_non_0x567_endpoint(tmp_path,monkeypatch):
    import pytest
    from llm_models import ModelEntry, ModelRegistry
    entry=ModelEntry(id='0x567-flash',name='synthetic',vendor='0x567',base_url='https://not-0x567.example.test',provider_model='gpt-6-luna',api_key='synthetic')
    monkeypatch.setattr(ModelRegistry,'load',lambda *a,**k:ModelRegistry((entry,),entry.id,entry.id,{}))
    with pytest.raises(ValueError):server._auto_recognition_target(None)


def test_registry_switch_after_permission_never_sends(tmp_path,monkeypatch):
    import httpx
    from llm_models import ModelEntry, ModelRegistry
    from llm_client import LLMClient, LLMConfig
    real_target=server._auto_recognition_target
    safe=ModelEntry(id='0x567-flash',name='synthetic',vendor='0x567',base_url='https://api-cdn.0x567.com',provider_model='gpt-6-luna',api_key='synthetic')
    changed=ModelEntry(id=safe.id,name=safe.name,vendor=safe.vendor,base_url='https://evil.example.test',provider_model=safe.provider_model,api_key='synthetic')
    reg1=ModelRegistry((safe,),safe.id,safe.id,{})
    reg2=ModelRegistry((changed,),changed.id,changed.id,{})
    session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,object())
    monkeypatch.setattr(server,'_auto_recognition_target',real_target)
    calls=[]
    def load(*a,**k):
        calls.append(1)
        return reg1 if len(calls)<3 else reg2
    monkeypatch.setattr(ModelRegistry,'load',load)
    config=LLMConfig(api_key=None,base_url='https://unused.invalid',model=safe.id,model_coding=safe.id,timeout_sec=1,context_limit_override=None)
    monkeypatch.setattr(llm_client,'LLMClient',lambda **kwargs:LLMClient(config,paths=session.paths))
    monkeypatch.setattr(llm_client.httpx,'Client',lambda **kwargs: (_ for _ in ()).throw(AssertionError('network must not start')))
    async def run():
        ws,task=await begin();rid=await message(ws,'synthetic task')
        result=await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid)
        assert result['status']=='uncertain' and len(calls)>=3
        assert WorkRecordStore(session).list_items()==[]
        await finish(ws,task)
    asyncio.run(run())


def test_commit_and_revoke_linearize_at_shared_host_lock(tmp_path,monkeypatch):
    import threading
    entered,release=threading.Event(),threading.Event()
    class Provider:
        paths=None
        def chat(self,*a,**k):return '{"action":"record","kind":"requirement","title":"item","requirement":"deliverable","scope":"independent","item_id":null}'
    session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,Provider())
    original=WorkRecordStore.create_item_with_requirement
    def pause_commit(self,*args,**kwargs):
        entered.set();assert release.wait(3)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(WorkRecordStore,'create_item_with_requirement',pause_commit)
    async def run():
        ws,task=await begin();rid=await message(ws,'synthetic work')
        confirm=await wait(ws,lambda e:e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn')
        await ws.incoming.put({'type':'confirm.response','request_id':confirm['request_id'],'choice':'y'})
        assert await asyncio.to_thread(entered.wait,2)
        bridge=held['bridge']
        revoke={'type':'work-record.auto-recognition.set','session_id':session.conversation_id,'request_id':str(uuid.uuid4()),'action':'revoke'}
        revoker=asyncio.create_task(asyncio.to_thread(bridge.set_auto_recognition,revoke,session))
        await asyncio.sleep(.05);assert not revoker.done()
        release.set()
        assert (await revoker)['status']=='ok'
        assert (await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid))['status']=='recorded'
        assert len(WorkRecordStore(session).list_items())==1
        await finish(ws,task)
    try:asyncio.run(run())
    finally:release.set()


def test_cancel_reports_active_and_queued_without_waiting_for_network(tmp_path,monkeypatch):
    import threading
    entered,release=threading.Event(),threading.Event()
    class Provider:
        paths=None
        def __init__(self):self.calls=0
        def chat(self,*a,**k):
            self.calls+=1;entered.set();assert release.wait(3)
            return '{"action":"none","kind":"qa","title":null,"requirement":null,"scope":null,"item_id":null}'
    provider=Provider();session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,provider)
    async def run():
        ws,task=await begin();first=await message(ws,'first')
        assert await asyncio.to_thread(entered.wait,2)
        second=await message(ws,'second')
        for _ in range(200):
            if held['bridge']._auto_recognition_pending:break
            await asyncio.sleep(.01)
        assert held['bridge']._auto_recognition_pending
        await ws.incoming.put({'type':'turn.cancel'})
        for rid in (first,second):
            result=await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid)
            assert result['status']=='cancelled'
        assert not release.is_set() and provider.calls==1
        release.set();await asyncio.sleep(.1)
        assert provider.calls==1 and WorkRecordStore(session).list_items()==[]
        await finish(ws,task)
    try:asyncio.run(run())
    finally:release.set()
