"""Authoritative lifecycle for one in-process Agent execution."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, replace
from typing import Callable, Literal

ExecutionStateName = Literal[
    "queued",
    "running",
    "stopping",
    "settled",
    "paused",
    "failed",
]

_ACTIVE_STATES = frozenset({"queued", "running", "stopping"})
_FINAL_STATES = frozenset({"settled", "paused", "failed"})
_PAUSED_FINISH_REASONS = frozenset(
    {"runaway_paused", "task_paused", "segment_cap_pause", "total_cap_exceeded"}
)


@dataclass(frozen=True)
class ExecutionSnapshot:
    run_id: str
    mode: str
    state: ExecutionStateName
    sequence: int
    cancel_requested: bool = False
    cancel_reason: str | None = None
    finish_reason: str | None = None
    ok: bool | None = None
    started_at: float | None = None
    ended_at: float | None = None

    def to_event(self) -> dict[str, object]:
        """Return the stable wire representation consumed by UI clients."""
        return {
            "type": "execution.state",
            "run_id": self.run_id,
            "mode": self.mode,
            "state": self.state,
            "sequence": self.sequence,
            "cancel_requested": self.cancel_requested,
            "cancel_reason": self.cancel_reason,
            "finish_reason": self.finish_reason,
            "ok": self.ok,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


class ExecutionLifecycle:
    """Thread-safe state machine for one bridge-owned execution record.

    The lifecycle deliberately has no side effects beyond state mutation. The
    caller is responsible for cancelling the model, closing tools, and
    publishing the returned snapshot.
    """

    def __init__(
        self,
        *,
        run_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._run_id_factory = run_id_factory or (lambda: uuid.uuid4().hex)
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._snapshot: ExecutionSnapshot | None = None
        self._sequence = 0

    def snapshot(self) -> ExecutionSnapshot | None:
        with self._lock:
            return self._snapshot

    def start_or_activate(self, mode: str) -> ExecutionSnapshot:
        normalized_mode = self._normalize_mode(mode)
        with self._lock:
            current = self._snapshot
            if current is not None and current.state in _ACTIVE_STATES:
                if current.state == "queued" and not current.cancel_requested:
                    self._snapshot = replace(
                        current,
                        mode=normalized_mode,
                        state="running",
                        sequence=self._next_sequence(),
                    )
                    return self._snapshot
                raise RuntimeError(
                    f"execution cannot start while state={current.state}"
                )

            self._snapshot = ExecutionSnapshot(
                run_id=self._run_id_factory(),
                mode=normalized_mode,
                state="running",
                sequence=self._next_sequence(),
                started_at=self._clock(),
            )
            return self._snapshot

    def enqueue(self, mode: str) -> ExecutionSnapshot:
        normalized_mode = self._normalize_mode(mode)
        with self._lock:
            current = self._snapshot
            if current is not None and current.state in _ACTIVE_STATES:
                raise RuntimeError(
                    f"execution cannot queue while state={current.state}"
                )
            self._snapshot = ExecutionSnapshot(
                run_id=self._run_id_factory(),
                mode=normalized_mode,
                state="queued",
                sequence=self._next_sequence(),
                started_at=self._clock(),
            )
            return self._snapshot

    def request_stop(
        self,
        reason: str = "user",
    ) -> tuple[ExecutionSnapshot | None, bool]:
        normalized_reason = reason.strip() or "user"
        with self._lock:
            current = self._snapshot
            if current is None or current.state not in {"queued", "running"}:
                return current, False
            self._snapshot = replace(
                current,
                state="stopping",
                sequence=self._next_sequence(),
                cancel_requested=True,
                cancel_reason=normalized_reason,
            )
            return self._snapshot, True

    def finish(
        self,
        finish_reason: str,
        *,
        ok: bool,
        final_state: str | None = None,
        run_id: str | None = None,
    ) -> tuple[ExecutionSnapshot | None, bool]:
        normalized_reason = finish_reason.strip() or ("completed" if ok else "error")
        with self._lock:
            current = self._snapshot
            if current is None or current.state in _FINAL_STATES:
                return current, False
            if run_id is not None and current.run_id != run_id:
                return current, False
            state = self._resolve_final_state(
                normalized_reason,
                ok=ok,
                requested=final_state,
            )
            self._snapshot = replace(
                current,
                state=state,
                sequence=self._next_sequence(),
                finish_reason=normalized_reason,
                ok=ok,
                ended_at=self._clock(),
            )
            return self._snapshot, True

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        normalized = mode.strip()
        if not normalized:
            raise ValueError("execution mode must not be empty")
        return normalized

    @staticmethod
    def _resolve_final_state(
        finish_reason: str,
        *,
        ok: bool,
        requested: str | None,
    ) -> ExecutionStateName:
        if requested is not None:
            if requested not in _FINAL_STATES:
                raise ValueError(f"invalid execution final state: {requested}")
            return requested  # type: ignore[return-value]
        if finish_reason in _PAUSED_FINISH_REASONS:
            return "paused"
        if ok or finish_reason in {"cancelled", "timeout", "stopped"}:
            return "settled"
        return "failed"
