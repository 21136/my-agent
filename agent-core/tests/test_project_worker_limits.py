from __future__ import annotations

import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CORE))

from project_worker_limits import project_worker_max_concurrent, schedule_dispatch_batch_max


def test_schedule_dispatch_batch_max_matches_multi_project_selection():
    assert schedule_dispatch_batch_max() == 6


def test_project_worker_max_concurrent_defaults_and_clamps(monkeypatch):
    monkeypatch.delenv("MY_AGENT_PROJECT_WORKER_MAX", raising=False)
    assert project_worker_max_concurrent() == 2
    monkeypatch.setenv("MY_AGENT_PROJECT_WORKER_MAX", "4")
    assert project_worker_max_concurrent() == 4
    monkeypatch.setenv("MY_AGENT_PROJECT_WORKER_MAX", "99")
    assert project_worker_max_concurrent() == 2
