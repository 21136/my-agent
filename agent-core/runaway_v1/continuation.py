"""V1 runaway continuation and project-start flow (lazy Agent delegate target)."""

from __future__ import annotations

import re
from typing import Any

from session import utc_now_iso
from user_copy import (
    harness_chain_user_line,
    runaway_auto_chain_notice,
    runaway_chain_continue_line,
    runaway_chain_verifying_line,
)

_TASK_CHECKBOX_DONE_RE = re.compile(r"^\s*-\s*\[x\]", re.IGNORECASE)


def _continue_runaway_after_natural_stop(
    self,
    *,
    final_text: str,
    finish_reason: str | None,
) -> bool:
    """Turn an ordinary model stop into an internal checkpoint continuation."""
    if not getattr(self.session.meta, "project_runaway_enabled", False):
        return False
    if self.session.meta.active_shell != "project":
        return False
    if finish_reason in {"cancelled", "timeout", "error", "provider_error", "context_switched"}:
        return False
    from runaway_flow import normalize_checkpoint

    checkpoint = normalize_checkpoint(
        getattr(self.session.meta, "project_runaway_checkpoint", "")
    )
    if checkpoint in {"paused", "release_wait", "completed"}:
        return False
    stage = str(getattr(self.session.meta, "project_workflow_stage", "") or "")
    if stage == "verification" and self._runaway_formal_queue_done():
        had_acceptance = bool(
            getattr(self.session.meta, "project_runaway_acceptance_passed", False)
        )
        if had_acceptance and checkpoint in {"verifying", "repairing"}:
            self._emit_turn_event(
                {
                    "type": "turn.notice",
                    "level": "info",
                    "text": "正式任务已清空且硬验收已通过，停止自动续接。",
                }
            )
            return False
        if self._sync_runaway_harness_truth():
            return False
    if self._advance_runaway_checkpoint():
        # Verification can advance straight into a repair checkpoint
        # without changing the active task.  Record that post-advance
        # state so the next identical natural stop cannot start another
        # continuation segment immediately.
        next_checkpoint = normalize_checkpoint(
            getattr(self.session.meta, "project_runaway_checkpoint", "")
        )
        next_stage = str(
            getattr(self.session.meta, "project_workflow_stage", "") or ""
        ).strip()
        next_active_id = (
            getattr(self.session.meta, "project_active_task_id", "") or ""
        ).strip()
        self._runaway_last_continue_key = (
            f"{next_checkpoint}|{next_stage}|{next_active_id}"
        )
        if stage == "verification" and self._runaway_formal_queue_done():
            return False
        return True
    stage = str(getattr(self.session.meta, "project_workflow_stage", "") or "")
    if stage in {"release", "requirements", "documentation", "design"}:
        return False
    active_id = (getattr(self.session.meta, "project_active_task_id", "") or "").strip()
    continue_key = f"{checkpoint}|{stage}|{active_id}"
    if continue_key == self._runaway_last_continue_key:
        return False

    from project_mode import parse_tasks_metadata, project_dir, read_formal_task_stats

    pid = (self.session.meta.project_id or "").strip()
    task_hint = active_id or "下一项开放任务"
    if active_id and pid:
        tasks_path = project_dir(self.session.paths, pid) / "TASKS.md"
        if tasks_path.is_file():
            tasks_text = tasks_path.read_text(encoding="utf-8")
            for task in parse_tasks_metadata(tasks_text):
                if str(task.get("id") or "").upper() == active_id.upper():
                    body = str(task.get("text") or "").strip()
                    if body:
                        task_hint = f"{active_id}（{body[:120]}）"
                    break
            stats = read_formal_task_stats(tasks_path)
            baseline = int(
                getattr(self.session.meta, "project_runaway_task_done_baseline", 0) or 0
            )
            if stats.done <= baseline:
                stuck = int(getattr(self, "_runaway_stuck_turns", 0) or 0) + 1
                self._runaway_stuck_turns = stuck
                if stuck >= 3:
                    self._emit_turn_event(
                        {
                            "type": "turn.notice",
                            "level": "warn",
                            "text": (
                                f"已连续 {stuck} 轮无任务进展；请专注 {active_id or '当前授权任务'}，"
                                "plan_partner 网关失败不阻塞 VERIFY→TASKS 同步。"
                            ),
                        }
                    )
            else:
                self._runaway_stuck_turns = 0

    self.session.append_message(
        {
            "role": "user",
            "content": runaway_chain_continue_line(
                task_hint=task_hint,
                final_text=final_text,
            ),
        }
    )
    self._runaway_last_continue_key = continue_key
    self.session.meta.project_runaway_tool_rounds = 0
    self.session.meta.updated_at = utc_now_iso()
    self.session.save()
    self._emit_turn_event(
        {"type": "turn.notice", "level": "info", "text": runaway_auto_chain_notice()}
    )
    return True

