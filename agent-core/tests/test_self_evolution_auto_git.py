from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_evolution_auto_git import GitExperiment


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
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=GIT_ENV, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout.decode("utf-8").strip()


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "--initial-branch=experiment-base")
    core = source / "agent-core"
    core.mkdir()
    (core / ".gitattributes").write_text("*.py text eol=lf\n", encoding="utf-8")
    (core / "agent.py").write_text("VALUE = 1\n", encoding="utf-8")
    (core / "test_agent.py").write_text("assert True\n", encoding="utf-8")
    (core / "nested").mkdir()
    (core / "nested" / "module.py").write_text("NAME = 'old'\n", encoding="utf-8")
    git(source, "-c", "core.autocrlf=false", "add", "agent-core")
    git(source, "commit", "-m", "base")
    parent = tmp_path / "experiments"
    parent.mkdir()
    destination = parent / "my-agent-auto-target"
    git(tmp_path, "-c", "core.autocrlf=false", "clone", "--no-hardlinks", "--no-local", str(source), str(destination))
    return destination


def open_repo(path: Path, parent: Path) -> GitExperiment:
    return GitExperiment.open(path, allowed_parent=parent)


def test_open_accepts_only_direct_child_independent_clone(clone: Path) -> None:
    repo = open_repo(clone, clone.parent)
    base = git(clone, "rev-parse", "refs/heads/experiment-base")
    assert repo.head() == base
    assert git(clone, "symbolic-ref", "HEAD") == "refs/heads/experiment-base"


def test_open_rejects_outside_or_nested_path(clone: Path, tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        GitExperiment.open(clone, allowed_parent=tmp_path)
    nested = clone / "nested"
    with pytest.raises(RuntimeError):
        GitExperiment.open(nested, allowed_parent=clone.parent)


def test_open_rejects_linked_worktree_and_bare_repo(clone: Path, tmp_path: Path) -> None:
    linked = clone.parent / "linked"
    git(clone, "worktree", "add", "-b", "linked-branch", str(linked), "experiment-base")
    with pytest.raises(RuntimeError):
        GitExperiment.open(linked, allowed_parent=clone.parent)
    bare = clone.parent / "bare.git"
    git(clone.parent, "clone", "--bare", str(clone), str(bare))
    with pytest.raises(RuntimeError):
        GitExperiment.open(bare, allowed_parent=clone.parent)


def test_source_reads_only_requested_regular_utf8_tracked_blobs(clone: Path) -> None:
    repo = open_repo(clone, clone.parent)
    base = repo.head()
    assert repo.source(base, ("agent-core/agent.py", "agent-core/nested/module.py")) == {
        "agent-core/agent.py": "VALUE = 1\n",
        "agent-core/nested/module.py": "NAME = 'old'\n",
    }
    for invalid in ("../outside", ".git/config", "agent-core/agent.py:stream", "agent-core\\nested\\module.py", "CON.txt"):
        with pytest.raises(RuntimeError):
            repo.source(base, (invalid,))


def test_source_rejects_symlinks_and_non_utf8(clone: Path) -> None:
    link_oid = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=clone, env=GIT_ENV, input=b"agent-core/agent.py", stdout=subprocess.PIPE, check=True).stdout.decode().strip()
    git(clone, "update-index", "--add", "--cacheinfo", f"120000,{link_oid},agent-core/link.py")
    (clone / "agent-core" / "binary.dat").write_bytes(b"\xff")
    git(clone, "add", "agent-core/binary.dat")
    git(clone, "commit", "-m", "unsafe inputs")
    repo = open_repo(clone, clone.parent)
    head = repo.head()
    with pytest.raises(RuntimeError):
        repo.source(head, ("agent-core/link.py",))
    with pytest.raises(RuntimeError):
        repo.source(head, ("agent-core/binary.dat",))


