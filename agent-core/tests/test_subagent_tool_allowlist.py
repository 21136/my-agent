import json
from types import SimpleNamespace
from unittest.mock import patch
import pytest
import subagent
from llm_client import LLMResponse
from tools.schema import tool_ok


class FakeLLM:
    def __init__(self, names): self.names=names; self.messages=None; self.tools=None
    def chat(self, messages, **kw):
        if self.messages is not None:
            self.messages=list(messages)
            return LLMResponse(content='REVIEW_VERDICT: pass\nCHECKER_VERDICT: pass', tool_calls=[], model='fake', usage={}, finish_reason='stop', raw={})
        self.messages=[]; self.tools=kw['tools']
        calls=[{'id':str(i),'type':'function','function':{'name':name,'arguments':'{}'}} for i,name in enumerate(self.names)]
        return LLMResponse(content='',tool_calls=calls,model='fake',usage={},finish_reason='tool_calls',raw={})


@pytest.mark.parametrize('kind',['explore','checker','review','terminal_plan'])
def test_only_exact_schema_names_reach_executor(kind):
    names=['read_file',' read_file ','READ_FILE','run_command','fetch_url','web_search','run_evolved','spawn_subagent','unknown','list_dir','glob_file_search','grep']
    llm=FakeLLM(names); seen=[]
    executor=SimpleNamespace(session=SimpleNamespace(),run=lambda name,args: (seen.append(name),tool_ok(name,{'value':'ok'}))[1])
    session=SimpleNamespace(meta=SimpleNamespace(project_id='p',project_root='',harness='',terminal_scope_kind='',terminal_cwd='',terminal_foreign_root='',terminal_host_id=''),conversation_id='synthetic',scaffold_tool_turn=False)
    runner=subagent.SubagentRunner(paths=SimpleNamespace())
    reg=SimpleNamespace(get_evolved=lambda name:None)
    defs=lambda names:[{'type':'function','function':{'name':name}} for name in names]
    with patch.object(subagent.ToolRegistry,'load',return_value=reg), patch.object(subagent.ToolExecutor,'create',return_value=executor), \
         patch.object(subagent,'build_explore_tools',return_value=defs(['read_file','list_dir','glob_file_search','grep','web_search','fetch_url'])), \
         patch.object(subagent,'build_checker_tools',return_value=defs(['read_file','list_dir','glob_file_search','grep'])), \
         patch.object(subagent,'build_review_tools',return_value=defs(['read_file','list_dir','glob_file_search','grep'])), \
         patch.object(subagent,'build_hard_checklist',return_value=[]), \
         patch.object(subagent,'_explore_system_prompt',return_value='prompt'), \
         patch.object(subagent,'_checker_system_prompt',return_value='prompt'), \
         patch.object(subagent,'_review_system_prompt',return_value='prompt'), \
         patch.object(subagent,'_terminal_planner_system_prompt',return_value='prompt'), \
         patch.object(subagent,'_build_review_plan_slice',return_value=''), \
         patch('llm_routing.resolve_model_id_for_role',return_value='fake'):
        if kind=='explore': runner.run_explore('task',session=session,llm=llm,max_rounds=2)
        elif kind=='checker': runner.run_checker(subagent.CheckerTask(tool_name='test'),session=session,llm=llm,max_rounds=2)
        elif kind=='review': runner.run_deliverable_review('task',session=session,llm=llm,max_rounds=2)
        else: runner.run_terminal_plan('task',session=session,llm=llm,max_rounds=2)
    expected=(['read_file','fetch_url','web_search'] if kind in ('explore','terminal_plan') else ['read_file']) + ['list_dir','glob_file_search','grep']
    assert seen==expected
    results=[m for m in llm.messages if m['role']=='tool']
    assert len(results)==len(names)
    assert json.loads(results[0]['content'])['ok'] is True
    assert all(json.loads(results[i]['content'])['ok'] is False for i in (1,2,3,6,7,8))

