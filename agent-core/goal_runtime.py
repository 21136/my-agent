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
_PROCESS_INSTANCE_ID = uuid.uuid4().hex

GoalStatus = Literal["idle", "active", "paused", "done", "failed", "stopped"]

_TERMINAL_STATUSES = frozenset({"done", "failed", "stopped"})
_DEFAULT_TOOL_TURNS_LIMIT = 50
_DEFAULT_WALL_SECONDS_LIMIT = 900.0
_DEFAULT_REPAIR_LIMIT = 3
_DEFAULT_AUTO_CONTINUE_TOOL_TURNS_LIMIT = 50
_DEFAULT_AUTO_CONTINUE_WALL_SECONDS_LIMIT = 900.0
_MAX_WALL_EXTENSION_SECONDS = 900
_MAX_WALL_SECONDS_LIMIT = 3600.0
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
    multi_project_unverified: bool = False
    multi_project_ids: list[str] = field(default_factory=list)
    pause_reason: str | None = None
    stop_requested: bool = False
    last_run_id: str | None = None
    process_instance_id: str | None = None
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
            "multi_project_unverified": bool(self.multi_project_unverified),
            "multi_project_ids": list(self.multi_project_ids),
            "pause_reason": self.pause_reason,
            "stop_requested": bool(self.stop_requested),
            "last_run_id": self.last_run_id,
            "process_instance_id": self.process_instance_id,
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
            multi_project_unverified=raw.get("multi_project_unverified") is True,
            multi_project_ids=list(dict.fromkeys(
                project_id for project_id in raw.get("multi_project_ids", [])
                if type(project_id) is str and project_id.strip()
            )) if type(raw.get("multi_project_ids", [])) is list else [],
            pause_reason=(
                str(raw["pause_reason"])
                if raw.get("pause_reason") is not None
                else None
            ),
            stop_requested=bool(raw.get("stop_requested")),
            last_run_id=(
                str(raw["last_run_id"]) if raw.get("last_run_id") is not None else None
            ),
            process_instance_id=(
                str(raw["process_instance_id"]) if raw.get("process_instance_id") is not None else None
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
        self._clock = clock or time.monotonic
        self.lifecycle = lifecycle or ExecutionLifecycle(
            run_id_factory=run_id_factory,
            clock=self._clock,
        )
        self._state: GoalState | None = None
        self._run_wall_started_at: float | None = None
        self._pending_wall_seconds = 0.0
        self._wall_clock_pause_depth = 0
        self._run_auto_tool_turns_used = 0
        self._run_auto_wall_seconds_used = 0.0
        self._run_auto_tool_turns_limit = _DEFAULT_AUTO_CONTINUE_TOOL_TURNS_LIMIT
        self._run_auto_wall_seconds_limit = _DEFAULT_AUTO_CONTINUE_WALL_SECONDS_LIMIT

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
        # An active Goal may outlive an individual Run in the same process. Only
        # reinterpret it as interrupted when its owning process differs.
        if (state.status == "active" and not state.stop_requested
                and state.process_instance_id != _PROCESS_INSTANCE_ID):
            state.status = "paused"
            state.pause_reason = state.pause_reason or "process_restart"
            state.next_step = state.next_step or "等待用户确认后继续"
        legacy_budget_recovery = (
            state.status == "idle" and state.pause_reason is None
        ) or (
            state.status == "paused" and state.pause_reason == "budget_exhausted"
        )
        if (not state.stop_requested and state.status not in _TERMINAL_STATUSES
                and legacy_budget_recovery
                and (state.budgets.tool_exhausted() or state.budgets.wall_exhausted())):
            # Reconcile only legacy budget/restart states; never downgrade a
            # repair, authorization, or unknown safety pause.
            state.status = "paused"
            state.pause_reason = "budget_exhausted"
            state.next_step = "累计预算已用尽；可收窄目标，或显式扩展 wall 预算"
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

        if current is not None:
            self._archive_goal(current)

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

    def begin_run(
        self,
        mode: str = "agent",
        *,
        auto_continue_tool_turns_limit: int = _DEFAULT_AUTO_CONTINUE_TOOL_TURNS_LIMIT,
        auto_continue_wall_seconds_limit: float = _DEFAULT_AUTO_CONTINUE_WALL_SECONDS_LIMIT,
    ) -> tuple[GoalState, ExecutionSnapshot]:
        state = self._require_state()
        if state.stop_requested or state.status == "stopped":
            raise RuntimeError("goal is stopped; refuse to begin_run")
        if state.status in {"done", "failed"}:
            raise RuntimeError(f"goal is {state.status}; refuse to begin_run")
        if state.status == "paused" and state.pause_reason not in {
            "budget_exhausted", "process_restart", "budget_extended",
        }:
            raise RuntimeError("paused goal requires explicit recovery")
        snapshot = self.lifecycle.start_or_activate(mode)
        state.status = "active"
        state.pause_reason = None
        state.last_run_id = snapshot.run_id
        state.process_instance_id = _PROCESS_INSTANCE_ID
        state.next_step = state.next_step or "执行中"
        self._run_wall_started_at = self._clock()
        self._pending_wall_seconds = 0.0
        self._wall_clock_pause_depth = 0
        self._run_auto_tool_turns_used = 0
        self._run_auto_wall_seconds_used = 0.0
        self._run_auto_tool_turns_limit = max(1, int(auto_continue_tool_turns_limit))
        self._run_auto_wall_seconds_limit = max(1.0, float(auto_continue_wall_seconds_limit))
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
        normalized_tool_turns = max(0, int(tool_turns))
        state.budgets.tool_turns_used += normalized_tool_turns
        self._run_auto_tool_turns_used += normalized_tool_turns
        if wall_seconds is None:
            if self._run_wall_started_at is not None:
                wall_seconds = self._pending_wall_seconds + max(0.0, self._clock() - self._run_wall_started_at)
                self._pending_wall_seconds = 0.0
                self._run_wall_started_at = self._clock()
            else:
                wall_seconds = 0.0
        normalized_wall_seconds = max(0.0, float(wall_seconds))
        state.budgets.wall_seconds_used += normalized_wall_seconds
        self._run_auto_wall_seconds_used += normalized_wall_seconds
        if repair_delta:
            state.budgets.repair_count += max(0, int(repair_delta))
        self.save()
        return state

    def pause_wall_clock(self) -> None:
        """Exclude synchronous human-confirmation waits from active wall usage."""
        self._wall_clock_pause_depth += 1
        if self._wall_clock_pause_depth == 1 and self._run_wall_started_at is not None:
            self._pending_wall_seconds += max(0.0, self._clock() - self._run_wall_started_at)
            self._run_wall_started_at = None

    def resume_wall_clock(self) -> None:
        """Resume accounting after the outermost confirmation callback returns."""
        if self._wall_clock_pause_depth <= 0:
            return
        self._wall_clock_pause_depth -= 1
        if self._wall_clock_pause_depth == 0 and self._run_wall_started_at is None:
            self._run_wall_started_at = self._clock()

    def _archive_goal(self, state: GoalState) -> None:
        """Retain replaced Goal records instead of silently dropping their budgets/evidence."""
        path = self.session_dir / "goal-history.json"
        try:
            rows = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("goal history is unreadable; refusing to replace the current goal") from exc
        if (not isinstance(rows, list) or any(
                not isinstance(row, dict) or not isinstance(row.get("goal_id"), str)
                or not isinstance(row.get("budgets"), dict)
                for row in rows
        )):
            raise RuntimeError("goal history has an invalid shape; refusing to replace the current goal")
        if not any(row.get("goal_id") == state.goal_id for row in rows):
            rows.append(state.to_dict())
        try:
            atomic_write_text(path, json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
        except OSError as exc:
            raise RuntimeError("goal history could not be written; refusing to replace the current goal") from exc

    def extend_wall_budget(self, seconds: int) -> GoalState:
        """Explicitly extend a paused Goal's wall cap; never starts or auto-resumes a Run."""
        if type(seconds) is not int or not 1 <= seconds <= _MAX_WALL_EXTENSION_SECONDS:
            raise ValueError(f"wall budget extension must be 1..{_MAX_WALL_EXTENSION_SECONDS} seconds")
        state = self._require_state()
        if state.status != "paused" or state.pause_reason != "budget_exhausted":
            raise RuntimeError("only a budget-exhausted paused Goal can be extended")
        if state.budgets.tool_exhausted() or state.budgets.repair_exhausted():
            raise RuntimeError("tool-turn or repair budget is also exhausted; wall-only extension is insufficient")
        new_limit = state.budgets.wall_seconds_limit + seconds
        if new_limit > _MAX_WALL_SECONDS_LIMIT:
            raise ValueError(f"wall budget limit cannot exceed {_MAX_WALL_SECONDS_LIMIT:g} seconds")
        state.budgets.wall_seconds_limit = new_limit
        if not state.budgets.wall_exhausted():
            state.pause_reason = "budget_extended"
            state.next_step = "预算已扩展；等待用户在新回合明确继续，不会自动执行"
        self.save()
        return state

    def remaining_tool_budget(self) -> int:
        """Legacy cumulative counter view; never used to gate manual execution."""
        state = self._state
        if state is None:
            return 0
        return state.budgets.tool_budget_remaining()

    def should_auto_continue(self) -> bool:
        """Gate only same-Run automatic segments, never a new human request.

        The round and elapsed-wall counters reset at ``begin_run``. Elapsed wall
        is checked only after a segment returns, so it cannot interrupt active
        first-segment work. Progress events are transport activity, not evidence
        that the task is semantically advancing. Stop is sticky across reloads.
        """
        state = self._state if self._state is not None else self.load()
        if state is None:
            return False
        if state.stop_requested or state.status in _TERMINAL_STATUSES:
            return False
        if state.status == "paused":
            return False
        # These counters are cumulative telemetry. Only the per-run loop bound
        # limits an explicitly requested execution; repair exhaustion gates
        # automatic continuation until a human initiates another turn.
        if state.budgets.repair_exhausted():
            return False
        if self._run_auto_tool_turns_used >= self._run_auto_tool_turns_limit:
            return False
        if self._run_auto_wall_seconds_used >= self._run_auto_wall_seconds_limit:
            return False
        return state.status in {"idle", "active"}

    def mark_multi_project_unverified(
        self, project_ids: tuple[str, ...] | list[str],
    ) -> GoalState:
        """Persist a conservative gate: a multi-project batch is not yet verified."""
        state = self._require_state()
        if type(project_ids) not in (tuple, list) or len(project_ids) < 2:
            raise ValueError("multi-project batch requires at least two project_ids")
        if any(type(project_id) is not str or not project_id.strip() for project_id in project_ids):
            raise ValueError("project_id must be a non-empty string")
        requested = list(dict.fromkeys(project_ids))
        if len(requested) < 2:
            raise ValueError("multi-project batch requires at least two distinct project_ids")
        state.multi_project_unverified = True
        state.multi_project_ids = list(dict.fromkeys((*state.multi_project_ids, *requested)))
        # A previous single-project pass cannot be presented as current evidence.
        state.last_verification = None
        state.status = "active"
        state.pause_reason = None
        state.next_step = (
            "多项目 worker 批次尚未逐项验收，父 Goal 保持未完成："
            + "、".join(state.multi_project_ids)
        )
        self.save()
        return state

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
        if state.multi_project_unverified:
            raise RuntimeError("multi-project Goal remains unverified")
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
            # Failure feeds a later human turn. Cumulative usage is telemetry;
            # only the repair circuit-breaker may pause this Goal here.
            state.budgets.repair_count += 1
            if state.budgets.repair_exhausted():
                state.status = "paused"
                state.pause_reason = "repair_exhausted"
                state.next_step = (
                    f"验收失败（exit={verdict['exit_code']}，"
                    f"期望 {verdict['expected_exit_code']}）；修复次数已用尽"
                )
                self._finish_lifecycle(ok=False, finish_reason="repair_exhausted")
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
        if self._require_state().multi_project_unverified:
            raise RuntimeError("multi-project Goal remains unverified")
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
        state.next_step = "本回合工具执行额度已用尽，自动续段已停止；检查进展后可由用户发起新回合"
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
        payload.pop("process_instance_id", None)
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


def resolve_turn_goal_text(
    current: GoalState | None, session_goal: str, explicit_new_goal: str | None = None,
) -> str:
    """Keep persisted Goal identity stable; only the explicit control action replaces it."""
    if explicit_new_goal is not None:
        return explicit_new_goal
    if current is not None and current.goal_text.strip():
        return current.goal_text
    return (session_goal or "").strip() or "动手任务"


def resolve_goal_action_intent(classified_intent: str, explicit_new_goal: str | None) -> str:
    """The explicit new-Goal command always routes its task body to Agent execution."""
    return "execute" if explicit_new_goal is not None else classified_intent


def explicit_new_goal_from_request(request: str) -> str | None:
    """Parse the deliberate `/goal new: ...` budget-boundary action; never infer from prose."""
    if type(request) is not str:
        return None
    import re
    match = re.fullmatch(r"\s*/goal\s+new:\s*(.+?)\s*", request, flags=re.IGNORECASE | re.DOTALL)
    if match is None:
        return None
    text = match.group(1).strip()
    return text if text else None


def explicit_wall_budget_extension(request: str) -> int | None:
    """Parse only the standalone `/goal extend: N` manual budget action."""
    if type(request) is not str:
        return None
    import re
    match = re.fullmatch(r"\s*/goal\s+extend:\s*(\d+)\s*", request, flags=re.IGNORECASE)
    if match is None:
        return None
    seconds = int(match.group(1))
    return seconds if 1 <= seconds <= _MAX_WALL_EXTENSION_SECONDS else None


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
