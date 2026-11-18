from types import SimpleNamespace
import json

import pytest

from self_evolution_selected_sync import SelectedSync, SyncProfile, SyncPolicy, split_draft


def _reply(name, args, serial):
    return SimpleNamespace(finish_reason="tool_calls", tool_calls=[{
        "type": "function", "id": f"c{serial}", "function": {"name": name, "arguments": json.dumps(args)},
    }])


def test_diff3_only_replaces_one_conflict_and_preserves_clean_sections():
    draft = (b"before\r\n<<<<<<<<<<<<<<<< SYNC_TARGET\r\nours\r\n|||||||||||||||| SYNC_BASE\r\n"
             b"old\r\n================\r\ntheirs\r\n>>>>>>>>>>>>>>>> SYNC_SOURCE\r\nafter\r\n")
    parts, conflicts = split_draft("agent-core/session.py", draft, has_conflicts=True)
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert (conflict.target, conflict.base, conflict.source) == ("ours\n", "old\n", "theirs\n")
    assert parts == ("before\n", conflict, "after\n")
    with pytest.raises(ValueError):
        split_draft("a", b"<<<<<<<<<<<<<<<< SYNC_TARGET\n", has_conflicts=True)


def test_only_provider_tool_calls_select_and_finish_snapshot(tmp_path):
    path, test = "agent-core/session.py", "agent-core/tests/test_session.py"
    profile = SyncProfile("a" * 40, (path,), (), (test,), (test,))
    policy = SyncPolicy(tmp_path, "refs/heads/stable", "sha256:" + "a" * 64,
                        False, tmp_path / "candidates", (profile,))
    snapshot = SimpleNamespace(target_head="b" * 40, source_head="a" * 40, digest="c" * 64,
                               verification={test: b"test"}, files={path: SimpleNamespace(
                                   target=b"old\n", merged=b"new\n", has_conflicts=False,
                               )})
    events = []

    def complete(messages, tools):
        assert "sync_finalize" in {x["function"]["name"] for x in tools}
        if not events:
            events.append("choose")
            return _reply("sync_inspect", {"commit": profile.commit}, 1)
        assert messages[-1]["role"] == "tool"
        return _reply("sync_finalize", {}, 2)

    def capture(*args, **kwargs):
        return snapshot

    def verify(baseline, candidate, *, cancelled):
        assert not cancelled() and baseline[path] == b"old\n" and candidate[path] == b"new\n"
        assert baseline[test] == candidate[test] == b"test"
        events.append("verified")
        return SimpleNamespace(passed=True)

    def publish(*_args, **_kwargs):
        pytest.fail("operator has not authorized applying")

    worker = SelectedSync(policy, complete, capture=capture, verifier_factory=lambda _: SimpleNamespace(verify=verify),
                          publish=publish)
    assert worker.run("把这次会话修复同步到实验区").status == "candidate"
    assert events == ["choose", "verified"]
    assert len(list(policy.store.glob("*.json"))) == 1


def test_model_claim_without_tool_never_captures_or_publishes(tmp_path):
    profile = SyncProfile("a" * 40, ("agent-core/session.py",), (), ("test.py",), ("test.py",))
    policy = SyncPolicy(tmp_path, "refs/heads/stable", "sha256:" + "a" * 64,
                        True, tmp_path / "candidates", (profile,))
    unexpected = lambda *a, **k: pytest.fail("model prose cannot grant authority")
    worker = SelectedSync(policy, lambda *_: SimpleNamespace(finish_reason="stop", tool_calls=[], content="已发布"),
                          capture=unexpected, publish=unexpected)
    assert worker.run("你看一下能不能同步").status == "blocked"


