"""Plan Agent: owns the project plan (TASKS.md / MAP.md / PROJECT.md).

Routes user messages, manages task operations, maintains change log,
detects plan_dirty via fingerprint comparison.

Phase 4: infrastructure + routing + task operations + fingerprint.
Phase 5+: LLM-based split/suggest will be added.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from project_mode import (
    ProjectModeError,
    drop_task_line,
    normalize_project_id,
    phase_fingerprint_from_text,
    plan_allows_code_writes,
    project_dir,
    read_project_artifacts,
    read_task_stats,
    reorder_task_line,
    skip_task_line,
    toggle_task_line,
)
from session import Session

RouteDecision = Literal["handle", "forward", "split"]
ChangeKind = Literal["add", "drop", "skip", "reorder", "toggle", "external"]


@dataclass
class PlanChange:
    id: str
    kind: ChangeKind
    task_text: str
    reason: str
    time: str
    line: int | None = None


@dataclass
class PlanAgent:
    """Manages the project plan for one project workspace.

    Lifecycle: bound to a workspace directory (not a session).
    Persisted to workspace/<project_id>/.plan-agent/.
    """

    paths: AgentPaths
    project_id: str
    _change_log: list[PlanChange] = field(default_factory=list)
    _last_fingerprint: str = ""
    _change_counter: int = 0

    def __post_init__(self) -> None:
        self._load_state()

    # ---- persistence ----

    @property
    def _state_dir(self) -> Path:
        return project_dir(self.paths, self.project_id) / ".plan-agent"

    @property
    def _state_path(self) -> Path:
        return self._state_dir / "state.json"

    def _load_state(self) -> None:
        if not self._state_path.is_file():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._last_fingerprint = data.get("fingerprint", "")
            self._change_counter = data.get("change_counter", 0)
            for entry in data.get("change_log", []):
                self._change_log.append(PlanChange(
                    id=entry.get("id", ""),
                    kind=entry.get("kind", ""),  # type: ignore[arg-type]
                    task_text=entry.get("task_text", ""),
                    reason=entry.get("reason", ""),
                    time=entry.get("time", ""),
                    line=entry.get("line"),
                ))
        except (OSError, json.JSONDecodeError):
            pass

    def _save_state(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "fingerprint": self._last_fingerprint,
            "change_counter": self._change_counter,
            "change_log": [
                {
                    "id": c.id,
                    "kind": c.kind,
                    "task_text": c.task_text,
                    "reason": c.reason,
                    "time": c.time,
                    "line": c.line,
                }
                for c in self._change_log[-200:]  # cap at 200 entries
            ],
        }
        self._state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- change log ----

    def _record_change(self, kind: ChangeKind, task_text: str, reason: str = "", line: int | None = None) -> PlanChange:
        self._change_counter += 1
        change = PlanChange(
            id=f"ch_{self._change_counter:04d}",
            kind=kind,
            task_text=task_text,
            reason=reason,
            time=datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            line=line,
        )
        self._change_log.append(change)
        if len(self._change_log) > 200:
            self._change_log = self._change_log[-200:]
        return change

    def pending_changes(self) -> list[PlanChange]:
        """Changes since last plan confirmation."""
        if not self._last_fingerprint:
            return list(self._change_log)
        confirmed_idx = -1
        for i, c in enumerate(self._change_log):
            if c.kind == "confirm":
                confirmed_idx = i
        return list(self._change_log[confirmed_idx + 1:])

    # ---- fingerprint & plan_dirty ----

    def _current_fingerprint(self) -> str:
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        if not tasks_path.is_file():
            return ""
        return phase_fingerprint_from_text(tasks_path.read_text(encoding="utf-8"))

    def check_plan_dirty(self) -> bool:
        """True if phase structure changed since last confirm."""
        current = self._current_fingerprint()
        stored = self._last_fingerprint
        if not stored:
            return False
        return current != stored

    def confirm_plan(self) -> None:
        """Snap fingerprint on plan confirm."""
        self._last_fingerprint = self._current_fingerprint()
        self._record_change("confirm", "(计划确认)", reason="plan confirmed")
        self._save_state()

    # ---- task operations (wrapped with change log) ----

    def toggle_task(self, line: int, done: bool) -> dict[str, Any]:
        result = toggle_task_line(self.paths, self.project_id, line, done)
        text = result.get("line", line)
        self._record_change("toggle", f"line {text}", reason="manual toggle", line=line)
        self._save_state()
        return result

    def reorder_task(self, line: int, direction: str) -> dict[str, Any]:
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        task_text = ""
        if tasks_path.is_file():
            file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
            if 0 <= line < len(file_lines):
                task_text = file_lines[line].strip()
        result = reorder_task_line(self.paths, self.project_id, line, direction)
        self._record_change("reorder", task_text or f"line {line}", reason=f"move {direction}", line=line)
        self._save_state()
        return result

    def drop_task(self, line: int) -> dict[str, Any]:
        result = drop_task_line(self.paths, self.project_id, line)
        removed = result.get("removed", f"line {line}")
        self._record_change("drop", removed, reason="manual drop", line=line)
        self._save_state()
        return result

    def skip_task(self, line: int) -> dict[str, Any]:
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        task_text = ""
        if tasks_path.is_file():
            file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
            if 0 <= line < len(file_lines):
                task_text = file_lines[line].strip()
        result = skip_task_line(self.paths, self.project_id, line)
        self._record_change("skip", task_text or f"line {line}", reason="manual skip", line=line)
        self._save_state()
        return result

    # ---- message routing ----

    _PLAN_KEYWORDS = [
        "先做", "再做", "提前", "推迟", "暂缓", "跳过", "拆成", "拆分",
        "加一个", "新增", "加个", "添加任务", "调整顺序", "重新排序",
        "把", "移到", "放到", "先别做", "不要做",
    ]

    def classify_message(self, text: str) -> RouteDecision:
        """Determine whether a user message is plan change, execution, or mixed."""
        stripped = text.strip()
        lowered = stripped.lower()

        has_plan = any(kw in lowered for kw in self._PLAN_KEYWORDS)
        has_execution = len(stripped) > 30 or any(
            kw in lowered for kw in ["写", "实现", "修复", "重构", "帮我",
                                       "写代码", "改bug", "测试", "部署",
                                       "继续", "下一", "开始", "run", "运行"]
        )

        if has_plan and not has_execution:
            return "handle"
        if has_plan and has_execution:
            return "split"
        return "forward"

    # ---- helpers ----

    def _current_stats(self):
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        return read_task_stats(tasks_path)

    def next_task_text(self) -> str | None:
        """Return the text of the first undone task, or None."""
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        if not tasks_path.is_file():
            return None
        text = tasks_path.read_text(encoding="utf-8")
        for line in text.splitlines():
            m = __import__("re").match(r"^\s*-\s*\[\s\]\s+(.*)", line)
            if m:
                return m.group(1).strip()
        return None

    # ---- quality checks + auto-fix ----

    def quality_check(self) -> list[str]:
        """Run all quality checks. Returns warnings (auto-fix is applied first)."""
        warnings: list[str] = []
        warnings.extend(self._check_duplicates())
        warnings.extend(self._check_granularity())
        warnings.extend(self._check_empty_phases())
        return warnings

    def auto_fix(self) -> list[str]:
        """Run auto-fix on TASKS.md. Returns list of actions taken.
        Safe: only removes >=95% similar undone duplicate task lines, keeping one.
        """
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        if not tasks_path.is_file():
            return []
        import re
        from difflib import SequenceMatcher

        file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
        actions: list[str] = []

        # ---- duplicate removal (>=95% similar undone tasks) ----
        # Build entries: (line_number, description, is_done)
        entries: list[tuple[int, str, bool]] = []
        for i, line in enumerate(file_lines):
            m = re.match(r"^\s*-\s*\[([ xX])\]\s+(.*)", line)
            if m:
                done = m.group(1).lower() == "x"
                entries.append((i, m.group(2).strip(), done))

        # Find pairs >= 95% similar, both undone → remove the later one
        to_remove: set[int] = set()
        for a in range(len(entries)):
            for b in range(a + 1, len(entries)):
                la, da, done_a = entries[a]
                lb, db, done_b = entries[b]
                if la in to_remove or lb in to_remove:
                    continue
                ratio = SequenceMatcher(None, da, db).ratio()
                if ratio >= 0.95:
                    if done_a and done_b:
                        # Both done: remove the shorter one
                        remove_line = la if len(da) < len(db) else lb
                        keep_line = lb if remove_line == la else la
                    elif done_a:
                        remove_line = lb  # keep done, remove undone duplicate
                        keep_line = la
                    elif done_b:
                        remove_line = la
                        keep_line = lb
                    else:
                        # Both undone: keep the longer one (more detail), remove shorter
                        remove_line = la if len(da) < len(db) else lb
                        keep_line = lb if remove_line == la else la

                    to_remove.add(remove_line)
                    actions.append(
                        f"[自动清理] 删除重复任务行 {remove_line} "
                        f"（与行 {keep_line} {ratio:.0%} 相似）"
                    )
                    self._record_change(
                        "drop",
                        file_lines[remove_line].strip(),
                        reason=f"auto-fix: duplicate of line {keep_line} ({ratio:.0%})",
                        line=remove_line,
                    )

        if to_remove:
            # Remove lines in reverse order (highest index first)
            for line_idx in sorted(to_remove, reverse=True):
                file_lines.pop(line_idx)
            content = "\n".join(file_lines)
            if not content.endswith("\n"):
                content += "\n"
            tasks_path.write_text(content, encoding="utf-8")
            self._save_state()

        return actions

    def _check_duplicates(self) -> list[str]:
        """Detect >=85% similar task descriptions (not auto-fixed, just warnings)."""
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        if not tasks_path.is_file():
            return []
        text = tasks_path.read_text(encoding="utf-8")
        import re
        from difflib import SequenceMatcher

        entries: list[tuple[int, str]] = []
        for i, line in enumerate(text.splitlines()):
            m = re.match(r"^\s*-\s*\[[ x]\]\s+(.*)", line)
            if m:
                entries.append((i, m.group(1).strip()))

        warnings: list[str] = []
        for a in range(len(entries)):
            for b in range(a + 1, len(entries)):
                la, da = entries[a]
                lb, db = entries[b]
                ratio = SequenceMatcher(None, da, db).ratio()
                if 0.92 <= ratio < 0.95:
                    warnings.append(
                        f"[相似] 行 {la} 和 {lb} 疑似重复 ({ratio:.0%})："
                        f"\"{da[:40]}\" ≈ \"{db[:40]}\""
                    )
        return warnings

    def _check_granularity(self) -> list[str]:
        """Flag tasks that are too vague (single short word) or too long (>120 chars)."""
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        if not tasks_path.is_file():
            return []
        text = tasks_path.read_text(encoding="utf-8")
        import re
        warnings: list[str] = []
        for i, line in enumerate(text.splitlines()):
            m = re.match(r"^\s*-\s*\[[ x]\]\s+(.*)", line)
            if m:
                desc = m.group(1).strip()
                if len(desc) < 10:
                    warnings.append(
                        f"[粒度] 行 {i} 任务过短（{len(desc)} 字）：\"{desc}\""
                    )
                elif len(desc) > 120:
                    warnings.append(
                        f"[粒度] 行 {i} 任务过长（{len(desc)} 字），建议拆分"
                    )
        return warnings

    def _check_empty_phases(self) -> list[str]:
        """Flag ## Phase sections with no tasks under them."""
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        if not tasks_path.is_file():
            return []
        text = tasks_path.read_text(encoding="utf-8")
        import re
        lines = text.splitlines()
        warnings: list[str] = []
        current_phase: str | None = None
        phase_line: int = -1
        has_tasks = False

        for i, line in enumerate(lines):
            if line.strip().startswith("## "):
                if current_phase is not None and not has_tasks:
                    warnings.append(
                        f"[空阶段] 行 {phase_line} \"{current_phase}\" 下无任何任务"
                    )
                current_phase = line.strip().lstrip("#").strip()
                phase_line = i
                has_tasks = False
            elif re.match(r"^\s*-\s*\[[ x]\]\s+", line):
                has_tasks = True

        # Check last phase
        if current_phase is not None and not has_tasks:
            warnings.append(
                f"[空阶段] 行 {phase_line} \"{current_phase}\" 下无任何任务"
            )

        return warnings

    # ---- state payload ----

    def build_state(self, session: Session | None = None) -> dict[str, Any]:
        """Build project.plan.state payload."""
        artifacts = read_project_artifacts(self.paths, self.project_id)
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        stats = read_task_stats(tasks_path)

        plan_status = ""
        if session is not None:
            plan_status = session.meta.project_plan_status or "draft"

        needs_confirm = self.check_plan_dirty()

        warnings = self.quality_check()

        return {
            "type": "project.plan.state",
            "project_id": self.project_id,
            "plan_status": plan_status,
            "tasks_markdown": artifacts.get("TASKS.md", ""),
            "map_markdown": artifacts.get("MAP.md", ""),
            "tasks_done": stats.done,
            "tasks_total": stats.total,
            "tasks_open": stats.open_count,
            "tasks_all_done": stats.all_done,
            "needs_confirm": needs_confirm,
            "warnings": warnings,
            "change_log": [
                {
                    "id": c.id,
                    "kind": c.kind,
                    "task_text": c.task_text,
                    "reason": c.reason,
                    "time": c.time,
                    "line": c.line,
                }
                for c in self.pending_changes()
            ],
        }

    # ---- report progress from main agent ----

    def report_progress(self, task_line: int | None, summary: str) -> dict[str, Any]:
        """Main agent reports completing a task."""
        self._record_change(
            "toggle" if task_line is not None else "add",
            summary,
            reason=f"agent progress: {summary[:80]}",
            line=task_line,
        )
        self._save_state()
        return self.build_state()


# ---- singleton registry (one PlanAgent per project) ----

_agents: dict[str, PlanAgent] = {}


def get_plan_agent(paths: AgentPaths, project_id: str) -> PlanAgent:
    pid = normalize_project_id(project_id)
    if pid not in _agents:
        _agents[pid] = PlanAgent(paths=paths, project_id=pid)
    return _agents[pid]


def drop_plan_agent(project_id: str) -> None:
    pid = normalize_project_id(project_id)
    _agents.pop(pid, None)
