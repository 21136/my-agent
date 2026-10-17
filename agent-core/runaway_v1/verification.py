"""V1 runaway verification and repair flow (loaded lazily by Agent wrappers)."""

from __future__ import annotations

import hashlib
from typing import Any

from llm_client import LLMCancelledError
from session import utc_now_iso
from tools.logging import EvolveLog
from user_copy import (
    harness_disk_sync_notice,
    harness_hard_verify_passed_notice,
    harness_verify_passed_notice,
    runaway_bugfix_spawn_notice,
    runaway_release_wait_assistant_text,
    runaway_release_wait_turn_notice,
    runaway_verification_harness_assistant_text,
    runaway_verification_harness_skip_notice,
)
from agent import TurnResult

RUNAWAY_REPAIR_BUDGET = 3


def _matrix_repair_fingerprint(self, summary: str) -> str:
    return hashlib.sha256(summary.encode("utf-8")).hexdigest()[:16]


def _transition_runaway_checkpoint(
    self,
    target: str,
    *,
    paused_reason: str | None = None,
) -> str:
    from runaway_flow import transition_checkpoint

    checkpoint = transition_checkpoint(
        self.session.meta,
        target,
        paused_reason=paused_reason,
    )
    self.session.meta.updated_at = utc_now_iso()
    return checkpoint

def _enter_runaway_repair_checkpoint(
    self,
    *,
    summary: str,
    fingerprint: str,
) -> bool:
    """Arm repairing checkpoint, run bug-fix, fall back to harness user nudge."""
    from runaway_flow import normalize_checkpoint

    meta = self.session.meta
    checkpoint = normalize_checkpoint(
        getattr(meta, "project_runaway_checkpoint", "")
    )
    previous_fingerprint = str(
        getattr(meta, "project_runaway_last_error_fingerprint", "") or ""
    ).strip()
    if (
        checkpoint == "repairing"
        and previous_fingerprint
        and previous_fingerprint == fingerprint
    ):
        self._transition_runaway_checkpoint("paused", paused_reason="同一验收问题再次出现")
        meta.project_runaway_last_error = summary[:500]
        self.session.save()
        return False
    if int(getattr(meta, "project_runaway_repair_count", 0) or 0) >= RUNAWAY_REPAIR_BUDGET:
        self._transition_runaway_checkpoint("paused", paused_reason="自动修复次数已用尽")
        meta.project_runaway_last_error = summary[:500]
        self.session.save()
        return False

    self._transition_runaway_checkpoint("repairing")
    meta.project_runaway_repair_count = int(
        getattr(meta, "project_runaway_repair_count", 0) or 0
    ) + 1
    meta.project_runaway_last_error = summary[:500]
    meta.project_runaway_last_error_fingerprint = fingerprint
    meta.updated_at = utc_now_iso()
    self.session.save()
    self._sync_turn_mode()
    if self._run_runaway_repair_lane(error_summary=summary):
        return True
    from exec_reliability import runaway_bug_fix_enabled

    if runaway_bug_fix_enabled():
        repair_hint = (
            "Harness 已走 bug-fix 轨；主 Agent 勿直接写 PROJECT/ENV。"
            "若仍失败请等待 Harness 自动重试 bug-fix，勿调用 plan_partner。"
        )
    else:
        repair_hint = (
            "若 PROJECT.md 缺少验收命令，请用 write_text/patch_file 追加「## 验收标准」与 "
            "``命令：`python …` ``，不要调用 plan_partner。"
        )
        if "PROJECT.md" in summary and "验收" in summary:
            repair_hint = (
                "请用 write_text 或 patch_file 在 PROJECT.md 追加「## 验收标准」和可执行的 "
                "``命令：`python …` `` 行，不要调用 plan_partner。"
            )
    self.session.append_message(
        {
            "role": "user",
            "content": (
                "[Harness] 项目验收未通过或验收矩阵未满足。请只在当前项目范围内修复问题，"
                "然后重新实现、测试并验证；不要等待用户回复。"
                f"验收结果：{summary[:500]} "
                f"{repair_hint}"
            ),
        }
    )
    self._emit_turn_event(
        {"type": "turn.notice", "level": "warn", "text": "项目验收未通过，正在自动修复并复验…"}
    )
    return False

