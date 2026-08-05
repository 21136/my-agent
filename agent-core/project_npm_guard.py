"""PROJECT-MODE E7/E9 helpers for run_command (migrated from archived npm_exec)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_NPM_INSTALL_CMD = re.compile(
    r"(?is)^\s*(?:npm|pnpm|yarn)(?:\.cmd)?\s+(?:install|i|ci|add)\b"
)


def coalesce_working_dir(payload: dict[str, Any]) -> str:
    """E7: accept cwd as alias for working_dir."""
    working = payload.get("working_dir", "")
    if isinstance(working, str) and working.strip():
        return working.strip()
    cwd = payload.get("cwd", "")
    if isinstance(cwd, str) and cwd.strip():
        return cwd.strip()
    return ""


def redundant_npm_install_error(
    cwd: Path,
    command: str,
    *,
    force_install: bool = False,
) -> str | None:
    """E9: reject redundant npm/pnpm/yarn install when node_modules exists."""
    if force_install:
        return None
    if not _NPM_INSTALL_CMD.match((command or "").strip()):
        return None
    if not (cwd / "node_modules").is_dir():
        return None
    return (
        f"{cwd} 已有 node_modules；测前端/验证请直接 "
        "run_command command='npm run build' 或 'npm test'，"
        "不要先 install。确需重装时传 force_install=true"
    )