def _runaway_task_line_is_done(self, line: str) -> bool:
    return bool(_TASK_CHECKBOX_DONE_RE.match(line or ""))

def _runaway_active_task_is_done(self, tasks_text: str) -> bool:
    active_id = (getattr(self.session.meta, "project_active_task_id", "") or "").strip().upper()
    if not active_id:
        return False
    from project_mode import iter_tasks_lines_skipping_closed, parse_tasks_metadata

    visible = {index: line for index, line in iter_tasks_lines_skipping_closed(tasks_text)}
    for task in parse_tasks_metadata(tasks_text):
        if str(task.get("id") or "").upper() != active_id:
            continue
        line = visible.get(int(task.get("line", -1)), "")
        return self._runaway_task_line_is_done(line)
    return False

def _runaway_active_task_has_advance_evidence(self, tasks_text: str) -> bool:
    active_id = (getattr(self.session.meta, "project_active_task_id", "") or "").strip().upper()
    if not active_id:
        return False
    pid = (self.session.meta.project_id or "").strip()
    verify_text = ""
    if pid:
        from project_mode import read_project_artifacts

        verify_text = read_project_artifacts(self.session.paths, pid).get("VERIFY.md", "")
    from runaway_flow import runaway_task_has_advance_evidence

    return runaway_task_has_advance_evidence(
        task_id=active_id,
        tasks_text=tasks_text,
        turn_evidence=list(self.executor.session.turn_evidence or []),
        verify_text=verify_text,
    )

def _runaway_implementation_progressed(self, stats: Any, tasks_text: str) -> bool:
    """Detect task completion with evidence; ignore bare TASKS checkboxes."""
    if self.executor.session.task_stop_armed:
        return True
    if not self._runaway_active_task_is_done(tasks_text):
        return False
    return self._runaway_active_task_has_advance_evidence(tasks_text)

