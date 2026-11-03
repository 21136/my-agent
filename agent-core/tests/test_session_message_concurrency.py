import os
import subprocess
import sys
import threading
import time
import pytest
from session import Session, SessionError, create_new
from tests.isolation_helpers import temporary_agent_paths


def test_stale_snapshot_cannot_overwrite_appended_message():
    with temporary_agent_paths() as paths:
        first=create_new(paths); second=Session.load(paths,first.conversation_id)
        first.append_message({'role':'user','content':'first'})
        second.messages.append({'role':'user','content':'stale'})
        with pytest.raises(SessionError): second.save()
        assert [m['content'] for m in Session.load(paths,first.conversation_id).messages]==['first']


def test_concurrent_append_never_loses_or_silently_overwrites():
    with temporary_agent_paths() as paths:
        first=create_new(paths); second=Session.load(paths,first.conversation_id)
        barrier=threading.Barrier(2); results=[]
        def run(session,value):
            barrier.wait()
            try: session.append_message({'role':'user','content':value}); results.append(('ok',value))
            except SessionError: results.append(('stale',value))
        threads=[threading.Thread(target=run,args=(s,v)) for s,v in [(first,'A'),(second,'B')]]
        for t in threads:t.start()
        for t in threads:t.join(5)
        assert all(not t.is_alive() for t in threads)
        got=[m['content'] for m in Session.load(paths,first.conversation_id).messages]
        assert sorted(got)==sorted(v for status,v in results if status=='ok')
        assert len(results)==2 and len(got)>=1


def test_corrupt_tail_never_silently_overwritten_or_appended():
    with temporary_agent_paths() as paths:
        first=create_new(paths); first.append_message({'role':'user','content':'safe'})
        with first.messages_path.open('ab') as f:f.write(b'{"role":')
        broken=first.messages_path.read_bytes()
        loaded=Session.load(paths,first.conversation_id)
        assert loaded.corruption_notices
        with pytest.raises(SessionError): loaded.append_message({'role':'user','content':'next'})
        with pytest.raises(SessionError): loaded.save()
        assert first.messages_path.read_bytes()==broken


def test_legacy_repair_under_lock_keeps_valid_messages():
    with temporary_agent_paths() as paths:
        first=create_new(paths)
        first.messages_path.write_text('{"role":"user","content":"kept"}\n{"role":"tool","tool_call_id":"missing","content":"orphan"}\n',encoding='utf-8')
        session=Session.load(paths,first.conversation_id)
        assert session.messages==[{'role':'user','content':'kept'}]
        assert 'orphan' not in first.messages_path.read_text(encoding='utf-8')


@pytest.mark.skipif(os.name!='nt',reason='Windows byte-range lock')
def test_windows_cross_process_lock(tmp_path):
    from session import _session_messages_lock
    lock=tmp_path; marker=tmp_path/'acquired'; ready=tmp_path/'ready'
    code='''import sys,time\nfrom pathlib import Path\nfrom session import _session_messages_lock\nlock,ready,marker=map(Path,sys.argv[1:])\nready.write_text("ready")\nwith _session_messages_lock(lock): marker.write_text("acquired")\n'''
    with _session_messages_lock(lock):
        p=subprocess.Popen([sys.executable,'-c',code,str(lock),str(ready),str(marker)],cwd=str(__import__('pathlib').Path(__file__).resolve().parents[1]))
        deadline=time.monotonic()+3
        while not ready.exists() and time.monotonic()<deadline: time.sleep(.01)
        assert ready.exists() and not marker.exists()
        time.sleep(.2); assert not marker.exists() and p.poll() is None
    assert p.wait(timeout=5)==0 and marker.exists()


def test_deferred_load_uses_latest_and_then_detects_stale_save():
    with temporary_agent_paths() as paths:
        first=create_new(paths); deferred=Session.load(paths,first.conversation_id,message_cap=0)
        first.append_message({'role':'user','content':'first'})
        deferred.ensure_messages_loaded()
        assert deferred.messages==[{'role':'user','content':'first'}]
        first.append_message({'role':'user','content':'second'})
        with pytest.raises(SessionError): deferred.save()
        assert len(Session.load(paths,first.conversation_id).messages)==2


