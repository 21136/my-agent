from __future__ import annotations

import io
import os
import subprocess
import tarfile
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_evolution_auto_git import GitExperiment
from self_evolution_stable_promote import StablePromoter

GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test Committer",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.decode("utf-8").strip()


def git_text(cwd: Path, *args: str) -> str:
    return git(cwd, *args).replace("\r\n", "\n")


@dataclass(frozen=True)
class Policy:
    test_image: str = "sha256:" + "1" * 64
    tests: tuple[str, ...] = ("agent-core/tests/test_sample.py",)
    verification_files: tuple[str, ...] = ("agent-core/app.py", "agent-core/tests/test_sample.py")
    editable_files: tuple[str, ...] = ("agent-core/app.py",)
    test_timeout_sec: int = 30


@pytest.fixture
def repositories(tmp_path: Path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "--initial-branch=main")
    (source / "agent-core").mkdir()
    (source / "agent-core/app.py").write_text("A = 1\nB = 2\nC = 3\n", encoding="utf-8")
    (source / "agent-core/tests").mkdir()
    (source / "agent-core/tests/test_sample.py").write_text("def test_sample():\n    assert True\n", encoding="utf-8")
    (source / "notes.txt").write_text("tracked\n", encoding="utf-8")
    git(source, "add", ".")
    git(source, "commit", "-m", "base")

    stable = tmp_path / "stable"
    git(tmp_path, "clone", str(source), str(stable))
    experiments = tmp_path / "experiments"
    experiments.mkdir()
    worker_path = experiments / "my-agent-auto-target"
    git(tmp_path, "clone", str(source), str(worker_path))
    git(worker_path, "checkout", "-b", "experiment-base")
    repo = GitExperiment.open(worker_path, allowed_parent=experiments)

    from self_evolution_auto_runner import DockerVerifier
    monkeypatch.setattr(DockerVerifier, "preflight", lambda self: None)
    monkeypatch.setattr(DockerVerifier, "run", lambda self, archive, *, cancelled: SimpleNamespace(
        passed=True, summary="passed"))
    return stable, repo, Policy()


def worker_commit(repo: GitExperiment, *, path="agent-core/app.py", content="A = 9\nB = 2\nC = 3\n") -> str:
    base = repo.head()
    entries = repo._tree_entries(base)
    oid = repo._run("hash-object", "-w", "--stdin", input_data=content.encode()).strip()
    mode = entries.get(path, ("100644", ""))[0]
    with __import__("tempfile").TemporaryDirectory(dir=repo.root / ".git") as scratch:
        env = repo._env(index_file=str(Path(scratch) / "index"))
        repo._run("read-tree", base, env=env)
        repo._run("update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}", env=env)
        tree = repo._run("write-tree", env=env).strip()
    return repo._run("commit-tree", tree, "-p", base, "-m", "worker candidate").strip()


def test_promotes_scoped_worker_commit_and_returns_stable_commit(repositories):
    stable, repo, policy = repositories
    commit = worker_commit(repo)
    result = StablePromoter(stable, policy).promote(repo, commit, cancelled=lambda: False)
    assert result.status == "promoted", result.detail
    assert result.commit == git(stable, "rev-parse", "HEAD")
    assert git_text(stable, "show", "HEAD:agent-core/app.py") == "A = 9\nB = 2\nC = 3"
    assert git(stable, "show", "--format=%P", "--no-patch", "HEAD") == git(stable, "rev-parse", "HEAD^")


def test_preserves_unrelated_staged_and_unstaged_work_and_merges_dirty_hunk(repositories):
    stable, repo, policy = repositories
    commit = worker_commit(repo)
    (stable / "notes.txt").write_text("staged note\n", encoding="utf-8")
    git(stable, "add", "notes.txt")
    (stable / "agent-core/app.py").write_text("A = 1\nB = 2\nC = 30\n", encoding="utf-8")
    (stable / "untracked.txt").write_text("keep\n", encoding="utf-8")
    result = StablePromoter(stable, policy).promote(repo, commit, cancelled=lambda: False)
    assert result.status == "promoted", result.detail
    assert (stable / "agent-core/app.py").read_text(encoding="utf-8") == "A = 9\nB = 2\nC = 30\n"
    assert git_text(stable, "show", "HEAD:agent-core/app.py") == "A = 9\nB = 2\nC = 3"
    assert git_text(stable, "show", ":notes.txt") == "staged note"
    assert git_text(stable, "show", "HEAD:notes.txt") == "tracked"
    assert (stable / "untracked.txt").read_text(encoding="utf-8") == "keep\n"


