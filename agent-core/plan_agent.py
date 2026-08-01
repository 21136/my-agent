"""Plan Agent: owns the project plan (TASKS.md / MAP.md / PROJECT.md).

Routes user messages, manages task operations, maintains change log,
detects plan_dirty via fingerprint comparison.

Phase 4: infrastructure + routing + task operations + fingerprint.
Phase 5+: LLM-based split/suggest will be added.
"""

from __future__ import annotations

import json
import re
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


_PLAN_SYSTEM = """你是侧栏 **Plan Agent（计划搭档）**，拥有本项目 TASKS.md 的编排权。
用户在侧栏输入的每一句都是对你说的。系统**只执行**你返回的 JSON，不会事后改挂 Phase。

## 输出
只输出 JSON：{"operations":[ ... ]}
字段：kind(add|move|drop|skip|split|reorder)、phase、description、line(0-indexed)、direction(up|down)、reason。

## 意图分流（先分清用户要什么）
- 「加一个 / 新增 …」→ add（不要把整句口令当标题）
- 「提前 / 推后 / 挪到 / 放到」→ move 或 reorder，禁止再 add 一条同名任务
- 「暂缓 / 先不做 / 跳过」→ skip（不是勾完成，也不是删）
- 「删除 / 不要了」→ drop
- 「拆分」→ split
- 「优化 / 整理 / 检查 / 太乱」→ 审顺序与结构；若进度摘要里有 ⚠ 异常信号，必须处理（move/drop/skip/add 测试等），**禁止**空 operations 假装没事
- 不要把「优化下计划」「需要加测试」等**元口令**写成任务标题

## 顺序（看摘要里的 ⚠，再对照全文 [x]/[ ]）
- **夹心**：靠前 Phase 仍有 [ ]，后面 Phase 已大量 [x] → 通常 move 到当前前沿或独立基建 Phase
- **跳段**：靠前 Phase 仍大量 [ ]，后面 Phase 却已开始 [x] → 调整顺序或暂缓偷跑项
- **下一项被拽歪**：全局第一个 [ ] 不是当前应做的工作 → move/reorder 纠正
- 中途发现的前置（如对接数据库）：按**执行顺序**挂当前前沿或新基建段，勿只因语义像脚手架就塞回已收尾的早期 Phase

## 内容与结构
- 完全重复 → 不必再 add（系统也会自动清极高相似重复）
- 并行模块脚手架（医师 Entity vs 医保 Entity）**不是**重复，勿合并
- 过粗可 split；无意义碎片可 drop/merge（用 drop+add）
- 空 Phase：补任务或去掉空段（drop 不适用标题时，可 add 占位或在 reason 说明）
- 模块做完缺「本阶段测试/联调」→ 可 add 测试任务
- 挂哪一段、是否新建 Phase，由你决定；无改动且摘要无 ⚠ 时才返回 {"operations":[]}
"""


_PLAN_META_COMMAND_RE = re.compile(
    r"(优化|整理|清理|检查|查重|太乱|帮我看|看看计划|质检|auto[\s_-]?fix)",
    re.IGNORECASE,
)
_PLAN_MUTATE_RE = re.compile(
    r"(提前|推后|暂缓|跳过|拆分|重排|删除|合并|挪到|放到|移到|先做|再做|顺序)",
    re.IGNORECASE,
)
_PLAN_ADD_PREFIX_RE = re.compile(
    r"^(加(一)?个|新增|添加(一)?(个|条)?任务|记一?条)[:：\s]*",
    re.IGNORECASE,
)


def looks_like_plan_meta_command(text: str) -> bool:
    """True when input asks Plan Agent to review/fix the plan (not a new task title)."""
    t = (text or "").strip()
    if not t or len(t) > 80:
        return False
    if re.match(r"^T-\d+", t, re.IGNORECASE):
        return False
    if re.match(r"^-\s*\[[ xX]?\]", t):
        return False
    return bool(_PLAN_META_COMMAND_RE.search(t))


def looks_like_new_task_utterance(text: str) -> bool:
    """Heuristic: user is naming a work item to add (safe L2 fallback)."""
    t = (text or "").strip()
    if not t or looks_like_plan_meta_command(t):
        return False
    if _PLAN_MUTATE_RE.search(t):
        return False
    if re.fullmatch(r"(你好|在吗|嗨|hello|hi)[.!！？\s]*", t, re.IGNORECASE):
        return False
    if _PLAN_ADD_PREFIX_RE.match(t):
        return True
    return len(t) >= 4


