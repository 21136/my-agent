"""Pytest hooks for agent-core tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))


@pytest.fixture(autouse=True)
def _linux_project_worker_directory_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """ProjectTextReader root identity is Windows-only; batch auth needs stat identity elsewhere."""
    if os.name == "nt":
        return
    from project_worker_execution import ProjectWorkerCoordinator

    def _stat_identity(path: Path) -> tuple[int, int, int]:
        dev, ino = ProjectWorkerCoordinator._directory_stat_identity(path)
        return dev, ino, 0

    monkeypatch.setattr(
        ProjectWorkerCoordinator,
        "_secure_directory_identity",
        staticmethod(_stat_identity),
    )