def _advance_runaway_checkpoint(self) -> bool:
    """Advance a completed project task without exposing a manual continue step."""
    if not getattr(self.session.meta, "project_runaway_enabled", False):
        return False
    if self.session.meta.active_shell != "project":
        return False

    from project_mode import (
        next_open_task,
        project_dir,
        project_id_from_root,
        read_formal_task_stats,
        task_dependency_blockers,
    )
    from runaway_flow import sync_checkpoint_to_target, task_fingerprint
    from session import utc_now_iso

    pid = (self.session.meta.project_id or "").strip() or project_id_from_root(
        self.session.meta.project_root or ""
    )
    if not pid:
        return False
    tasks_path = project_dir(self.session.paths, pid) / "TASKS.md"
    if not tasks_path.is_file():
        return False

    workflow_stage = getattr(self.session.meta, "project_workflow_stage", "requirements")
    if workflow_stage == "documentation":
        from project_cli import confirm_project_design

        try:
            confirm_project_design(self.session, self.session.paths)
        except Exception as exc:
            if self._queue_runaway_documentation_repair(exc):
                return True
            self._pause_runaway("文档尚未达到设计确认标准", error=str(exc))
            return False
        workflow_stage = "design"
    if workflow_stage == "design":
        from project_cli import start_project_task

        tasks_text = tasks_path.read_text(encoding="utf-8")
        _, next_task_text, next_task_id = next_open_task(tasks_text)
        if not next_task_id:
            blockers = task_dependency_blockers(tasks_text)
            reason = "没有依赖已满足的开放任务"
            if blockers:
                details = "; ".join(
                    f"{task_id} 等待 {', '.join(values)}"
                    for task_id, values in sorted(blockers.items())
                )
                reason = f"任务依赖未满足：{details}"
            self._pause_runaway(reason)
            return False
        try:
            start_project_task(self.session, self.session.paths, next_task_id)
        except Exception as exc:
            self._pause_runaway("无法自动启动实现任务", error=str(exc))
            return False
        self._sync_turn_mode()
        sync_checkpoint_to_target(self.session.meta, "implementing")
        self.session.meta.project_runaway_task_fingerprint = task_fingerprint(
            next_task_id, next_task_text
        )
        self.session.meta.project_runaway_task_done_baseline = read_formal_task_stats(tasks_path).done
        self.session.meta.project_runaway_tool_rounds = 0
        self.session.meta.project_runaway_repair_count = 0
        self.session.meta.project_runaway_last_verification = ""
        self.session.meta.updated_at = utc_now_iso()
        self.session.save()
        self.executor.begin_turn(reset_plan_cap=False)
        self._runaway_stuck_turns = 0
        self.session.append_message(
            {
                "role": "system",
                "content": (
                    f"[Harness] 狂奔模式：已自动授权 {next_task_id}。"
                    "请继续实现、测试并验证，不要等待用户回复。"
                ),
            }
        )
        self._emit_turn_event(
            {"type": "turn.notice", "level": "info", "text": "项目已准备好，正在开始第一项任务…"}
        )
        return True
    if workflow_stage != "implementation":
        return False

    active_id = (self.session.meta.project_active_task_id or "").strip()
    from runaway_flow import sync_all_runaway_task_checkoffs_from_verify

    sync_all_runaway_task_checkoffs_from_verify(self.session.paths, pid)

    stats = read_formal_task_stats(tasks_path)
    tasks_text = tasks_path.read_text(encoding="utf-8")
    _, _, next_open_id = next_open_task(tasks_text)
    queue_complete = stats.all_done and not next_open_id
    if not queue_complete and not self._runaway_implementation_progressed(stats, tasks_text):
        return False

    previous_task_id = (self.session.meta.project_active_task_id or "").strip().upper()
    task_line, task_text, task_id = next_open_task(tasks_text)
    if task_id:
        if task_id.upper() == previous_task_id:
            return False
        self.session.meta.project_workflow_stage = "implementation"
        self.session.meta.project_plan_status = "confirmed"
        self.session.meta.project_active_task_id = task_id
        sync_checkpoint_to_target(self.session.meta, "implementing")
        self.session.meta.project_runaway_task_fingerprint = task_fingerprint(
            task_id, task_text
        )
        self.session.meta.project_runaway_task_done_baseline = stats.done
        self.session.meta.project_runaway_tool_rounds = 0
        self.session.meta.project_runaway_repair_count = 0
        self.session.meta.project_runaway_last_error = ""
        self.session.meta.project_runaway_last_error_fingerprint = ""
        self.session.meta.project_runaway_last_verification = ""
        self.session.meta.project_runaway_review_blockers_count = 0
        self.session.meta.project_runaway_paused_reason = ""
        self.session.meta.updated_at = utc_now_iso()
        self.session.save()
        self._sync_turn_mode()
        self._runaway_last_continue_key = ""
        self._runaway_stuck_turns = 0
        self.executor.begin_turn(reset_plan_cap=False)
        self.session.append_message(
            {
                "role": "system",
                "content": (
                    f"[Harness] 狂奔模式：{previous_task_id or '上一任务'} 已完成，"
                    f"自动进入 {task_id}。"
                    f"任务内容：{task_text or task_line or '见 TASKS.md'}。"
                    "请继续实现、测试并验证，不要等待用户回复。"
                ),
            }
        )
        self._emit_turn_event(
            {
                "type": "turn.notice",
                "level": "info",
                "text": f"{previous_task_id or '上一任务'} 已完成，正在进入 {task_id}…",
            }
        )
        return True

    blockers = task_dependency_blockers(tasks_text)
    if blockers:
        details = "; ".join(
            f"{task_id} 等待 {', '.join(values)}"
            for task_id, values in sorted(blockers.items())
        )
        self._pause_runaway(f"任务依赖未满足：{details}")
        return False
    if self.session.meta.project_workflow_stage == "verification":
        return False
    self.session.meta.project_workflow_stage = "verification"
    self.session.meta.project_active_task_id = ""
    sync_checkpoint_to_target(self.session.meta, "verifying")
    self.session.meta.project_runaway_task_done_baseline = stats.done
    self.session.meta.project_runaway_tool_rounds = 0
    self.session.meta.project_runaway_repair_count = 0
    self.session.meta.project_runaway_last_verification = ""
    self.session.meta.project_runaway_review_blockers_count = 0
    self.session.meta.project_runaway_acceptance_passed = False
    self.session.meta.project_runaway_verification_evidence_fingerprint = ""
    self.session.meta.project_runaway_paused_reason = ""
    self.session.last_review_verdict = None
    self.session.last_review_blockers_count = 0
    self.session.meta.updated_at = utc_now_iso()
    self.session.save()
    hard_passed = self._run_runaway_hard_verification()
    self._sync_turn_mode()
    self.executor.begin_turn()
    if not hard_passed:
        return True
    self.session.append_message(
        {
            "role": "system",
            "content": (
                "[Harness] 狂奔模式：所有开放任务已完成。"
                "现在自动执行 VERIFY.md 对口验证和交付检查；发现可安全修复的问题就修复并复验，"
                "不要等待用户回复。"
            ),
        }
    )
    self._emit_turn_event(
        {"type": "turn.notice", "level": "info", "text": "任务已完成，正在自动验证交付结果…"}
    )
    return True