def strip_add_prefix(text: str) -> str:
    t = (text or "").strip()
    return _PLAN_ADD_PREFIX_RE.sub("", t).strip() or t


def _plan_progress_brief(tasks_text: str) -> str:
    """Compact progress + explicit anomaly signals for Plan LLM."""
    from project_mode import (
        active_phase_title_from_lines,
        iter_phase_headers,
        phase_open_and_done_counts,
    )

    lines = tasks_text.splitlines()
    headers = iter_phase_headers(lines)
    if not headers:
        return "（尚无 Phase）"

    rows: list[str] = []
    phase_stats: list[tuple[str, int, int]] = []
    for _, title in headers:
        open_n, done_n = phase_open_and_done_counts(lines, title)
        phase_stats.append((title, open_n, done_n))
        if open_n == 0 and done_n > 0:
            status = "已完成"
        elif open_n > 0 and done_n == 0:
            status = "未开始"
        elif open_n > 0:
            status = "进行中"
        else:
            status = "空"
        rows.append(f"- {title}: {done_n} done / {open_n} open · {status}")

    active = active_phase_title_from_lines(lines)
    next_open = None
    next_line = None
    for i, line in enumerate(lines):
        m = re.match(r"^\s*-\s*\[\s\]\s+(.*)", line)
        if m:
            next_open = m.group(1).strip()
            next_line = i
            break
    rows.append(f"- 当前前沿 Phase: {active or '（无未完成）'}")
    rows.append(
        f"- 全局下一项: {f'行{next_line}|{next_open}' if next_open is not None else '（无）'}"
    )

    alerts: list[str] = []
    for i, (title, open_n, done_n) in enumerate(phase_stats):
        later_done = any(d > 0 for _, _, d in phase_stats[i + 1 :])
        if not later_done or open_n <= 0:
            continue
        if done_n > 0:
            alerts.append(
                f"⚠ 夹心：「{title}」仍有 {open_n} 条未完成，但后续 Phase 已有完成项"
                "——优化时应 move（或说明为何不挪），禁止空操作装没事"
            )
        elif open_n >= 2:
            alerts.append(
                f"⚠ 跳段：「{title}」仍有 {open_n} 条未开始/未完成，"
                "但后续 Phase 已开始勾选——检查是否偷跑"
            )
    # Empty phases
    for title, open_n, done_n in phase_stats:
        if open_n == 0 and done_n == 0:
            alerts.append(f"⚠ 空 Phase：「{title}」下没有任务")
    # Next item dragged back into mostly-done early phase
    if next_open and active:
        for title, open_n, done_n in phase_stats:
            if title != active:
                continue
            if done_n >= 2 and open_n >= 1:
                later_done = False
                saw = False
                for t, _o, d in phase_stats:
                    if t == title:
                        saw = True
                        continue
                    if saw and d > 0:
                        later_done = True
                        break
                if later_done:
                    alerts.append(
                        f"⚠ 下一项被拽回：「{next_open[:40]}」落在已大部分完成的「{title}」，"
                        "而后面 Phase 已推进"
                    )
            break

    if alerts:
        # de-dupe while preserving order
        seen: set[str] = set()
        uniq: list[str] = []
        for a in alerts:
            if a in seen:
                continue
            seen.add(a)
            uniq.append(a)
        rows.append("- 异常信号:")
        rows.extend(f"  {a}" for a in uniq)
    else:
        rows.append("- 异常信号: （无）")
    return "\n".join(rows)


def _format_tasks_with_line_numbers(tasks_text: str) -> str:
    lines = tasks_text.splitlines()
    return "\n".join(f"{i}|{line}" for i, line in enumerate(lines))


