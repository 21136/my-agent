"""Phase 29 Track A — layered confirm policy for run_command (CURSOR-ALIGN A2)."""

from __future__ import annotations

import re
from typing import Literal

CommandClass = Literal["danger", "install", "network", "build_test", "readonly", "other"]

_DANGER_RE = re.compile(
    r"(?is)"
    r"\brm\s+-[a-z]*f[a-z]*\b"
    r"|Remove-Item\b[^\n]*(-Recurse|-Force)"
    r"|\bformat\s+[a-z]:"
    r"|\bmkfs\."
    r"|\bdd\s+if="
    r"|\bdel\s+/[sf]"
    r"|\brd\s+/s"
    r"|\brmdir\s+/s"
)

_INSTALL_RE = re.compile(
    r"(?is)"
    r"\bpip(?:3)?\s+install\b"
    r"|\bpython(?:3)?\s+-m\s+pip\s+install\b"
    r"|\bnpm\s+(?:i|install)\b"
    r"|\bpnpm\s+(?:i|install)\b"
    r"|\byarn\s+(?:add|install)\b"
    r"|\bcargo\s+install\b"
)

_NETWORK_WRITE_RE = re.compile(
    r"(?is)"
    r"\bgit\s+push\b"
    r"|\bcurl\b[^\n]*\s-[A-Z]*[dFX]\b"
    r"|\bInvoke-(?:WebRequest|RestMethod)\b"
    r"|\bwget\b[^\n]*--post"
)

_BUILD_TEST_RE = re.compile(
    r"(?is)"
    r"\bmvn\b"
    r"|\bgradlew?\b"
    r"|\bnpm\s+run\s+(?:build|test|lint|typecheck)\b"
    r"|\bnpm\s+test\b"
    r"|\bpnpm\s+(?:run\s+)?(?:build|test|lint)\b"
    r"|\byarn\s+(?:run\s+)?(?:build|test|lint)\b"
    r"|\bpytest\b"
    r"|\bpython(?:3)?\s+-m\s+pytest\b"
    r"|\bgo\s+test\b"
    r"|\bcargo\s+(?:build|test)\b"
)

_READONLY_RE = re.compile(
    r"(?is)^\s*"
    r"(?:"
    r"echo\b|Write-Output\b|dir\b|ls\b|pwd\b|Get-Location\b|"
    r"git\s+(?:status|diff|log|show|branch)\b|"
    r"type\b|cat\b|Get-ChildItem\b|Get-Content\b|"
    r"mvn\s+-v\b|npm\s+-v\b|node\s+-v\b|python(?:3)?\s+--version\b"
    r")"
)


def classify_run_command(command: str) -> CommandClass:
    text = (command or "").strip()
    if not text:
        return "other"
    if _DANGER_RE.search(text):
        return "danger"
    if _INSTALL_RE.search(text):
        return "install"
    if _NETWORK_WRITE_RE.search(text):
        return "network"
    if _BUILD_TEST_RE.search(text):
        return "build_test"
    if _READONLY_RE.search(text):
        return "readonly"
    return "other"


def _normalize_rel(path: str) -> str:
    return path.strip().replace("\\", "/").lstrip("/")


def working_dir_under_project(working_dir: str, project_root: str) -> bool:
    """True when cwd is the bound project_root or a subdirectory."""
    root = _normalize_rel(project_root)
    if not root:
        return False
    cwd = _normalize_rel(working_dir)
    if not cwd or cwd in {".", ""}:
        return False
    return cwd == root or cwd.startswith(root + "/")


def run_command_requires_confirm(
    *,
    command: str,
    working_dir: str = "",
    project_root: str = "",
    background: bool = False,
) -> tuple[bool, str]:
    """A2: build/test + readonly under project_root may skip; else confirm.

    Background escalate (D1) always confirms — same risk class as run_service start.

    Returns ``(needs_confirm, reason)``.
    """
    if background:
        return True, "background"
    kind = classify_run_command(command)
    if kind == "danger":
        return True, "danger"
    if kind == "install":
        return True, "install"
    if kind == "network":
        return True, "network"
    in_project = working_dir_under_project(working_dir, project_root)
    if kind in {"build_test", "readonly"} and in_project:
        return False, f"skip:{kind}"
    if not in_project:
        return True, "outside_project"
    return True, f"other:{kind}"


def is_node_modules_wipe_command(command: str) -> bool:
    """True when command deletes a node_modules tree (prefer repair_node_modules)."""
    text = (command or "").strip()
    if not text:
        return False
    normalized = text.replace("\\", "/")
    if "node_modules" not in normalized.lower():
        return False
    return bool(
        re.search(
            r"(?is)rmdir\s+/s|rd\s+/s|Remove-Item\b|\brm\s+-[a-z]*r|\bdel\s+/[sf]",
            text,
        )
    )
