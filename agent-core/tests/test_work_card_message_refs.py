import json
import threading
import uuid
import pytest
from context import build_llm_messages, format_messages_for_digest
from session import Session, SessionError, create_new, session_history_event
from tests.isolation_helpers import temporary_agent_paths


def ids(): return str(uuid.uuid4()), 'wc-'+'a'*64


def test_reference_replay_history_and_no_model_metadata():
    with temporary_agent_paths() as paths:
        s=create_new(paths); s.append_message({'role':'user','content':'hello'}); pid,cid=ids()
        assert s.append_sent_work_card_reference(pid,cid)==(1,True)
        s.append_message({'role':'assistant','content':'after'})
        assert s.append_sent_work_card_reference(pid,cid)==(1,False)
        with pytest.raises(SessionError): s.append_sent_work_card_reference(pid,'wc-'+'b'*64)
        loaded=Session.load(paths,s.conversation_id); history=session_history_event(loaded)['items']
        assert [x['role'] for x in history]==['user','work_card','assistant']
        assert history[1]['type']=='work_card_reference' and history[1]['card_id']==cid and history[1]['presentation_id']==pid
        raw=s.messages_path.read_text(encoding='utf-8'); assert raw.count(pid)==1
        llm=json.dumps(build_llm_messages(loaded),ensure_ascii=False)
        assert pid not in llm and cid not in llm and 'work_card_presentation' not in llm
        digest=format_messages_for_digest(loaded.messages)
        assert pid not in digest and cid not in digest


def test_reference_window_deferred_and_only_recorded():
    with temporary_agent_paths() as paths:
        s=create_new(paths); pid,cid=ids(); s.append_sent_work_card_reference(pid,cid)
        for i in range(205):s.append_message({'role':'assistant','content':str(i)})
        deferred=Session.load(paths,s.conversation_id,message_cap=0)
        event=session_history_event(deferred)
        assert len(event['items'])==200 and event['omitted_count']==6
        assert all(x.get('card_id')!=cid for x in event['items'])


def test_stale_corrupt_and_unsaved_refuse():
    with temporary_agent_paths() as paths:
        first=create_new(paths); second=Session.load(paths,first.conversation_id); pid,cid=ids()
        first.append_message({'role':'user','content':'A'})
        with pytest.raises(SessionError): second.append_sent_work_card_reference(pid,cid)
        first.append_message({'role':'user','content':'unsaved'},persist=False)
        with pytest.raises(SessionError): first.append_sent_work_card_reference(pid,cid)
        assert first.messages[-1]['content']=='unsaved'
        with first.messages_path.open('ab') as f:f.write(b'{broken')
        corrupt=Session.load(paths,first.conversation_id)
        with pytest.raises(SessionError): corrupt.append_sent_work_card_reference(pid,cid)


def test_concurrent_same_id_at_most_one_durable():
    with temporary_agent_paths() as paths:
        first=create_new(paths); second=Session.load(paths,first.conversation_id); pid,cid=ids(); barrier=threading.Barrier(2); outcomes=[]
        def run(s):
            barrier.wait()
            try: outcomes.append(s.append_sent_work_card_reference(pid,cid))
            except SessionError: outcomes.append('stale')
        ts=[threading.Thread(target=run,args=(s,)) for s in (first,second)]
        for t in ts:t.start()
        for t in ts:t.join(5)
        assert len(outcomes)==2 and (0,True) in outcomes
        assert first.messages_path.read_text(encoding='utf-8').count(pid)==1


def test_tool_protocol_and_invalid_ids():
    with temporary_agent_paths() as paths:
        s=create_new(paths); pid,cid=ids()
        for bad in ('',pid.upper(),'bad'):
            with pytest.raises(SessionError):s.append_sent_work_card_reference(bad,cid)
        with pytest.raises(SessionError):s.append_sent_work_card_reference(pid,'bad')
        s.append_message({'role':'assistant','content':None,'tool_calls':[{'id':'call1','type':'function','function':{'name':'read_file','arguments':'{}'}}]})
        with pytest.raises(SessionError):s.append_sent_work_card_reference(pid,cid)
        s.append_message({'role':'tool','tool_call_id':'call1','content':'ok'})
        s.append_sent_work_card_reference(pid,cid)
        llm=build_llm_messages(Session.load(paths,s.conversation_id))
        assert [m['role'] for m in llm]==['assistant','tool']


def test_unsaved_or_corrupt_references_are_not_projected():
    with temporary_agent_paths() as paths:
        s=create_new(paths); pid,cid=ids()
        fake={'role':'work_card_presentation','_work_card_reference':1,'presentation_id':pid,'card_id':cid}
        s.append_message(fake,persist=False)
        assert all(x.get('card_id')!=cid for x in session_history_event(s)['items'])
        s=Session.load(paths,s.conversation_id); s.append_sent_work_card_reference(pid,cid)
        with s.messages_path.open('ab') as f:f.write(b'{bad}\n')
        assert all(x.get('card_id')!=cid for x in session_history_event(s)['items'])
        lazy=Session.load(paths,s.conversation_id,message_cap=0)
        assert all(x.get('card_id')!=cid for x in session_history_event(lazy)['items'])


def test_malformed_internal_markers_do_not_reach_model_or_digest():
    with temporary_agent_paths() as paths:
        s=create_new(paths); secret='PRIVATE_MARKER_VALUE'
        s.messages.append({'role':'assistant','content':secret,'_work_card_reference':1})
        assert secret not in json.dumps(build_llm_messages(s))
        assert secret not in format_messages_for_digest(s.messages)

def test_run_bound_reference_history_hides_host_run_identity():
    with temporary_agent_paths() as paths:
        s=create_new(paths); pid,cid=ids(); run_id=str(uuid.uuid4())
        assert s.append_sent_work_card_reference(pid,cid,run_id=run_id)==(0,True)
        assert s.append_sent_work_card_reference(pid,cid,run_id=run_id)==(0,False)
        with pytest.raises(SessionError):
            s.append_sent_work_card_reference(pid,cid,run_id=str(uuid.uuid4()))
        loaded=Session.load(paths,s.conversation_id)
        history=session_history_event(loaded)['items']
        ref=next(x for x in history if x.get('type')=='work_card_reference')
        assert ref['presentation_id']==pid and ref['card_id']==cid
        assert 'run_id' not in ref
        stored=json.loads(s.messages_path.read_text(encoding='utf-8').splitlines()[0])
        assert stored['run_id']==run_id