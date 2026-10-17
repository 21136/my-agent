"""Adapter: ``Agent`` → ``AgentDriver`` for the orchestration kernel."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent import Agent


class AgentDriverAdapter:
    """Thin facade so orchestration does not import private Agent methods ad hoc."""

    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    @property
    def agent(self) -> Agent:
        return self._agent

    @property
    def session(self):
        return self._agent.session

    @property
    def cancel_event(self):
        return self._agent.cancel_event

    def prepare_session_for_turn(self) -> None:
        self.session.ensure_messages_loaded()
        from agent import prepare_session_for_s4

        prepare_session_for_s4(self.session)

    def sync_turn_mode(self) -> None:
        self._agent._sync_turn_mode()

    def sync_allowed_evolved(self) -> None:
        self._agent._sync_allowed_evolved()

    def ensure_executor_project_scope(self) -> None:
        self._agent.executor._ensure_project_scope_tools_allowed()

    def apply_runaway_execution_context(
        self,
        *,
        write_scope: list[str] | None,
        forbid_plan_partner: bool,
        workflow_stage: str,
    ) -> None:
        executor_session = self._agent.executor.session
        executor_session.runaway_v2_write_scope = write_scope
        executor_session.runaway_v2_forbid_plan_partner = bool(forbid_plan_partner)
        executor_session.runaway_v2_runtime_workflow_stage = workflow_stage

    def clear_runaway_runtime_workflow_stage(self) -> None:
        self._agent.executor.session.runaway_v2_runtime_workflow_stage = ""

    def begin_executor_turn(self) -> None:
        self._agent.executor.begin_turn()

    def emit_turn_event(self, payload: dict[str, Any]) -> None:
        self._agent._emit_turn_event(payload)

    def build_llm_tools(self) -> list[dict[str, Any]]:
        from agent import build_llm_tools

        return build_llm_tools(self.session, registry=self._agent.executor.registry)

    def resolve_main_turn_model(self) -> str:
        from llm_routing import resolve_model_id_for_role

        return resolve_model_id_for_role("main_turn", self.session.meta)

    def run_parent_tool_loop(self, *, max_rounds: int, tools: list, model: str, segment_start_index: int):
        return self._agent._run_parent_tool_loop(
            max_rounds=max_rounds,
            tools=tools,
            model=model,
            segment_start_index=segment_start_index,
        )

    def flush_runaway_plan_proposals(self) -> None:
        self._agent._flush_runaway_plan_proposals()

    def finish_short_tool_loop(self, **kwargs):
        return self._agent._finish_short_tool_loop(**kwargs)

    def pending_runaway_proposal_ids(self, project_id: str) -> list[str]:
        return self._agent._pending_runaway_proposal_ids(project_id)

    def runaway_chain_user_line(self) -> str:
        return self._agent.runaway_chain_user_line()

    def pause_runaway(self, reason: str) -> None:
        self._agent._pause_runaway(reason)
