"""Runaway v2 — external checklist · single turn · thin harness · profile."""

from __future__ import annotations

from runaway_v2.config import runaway_v2_env_enabled


def runaway_v2_enabled(session: object) -> bool:
    """True when env flag and project runaway are both on."""
    meta = getattr(session, "meta", session)
    return (
        runaway_v2_env_enabled()
        and bool(getattr(meta, "project_runaway_enabled", False))
        and bool(str(getattr(meta, "project_id", "") or "").strip())
    )