def _run_runaway_hard_verification(self) -> bool:
    """Run the project acceptance command and persist its evidence."""
    if not getattr(self.session.meta, "project_runaway_enabled", False):
        return False
    pid = (self.session.meta.project_id or "").strip()
    if not pid or self.session.meta.active_shell != "project":
        self._pause_runaway("无法确定当前项目，不能执行验收")
        return False

    self._bootstrap_runaway_verification_artifacts()
    from runaway_verify_matrix import format_matrix_summary, lint_verify_matrix

    matrix = lint_verify_matrix(self.session.paths, pid)
    if not matrix.ok:
        self._emit_turn_event(
            {
                "type": "turn.notice",
                "level": "warn",
                "text": format_matrix_summary(matrix),
            }
        )

    acceptance_passed = self._rerun_runaway_harness_verification()
    if acceptance_passed and matrix.ok:
        return True

    meta = self.session.meta
    if acceptance_passed and not matrix.ok:
        summary = format_matrix_summary(matrix)
        fingerprint = self._matrix_repair_fingerprint(summary)
    else:
        summary = str(
            getattr(meta, "project_runaway_last_error", "") or "项目验收命令未通过"
        ).strip()
        fingerprint = str(
            getattr(meta, "project_runaway_last_error_fingerprint", "") or ""
        ).strip()

    return self._enter_runaway_repair_checkpoint(
        summary=summary,
        fingerprint=fingerprint,
    )

def _spawn_runaway_bug_fix(
    self,
    *,
    error_summary: str,
    matrix_summary: str = "",
) -> bool:
    """Harness-owned bug-fix lane; returns True when subagent reports pass."""
    from exec_reliability import runaway_bug_fix_enabled
    from runaway_flow import normalize_checkpoint
    from runaway_verify_matrix import format_matrix_summary, lint_verify_matrix
    from subagent import SubagentRunner, format_subagent_overlay

    if not runaway_bug_fix_enabled():
        return False
    if not getattr(self.session.meta, "project_runaway_enabled", False):
        return False
    if normalize_checkpoint(
        getattr(self.session.meta, "project_runaway_checkpoint", "")
    ) != "repairing":
        return False

    pid = (self.session.meta.project_id or "").strip()
    if not pid:
        return False

    lint = lint_verify_matrix(self.session.paths, pid)
    matrix_text = matrix_summary.strip() or format_matrix_summary(lint)
    task = (
        "修复狂奔验收阻塞：补全 PROJECT.md 可执行验收命令、"
        "ENV.md quality.commands、VERIFY 证据，"
        "并修复导致验收失败的代码或配置。"
        f"\n\nHarness 错误：{error_summary[:800]}"
    )
    self._emit_turn_event(
        {
            "type": "turn.notice",
            "level": "info",
            "text": runaway_bugfix_spawn_notice(),
        }
    )
    try:
        runner = SubagentRunner(
            paths=self.session.paths,
            evolve_log=EvolveLog.for_agent(self.session.paths),
        )
        result = runner.run_bug_fix(
            task,
            session=self.session,
            facts={
                "last_error": error_summary[:500],
                "matrix": lint.to_dict(),
            },
            matrix_summary=matrix_text,
            llm=self.llm,
            confirm_fn=self.executor.confirm_fn,
            cancel_event=self.cancel_event,
        )
    except LLMCancelledError:
        raise
    except Exception as exc:
        self._emit_turn_event(
            {
                "type": "turn.notice",
                "level": "warn",
                "text": f"bug-fix 子代理失败：{exc}",
            }
        )
        return False

    from runaway_flow import sync_all_runaway_task_checkoffs_from_verify

    sync_all_runaway_task_checkoffs_from_verify(self.session.paths, pid)
    overlay = format_subagent_overlay(result)
    self.session.subagent_overlay = overlay
    self._emit_turn_event(
        {"type": "turn.notice", "level": "info", "text": overlay}
    )
    return (result.verdict or "fail") == "pass"

def _rerun_runaway_harness_verification(self) -> bool:
    """Run PROJECT acceptance command and persist evidence (no checkpoint side effects)."""
    pid = (self.session.meta.project_id or "").strip()
    if not pid:
        return False
    from runaway_verification import (
        MISSING_ACCEPTANCE_ERROR,
        ensure_project_acceptance_section,
        evidence_passed,
        run_harness_verification,
    )

    record = run_harness_verification(self.session.paths, pid)
    if (
        not evidence_passed(record, pid)
        and str(record.get("error") or "").strip() == MISSING_ACCEPTANCE_ERROR
    ):
        if ensure_project_acceptance_section(self.session.paths, pid):
            record = run_harness_verification(self.session.paths, pid)
    meta = self.session.meta
    passed = evidence_passed(record, pid)
    meta.project_runaway_acceptance_passed = passed
    meta.project_runaway_verification_evidence_fingerprint = str(
        record.get("evidence_fingerprint") or ""
    )
    if not passed:
        meta.project_runaway_last_error = str(
            record.get("error") or "项目验收命令未通过"
        )[:500]
        meta.project_runaway_last_error_fingerprint = str(
            record.get("evidence_fingerprint") or ""
        )
    meta.updated_at = utc_now_iso()
    self.session.save()
    return passed

