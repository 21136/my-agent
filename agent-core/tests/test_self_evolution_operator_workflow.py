from types import SimpleNamespace

import pytest

from self_evolution_auto import AutoEvolution, AutoEvolutionError, AutoResult, PendingEvolution


def native(name=None, *, content=None, args='{}'):
    return SimpleNamespace(content=content, tool_calls=[] if name is None else [
        {'type': 'function', 'function': {'name': name, 'arguments': args}},
    ])


def worker(receipts, completion, *, scope='one', baseline=None, fail=False):
    repo = object()
    promoted = []
    def promote(actual, candidate, *, cancelled):
        assert actual is repo and not cancelled()
        promoted.append(candidate)
        return AutoResult('blocked', 'stable drift') if fail else AutoResult('promoted', 'stable updated', 'b'*40)
    instance = AutoEvolution(SimpleNamespace(model_id='0x567-flash'), lambda *_: pytest.fail('native expected'),
        complete_tools=completion, stable_baseline=baseline, stable_promoter=SimpleNamespace(promote=promote),
        pending=receipts, session_id=scope, open_repo=lambda _p: repo)
    instance._run = lambda _goal, _cancelled: AutoResult('published', 'experiment ready', 'a'*40)
    return instance, promoted


def test_native_promotion_intent_can_be_combined_but_bare_labels_cannot_grant_stable():
    messages = []
    def complete(prompt, tools):
        assert {t['function']['name'] for t in tools} == {'evolve_and_promote', 'promote_last_candidate'}
        messages.append(prompt)
        return native('evolve_and_promote')
    auto, _ = worker(PendingEvolution(), complete)
    assert auto.classify('修好，测试通过后合入稳定区') == 'evolve_and_promote'
    auto._complete_tools = lambda *_: native(content='{"route":"stable"}')
    assert auto.classify('能否直接改稳定区？') == 'stable'
    auto._complete_tools = lambda *_: native(content='{"route":"evolve_and_promote"}')
    with pytest.raises(AutoEvolutionError):
        auto.classify('聊天')
    auto._complete_tools = lambda *_: native('promote_last_candidate', args='{"commit":"foreign"}')
    with pytest.raises(AutoEvolutionError):
        auto.classify('合入之前别人做的东西')


def test_latest_candidate_is_session_bound_and_survives_worker_instances_but_not_failures():
    receipts = PendingEvolution()
    first, _ = worker(receipts, lambda *_: native('promote_last_candidate'))
    assert first.run('只改实验区').commit == 'a'*40
    other, calls = worker(receipts, lambda *_: native(), scope='other')
    assert other.promote_last_candidate().status == 'blocked' and not calls
    second, calls = worker(receipts, lambda *_: native('promote_last_candidate'))
    assert second.classify('把刚才的改动合入稳定') == 'promote'
    result = second.promote_last_candidate()
    assert result.status == 'promoted' and result.commit == 'a'*40 and result.stable_commit == 'b'*40
    assert calls == ['a'*40]
    assert second.promote_last_candidate().status == 'blocked' and calls == ['a'*40]


def test_combined_workflow_keeps_experiment_candidate_when_promotion_fails():
    receipts = PendingEvolution()
    combined, calls = worker(receipts, lambda *_: native('evolve_and_promote'), fail=True)
    assert combined.classify('修好然后合入稳定') == 'evolve_and_promote'
    result = combined.run_and_promote('修好然后合入稳定')
    assert result.status == 'blocked' and result.commit == 'a'*40 and result.stable_commit is None
    assert calls == ['a'*40]
    retry, calls = worker(receipts, lambda *_: native('promote_last_candidate'))
    assert retry.classify('重试合入本会话候选') == 'promote'
    assert retry.promote_last_candidate().status == 'promoted' and calls == ['a'*40]


def test_failed_new_edit_cannot_promote_previous_candidate_under_new_request():
    receipts = PendingEvolution()
    auto, calls = worker(receipts, lambda *_: native('evolve_and_promote'))
    auto.run('之前的修改')
    auto._run = lambda *_: AutoResult('failed', 'candidate verification failed')
    assert auto.classify('另一个修改，做完合入') == 'evolve_and_promote'
    assert auto.run_and_promote('另一个修改，做完合入').status == 'failed' and not calls


def test_cancelled_stable_request_does_not_publish_pending_candidate():
    auto, calls = worker(PendingEvolution(), lambda *_: native('promote_last_candidate'))
    auto.run('改实验区')
    assert auto.classify('把这次候选合入稳定') == 'promote'
    result = auto.promote_last_candidate(cancelled=lambda: True)
    assert result.status == 'cancelled' and not calls


def test_refresh_happens_before_reading_editable_source():
    log = []
    policy = SimpleNamespace(model_id='0x567-flash', editable_files=('agent-core/f.py',))
    def preflight(): log.append('preflight')
    def refresh(_repo, _verifier, *, cancelled):
        assert not cancelled()
        log.append('refresh')
        return 'fresh'
    def source(base, _paths):
        log.append('source')
        assert base == 'fresh'
        return {}
    repo = SimpleNamespace(source=source, head=lambda: pytest.fail('ordinary head lookup missed stable check'))
    auto = AutoEvolution(policy, lambda *_: pytest.fail('model should not read unavailable sources'),
        stable_baseline=SimpleNamespace(refresh=refresh), open_repo=lambda _p: repo,
        make_verifier=lambda _p: SimpleNamespace(preflight=preflight))
    assert auto.run('修实验区').status == 'blocked'
    assert log == ['preflight', 'refresh', 'source']


def test_model_authorization_for_last_candidate_cannot_follow_a_changed_receipt():
    receipts = PendingEvolution()
    auto, calls = worker(receipts, lambda *_: native('promote_last_candidate'))
    auto.run('第一次')
    assert auto.classify('把刚才的候选合入稳定') == 'promote'
    receipts.record('one', 'c'*40, 'another connection replaced the candidate')
    assert auto.promote_last_candidate().status == 'blocked'
    assert not calls


def test_semantic_resolver_cannot_overwrite_unconflicted_context():
    import json
    from self_evolution_selected_sync import resolve_scoped_overlap
    seen=[]
    draft=(b'HEAD\n<<<<<<<<<<<<<<<< SYNC_TARGET\nours\n|||||||||||||||| SYNC_BASE\nold\n'
           b'================\ntheirs\n>>>>>>>>>>>>>>>> SYNC_SOURCE\nTAIL\n')
    def respond(messages, tools):
        seen.append(json.loads(messages[-1]['content']))
        assert [tool['function']['name'] for tool in tools]==['sync_resolve_conflicts']
        response=native('sync_resolve_conflicts', args=json.dumps({'patches':[{'id':'c1','replacement':'keep both'}]}))
        response.finish_reason='tool_calls'
        response.tool_calls[0]['id']='r-1'
        return response
    assert resolve_scoped_overlap('agent-core/f.py',draft,respond)==b'HEAD\nkeep both\nTAIL\n'
    assert 'HEAD' not in json.dumps(seen) and 'TAIL' not in json.dumps(seen)
    assert resolve_scoped_overlap('agent-core/tests/test_f.py',draft,respond) is None
    assert len(seen)==1