def test_prepare_creates_tree_without_touching_worktree_or_index(clone: Path) -> None:
    repo = open_repo(clone, clone.parent)
    index_before = git(clone, "write-tree")
    tree = repo.prepare(repo.head(), {"agent-core/agent.py": "VALUE = 2\n", "agent-core/nested/module.py": "NAME = 'new'\n"})
    assert len(tree) in (40, 64)
    assert git(clone, "write-tree") == index_before
    assert (clone / "agent-core/agent.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert git(clone, "status", "--porcelain") == ""
    assert repo.archive(tree, ("agent-core/agent.py",))


@pytest.mark.parametrize("edits", [
    {"new.py": "new\n"},
    {"agent-core/test_agent.py": "assert False\n"},
    {".git/config": "unsafe\n"},
    {"../escape.py": "unsafe\n"},
])
def test_prepare_rejects_new_test_or_unsafe_paths(clone: Path, edits: dict[str, str]) -> None:
    repo = open_repo(clone, clone.parent)
    with pytest.raises(RuntimeError):
        repo.prepare(repo.head(), edits)


def test_publish_is_commit_tree_and_updates_only_auto_ref(clone: Path) -> None:
    repo = open_repo(clone, clone.parent)
    base = repo.head()
    tree = repo.prepare(base, {"agent-core/agent.py": "VALUE = 3\n"})
    before = git(clone, "rev-parse", "HEAD")
    published = repo.publish(base, tree, "candidate result")
    assert repo.head() == published
    assert git(clone, "rev-parse", "HEAD") == before
    assert git(clone, "show", f"{published}:agent-core/agent.py") == "VALUE = 3"
    assert git(clone, "rev-parse", f"{published}^") == base
    assert repo._ref("refs/heads/experiment-auto") == published
    assert git(clone, "branch", "--show-current") == "experiment-base"


def test_publish_rejects_stale_base_and_never_overwrites_auto(clone: Path) -> None:
    repo = open_repo(clone, clone.parent)
    base = repo.head()
    tree = repo.prepare(base, {"agent-core/agent.py": "VALUE = 4\n"})
    git(clone, "commit", "--allow-empty", "-m", "advance base")
    with pytest.raises(RuntimeError):
        repo.publish(base, tree, "stale")
    assert repo._ref("refs/heads/experiment-auto") == ""


def test_archive_contains_only_explicit_safe_regular_paths(clone: Path) -> None:
    repo = open_repo(clone, clone.parent)
    base = repo.head()
    tree = repo.prepare(base, {"agent-core/agent.py": "VALUE = 8\n"})
    payload = repo.archive(tree, ("agent-core/agent.py", "agent-core/test_agent.py"))
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        assert archive.getnames() == ["agent-core/agent.py", "agent-core/test_agent.py"]
        assert archive.extractfile("agent-core/agent.py").read() == b"VALUE = 8\n"
        assert all(member.isfile() for member in archive.getmembers())
    with pytest.raises(RuntimeError):
        repo.archive(tree, ("../agent-core/agent.py",))







def test_open_rejects_wrong_target_name(clone: Path) -> None:
    wrong = clone.parent / "candidate"
    clone.rename(wrong)
    with pytest.raises(RuntimeError):
        GitExperiment.open(wrong, allowed_parent=wrong.parent)


def test_source_and_prepare_reject_stale_head(clone: Path) -> None:
    repo = open_repo(clone, clone.parent)
    stale = repo.head()
    git(clone, "commit", "--allow-empty", "-m", "advance base")
    with pytest.raises(RuntimeError):
        repo.source(stale, ("agent-core/agent.py",))
    with pytest.raises(RuntimeError):
        repo.prepare(stale, {"agent-core/agent.py": "VALUE = 9\n"})


def test_publish_rejects_auto_branch_checked_out_in_another_worktree(clone: Path) -> None:
    repo = open_repo(clone, clone.parent)
    base = repo.head()
    tree = repo.prepare(base, {"agent-core/agent.py": "VALUE = 5\n"})
    published = repo.publish(base, tree, "first candidate")
    checkout = clone.parent / "auto-checkout"
    git(clone, "worktree", "add", str(checkout), "experiment-auto")
    next_tree = repo.prepare(published, {"agent-core/agent.py": "VALUE = 6\n"})
    with pytest.raises(RuntimeError):
        repo.publish(published, next_tree, "must not update checked out branch")



def test_publish_can_advance_auto_with_compare_and_swap(clone: Path) -> None:
    repo = open_repo(clone, clone.parent)
    base = repo.head()
    first_tree = repo.prepare(base, {"agent-core/agent.py": "VALUE = 10\n"})
    first = repo.publish(base, first_tree, "first")
    second_tree = repo.prepare(first, {"agent-core/agent.py": "VALUE = 11\n"})
    second = repo.publish(first, second_tree, "second")
    assert repo.head() == second
    assert git(clone, "rev-parse", f"{second}^") == first
    assert git(clone, "show", f"{second}:agent-core/agent.py") == "VALUE = 11"


def test_open_rejects_object_alternate_clone(clone: Path) -> None:
    alternate = clone / ".git" / "objects" / "info" / "alternates"
    alternate.parent.mkdir(parents=True, exist_ok=True)
    alternate.write_text(str(clone.parent / "source" / ".git" / "objects"), encoding="utf-8")
    with pytest.raises(RuntimeError):
        GitExperiment.open(clone, allowed_parent=clone.parent)


def test_archive_rejects_python_paths_outside_agent_core(clone: Path) -> None:
    repo = open_repo(clone, clone.parent)
    with pytest.raises(RuntimeError):
        repo.archive(repo.prepare(repo.head(), {"agent-core/agent.py": "VALUE = 12\n"}), ("agent-core/agent.py", "README.py"))


def test_open_requires_exact_checked_out_base_branch(clone: Path) -> None:
    git(clone, "switch", "--detach", "HEAD")
    with pytest.raises(RuntimeError):
        GitExperiment.open(clone, allowed_parent=clone.parent)



def test_publish_disables_configured_reference_transaction_hook(clone: Path) -> None:
    repo = open_repo(clone, clone.parent)
    base = repo.head()
    tree = repo.prepare(base, {"agent-core/agent.py": "VALUE = 20\n"})
    hooks = clone / ".git" / "attacker-hooks"
    hooks.mkdir()
    hook = hooks / "reference-transaction"
    hook.write_text(
        "#!/bin/sh\nprintf 'ran\\n' > \"$GIT_DIR/reference-hook-ran\"\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    git(clone, "config", "core.hooksPath", str(hooks))

    repo.publish(base, tree, "hook isolation")

    assert not (clone / ".git" / "reference-hook-ran").exists()


def test_open_rejects_symbolic_experiment_control_refs(clone: Path) -> None:
    git(clone, "branch", "protected", "experiment-base")
    git(clone, "symbolic-ref", "refs/heads/experiment-base", "refs/heads/protected")
    with pytest.raises(RuntimeError):
        GitExperiment.open(clone, allowed_parent=clone.parent)


def test_publish_never_dereferences_raced_auto_ref(clone: Path) -> None:
    repo = open_repo(clone, clone.parent)
    base = repo.head()
    tree = repo.prepare(base, {"agent-core/agent.py": "VALUE = 21\n"})
    git(clone, "branch", "protected", base)
    git(clone, "symbolic-ref", "refs/heads/experiment-auto", "refs/heads/protected")

    with pytest.raises(RuntimeError):
        repo.publish(base, tree, "must not update symbolic target")

    assert git(clone, "rev-parse", "refs/heads/protected") == base
    assert git(clone, "rev-parse", "refs/heads/experiment-base") == base


def test_prepare_uses_index_scratch_on_repository_volume(clone: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tempfile

    seen: list[Path] = []
    real_temporary_directory = tempfile.TemporaryDirectory

    def capture_tempdir(*args, **kwargs):
        seen.append(Path(kwargs["dir"]))
        return real_temporary_directory(*args, **kwargs)

    monkeypatch.setattr(tempfile, "TemporaryDirectory", capture_tempdir)
    repo = open_repo(clone, clone.parent)
    repo.prepare(repo.head(), {"agent-core/agent.py": "VALUE = 22\n"})

    assert seen
    assert all(path.resolve() == (clone / ".git").resolve() for path in seen)



def test_replace_refs_cannot_redirect_broker_blob_reads(clone: Path) -> None:
    repo = open_repo(clone, clone.parent)
    base = repo.head()
    replacement_tree = repo.prepare(base, {"agent-core/agent.py": "VALUE = 999\n"})
    replacement = git(clone, "commit-tree", replacement_tree, "-p", base, "-m", "replacement object")
    git(clone, "replace", base, replacement)

    assert repo.source(base, ("agent-core/agent.py",))["agent-core/agent.py"] == "VALUE = 1\n"



def test_publish_no_deref_option_contains_control_ref_race(clone: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = open_repo(clone, clone.parent)
    base = repo.head()
    tree = repo.prepare(base, {"agent-core/agent.py": "VALUE = 23\n"})
    git(clone, "branch", "protected", base)
    real_run = repo._run
    injected = False

    def race_before_update_ref(*args, **kwargs):
        nonlocal injected
        if args and args[0] == "update-ref" and not injected:
            injected = True
            git(clone, "symbolic-ref", "refs/heads/experiment-auto", "refs/heads/protected")
        return real_run(*args, **kwargs)

    monkeypatch.setattr(repo, "_run", race_before_update_ref)
    try:
        repo.publish(base, tree, "raced ref")
    except RuntimeError:
        pass

    assert injected
    assert git(clone, "rev-parse", "refs/heads/protected") == base
    assert git(clone, "rev-parse", "refs/heads/experiment-base") == base