def _maybe_run_runaway_startup_prep(self) -> bool:
    """UI-6041: advance requirements→design when runaway is on (any turn intent)."""
    if not getattr(self.session.meta, "project_runaway_enabled", False):
        return False
    if self.session.meta.active_shell != "project" or not self.session.meta.project_id:
        return False
    stage = str(getattr(self.session.meta, "project_workflow_stage", "") or "")
    if stage not in {"requirements", "documentation", "design"}:
        return False
    return self._prepare_runaway_project_start()

def _prepare_runaway_project_start(self) -> bool:
    """Prepare the first executable task when a runaway turn starts."""
    if not getattr(self.session.meta, "project_runaway_enabled", False):
        return False
    if self.session.meta.active_shell != "project" or not self.session.meta.project_id:
        return False
    from project_cli import confirm_project_design, start_project_task
    from project_mode import next_open_task, project_dir, task_dependency_blockers
    from runaway_flow import sync_checkpoint_to_stage, sync_checkpoint_to_target, task_fingerprint

    for _ in range(4):
        stage = getattr(self.session.meta, "project_workflow_stage", "requirements")
        try:
            sync_checkpoint_to_stage(self.session.meta, stage)
            if stage == "requirements":
                from project_cli import organize_project_plan

                organize_project_plan(self.session, self.session.paths)
                continue
            if stage == "documentation":
                try:
                    confirm_project_design(self.session, self.session.paths)
                except Exception as exc:
                    if self._queue_runaway_documentation_repair(exc):
                        return True
                    raise
                continue
            if stage == "design":
                tasks_path = project_dir(self.session.paths, self.session.meta.project_id) / "TASKS.md"
                tasks_text = tasks_path.read_text(encoding="utf-8")
                _, task_text, task_id = next_open_task(tasks_text)
                if not task_id:
                    blockers = task_dependency_blockers(tasks_text)
                    reason = "没有依赖已满足的开放任务"
                    if blockers:
                        details = "; ".join(
                            f"{task_id} 等待 {', '.join(values)}"
                            for task_id, values in sorted(blockers.items())
                        )
                        reason = f"任务依赖未满足：{details}"
                    self._pause_runaway(reason)
                    return False
                start_project_task(self.session, self.session.paths, task_id)
                self._sync_turn_mode()
                sync_checkpoint_to_target(self.session.meta, "implementing")
                self.session.meta.project_runaway_task_fingerprint = task_fingerprint(
                    task_id, task_text
                )
                self.session.meta.updated_at = utc_now_iso()
                self.session.save()
                return True
            if stage == "implementation":
                if self._advance_runaway_checkpoint():
                    return True
                return True
            if stage == "verification":
                return True
            return stage == "release"
        except Exception as exc:
            self._pause_runaway("无法自动准备项目流程", error=str(exc))
            return False
    return False

