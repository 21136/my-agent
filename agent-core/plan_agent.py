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

ChangeKind = Literal["add", "drop", "skip", "reorder", "toggle", "confirm", "external"]

DegradationLevel = Literal["L1", "L2", "L3"]


_LEVEL_LABEL: dict[DegradationLevel, str] = {
    "L1": "全功能",
    "L2": "无推理",
    "L3": "无 Plan",
}


@dataclass
class PlanChange:
    id: str
    kind: ChangeKind
    task_text: str
    reason: str
    time: str
    line: int | None = None


@dataclass
class UndoEntry:
    """A reversible mutation. Stored on a stack (max 50)."""
    description: str  # "已删除「旧任务」"
    reverse_kind: str  # "toggle" | "insert" | "move" | "drop"
    reverse_data: dict  # params for the reverse operation


_PLAN_REASONING_SYSTEM = """你是一个项目管理器。你的任务是理解用户的需求，分析当前的项目任务结构，然后输出精准的修改操作。

## 输出格式
返回 JSON 对象，包含 operations 数组。每个操作对象要有以下字段：
- "kind": "add" | "skip" | "split" | "reorder"
- "phase": 目标 Phase 标题（仅 add 需要）
- "description": 任务描述（add 需要）；拆出的子任务描述列表（split 需要）
- "line": 行号（skip/reorder/split 需要）
- "reason": 简短解释

## 规则
1. 理解用户的真实意图。例如"在每个阶段加测试" = 每个 Phase 都加一条测试相关任务。
2. 如果有完全重复的已有任务，不要新增，跳过它们。
3. 保持任务描述简洁（< 80 字）。
4. 只输出 JSON，不要额外解释。
5. Phase 标题使用项目文档中已有的精确标题。
"""


def _build_reasoning_prompt(tasks_text: str, user_intent: str) -> str:
    """Build a user prompt for PlanAgent LLM reasoning."""
    return f"""## 当前任务列表

{tasks_text}

## 用户需求

{user_intent}

请分析以上内容，输出对此项目计划的修改操作 JSON。"""


