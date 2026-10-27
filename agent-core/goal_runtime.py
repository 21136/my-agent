"""Thin Goal Runtime: Session / Goal / Run split for long-task budgets.

P0 first knife — harness-agnostic. Persists ``goal-state.json`` beside the
session. A Run wraps one in-process ``ExecutionLifecycle`` activation and is
never revived across process restart (fresh run_id each ``begin_run``).

Does not rebuild runaway / TASKS / progress_gate / LangGraph.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Literal

from execution_lifecycle import ExecutionLifecycle, ExecutionSnapshot
from file_guard import atomic_write_text

GOAL_STATE_FILENAME = "goal-state.json"
SCHEMA_VERSION = 1

GoalStatus = Literal["idle", "active", "paused", "done", "failed", "stopped"]

_TERMINAL_STATUSES = frozenset({"done", "failed", "stopped"})
_DEFAULT_TOOL_TURNS_LIMIT = 50
_DEFAULT_WALL_SECONDS_LIMIT = 900.0
_DEFAULT_REPAIR_LIMIT = 3
_TAIL_MAX = 1200


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class GoalBudgets:
    tool_turns_used: int = 0
    tool_turns_limit: int = _DEFAULT_TOOL_TURNS_LIMIT
    wall_seconds_used: float = 0.0
    wall_seconds_limit: float = _DEFAULT_WALL_SECONDS_LIMIT
    repair_count: int = 0
    repair_limit: int = _DEFAULT_REPAIR_LIMIT

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_turns_used": int(self.tool_turns_used),
            "tool_turns_limit": int(self.tool_turns_limit),
            "wall_seconds_used": float(self.wall_seconds_used),
            "wall_seconds_limit": float(self.wall_seconds_limit),
            "repair_count": int(self.repair_count),
            "repair_limit": int(self.repair_limit),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> GoalBudgets:
        raw = raw if isinstance(raw, dict) else {}
        return cls(
            tool_turns_used=max(0, int(raw.get("tool_turns_used") or 0)),
            tool_turns_limit=max(
                1, int(raw.get("tool_turns_limit") or _DEFAULT_TOOL_TURNS_LIMIT)
            ),
            wall_seconds_used=max(0.0, float(raw.get("wall_seconds_used") or 0.0)),
            wall_seconds_limit=max(
                1.0,
                float(raw.get("wall_seconds_limit") or _DEFAULT_WALL_SECONDS_LIMIT),
            ),
            repair_count=max(0, int(raw.get("repair_count") or 0)),
            repair_limit=max(0, int(raw.get("repair_limit") or _DEFAULT_REPAIR_LIMIT)),
        )

    def tool_budget_remaining(self) -> int:
        return max(0, self.tool_turns_limit - self.tool_turns_used)

    def wall_budget_remaining(self) -> float:
        return max(0.0, self.wall_seconds_limit - self.wall_seconds_used)

    def tool_exhausted(self) -> bool:
        return self.tool_turns_used >= self.tool_turns_limit

    def wall_exhausted(self) -> bool:
        return self.wall_seconds_used >= self.wall_seconds_limit

    def repair_exhausted(self) -> bool:
        return self.repair_count >= self.repair_limit


@dataclass
class LastVerification:
    command: str = ""
    exit_code: int | None = None
    expected_exit_code: int = 0
    tail: str = ""
    passed: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "expected_exit_code": int(self.expected_exit_code),
            "tail": self.tail,
            "passed": self.passed,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> LastVerification | None:
        if not isinstance(raw, dict):
            return None
        exit_raw = raw.get("exit_code")
        exit_code: int | None
        if exit_raw is None:
            exit_code = None
        else:
            try:
                exit_code = int(exit_raw)
            except (TypeError, ValueError):
                exit_code = None
        passed_raw = raw.get("passed")
        passed: bool | None
        if passed_raw is None:
            passed = None
        else:
            passed = bool(passed_raw)
        return cls(
            command=str(raw.get("command") or ""),
            exit_code=exit_code,
            expected_exit_code=int(raw.get("expected_exit_code") or 0),
            tail=str(raw.get("tail") or ""),
            passed=passed,
        )


@dataclass
class GoalState:
    goal_id: str
    goal_text: str
    workspace_scope: str = ""
    acceptance_command: str = ""
    expected_exit_code: int = 0
    status: GoalStatus = "idle"
    next_step: str = ""
    budgets: GoalBudgets = field(default_factory=GoalBudgets)
    last_verification: LastVerification | None = None
    pause_reason: str | None = None
    stop_requested: bool = False
    last_run_id: str | None = None
    schema_version: int = SCHEMA_VERSION
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": int(self.schema_version),
            "goal_id": self.goal_id,
            "goal_text": self.goal_text,
            "workspace_scope": self.workspace_scope,
            "acceptance_command": self.acceptance_command,
            "expected_exit_code": int(self.expected_exit_code),
            "status": self.status,
            "next_step": self.next_step,
            "budgets": self.budgets.to_dict(),
            "last_verification": (
                self.last_verification.to_dict()
                if self.last_verification is not None
                else None
            ),
            "pause_reason": self.pause_reason,
            "stop_requested": bool(self.stop_requested),
            "last_run_id": self.last_run_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> GoalState:
        status = str(raw.get("status") or "idle")
        if status not in {
            "idle",
            "active",
            "paused",
            "done",
            "failed",
            "stopped",
        }:
            status = "idle"
        return cls(
            goal_id=str(raw.get("goal_id") or uuid.uuid4().hex),
            goal_text=str(raw.get("goal_text") or ""),
            workspace_scope=str(raw.get("workspace_scope") or ""),
            acceptance_command=str(raw.get("acceptance_command") or ""),
            expected_exit_code=int(raw.get("expected_exit_code") or 0),
            status=status,  # type: ignore[arg-type]
            next_step=str(raw.get("next_step") or ""),
            budgets=GoalBudgets.from_dict(
                raw.get("budgets") if isinstance(raw.get("budgets"), dict) else None
            ),
            last_verification=LastVerification.from_dict(
                raw.get("last_verification")
                if isinstance(raw.get("last_verification"), dict)
                else None
            ),
            pause_reason=(
                str(raw["pause_reason"])
                if raw.get("pause_reason") is not None
                else None
            ),
            stop_requested=bool(raw.get("stop_requested")),
            last_run_id=(
                str(raw["last_run_id"]) if raw.get("last_run_id") is not None else None
            ),
            schema_version=int(raw.get("schema_version") or SCHEMA_VERSION),
            created_at=str(raw.get("created_at") or ""),
            updated_at=str(raw.get("updated_at") or ""),
        )


def goal_state_path(session_dir: Path) -> Path:
    return Path(session_dir) / GOAL_STATE_FILENAME


def truncate_tail(*parts: str, limit: int = _TAIL_MAX) -> str:
    text = "\n".join(p for p in parts if p).strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def evaluate_acceptance_exit(
    *,
    exit_code: int,
    expected_exit_code: int = 0,
    command: str = "",
    stdout: str = "",
    stderr: str = "",
) -> dict[str, Any]:
    """Acceptance truth is exit code only — never LLM prose."""
    passed = int(exit_code) == int(expected_exit_code)
    return {
        "ok": True,
        "passed": passed,
        "exit_code": int(exit_code),
        "expected_exit_code": int(expected_exit_code),
        "command": command,
        "tail": truncate_tail(stdout, stderr),
    }


def normalize_project_acceptance_result(result: dict[str, Any]) -> dict[str, Any]:
    """Map ``run_acceptance_check`` output onto exit-code-only verdict."""
    command = str(result.get("command") or "")
    expected = int(result.get("expected_exit_code") or 0)
    if "exit_code" not in result or result.get("exit_code") is None:
        # Missing exit code cannot pass — do not trust narrative fields.
        return {
            "ok": bool(result.get("ok", False)),
            "passed": False,
            "exit_code": -1,
            "expected_exit_code": expected,
            "command": command,
            "tail": truncate_tail(
                str(result.get("error") or ""),
                str(result.get("stdout") or ""),
                str(result.get("stderr") or ""),
            ),
            "error": result.get("error"),
        }
    verdict = evaluate_acceptance_exit(
        exit_code=int(result["exit_code"]),
        expected_exit_code=expected,
        command=command,
        stdout=str(result.get("stdout") or ""),
        stderr=str(result.get("stderr") or ""),
    )
    if result.get("error"):
        verdict["error"] = result.get("error")
    return verdict


class GoalRuntime:
    """Session-adjacent Goal store + one-shot Run over ExecutionLifecycle."""

    def __init__(
        self,
        session_dir: Path,
        *,
        lifecycle: ExecutionLifecycle | None = None,
        clock: Callable[[], float] | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.session_dir = Path(session_dir)
        self._clock = clock or time.time
        self.lifecycle = lifecycle or ExecutionLifecycle(
            run_id_factory=run_id_factory,
            clock=self._clock,
        )
        self._state: GoalState | None = None
        self._run_wall_started_at: float | None = None

    @property
    def path(self) -> Path:
        return goal_state_path(self.session_dir)

    @property
    def state(self) -> GoalState | None:
        return self._state

    def load(self) -> GoalState | None:
        path = self.path
        if not path.is_file():
            self._state = None
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._state = None
            return None
        if not isinstance(payload, dict):
            self._state = None
            return None
        state = GoalState.from_dict(payload)
        # Never revive a Run across restart: clear last_run_id linkage to lifecycle.
        # Status stays so UI / budgets persist; auto-continue is gated separately.
        if state.status == "active" and not state.stop_requested:
            # Soft-land as paused so a restart does not silently keep running.
            state.status = "paused"
            state.pause_reason = state.pause_reason or "process_restart"
            state.next_step = state.next_step or "等待用户确认后继续"
        self._state = state
        return state

    def save(self) -> None:
        if self._state is None:
            return
        self._state.updated_at = utc_now_iso()
        self.session_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.path,
            json.dumps(self._state.to_dict(), ensure_ascii=False, indent=2) + "\n",
        )

    def ensure_goal(
        self,
        *,
        goal_text: str,
        workspace_scope: str = "",
        acceptance_command: str = "",
        expected_exit_code: int = 0,
        tool_turns_limit: int | None = None,
        wall_seconds_limit: float | None = None,
        repair_limit: int | None = None,
        replace: bool = False,
    ) -> GoalState:
        text = (goal_text or "").strip() or "动手任务"
        current = self._state if self._state is not None else self.load()
        if (
            current is not None
            and not replace
            and current.status not in _TERMINAL_STATUSES
            and (current.goal_text.strip() == text or not current.goal_text.strip())
        ):
            if workspace_scope:
                current.workspace_scope = workspace_scope
            if acceptance_command:
                current.acceptance_command = acceptance_command
                current.expected_exit_code = int(expected_exit_code)
            if tool_turns_limit is not None:
                current.budgets.tool_turns_limit = max(1, int(tool_turns_limit))
            if wall_seconds_limit is not None:
                current.budgets.wall_seconds_limit = max(1.0, float(wall_seconds_limit))
            if repair_limit is not None:
                current.budgets.repair_limit = max(0, int(repair_limit))
            if not current.goal_text.strip():
                current.goal_text = text
            self._state = current
            self.save()
            return current

        now = utc_now_iso()
        budgets = GoalBudgets(
            tool_turns_limit=max(
                1, int(tool_turns_limit or _DEFAULT_TOOL_TURNS_LIMIT)
            ),
            wall_seconds_limit=max(
                1.0, float(wall_seconds_limit or _DEFAULT_WALL_SECONDS_LIMIT)
            ),
            repair_limit=max(
                0,
                int(
                    repair_limit
                    if repair_limit is not None
                    else _DEFAULT_REPAIR_LIMIT
                ),
            ),
        )
        state = GoalState(
            goal_id=uuid.uuid4().hex,
            goal_text=text,
            workspace_scope=workspace_scope.strip(),
            acceptance_command=acceptance_command.strip(),
            expected_exit_code=int(expected_exit_code),
            status="idle",
            next_step="开始执行",
            budgets=budgets,
            created_at=now,
            updated_at=now,
        )
        self._state = state
        self.save()
        return state

    def begin_run(self, mode: str = "agent") -> tuple[GoalState, ExecutionSnapshot]:
        state = self._require_state()
        if state.stop_requested or state.status == "stopped":
            raise RuntimeError("goal is stopped; refuse to begin_run")
        if state.status in {"done", "failed"}:
            raise RuntimeError(f"goal is {state.status}; refuse to begin_run")
        if state.budgets.tool_exhausted() or state.budgets.wall_exhausted():
            state.status = "paused"
            state.pause_reason = "budget_exhausted"
            state.next_step = "预算已用尽，需用户扩预算或收窄目标"
            self.save()
            raise RuntimeError("goal budget exhausted; refuse to begin_run")

        snapshot = self.lifecycle.start_or_activate(mode)
        state.status = "active"
        state.pause_reason = None
        state.last_run_id = snapshot.run_id
        state.next_step = state.next_step or "执行中"
        self._run_wall_started_at = self._clock()
        self.save()
        return state, snapshot

    def record_segment_usage(
        self,
        *,
        tool_turns: int,
        wall_seconds: float | None = None,
        repair_delta: int = 0,
    ) -> GoalState:
        """Accumulate budgets across segments — never reset mid-goal."""
        state = self._require_state()
        state.budgets.tool_turns_used += max(0, int(tool_turns))
        if wall_seconds is None:
            if self._run_wall_started_at is not None:
                wall_seconds = max(0.0, self._clock() - self._run_wall_started_at)
                self._run_wall_started_at = self._clock()
            else:
                wall_seconds = 0.0
        state.budgets.wall_seconds_used += max(0.0, float(wall_seconds))
        if repair_delta:
            state.budgets.repair_count += max(0, int(repair_delta))
        if state.budgets.tool_exhausted() or state.budgets.wall_exhausted():
            state.status = "paused"
            state.pause_reason = "budget_exhausted"
            state.next_step = "预算已用尽，已暂停"
        self.save()
        return state

    def remaining_tool_budget(self) -> int:
        state = self._state
        if state is None:
            return 0
        return state.budgets.tool_budget_remaining()

    def should_auto_continue(self) -> bool:
        """Segments may chain only while active, not stopped, and under budget.

        Stop is sticky: after ``request_stop`` this returns False forever for
        this goal (including after reload), so restart never auto-continues.
        """
        state = self._state if self._state is not None else self.load()
        if state is None:
            return False
        if state.stop_requested or state.status in _TERMINAL_STATUSES:
            return False
        if state.status == "paused":
            return False
        if state.budgets.tool_exhausted() or state.budgets.wall_exhausted():
            return False
        if state.budgets.repair_exhausted() and state.pause_reason == "repair_exhausted":
            return False
        return state.status in {"idle", "active"}

    def apply_acceptance(
        self,
        *,
        command: str,
        exit_code: int,
        expected_exit_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> GoalState:
        state = self._require_state()
        expected = (
            int(expected_exit_code)
            if expected_exit_code is not None
            else int(state.expected_exit_code)
        )
        verdict = evaluate_acceptance_exit(
            exit_code=int(exit_code),
            expected_exit_code=expected,
            command=command or state.acceptance_command,
            stdout=stdout,
            stderr=stderr,
        )
        state.last_verification = LastVerification(
            command=str(verdict["command"]),
            exit_code=int(verdict["exit_code"]),
            expected_exit_code=int(verdict["expected_exit_code"]),
            tail=str(verdict["tail"]),
            passed=bool(verdict["passed"]),
        )
        if verdict["passed"]:
            state.status = "done"
            state.pause_reason = None
            state.next_step = ""
            self._finish_lifecycle(ok=True, finish_reason="acceptance_passed")
        else:
            # Failure feeds the next turn — stay active (or paused if budget gone).
            state.budgets.repair_count += 1
            if state.budgets.repair_exhausted():
                state.status = "paused"
                state.pause_reason = "repair_exhausted"
                state.next_step = (
                    f"验收失败（exit={verdict['exit_code']}，"
                    f"期望 {verdict['expected_exit_code']}）；修复次数已用尽"
                )
                self._finish_lifecycle(ok=False, finish_reason="repair_exhausted")
            elif state.budgets.tool_exhausted() or state.budgets.wall_exhausted():
                state.status = "paused"
                state.pause_reason = "budget_exhausted"
                state.next_step = (
                    f"验收失败（exit={verdict['exit_code']}）；预算已用尽"
                )
                self._finish_lifecycle(ok=False, finish_reason="budget_exhausted")
            else:
                state.status = "active"
                state.pause_reason = None
                state.next_step = (
                    f"验收失败（exit={verdict['exit_code']}，"
                    f"期望 {verdict['expected_exit_code']}）；根据证据修复后重跑验收"
                )
                self._finish_lifecycle(ok=False, finish_reason="acceptance_failed")
        self.save()
        return state

    def apply_acceptance_result(self, result: dict[str, Any]) -> GoalState:
        verdict = normalize_project_acceptance_result(result)
        return self.apply_acceptance(
            command=str(verdict.get("command") or ""),
            exit_code=int(verdict.get("exit_code", -1)),
            expected_exit_code=int(verdict.get("expected_exit_code") or 0),
            stdout=str(verdict.get("tail") or ""),
            stderr="",
        )

    def mark_budget_paused(self) -> GoalState:
        state = self._require_state()
        state.status = "paused"
        state.pause_reason = "budget_exhausted"
        state.next_step = "预算已用尽，已暂停"
        self._finish_lifecycle(ok=False, finish_reason="total_cap_exceeded")
        self.save()
        return state

    def request_stop(
        self,
        reason: str = "user",
    ) -> tuple[GoalState, ExecutionSnapshot | None]:
        state = self._require_state()
        state.stop_requested = True
        state.status = "stopped"
        state.pause_reason = (reason or "user").strip() or "user"
        state.next_step = "已停止；不会自动续跑"
        snapshot, _changed = self.lifecycle.request_stop(state.pause_reason)
        # Settle the lifecycle so a later begin_run on a new Goal can start cleanly.
        self.lifecycle.finish("stopped", ok=False, final_state="settled")
        self.save()
        return state, snapshot

    def finish_run(
        self,
        *,
        ok: bool,
        finish_reason: str,
    ) -> GoalState:
        state = self._require_state()
        if state.status not in _TERMINAL_STATUSES and state.status != "paused":
            if ok:
                # Without acceptance, leave active so a later accept can close it;
                # caller may set done explicitly via apply_acceptance.
                state.next_step = state.next_step or "等待验收或下一步"
            elif finish_reason in {"cancelled", "stopped"}:
                return self.request_stop(finish_reason)[0]
            elif finish_reason in {
                "total_cap_exceeded",
                "segment_cap_pause",
                "tool_loop_exceeded",
            }:
                return self.mark_budget_paused()
        self._finish_lifecycle(ok=ok, finish_reason=finish_reason)
        self.save()
        return state

    def status_event(self) -> dict[str, Any] | None:
        if self._state is None:
            return None
        payload = self._state.to_dict()
        payload["type"] = "goal.state"
        payload["can_auto_continue"] = self.should_auto_continue()
        return payload

    def _finish_lifecycle(self, *, ok: bool, finish_reason: str) -> None:
        current = self.lifecycle.snapshot()
        if current is None:
            return
        if current.state in {"settled", "paused", "failed"}:
            return
        self.lifecycle.finish(finish_reason, ok=ok)

    def _require_state(self) -> GoalState:
        if self._state is None:
            loaded = self.load()
            if loaded is None:
                raise RuntimeError("no goal state; call ensure_goal first")
        assert self._state is not None
        return self._state


def goal_runtime_for_session(
    session: Any,
    *,
    lifecycle: ExecutionLifecycle | None = None,
) -> GoalRuntime | None:
    """Bind GoalRuntime for 动手 (agent) turns only — chat-only stays untouched."""
    meta = getattr(session, "meta", None)
    turn_mode = getattr(meta, "turn_mode", None) if meta is not None else None
    if turn_mode != "agent":
        return None
    session_dir = getattr(session, "session_dir", None)
    if session_dir is None:
        return None
    runtime = GoalRuntime(Path(session_dir), lifecycle=lifecycle)
    runtime.load()
    return runtime


def resolve_workspace_scope(session: Any) -> str:
    meta = getattr(session, "meta", None)
    if meta is None:
        return ""
    project_id = (getattr(meta, "project_id", None) or "").strip()
    active_shell = (getattr(meta, "active_shell", None) or "").strip()
    if project_id:
        return f"workspace/{project_id}"
    if active_shell:
        return active_shell
    return ""


def resolve_acceptance_from_session(session: Any) -> tuple[str, int]:
    """Best-effort PROJECT.md acceptance command for the bound project."""
    meta = getattr(session, "meta", None)
    if meta is None:
        return "", 0
    project_id = (getattr(meta, "project_id", None) or "").strip()
    if not project_id:
        return "", 0
    paths = getattr(session, "paths", None)
    if paths is None:
        return "", 0
    try:
        from project_mode import parse_acceptance_spec, read_project_artifacts
    except Exception:
        return "", 0
    try:
        artifacts = read_project_artifacts(paths, project_id)
        project_md = artifacts.get("PROJECT.md", "") if isinstance(artifacts, dict) else ""
        spec = parse_acceptance_spec(project_md) if project_md else None
    except Exception:
        return "", 0
    if spec is None:
        return "", 0
    return spec.display, int(spec.expected_exit_code)


def maybe_run_project_acceptance(session: Any) -> dict[str, Any] | None:
    """Run PROJECT.md acceptance when bound; return exit-code-normalized verdict."""
    meta = getattr(session, "meta", None)
    if meta is None:
        return None
    project_id = (getattr(meta, "project_id", None) or "").strip()
    if not project_id:
        return None
    paths = getattr(session, "paths", None)
    if paths is None:
        return None
    try:
        from project_mode import (
            parse_acceptance_spec,
            read_project_artifacts,
            run_acceptance_check,
        )
    except Exception:
        return None
    try:
        artifacts = read_project_artifacts(paths, project_id)
        project_md = artifacts.get("PROJECT.md", "") if isinstance(artifacts, dict) else ""
        spec = parse_acceptance_spec(project_md) if project_md else None
    except Exception:
        return None
    if spec is None:
        return None
    try:
        raw = run_acceptance_check(paths, project_id, spec)
    except Exception as exc:
        return normalize_project_acceptance_result(
            {
                "ok": False,
                "command": spec.display,
                "expected_exit_code": spec.expected_exit_code,
                "error": str(exc),
            }
        )
    return normalize_project_acceptance_result(raw)