def test_only_observed_conflict_blocks_can_be_applied_and_verification_runs_first(tmp_path):
    path = "agent-core/session.py"
    check = "agent-core/tests/test_session.py"
    profile = SyncProfile("a" * 40, (path,), (check,), (check,), (check,))
    policy = SyncPolicy(tmp_path, "refs/heads/stable", "sha256:" + "a" * 64, True, tmp_path / "candidates", (profile,))
    fixture = b"HEAD\n<<<<<<<<<<<<<<<< SYNC_TARGET\nOURS\n|||||||||||||||| SYNC_BASE\nBASE\n================\nTHEIRS\n>>>>>>>>>>>>>>>> SYNC_SOURCE\nTAIL\n"
    snapshot = SimpleNamespace(target_head="b" * 40, source_head="a" * 40, digest="c" * 64,
                               verification={check: b"test"}, files={path: SimpleNamespace(
                                   target=b"HEAD\nOURS\nTAIL\n", merged=fixture, has_conflicts=True,
                               )})
    sequence = iter([
        _reply("sync_inspect", {"commit": profile.commit}, 1),
        _reply("sync_resolve_conflicts", {"patches": [{"id": "c1", "replacement": "premature"}]}, 2),
        _reply("sync_read_conflict", {"id": "*"}, 3),
        _reply("sync_resolve_conflicts", {"patches": [{"id": "c1", "replacement": "OURS + THEIRS"}]}, 4),
        _reply("sync_finalize", {}, 5),
    ])
    stages = []

    def complete(messages, tools):
        result = next(sequence)
        if result.tool_calls[0]["id"] == "c3":
            assert json.loads(messages[-1]["content"])["status"] == "invalid_or_unobserved_conflict"
        return result

    def verify(baseline, candidate, *, cancelled):
        assert candidate[path] == b"HEAD\nOURS + THEIRS\nTAIL\n"
        stages.append("verify")
        return SimpleNamespace(passed=True)

    def publish(actual, updates, *, cancelled):
        assert stages == ["verify"] and actual is snapshot and not cancelled()
        assert updates == {path: b"HEAD\nOURS + THEIRS\nTAIL\n"}
        stages.append("publish")
        return SimpleNamespace(published=True)

    worker = SelectedSync(policy, complete, capture=lambda *a, **k: snapshot,
                          verifier_factory=lambda _: SimpleNamespace(verify=verify), publish=publish)
    assert worker.run("同步本次提交").status == "published"
    assert stages == ["verify", "publish"]


def test_failed_candidate_can_redo_only_the_already_observed_blocks(tmp_path):
    path, check = 'agent-core/session.py', 'agent-core/tests/test_session.py'
    profile = SyncProfile('a'*40, (path,), (check,), (check,), (check,), description='Preserve session writes')
    policy = SyncPolicy(tmp_path, 'refs/heads/stable', 'sha256:'+'a'*64, True, tmp_path/'candidates', (profile,))
    diff3 = b'HEAD\n<<<<<<<<<<<<<<<< SYNC_TARGET\nours\n|||||||||||||||| SYNC_BASE\nbase\n================\ntheirs\n>>>>>>>>>>>>>>>> SYNC_SOURCE\nTAIL\n'
    snapshot = SimpleNamespace(target_head='b'*40, source_head='a'*40, digest='c'*64,
        verification={check:b'trusted'}, files={path:SimpleNamespace(target=b'HEAD\nours\nTAIL\n', merged=diff3, has_conflicts=True)})
    replies = iter([_reply('sync_inspect', {'commit':profile.commit}, 1), _reply('sync_read_conflict', {'id':'*'}, 2),
        _reply('sync_resolve_conflicts', {'patches':[{'id':'c1','replacement':'incomplete'}]}, 3),
        _reply('sync_finalize', {}, 4), _reply('sync_resolve_conflicts', {'patches':[{'id':'c1','replacement':'complete'}]}, 5),
        _reply('sync_finalize', {}, 6)])
    def complete(messages, tools):
        assert 'Preserve session writes' in messages[1]['content']
        response = next(replies)
        if response.tool_calls[0]['id']=='c5':
            data=json.loads(messages[-1]['content'])
            assert data['status']=='candidate_checks_failed' and data['diagnostics']==['AttributeError: missing helper']
        return response
    def verify(baseline, candidate, *, cancelled):
        return (SimpleNamespace(passed=True) if candidate[path]==b'HEAD\ncomplete\nTAIL\n' else
                SimpleNamespace(passed=False, stage='candidate', diagnostics=('AttributeError: missing helper',)))
    def publish(actual, updates, *, cancelled):
        assert updates[path]==b'HEAD\ncomplete\nTAIL\n'
        return SimpleNamespace(published=True)
    service = SelectedSync(policy, complete, capture=lambda *_a, **_kw:snapshot,
                    verifier_factory=lambda _:SimpleNamespace(verify=verify), publish=publish)
    assert service.run('同步修复').status=='published'
    assert len(list(policy.store.glob('*.json')))==2
