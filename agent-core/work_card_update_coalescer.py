"""Bounded, per-project coalescing for host-authored work-card updates."""
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any


@dataclass
class _Pending:
    handle: asyncio.TimerHandle
    publish: Callable[[], Any]


class ProjectWorkCardUpdateCoalescer:
    """Keep the first deadline per project and publish only its latest callback.

    This is deliberately ephemeral: closing/restarting the connection cancels the
    timer and never replays project work. Durable work-record/card state remains
    the source of truth; callers decide whether a publication can be reconstructed.
    """

    def __init__(self, *, delay_seconds: float = 30.0,
                 loop: asyncio.AbstractEventLoop | None = None) -> None:
        if isinstance(delay_seconds, bool) or not isinstance(delay_seconds, (int, float)):
            raise ValueError("delay_seconds must be finite and positive")
        if not math.isfinite(delay_seconds) or delay_seconds <= 0:
            raise ValueError("delay_seconds must be finite and positive")
        self._delay = float(delay_seconds)
        self._loop = loop or asyncio.get_running_loop()
        self._pending: dict[str, _Pending] = {}
        self._closed = False

    def schedule(
        self, project_id: str, publish: Callable[[], Any], *, immediate: bool = False,
        delay_seconds: float | None = None,
    ) -> None:
        if self._closed:
            return
        if type(project_id) is not str or not project_id.strip() or not callable(publish):
            raise ValueError("a project identity and publisher are required")
        delay = self._delay if delay_seconds is None else delay_seconds
        if (isinstance(delay, bool) or not isinstance(delay, (int, float))
                or not math.isfinite(delay) or delay <= 0):
            raise ValueError("delay_seconds must be finite and positive")
        if immediate:
            pending = self._pending.pop(project_id, None)
            if pending is not None:
                pending.handle.cancel()
            publish()
            return
        pending = self._pending.get(project_id)
        if pending is not None:
            # Preserve the first update's deadline; later updates only refresh the
            # function that will read and publish the latest persistent snapshot.
            pending.publish = publish
            return
        holder: dict[str, _Pending] = {}

        def fire() -> None:
            current = self._pending.get(project_id)
            if current is not holder.get("pending"):
                return
            self._pending.pop(project_id, None)
            current.publish()

        handle = self._loop.call_later(float(delay), fire)
        pending = _Pending(handle=handle, publish=publish)
        holder["pending"] = pending
        self._pending[project_id] = pending

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for pending in self._pending.values():
            pending.handle.cancel()
        self._pending.clear()
