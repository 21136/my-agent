"""Companion home (小魔王空间) chat surface policy — defers noisy host chrome from main chat."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from session import Session


def companion_home_unbound_desktop(session: object | None) -> bool:
    """Main companion chat: desktop harness without a bound project_id."""
    if session is None:
        return False
    meta = getattr(session, "meta", None)
    if meta is None:
        return False
    if getattr(meta, "project_id", ""):
        return False
    return getattr(meta, "harness", "") == "desktop"


def companion_home_defers_project_scoped_chat(session: object | None) -> bool:
    """Project-scoped history snapshots belong in 当前工作 / 工作动态, not companion chat."""
    return companion_home_unbound_desktop(session)