def test_refuses_forbidden_worker_scope_without_writing(repositories):
    stable, repo, policy = repositories
    commit = worker_commit(repo, path="notes.txt", content="changed\n")
    before = git(stable, "rev-parse", "HEAD")
    result = StablePromoter(stable, policy).promote(repo, commit, cancelled=lambda: False)
    assert result.status == "blocked"
    assert git(stable, "rev-parse", "HEAD") == before


def test_retries_when_stable_head_drifts_during_verification(repositories, monkeypatch):
    stable, repo, policy = repositories
    commit = worker_commit(repo)
    from self_evolution_auto_runner import DockerVerifier
    calls = 0

    def drift_once(self, archive, *, cancelled):
        nonlocal calls
        calls += 1
        if calls == 1:
            (stable / "notes.txt").write_text("concurrent commit\n", encoding="utf-8")
            git(stable, "add", "notes.txt")
            git(stable, "commit", "-m", "concurrent stable change")
        return SimpleNamespace(passed=True, summary="passed")

    monkeypatch.setattr(DockerVerifier, "run", drift_once)
    result = StablePromoter(stable, policy).promote(repo, commit, cancelled=lambda: False)
    assert result.status == "promoted", result.detail
    assert calls >= 2
    assert git_text(stable, "show", "HEAD:agent-core/app.py") == "A = 9\nB = 2\nC = 3"
    assert git_text(stable, "show", "HEAD:notes.txt") == "concurrent commit"


def test_failed_verification_never_changes_stable(repositories, monkeypatch):
    stable, repo, policy = repositories
    commit = worker_commit(repo)
    from self_evolution_auto_runner import DockerVerifier
    calls = 0

    def baseline_pass_candidate_fail(self, archive, *, cancelled):
        nonlocal calls
        calls += 1
        return SimpleNamespace(passed=calls == 1, summary="failed candidate")

    monkeypatch.setattr(DockerVerifier, "run", baseline_pass_candidate_fail)
    before = git(stable, "rev-parse", "HEAD")
    result = StablePromoter(stable, policy).promote(repo, commit, cancelled=lambda: False)
    assert result.status == "failed"
    assert result.commit is None
    assert git(stable, "rev-parse", "HEAD") == before


def test_held_index_lock_is_never_removed(repositories):
    stable, repo, policy = repositories
    commit = worker_commit(repo)
    lock = Path(git(stable, "rev-parse", "--git-path", "index").strip())
    if not lock.is_absolute():
        lock = stable / lock
    lock = Path(str(lock.resolve()) + ".lock")
    lock.write_bytes(b"owned by concurrent git writer")
    result = StablePromoter(stable, policy).promote(repo, commit, cancelled=lambda: False)
    assert result.status == "failed"
    assert lock.read_bytes() == b"owned by concurrent git writer"


def test_rollback_does_not_overwrite_concurrent_affected_file_and_keeps_journal(repositories, monkeypatch):
    stable, repo, policy = repositories
    commit = worker_commit(repo)
    target = stable / "agent-core/app.py"
    old_head = git(stable, "rev-parse", "HEAD")
    from self_evolution_stable_promote import os as promoter_os
    original_replace = promoter_os.replace
    injected = False

    def concurrent_writer(source, destination):
        nonlocal injected
        if Path(destination) == target and ".stable-promote-" in Path(source).name and not injected:
            original_replace(source, destination)  # promotion has already CAS-updated HEAD
            target.write_text("concurrent writer after CAS\n", encoding="utf-8")
            injected = True
            raise OSError("simulated post-install failure")
        return original_replace(source, destination)

    monkeypatch.setattr(promoter_os, "replace", concurrent_writer)
    result = StablePromoter(stable, policy).promote(repo, commit, cancelled=lambda: False)
    assert result.status == "failed"
    assert injected
    assert git(stable, "rev-parse", "HEAD") == old_head
    assert target.read_text(encoding="utf-8").replace("\r\n", "\n") == "concurrent writer after CAS\n"
    journals = list((stable / ".git").glob("stable-promote-recovery-*"))
    assert journals
    assert any((journal / "journal.json").is_file() and (journal / "working-0.before").is_file()
               for journal in journals)


