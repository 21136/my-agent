from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from self_evolution_auto import AutoEvolution, AutoPolicy


def paths(tmp_path):
    root = tmp_path / "my-agent-main" / ".worktrees" / "sandbox"
    root.mkdir(parents=True)
    target = tmp_path / "my-agent-auto-target"
    target.mkdir()
    file = tmp_path / "auto-policy.json"
    return root, target, file


def payload(target):
    return {
        "schema": "self-evolution-auto.v1",
        "target_root": str(target),
        "model_id": "configured-test-model",
        "test_image": "sha256:" + "1" * 64,
        "editable_files": ["agent-core/behavior.py"],
        "verification_files": ["agent-core/behavior.py", "agent-core/tests/test_behavior.py"],
        "tests": ["agent-core/tests/test_behavior.py"],
        "max_attempts": 3,
        "test_timeout_sec": 45,
    }


def policy(tmp_path, **override):
    root, target, file = paths(tmp_path)
    file.write_text(json.dumps(payload(target) | override), encoding="utf-8")
    return AutoPolicy.load(file, source_root=root)


@pytest.mark.parametrize("override", [
    {"target_root": "/unrelated"},
    {"model_id": ""},
    {"test_image": "python:latest"},
    {"editable_files": ["agent-core/tests/test_behavior.py"]},
    {"editable_files": ["agent-core/../secret.py"]},
    {"editable_files": ["agent-core/CON.py"]},
    {"verification_files": ["agent-core/behavior.py"]},
    {"verification_files": ["agent-core/behavior.py", "agent-core/tests/test_behavior.py", "agent-core/Behavior.py"]},
    {"max_attempts": True},
    {"test_timeout_sec": 900},
    {"allow_stable": True},
])
def test_policy_rejects_broad_or_ambiguous_scope(tmp_path, override):
    with pytest.raises(ValueError):
        policy(tmp_path, **override)


def test_policy_rejects_non_sandbox_and_duplicate_keys(tmp_path):
    root, target, file = paths(tmp_path)
    file.write_text(json.dumps(payload(target)), encoding="utf-8")
    with pytest.raises(ValueError, match="sandbox"):
        AutoPolicy.load(file, source_root=root.parents[1])
    file.write_text(json.dumps(payload(target))[:-1] + ',"max_attempts":2}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        AutoPolicy.load(file, source_root=root)


class Repo:
    def __init__(self):
        self.publishes = []
        self.prepares = []
        self.archives = []
        self.file = "agent-core/behavior.py"

    def head(self):
        return "base"

    def source(self, base, paths):
        assert base == "base"
        assert paths == (self.file,)
        return {self.file: "def answer():\n    return 1\n"}

    def prepare(self, base, edits):
        assert base == "base"
        self.prepares.append(edits)
        return "tree" + str(len(self.prepares))

    def archive(self, tree, paths):
        assert paths == (self.file, "agent-core/tests/test_behavior.py")
        self.archives.append(tree)
        return tree.encode()

    def publish(self, base, tree, message):
        self.publishes.append((base, tree, message))
        return "commit-auto"


class Verifier:
    def __init__(self, outcomes, *, unavailable=False):
        self.outcomes = iter(outcomes)
        self.unavailable = unavailable
        self.runs = []

    def preflight(self):
        if self.unavailable:
            raise RuntimeError("local runner unavailable")

    def run(self, archive, *, cancelled):
        assert not cancelled()
        self.runs.append(archive)
        decision = next(self.outcomes)
        return SimpleNamespace(passed=decision, summary="passes" if decision else "test expected 3")


def edit(number, *, path="agent-core/behavior.py"):
    return {"action": "edit", "message": "调整例程", "edits": [
        {"path": path, "before": "return 1", "after": f"return {number}"},
    ]}


def adapter(tmp_path, responses, outcomes, *, unavailable=False):
    repo, verifier = Repo(), Verifier(outcomes, unavailable=unavailable)
    responses = iter(responses)
    calls = []

    def complete(messages, model_id):
        assert model_id == "configured-test-model"
        calls.append(messages)
        return json.dumps(next(responses))

    service = AutoEvolution(policy(tmp_path), complete,
                            open_repo=lambda *_: repo, make_verifier=lambda _: verifier)
    return service, repo, verifier, calls


def test_semantic_route_does_not_open_repo_or_run_tests(tmp_path):
    service, repo, verifier, calls = adapter(tmp_path, [{"route": "chat"}, {"route": "evolve"}, {"route": "stable"}], [])
    for text, expected in [("我们讨论怎么改", "chat"), ("你来实现", "evolve"), ("合入稳定区", "stable")]:
        assert service.classify(text) == expected
        assert text in calls[-1][-1]["content"]
    assert not repo.archives and not verifier.runs


def test_automatic_retry_then_review_and_atomic_single_commit(tmp_path):
    svc, repo, verifier, calls = adapter(tmp_path, [edit(2), edit(3), {"decision": "accept", "reason": "满足目标"}], [True, False, True])
    result = svc.run("改成三")
    assert result.status == "published" and result.commit == "commit-auto"
    assert verifier.runs == [b"base", b"tree1", b"tree2"]
    assert [(base, tree) for base, tree, _ in repo.publishes] == [("base", "tree2")]
    assert "test expected 3" in calls[1][-1]["content"]
    assert "return 3" in calls[-1][-1]["content"]


def test_baseline_failure_stops_before_sending_source(tmp_path):
    svc, repo, verifier, calls = adapter(tmp_path, [], [False])
    assert svc.run("修复").status == "failed"
    assert not calls and not repo.publishes and verifier.runs == [b"base"]


def test_docker_unavailable_never_runs_candidate_on_host(tmp_path):
    svc, repo, verifier, calls = adapter(tmp_path, [], [], unavailable=True)
    assert svc.run("修复").status == "unavailable"
    assert not calls and not repo.publishes and not verifier.runs


def test_bad_edit_and_negative_review_never_publish(tmp_path):
    svc, repo, verifier, _ = adapter(tmp_path, [edit(7, path="../agent.py"), edit(8),
        {"decision": "stop", "reason": "风险太高"}], [True, True])
    assert svc.run("修复").status == "blocked"
    assert not repo.publishes and verifier.runs == [b"base", b"tree1"]


def test_stop_after_review_cancellation_cannot_publish(tmp_path):
    svc, repo, _, calls = adapter(tmp_path, [edit(3), {"decision": "accept", "reason": "ok"}], [True, True])
    result = svc.run("改成三", cancelled=lambda: len(calls) >= 2)
    assert result.status == "cancelled" and not repo.publishes


def test_no_change_is_not_reported_as_commit(tmp_path):
    svc, repo, verifier, _ = adapter(tmp_path, [{"action": "stop", "message": "目标没说清楚", "edits": []}], [True])
    assert svc.run("看看").status == "no_change"
    assert not repo.publishes and verifier.runs == [b"base"]
