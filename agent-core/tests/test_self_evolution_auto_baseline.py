from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_evolution_auto_baseline import StableBaseline
from self_evolution_auto_git import GitExperiment

GIT_ENV = {
    **os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_AUTHOR_NAME": "Baseline Test", "GIT_AUTHOR_EMAIL": "baseline@example.invalid",
    "GIT_COMMITTER_NAME": "Baseline Test", "GIT_COMMITTER_EMAIL": "baseline@example.invalid",
}


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.decode("utf-8").strip()


def ref(cwd: Path, name: str) -> str:
    result = subprocess.run(["git", "show-ref", "--verify", "--hash", name], cwd=cwd, env=GIT_ENV,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return result.stdout.decode("ascii").strip() if result.returncode == 0 else ""


@pytest.fixture
def setup_repos(tmp_path: Path):
    stable = tmp_path / "stable"
    stable.mkdir()
    git(stable, "init", "--initial-branch=stable")
    (stable / "agent-core").mkdir()
    (stable / "agent-core/module.py").write_text("FIRST = 1\nSECOND = 1\nTHIRD = 1\nFOURTH = 1\n", encoding="utf-8")
    (stable / "agent-core/tests").mkdir()
    (stable / "agent-core/tests/test_module.py").write_text("assert True\n", encoding="utf-8")
    git(stable, "add", "agent-core")
    git(stable, "commit", "-m", "shared stable baseline")
    parent = tmp_path / "experiments"
    parent.mkdir()
    experiment = parent / "my-agent-auto-target"
    git(tmp_path, "clone", "--no-hardlinks", "--no-local", "--branch", "stable", str(stable), str(experiment))
    git(experiment, "branch", "-m", "experiment-base")
    repo = GitExperiment.open(experiment, allowed_parent=parent)
    policy = SimpleNamespace(
        editable_files=("agent-core/module.py",),
        verification_files=("agent-core/module.py", "agent-core/tests/test_module.py"),
        tests=("agent-core/tests/test_module.py",),
    )
    return stable, experiment, repo, policy


class RecordingVerifier:
    def __init__(self, passed=True, before_return=None):
        self.passed = passed
        self.before_return = before_return
        self.archives = []

    def run(self, archive: bytes, *, cancelled=lambda: False):
        self.archives.append(archive)
        if self.before_return:
            self.before_return()
        return SimpleNamespace(passed=self.passed, summary="fixed checks")


def archive_files(archive: bytes) -> dict[str, str]:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tf:
        return {item.name: tf.extractfile(item).read().decode("utf-8") for item in tf.getmembers()}


def add_experiment_candidate(repo: GitExperiment) -> str:
    base = repo.head()
    tree = repo.prepare(base, {"agent-core/module.py": "FIRST = 2\nSECOND = 1\nTHIRD = 1\nFOURTH = 1\n"})
    return repo.publish(base, tree, "candidate change")


def add_unprotected_test_edit(repo: GitExperiment, experiment: Path, text: str) -> str:
    base = repo.head()
    index = experiment / ".git" / "test-index"
    env = {**GIT_ENV, "GIT_INDEX_FILE": str(index)}
    def run(*args):
        return subprocess.run(["git", "-C", str(experiment), *args], env=env, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode().strip()
    run("read-tree", base)
    result = subprocess.run(["git", "-C", str(experiment), "hash-object", "-w", "--stdin"], env=env,
                            input=text.encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    oid = result.stdout.decode().strip()
    run("update-index", "--add", "--cacheinfo", f"100644,{oid},agent-core/tests/test_module.py")
    tree = run("write-tree")
    commit = subprocess.run(["git", "-C", str(experiment), "commit-tree", tree, "-p", base, "-m", "test-only experiment edit"],
                            env=GIT_ENV, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode().strip()
    subprocess.run(["git", "-C", str(experiment), "update-ref", "refs/heads/experiment-auto", commit], env=GIT_ENV, check=True)
    return commit


def test_unrelated_stable_drift_is_not_imported_or_anchored(setup_repos):
    stable, experiment, repo, policy = setup_repos
    before = add_experiment_candidate(repo)
    (stable / "README.md").write_text("unrelated stable documentation\n", encoding="utf-8")
    git(stable, "add", "README.md")
    git(stable, "commit", "-m", "unrelated change")
    verifier = RecordingVerifier()

    result = StableBaseline(stable, policy).refresh(repo, verifier, cancelled=lambda: False)

    assert result == before
    assert repo.head() == before
    assert ref(experiment, "refs/agent/stable-baseline") == ""
    assert ref(experiment, "refs/heads/experiment-auto") == before
    assert verifier.archives == []
    assert "README.md" not in git(experiment, "ls-tree", "-r", "--name-only", result).splitlines()


def test_scoped_drift_three_way_merges_and_keeps_candidate_as_parent(setup_repos):
    stable, experiment, repo, policy = setup_repos
    candidate = add_experiment_candidate(repo)
    (stable / "agent-core/module.py").write_text("FIRST = 1\nSECOND = 1\nTHIRD = 1\nFOURTH = 2\n", encoding="utf-8")
    git(stable, "add", "agent-core/module.py")
    git(stable, "commit", "-m", "stable nonconflicting edit")
    stable_head = git(stable, "rev-parse", "HEAD")
    verifier = RecordingVerifier()

    refreshed = StableBaseline(stable, policy).refresh(repo, verifier, cancelled=lambda: False)

    assert refreshed == repo.head() != candidate
    assert git(experiment, "rev-parse", f"{refreshed}^1") == candidate
    assert archive_files(verifier.archives[0])["agent-core/module.py"] == "FIRST = 2\nSECOND = 1\nTHIRD = 1\nFOURTH = 2\n"
    assert git(experiment, "cat-file", "blob", ref(experiment, "refs/agent/stable-baseline")).split()[1] == stable_head
    assert git(stable, "status", "--porcelain") == ""


@pytest.mark.parametrize("failure", ["verification", "stale-stable"])
def test_verification_failure_or_stale_stable_head_does_not_advance_refs(setup_repos, failure):
    stable, experiment, repo, policy = setup_repos
    candidate = add_experiment_candidate(repo)
    (stable / "agent-core/module.py").write_text("FIRST = 1\nSECOND = 1\nTHIRD = 1\nFOURTH = 2\n", encoding="utf-8")
    git(stable, "add", "agent-core/module.py")
    git(stable, "commit", "-m", "stable change to verify")
    stable_before = git(stable, "rev-parse", "HEAD")

    def advance_stable():
        if failure == "stale-stable":
            (stable / "agent-core/tests/test_module.py").write_text("assert 2 == 2\n", encoding="utf-8")
            git(stable, "add", "agent-core/tests/test_module.py")
            git(stable, "commit", "--allow-empty", "-m", "concurrent stable change")

    verifier = RecordingVerifier(passed=failure != "verification", before_return=advance_stable)
    reason = "stale" if failure == "stale-stable" else "verification failed"
    with pytest.raises(RuntimeError, match=reason):
        StableBaseline(stable, policy).refresh(repo, verifier, cancelled=lambda: False)

    assert repo.head() == candidate
    assert ref(experiment, "refs/heads/experiment-auto") == candidate
    assert ref(experiment, "refs/agent/stable-baseline") == ""
    assert (git(stable, "rev-parse", "HEAD") != stable_before) == (failure == "stale-stable")




def test_anchor_tracks_only_successfully_applied_stable_snapshots(setup_repos):
    stable, experiment, repo, policy = setup_repos
    add_experiment_candidate(repo)
    (stable / "agent-core/module.py").write_text("FIRST = 1\nSECOND = 1\nTHIRD = 1\nFOURTH = 2\n", encoding="utf-8")
    git(stable, "add", "agent-core/module.py")
    git(stable, "commit", "-m", "first scoped stable update")
    verifier = RecordingVerifier()
    StableBaseline(stable, policy).refresh(repo, verifier, cancelled=lambda: False)
    marker_one = ref(experiment, "refs/agent/stable-baseline")

    (stable / "agent-core/module.py").write_text("FIRST = 1\nSECOND = 1\nTHIRD = 3\nFOURTH = 2\n", encoding="utf-8")
    git(stable, "add", "agent-core/module.py")
    git(stable, "commit", "-m", "second scoped stable update")
    refreshed = StableBaseline(stable, policy).refresh(repo, verifier, cancelled=lambda: False)

    assert ref(experiment, "refs/agent/stable-baseline") != marker_one
    assert archive_files(verifier.archives[-1])["agent-core/module.py"] == "FIRST = 2\nSECOND = 1\nTHIRD = 3\nFOURTH = 2\n"
    assert git(experiment, "rev-parse", f"{refreshed}^1") != ""


def test_irreconcilable_scoped_edit_is_blocked_without_ref_writes(setup_repos):
    stable, experiment, repo, policy = setup_repos
    candidate = add_experiment_candidate(repo)
    (stable / "agent-core/module.py").write_text("FIRST = 3\nSECOND = 1\nTHIRD = 1\nFOURTH = 1\n", encoding="utf-8")
    git(stable, "add", "agent-core/module.py")
    git(stable, "commit", "-m", "conflicting stable update")
    verifier = RecordingVerifier()

    with pytest.raises(RuntimeError, match="conflicts"):
        StableBaseline(stable, policy).refresh(repo, verifier, cancelled=lambda: False)

    assert repo.head() == candidate
    assert ref(experiment, "refs/heads/experiment-auto") == candidate
    assert ref(experiment, "refs/agent/stable-baseline") == ""
    assert verifier.archives == []


def test_scoped_snapshot_remains_reachable_after_aggressive_gc(setup_repos):
    stable, experiment, repo, policy = setup_repos
    add_experiment_candidate(repo)
    (stable / "agent-core/module.py").write_text("FIRST = 1\nSECOND = 1\nTHIRD = 1\nFOURTH = 2\n", encoding="utf-8")
    git(stable, "add", "agent-core/module.py")
    git(stable, "commit", "-m", "scoped stable update")
    StableBaseline(stable, policy).refresh(repo, RecordingVerifier(), cancelled=lambda: False)
    marker = git(experiment, "cat-file", "blob", ref(experiment, "refs/agent/stable-baseline")).split()
    assert len(marker) == 3
    snapshot_tree = marker[2]

    gc = subprocess.run(["git", "gc", "--aggressive", "--prune=now"], cwd=experiment, env=GIT_ENV,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert gc.returncode == 0, gc.stderr.decode("utf-8", "replace")
    assert ref(experiment, "refs/codex/stable-baseline-scope-tree") == snapshot_tree
    assert subprocess.run(["git", "cat-file", "-e", f"{snapshot_tree}^{{tree}}"], cwd=experiment, env=GIT_ENV,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False).returncode == 0

    (stable / "agent-core/module.py").write_text("FIRST = 1\nSECOND = 1\nTHIRD = 3\nFOURTH = 2\n", encoding="utf-8")
    git(stable, "add", "agent-core/module.py")
    git(stable, "commit", "-m", "later scoped stable update")
    verifier = RecordingVerifier()
    StableBaseline(stable, policy).refresh(repo, verifier, cancelled=lambda: False)
    assert archive_files(verifier.archives[-1])["agent-core/module.py"] == "FIRST = 2\nSECOND = 1\nTHIRD = 3\nFOURTH = 2\n"


def test_conflict_resolver_receives_diff3_only_for_editable_source(setup_repos):
    stable, experiment, repo, policy = setup_repos
    candidate = add_experiment_candidate(repo)
    (stable / "agent-core/module.py").write_text("FIRST = 3\nSECOND = 1\nTHIRD = 1\nFOURTH = 1\n", encoding="utf-8")
    git(stable, "add", "agent-core/module.py")
    git(stable, "commit", "-m", "conflicting stable update")
    calls = []

    def resolve(path, diff3):
        calls.append((path, diff3))
        return b"FIRST = 4\nSECOND = 1\nTHIRD = 1\nFOURTH = 1\n"

    verifier = RecordingVerifier()
    refreshed = StableBaseline(stable, policy, resolve=resolve).refresh(repo, verifier, cancelled=lambda: False)

    assert calls[0][0] == "agent-core/module.py"
    assert b"<<<<<<<<<<<<<<<< SYNC_TARGET" in calls[0][1]
    assert b"|||||||||||||||| SYNC_BASE" in calls[0][1]
    assert b"================" in calls[0][1]
    assert b">>>>>>>>>>>>>>>> SYNC_SOURCE" in calls[0][1]
    assert git(experiment, "rev-parse", f"{refreshed}^1") == candidate
    assert archive_files(verifier.archives[0])["agent-core/module.py"].startswith("FIRST = 4")


def test_conflict_resolver_is_never_called_for_test_files(setup_repos):
    stable, experiment, repo, policy = setup_repos
    add_unprotected_test_edit(repo, experiment, "assert 1 == 2\n")
    (stable / "agent-core/tests/test_module.py").write_text("assert 3 == 4\n", encoding="utf-8")
    git(stable, "add", "agent-core/tests/test_module.py")
    git(stable, "commit", "-m", "conflicting test change")
    calls = []

    with pytest.raises(RuntimeError, match="conflicts"):
        StableBaseline(stable, policy, resolve=lambda path, data: calls.append(path)).refresh(
            repo, RecordingVerifier(), cancelled=lambda: False)

    assert calls == []
    assert ref(experiment, "refs/agent/stable-baseline") == ""


def test_retries_one_stable_advance_and_refreshes_noneditable_verification_dependency(setup_repos):
    stable, experiment, repo, policy = setup_repos
    dependency = "agent-core/new_dependency.py"
    policy.verification_files += (dependency,)
    (stable / dependency).write_bytes(b"VALUE = 1\n")
    git(stable, "add", dependency)
    git(stable, "commit", "-m", "add verification dependency")
    advances = []

    def advance_once():
        if not advances:
            (stable / dependency).write_bytes(b"VALUE = 2\n")
            git(stable, "add", dependency)
            git(stable, "commit", "-m", "concurrent update to verification dependency")
            advances.append(git(stable, "rev-parse", "HEAD"))

    verifier = RecordingVerifier(before_return=advance_once)
    refreshed = StableBaseline(stable, policy).refresh(repo, verifier, cancelled=lambda: False)
    assert [archive_files(value)[dependency] for value in verifier.archives] == ["VALUE = 1\n", "VALUE = 2\n"]
    assert git(experiment, "show", f"{refreshed}:{dependency}") == "VALUE = 2"