def _bootstrap_runaway_verification_artifacts(self) -> bool:
    """Harness-owned: PROJECT acceptance + ENV quality.commands before matrix/hard verify."""
    if not getattr(self.session.meta, "project_runaway_enabled", False):
        return False
    if getattr(self.session.meta, "project_workflow_stage", "") != "verification":
        return False
    pid = (self.session.meta.project_id or "").strip()
    if not pid:
        return False
    from runaway_verification import (
        ensure_env_quality_commands,
        ensure_project_acceptance_section,
    )

    changed = False
    if ensure_project_acceptance_section(self.session.paths, pid):
        changed = True
    if ensure_env_quality_commands(self.session.paths, pid):
        changed = True
    return changed

def _runaway_formal_queue_done(self) -> bool:
    """True when all formal ``T-*`` rows in TASKS.md are checked off."""
    pid = (self.session.meta.project_id or "").strip()
    if not pid:
        return False
    from project_mode import project_dir, read_formal_task_stats

    tasks_path = project_dir(self.session.paths, pid) / "TASKS.md"
    return read_formal_task_stats(tasks_path).all_done

def _sync_runaway_harness_truth(self, *, notice: str = "") -> bool:
    """UI-5968: matrix + hard acceptance green → verifying; queue done → release_wait."""
    if not getattr(self.session.meta, "project_runaway_enabled", False):
        return False
    if getattr(self.session.meta, "project_workflow_stage", "") != "verification":
        return False
    pid = (self.session.meta.project_id or "").strip()
    if not pid:
        return False

    from runaway_flow import normalize_checkpoint
    from runaway_verify_matrix import lint_verify_matrix

    checkpoint = normalize_checkpoint(
        getattr(self.session.meta, "project_runaway_checkpoint", "")
    )
    if checkpoint in {"completed", "paused"}:
        return False
    if checkpoint == "release_wait":
        return True

    self._bootstrap_runaway_verification_artifacts()
    lint = lint_verify_matrix(self.session.paths, pid)
    if not lint.ok:
        return False
    if not self._rerun_runaway_harness_verification():
        return False

    queue_done = self._runaway_formal_queue_done()
    target = "release_wait" if queue_done else "verifying"
    meta = self.session.meta
    if checkpoint == "repairing" and target == "release_wait":
        self._transition_runaway_checkpoint("verifying")
        checkpoint = "verifying"
    if checkpoint != target:
        self._transition_runaway_checkpoint(target)
    meta.project_runaway_last_error = ""
    meta.project_runaway_last_error_fingerprint = ""
    meta.project_runaway_paused_reason = ""
    meta.project_runaway_last_verification = "pass"
    meta.project_runaway_review_blockers_count = 0
    meta.updated_at = utc_now_iso()
    self.session.save()
    self._sync_turn_mode()
    default = harness_hard_verify_passed_notice(target=target)
    text = notice.strip() or default
    self._emit_turn_event({"type": "turn.notice", "level": "info", "text": text})
    return True

def _promote_runaway_to_verifying_if_harness_green(
    self,
    *,
    notice: str = "",
) -> bool:
    """P0/P1: Matrix + hard acceptance green → verifying (ignore stale review meta)."""
    if not getattr(self.session.meta, "project_runaway_enabled", False):
        return False
    if getattr(self.session.meta, "project_workflow_stage", "") != "verification":
        return False
    from runaway_flow import normalize_checkpoint

    before = normalize_checkpoint(
        getattr(self.session.meta, "project_runaway_checkpoint", "")
    )
    if not self._sync_runaway_harness_truth(notice=notice):
        return False
    after = normalize_checkpoint(
        getattr(self.session.meta, "project_runaway_checkpoint", "")
    )
    if after == "release_wait":
        return True
    return before != "verifying" or after == "verifying"