def manual_from(session, messages):
    return Session(conversation_id=session.conversation_id,session_dir=session.session_dir,
                   goal=session.goal,meta=session.meta,messages=messages,paths=session.paths)


def test_manual_matching_existing_can_save_and_append():
    with temporary_agent_paths() as paths:
        first=create_new(paths); first.append_message({'role':'user','content':'old'})
        manual=manual_from(first,[{'role':'user','content':'old'}])
        manual.save(); manual.append_message({'role':'assistant','content':'new'})
        assert [m['content'] for m in Session.load(paths,first.conversation_id).messages]==['old','new']


def test_manual_mismatch_unsaved_or_damaged_never_authorized():
    with temporary_agent_paths() as paths:
        first=create_new(paths); first.append_message({'role':'user','content':'old'})
        for messages in ([], [{'role':'user','content':'different'}]):
            manual=manual_from(first,messages)
            with pytest.raises(SessionError): manual.save()
            with pytest.raises(SessionError): manual.append_message({'role':'user','content':'new'})
        manual=manual_from(first,[{'role':'user','content':'old'}])
        manual.append_message({'role':'user','content':'unsaved'},persist=False)
        with pytest.raises(SessionError): manual.save()
        assert manual.messages[-1]['content']=='unsaved'
        with first.messages_path.open('ab') as f: f.write(b'{invalid}\n')
        damaged=first.messages_path.read_bytes(); manual=manual_from(first,[{'role':'user','content':'old'}])
        with pytest.raises(SessionError): manual.save()
        assert first.messages_path.read_bytes()==damaged


def test_manual_matching_incomplete_line_refused():
    with temporary_agent_paths() as paths:
        first=create_new(paths); first.messages_path.write_text('{"role":"user","content":"old"}',encoding='utf-8')
        manual=manual_from(first,[{'role':'user','content':'old'}])
        with pytest.raises(SessionError): manual.save()


def test_manual_concurrent_initialization_fails_closed():
    with temporary_agent_paths() as paths:
        first=create_new(paths); first.append_message({'role':'user','content':'old'})
        one=manual_from(first,list(first.messages)); two=manual_from(first,list(first.messages)); barrier=threading.Barrier(2); outcomes=[]
        def run(obj,value):
            barrier.wait()
            try: obj.append_message({'role':'user','content':value}); outcomes.append('ok')
            except SessionError: outcomes.append('stale')
        threads=[threading.Thread(target=run,args=(obj,value)) for obj,value in ((one,'A'),(two,'B'))]
        for t in threads:t.start()
        for t in threads:t.join(5)
        assert sorted(outcomes)==['ok','stale']
        assert len(Session.load(paths,first.conversation_id).messages)==2


def test_message_version_streams_without_large_memory_allocation(tmp_path):
    import hashlib
    import tracemalloc
    from session import _messages_version
    path=tmp_path/'large.jsonl'; block=b'x'*(1024*1024)
    with path.open('wb') as f:
        for _ in range(32):f.write(block)
    digest=hashlib.sha256()
    for _ in range(32):digest.update(block)
    tracemalloc.start()
    try:
        assert _messages_version(path)==digest.hexdigest()
        _,peak=tracemalloc.get_traced_memory()
    finally:tracemalloc.stop()
    assert peak < 4*1024*1024


def test_manual_validation_requires_exact_json_types_and_unique_keys():
    with temporary_agent_paths() as paths:
        first=create_new(paths)
        first.messages_path.write_text('{"role":"user","value":1}\n',encoding='utf-8')
        manual=manual_from(first,[{'role':'user','value':True}])
        with pytest.raises(SessionError): manual.save()
        first.messages_path.write_text('{"role":"user","value":1,"value":1}\n',encoding='utf-8')
        manual=manual_from(first,[{'role':'user','value':1}])
        with pytest.raises(SessionError): manual.save()


def test_untracked_persist_false_not_authorized_even_if_equal_to_disk():
    with temporary_agent_paths() as paths:
        first=create_new(paths); first.append_message({'role':'user','content':'old'})
        manual=manual_from(first,[])
        manual.append_message({'role':'user','content':'old'},persist=False)
        with pytest.raises(SessionError): manual.save()
        assert manual.messages==[{'role':'user','content':'old'}]
        assert Session.load(paths,first.conversation_id).messages==manual.messages