def test_refuses_same_path_staged_change(repositories):
    stable, repo, policy = repositories
    commit = worker_commit(repo)
    (stable / "agent-core/app.py").write_text("A = 1\nB = 20\nC = 3\n", encoding="utf-8")
    git(stable, "add", "agent-core/app.py")
    result = StablePromoter(stable, policy).promote(repo, commit, cancelled=lambda: False)
    assert result.status == "blocked"
    assert git_text(stable, "cat-file", "blob", "HEAD:agent-core/app.py") == "A = 1\nB = 2\nC = 3"
    assert git_text(stable, "cat-file", "blob", ":agent-core/app.py") == "A = 1\nB = 20\nC = 3"


def test_preserves_concurrent_committed_hunk_in_same_scoped_file(repositories, monkeypatch):
    stable, repo, policy = repositories
    commit = worker_commit(repo)
    (stable / "agent-core/app.py").write_text("A = 1\nB = 2\nC = 30\n", encoding="utf-8")
    git(stable, "add", "agent-core/app.py")
    git(stable, "commit", "-m", "stable scoped edit")
    from self_evolution_auto_runner import DockerVerifier

    runs = 0

    def inspect_candidate(self, archive, *, cancelled):
        nonlocal runs
        runs += 1
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as candidate:
            app = candidate.extractfile("agent-core/app.py").read().decode("utf-8")
            test = candidate.extractfile("agent-core/tests/test_sample.py").read().decode("utf-8")
        expected_a = 1 if runs == 1 else 9
        assert app.replace("\r\n", "\n") == f"A = {expected_a}\nB = 2\nC = 30\n"
        assert "assert True" in test
        return SimpleNamespace(passed=True, summary="passed")

    monkeypatch.setattr(DockerVerifier, "run", inspect_candidate)
    result = StablePromoter(stable, policy).promote(repo, commit, cancelled=lambda: False)
    assert result.status == "promoted", result.detail
    assert git_text(stable, "show", "HEAD:agent-core/app.py") == "A = 9\nB = 2\nC = 30"


def test_blocks_conflicting_dirty_hunk_without_writing(repositories):
    stable, repo, policy = repositories
    commit = worker_commit(repo)
    (stable / "agent-core/app.py").write_text("A = 8\nB = 2\nC = 3\n", encoding="utf-8")
    before = git(stable, "rev-parse", "HEAD")
    result = StablePromoter(stable, policy).promote(repo, commit, cancelled=lambda: False)
    assert result.status == "blocked"
    assert git(stable, "rev-parse", "HEAD") == before
    assert (stable / "agent-core/app.py").read_text(encoding="utf-8").replace("\r\n", "\n") == "A = 8\nB = 2\nC = 3\n"




def test_optional_resolver_receives_diff3_only_for_committed_merge_conflict(repositories):
    stable, repo, policy = repositories
    commit = worker_commit(repo)
    (stable / "agent-core/app.py").write_text("A = 8\nB = 2\nC = 3\n", encoding="utf-8")
    git(stable, "add", "agent-core/app.py")
    git(stable, "commit", "-m", "conflicting stable committed edit")
    received = []

    from self_evolution_selected_sync import resolve_scoped_overlap

    def resolve(path, diff3):
        received.append((path, diff3))

        def complete(_messages, _tools):
            return SimpleNamespace(finish_reason="tool_calls", tool_calls=[{
                "id": "resolved-stable-conflict", "type": "function",
                "function": {"name": "sync_resolve_conflicts", "arguments":
                    '{"patches":[{"id":"c1","replacement":"A = 9"}]}'},
            }])

        return resolve_scoped_overlap(path, diff3, complete)

    result = StablePromoter(stable, policy, resolve=resolve).promote(
        repo, commit, cancelled=lambda: False)
    assert result.status == "promoted", result.detail
    assert len(received) == 1
    assert received[0][0] == "agent-core/app.py"
    assert b"<<<<<<<<<<<<<<<< SYNC_TARGET" in received[0][1] and b"|||||||||||||||| SYNC_BASE" in received[0][1]
    assert git_text(stable, "show", "HEAD:agent-core/app.py") == "A = 9\nB = 2\nC = 3"


