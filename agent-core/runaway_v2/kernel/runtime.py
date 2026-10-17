"""Runaway orchestration kernel — single driver for v2 turns (Phase 62 R0)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from paths import AgentPaths
from runaway_v2.acceptance import run_acceptance
from runaway_v2.checklist import load_or_build_checklist, save_checklist
from runaway_v2.config import max_auto_turns
from runaway_v2.continuation import (
    is_fatal_finish_reason,
    observe_runaway_turn,
    pending_runaway_work,
    repair_stale_v2_plan_status,
    should_continue_runaway,
)
from runaway_v2.kernel.driver_adapter import AgentDriverAdapter
from runaway_v2.kernel.ports import AgentDriver
from runaway_v2.kernel.project_sync import RunawayProjectSync
from runaway_v2.phase import derive_phase
from runaway_v2.plan import build_turn_plan
from runaway_v2.progress import append_progress

if TYPE_CHECKING:
    from agent import Agent, TurnResult


@dataclass
class RunawayRuntime:
    """Orchestration kernel: phase → plan → execute → accept → continue.

    Transport (WS lease, idle resume) and v1 chain hooks stay outside this
    module; only in-turn continuation is owned here.
    """

    agent: Agent
    chain_depth: int = 0
    driver: AgentDriver | None = None
    _driver: AgentDriver = field(init=False, repr=False)
    _sync: RunawayProjectSync = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._driver = self.driver if self.driver is not None else AgentDriverAdapter(self.agent)
        self._sync = RunawayProjectSync(self._driver)

    @property
    def session(self):
        return self._driver.session

    @property
    def paths(self) -> AgentPaths:
        return self.session.paths

    def run_turn(
        self,
        user_text: str,
        *,
        spawn_explore: bool | None = None,
        force_skip_plan_spawn: bool = False,
    ) -> TurnResult:
        self.agent._begin_skill_turn(user_text)
        try:
            return self._run_turn_impl(
                user_text,
                spawn_explore=spawn_explore,
                force_skip_plan_spawn=force_skip_plan_spawn,
            )
        finally:
            self.agent._clear_skill_turn()

    def _run_turn_impl(
        self,
        user_text: str,
        *,
        spawn_explore: bool | None = None,
        force_skip_plan_spawn: bool = False,
    ) -> TurnResult:
        from agent import TurnResult
        from turn_intent import classify_turn, intent_label

        self._driver.prepare_session_for_turn()
        repair_stale_v2_plan_status(self.paths, self.session)
        self._driver.sync_turn_mode()
        self._driver.sync_allowed_evolved()
        self._driver.ensure_executor_project_scope()
        self.session.subagent_overlay = None
        self.session.turn_intent = None

        pid = str(self.session.meta.project_id or "").strip()
        if not pid:
            return TurnResult(
                assistant_text="狂奔模式需要先绑定项目。",
                tool_rounds=0,
                finish_reason="stop",
            )

        return self._run_turn_inner(
            user_text,
            spawn_explore=spawn_explore,
            force_skip_plan_spawn=force_skip_plan_spawn,
            classify_turn=classify_turn,
            intent_label=intent_label,
            turn_result_cls=TurnResult,
        )

    def _run_turn_inner(
        self,
        user_text: str,
        *,
        spawn_explore: bool | None,
        force_skip_plan_spawn: bool,
        classify_turn,
        intent_label,
        turn_result_cls,
    ):
        intent = classify_turn(user_text)
        from exec_reliability import (
            is_runaway_continue_utterance,
            is_runaway_harness_utterance,
        )

        if is_runaway_continue_utterance(user_text):
            intent = "execute"
        allow_user_request = not is_runaway_harness_utterance(user_text) and not is_runaway_continue_utterance(
            user_text
        )
        self.session.turn_intent = intent
        self.session.append_message({"role": "user", "content": user_text})

        checklist = load_or_build_checklist(self.paths, pid := str(self.session.meta.project_id))
        paused_reason = str(
            getattr(self.session.meta, "project_runaway_paused_reason", "") or ""
        ).strip()
        if paused_reason:
            text = f"狂奔已暂停：{paused_reason}。请先恢复狂奔后再继续。"
            self.session.append_message({"role": "assistant", "content": text})
            self.session.save()
            return turn_result_cls(
                assistant_text=text,
                tool_rounds=0,
                finish_reason="runaway_paused",
                turn_intent=intent,
            )
        resume_hint = ""

        if is_runaway_harness_utterance(user_text):
            from runaway_v2.resume import bootstrap_on_resume

            resume_hint, bootstrap_changed = bootstrap_on_resume(self.paths, pid, checklist)
            if bootstrap_changed:
                save_checklist(self.paths, checklist)
        phase = derive_phase(
            self.paths,
            pid,
            plan_status=str(self.session.meta.project_plan_status or "draft"),
            checklist=checklist,
        )
        if phase.phase == "prepare":
            prepare_plan = build_turn_plan(
                paths=self.paths,
                project_id=pid,
                phase=phase,
                checklist=checklist,
                user_text=user_text,
                intent=intent,
                allow_user_request=allow_user_request,
            )
            prepare_hook = run_acceptance(self.paths, pid, prepare_plan.acceptance_item)
            if prepare_hook.ok:
                self._sync.advance_after_prepare_ready(pid)
                checklist = load_or_build_checklist(self.paths, pid)
                phase = derive_phase(
                    self.paths,
                    pid,
                    plan_status=str(self.session.meta.project_plan_status or "draft"),
                    checklist=checklist,
                )
        plan = build_turn_plan(
            paths=self.paths,
            project_id=pid,
            phase=phase,
            checklist=checklist,
            user_text=user_text,
            intent=intent,
            allow_user_request=allow_user_request,
        )
        self._sync.mirror_runtime(phase, plan, checklist)

        if plan.mode == "human":
            text = plan.user_line
            self.session.append_message({"role": "assistant", "content": text})
            self.session.save()
            return turn_result_cls(
                assistant_text=text,
                tool_rounds=0,
                finish_reason="stop",
                turn_intent=intent,
            )

        if plan.phase == "release_wait" and plan.max_tool_rounds == 0:
            text = plan.user_line
            self.session.append_message({"role": "assistant", "content": text})
            self.session.save()
            return turn_result_cls(
                assistant_text=text,
                tool_rounds=0,
                finish_reason="stop",
                turn_intent=intent,
            )

        self._sync.apply_plan(plan, phase)
        self._driver.begin_executor_turn()
        self._driver.emit_turn_event(
            {
                "type": "turn.start",
                "intent": intent,
                "intent_label": intent_label(intent),
                "runaway_version": 2,
                "runaway_phase": plan.phase,
            }
        )

        from runaway_v2.plan import _with_locale_append

        segment_start_index = len(self.session.messages)
        system_bits = [plan.system_append, resume_hint]
        system_content = _with_locale_append("\n".join(part for part in system_bits if part and part.strip()))
        self.session.append_message(
            {"role": "system", "content": system_content},
            persist=False,
        )
        tools = self._driver.build_llm_tools()
        loop_result = self._driver.run_parent_tool_loop(
            max_rounds=plan.max_tool_rounds,
            tools=tools,
            model=self._driver.resolve_main_turn_model(),
            segment_start_index=segment_start_index,
        )
        self._driver.flush_runaway_plan_proposals()
        hook = run_acceptance(self.paths, pid, plan.acceptance_item)
        self._sync.commit_acceptance(checklist, plan, hook)
        save_checklist(self.paths, checklist)
        self._sync.mirror_runtime(phase, plan, checklist)

        next_focus = "继续下一项"
        if plan.acceptance_item is not None and not hook.ok:
            if plan.mode == "directed":
                next_focus = f"{plan.acceptance_item.id} directed"
            elif plan.acceptance_item.directed_used:
                next_focus = "human"
            else:
                next_focus = plan.acceptance_item.id
        append_progress(
            self.paths,
            pid,
            phase=plan.phase,
            item=plan.acceptance_item,
            status="passed" if hook.ok else "failed",
            summary=hook.tail or plan.user_line,
            next_focus=next_focus,
        )

        result = self._driver.finish_short_tool_loop(
            loop_result=loop_result,
            loop_max=plan.max_tool_rounds,
            intent=intent,
            spawn_explore_flag=False,
            subagent_tool_rounds=0,
        )

        if is_fatal_finish_reason(result.finish_reason):
            self._sync.pause_after_fatal_finish(result.finish_reason)
            return result

        from exec_reliability import is_runaway_harness_utterance

        continuation_allowed = observe_runaway_turn(
            self.paths,
            pid,
            self.session,
            automatic=is_runaway_harness_utterance(user_text) or self.chain_depth > 0,
        )
        remaining = self._driver.pending_runaway_proposal_ids(pid)
        if (
            remaining
            and continuation_allowed
            and not self._driver.cancel_event.is_set()
            and self.chain_depth < max_auto_turns()
        ):
            self.chain_depth += 1
            from user_copy import harness_stale_proposals_chain_line

            chained = self.run_turn(
                harness_stale_proposals_chain_line(),
                spawn_explore=False,
                force_skip_plan_spawn=True,
            )
            self.chain_depth -= 1
            return turn_result_cls(
                assistant_text=chained.assistant_text,
                tool_rounds=result.tool_rounds + chained.tool_rounds,
                finish_reason=chained.finish_reason,
                turn_intent=intent,
                total_tool_rounds=result.total_tool_rounds + chained.total_tool_rounds,
                notices=[*result.notices, *chained.notices],
            )

        if (
            continuation_allowed
            and not self._driver.cancel_event.is_set()
            and should_continue_runaway(
                self.paths,
                pid,
                self.session,
                in_turn_depth=self.chain_depth,
                max_in_turn_depth=max_auto_turns(),
                observe=False,
            )
        ):
            self.chain_depth += 1
            pending = pending_runaway_work(self.paths, pid, self.session)
            chain_line = pending.chain_user_line if pending is not None else self._driver.runaway_chain_user_line()
            chained = self.run_turn(chain_line, spawn_explore=False, force_skip_plan_spawn=True)
            self.chain_depth -= 1
            return turn_result_cls(
                assistant_text=chained.assistant_text,
                tool_rounds=result.tool_rounds + chained.tool_rounds,
                finish_reason=chained.finish_reason,
                turn_intent=intent,
                total_tool_rounds=result.total_tool_rounds + chained.total_tool_rounds,
                notices=[*result.notices, *chained.notices],
            )
        return result
