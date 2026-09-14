"""Runaway v2 controller — single turn entry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from paths import AgentPaths
from runaway_v2.acceptance import format_hook_failure, run_acceptance
from runaway_v2.checklist import (
    Checklist,
    load_or_build_checklist,
    save_checklist,
)
from runaway_v2.config import directed_after, max_auto_turns
from runaway_v2.continuation import (
    is_fatal_finish_reason,
    observe_runaway_turn,
    pending_runaway_work,
    repair_stale_v2_plan_status,
    should_continue_runaway,
)
from runaway_v2.phase import PhaseResult, derive_phase
from runaway_v2.plan import TurnPlan, build_turn_plan
from runaway_v2.progress import append_progress
from runaway_v2.state import build_v2_state_fields

if TYPE_CHECKING:
    from agent import Agent, TurnResult


_WORKFLOW_STAGE_BY_PHASE = {
    "prepare": "documentation",
    "implement": "implementation",
    "verify": "verification",
    "release_wait": "release",
}


@dataclass
class RunawayController:
    agent: Agent
    _chain_depth: int = 0

    @property
    def session(self):
        return self.agent.session

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
        from agent import TurnResult
        from turn_intent import classify_turn, intent_label

        self.session.ensure_messages_loaded()
        from agent import prepare_session_for_s4

        prepare_session_for_s4(self.session)
        repair_stale_v2_plan_status(self.paths, self.session)
        self.agent._sync_turn_mode()
        self.agent._sync_allowed_evolved()
        self.agent.executor._ensure_project_scope_tools_allowed()
        self.session.subagent_overlay = None
        self.session.turn_intent = None

        pid = str(self.session.meta.project_id or "").strip()
        if not pid:
            return TurnResult(
                assistant_text="狂奔模式需要先绑定项目。",
                tool_rounds=0,
                finish_reason="stop",
            )

        # Lease is held by server._run_line for the whole turn; do not re-acquire here.
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
        # A session can be interrupted after the project artifacts are
        # complete but before the legacy plan metadata is promoted. Recover
        # that stale mirror before asking the model to do another prepare turn.
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
                self._advance_after_prepare_ready(pid)
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
        self._mirror_runtime(phase, plan, checklist)

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

        self._apply_plan(plan, phase)
        self.agent.executor.begin_turn()
        self.agent._emit_turn_event(
            {
                "type": "turn.start",
                "intent": intent,
                "intent_label": intent_label(intent),
                "runaway_version": 2,
                "runaway_phase": plan.phase,
            }
        )

        from agent import build_llm_tools
        from llm_routing import resolve_model_id_for_role

        segment_start_index = len(self.session.messages)
        from runaway_v2.plan import _with_locale_append

        system_bits = [plan.system_append, resume_hint]
        system_content = _with_locale_append("\n".join(part for part in system_bits if part and part.strip()))
        self.session.append_message(
            {"role": "system", "content": system_content},
            persist=False,
        )
        tools = build_llm_tools(self.session, registry=self.agent.executor.registry)
        loop_result = self.agent._run_parent_tool_loop(
            max_rounds=plan.max_tool_rounds,
            tools=tools,
            model=resolve_model_id_for_role("main_turn", self.session.meta),
            segment_start_index=segment_start_index,
        )
        self.agent._flush_runaway_plan_proposals()
        hook = run_acceptance(self.paths, pid, plan.acceptance_item)
        self._commit_acceptance(checklist, plan, hook)
        save_checklist(self.paths, checklist)
        self._mirror_runtime(phase, plan, checklist)

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

        result = self.agent._finish_short_tool_loop(
            loop_result=loop_result,
            loop_max=plan.max_tool_rounds,
            intent=intent,
            spawn_explore_flag=False,
            subagent_tool_rounds=0,
        )

        # A transport/timeout/cancel result is a terminal turn outcome. It
        # must never be converted into another harness prompt, otherwise one
        # provider failure can fan out into a long recursive retry chain.
        if is_fatal_finish_reason(result.finish_reason):
            self._pause_after_fatal_finish(result.finish_reason)
            return result

        from exec_reliability import is_runaway_harness_utterance

        continuation_allowed = observe_runaway_turn(
            self.paths,
            pid,
            self.session,
            automatic=is_runaway_harness_utterance(user_text) or self._chain_depth > 0,
        )
        remaining = self.agent._pending_runaway_proposal_ids(pid)
        if (
            remaining
            and continuation_allowed
            and not self.agent.cancel_event.is_set()
            and self._chain_depth < max_auto_turns()
        ):
            self._chain_depth += 1
            from user_copy import harness_stale_proposals_chain_line

            chained = self.run_turn(
                harness_stale_proposals_chain_line(),
                spawn_explore=False,
                force_skip_plan_spawn=True,
            )
            self._chain_depth -= 1
            return turn_result_cls(
                assistant_text=chained.assistant_text,
                tool_rounds=result.tool_rounds + chained.tool_rounds,
                finish_reason=chained.finish_reason,
                turn_intent=intent,
                total_tool_rounds=result.total_tool_rounds + chained.total_tool_rounds,
                notices=[*result.notices, *chained.notices],
            )

        if continuation_allowed and should_continue_runaway(
            self.paths,
            pid,
            self.session,
            in_turn_depth=self._chain_depth,
            max_in_turn_depth=max_auto_turns(),
            observe=False,
        ):
            self._chain_depth += 1
            pending = pending_runaway_work(self.paths, pid, self.session)
            chain_line = pending.chain_user_line if pending is not None else self.agent.runaway_chain_user_line()
            chained = self.run_turn(chain_line, spawn_explore=False, force_skip_plan_spawn=True)
            self._chain_depth -= 1
            return turn_result_cls(
                assistant_text=chained.assistant_text,
                tool_rounds=result.tool_rounds + chained.tool_rounds,
                finish_reason=chained.finish_reason,
                turn_intent=intent,
                total_tool_rounds=result.total_tool_rounds + chained.total_tool_rounds,
                notices=[*result.notices, *chained.notices],
            )
        return result

    def _pause_after_fatal_finish(self, finish_reason: str | None) -> None:
        reason = (finish_reason or "").strip()
        pause_reason = {
            "cancelled": "用户停止了狂奔",
            "timeout": "运行时间达到上限",
            "error": "狂奔运行异常",
        }.get(reason)
        if pause_reason is not None:
            self.agent._pause_runaway(pause_reason)

    def _apply_plan(self, plan: TurnPlan, phase: PhaseResult) -> None:
        workflow = _WORKFLOW_STAGE_BY_PHASE.get(plan.phase, "")
        if workflow:
            self.session.meta.project_workflow_stage = workflow
        if phase.phase == "release_wait" and plan.phase == "implement":
            # A user-requested maintenance turn temporarily reopens the
            # implementation context.  Keep the legacy mirror aligned while
            # the model sees the prompt; _mirror_runtime() derives the
            # persisted release_wait state again after the turn.
            self.session.meta.project_runaway_checkpoint = "implementing"
        if plan.active_task_id:
            self.session.meta.project_active_task_id = plan.active_task_id
        scope = plan.allowed_write_globs if plan.allowed_write_globs else None
        self.agent.executor.session.runaway_v2_write_scope = scope
        self.agent.executor.session.runaway_v2_forbid_plan_partner = bool(plan.forbid_plan_partner)
        self.agent.executor.session.runaway_v2_runtime_workflow_stage = workflow
        self.agent._sync_turn_mode()

    def _commit_acceptance(self, checklist: Checklist, plan: TurnPlan, hook) -> None:
        item = plan.acceptance_item
        if item is None:
            return
        target = next((entry for entry in checklist.items if entry.id == item.id), None)
        if target is None:
            # PREPARE is a derived gate, not a persisted checklist item. Its
            # successful hook still has to promote the project into execution.
            if item.id == "PREPARE" and hook.ok:
                project_id = str(self.session.meta.project_id or "").strip()
                self._advance_after_prepare_ready(project_id)
            return
        project_id = str(self.session.meta.project_id or "").strip()
        from datetime import datetime, timezone

        target.last_run_at = (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        if hook.ok:
            target.status = "passed"
            target.last_failure = None
            if plan.phase == "prepare" and target.id == "PREPARE":
                self._advance_after_prepare_ready(project_id)
            return
        target.status = "failed"
        target.attempts += 1
        target.last_failure = format_hook_failure(hook)
        if plan.mode == "directed":
            target.directed_used = True
        elif target.attempts >= directed_after() and not target.directed_used:
            pass

    def _advance_after_prepare_ready(self, project_id: str) -> None:
        """Exit prepare once documentation_ready passes — v2 does not use v1 CLI stage gates."""
        from project_mode import (
            next_open_task,
            read_project_artifacts,
            snapshot_plan_fingerprints,
        )
        from runaway_flow import sync_checkpoint_to_target, task_fingerprint
        from session import utc_now_iso

        artifacts = read_project_artifacts(self.paths, project_id)
        _, task_text, task_id = next_open_task(artifacts.get("TASKS.md", ""))
        self.session.meta.project_plan_status = "confirmed"
        # The legacy after-turn hook still checks structural plan fingerprints.
        # v2 may have just created or normalized the project documents during
        # prepare; refresh the confirmation baseline now or that hook turns
        # the session back into plan_dirty and re-enters prepare forever.
        snapshot_plan_fingerprints(self.session, self.paths, project_id)
        self.session.meta.project_workflow_stage = "implementation"
        if task_id:
            self.session.meta.project_active_task_id = task_id
            self.session.meta.project_runaway_task_fingerprint = task_fingerprint(
                task_id,
                task_text or "",
            )
        sync_checkpoint_to_target(self.session.meta, "implementing")
        self.session.meta.updated_at = utc_now_iso()
        self.session.save()
        self.agent._sync_turn_mode()

    def _mirror_runtime(self, phase: PhaseResult, plan: TurnPlan, checklist: Checklist) -> None:
        # The tool loop may have changed TASKS/VERIFY/ENV after the plan was
        # built. Rebuild from disk so the mirrored phase reflects the same
        # checklist that the next turn will consume.
        runtime_checklist = load_or_build_checklist(
            self.paths,
            str(self.session.meta.project_id or ""),
        )
        fields = build_v2_state_fields(
            self.paths,
            project_id=str(self.session.meta.project_id or ""),
            plan_status=str(self.session.meta.project_plan_status or "draft"),
            checklist=runtime_checklist,
        )
        self.session.meta.project_runaway_v2_phase = fields.get("runaway_phase", "")
        self.session.meta.project_runaway_v2_mode = plan.mode
        self.session.meta.project_runaway_v2_user_line = (
            fields.get("runaway_user_line") or plan.user_line
        )
        self.session.meta.project_runaway_v2_blocked = bool(fields.get("runaway_blocked"))
        checkpoint_map = {
            "prepare": "preparing",
            "implement": "implementing",
            "verify": "verifying",
            "release_wait": "release_wait",
            "human": "paused",
        }
        workflow_map = {
            "prepare": "documentation",
            "implement": "implementation",
            "verify": "verification",
            "release_wait": "release",
            # Human intervention remains part of the verification surface;
            # the session model has no separate human workflow stage.
            "human": "verification",
        }
        # ``phase`` is the phase at turn start. Acceptance hooks can advance
        # the project before this mirror runs, so prefer the freshly derived
        # phase in the state payload.
        current_phase = str(fields.get("runaway_phase") or phase.phase)
        self.session.meta.project_runaway_checkpoint = checkpoint_map.get(
            current_phase, "verifying"
        )
        workflow_stage = workflow_map.get(current_phase)
        if workflow_stage:
            self.session.meta.project_workflow_stage = workflow_stage
        if current_phase != "implement":
            self.session.meta.project_active_task_id = ""
        self.agent.executor.session.runaway_v2_runtime_workflow_stage = ""
        self.agent._sync_turn_mode()