def test_optional_resolver_is_not_used_for_dirty_working_conflict(repositories):
    stable, repo, policy = repositories
    commit = worker_commit(repo)
    target = stable / "agent-core/app.py"
    target.write_text("A = 8\nB = 2\nC = 3\n", encoding="utf-8")
    calls = []
    result = StablePromoter(stable, policy, resolve=lambda path, diff3: calls.append((path, diff3))).promote(
        repo, commit, cancelled=lambda: False)
    assert result.status == "blocked"
    assert calls == []
    assert target.read_text(encoding="utf-8").replace("\r\n", "\n") == "A = 8\nB = 2\nC = 3\n"


def test_stable_git_calls_use_only_exact_root_safe_directory(repositories, monkeypatch):
    stable, repo, policy = repositories
    commit = worker_commit(repo)
    from self_evolution_stable_promote import subprocess as promoter_subprocess
    original_run = promoter_subprocess.run
    calls = []

    def capture(argv, *args, **kwargs):
        values = [str(value) for value in argv]
        if len(values) > 2 and values[0] == "git" and values[1] == "-C" and Path(values[2]) == stable:
            calls.append(values)
        return original_run(argv, *args, **kwargs)

    monkeypatch.setattr(promoter_subprocess, "run", capture)
    result = StablePromoter(stable, policy).promote(repo, commit, cancelled=lambda: False)
    assert result.status == "promoted", result.detail
    expected = f"safe.directory={stable.resolve().as_posix()}"
    assert any("hash-object" in argv for argv in calls)
    assert calls
    assert all(any(argv[i] == "-c" and argv[i + 1] == expected for i in range(len(argv) - 1))
               for argv in calls)


def test_exception_before_generated_commit_is_guarded(repositories):
    stable, repo, policy = repositories
    commit = worker_commit(repo)
    promoter = StablePromoter(stable, policy)
    original_git = promoter._git

    def fail_read_tree(root, *args, **kwargs):
        if args and args[0] == "read-tree":
            raise RuntimeError("injected before commit assignment")
        return original_git(root, *args, **kwargs)

    promoter._git = fail_read_tree
    result = promoter.promote(repo, commit, cancelled=lambda: False)
    assert result.status == "failed"
    assert "injected before commit assignment" in result.detail
    assert "UnboundLocalError" not in result.detail


def test_exception_before_prepared_index_assignment_is_guarded(repositories):
    stable, repo, policy = repositories
    commit = worker_commit(repo)
    promoter = StablePromoter(stable, policy)
    original_git = promoter._git
    original_tree_entry = promoter._tree_entry
    commit_tree_done = False
    tree_entry_calls = 0

    def observe_git(root, *args, **kwargs):
        nonlocal commit_tree_done
        result = original_git(root, *args, **kwargs)
        if args and args[0] == "commit-tree":
            commit_tree_done = True
        return result

    def fail_index_entry(root, stable_commit, path):
        nonlocal tree_entry_calls
        if commit_tree_done:
            tree_entry_calls += 1
            if tree_entry_calls == 2:
                raise RuntimeError("injected before index byte assignment")
        return original_tree_entry(root, stable_commit, path)

    promoter._git = observe_git
    promoter._tree_entry = fail_index_entry
    result = promoter.promote(repo, commit, cancelled=lambda: False)
    assert result.status == "failed"
    assert "injected before index byte assignment" in result.detail
    assert "UnboundLocalError" not in result.detail