def _adopt_runaway_plan(self, plan_result: Any) -> bool:
    """Auto-adopt plan-domain proposals under the explicit runaway grant."""
    if not getattr(self.session.meta, "project_runaway_enabled", False):
        return False
    proposal_ids = getattr(plan_result, "proposal_ids", ())
    return self._adopt_runaway_proposals(proposal_ids)


def should_chain_runaway_after_turn_v1(self, finish_reason: str | None) -> bool:
    if not getattr(self.session.meta, "project_runaway_enabled", False):
        return False
    if self.session.meta.active_shell != "project":
        return False
    if getattr(self.session.meta, "project_runaway_paused_reason", "").strip():
        return False
    reason = (finish_reason or "").strip()
    if reason in {
        "cancelled",
        "timeout",
        "error",
        "provider_error",
        "context_switched",
        "runaway_paused",
        "runaway_duplicate",
        "runaway_verification_exit",
    }:
        return False
    from runaway_flow import normalize_checkpoint

    checkpoint = normalize_checkpoint(
        getattr(self.session.meta, "project_runaway_checkpoint", "")
    )
    if checkpoint in {"paused", "completed", "release_wait"}:
        return False
    if (
        checkpoint == "repairing"
        and self._runaway_formal_queue_done()
        and bool(getattr(self.session.meta, "project_runaway_acceptance_passed", False))
    ):
        return False
    if (
        checkpoint == "verifying"
        and self._runaway_formal_queue_done()
        and bool(getattr(self.session.meta, "project_runaway_acceptance_passed", False))
    ):
        if self._sync_runaway_harness_truth():
            return False
        return True
    return True


def runaway_chain_skip_reason_v1(self) -> str | None:
    from runaway_flow import normalize_checkpoint

    checkpoint = normalize_checkpoint(
        getattr(self.session.meta, "project_runaway_checkpoint", "")
    )
    if checkpoint in {"paused", "completed", "release_wait"}:
        if checkpoint == "release_wait":
            return "验收已通过，等待你确认发布。"
        paused = str(getattr(self.session.meta, "project_runaway_paused_reason", "") or "").strip()
        return paused or "狂奔已暂停，未自动续接。"
    return None


def runaway_chain_user_line_v1(self) -> str:
    if self._runaway_formal_queue_done():
        return runaway_chain_verifying_line()
    active_id = (getattr(self.session.meta, "project_active_task_id", "") or "").strip()
    task_hint = active_id or "下一项开放任务"
    pid = (self.session.meta.project_id or "").strip()
    if active_id and pid:
        from project_mode import parse_tasks_metadata, project_dir

        tasks_path = project_dir(self.session.paths, pid) / "TASKS.md"
        if tasks_path.is_file():
            for task in parse_tasks_metadata(tasks_path.read_text(encoding="utf-8")):
                if str(task.get("id") or "").upper() == active_id.upper():
                    body = str(task.get("text") or "").strip()
                    if body:
                        task_hint = f"{active_id}（{body[:120]}）"
                    break
    return harness_chain_user_line(task_hint=task_hint)


def should_continue_v1_execute_segment(
    self,
    *,
    final_text: str,
    finish_reason: str | None,
    total_tool_rounds: int,
    total_max: int,
) -> bool:
    """Preserve the V1 repair → checkpoint → natural-stop continuation order."""
    if self._runaway_repair_pending() and total_tool_rounds < total_max:
        return True
    if self._advance_runaway_checkpoint() and total_tool_rounds < total_max:
        return True
    return bool(
        self._continue_runaway_after_natural_stop(
            final_text=final_text,
            finish_reason=finish_reason,
        )
        and total_tool_rounds < total_max
    )
