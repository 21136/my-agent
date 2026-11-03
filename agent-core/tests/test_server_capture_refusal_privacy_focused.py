from __future__ import annotations
import asyncio, sys, uuid
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server, llm_client
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore

@pytest.mark.parametrize('choice',["n","timeout"])
def test_refused_real_capture_stops_before_ordinary_or_classifier_provider(tmp_path,monkeypatch,choice):
    paths=AgentPaths(tmp_path,tmp_path/'workspace',tmp_path/'data',tmp_path/'evolve')
    paths.workspace.mkdir();sid=str(uuid.uuid4());directory=paths.data/'sessions'/sid;directory.mkdir(parents=True)
    session=Session(sid,directory,'',SessionMeta(),[],paths);session.save()
    calls=[];events=[];previews=[]
    class FakeClassifier:
        def __init__(self,**kwargs): self.paths=paths
        def set_cancel_event(self,event): pass
        def chat(self,*args,**kwargs): calls.append('classifier');raise AssertionError('must not send')
    monkeypatch.setattr(llm_client,'LLMClient',FakeClassifier)
    bridge=server.WsBridge(emit=events.append,paths=paths)
    def deny(preview,allow):
        previews.append(preview)
        return choice,str(uuid.uuid4())
    monkeypatch.setattr(bridge,'confirm_with_receipt',deny)
    repl=SimpleNamespace(session=session,last_turn_finish_reason=None,agent=SimpleNamespace(bind_host_run_context=None,
        executor=SimpleNamespace(session=SimpleNamespace(evolution_authorization_id=''))),
        handle_line=lambda text:calls.append('ordinary-model-path'))
    handler=server.WsSessionHandler(paths)
    message='记录一下 synthetic sensitive input'
    asyncio.run(handler._dispatch({'type':'user.message','text':message,'request_id':str(uuid.uuid4())},repl,bridge))
    assert len(previews)==1 and '工作记录单次外发确认' in previews[0] and message in previews[0]
    assert calls==[] and WorkRecordStore(session).list_items()==[]
    assert bridge.lifecycle.snapshot().state=='settled'
    assert any('未进入普通 Agent' in e.get('text','') for e in events)
    assert [(m['role'],m['content']) for m in session.messages][-2:]==[
        ('user',message),('assistant',next(m['content'] for m in session.messages if m['role']=='assistant'))]
    assert '后续对话可能发送' in session.messages[-1]['content']
