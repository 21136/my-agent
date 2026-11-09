"""Temp AgentPaths helpers for IT-62 / T-1824-05 test isolation.

Prefer this over ``AgentPaths.discover()`` whenever a test writes sessions,
``state.json``, ``evolve_log``, or evolve tools.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import threading
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from paths import AgentPaths

_CODE_ROOT = Path(__file__).resolve().parents[2]

_DEFAULT_INDEX = (
    '[[topic]]\nid = "common"\n\n'
    '[[topic]]\nid = "coding"\n\n'
    '[[topic]]\nid = "workflow"\n'
)

# Cleanup is authorized only for roots created by this module in this process.
_TEMP_ROOTS: dict[str, tuple[tuple[int, int], tuple[int, int] | None, str]] = {}
_TEMP_ROOTS_LOCK = threading.RLock()


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _identity(path: Path) -> tuple[int, int] | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    return info.st_dev, info.st_ino


def _is_reparse_or_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    is_junction = getattr(path, "is_junction", lambda: False)
    return (
        path.is_symlink()
        or is_junction()
        or bool(getattr(info, "st_file_attributes", 0) & reparse_flag)
    )


def _is_plain_directory(path: Path) -> bool:
    if _identity(path) is None or _is_reparse_or_link(path):
        return False
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


def _path_has_link_or_reparse_component(path: Path) -> bool:
    try:
        return any(_is_reparse_or_link(part) for part in (path, *path.parents))
    except OSError:
        return True


def _tree_has_link_or_reparse(root: Path) -> bool:
    try:
        for current, dirs, files in os.walk(root, followlinks=False):
            base = Path(current)
            for name in (*dirs, *files):
                if _is_reparse_or_link(base / name):
                    return True
        return False
    except OSError:
        return True


def _build_temp_root(
    *,
    copy_tool_dirs: tuple[str, ...] = (),
    prefix: str = "stab-iso-",
) -> Path:
    # Bind to this sandbox checkout explicitly; discovery can inspect data/state.json.
    live = AgentPaths.from_root(_CODE_ROOT)
    tmp = Path(tempfile.mkdtemp(prefix=prefix)).absolute()
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
    root_identity = _identity(tmp)
    link_identity = _identity(core_link)
    if root_identity is None or link_identity is None:
        raise RuntimeError("temporary isolation root identity could not be recorded")
    with _TEMP_ROOTS_LOCK:
        _TEMP_ROOTS[_path_key(tmp)] = (root_identity, link_identity, str(core_src.resolve()))
    return tmp


def _unlink_agent_core_link(tmp: Path, expected: tuple[int, int], target: str) -> bool:
    """Remove only the exact symlink/junction created by _build_temp_root."""
    core = tmp / "agent-core"
    try:
        if _identity(core) != expected or not _is_reparse_or_link(core):
            return False
        if str(core.resolve(strict=True)) != target:
            return False
        if hasattr(core, "is_junction") and core.is_junction():  # type: ignore[attr-defined]
            core.rmdir()
        elif core.is_symlink():
            core.unlink()
        else:
            return False
        return _identity(core) is None
    except (OSError, RuntimeError):
        return False


def _cleanup_temp_root(tmp: Path) -> None:
    with _TEMP_ROOTS_LOCK:
        key = _path_key(tmp)
        record = _TEMP_ROOTS.get(key)
        if record is None:
            return
        root_identity, link_identity, expected_link_target = record
        root = Path(key)
        temp_parent = Path(tempfile.gettempdir()).absolute()
        try:
            resolved_parent = root.resolve(strict=True).parent
            resolved_temp_parent = temp_parent.resolve(strict=True)
            if root.parent != temp_parent or resolved_parent != resolved_temp_parent:
                return
            if not _is_plain_directory(root) or _identity(root) != root_identity:
                return
            if _path_has_link_or_reparse_component(root):
                return
        except (OSError, RuntimeError):
            return
        if link_identity is None:
            core = root / "agent-core"
            if _identity(core) is not None or core.exists() or core.is_symlink():
                return
        else:
            if not _unlink_agent_core_link(root, link_identity, expected_link_target):
                return
            _TEMP_ROOTS[key] = (root_identity, None, expected_link_target)
        if not _is_plain_directory(root) or _identity(root) != root_identity:
            return
        if _tree_has_link_or_reparse(root):
            return
        try:
            shutil.rmtree(root)
        except OSError:
            return
        if _identity(root) is None:
            _TEMP_ROOTS.pop(key, None)


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


def create_project_workspace(
    session: "Session",
    paths: AgentPaths,
    project_id: str,
    *,
    bind_shell: bool = True,
    plan_status: str = "draft",
) -> str:
    """Create ``workspace/<id>/``; optionally bind project shell (legacy test helper)."""
    from project_cli import bind_project_session, parse_project_command, run_project_command
    from project_mode import normalize_project_id
    from session import Session

    assert isinstance(session, Session)
    command = parse_project_command(f"项目 新建 {project_id}")
    assert command is not None
    run_project_command(session, paths, command, output_fn=lambda _line: None)
    pid = normalize_project_id(project_id)
    if bind_shell:
        bind_project_session(session, pid, plan_status=plan_status)
        session.save()
    return pid