_SPLIT_SYSTEM = """你是一个项目管理器。用户要求拆分一条任务为多个更小的子任务。

## 规则
1. 拆成 2-5 个具体可执行的子任务，每个 5-15 分钟可完成
2. 保持子任务描述简洁（< 60 字）
3. 仅输出 JSON 数组，每个元素是一条子任务描述字符串，不要额外解释
4. 格式: ["子任务1描述", "子任务2描述", "子任务3描述"]
"""




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
    _llm: Any = field(default=None, repr=False)
    _undo_stack: list[UndoEntry] = field(default_factory=list)
    _undo_applying: bool = field(default=False, repr=False)
    _degradation_level: DegradationLevel = field(default="L1")
    _last_tasks_snapshot: str = ""  # full TASKS.md text after last mutation
    _stale_task_line: int = -1  # line of current task last seen
    _stale_task_count: int = 0  # consecutive build_state calls with same current
    _suggestions: list[str] = field(default_factory=list)
    _last_progress_time: float = 0.0  # time.time() of last report_progress call

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
        # Update snapshot for external change detection
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        if tasks_path.is_file():
            self._last_tasks_snapshot = tasks_path.read_text(encoding="utf-8")

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

    def undo_last(self) -> UndoEntry | None:
        """Pop and execute the most recent undo entry. Returns None if stack empty."""
        if not self._undo_stack:
            return None
        entry = self._undo_stack.pop()
        self._undo_applying = True
        try:
            if entry.reverse_kind == "toggle":
                self.toggle_task(entry.reverse_data["line"], entry.reverse_data["done"])
            elif entry.reverse_kind == "move":
                self.reorder_task(entry.reverse_data["line"], entry.reverse_data["direction"])
            elif entry.reverse_kind == "insert":
                pos = entry.reverse_data["position"]
                content = entry.reverse_data["content"]
                tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
                fls = tasks_path.read_text(encoding="utf-8").splitlines()
                fls.insert(pos, content)
                txt = "\n".join(fls)
                if not txt.endswith("\n"): txt += "\n"
                tasks_path.write_text(txt, encoding="utf-8")
            elif entry.reverse_kind == "unskip":
                cur = entry.reverse_data["current_line"]
                orig = entry.reverse_data["original_line"]
                steps = cur - orig
                for _ in range(steps):
                    reorder_task_line(self.paths, self.project_id, cur, "up")
                    cur -= 1
            elif entry.reverse_kind == "drop":
                self.drop_task(entry.reverse_data["line"])
            self._save_state()
        except Exception:
            # Rollback undo entry if it failed
            self._undo_stack.append(entry)
            self._undo_applying = False
            return None
        self._undo_applying = False
        return entry

    def push_undo(self, description: str, reverse_kind: str, reverse_data: dict) -> None:
        """Manually push an undo entry (for add_task operations outside _mutate_and_check)."""
        self._undo_stack.append(UndoEntry(
            description=description,
            reverse_kind=reverse_kind,
            reverse_data=reverse_data,
        ))
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)
        self._save_state()

    # ---- task operations (wrapped with change log + auto-fix) ----

    def _mutate_and_check(self, result: dict[str, Any], kind: ChangeKind,
                          task_text: str, reason: str = "", line: int | None = None,
                          undo: UndoEntry | None = None) -> dict[str, Any]:
        """After any mutation, run auto_fix + quality_check. Returns enriched result."""
        self._record_change(kind, task_text, reason=reason, line=line)

        if undo is not None and not self._undo_applying:
            self._undo_stack.append(undo)
            if len(self._undo_stack) > 50:
                self._undo_stack.pop(0)
            result["_undo_desc"] = undo.description

        self._save_state()

        actions = self.auto_fix()
        warnings = self.quality_check()

        result["_auto_fix_actions"] = actions
        result["_warnings"] = warnings
        result["_next_task"] = self.next_task_text()
        return result

    def toggle_task(self, line: int, done: bool) -> dict[str, Any]:
        # Capture pre-state for undo
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        task_text = f"line {line}"
        if tasks_path.is_file():
            fls = tasks_path.read_text(encoding="utf-8").splitlines()
            if 0 <= line < len(fls):
                import re
                m = re.match(r"^\s*-\s*\[[ x]\]\s+(.*)", fls[line])
                if m: task_text = m.group(1).strip()

        result = toggle_task_line(self.paths, self.project_id, line, done)
        label = "已勾选" if done else "已取消勾选"
        undo = UndoEntry(
            description=f"{label}「{task_text[:30]}」",
            reverse_kind="toggle",
            reverse_data={"line": line, "done": not done},
        )
        return self._mutate_and_check(result, "toggle", f"line {line}",
                                       reason="toggle", line=line, undo=undo)

    def reorder_task(self, line: int, direction: str) -> dict[str, Any]:
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        task_text = f"line {line}"
        if tasks_path.is_file():
            file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
            if 0 <= line < len(file_lines):
                task_text = file_lines[line].strip()
        result = reorder_task_line(self.paths, self.project_id, line, direction)
        label = "已上移" if direction == "up" else "已下移"
        reverse_dir = "down" if direction == "up" else "up"
        # After swap: if moved up (line→line-1), undo target is at line-1 going down
        #             if moved down (line→line+1), undo target is at line+1 going up
        new_line = line - 1 if direction == "up" else line + 1
        undo = UndoEntry(
            description=f"{label}「{task_text[:30]}」",
            reverse_kind="move",
            reverse_data={"line": new_line, "direction": reverse_dir},
        )
        return self._mutate_and_check(result, "reorder", task_text or f"line {line}",
                                       reason=f"move {direction}", line=line, undo=undo)

    def drop_task(self, line: int) -> dict[str, Any]:
        # Capture pre-state content BEFORE deletion
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        original_content = f"line {line}"
        if tasks_path.is_file():
            fls = tasks_path.read_text(encoding="utf-8").splitlines()
            if 0 <= line < len(fls):
                original_content = fls[line]

        result = drop_task_line(self.paths, self.project_id, line)
        removed = result.get("removed", original_content)
        undo = UndoEntry(
            description=f"已删除「{removed.strip()[:30]}」",
            reverse_kind="insert",
            reverse_data={"position": line, "content": original_content},
        )
        return self._mutate_and_check(result, "drop", removed, reason="drop",
                                       line=line, undo=undo)

    def skip_task(self, line: int) -> dict[str, Any]:
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        task_text = f"line {line}"
        if tasks_path.is_file():
            file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
            if 0 <= line < len(file_lines):
                task_text = file_lines[line].strip()
        result = skip_task_line(self.paths, self.project_id, line)
        new_pos = result.get("new_position", line)
        # Skip moved task from `line` to `new_pos` (end of phase).
        # Undo: move it back up from new_pos to original `line`
        undo = UndoEntry(
            description=f"已暂缓「{task_text[:30]}」",
            reverse_kind="unskip",
            reverse_data={"current_line": new_pos, "original_line": line},
        )
        return self._mutate_and_check(result, "skip", task_text or f"line {line}",
                                       reason="skip", line=line, undo=undo)

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
        """Flag tasks that are too vague or too long. Also check phase overload."""
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        if not tasks_path.is_file():
            return []
        text = tasks_path.read_text(encoding="utf-8")
        import re
        warnings: list[str] = []

        # Per-task granularity
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

        # Phase overload: warn if any phase has >12 tasks
        current_phase = ""
        phase_task_count = 0
        for line in text.splitlines():
            if line.strip().startswith("## "):
                if phase_task_count > 12:
                    warnings.append(
                        f"[阶段过长] {current_phase} 有 {phase_task_count} 个任务，建议拆分阶段"
                    )
                current_phase = line.strip().lstrip("#").strip()
                phase_task_count = 0
            elif re.match(r"^\s*-\s*\[[ x]\]\s+", line):
                phase_task_count += 1
        if phase_task_count > 12:
            warnings.append(
                f"[阶段过长] {current_phase} 有 {phase_task_count} 个任务，建议拆分阶段"
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

    # ---- LLM reasoning ----

    def _fallback_add_task(self, text: str, extra: str = "") -> str:
        """Fallback: add text as a single task to the first phase with undone tasks."""
        from project_mode import add_task_to_tasks_md

        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        tasks_text = tasks_path.read_text(encoding="utf-8") if tasks_path.is_file() else ""

        # Pick first phase that has undone tasks, or first phase overall
        phase_title = "Phase 1"
        current_phase = ""
        for line in tasks_text.splitlines():
            if line.strip().startswith("## "):
                current_phase = line.strip().lstrip("#").strip()
                if not phase_title or phase_title == "Phase 1":
                    phase_title = current_phase
            if "- [ ]" in line and current_phase:
                phase_title = current_phase  # use the phase that still has work
                break

        try:
            result = add_task_to_tasks_md(self.paths, self.project_id, phase_title, text)
            new_line = result.get("line", -1)
            self._record_change("add", text, reason="quick-add fallback")
            self.push_undo(
                description=f"已添加「{text[:30]}」",
                reverse_kind="drop",
                reverse_data={"line": new_line},
            )
            self._save_state()
            prefix = f"{extra}。已添加到 {phase_title}：" if extra else f"已添加到 {phase_title}："
            return prefix + text[:60]
        except Exception as exc:
            return f"添加失败：{exc}"

    def _ensure_llm(self):
        if self._llm is not None:
            return self._llm
        from llm_client import LLMClient, DEFAULT_MODEL
        import os
        model = os.environ.get("PLAN_AGENT_MODEL", DEFAULT_MODEL)
        try:
            self._llm = LLMClient()
        except Exception:
            self._degradation_level = "L2"
            raise
        self._llm._plan_model = model
        self._degradation_level = "L1"
        return self._llm

    def pulse(self) -> DegradationLevel:
        """Return current degradation level. Call this to get the status indicator."""
        if self._degradation_level != "L1":
            # Already degraded; try recovery
            try:
                self._ensure_llm()
            except Exception:
                pass
        return self._degradation_level

    def split_task(self, line: int) -> str:
        """Use LLM to split a single task into 2-5 subtasks.
        Toggles the original task as done and inserts subtasks as undone lines below it."""
        import json as json_mod
        import re

        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        if not tasks_path.is_file():
            return "TASKS.md 不存在"

        file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
        if line < 0 or line >= len(file_lines):
            return f"行 {line} 超出范围"

        m = re.match(r"^\s*-\s*\[([ xX])\]\s+(.*)", file_lines[line])
        if not m:
            return f"行 {line} 不是任务行"
        task_description = m.group(2).strip()

        # Gather surrounding context (± 10 lines of task context)
        ctx_start = max(0, line - 10)
        ctx_end = min(len(file_lines), line + 10)
        context = "\n".join(file_lines[ctx_start:ctx_end])

        try:
            llm = self._ensure_llm()
        except Exception as exc:
            return self._fallback_split(line, task_description, f"LLM 不可用（{exc}）")

        try:
            response = llm.chat(
                [
                    {"role": "system", "content": _SPLIT_SYSTEM},
                    {"role": "user", "content": f"任务上下文：\n{context}\n\n请拆分任务：{task_description}"},
                ],
                model=getattr(llm, "_plan_model", "deepseek-v4-flash"),
                temperature=0.0,
            )
        except Exception as exc:
            self._degradation_level = "L2"
            return self._fallback_split(line, task_description, f"LLM 调用失败（{exc}）")

        raw = response.content.strip()
        # Parse JSON array from response
        subtasks: list[str] = []
        try:
            if "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()
                if raw.startswith("json"):
                    raw = raw[4:].strip()
            parsed = json_mod.loads(raw)
            if isinstance(parsed, list):
                subtasks = [str(s).strip() for s in parsed if str(s).strip()]
        except (json_mod.JSONDecodeError, IndexError):
            pass

        if not subtasks:
            return self._fallback_split(line, task_description, "LLM 返回格式异常")

        # Toggle original as done
        self.toggle_task(line, True)

        # Insert subtasks as new task lines below the original
        from project_mode import add_task_to_tasks_md
        # Find the phase this task belongs to
        phase_title = "Phase 1"
        for i in range(line, -1, -1):
            if file_lines[i].strip().startswith("## "):
                phase_title = file_lines[i].strip().lstrip("#").strip()
                break

        for i, sub in enumerate(subtasks):
            add_task_to_tasks_md(self.paths, self.project_id, phase_title, sub)
            self._record_change("add", sub, reason=f"split of line {line}: {task_description[:30]}")
            self.push_undo(
                description=f"已拆分「{sub[:20]}」",
                reverse_kind="drop",
                reverse_data={"line": line + 1 + i},  # approx position
            )

        self._save_state()
        self.auto_fix()

        return f"已将「{task_description[:30]}」拆分为 {len(subtasks)} 项：" + ", ".join(s[:20] for s in subtasks)

    def _fallback_split(self, line: int, task_description: str, extra: str) -> str:
        """Fallback: mark the task as done, add a single continuation subtask."""
        self.toggle_task(line, True)
        from project_mode import add_task_to_tasks_md
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
        phase_title = "Phase 1"
        for i in range(line, -1, -1):
            if file_lines[i].strip().startswith("## "):
                phase_title = file_lines[i].strip().lstrip("#").strip()
                break
        desc = f"继续：{task_description[:40]}"
        add_task_to_tasks_md(self.paths, self.project_id, phase_title, desc)
        self._save_state()
        return f"{extra}。已标记完成并添加续做任务：「{desc}」"

    def reason_about_intent(self, text: str) -> str:
        """Use LLM to reason about user intent and produce structured plan changes.
        Returns a human-readable summary of what was done."""
        try:
            llm = self._ensure_llm()
        except Exception as exc:
            return self._fallback_add_task(text, extra=f"LLM 不可用（{exc}）")

        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        tasks_text = ""
        if tasks_path.is_file():
            tasks_text = tasks_path.read_text(encoding="utf-8")

        user_prompt = _build_reasoning_prompt(tasks_text, text)

        try:
            response = llm.chat(
                [
                    {"role": "system", "content": _PLAN_REASONING_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                model=getattr(llm, "_plan_model", "deepseek-v4-flash"),
                temperature=0.0,
            )
        except Exception as exc:
            self._degradation_level = "L2"
            return self._fallback_add_task(text, extra=f"LLM 调用失败（{exc}）")

        raw = response.content.strip()
        # Extract JSON from response
        try:
            # Handle markdown code blocks
            if "```" in raw:
                lines = raw.splitlines()
                json_lines = []
                in_block = False
                for line in lines:
                    if line.strip().startswith("```"):
                        if in_block:
                            break
                        in_block = True
                        continue
                    if in_block:
                        json_lines.append(line)
                raw = "\n".join(json_lines)

            result = json.loads(raw)
            operations = result.get("operations", []) if isinstance(result, dict) else []
        except (json.JSONDecodeError, AttributeError):
            return self._fallback_add_task(text)

        if not operations:
            return self._fallback_add_task(text)

        # Apply operations
        applied: list[str] = []
        for op in operations:
            kind = op.get("kind", "")
            try:
                if kind == "add":
                    phase = op.get("phase", "")
                    desc = op.get("description", "")
                    if not desc:
                        continue
                    from project_mode import add_task_to_tasks_md
                    # Find matching phase title
                    phase_title = ""
                    for line in tasks_text.splitlines():
                        if line.strip().startswith("## ") and phase.lower() in line.lower():
                            phase_title = line.strip().lstrip("#").strip()
                            break
                    if not phase_title:
                        phase_title = "Phase 1"
                    result = add_task_to_tasks_md(self.paths, self.project_id, phase_title, desc)
                    new_line = result.get("line", -1)
                    self._record_change("add", desc, reason=f"LLM: {op.get('reason', text[:40])}")
                    self.push_undo(
                        description=f"已添加「{desc[:30]}」",
                        reverse_kind="drop",
                        reverse_data={"line": new_line},
                    )
                    applied.append(f"+ {phase_title}: {desc}")

                elif kind == "skip":
                    line = op.get("line", -1)
                    if line >= 0:
                        self.skip_task(line)
                        applied.append(f"~ 行 {line} 已暂缓")

                elif kind == "split":
                    line = op.get("line", -1)
                    subtasks = op.get("description", [])
                    if isinstance(subtasks, list) and line >= 0:
                        # Toggle original as done
                        self.toggle_task(line, True)
                        for sub in subtasks:
                            self._record_change("add", str(sub), reason=f"split of line {line}")
                        applied.append(f"⇅ 行 {line} 拆分为 {len(subtasks)} 项")

            except Exception:
                continue  # skip failed ops, apply what we can

        self._save_state()
        actions = self.auto_fix()
        for action in actions:
            applied.append(action)

        if not applied:
            return self._fallback_add_task(text, extra="所有 LLM 操作执行失败")

        return "\n".join(applied)

    # ---- state payload ----

    def build_state(self, session: Session | None = None) -> dict[str, Any]:
        """Build project.plan.state payload. Runs auto_fix + quality_check every time."""
        artifacts = read_project_artifacts(self.paths, self.project_id)
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        stats = read_task_stats(tasks_path)

        plan_status = ""
        if session is not None:
            plan_status = session.meta.project_plan_status or "draft"

        needs_confirm = self.check_plan_dirty()
        pending = self.pending_changes()

        # Changes level: phase (manual) vs task (30s auto) vs null
        if needs_confirm:
            changes_level = "phase"
        elif pending:
            changes_level = "task"
        else:
            changes_level = None

        # Detect external changes (git, editor, etc modifying TASKS.md directly)
        current_tasks = artifacts.get("TASKS.md", "")
        external_changes = False
        if self._last_tasks_snapshot and current_tasks != self._last_tasks_snapshot:
            external_changes = True
            self._record_change("external", "(外部修改)", reason="TASKS.md changed outside PlanAgent")
        # Always update snapshot after checking
        self._last_tasks_snapshot = current_tasks

        # Stale task detection: same current task across multiple build_state calls
        self._suggestions = []
        current_line = -1
        for line_n, line_t in enumerate(current_tasks.splitlines()):
            if line_t.strip().startswith("- [ ]"):
                current_line = line_n
                break
        if current_line >= 0 and current_line == self._stale_task_line:
            self._stale_task_count += 1
        else:
            self._stale_task_line = current_line
            self._stale_task_count = 1
        if self._stale_task_count >= 5:
            task_text = current_tasks.splitlines()[current_line].strip()
            self._suggestions.append(
                f"[耗时提醒] 任务「{task_text[:40]}」已保持 {self._stale_task_count} 轮未完成，是否拆分为更小的子任务？"
            )

        # Always run checks on every state request
        auto_fix_actions = self.auto_fix()
        warnings = self.quality_check()

        return {
            "type": "project.plan.state",
            "project_id": self.project_id,
            "plan_status": plan_status,
            "tasks_markdown": current_tasks,
            "map_markdown": artifacts.get("MAP.md", ""),
            "tasks_done": stats.done,
            "tasks_total": stats.total,
            "tasks_open": stats.open_count,
            "tasks_all_done": stats.all_done,
            "needs_confirm": needs_confirm,
            "changes_level": changes_level,
            "external_changes": external_changes,
            "suggestions": list(self._suggestions),
            "degradation_level": self.pulse(),
            "degradation_label": _LEVEL_LABEL.get(self.pulse(), "未知"),
            "warnings": warnings,
            "auto_fix_actions": auto_fix_actions,
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
        """Main agent reports completing a task. Verify with file output check."""
        import time as _time

        # Verify: check if any files were modified under project dir since last report
        verification_note = ""
        project_root = project_dir(self.paths, self.project_id)
        if self._last_progress_time > 0 and project_root.is_dir():
            found_new = False
            try:
                for f in project_root.rglob("*"):
                    if f.is_file() and f.suffix not in {".md", ".txt"}:
                        try:
                            if f.stat().st_mtime >= self._last_progress_time:
                                found_new = True
                                break
                        except OSError:
                            pass
            except OSError:
                pass
            if not found_new:
                verification_note = (
                    "[验证失败] 未检测到新的源码文件产出。"
                    "如确实完成请忽略，否则请在下一轮补充代码产出后重新标记。"
                )

        self._last_progress_time = _time.time()
        self._record_change(
            "toggle" if task_line is not None else "add",
            summary,
            reason=f"agent progress: {summary[:80]}",
            line=task_line,
        )
        self._save_state()
        state = self.build_state()
        # Append verification note AFTER build_state (which resets _suggestions)
        if verification_note:
            state["warnings"] = list(state.get("warnings", [])) + [verification_note]
            state["suggestions"] = list(state.get("suggestions", [])) + [verification_note]
            self._suggestions.append(verification_note)
        return state


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
