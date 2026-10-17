"""Runaway v2 controller — compatibility façade over :class:`RunawayRuntime`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from runaway_v2.checklist import Checklist
from runaway_v2.kernel.driver_adapter import AgentDriverAdapter
from runaway_v2.kernel.project_sync import RunawayProjectSync
from runaway_v2.kernel.runtime import RunawayRuntime
from runaway_v2.phase import PhaseResult
from runaway_v2.plan import TurnPlan

if TYPE_CHECKING:
    from agent import Agent, TurnResult


@dataclass
class RunawayController:
    """Deprecated entry name; new code should use ``RunawayRuntime`` directly."""

    agent: Agent
    _chain_depth: int = 0
    _sync: RunawayProjectSync | None = field(default=None, repr=False)

    def _project_sync(self) -> RunawayProjectSync:
        if self._sync is None:
            self._sync = RunawayProjectSync(AgentDriverAdapter(self.agent))
        return self._sync

    def run_turn(
        self,
        user_text: str,
        *,
        spawn_explore: bool | None = None,
        force_skip_plan_spawn: bool = False,
    ) -> TurnResult:
        runtime = RunawayRuntime(self.agent, chain_depth=self._chain_depth)
        result = runtime.run_turn(
            user_text,
            spawn_explore=spawn_explore,
            force_skip_plan_spawn=force_skip_plan_spawn,
        )
        self._chain_depth = runtime.chain_depth
        return result

    # Unit tests and legacy callers target these hooks on the controller name.
    def _commit_acceptance(self, checklist: Checklist, plan: TurnPlan, hook) -> None:
        self._project_sync().commit_acceptance(
            checklist,
            plan,
            hook,
            advance_after_prepare=self._advance_after_prepare_ready,
        )

    def _advance_after_prepare_ready(self, project_id: str) -> None:
        self._project_sync().advance_after_prepare_ready(project_id)

    def _apply_plan(self, plan: TurnPlan, phase: PhaseResult) -> None:
        self._project_sync().apply_plan(plan, phase)

    def _mirror_runtime(self, phase: PhaseResult, plan: TurnPlan, checklist: Checklist) -> None:
        self._project_sync().mirror_runtime(phase, plan, checklist)
