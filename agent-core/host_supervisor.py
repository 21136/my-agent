"""Candidate Host Agent process switching with best-effort recovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


@dataclass(frozen=True, slots=True)
class HostProcessSpec:
    command: tuple[str, ...]
    cwd: Path
    name: str


@dataclass(frozen=True, slots=True)
class HostSwitchResult:
    ok: bool
    state: str
    recovered: bool
    old_state: Mapping[str, Any] | None = None
    candidate_state: Mapping[str, Any] | None = None
    recovery_state: Mapping[str, Any] | None = None
    error: str = ""


class HostSupervisor:
    """Orchestrate a candidate process without owning process implementation."""

    def __init__(
        self,
        *,
        start: Callable[[HostProcessSpec], Mapping[str, Any]],
        stop: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        health: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> None:
        self.start = start
        self.stop = stop
        self.health = health

    def switch(
        self,
        *,
        old_spec: HostProcessSpec,
        candidate_spec: HostProcessSpec,
        old_state: Mapping[str, Any],
        confirm: bool,
    ) -> HostSwitchResult:
        if not confirm:
            return HostSwitchResult(
                ok=True,
                state="dry_run",
                recovered=False,
                old_state=dict(old_state),
            )

        candidate = dict(self.start(candidate_spec))
        candidate_health = dict(self.health(candidate))
        if bool(candidate_health.get("ok")):
            stopped = dict(self.stop(old_state))
            if bool(stopped.get("ok")):
                return HostSwitchResult(
                    ok=True,
                    state="switched",
                    recovered=False,
                    old_state=dict(old_state),
                    candidate_state=candidate,
                )
            self.stop(candidate)
            recovered = dict(self.start(old_spec))
            return HostSwitchResult(
                ok=False,
                state="rolled_back" if bool(recovered.get("ok")) else "blocked",
                recovered=bool(recovered.get("ok")),
                old_state=dict(old_state),
                candidate_state=candidate,
                recovery_state=recovered,
                error="old process stop failed",
            )

        self.stop(candidate)
        recovered = dict(self.start(old_spec))
        recovery_health = dict(self.health(recovered)) if recovered.get("ok", True) else {"ok": False}
        recovered_ok = bool(recovered.get("ok")) and bool(recovery_health.get("ok"))
        return HostSwitchResult(
            ok=False,
            state="rolled_back" if recovered_ok else "blocked",
            recovered=recovered_ok,
            old_state=dict(old_state),
            candidate_state=candidate,
            recovery_state=recovered,
            error="candidate health check failed",
        )
