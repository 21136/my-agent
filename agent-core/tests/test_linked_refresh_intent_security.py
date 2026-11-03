import sys
from pathlib import Path
from tests.isolation_helpers import temporary_agent_paths
from session import create_new
from server import WsBridge, _build_repl
from agent import _explicit_project_card_refresh
from multi_project_work_record_bindings import ConversationWorkRecordBindings


def test_refresh_intent_requires_standalone_exact_project():
    assert _explicit_project_card_refresh('刷新项目工作卡 project-alpha') == 'project-alpha'
    for text in ('不要刷新项目工作卡 project-alpha', '刷新项目工作卡 project-alpha 并执行任务',
                 '刷新项目工作卡 project-alpha\n执行', '刷新项目工作卡', '"刷新项目工作卡 project-alpha"'):
        assert _explicit_project_card_refresh(text) is None


def test_ambiguous_project_never_authorizes_or_uses_model(monkeypatch):
    with temporary_agent_paths() as paths:
        session = create_new(paths, conversation_id='20261106-00000120')
        bridge = WsBridge(emit=lambda e: None, paths=paths)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_: None)
        repl.agent.bind_project_worker_live_context('a'*32, 1, lambda *_: True)
        monkeypatch.setattr(ConversationWorkRecordBindings, 'list_bindings', lambda self: [
            {'project_id': 'project-alpha', 'project_session_copy': {}},
            {'project_id': 'project-alpha', 'project_session_copy': {}},
        ])
        monkeypatch.setattr('multi_project_work_record_authorization.authorize_project_record_sources',
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not authorize')))
        monkeypatch.setattr(repl.agent.llm, 'chat', lambda *a, **k: (_ for _ in ()).throw(AssertionError('model called')))
        result = repl.agent.run_turn('刷新项目工作卡 project-alpha', spawn_explore=False)
        assert '多个可能的来源副本' in result.assistant_text
        assert '请先明确' in result.assistant_text and '未读取来源' in result.assistant_text


def test_plain_refresh_language_preserves_history_and_overview_boundaries():
    assert _explicit_project_card_refresh('把项目 project-alpha 的工作卡重新发一遍') == 'project-alpha'
    assert _explicit_project_card_refresh('看看项目 project-alpha 最新工作卡') == 'project-alpha'
    assert _explicit_project_card_refresh('请把项目 project-alpha 的工作卡再发一次') == 'project-alpha'
    for text in ('看看项目 project-alpha 之前的工作卡', '看看项目 project-alpha 工作概况',
                 '不要把项目 project-alpha 的工作卡重新发一遍',
                 '把项目 project-alpha 的工作卡重新发一遍并执行',
                 '看看项目 project-alpha 和 project-beta 最新工作卡',
                 '把项目 project-alpha 的工作卡重新发给张三',
                 '看看项目 project-alpha 最新工作卡并修改代码'):
        assert _explicit_project_card_refresh(text) is None


def test_multiple_project_refresh_asks_without_model_or_authorization(monkeypatch):
    with temporary_agent_paths() as paths:
        session = create_new(paths, conversation_id='20261106-00000121')
        bridge = WsBridge(emit=lambda e: None, paths=paths)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_: None)
        monkeypatch.setattr('multi_project_work_record_authorization.authorize_project_record_sources',
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not authorize')))
        monkeypatch.setattr(repl.agent.llm, 'chat', lambda *a, **k: (_ for _ in ()).throw(AssertionError('model called')))
        result = repl.agent.run_turn('看看项目 project-alpha 和 project-beta 最新工作卡', spawn_explore=False)
        assert '明确一个项目' in result.assistant_text
        assert '未读取' in result.assistant_text
