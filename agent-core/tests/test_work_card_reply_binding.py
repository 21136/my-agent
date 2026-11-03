from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_work_card_ledger import make_independent_session
from context import build_llm_messages
from work_card_reply_binding import HostReplyBindingAuthority


def target(session):
    return {'session_id':session.conversation_id, 'presentation_id':str(uuid.uuid4()),
            'card_id':'wc-'+'a'*64, 'result_id':'result-1', 'requirement_id':'requirement-1'}


def test_host_binding_is_unique_for_identical_persisted_messages_and_not_sent_to_model(tmp_path):
    session, *_ = make_independent_session(tmp_path, False)
    authority = HostReplyBindingAuthority(session, lambda: session)
    expected=target(session)
    first=authority.append_bound_user_message('相同合成文本', expected)
    second=authority.append_bound_user_message('相同合成文本', expected)
    assert first is not None and second is not None and first is not second
    assert authority.validate(first) == expected and authority.validate(second) == expected
    assert authority.validate(first) == expected
    assert len([m for m in session.messages if m.get('content') == '相同合成文本']) == 2
    assert all('_host_internal' not in m for m in build_llm_messages(session))


def test_cannot_forge_binding_or_cross_session_and_client_metadata_is_not_authority(tmp_path):
    session, *_ = make_independent_session(tmp_path, False)
    current=[session]; authority=HostReplyBindingAuthority(session, lambda: current[0])
    data=target(session)
    session.append_message({'role':'user','content':'client payload','_host_internal':{'reply_target':data}})
    assert authority.validate(object()) is None
    valid=authority.append_bound_user_message('host message',data)
    assert authority.validate(valid) == data
    current[0]=object()
    assert authority.validate(valid) is None
    current[0]=session
    invalid={**data,'session_id':'other-session'}
    with pytest.raises(ValueError): authority.append_bound_user_message('cross session',invalid)


def test_failed_append_or_tampering_never_yields_valid_binding(tmp_path, monkeypatch):
    session, *_ = make_independent_session(tmp_path, False)
    authority=HostReplyBindingAuthority(session,lambda:session)
    with monkeypatch.context() as patch:
        patch.setattr(session, 'append_message', lambda *a, **kw: (_ for _ in ()).throw(OSError('synthetic failure')))
        assert authority.append_bound_user_message('cannot persist',target(session)) is None
    receipt=authority.append_bound_user_message('persisted',target(session))
    assert authority.validate(receipt) is not None
    session.messages[-1]['content']='changed in memory'
    assert authority.validate(receipt) is None


def test_no_viewed_fact_or_network_is_implicitly_created(tmp_path):
    session, store, *_ = make_independent_session(tmp_path, False)
    from work_card_ledger import WorkCardLedger
    authority=HostReplyBindingAuthority(session,lambda:session)
    receipt=authority.append_bound_user_message('synthetic reply',target(session))
    assert authority.validate(receipt) is not None
    assert WorkCardLedger(session,store).list_cards() == []


def test_append_raises_after_persist_does_not_issue_or_retry(tmp_path, monkeypatch):
    session, *_ = make_independent_session(tmp_path, False)
    authority = HostReplyBindingAuthority(session, lambda: session)
    original = session.append_message
    def wrote_then_failed(message):
        original(message)
        raise OSError('simulated ambiguous append')
    monkeypatch.setattr(session, 'append_message', wrote_then_failed)
    assert authority.append_bound_user_message('uncertain reply', target(session)) is None
    assert len([m for m in session.messages if m.get('content') == 'uncertain reply']) == 1


def test_binding_cannot_be_reconstructed_after_restart_or_different_authority(tmp_path):
    session, *_ = make_independent_session(tmp_path, False)
    first = HostReplyBindingAuthority(session, lambda: session)
    expected = target(session)
    capability = first.append_bound_user_message('test reply', expected)
    second = HostReplyBindingAuthority(session, lambda: session)
    assert first.validate(capability) == expected
    assert second.validate(capability) is None
    expected['result_id'] = 'mutated-client-value'
    assert first.validate(capability)['result_id'] == 'result-1'


def test_bind_existing_persisted_user_message_without_duplicate_append_and_consume_once(tmp_path):
    session,*_=make_independent_session(tmp_path,False)
    authority=HostReplyBindingAuthority(session,lambda:session)
    target_row=target(session);text="What does this exact result mean?"
    # A prior turn may contain byte-for-byte identical text. The ingress ticket
    # must bind only the append boundary captured for this turn, not that history.
    session.append_message({"role":"user","content":text})
    older_position=len(session.messages)-1
    ticket=authority.prepare_user_message_binding(text,target_row)
    assert ticket is not None
    before=len(session.messages)
    session.append_message({"role":"user","content":text})
    position=len(session.messages)-1
    assert position == older_position + 1
    capability=authority.bind_persisted_user_message(ticket,position)
    assert capability is not None
    assert len(session.messages)==before+1, "binding an existing message must not append a duplicate"
    assert authority.validate(capability) is None, "existing-message capability must use the one-shot consumer"
    # Normal run_turn may continue before the result is consumed; binding stays
    # pinned to the indexed user append, not whichever message is currently last.
    session.append_message({"role":"assistant","content":"continued turn"})
    assert authority.consume_persisted_user_message_binding(capability,expected_text=text)==target_row
    assert authority.consume_persisted_user_message_binding(capability,expected_text=text) is None
    assert len([m for m in session.messages if m.get("content")==text])==2


def test_existing_message_binding_rejects_wrong_text_forgery_tamper_and_session_switch(tmp_path):
    session,*_=make_independent_session(tmp_path,False)
    current=[session];authority=HostReplyBindingAuthority(session,lambda:current[0])
    target_row=target(session);text="A question about one result"
    ticket=authority.prepare_user_message_binding(text,target_row)
    assert ticket is not None
    session.append_message({"role":"user","content":"different question"})
    assert authority.bind_persisted_user_message(ticket,len(session.messages)-1) is None

    ticket=authority.prepare_user_message_binding(text,target_row)
    session.append_message({"role":"user","content":text})
    position=len(session.messages)-1
    message_capability=authority.bind_persisted_user_message(ticket,position)
    assert message_capability is not None
    session.messages[position]["content"]="tampered after semantic match"
    assert authority.consume_persisted_user_message_binding(message_capability,expected_text=text) is None
    session.messages[position]["content"]=text
    assert authority.consume_persisted_user_message_binding(message_capability,expected_text=text) is None

    ticket=authority.prepare_user_message_binding("question before switch",target_row)
    current[0]=object()
    session.append_message({"role":"user","content":"question before switch"})
    switched=authority.bind_persisted_user_message(ticket,len(session.messages)-1)
    assert switched is None
    current[0]=session
    assert authority.bind_persisted_user_message(ticket,len(session.messages)-1) is None


def test_existing_message_binding_rejects_client_metadata_and_uncertain_session_persistence(tmp_path):
    session,*_=make_independent_session(tmp_path,False);authority=HostReplyBindingAuthority(session,lambda:session)
    target_row=target(session)
    forged={"role":"user","content":"forged metadata","_host_internal":{"reply_target":target_row}}
    ticket=authority.prepare_user_message_binding("forged metadata",target_row)
    session.append_message(forged)
    assert authority.bind_persisted_user_message(ticket,len(session.messages)-1) is None

    uncertain={"role":"user","content":"unpersisted question"}
    ticket=authority.prepare_user_message_binding("unpersisted question",target_row)
    session.append_message(uncertain,persist=False)
    assert authority.bind_persisted_user_message(ticket,len(session.messages)-1) is None
