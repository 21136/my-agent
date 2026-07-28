"""Temp AgentPaths helpers for IT-62 / T-1824-05 test isolation.

Prefer this over ``AgentPaths.discover()`` whenever a test writes sessions,
``state.json``, ``evolve_log``, or evolve tools.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from paths import AgentPaths

_DEFAULT_INDEX = (
    '[[topic]]\nid = "common"\n\n'
    '[[topic]]\nid = "coding"\n\n'
    '[[topic]]\nid = "workflow"\n'
)


def _build_temp_root(
    *,
    copy_tool_dirs: tuple[str, ...] = (),
    prefix: str = "stab-iso-",
) -> Path:
    live = AgentPaths.discover()
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    (tmp / "evolve").mkdir(parents=True)
    (tmp / "evolve" / "_index.core.toml").write_text(_DEFAULT_INDEX, encoding="utf-8")
    (tmp / "workspace").mkdir(parents=True)
    (tmp / "data" / "sessions").mkdir(parents=True)

    for rel in copy_tool_dirs:
        src = live.evolve / "tools" / rel
        if not src.is_dir():
            raise FileNotFoundError(f"evolve tool to copy missing: {src}")
        dst = tmp / "evolve" / "tools" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)

    # Evolved tools (e.g. write_evolve) import from ``<agent_root>/agent-core``.
    core_link = tmp / "agent-core"
    core_src = live.agent_root / "agent-core"
    if not core_link.exists():
        try:
            core_link.symlink_to(core_src, target_is_directory=True)
        except OSError:
            # Windows without symlink privilege: directory junction.
            import subprocess

            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(core_link), str(core_src)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
    return tmp


def _unlink_agent_core_link(tmp: Path) -> None:
    """Remove agent-core symlink/junction without descending into the live tree."""
    core = tmp / "agent-core"
    if not core.exists() and not core.is_symlink():
        return
    try:
        if hasattr(core, "is_junction") and core.is_junction():  # type: ignore[attr-defined]
            core.rmdir()
            return
    except OSError:
        pass
    if core.is_symlink():
        core.unlink(missing_ok=True)
        return
    # Junction fallback (Windows): rmdir removes the link, not the target.
    try:
        core.rmdir()
    except OSError:
        pass


def _cleanup_temp_root(tmp: Path) -> None:
    _unlink_agent_core_link(tmp)
    shutil.rmtree(tmp, ignore_errors=True)


@contextmanager
def temporary_agent_paths(
    *,
    copy_tool_dirs: tuple[str, ...] = (),
    prefix: str = "stab-iso-",
) -> Iterator[AgentPaths]:
    """Context manager: isolated agent root, always removed on exit."""
    tmp = _build_temp_root(copy_tool_dirs=copy_tool_dirs, prefix=prefix)
    try:
        yield AgentPaths.from_root(tmp)
    finally:
        _cleanup_temp_root(tmp)


def make_temp_agent_paths(
    test: unittest.TestCase,
    *,
    copy_tool_dirs: tuple[str, ...] = (),
    prefix: str = "stab-iso-",
) -> AgentPaths:
    """Create an isolated agent root under tempfile; cleaned via ``addCleanup``."""
    tmp = _build_temp_root(copy_tool_dirs=copy_tool_dirs, prefix=prefix)
    test.addCleanup(_cleanup_temp_root, tmp)
    return AgentPaths.from_root(tmp)
