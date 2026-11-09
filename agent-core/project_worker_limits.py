"""Shared, fail-closed limits for project worker batches and workbench schedule dispatch."""
from __future__ import annotations

import os

# Hard host batch queue (see project_worker_host._MAX_WORKER_BATCH_SIZE).
_MAX_WORKER_BATCH_SIZE = 25
# Multi-project workbench selection cap (multi_project_work_records._validate_project_selection).
_MULTI_PROJECT_SELECTION_MAX = 6
_SCHEDULE_DISPATCH_MAX = min(_MULTI_PROJECT_SELECTION_MAX, _MAX_WORKER_BATCH_SIZE)
_CONCURRENT_MIN = 1
_CONCURRENT_MAX = 6


def project_worker_max_concurrent() -> int:
    """Distinct project workers that may run at once on one host (default 2)."""
    raw = os.environ.get("MY_AGENT_PROJECT_WORKER_MAX", "2")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 2
    if isinstance(value, bool) or not _CONCURRENT_MIN <= value <= _CONCURRENT_MAX:
        return 2
    return value


def schedule_dispatch_batch_max() -> int:
    """Max distinct projects in one explicit work-records.schedule.dispatch batch."""
    return _SCHEDULE_DISPATCH_MAX
