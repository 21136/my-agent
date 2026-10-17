"""Runaway orchestration ports — execution driver boundary (Phase 62 R0)."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AgentDriver(Protocol):
    """What the runaway **orchestration kernel** may call on the LLM driver.

    The kernel owns phase/plan/acceptance/continuation; the driver owns the
    parent tool loop and runaway-specific agent hooks. ``agent.py`` implements
    this protocol indirectly via ``AgentDriverAdapter``.
    """

    @property
    def session(self) -> Any: ...

    @property
    def cancel_event(self) -> Any: ...

    def prepare_session_for_turn(self) -> None: ...

    def sync_turn_mode(self) -> None: ...

    def sync_allowed_evolved(self) -> None: ...

    def ensure_executor_project_scope(self) -> None: ...

    def apply_runaway_execution_context(
        self,
        *,
        write_scope: list[str] | None,
        forbid_plan_partner: bool,
        workflow_stage: str,
    ) -> None: ...

    def clear_runaway_runtime_workflow_stage(self) -> None: ...

    def begin_executor_turn(self) -> None: ...

    def emit_turn_event(self, payload: dict[str, Any]) -> None: ...

    def build_llm_tools(self) -> list[dict[str, Any]]: ...

    def resolve_main_turn_model(self) -> str: ...

    def run_parent_tool_loop(
        self,
        *,
        max_rounds: int,
        tools: list[dict[str, Any]],
        model: str,
        segment_start_index: int,
    ) -> Any: ...

    def flush_runaway_plan_proposals(self) -> None: ...

    def finish_short_tool_loop(
        self,
        *,
        loop_result: Any,
        loop_max: int,
        intent: str,
        spawn_explore_flag: bool,
        subagent_tool_rounds: int,
    ) -> Any: ...

    def pending_runaway_proposal_ids(self, project_id: str) -> list[str]: ...

    def runaway_chain_user_line(self) -> str: ...

    def pause_runaway(self, reason: str) -> None: ...