def _build_plan_prompt(tasks_text: str, user_intent: str) -> str:
    """Context for Plan LLM — progress + numbered TASKS; no routing hints."""
    brief = _plan_progress_brief(tasks_text)
    numbered = _format_tasks_with_line_numbers(tasks_text)
    return f"""## 进度摘要（先看异常信号 ⚠）

{brief}

## TASKS.md（行号|内容；[x]=完成 [ ]=未完成）

{numbered}

## 用户说

{user_intent}
"""


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
    _suggestions: list[dict[str, Any]] = field(default_factory=list)
    _last_suggestions: dict[str, dict[str, Any]] = field(default_factory=dict)
    _ignored_suggestion_ids: set[str] = field(default_factory=set)
    _last_progress_time: float = 0.0  # time.time() of last report_progress call
    _last_partner_notices: list[str] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._load_state()

    def set_partner_notices(self, summary: str | list[str]) -> None:
        """Sidebar-visible Plan Agent reply (V7 — not main-chat bubbles)."""
        if isinstance(summary, list):
            lines = [str(x).strip() for x in summary if str(x).strip()]
        else:
            lines = [ln.strip() for ln in str(summary).splitlines() if ln.strip()]
        self._last_partner_notices = lines[:12]

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
            ignored = data.get("ignored_suggestion_ids", [])
            if isinstance(ignored, list):
                self._ignored_suggestion_ids = {str(x) for x in ignored if x}
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
            "ignored_suggestion_ids": sorted(self._ignored_suggestion_ids)[-200:],
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

    @staticmethod
    def _suggestion(
        *,
        kind: str,
        title: str,
        body: str,
        key: str,
        risk: str = "suggest",
        action: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "id": f"sug-{kind}-{key}",
            "kind": kind,
            "title": title,
            "body": body,
            "risk": risk,
            "action": action,
            "payload": payload or {},
        }

    def quality_suggestions(self) -> list[dict[str, Any]]:
        """Structured actionable suggestions (Phase 22 / §15.10 V3).

        Only emit cards the user can **采纳** (or we would waste a trip for Ignore-only nags).
        Near-duplicate fuzzy matching was removed (parallel scaffolds false-positive).
        Exact dups still go through auto_fix.
        """
        items: list[dict[str, Any]] = []
        items.extend(self._suggest_granularity())
        # empty_phase / phase_long without action = Ignore-only noise — omit
        return [
            s
            for s in items
            if s.get("action") and s["id"] not in self._ignored_suggestion_ids
        ]

    def quality_check(self) -> list[str]:
        """Legacy string warnings derived from structured suggestions."""
        return [str(s.get("body", "")) for s in self.quality_suggestions()]

    def ignore_suggestion(self, suggestion_id: str) -> None:
        sid = str(suggestion_id or "").strip()
        if not sid:
            raise ProjectModeError("ignore_suggestion requires suggestion_id")
        self._ignored_suggestion_ids.add(sid)
        self._save_state()

    def accept_suggestion(self, suggestion_id: str) -> dict[str, Any]:
        """Apply a previously emitted suggestion via its action/payload."""
        sid = str(suggestion_id or "").strip()
        sug = self._last_suggestions.get(sid)
        if sug is None:
            # UI may hold cards across sidecar restart; rebuild actionable catalog.
            rebuilt = {s["id"]: s for s in self.quality_suggestions() if s.get("id")}
            self._last_suggestions = rebuilt
            sug = rebuilt.get(sid)
        if sug is None:
            raise ProjectModeError(f"unknown or stale suggestion: {sid}")
        action = sug.get("action")
        payload = sug.get("payload") if isinstance(sug.get("payload"), dict) else {}
        if not action:
            raise ProjectModeError(f"suggestion is not actionable: {sid}")

        if action == "drop_task":
            line = payload.get("line")
            if not isinstance(line, int) or line < 0:
                raise ProjectModeError("drop_task suggestion missing line")
            result = self.drop_task(line)
            self._ignored_suggestion_ids.add(sid)
            self._save_state()
            return result

        if action == "split_task":
            line = payload.get("line")
            if not isinstance(line, int) or line < 0:
                raise ProjectModeError("split_task suggestion missing line")
            summary = self.split_task(line)
            self._ignored_suggestion_ids.add(sid)
            self._save_state()
            return {"ok": True, "summary": summary, "_next_task": self.next_task_text()}

        if action == "skip_task":
            line = payload.get("line")
            if not isinstance(line, int) or line < 0:
                raise ProjectModeError("skip_task suggestion missing line")
            result = self.skip_task(line)
            self._ignored_suggestion_ids.add(sid)
            self._save_state()
            return result

        if action == "confirm_changes":
            self.confirm_plan()
            self._ignored_suggestion_ids.add(sid)
            self._save_state()
            return {"ok": True, "_next_task": self.next_task_text()}

        raise ProjectModeError(f"unsupported suggestion action: {action}")

    def auto_fix(self) -> list[str]:
        """Run auto-fix on TASKS.md. Returns list of actions taken.
        Safe: only removes >=95% similar duplicate task lines, keeping one.
        """
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        if not tasks_path.is_file():
            return []
        import re
        from difflib import SequenceMatcher

        file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
        actions: list[str] = []

        # ---- duplicate removal (>=95% similar) ----
        entries: list[tuple[int, str, bool]] = []
        for i, line in enumerate(file_lines):
            m = re.match(r"^\s*-\s*\[([ xX])\]\s+(.*)", line)
            if m:
                done = m.group(1).lower() == "x"
                entries.append((i, m.group(2).strip(), done))

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
                        remove_line = la if len(da) < len(db) else lb
                        keep_line = lb if remove_line == la else la
                    elif done_a:
                        remove_line = lb
                        keep_line = la
                    elif done_b:
                        remove_line = la
                        keep_line = lb
                    else:
                        remove_line = la if len(da) < len(db) else lb
                        keep_line = lb if remove_line == la else la

                    to_remove.add(remove_line)
                    removed_text = file_lines[remove_line].strip()
                    m_rm = re.match(r"^\s*-\s*\[[ xX]\]\s+(.*)", removed_text)
                    label = (m_rm.group(1).strip() if m_rm else removed_text)[:40]
                    actions.append(
                        f"[自动清理] 已删除重复任务「{label}」"
                        f"（与行 {keep_line} {ratio:.0%} 相似）"
                    )
                    self._record_change(
                        "drop",
                        removed_text,
                        reason=f"auto-fix: duplicate of line {keep_line} ({ratio:.0%})",
                        line=remove_line,
                    )

        if to_remove:
            for line_idx in sorted(to_remove, reverse=True):
                file_lines.pop(line_idx)
            content = "\n".join(file_lines)
            if not content.endswith("\n"):
                content += "\n"
            tasks_path.write_text(content, encoding="utf-8")
            self._save_state()

        return actions

    def _suggest_granularity(self) -> list[dict[str, Any]]:
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        if not tasks_path.is_file():
            return []
        text = tasks_path.read_text(encoding="utf-8")
        import re
        out: list[dict[str, Any]] = []

        for i, line in enumerate(text.splitlines()):
            m = re.match(r"^\s*-\s*\[[ x]\]\s+(.*)", line)
            if not m:
                continue
            desc = m.group(1).strip()
            if len(desc) < 10:
                out.append(self._suggestion(
                    kind="too_short",
                    title="过短占位，建议删除",
                    body=(
                        f"行 {i}「{desc}」仅 {len(desc)} 字，多半是占位。"
                        f"采纳 = 删除该行；忽略 = 本会话不再提示。"
                    ),
                    key=f"short-{i}",
                    action="drop_task",
                    payload={"line": i},
                ))
            elif len(desc) > 120:
                out.append(self._suggestion(
                    kind="split",
                    title="建议拆分任务",
                    body=(
                        f"行 {i} 过长（{len(desc)} 字）。"
                        f"采纳 = 拆成更小步骤；忽略 = 本会话不再提示。"
                    ),
                    key=f"long-{i}",
                    action="split_task",
                    payload={"line": i},
                ))

        # phase_long without a safe auto action — skip (Ignore-only cards banned)
        return out

    def _suggest_empty_phases(self) -> list[dict[str, Any]]:
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        if not tasks_path.is_file():
            return []
        text = tasks_path.read_text(encoding="utf-8")
        import re
        lines = text.splitlines()
        out: list[dict[str, Any]] = []
        current_phase: str | None = None
        phase_line: int = -1
        has_tasks = False

        def _flush() -> None:
            nonlocal current_phase, phase_line, has_tasks
            if current_phase is not None and not has_tasks:
                out.append(self._suggestion(
                    kind="empty_phase",
                    title="空阶段",
                    body=f"「{current_phase}」（行 {phase_line}）下没有任务。",
                    key=f"empty-{phase_line}",
                ))

        for i, line in enumerate(lines):
            if line.strip().startswith("## "):
                _flush()
                current_phase = line.strip().lstrip("#").strip()
                phase_line = i
                has_tasks = False
            elif re.match(r"^\s*-\s*\[[ x]\]\s+", line):
                has_tasks = True
        _flush()
        return out

    # ---- LLM reasoning ----

    def _fallback_add_task(self, text: str, extra: str = "") -> str:
        """Add text as a single task to the first phase with undone tasks."""
        from project_mode import add_task_to_tasks_md

        desc = strip_add_prefix(text)
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
            result = add_task_to_tasks_md(self.paths, self.project_id, phase_title, desc)
            new_line = result.get("line", -1)
            self._record_change("add", desc, reason="plan-channel add")
            self.push_undo(
                description=f"已添加「{desc[:30]}」",
                reverse_kind="drop",
                reverse_data={"line": new_line},
            )
            self._save_state()
            prefix = f"{extra}。已添加到 {phase_title}：" if extra else f"已添加到 {phase_title}："
            return prefix + desc[:60]
        except Exception as exc:
            return f"添加失败：{exc}"

    def _plan_channel_fallback(self, text: str, extra: str = "") -> str:
        """L2兜底 only — LLM 不可用 / 解析失败时。正常路径应已走 LLM。"""
        if looks_like_plan_meta_command(text):
            return self._handle_meta_plan_command(text)
        if looks_like_new_task_utterance(text):
            return self._fallback_add_task(text, extra=extra)
        msg = (
            "（兜底）LLM 不可用或未返回可执行操作；侧栏反馈只走建议卡与短告知，"
            "不会把原话写进 TASKS。可稍后重试，或说：加任务「…」。"
        )
        if extra:
            return f"{extra}。{msg}"
        return msg

    def _llm_noop_summary(self, text: str, *, prefix: str = "") -> str:
        """LLM returned empty operations — trust it; do not dump as task."""
        label = text.strip()[:24]
        actions = self.auto_fix()
        suggestions = self.quality_suggestions()
        self._last_suggestions = {
            str(s["id"]): s for s in suggestions if isinstance(s, dict) and s.get("id")
        }
        self._save_state()
        lines: list[str] = list(actions)
        if prefix:
            lines.append(prefix)
        lines.append(f"计划搭档已处理：未改 TASKS。未把「{label}」写成任务。")
        if suggestions:
            lines.append(f"另有 {len(suggestions)} 条建议卡可点「采纳」。")
        return "\n".join(lines)

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

    def _handle_meta_plan_command(self, text: str) -> str:
        """Local-only兜底 when LLM unavailable (auto_fix + suggestion cards)."""
        actions = self.auto_fix()
        suggestions = self.quality_suggestions()
        self._last_suggestions = {
            str(s["id"]): s for s in suggestions if isinstance(s, dict) and s.get("id")
        }
        self._save_state()
        label = text.strip()[:24]
        lines: list[str] = list(actions)
        if suggestions:
            lines.append(
                f"计划搭档已检查：{len(suggestions)} 条可执行建议（点「采纳」才会改 TASKS；"
                f"「忽略」= 本会话不再提示）。未把「{label}」写成任务。"
            )
        else:
            lines.append(
                f"已检查计划：没有需要你拍板的改动（完全重复会已自动清）。"
                f"未把「{label}」写成任务。"
            )
        return "\n".join(lines)

    @staticmethod
    def _parse_operations_json(raw: str) -> tuple[list[dict[str, Any]], bool]:
        """Return (operations, parsed_ok). parsed_ok True even when operations is empty."""
        text = (raw or "").strip()
        if not text:
            return [], False
        if "```" in text:
            lines = text.splitlines()
            json_lines: list[str] = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```"):
                    if in_block:
                        break
                    in_block = True
                    continue
                if in_block:
                    json_lines.append(line)
            text = "\n".join(json_lines)
        try:
            result = json.loads(text)
        except (json.JSONDecodeError, AttributeError):
            return [], False
        if isinstance(result, dict):
            ops = result.get("operations", [])
            return (ops if isinstance(ops, list) else []), True
        if isinstance(result, list):
            return result, True
        return [], False

    def _apply_plan_operations(self, operations: list[Any], *, reason_prefix: str) -> list[str]:
        """Apply LLM plan ops. Process move/drop from high line numbers first."""
        from project_mode import add_task_to_tasks_md, move_task_to_phase

        applied: list[str] = []
        ops = [op for op in operations if isinstance(op, dict)]

        def _line_key(op: dict[str, Any]) -> int:
            line = op.get("line", -1)
            return line if isinstance(line, int) else -1

        # Higher lines first so earlier indices stay stable within one batch.
        ordered = sorted(
            ops,
            key=lambda op: (
                0 if op.get("kind") in {"move", "drop"} else 1,
                -_line_key(op),
            ),
        )

        for op in ordered:
            kind = op.get("kind", "")
            try:
                if kind == "add":
                    phase = op.get("phase", "")
                    desc = op.get("description", "")
                    if not desc:
                        continue
                    phase_title = str(phase).strip() if phase else ""
                    if not phase_title:
                        continue
                    result = add_task_to_tasks_md(self.paths, self.project_id, phase_title, desc)
                    new_line = result.get("line", -1)
                    landed = result.get("phase") or phase_title
                    self._record_change(
                        "add",
                        desc,
                        reason=f"{reason_prefix}: {op.get('reason', '')}"[:120],
                    )
                    self.push_undo(
                        description=f"已添加「{desc[:30]}」",
                        reverse_kind="drop",
                        reverse_data={"line": new_line},
                    )
                    applied.append(f"+ {landed}: {desc}")

                elif kind in {"move", "rephase"}:
                    line = op.get("line", -1)
                    phase = str(op.get("phase", "") or "").strip()
                    if not isinstance(line, int) or line < 0 or not phase:
                        continue
                    result = move_task_to_phase(self.paths, self.project_id, line, phase)
                    desc = str(result.get("description") or "")
                    landed = str(result.get("phase") or phase)
                    self._record_change(
                        "reorder",
                        desc,
                        reason=f"{reason_prefix}: {op.get('reason', 'move')}"[:120],
                        line=result.get("line"),
                    )
                    applied.append(f"→ 移到 {landed}: {desc[:50]}")

                elif kind == "drop":
                    line = op.get("line", -1)
                    if isinstance(line, int) and line >= 0:
                        self.drop_task(line)
                        applied.append(f"× 已删除行 {line}")

                elif kind == "skip":
                    line = op.get("line", -1)
                    if isinstance(line, int) and line >= 0:
                        self.skip_task(line)
                        applied.append(f"~ 行 {line} 已暂缓")

                elif kind == "split":
                    line = op.get("line", -1)
                    subtasks = op.get("description", [])
                    if isinstance(subtasks, list) and isinstance(line, int) and line >= 0:
                        self.toggle_task(line, True)
                        for sub in subtasks:
                            self._record_change("add", str(sub), reason=f"split of line {line}")
                        applied.append(f"⇅ 行 {line} 拆分为 {len(subtasks)} 项")

                elif kind == "reorder":
                    line = op.get("line", -1)
                    direction = str(op.get("direction") or op.get("description") or "up")
                    if isinstance(line, int) and line >= 0 and direction in {"up", "down"}:
                        self.reorder_task(line, direction)
                        applied.append(f"↕ 行 {line} {direction}")

            except Exception:
                continue

        return applied

    def reason_about_intent(self, text: str) -> str:
        """LLM-first plan channel. Local heuristics only when LLM/parse fails (兜底)."""
        try:
            llm = self._ensure_llm()
        except Exception as exc:
            out = self._plan_channel_fallback(text, extra=f"LLM 不可用（{exc}）")
            self.set_partner_notices(out)
            return out

        # Exact-dup cleanup is a silent safety net, not a substitute for LLM judgment.
        pre_actions = self.auto_fix()

        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        tasks_text = tasks_path.read_text(encoding="utf-8") if tasks_path.is_file() else ""

        try:
            response = llm.chat(
                [
                    {"role": "system", "content": _PLAN_SYSTEM},
                    {"role": "user", "content": _build_plan_prompt(tasks_text, text)},
                ],
                model=getattr(llm, "_plan_model", "deepseek-v4-flash"),
                temperature=0.0,
            )
        except Exception as exc:
            self._degradation_level = "L2"
            out = self._plan_channel_fallback(text, extra=f"LLM 调用失败（{exc}）")
            self.set_partner_notices(out)
            return out

        raw = response.content or ""
        operations, parsed_ok = self._parse_operations_json(raw)
        if not parsed_ok:
            out = self._plan_channel_fallback(text, extra="LLM 返回无法解析")
            self.set_partner_notices(out)
            return out
        if not operations:
            prefix = "\n".join(pre_actions) if pre_actions else ""
            out = self._llm_noop_summary(text, prefix=prefix)
            self.set_partner_notices(out)
            return out

        applied = list(pre_actions)
        applied.extend(self._apply_plan_operations(operations, reason_prefix="LLM"))
        self._save_state()
        for action in self.auto_fix():
            applied.append(action)

        if not applied:
            out = self._llm_noop_summary(text)
            self.set_partner_notices(out)
            return out

        out = "\n".join(applied)
        self.set_partner_notices(out)
        return out

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

        # Always auto_fix first so suggestion line numbers match post-fix file
        auto_fix_actions = self.auto_fix()
        if auto_fix_actions and tasks_path.is_file():
            current_tasks = tasks_path.read_text(encoding="utf-8")
            self._last_tasks_snapshot = current_tasks
            artifacts = read_project_artifacts(self.paths, self.project_id)
            stats = read_task_stats(tasks_path)

        # Stale task detection + next step (after auto_fix)
        self._suggestions = []
        import re as _re
        current_line = -1
        next_task_text: str | None = None
        for line_n, line_t in enumerate(current_tasks.splitlines()):
            if line_t.strip().startswith("- [ ]"):
                current_line = line_n
                m = _re.match(r"^\s*-\s*\[\s\]\s+(.*)", line_t)
                next_task_text = m.group(1).strip() if m else line_t.strip()
                break
        if current_line >= 0 and current_line == self._stale_task_line:
            self._stale_task_count += 1
        else:
            self._stale_task_line = current_line
            self._stale_task_count = 1
        if self._stale_task_count >= 5 and current_line >= 0:
            stale = self._suggestion(
                kind="stale",
                title="耗时提醒",
                body=(
                    f"任务「{(next_task_text or '')[:40]}」已保持 "
                    f"{self._stale_task_count} 轮未完成，是否拆分为更小的子任务？"
                ),
                key=f"stale-{current_line}",
                action="split_task",
                payload={"line": current_line},
            )
            if stale["id"] not in self._ignored_suggestion_ids:
                self._suggestions.append(stale)

        self._suggestions.extend(self.quality_suggestions())
        self._last_suggestions = {s["id"]: s for s in self._suggestions}

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
            "next_task": next_task_text,
            "next_task_line": current_line if current_line >= 0 else None,
            "degradation_level": self.pulse(),
            "degradation_label": _LEVEL_LABEL.get(self.pulse(), "未知"),
            "warnings": [],  # actionable items live in suggestions (Phase 22)
            "auto_fix_actions": auto_fix_actions,
            "partner_notices": list(self._last_partner_notices),
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
        from project_mode import resolve_progress_task_line

        resolved_line, resolve_note = resolve_progress_task_line(
            self.paths,
            self.project_id,
            task_line=task_line if isinstance(task_line, int) else None,
            summary=summary,
        )
        if isinstance(resolved_line, int) and resolved_line >= 0:
            try:
                self.toggle_task(resolved_line, True)
            except Exception:
                # Fall through to change log so UI still sees the report attempt.
                pass
        self._record_change(
            "toggle" if resolved_line is not None else "add",
            summary,
            reason=f"agent progress: {summary[:80]}",
            line=resolved_line,
        )
        self._save_state()
        state = self.build_state()
        # Append notes AFTER build_state (which resets _suggestions)
        extra: list[dict[str, Any]] = []
        if resolve_note:
            extra.append(
                self._suggestion(
                    kind="resolve",
                    title="进度行号校正",
                    body=resolve_note,
                    key=f"resolve-{int(self._last_progress_time)}",
                )
            )
        if verification_note:
            extra.append(
                self._suggestion(
                    kind="verify",
                    title="产出核验",
                    body=verification_note,
                    key=f"verify-{int(self._last_progress_time)}",
                )
            )
        if extra:
            state["suggestions"] = list(state.get("suggestions", [])) + extra
            self._suggestions.extend(extra)
            for note in extra:
                self._last_suggestions[note["id"]] = note
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
