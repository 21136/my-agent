from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from context import (ContextConfig, build_llm_messages, build_llm_messages_with_optional_trim,
                     compact_context, format_messages_for_digest, summarize_messages_for_digest)
from paths import AgentPaths
from session import Session, SessionMeta


SECRET = 'SYNTHETIC_HOST_ONLY_TARGET_781'


def make_session(tmp_path):
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path/'workspace', data=tmp_path/'data', evolve=tmp_path/'evolve')
    directory = paths.data/'sessions'/'synthetic-host-metadata'
    directory.mkdir(parents=True)
    messages = [
        {'role': 'user', 'content': 'hello user', '_host_internal': {'request_id': SECRET, 'reply_target': {'result_id': SECRET}}},
        {'role': 'assistant', 'content': None, 'tool_calls': [{'id':'call-1','type':'function','function':{'name':'synthetic_tool','arguments':'{"value":1}'}}], '_host_internal': {'opaque': SECRET}},
        {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'tool output', '_host_internal': {'opaque': SECRET}},
        {'role': 'assistant', 'content': 'done'},
    ]
    session = Session(conversation_id='synthetic-host-metadata', session_dir=directory, goal='synthetic goal',
                      meta=SessionMeta(llm_model='deepseek-v4-flash'), messages=messages, paths=paths)
    return session


class CaptureLLM:
    def __init__(self): self.calls = []
    def chat(self, messages, **kwargs):
        self.calls.append((copy.deepcopy(messages), kwargs))
        return SimpleNamespace(content='synthetic digest')


def test_model_projection_strips_reserved_namespace_preserves_content_and_tool_exchange(tmp_path):
    session = make_session(tmp_path)
    before = copy.deepcopy(session.messages)
    projected = build_llm_messages(session)
    assert SECRET not in json.dumps(projected, ensure_ascii=False)
    assert all('_host_internal' not in row for row in projected)
    assert projected[0] == {'role':'user', 'content':'hello user'}
    assert projected[1]['tool_calls'][0]['id'] == 'call-1'
    assert json.loads(projected[1]['tool_calls'][0]['function']['arguments']) == {'value': 1}
    assert projected[2] == {'role':'tool', 'tool_call_id':'call-1', 'content':'tool output'}
    assert session.messages == before
    assert SECRET not in json.dumps(build_llm_messages(session, include_images=True), ensure_ascii=False)
    trimmed, _ = build_llm_messages_with_optional_trim(session, '', 'deepseek-v4-flash', force_trim=True)
    assert SECRET not in json.dumps(trimmed, ensure_ascii=False)


def test_summarize_and_compact_never_send_host_namespace(tmp_path):
    session = make_session(tmp_path)
    session.messages += [{'role':'user','content':f'turn-{n}', '_host_internal': {'secret':SECRET}} for n in range(8)]
    session.save()
    llm = CaptureLLM()
    assert SECRET not in format_messages_for_digest(session.messages)
    summarize_messages_for_digest(session, session.messages, llm)
    assert 'hello user' in json.dumps(llm.calls[-1][0], ensure_ascii=False)
    assert SECRET not in json.dumps(llm.calls[-1][0], ensure_ascii=False)
    result = compact_context(session, llm, force=True, config=ContextConfig(0.8, 2, 8000))
    assert result.compacted and len(llm.calls) >= 2
    assert SECRET not in json.dumps(llm.calls, ensure_ascii=False)
    assert SECRET not in session.digest_path.read_text(encoding='utf-8')
    assert any('_host_internal' in row for row in session.messages)


def test_namespace_only_is_reserved_and_legacy_user_content_is_preserved(tmp_path):
    session = make_session(tmp_path)
    text = 'Literal _host_internal and a user-authored value must remain.'
    session.messages = [{'role': 'user', 'content': text, 'legacy_field': 'retained'}]
    assert build_llm_messages(session) == [{'role': 'user', 'content': text, 'legacy_field': 'retained'}]
