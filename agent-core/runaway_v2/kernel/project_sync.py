"""Project/session sync for runaway orchestration (checkpoint mirror, plan apply)."""

from __future__ import annotations

from datetime import datetime, timezone
from collections.abc import Callable
from typing import TYPE_CHECKING

from paths import AgentPaths
from runaway_v2.acceptance import format_hook_failure
from runaway_v2.checklist import Checklist, load_or_build_checklist
from runaway_v2.config import directed_after
from runaway_v2.phase import PhaseResult
from runaway_v2.plan import TurnPlan
from runaway_v2.state import build_v2_state_fields
from runaway_v2.kernel.ports import AgentDriver

_WORKFLOW_STAGE_BY_PHASE = {
    "prepare": "documentation",
    "implement": "implementation",
    "verify": "verification",
    "release_wait": "release",
}


class RunawayProjectSync:
    def __init__(self, driver: AgentDriver) -> None:
        self._driver = driver

    @property
    def session(self):
        return self._driver.session

    @property
    def paths(self) -> AgentPaths:
        return self.session.paths

    def pause_after_fatal_finish(self, finish_reason: str | None) -> None:
        reason = (finish_reason or "").strip()
        pause_reason = {
            "cancelled": "用户停止了狂奔",
            "timeout": "运行时间达到上限",
            "error": "狂奔运行异常",
        }.get(reason)
        if pause_reason is not None:
            self._driver.pause_runaway(pause_reason)

    def apply_plan(self, plan: TurnPlan, phase: PhaseResult) -> None:
        workflow = _WORKFLOW_STAGE_BY_PHASE.get(plan.phase, "")
        if workflow:
            self.session.meta.project_workflow_stage = workflow
        if phase.phase == "release_wait" and plan.phase == "implement":
            self.session.meta.project_runaway_checkpoint = "implementing"
        if plan.active_task_id:
            self.session.meta.project_active_task_id = plan.active_task_id
        scope = plan.allowed_write_globs if plan.allowed_write_globs else None
        self._driver.apply_runaway_execution_context(
            write_scope=scope,
            forbid_plan_partner=bool(plan.forbid_plan_partner),
            workflow_stage=workflow,
        )
        self._driver.sync_turn_mode()

    def commit_acceptance(
        self,
        checklist: Checklist,
        plan: TurnPlan,
        hook,
        *,
        advance_after_prepare: Callable[[str], None] | None = None,
    ) -> None:
        advance = advance_after_prepare or self.advance_after_prepare_ready
        item = plan.acceptance_item
        if item is None:
            return
        target = next((entry for entry in checklist.items if entry.id == item.id), None)
        if target is None:
            if item.id == "PREPARE" and hook.ok:
                project_id = str(self.session.meta.project_id or "").strip()
                advance(project_id)
            return
        project_id = str(self.session.meta.project_id or "").strip()
        target.last_run_at = (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        if hook.ok:
            target.status = "passed"
            target.last_failure = None
            if plan.phase == "prepare" and target.id == "PREPARE":
                advance(project_id)
            return
        target.status = "failed"
        target.attempts += 1
        target.last_failure = format_hook_failure(hook)
        if plan.mode == "directed":
            target.directed_used = True

    def advance_after_prepare_ready(self, project_id: str) -> None:
        from project_mode import next_open_task, read_project_artifacts, snapshot_plan_fingerprints
        from runaway_flow import sync_checkpoint_to_target, task_fingerprint
        from session import utc_now_iso

        artifacts = read_project_artifacts(self.paths, project_id)
        _, task_text, task_id = next_open_task(artifacts.get("TASKS.md", ""))
        self.session.meta.project_plan_status = "confirmed"
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
        self._driver.sync_turn_mode()

    def mirror_runtime(self, phase: PhaseResult, plan: TurnPlan, checklist: Checklist) -> None:
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
            "human": "verification",
        }
        current_phase = str(fields.get("runaway_phase") or phase.phase)
        self.session.meta.project_runaway_checkpoint = checkpoint_map.get(current_phase, "verifying")
        workflow_stage = workflow_map.get(current_phase)
        if workflow_stage:
            self.session.meta.project_workflow_stage = workflow_stage
        if current_phase != "implement":
            self.session.meta.project_active_task_id = ""
        self._driver.clear_runaway_runtime_workflow_stage()
        self._driver.sync_turn_mode()
