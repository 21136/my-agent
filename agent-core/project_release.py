"""Persistent human release acceptance for Desktop projects (T-5818)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RELEASE_ACCEPTANCE_FILENAME = "release_acceptance.json"
RELEASE_ACCEPTANCE_SCHEMA_VERSION = "0.1"


def release_acceptance_path(project_root: Path | str) -> Path:
    return Path(project_root) / ".plan-agent" / RELEASE_ACCEPTANCE_FILENAME


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def empty_release_acceptance() -> dict[str, Any]:
    return {
        "accepted": False,
        "accepted_at": None,
        "release_revision": None,
        "checklist": {},
    }


def load_release_acceptance(
    project_root: Path | str,
    project_id: str,
    *,
    release_revision: str | None,
) -> dict[str, Any]:
    """Load acceptance and invalidate it when RELEASE revision moved."""
    path = release_acceptance_path(project_root)
    if not path.is_file():
        return empty_release_acceptance()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_release_acceptance()
    if not isinstance(payload, dict) or payload.get("schema_version") != RELEASE_ACCEPTANCE_SCHEMA_VERSION:
        return empty_release_acceptance()
    if payload.get("project_id") != project_id:
        return empty_release_acceptance()
    stored_revision = payload.get("release_revision")
    accepted = bool(payload.get("accepted")) and bool(stored_revision) and stored_revision == release_revision
    checklist = payload.get("checklist")
    return {
        "accepted": accepted,
        "accepted_at": payload.get("accepted_at") if accepted else None,
        "release_revision": stored_revision if accepted else release_revision,
        "checklist": dict(checklist) if isinstance(checklist, dict) and accepted else {},
    }


def save_release_acceptance(
    project_root: Path | str,
    project_id: str,
    *,
    release_revision: str,
    checklist: dict[str, bool],
    accepted_at: str | None = None,
) -> dict[str, Any]:
    """Persist a human acceptance record tied to one RELEASE revision."""
    result = {
        "schema_version": RELEASE_ACCEPTANCE_SCHEMA_VERSION,
        "project_id": project_id,
        "accepted": True,
        "accepted_at": accepted_at or _now_iso(),
        "release_revision": release_revision,
        "checklist": {key: bool(value) for key, value in checklist.items()},
    }
    path = release_acceptance_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "accepted": True,
        "accepted_at": result["accepted_at"],
        "release_revision": release_revision,
        "checklist": dict(result["checklist"]),
    }
