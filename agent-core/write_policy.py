"""Phase 42 Track H — layered confirm policy for write_text / patch_file."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Literal

from plan_patch import PLAN_PATCH_ALLOWLIST
from run_command_policy import _normalize_rel

WriteTool = Literal["write_text", "patch_file"]

_SENSITIVE_GLOBS = (
    "**/.env",
    "**/.env.*",
    "**/credentials*",
    "**/*secret*",
    "data/sessions/**",
    ".git/**",
)


def write_project_policy_enabled(*, project_root: str, active_shell: str) -> bool:
    """H1: layered write confirm only when project is bound on project shell."""
    return bool((project_root or "").strip()) and (active_shell or "").strip() == "project"


def path_under_project(path: str, project_root: str) -> bool:
    """True when *path* (agent-relative) is project_root or under it."""
    root = _normalize_rel(project_root)
    if not root:
        return False
    rel = _normalize_rel(path)
    if not rel:
        return False
    if rel.lower().startswith("host:"):
        return False
    return rel == root or rel.startswith(root + "/")


def is_sensitive_write_path(path: str) -> bool:
    """H4: paths that always require confirm even inside project_root."""
    rel = _normalize_rel(path)
    if not rel:
        return False
    base = Path(rel).name
    if base in PLAN_PATCH_ALLOWLIST:
        return True
    norm = rel.replace("\\", "/")
    for pattern in _SENSITIVE_GLOBS:
        if fnmatch.fnmatch(norm, pattern) or fnmatch.fnmatch(norm.lower(), pattern.lower()):
            return True
    parts = norm.split("/")
    for part in parts:
        if part.startswith(".env"):
            return True
    return False


def write_requires_confirm(
    *,
    tool: WriteTool,
    path: str,
    project_root: str = "",
    active_shell: str = "",
    on_conflict: str = "skip",
    dry_run: bool = False,
    file_exists: bool | None = None,
) -> tuple[bool, str]:
    """Layered confirm for write_text / patch_file (mirror run_command_policy).

    Returns ``(needs_confirm, reason)``.
    """
    if dry_run:
        return False, "skip:dry_run"

    raw = (path or "").strip()
    if not raw:
        return True, "confirm:empty_path"
    if raw.lower().startswith("host:"):
        return True, "confirm:host"

    if not write_project_policy_enabled(project_root=project_root, active_shell=active_shell):
        return True, "confirm:no_project_binding"

    if not path_under_project(raw, project_root):
        return True, "confirm:outside_project"

    if is_sensitive_write_path(raw):
        base = Path(_normalize_rel(raw)).name
        if base in PLAN_PATCH_ALLOWLIST:
            return True, "confirm:plan_domain"
        return True, "confirm:sensitive"

    if tool == "patch_file":
        return False, "skip:project_patch"

    if tool == "write_text":
        conflict = (on_conflict or "skip").strip().lower()
        if file_exists is True and conflict == "overwrite":
            return False, "skip:project_overwrite"
        return True, "confirm:new_file"

    return True, "confirm:other"