def _run_runaway_repair_lane(self, *, error_summary: str) -> bool:
    """repairing checkpoint: bug-fix subagent + harness re-verify. True → verifying."""
    from runaway_flow import normalize_checkpoint

    if normalize_checkpoint(
        getattr(self.session.meta, "project_runaway_checkpoint", "")
    ) != "repairing":
        return False
    self._sync_turn_mode()
    self._spawn_runaway_bug_fix(error_summary=error_summary)
    meta = self.session.meta
    saved_fingerprint = str(
        getattr(meta, "project_runaway_last_error_fingerprint", "") or ""
    )
    if not self._rerun_runaway_harness_verification():
        meta.project_runaway_last_error_fingerprint = saved_fingerprint
        meta.updated_at = utc_now_iso()
        self.session.save()
        return False
    self._transition_runaway_checkpoint("verifying")
    meta.project_runaway_last_error = ""
    meta.project_runaway_last_error_fingerprint = ""
    meta.project_runaway_paused_reason = ""
    meta.updated_at = utc_now_iso()
    self.session.save()
    self._emit_turn_event(
        {
            "type": "turn.notice",
            "level": "info",
            "text": harness_verify_passed_notice(),
        }
    )
    return True

def _maybe_run_runaway_repair_lane_at_turn_start(self) -> bool:
    if not getattr(self.session.meta, "project_runaway_enabled", False):
        return False
    if getattr(self.session.meta, "project_workflow_stage", "") != "verification":
        return False
    self._bootstrap_runaway_verification_artifacts()
    if self._sync_runaway_harness_truth(
        notice=harness_disk_sync_notice(),
    ):
        return True
    from runaway_flow import normalize_checkpoint
    from runaway_verify_matrix import format_matrix_summary, lint_verify_matrix

    checkpoint = normalize_checkpoint(
        getattr(self.session.meta, "project_runaway_checkpoint", "")
    )
    pid = (self.session.meta.project_id or "").strip()
    if checkpoint == "verifying" and pid:
        lint = lint_verify_matrix(self.session.paths, pid)
        if not lint.ok:
            summary = format_matrix_summary(lint)
            return self._enter_runaway_repair_checkpoint(
                summary=summary,
                fingerprint=self._matrix_repair_fingerprint(summary),
            )

    if checkpoint != "repairing":
        return False
    summary = str(
        getattr(self.session.meta, "project_runaway_last_error", "") or "验收阻塞"
    )
    return self._run_runaway_repair_lane(error_summary=summary)

def _maybe_finish_runaway_verification_exit_turn(
    self,
    *,
    intent: str,
    user_text: str,
) -> TurnResult | None:
    """UI-5970: Harness release_wait — 0 LLM rounds; fixed assistant line only."""
    from exec_reliability import runaway_verification_exit_short_circuit
    from turn_intent import intent_label

    if not runaway_verification_exit_short_circuit(
        runaway_enabled=bool(getattr(self.session.meta, "project_runaway_enabled", False)),
        workflow_stage=str(
            getattr(self.session.meta, "project_workflow_stage", "") or ""
        ),
        checkpoint=str(
            getattr(self.session.meta, "project_runaway_checkpoint", "") or ""
        ),
        acceptance_passed=bool(
            getattr(self.session.meta, "project_runaway_acceptance_passed", False)
        ),
        turn_intent=intent,
        user_text=user_text,
    ):
        return None

    self._emit_turn_event(
        {
            "type": "turn.start",
            "intent": intent,
            "intent_label": intent_label(intent, spawn_explore=False),
        }
    )
    self._emit_turn_event(
        {
            "type": "turn.notice",
            "level": "info",
            "text": runaway_release_wait_turn_notice(),
        }
    )
    assistant_text = runaway_release_wait_assistant_text()
    self.session.append_message({"role": "assistant", "content": assistant_text})
    self.session.save()
    return TurnResult(
        assistant_text=assistant_text,
        tool_rounds=0,
        finish_reason="runaway_verification_exit",
        turn_intent=intent,
        total_tool_rounds=0,
    )

