from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordScopeError, WorkRecordStore


def _create_junction(link: Path, target: Path, tmp_path: Path) -> None:
    """Create a native Windows junction whose link and target stay in tmp_path."""
    if os.name != "nt":
        pytest.skip("native Windows junction validation requires Windows")

    tmp_root = tmp_path.resolve(strict=True)
    target.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    assert target.resolve(strict=True).is_relative_to(tmp_root)
    assert link.parent.resolve(strict=True).is_relative_to(tmp_root)
    assert not link.exists() and not link.is_symlink()

    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"native junction creation unavailable: {result.stderr.strip() or result.stdout.strip()}")

    is_junction = getattr(link, "is_junction", None)
    if not (callable(is_junction) and is_junction()):
        # Remove only the link entry itself; never recurse through it.
        os.rmdir(link)
        pytest.skip("mklink succeeded but pathlib does not recognize the result as a junction")
    assert link.resolve(strict=True) == target.resolve(strict=True)
    assert link.resolve(strict=True).is_relative_to(tmp_root)


def _session(paths: AgentPaths, conversation_id: str, session_dir: Path) -> Session:
    session_dir.mkdir(parents=True, exist_ok=True)
    return Session(
        conversation_id=conversation_id,
        session_dir=session_dir,
        goal="",
        meta=SessionMeta(),
        messages=[],
        paths=paths,
    )


def test_store_rejects_session_dir_junction(tmp_path: Path) -> None:
    root = tmp_path / "agent"
    real_session = root / "data" / "sessions" / "session-a-target"
    link = root / "data" / "sessions" / "session-a"
    real_session.mkdir(parents=True)
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    _create_junction(link, real_session, tmp_path)

    try:
        session = _session(paths, "session-a", link)
        with pytest.raises(WorkRecordScopeError, match="link or reparse point"):
            WorkRecordStore(session)
    finally:
        # rmdir removes only the junction entry; it does not traverse the target.
        if getattr(link, "is_junction", lambda: False)():
            os.rmdir(link)


def test_store_rejects_junction_in_agent_root_ancestor(tmp_path: Path) -> None:
    real_root = tmp_path / "real-parent" / "agent"
    parent_alias = tmp_path / "parent-alias"
    real_session = real_root / "data" / "sessions" / "session-a"
    real_session.mkdir(parents=True)
    _create_junction(parent_alias, real_root.parent, tmp_path)
    aliased_root = parent_alias / "agent"
    paths = AgentPaths(
        agent_root=aliased_root,
        workspace=aliased_root / "workspace",
        data=aliased_root / "data",
        evolve=aliased_root / "evolve",
    )

    try:
        session = _session(paths, "session-a", aliased_root / "data" / "sessions" / "session-a")
        with pytest.raises(WorkRecordScopeError, match="link or reparse point"):
            WorkRecordStore(session)
    finally:
        if getattr(parent_alias, "is_junction", lambda: False)():
            os.rmdir(parent_alias)