def _maybe_short_circuit_runaway_verification_harness_turn(
    self,
    *,
    intent: str,
    user_text: str,
) -> TurnResult | None:
    """Harness-owned verification exit: skip main LLM on auto-chain lines (UI-6047)."""
    from exec_reliability import is_runaway_harness_utterance
    from runaway_flow import normalize_checkpoint
    from runaway_verify_matrix import format_matrix_summary, lint_verify_matrix
    from turn_intent import intent_label

    if not getattr(self.session.meta, "project_runaway_enabled", False):
        return None
    if getattr(self.session.meta, "project_workflow_stage", "") != "verification":
        return None
    if not is_runaway_harness_utterance(user_text):
        return None

    self._bootstrap_runaway_verification_artifacts()
    if self._sync_runaway_harness_truth(notice="Harness 已同步验收配置"):
        return self._maybe_finish_runaway_verification_exit_turn(
            intent=intent,
            user_text=user_text,
        )

    checkpoint = normalize_checkpoint(
        getattr(self.session.meta, "project_runaway_checkpoint", "")
    )
    pid = (self.session.meta.project_id or "").strip()
    if checkpoint in {"verifying", "repairing"} and pid:
        lint = lint_verify_matrix(self.session.paths, pid)
        if not lint.ok:
            summary = format_matrix_summary(lint)
            self._enter_runaway_repair_checkpoint(
                summary=summary,
                fingerprint=self._matrix_repair_fingerprint(summary),
            )
            if self._sync_runaway_harness_truth():
                return self._maybe_finish_runaway_verification_exit_turn(
                    intent=intent,
                    user_text=user_text,
                )

    self._emit_turn_event(
        {
            "type": "turn.start",
            "intent": intent,
            "intent_label": intent_label(intent, spawn_explore=False),
        }
    )
    self._emit_turn_event(
        {
            "type": "turn.notice",
            "level": "info",
            "text": runaway_verification_harness_skip_notice(),
        }
    )
    assistant_text = runaway_verification_harness_assistant_text()
    self.session.append_message({"role": "assistant", "content": assistant_text})
    self.session.save()
    return TurnResult(
        assistant_text=assistant_text,
        tool_rounds=0,
        finish_reason="runaway_verification_harness",
        turn_intent=intent,
        total_tool_rounds=0,
    )

def _runaway_repair_pending(self) -> bool:
    from runaway_flow import normalize_checkpoint

    return bool(
        getattr(self.session.meta, "project_runaway_enabled", False)
        and normalize_checkpoint(
            getattr(self.session.meta, "project_runaway_checkpoint", "")
        ) == "repairing"
    )

def _queue_runaway_documentation_repair(self, error: Exception) -> bool:
    """Continue the current run to repair incomplete project documents."""
    from project_mode import documentation_ready_for_design
    from runaway_flow import normalize_checkpoint

    pid = (self.session.meta.project_id or "").strip()
    if not pid:
        return False
    try:
        ready, missing = documentation_ready_for_design(self.session.paths, pid)
    except Exception:
        return False
    if ready:
        return False

    missing_text = ", ".join(missing) or "核心项目文档"
    if normalize_checkpoint(
        getattr(self.session.meta, "project_runaway_checkpoint", "")
    ) != "preparing":
        self._transition_runaway_checkpoint("preparing")
    self._sync_turn_mode()
    self.session.meta.project_runaway_paused_reason = ""
    self.session.meta.project_runaway_last_error = (
        f"文档待补齐：{missing_text}"
    )[:500]
    self.session.meta.updated_at = utc_now_iso()
    self.session.save()
    self.session.append_message(
        {
            "role": "user",
            "content": (
                "[Harness] 当前项目文档尚未达到设计确认标准，不能把这个缺口交给用户处理。"
                f"请只在当前项目目录内补齐或修正文档：{missing_text}。"
                "先读取现有 PROJECT.md、DESIGN.md、TASKS.md、VERIFY.md，保留已有内容，"
                "补齐需求/验收/设计/任务/验证之间的映射；完成后重新检查文档，"
                "不要请求用户确认，也不要等待用户回复。"
                f"触发检查信息：{str(error).strip()[:300]}"
            ),
        }
    )
    self._emit_turn_event(
        {
            "type": "turn.notice",
            "level": "info",
            "text": f"文档尚未完整，正在自动补齐：{missing_text}",
        }
    )
    return True


def record_runaway_review_result(
    self,
    *,
    verdict: str,
    blockers: int,
    summary: str,
) -> bool:
    """Run the V1 review-to-repair path after shared metadata is persisted."""
    meta = self.session.meta
    if verdict == "pass" and blockers == 0:
        from runaway_flow import normalize_checkpoint

        if normalize_checkpoint(
            getattr(meta, "project_runaway_checkpoint", "")
        ) == "repairing":
            self._transition_runaway_checkpoint("verifying")
        if not self._run_runaway_hard_verification():
            return self._runaway_repair_pending()
        self._sync_runaway_harness_truth(
            notice="交付审查通过，Harness 硬验收已通过",
        )
        return False

    # UI-5968: deliverable_review is advisory; never repair from review alone.
    if self._sync_runaway_harness_truth(
        notice="交付审查未通过，但 Harness 硬验收已通过，审查结论仅作参考",
    ):
        return False
    if not self._run_runaway_hard_verification():
        return self._runaway_repair_pending()
    self._sync_runaway_harness_truth()
    self._emit_turn_event(
        {
            "type": "turn.notice",
            "level": "warn",
            "text": "交付审查未通过；checkpoint 仅随 Harness 硬验收更新。",
        }
    )
    return False
