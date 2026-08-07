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
    TASKS_ARCHIVE_NAME,
    MILESTONE_PROJECT_COMPLETE_KEY,
    build_suggestion_phase_key_map,
    drop_task_line,
    evaluate_milestone_after_archive,
    migrate_active_milestone_suggestion_keys,
    migrate_milestone_phase_key_set,
    milestone_phase_keys_for_persist,
    normalize_delivery_profile,
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
from session import Session, SessionMeta

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


_PLAN_SYSTEM = """你是 **Plan Agent（计划搭档）**：主输入「计划搭档」通道的对话伙伴。
先理解用户要什么，再决定是否改文件或查跑工具。系统只解析你返回的 JSON；**计划域四件套采纳前不会写盘**。
你 **看不到** 主 Agent 聊天全文；只吃本通道来回 + 计划域文件真源。

## 文档角色（先按这个判断「合不合理」）
- **MAP.md**：目录、入口、模块/文件指针、「现在卡在哪」。**不是**执行 Phase，**不是**修复流水账。
- **TASKS.md**：唯一执行队列（开放可勾项）。Phase 只出现在这里（或归档）。
- **TASKS.archive.md**：已完成/关闭项；侧栏勾选 = 完成并归档。误勾后用 restore，不要当「删了」去 add 重写。
- **PROJECT.md**：目标/非目标/约束。
- **ENV.md**：环境与端口约定。
- **bugs/**：缺陷与修复长文；MAP/TASKS 里最多留一行指针。

## 输出
只输出一个 JSON 对象：
{"reply":"给用户看的中文（可空）","operations":[ ... ],"tool_calls":[ ... ]}

operations 里每条：
- kind: **patch** | **add** | **restore**
- path: patch 时必填，且只能是 TASKS.md|MAP.md|PROJECT.md|ENV.md
- replacements: patch 时 [{ "old": "文件中唯一原文片段", "new": "替换后" }]
- phase / description: 仅 kind=add
- phase / task_ids / bodies: 仅 kind=restore（从归档恢复为开放 [ ]）
- reason: 短理由

restore 示例：
{"kind":"restore","phase":"Phase 7","task_ids":["T-020","T-021"],"reason":"误勾选归档"}

tool_calls（可选；结果只进本通道）：
- {"name":"read_file"|"list_dir"|"grep"|"web_search"|"fetch_url"|"run_command", "arguments":{...}}
- **禁止** write_text 等直写 TASKS/MAP/PROJECT/ENV（须 operations 提案）
- **禁止** 对 TASKS.md / TASKS.archive.md 再 read_file（开放队列与归档切片已在下方 user prompt）；除非读其它业务代码路径

## 意图分流（先分清）
- 「合不合理 / 该不该 / 为什么 / 是不是」→ **先 reply 讲清楚**；用户没明确要求改文件时 **operations 必须 []**
- 「把…改掉 / 整理 / 挪走 / 删掉 Phase 字样」→ reply 可一句 + operations 出 patch
- 「加一个 / 新增任务」→ add 或 TASKS patch
- 「恢复 / 找回 / 误勾 / 不见了」→ 看归档切片；优先 kind=restore（phase 或 task_ids），**禁止**用 add 重写已归档文案
- 「优化 / 夹心 / 跳段」→ 看进度摘要 ⚠；需要改队列再 patch TASKS；并行模块脚手架不是重复
- 需要读仓库其它文件 / 跑命令才能答 → 先 tool_calls，operations 可 []

## 「任务不见了」决策
1. 开放队列空 + 归档有匹配项 → restore（不要宣布项目已完成）
2. MAP 有任务表、TASKS 无开放项 → 按 MAP 文案 restore / patch，不要凭空编
3. 进度摘要出现「可能误归档」⚠ → 优先 restore

## 硬规则
- **禁止** move|drop|skip|split|reorder 与任何 line 行号字段
- 禁止因为用户只是在问，就强行 patch「意思一下」
- MAP 里出现「Phase N 修复记录」→ 角色上 **不合理**；reply 应说明：修复叙述归 bugs/，MAP 只留结构指针；只有用户要改时才 patch
- old 必须在文件中恰好出现一次；无改动时 operations=[]
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
_PLAN_RESTORE_RE = re.compile(
    r"(恢复|找回|误勾|误点|撤销归档|还原|取消完成|搞不见|不见了|误归档|restore)",
    re.IGNORECASE,
)
_LEGACY_LINE_OPS = frozenset({"move", "rephase", "drop", "skip", "split", "reorder"})

# §15.6 / auto-route: main composer → Plan when intent is plan-domain (uncertain → agent).
_PLAN_DOMAIN_HINT_RE = re.compile(
    r"(TASKS\.md|MAP\.md|PROJECT\.md|ENV\.md|任务(清单|列表)?|计划(表|域)?|"
    r"Phase\s*\d|T-\d{3,})",
    re.IGNORECASE,
)
_PLAN_JUDGE_HINT_RE = re.compile(
    r"(合不合理|该不该|是不是|要不要|合理吗|为什么.*(计划|任务|phase))",
    re.IGNORECASE,
)
_AGENT_EXEC_HINT_RE = re.compile(
    r"(编译|运行|报错|异常|stack|trace|bug|fix|实现|写代码|联调|"
    r"mvn|npm|gradle|\.java|\.vue|\.ts|controller|mapper|sql)",
    re.IGNORECASE,
)


def classify_user_plan_intent(text: str) -> Literal["plan", "agent"]:
    """Legacy keyword router (Phase 38 · deprecated for intercept; tests/compat only)."""
    t = (text or "").strip()
    if not t:
        return "agent"
    if _AGENT_EXEC_HINT_RE.search(t) and not _PLAN_DOMAIN_HINT_RE.search(t):
        if not looks_like_plan_meta_command(t):
            return "agent"
    if looks_like_plan_meta_command(t):
        return "plan"
    if _PLAN_ADD_PREFIX_RE.match(t):
        return "plan"
    if _PLAN_JUDGE_HINT_RE.search(t):
        return "plan"
    if _PLAN_DOMAIN_HINT_RE.search(t) and _PLAN_MUTATE_RE.search(t):
        return "plan"
    if re.match(r"^-\s*\[[ xX]?\]", t):
        return "plan"
    return "agent"


@dataclass(frozen=True, slots=True)
class PlanSpawnDecision:
    spawn: bool
    reason: str


_PLAN_SPAWN_CLASSIFY_PROMPT = """你是 Plan 子代理触发分类器（Phase 39 · 仅分类，不执行）。

判断用户本条消息是否**需要先**整理计划域（TASKS.md / MAP.md / PROJECT.md / ENV.md）。

spawn=true 示例：
- 规划/补文档/排任务/优化任务清单
- 问计划是否合理、Phase 该不该调整
- 新增任务描述（无具体写代码）
- 任务不见了、误勾、从归档恢复 Phase / T-xxx

spawn=false 示例：
- 写代码、修 bug、编译运行、联调
- 「继续」「下一项」等执行续作
- 纯闲聊、回顾上文

只输出一个 JSON 对象：{"spawn": true|false, "reason": "简短中文"}"""


def _parse_plan_spawn_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[^{}]*\"spawn\"[^{}]*\}", text, flags=re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {}


def classify_plan_spawn_intent(
    text: str,
    *,
    llm: Any,
    meta: SessionMeta | None = None,
) -> PlanSpawnDecision:
    """LLM classify for kernel pre-spawn (PLAN-SUBAGENT §4.3 · T-3905)."""
    import os

    from project_mode import is_project_continue_utterance

    t = (text or "").strip()
    if not t:
        return PlanSpawnDecision(False, "empty")
    if os.environ.get("PLAN_SPAWN_CLASSIFY", "1").strip().lower() in {"0", "false", "no"}:
        return PlanSpawnDecision(False, "disabled")
    if is_project_continue_utterance(t):
        return PlanSpawnDecision(False, "continue")

    from llm_routing import resolve_model_id_for_role

    model = resolve_model_id_for_role("topic_routing", meta or SessionMeta())

    try:
        response = llm.chat(
            [
                {"role": "system", "content": _PLAN_SPAWN_CLASSIFY_PROMPT},
                {"role": "user", "content": t},
            ],
            model=model,
            temperature=0.0,
        )
        data = _parse_plan_spawn_json(response.content or "")
        spawn = bool(data.get("spawn"))
        reason = str(data.get("reason") or "").strip() or ("plan_domain" if spawn else "not_plan")
        return PlanSpawnDecision(spawn, reason)
    except Exception as exc:
        return PlanSpawnDecision(False, f"classify_failed:{exc}")


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


def looks_like_restore_request(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    return bool(_PLAN_RESTORE_RE.search(t))


def _plan_progress_brief(
    tasks_text: str,
    *,
    archive_path: "Path | None" = None,
    archive_tail: str = "",
) -> str:
    """Compact progress + explicit anomaly signals for Plan LLM."""
    from pathlib import Path

    from project_mode import (
        active_phase_title_from_lines,
        archive_done_count_for_phase,
        iter_phase_headers,
        phase_open_count_visible,
    )

    lines = tasks_text.splitlines()
    headers = iter_phase_headers(lines)
    if not headers:
        return "（尚无 Phase）"

    archive = archive_path if isinstance(archive_path, Path) else None
    if archive is not None and not archive.is_file():
        archive = None

    rows: list[str] = []
    phase_stats: list[tuple[str, int, int]] = []
    for _, title in headers:
        open_n = phase_open_count_visible(lines, title)
        archived_done = (
            archive_done_count_for_phase(archive, title) if archive is not None else 0
        )
        phase_stats.append((title, open_n, archived_done))
        if open_n == 0 and archived_done > 0:
            status = "已完成"
        elif open_n > 0 and archived_done == 0:
            status = "未开始"
        elif open_n > 0:
            status = "进行中"
        else:
            status = "空"
        rows.append(f"- {title}: {archived_done} archived-done / {open_n} open · {status}")

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
    for i, (title, open_n, archived_done) in enumerate(phase_stats):
        later_done = any(d > 0 for _, _, d in phase_stats[i + 1 :])
        if not later_done or open_n <= 0:
            continue
        if archived_done > 0:
            alerts.append(
                f"⚠ 夹心：「{title}」仍有 {open_n} 条未完成，但后续 Phase 已有完成项"
                "——优化时应 move（或说明为何不挪），禁止空操作装没事"
            )
        elif open_n >= 2:
            alerts.append(
                f"⚠ 跳段：「{title}」仍有 {open_n} 条未开始/未完成，"
                "但后续 Phase 已开始勾选——检查是否偷跑"
            )
    # Empty phases (no open and no archive evidence)
    for title, open_n, archived_done in phase_stats:
        if open_n == 0 and archived_done == 0:
            alerts.append(f"⚠ 空 Phase：「{title}」下没有任务")
    # Next item dragged back into mostly-done early phase
    if next_open and active:
        for title, open_n, archived_done in phase_stats:
            if title != active:
                continue
            if archived_done >= 2 and open_n >= 1:
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

    open_total = sum(o for _, o, _ in phase_stats)
    if open_total == 0 and archive_tail.strip():
        empty_phases = [t for t, o, d in phase_stats if o == 0 and d == 0]
        for ep in empty_phases:
            key = ep.split("—")[0].strip() if "—" in ep else ep[:24]
            if key and key.casefold() in archive_tail.casefold():
                rows.append(
                    f"- 异常信号: ⚠ 可能误归档——「{ep}」在 TASKS 无开放项，"
                    "但归档里有相关任务；可说「恢复」或用 kind=restore"
                )
                break

    return "\n".join(rows)


def _format_tasks_with_line_numbers(tasks_text: str) -> str:
    """Numbered open-queue slice for Plan LLM (PLAN-ARCH M1 · IT-180)."""
    from project_mode import format_tasks_open_slice_numbered

    return format_tasks_open_slice_numbered(tasks_text)


def _clip_doc(text: str, *, limit: int = 6000) -> str:
    t = text or ""
    if len(t) <= limit:
        return t
    return t[: limit - 20] + "\n\n…(截断)…\n"


def _build_plan_prompt(
    tasks_text: str,
    user_intent: str,
    *,
    map_text: str = "",
    project_text: str = "",
    env_text: str = "",
    archive_tail: str = "",
    archive_path: "Path | None" = None,
    plan_transcript: list[dict[str, str]] | None = None,
    tool_results: list[str] | None = None,
) -> str:
    """Context for Plan LLM — Plan 本线 + 计划域文件真源（不灌主聊天）。"""
    brief = _plan_progress_brief(
        tasks_text, archive_path=archive_path, archive_tail=archive_tail
    )
    numbered = _format_tasks_with_line_numbers(tasks_text)
    prior = ""
    if plan_transcript:
        lines: list[str] = []
        # Exclude the latest user turn (passed as user_intent).
        hist = list(plan_transcript)
        if hist and hist[-1].get("role") == "user" and hist[-1].get("content") == user_intent:
            hist = hist[:-1]
        for item in hist[-12:]:
            role = item.get("role") or ""
            content = (item.get("content") or "").strip()
            if not content:
                continue
            label = "用户" if role == "user" else "计划搭档" if role == "assistant" else role
            lines.append(f"{label}: {content}")
        if lines:
            prior = "## 本通道先前来回（不含主 Agent 聊天）\n\n" + "\n".join(lines) + "\n\n"
    tools_block = ""
    if tool_results:
        tools_block = (
            "## 本轮工具结果（仅 Plan 线）\n\n"
            + "\n\n".join(tool_results[-6:])
            + "\n\n"
        )
    return f"""{prior}## 进度摘要（先看异常信号 ⚠）

{brief}

## TASKS.md 开放队列切片（只读参考；改文件请用 patch replacements）

{numbered}

## MAP.md（可 patch）

{_clip_doc(map_text) or "（无）"}

## PROJECT.md（可 patch）

{_clip_doc(project_text, limit=2500) or "（无）"}

## ENV.md（可 patch）

{_clip_doc(env_text, limit=2000) or "（无）"}

{archive_tail or ""}{tools_block}## 用户说

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
    # PLAN-ARCH Q1/M2: add/move/drop/… parked until human accept_suggestion
    _pending_gated: dict[str, dict[str, Any]] = field(default_factory=dict)
    _last_progress_time: float = 0.0  # time.time() of last report_progress call
    _last_partner_notices: list[str] = field(default_factory=list, repr=False)
    # T-4714/4715 · milestone review suggestion dedup (plan state.json, not session)
    _milestone_reminded_phase_keys: set[str] = field(default_factory=set, repr=False)
    _milestone_dismissed_phase_keys: set[str] = field(default_factory=set, repr=False)
    _active_milestone_suggestions: dict[str, dict[str, Any]] = field(
        default_factory=dict, repr=False
    )
    # Phase 38 · A11/C4–C6 — in-memory Plan channel only (never messages.jsonl)
    _plan_transcript: list[dict[str, str]] = field(default_factory=list, repr=False)
    _planning_model_id: str = field(default="", repr=False)

    def configure_planning_model(self, model_id: str) -> None:
        """Set planning model for this run (Phase 42 · plan_partner role)."""
        self._planning_model_id = (model_id or "").strip()
        if self._llm is not None:
            self._llm._plan_model = self._planning_model_id

    def __post_init__(self) -> None:
        self._load_state()

    def set_partner_notices(self, summary: str | list[str]) -> None:
        """Short sidebar operational notices (C3 — not Plan long-chat host)."""
        if isinstance(summary, list):
            lines = [str(x).strip() for x in summary if str(x).strip()]
        else:
            lines = [ln.strip() for ln in str(summary).splitlines() if ln.strip()]
        lines = self._sanitize_partner_notice_lines(lines)
        # Keep sidebar short; long reply lives on plan transcript / main-area bubbles.
        short: list[str] = []
        for ln in lines[:6]:
            short.append(ln if len(ln) <= 160 else ln[:157] + "…")
        self._last_partner_notices = short

    @staticmethod
    def _sanitize_partner_notice_lines(lines: list[str]) -> list[str]:
        """Strip diff hunks from sidebar notices (Phase 40 / BUG-022)."""
        out: list[str] = []
        for ln in lines:
            s = ln.strip()
            if not s:
                continue
            if s.startswith("@@"):
                continue
            if len(s) >= 2 and s[0] in "+-" and s[1] in " +-":
                continue
            out.append(s)
        return out

    @staticmethod
    def _adopted_write_notice(rel: str, *, detail: str = "") -> str:
        path = str(rel or "").strip() or "计划文件"
        base = f"已采纳写入 {path}"
        extra = str(detail or "").strip()
        return f"{base}（{extra}）" if extra else base

    def clear_plan_transcript(self) -> None:
        """C6: enter/switch project → wipe Plan chat memory; keep disk plan files."""
        self._plan_transcript = []
        self._last_partner_notices = []

    def append_plan_turn(self, role: str, content: str) -> None:
        text = str(content or "").strip()
        if not text:
            return
        self._plan_transcript.append({"role": str(role), "content": text})
        # Soft cap — memory only
        if len(self._plan_transcript) > 40:
            self._plan_transcript = self._plan_transcript[-40:]

    def plan_transcript_snapshot(self) -> list[dict[str, str]]:
        return [dict(x) for x in self._plan_transcript]

    def build_context_messages_for_audit(self) -> list[dict[str, str]]:
        """IT-190 helper: what Plan would send — never includes main chat jsonl."""
        return self.plan_transcript_snapshot()

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
            pending = data.get("pending_gated", {})
            if isinstance(pending, dict):
                self._pending_gated = {
                    str(k): v
                    for k, v in pending.items()
                    if isinstance(v, dict) and v.get("id")
                }
            for entry in data.get("change_log", []):
                self._change_log.append(PlanChange(
                    id=entry.get("id", ""),
                    kind=entry.get("kind", ""),  # type: ignore[arg-type]
                    task_text=entry.get("task_text", ""),
                    reason=entry.get("reason", ""),
                    time=entry.get("time", ""),
                    line=entry.get("line"),
                ))
            reminders = data.get("milestone_review_reminders")
            if isinstance(reminders, dict):
                reminded = reminders.get("reminded_phase_keys")
                dismissed = reminders.get("dismissed_phase_keys")
                if isinstance(reminded, list):
                    self._milestone_reminded_phase_keys = {
                        str(x) for x in reminded if str(x).strip()
                    }
                if isinstance(dismissed, list):
                    self._milestone_dismissed_phase_keys = {
                        str(x) for x in dismissed if str(x).strip()
                    }
                active = reminders.get("active_suggestions")
                if isinstance(active, dict):
                    self._active_milestone_suggestions = {
                        str(k): v
                        for k, v in active.items()
                        if isinstance(v, dict) and v.get("id")
                    }
            if self._migrate_milestone_phase_keys():
                self._save_state()
        except (OSError, json.JSONDecodeError):
            pass

    def _migrate_milestone_phase_keys(self) -> bool:
        """IT-541/542 · legacy ``phase:N`` / ``title:hash`` → ``phase_id`` on load."""
        root = project_dir(self.paths, self.project_id)
        tasks_path = root / "TASKS.md"
        archive_path = root / TASKS_ARCHIVE_NAME
        hint_map = build_suggestion_phase_key_map(self._active_milestone_suggestions)
        reminded, r1 = migrate_milestone_phase_key_set(
            self._milestone_reminded_phase_keys,
            self.paths,
            self.project_id,
            tasks_path=tasks_path,
            archive_path=archive_path,
            suggestion_phase_by_key=hint_map,
        )
        dismissed, r2 = migrate_milestone_phase_key_set(
            self._milestone_dismissed_phase_keys,
            self.paths,
            self.project_id,
            tasks_path=tasks_path,
            archive_path=archive_path,
            suggestion_phase_by_key=hint_map,
        )
        self._milestone_reminded_phase_keys = reminded
        self._milestone_dismissed_phase_keys = dismissed
        active, r3 = migrate_active_milestone_suggestion_keys(
            self._active_milestone_suggestions,
            self.paths,
            self.project_id,
            tasks_path=tasks_path,
            archive_path=archive_path,
            suggestion_phase_by_key=hint_map,
        )
        self._active_milestone_suggestions = active
        return r1 or r2 or r3

    def _save_state(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "fingerprint": self._last_fingerprint,
            "change_counter": self._change_counter,
            "ignored_suggestion_ids": sorted(self._ignored_suggestion_ids)[-200:],
            "pending_gated": {
                sid: sug
                for sid, sug in list(self._pending_gated.items())[-50:]
                if isinstance(sug, dict)
            },
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
            "milestone_review_reminders": {
                "reminded_phase_keys": milestone_phase_keys_for_persist(
                    self._milestone_reminded_phase_keys
                ),
                "dismissed_phase_keys": milestone_phase_keys_for_persist(
                    self._milestone_dismissed_phase_keys
                ),
                "active_suggestions": {
                    k: v
                    for k, v in list(self._active_milestone_suggestions.items())[-20:]
                    if isinstance(v, dict)
                },
            },
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
        open_line = ""
        if tasks_path.is_file():
            fls = tasks_path.read_text(encoding="utf-8").splitlines()
            if 0 <= line < len(fls):
                import re
                m = re.match(r"^\s*-\s*\[[ x]\]\s+(.*)", fls[line])
                if m:
                    task_text = m.group(1).strip()
                    open_line = f"- [ ] {task_text}"

        result = toggle_task_line(self.paths, self.project_id, line, done)
        if done:
            # Archived+removed — undo re-inserts open checkbox at original index
            undo = UndoEntry(
                description=f"已勾选并归档「{task_text[:30]}」",
                reverse_kind="insert",
                reverse_data={
                    "position": line,
                    "content": open_line or f"- [ ] {task_text}",
                },
            )
            return self._mutate_and_check(
                result,
                "toggle",
                f"line {line}",
                reason="toggle+archive",
                line=line,
                undo=undo,
            )
        label = "已取消勾选"
        undo = UndoEntry(
            description=f"{label}「{task_text[:30]}」",
            reverse_kind="toggle",
            reverse_data={"line": line, "done": True},
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

    @staticmethod
    def _format_milestone_review_body(
        ev: dict[str, Any],
        *,
        delivery_profile: str = "solo",
    ) -> str:
        phase = str(ev.get("phase") or "").strip() or "本 Phase"
        lines = [
            f"[里程碑] {phase} 开放任务已归档。",
            "建议：① git_commit 快照 ② build/test ③ 口语「验收」（只读 review，不挡写码）。",
        ]
        if ev.get("m2") or ev.get("should_remind_m2"):
            lines.append("全项目开放队列已空；收尾前建议 review + commit。")
        if normalize_delivery_profile(delivery_profile) == "ritual":
            lines.append(
                "ritual：建议先 deliverable_review，fail 时会挡 report_progress。"
            )
        return "\n".join(lines)

    def _emit_milestone_review_if_needed(
        self,
        toggle_result: dict[str, Any],
        *,
        delivery_profile: str = "solo",
    ) -> dict[str, Any] | None:
        """Post-archive milestone suggestion (LOCAL-DELIVERY-MODEL §5 · T-4715).

        Does **not** spawn ``deliverable_review`` (M-R1).
        """
        if not toggle_result.get("done"):
            return None
        phase = str(toggle_result.get("phase") or "").strip()
        if not phase:
            return None

        root = project_dir(self.paths, self.project_id)
        ev = evaluate_milestone_after_archive(
            tasks_path=root / "TASKS.md",
            archive_path=root / TASKS_ARCHIVE_NAME,
            phase=phase,
            project_id=self.project_id,
            paths=self.paths,
            reminded_phase_keys=self._milestone_reminded_phase_keys,
            dismissed_phase_keys=self._milestone_dismissed_phase_keys,
        )
        if not ev.get("should_remind"):
            return None

        phase_key = str(ev.get("phase_key") or "").strip()
        body = self._format_milestone_review_body(
            ev, delivery_profile=delivery_profile
        )
        sug = self._suggestion(
            kind="milestone_review",
            title="里程碑验收建议",
            body=body,
            key=phase_key or "project",
            risk="suggest",
            payload={
                "phase": phase,
                "phase_key": phase_key,
                "remind_scope": ev.get("remind_scope") or "",
                "m1": bool(ev.get("m1")),
                "m2": bool(ev.get("m2")),
            },
        )
        sid = str(sug.get("id") or "")
        if sid:
            self._active_milestone_suggestions[sid] = sug
            self._last_suggestions[sid] = sug
            self._ignored_suggestion_ids.discard(sid)

        if ev.get("should_remind_m1") and phase_key:
            self._milestone_reminded_phase_keys.add(phase_key)
        if ev.get("should_remind_m2"):
            self._milestone_reminded_phase_keys.add(MILESTONE_PROJECT_COMPLETE_KEY)

        notice = body.split("\n", 1)[0]
        self.set_partner_notices(notice)
        self._save_state()
        return sug

    def milestone_review_overlay_key(self) -> str | None:
        """Active milestone ``phase_key`` for project overlay (T-4717 · M-R6)."""
        for sid, sug in self._active_milestone_suggestions.items():
            if sid in self._ignored_suggestion_ids:
                continue
            if sug.get("kind") != "milestone_review":
                continue
            from project_mode import phase_key_from_milestone_suggestion

            key = phase_key_from_milestone_suggestion(sug)
            if key:
                return key
        return None

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
        sug = (
            self._last_suggestions.get(sid)
            or self._active_milestone_suggestions.get(sid)
            or self._pending_gated.get(sid)
        )
        if isinstance(sug, dict) and sug.get("kind") == "milestone_review":
            self._dismiss_milestone_review_suggestion(sug)
        self._mark_suggestion_resolved(sid)

    @staticmethod
    def _milestone_phase_key_from_suggestion(sug: dict[str, Any]) -> str:
        from project_mode import phase_key_from_milestone_suggestion

        return phase_key_from_milestone_suggestion(sug)

    def _dismiss_milestone_review_suggestion(self, sug: dict[str, Any]) -> None:
        """Permanent dismiss for a milestone card (LOCAL-DELIVERY-MODEL §5.6 · IT-477)."""
        phase_key = self._milestone_phase_key_from_suggestion(sug)
        if phase_key:
            self._milestone_dismissed_phase_keys.add(phase_key)
        payload = sug.get("payload") if isinstance(sug.get("payload"), dict) else {}
        if payload.get("m2") or payload.get("remind_scope") in {
            "project",
            "phase_and_project",
        }:
            self._milestone_dismissed_phase_keys.add(MILESTONE_PROJECT_COMPLETE_KEY)
        sid = str(sug.get("id") or "")
        if sid:
            self._active_milestone_suggestions.pop(sid, None)
        self._save_state()

    def clear_milestone_reminded_on_review(self, verdict: str | None) -> bool:
        """Clear reminded keys after deliverable_review pass/warn (M-R4 · IT-476-5)."""
        key = (verdict or "").strip().lower()
        if key not in {"pass", "warn"}:
            return False
        changed = False
        if self._milestone_reminded_phase_keys:
            self._milestone_reminded_phase_keys.clear()
            changed = True
        removed: list[str] = []
        for sid, active in list(self._active_milestone_suggestions.items()):
            if active.get("kind") == "milestone_review":
                self._active_milestone_suggestions.pop(sid, None)
                self._ignored_suggestion_ids.discard(sid)
                removed.append(sid)
                changed = True
        if changed:
            self._save_state()
        return changed

    def emit_bug_promote_from_review(
        self,
        summary: str,
        *,
        source: str = "deliverable_review",
        delivery_profile: str = "solo",
        verdict: str | None = None,
    ) -> list[dict[str, Any]]:
        """Emit gated bug_promote cards from review/checker blockers (T-5403 · BQ-1)."""
        from subagent import extract_review_blocker_items

        from project_mode import resolve_bug_promote_phase

        profile = normalize_delivery_profile(delivery_profile)
        blockers = extract_review_blocker_items(summary, verdict=verdict)
        if not blockers:
            return []

        phase_title = resolve_bug_promote_phase(self.paths, self.project_id)
        emitted: list[dict[str, Any]] = []
        for blk in blockers:
            severity = str(blk.get("severity") or "P1").strip().upper()
            if profile == "ritual" and severity not in {"P0", "P1"}:
                continue
            title = str(blk.get("title") or "缺陷").strip()[:120] or "缺陷"
            detail = str(blk.get("detail") or title).strip()[:500] or title
            desc = f"[{severity}] {title}"
            if detail.casefold() != title.casefold():
                desc = f"{desc} — {detail}"
            import hashlib

            key = hashlib.sha1(
                f"{source}|{title}".casefold().encode("utf-8")
            ).hexdigest()[:12]
            sid = f"sug-bug_promote-{key}"
            if sid in self._ignored_suggestion_ids or sid in self._pending_gated:
                continue
            sug = self._suggestion(
                kind="bug_promote",
                title=f"缺陷晋升 · {title[:40]}",
                body=detail,
                key=key,
                risk="gate",
                action="add_task",
                payload={
                    "title": title,
                    "detail": detail,
                    "source": source,
                    "severity": severity,
                    "phase": phase_title,
                    "description": desc[:500],
                },
            )
            self.park_gated_suggestion(sug)
            emitted.append(sug)
        if emitted:
            self.set_partner_notices(
                f"审查发现 {len(emitted)} 项可晋升 TASKS；侧栏「采纳进 TASKS」。"
            )
        return emitted

    def _mark_suggestion_resolved(self, sid: str) -> None:
        """Drop pending gate + clear stale「点采纳」notices when queue empties."""
        self._pending_gated.pop(sid, None)
        self._ignored_suggestion_ids.add(sid)
        if not self._pending_gated:
            self._last_partner_notices = []
        self._save_state()

    def park_gated_suggestion(self, sug: dict[str, Any]) -> dict[str, Any]:
        """Park a mutating plan change until human accept (PLAN-ARCH Q1)."""
        sid = str(sug.get("id") or "").strip()
        if not sid:
            raise ProjectModeError("park_gated_suggestion requires id")
        if sid in self._ignored_suggestion_ids:
            return sug
        sug = dict(sug)
        sug["risk"] = sug.get("risk") or "gate"
        self._pending_gated[sid] = sug
        self._last_suggestions[sid] = sug
        self._save_state()
        return sug

    def _rebase_pending_patch_suggestions_for_path(self, adopted_path: str) -> list[str]:
        """BUG-026 A2 (T-4812): refresh base_hash for other pending patches on same path."""
        from plan_patch import build_patch_preview

        path = str(adopted_path or "").strip()
        if not path:
            return []

        withdrawn: list[str] = []
        changed = False
        for other_sid, other_sug in list(self._pending_gated.items()):
            if other_sug.get("action") != "apply_patch":
                continue
            payload = other_sug.get("payload")
            if not isinstance(payload, dict):
                continue
            if str(payload.get("path") or "").strip() != path:
                continue
            reps = payload.get("replacements")
            if not isinstance(reps, list) or not reps:
                continue
            try:
                new_preview = build_patch_preview(
                    self.paths,
                    self.project_id,
                    relpath=path,
                    replacements=reps,
                    base_hash=None,
                )
            except ProjectModeError as exc:
                self._mark_suggestion_resolved(other_sid)
                withdrawn.append(f"已撤回无效提案：{exc}")
                continue

            updated = dict(other_sug)
            updated_payload = dict(payload)
            updated_payload["base_hash"] = new_preview["base_hash"]
            updated_payload["diff"] = new_preview["diff"]
            updated["payload"] = updated_payload
            self._pending_gated[other_sid] = updated
            self._last_suggestions[other_sid] = updated
            changed = True

        if changed:
            self._save_state()
        return withdrawn

    def accept_suggestion(self, suggestion_id: str) -> dict[str, Any]:
        """Apply a previously emitted suggestion via its action/payload."""
        sid = str(suggestion_id or "").strip()
        sug = self._last_suggestions.get(sid) or self._pending_gated.get(sid)
        if sug is None:
            # UI may hold cards across sidecar restart; rebuild actionable catalog.
            rebuilt = {s["id"]: s for s in self.quality_suggestions() if s.get("id")}
            rebuilt.update(self._pending_gated)
            self._last_suggestions = rebuilt
            sug = rebuilt.get(sid)
        if sug is None:
            raise ProjectModeError(f"unknown or stale suggestion: {sid}")
        action = sug.get("action")
        payload = sug.get("payload") if isinstance(sug.get("payload"), dict) else {}
        if not action:
            raise ProjectModeError(f"suggestion is not actionable: {sid}")

        if action == "apply_patch":
            from plan_patch import apply_plan_patch

            rel = str(payload.get("path") or "").strip()
            reps = payload.get("replacements")
            if not isinstance(reps, list):
                raise ProjectModeError("apply_patch requires replacements[]")
            base_hash = payload.get("base_hash")
            try:
                result = apply_plan_patch(
                    self.paths,
                    self.project_id,
                    relpath=rel,
                    replacements=reps,
                    base_hash=str(base_hash) if base_hash else None,
                )
            except ProjectModeError as exc:
                self._mark_suggestion_resolved(sid)
                self.set_partner_notices(f"已撤回无效提案：{exc}")
                return {
                    "ok": False,
                    "summary": f"已撤回无效提案：{exc}",
                    "_next_task": self.next_task_text(),
                }
            self._record_change(
                "external" if rel != "TASKS.md" else "add",
                f"patch {rel}",
                reason=f"accepted file_patch: {sug.get('title', '')}"[:120],
            )
            self._mark_suggestion_resolved(sid)
            rebase_withdrawn = self._rebase_pending_patch_suggestions_for_path(rel)
            notice = self._adopted_write_notice(rel)
            if rebase_withdrawn:
                notice = "\n".join([notice, *rebase_withdrawn])
            self.set_partner_notices(notice)
            return {
                **result,
                "summary": notice,
                "_next_task": self.next_task_text(),
            }

        if action == "add_task":
            from project_mode import add_task_to_tasks_md

            desc = str(payload.get("description") or "").strip()
            if not desc:
                raise ProjectModeError("add_task suggestion missing description")
            phase_title = str(payload.get("phase") or "").strip() or self._default_add_phase()
            result = add_task_to_tasks_md(self.paths, self.project_id, phase_title, desc)
            new_line = result.get("line", -1)
            landed = result.get("phase") or phase_title
            self._record_change(
                "add",
                desc,
                reason=f"accepted gated add: {sug.get('kind', '')}",
                line=new_line if isinstance(new_line, int) else None,
            )
            self.push_undo(
                description=f"已添加「{desc[:30]}」",
                reverse_kind="drop",
                reverse_data={"line": new_line},
            )
            self._mark_suggestion_resolved(sid)
            return {
                "ok": True,
                "phase": landed,
                "description": desc,
                "line": new_line,
                "_next_task": self.next_task_text(),
            }

        if action == "restore_archive":
            from project_mode import restore_archived_tasks

            phase_sub = str(payload.get("phase") or payload.get("phase_substring") or "").strip() or None
            bodies = payload.get("bodies")
            body_subs = (
                [str(b) for b in bodies if str(b).strip()]
                if isinstance(bodies, list)
                else None
            )
            task_ids = payload.get("task_ids")
            ids = (
                [str(t) for t in task_ids if str(t).strip()]
                if isinstance(task_ids, list)
                else None
            )
            result = restore_archived_tasks(
                self.paths,
                self.project_id,
                phase_substring=phase_sub,
                body_substrings=body_subs,
                task_ids=ids,
            )
            restored = result.get("restored") or []
            self._record_change(
                "add",
                f"restore {len(restored)} tasks",
                reason=f"accepted restore: {sug.get('title', '')}"[:120],
            )
            self._mark_suggestion_resolved(sid)
            notice = self._adopted_write_notice(
                "TASKS.md",
                detail=f"已恢复 {len(restored)} 条任务",
            )
            self.set_partner_notices(notice)
            return {
                **result,
                "ok": True,
                "summary": notice,
                "_next_task": self.next_task_text(),
            }

        if action == "drop_task":
            line = payload.get("line")
            if not isinstance(line, int) or line < 0:
                raise ProjectModeError("drop_task suggestion missing line")
            result = self.drop_task(line)
            self._mark_suggestion_resolved(sid)
            return result

        if action == "move_task":
            from project_mode import move_task_to_phase

            line = payload.get("line")
            phase = str(payload.get("phase") or "").strip()
            if not isinstance(line, int) or line < 0 or not phase:
                raise ProjectModeError("move_task suggestion missing line/phase")
            result = move_task_to_phase(self.paths, self.project_id, line, phase)
            self._record_change(
                "reorder",
                str(result.get("description") or ""),
                reason="accepted gated move",
                line=result.get("line") if isinstance(result.get("line"), int) else None,
            )
            self._mark_suggestion_resolved(sid)
            return {**result, "ok": True, "_next_task": self.next_task_text()}

        if action == "reorder_task":
            line = payload.get("line")
            direction = str(payload.get("direction") or "up")
            if not isinstance(line, int) or line < 0 or direction not in {"up", "down"}:
                raise ProjectModeError("reorder_task suggestion missing line/direction")
            result = self.reorder_task(line, direction)
            self._mark_suggestion_resolved(sid)
            return result

        if action == "split_task":
            line = payload.get("line")
            if not isinstance(line, int) or line < 0:
                raise ProjectModeError("split_task suggestion missing line")
            summary = self.split_task(line)
            self._mark_suggestion_resolved(sid)
            return {"ok": True, "summary": summary, "_next_task": self.next_task_text()}

        if action == "skip_task":
            line = payload.get("line")
            if not isinstance(line, int) or line < 0:
                raise ProjectModeError("skip_task suggestion missing line")
            result = self.skip_task(line)
            self._mark_suggestion_resolved(sid)
            return result

        if action == "confirm_changes":
            self.confirm_plan()
            self._mark_suggestion_resolved(sid)
            return {"ok": True, "_next_task": self.next_task_text()}

        raise ProjectModeError(f"unsupported suggestion action: {action}")

    def _default_add_phase(self) -> str:
        tasks_path = project_dir(self.paths, self.project_id) / "TASKS.md"
        tasks_text = tasks_path.read_text(encoding="utf-8") if tasks_path.is_file() else ""
        phase_title = "Phase 1"
        current_phase = ""
        for line in tasks_text.splitlines():
            if line.strip().startswith("## "):
                current_phase = line.strip().lstrip("#").strip()
                if phase_title == "Phase 1" and current_phase:
                    phase_title = current_phase
            if "- [ ]" in line and current_phase:
                return current_phase
        return phase_title or "Phase 1"

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
        """Propose adding text as a task (PLAN-ARCH Q1 — no auto write)."""
        desc = strip_add_prefix(text)
        phase_title = self._default_add_phase()
        key = f"fb-add-{abs(hash(f'{phase_title}|{desc}')) % 10_000_000:x}"
        sug = self._suggestion(
            kind="add_task",
            title="新增任务（待采纳）",
            body=f"建议加入 {phase_title}：{desc[:80]}",
            key=key,
            risk="gate",
            action="add_task",
            payload={
                "phase": phase_title,
                "description": desc,
                "source": "fallback",
            },
        )
        self.park_gated_suggestion(sug)
        prefix = f"{extra}。" if extra else ""
        return (
            f"{prefix}已提案新增到 {phase_title}（未写盘）：{desc[:60]}。"
            "审阅面或侧栏「查看」后可写入 TASKS.md。"
        )

    def _plan_channel_fallback(self, text: str, extra: str = "") -> str:
        """L2兜底 only — LLM 不可用 / 解析失败时。正常路径应已走 LLM。"""
        if looks_like_plan_meta_command(text):
            return self._handle_meta_plan_command(text)
        if looks_like_restore_request(text):
            restored = self._handle_restore_request(text)
            if restored:
                if extra:
                    return f"{extra}。{restored}"
                return restored
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
            lines.append(f"另有 {len(suggestions)} 条建议待审阅。")
        return "\n".join(lines)

    def _ensure_llm(self):
        if self._llm is not None:
            return self._llm
        from llm_client import LLMClient
        from llm_routing import resolve_model_id_for_role
        from session import SessionMeta

        if self._planning_model_id:
            model = self._planning_model_id
        else:
            model = resolve_model_id_for_role("plan_partner", SessionMeta())
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

    def _extract_restore_hints(self, text: str) -> dict[str, Any]:
        t = (text or "").strip()
        phase_sub: str | None = None
        m = re.search(r"phase\s*(\d+)", t, re.IGNORECASE)
        if m:
            phase_sub = f"phase {m.group(1)}"
        elif "蔡岭" in t:
            phase_sub = "蔡岭"
        task_ids = sorted(set(re.findall(r"\bT-\d{3,}\b", t, re.IGNORECASE)))
        return {"phase_substring": phase_sub, "task_ids": task_ids or None}

    def _park_restore_suggestion(
        self,
        *,
        preview: dict[str, Any],
        reason_prefix: str,
        phase_substring: str | None = None,
        task_ids: list[str] | None = None,
    ) -> str:
        bodies = preview.get("bodies") or []
        count = int(preview.get("count") or 0)
        key = f"restore-{abs(hash('|'.join(bodies))) % 10_000_000:x}"
        label = phase_substring or (", ".join(task_ids) if task_ids else "匹配项")
        sug = self._suggestion(
            kind="restore_archive",
            title=f"从归档恢复 {count} 条任务（待采纳）",
            body=f"{reason_prefix} 恢复 {label}：{'; '.join(str(b)[:40] for b in bodies[:4])}",
            key=key,
            risk="gate",
            action="restore_archive",
            payload={
                "phase": phase_substring or "",
                "phase_substring": phase_substring or "",
                "task_ids": task_ids,
                "bodies": bodies,
                "source": "plan_restore",
            },
        )
        self.park_gated_suggestion(sug)
        return (
            f"提案从归档恢复 {count} 条任务到 TASKS.md（待审阅）："
            + "；".join(str(b)[:50] for b in bodies[:6])
            + ("…" if len(bodies) > 6 else "")
        )

    def _handle_restore_request(self, text: str) -> str | None:
        from project_mode import preview_restore_archived_tasks

        hints = self._extract_restore_hints(text)
        preview = preview_restore_archived_tasks(
            self.paths,
            self.project_id,
            phase_substring=hints.get("phase_substring"),
            task_ids=hints.get("task_ids"),
        )
        if not preview.get("count"):
            return (
                "归档里没有匹配的任务可恢复。"
                "请指定 Phase（如 Phase 7）或任务号（如 T-020）。"
            )
        return self._park_restore_suggestion(
            preview=preview,
            reason_prefix="误归档恢复",
            phase_substring=hints.get("phase_substring"),
            task_ids=hints.get("task_ids"),
        )

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
                f"计划搭档已检查：{len(suggestions)} 条建议待审阅。"
                f"未把「{label}」写成任务。"
            )
        else:
            lines.append(
                f"已检查计划：没有需要你拍板的改动（完全重复会已自动清）。"
                f"未把「{label}」写成任务。"
            )
        return "\n".join(lines)

    @staticmethod
    def _parse_operations_json(
        raw: str,
    ) -> tuple[list[dict[str, Any]], bool, str, list[dict[str, Any]]]:
        """Return (operations, parsed_ok, reply, tool_calls)."""
        text = (raw or "").strip()
        if not text:
            return [], False, "", []
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
            return [], False, "", []
        if isinstance(result, dict):
            ops = result.get("operations", [])
            reply = str(result.get("reply") or result.get("notice") or "").strip()
            tool_calls = result.get("tool_calls") or result.get("tools") or []
            if not isinstance(tool_calls, list):
                tool_calls = []
            clean_tools = [t for t in tool_calls if isinstance(t, dict) and t.get("name")]
            return (ops if isinstance(ops, list) else []), True, reply, clean_tools
        if isinstance(result, list):
            return result, True, "", []
        return [], False, "", []

    def _park_file_patch_suggestion(
        self,
        *,
        preview: dict[str, Any],
        replacements: list[dict[str, Any]],
        reason_prefix: str,
        reason: str = "",
    ) -> str:
        """Park one gated file_patch suggestion (PLAN-ARCH M6)."""
        key = (
            f"patch-{preview['path']}-"
            f"{abs(hash(preview['diff'])) % 10_000_000:x}"
        )
        reason = (reason or "").strip()
        sug = self._suggestion(
            kind="file_patch",
            title=f"改 {preview['path']}（待采纳）",
            body=reason or f"{reason_prefix} 提议修改 {preview['path']}",
            key=key,
            risk="gate",
            action="apply_patch",
            payload={
                "path": preview["path"],
                "base_hash": preview["base_hash"],
                "replacements": replacements,
                "diff": preview["diff"],
                "source": "plan_llm",
                "reason": reason[:120],
            },
        )
        self.park_gated_suggestion(sug)
        return (
            f"提案 patch {preview['path']}（待采纳）"
            + (f"：{reason[:40]}" if reason else "")
        )

    def _apply_plan_operations(self, operations: list[Any], *, reason_prefix: str) -> list[str]:
        """Park mutating LLM ops as gated suggestions (PLAN-ARCH Q1 / M6).

        Default channel = file_patch (A8). Legacy line ops are rejected (IT-183).
        Does **not** write plan files until accept_suggestion.
        """
        from plan_patch import build_patch_preview

        applied: list[str] = []
        ops = [op for op in operations if isinstance(op, dict)]

        patch_groups: dict[str, tuple[list[dict[str, Any]], list[str]]] = {}
        deferred_ops: list[dict[str, Any]] = []

        for op in ops:
            kind = str(op.get("kind", "") or "").strip().lower()
            if kind != "patch":
                deferred_ops.append(op)
                continue
            path = str(op.get("path") or "").strip()
            reps = op.get("replacements")
            if not path or not isinstance(reps, list) or not reps:
                applied.append("跳过无效 patch（需要 path + replacements）")
                continue
            merged, reasons = patch_groups.setdefault(path, ([], []))
            merged.extend(reps)
            reason = str(op.get("reason") or "").strip()
            if reason:
                reasons.append(reason)

        for path, (all_reps, reasons) in patch_groups.items():
            try:
                preview = build_patch_preview(
                    self.paths,
                    self.project_id,
                    relpath=path,
                    replacements=all_reps,
                )
            except ProjectModeError as exc:
                applied.append(f"跳过无效 patch（{exc}）")
                continue
            combined_reason = "; ".join(reasons)[:120]
            applied.append(
                self._park_file_patch_suggestion(
                    preview=preview,
                    replacements=all_reps,
                    reason_prefix=reason_prefix,
                    reason=combined_reason,
                )
            )

        for op in deferred_ops:
            kind = str(op.get("kind", "") or "").strip().lower()
            try:
                if kind in _LEGACY_LINE_OPS:
                    applied.append(
                        f"已拒绝过时操作 {kind}（请改用 patch；行号 move/drop 已废止）"
                    )
                    continue

                if kind == "add":
                    phase = op.get("phase", "")
                    desc = op.get("description", "")
                    if not desc:
                        continue
                    phase_title = str(phase).strip() if phase else ""
                    if not phase_title:
                        phase_title = self._default_add_phase()
                    desc_s = str(desc).strip()
                    key = f"llm-add-{abs(hash(f'{phase_title}|{desc_s}')) % 10_000_000:x}"
                    sug = self._suggestion(
                        kind="add_task",
                        title="新增任务（待采纳）",
                        body=f"{reason_prefix} 建议加入 {phase_title}：{desc_s[:80]}",
                        key=key,
                        risk="gate",
                        action="add_task",
                        payload={
                            "phase": phase_title,
                            "description": desc_s,
                            "source": "plan_llm",
                            "reason": str(op.get("reason") or "")[:120],
                        },
                    )
                    self.park_gated_suggestion(sug)
                    applied.append(f"提案 + {phase_title}: {desc_s[:50]}（待采纳）")

                elif kind == "restore":
                    from project_mode import preview_restore_archived_tasks

                    phase_sub = str(op.get("phase") or op.get("phase_substring") or "").strip() or None
                    bodies_raw = op.get("bodies")
                    body_subs = (
                        [str(b) for b in bodies_raw if str(b).strip()]
                        if isinstance(bodies_raw, list)
                        else None
                    )
                    ids_raw = op.get("task_ids")
                    task_ids = (
                        [str(t) for t in ids_raw if str(t).strip()]
                        if isinstance(ids_raw, list)
                        else None
                    )
                    preview = preview_restore_archived_tasks(
                        self.paths,
                        self.project_id,
                        phase_substring=phase_sub,
                        body_substrings=body_subs,
                        task_ids=task_ids,
                    )
                    if not preview.get("count"):
                        applied.append("跳过 restore（归档无匹配项）")
                        continue
                    msg = self._park_restore_suggestion(
                        preview=preview,
                        reason_prefix=reason_prefix,
                        phase_substring=phase_sub,
                        task_ids=task_ids,
                    )
                    applied.append(msg)

                else:
                    applied.append(f"跳过未知操作 kind={kind or '∅'}")

            except Exception:
                continue

        return applied

    def _execute_plan_tool_calls(self, tool_calls: list[dict[str, Any]]) -> list[str]:
        """T-3804: run allowed query/run tools; domain writes stay gated."""
        from plan_tools import execute_plan_tool

        results: list[str] = []
        for call in tool_calls[:4]:
            name = str(call.get("name") or "").strip()
            args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
            if not args and isinstance(call.get("args"), dict):
                args = call["args"]
            try:
                tr = execute_plan_tool(self.paths, self.project_id, name, args)
            except Exception as exc:
                results.append(f"{name} → error: {exc}")
                continue
            if tr.ok:
                payload = ""
                try:
                    payload = json.dumps(tr.data, ensure_ascii=False)[:4000]
                except Exception:
                    payload = str(tr.data)[:4000]
                results.append(f"{name} → ok: {payload}")
            else:
                err = tr.error.message if tr.error else "failed"
                results.append(f"{name} → fail: {err}")
        return results

    def _finalize_plan_reply(self, out: str, *, sidebar_short: str | None = None) -> str:
        """Record assistant turn on Plan transcript; short sidebar notice only."""
        text = (out or "").strip()
        if text:
            self.append_plan_turn("assistant", text)
        notice = sidebar_short
        if notice is None:
            if self._pending_gated:
                notice = ""
            elif text:
                first = text.splitlines()[0]
                notice = first if len(first) <= 120 else first[:117] + "…"
            else:
                notice = ""
        if notice:
            self.set_partner_notices(notice)
        else:
            self._last_partner_notices = []
        return text

    def reason_about_intent(
        self,
        text: str,
        *,
        include_last_user: str | None = None,
        record_user: bool = True,
    ) -> str:
        """LLM-first plan channel. Local heuristics only when LLM/parse fails (兜底).

        Context = Plan 本线 + 计划域文件（C5）；不灌主聊天 messages.jsonl。
        """
        user_text = (text or "").strip()
        if record_user and user_text:
            self.append_plan_turn("user", user_text)
        # Optional one-shot hint from main chat (default off / C5)
        extra_hint = (include_last_user or "").strip()
        intent_for_prompt = user_text
        if extra_hint:
            intent_for_prompt = (
                f"{user_text}\n\n（可选参考·主聊上一句，默认应忽略过时信息）\n{extra_hint}"
            )

        try:
            llm = self._ensure_llm()
        except Exception as exc:
            out = self._plan_channel_fallback(user_text, extra=f"LLM 不可用（{exc}）")
            return self._finalize_plan_reply(out)

        # Exact-dup cleanup is a silent safety net, not a substitute for LLM judgment.
        pre_actions = self.auto_fix()

        root = project_dir(self.paths, self.project_id)
        tasks_path = root / "TASKS.md"
        tasks_text = tasks_path.read_text(encoding="utf-8") if tasks_path.is_file() else ""
        map_text = (root / "MAP.md").read_text(encoding="utf-8") if (root / "MAP.md").is_file() else ""
        project_text = (
            (root / "PROJECT.md").read_text(encoding="utf-8")
            if (root / "PROJECT.md").is_file()
            else ""
        )
        env_text = (root / "ENV.md").read_text(encoding="utf-8") if (root / "ENV.md").is_file() else ""
        from project_mode import TASKS_ARCHIVE_NAME, format_archive_tail_for_prompt

        archive_path = root / TASKS_ARCHIVE_NAME
        archive_tail = format_archive_tail_for_prompt(archive_path)

        tool_result_blocks: list[str] = []

        def _one_llm_call() -> tuple[list[dict[str, Any]], bool, str, list[dict[str, Any]]]:
            response = llm.chat(
                [
                    {"role": "system", "content": _PLAN_SYSTEM},
                    {
                        "role": "user",
                        "content": _build_plan_prompt(
                            tasks_text,
                            intent_for_prompt,
                            map_text=map_text,
                            project_text=project_text,
                            env_text=env_text,
                            archive_tail=archive_tail,
                            archive_path=archive_path,
                            plan_transcript=self._plan_transcript,
                            tool_results=tool_result_blocks or None,
                        ),
                    },
                ],
                model=getattr(llm, "_plan_model", "deepseek-v4-flash"),
                temperature=0.0,
            )
            raw = response.content or ""
            return self._parse_operations_json(raw)

        try:
            operations, parsed_ok, reply, tool_calls = _one_llm_call()
        except Exception as exc:
            self._degradation_level = "L2"
            out = self._plan_channel_fallback(user_text, extra=f"LLM 调用失败（{exc}）")
            return self._finalize_plan_reply(out)

        from subagent import plan_subagent_tool_rounds

        tool_round_cap = plan_subagent_tool_rounds()
        for _round_index in range(tool_round_cap):
            if not parsed_ok:
                break
            if operations or reply:
                break
            if not tool_calls:
                break
            tool_result_blocks.extend(self._execute_plan_tool_calls(tool_calls))
            try:
                operations, parsed_ok, reply, tool_calls = _one_llm_call()
            except Exception as exc:
                out = self._plan_channel_fallback(user_text, extra=f"工具后 LLM 失败（{exc}）")
                return self._finalize_plan_reply(out)

        if not parsed_ok:
            out = self._plan_channel_fallback(user_text, extra="LLM 返回无法解析")
            return self._finalize_plan_reply(out)
        if not operations:
            if reply:
                lines = list(pre_actions)
                if tool_result_blocks:
                    lines.append("（已查跑）" + "；".join(tool_result_blocks[:2])[:200])
                lines.append(reply)
                out = "\n".join(lines)
                return self._finalize_plan_reply(out)
            prefix = "\n".join(pre_actions) if pre_actions else ""
            out = self._llm_noop_summary(user_text, prefix=prefix)
            return self._finalize_plan_reply(out)

        applied = list(pre_actions)
        if reply:
            applied.append(reply)
        applied.extend(self._apply_plan_operations(operations, reason_prefix="LLM"))
        self._save_state()
        for action in self.auto_fix():
            applied.append(action)

        if not applied:
            out = self._llm_noop_summary(user_text)
            return self._finalize_plan_reply(out)

        if self._pending_gated:
            applied.append(
                f"以上为提案（{len(self._pending_gated)} 条待审阅）；"
                "未写盘，审阅后可写入。"
            )
        out = "\n".join(applied)
        return self._finalize_plan_reply(out)

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
            from project_mode import get_delivery_profile

            if session is not None and get_delivery_profile(session.meta) == "solo":
                external_changes = False
            else:
                external_changes = True
                self._record_change(
                    "external", "(外部修改)", reason="TASKS.md changed outside PlanAgent"
                )
        # Always update snapshot after checking
        self._last_tasks_snapshot = current_tasks

        # Always auto_fix first so suggestion line numbers match post-fix file
        auto_fix_actions = self.auto_fix()
        try:
            from project_mode import migrate_closed_sections_to_archive

            migrated = migrate_closed_sections_to_archive(self.paths, self.project_id)
            if migrated:
                auto_fix_actions = list(auto_fix_actions) + [
                    f"已将 {migrated} 条「已关闭」区任务迁入 TASKS.archive.md"
                ]
        except Exception:
            pass
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
        for sug in self._active_milestone_suggestions.values():
            sid = str(sug.get("id") or "")
            if sid and sid not in self._ignored_suggestion_ids:
                if not any(s.get("id") == sid for s in self._suggestions):
                    self._suggestions.append(sug)
        # Merge gated proposals (PLAN-ARCH Q1) — survive across build_state rebuilds
        # M6: drop legacy line-number LLM cards left in state.json
        _legacy_actions = {
            "move_task",
            "drop_task",
            "skip_task",
            "reorder_task",
        }
        pruned_legacy = False
        for sid, sug in list(self._pending_gated.items()):
            if sid in self._ignored_suggestion_ids:
                self._pending_gated.pop(sid, None)
                continue
            action = str(sug.get("action") or "")
            if action in _legacy_actions and str(
                (sug.get("payload") or {}).get("source") or ""
            ) == "plan_llm":
                self._pending_gated.pop(sid, None)
                self._ignored_suggestion_ids.add(sid)
                pruned_legacy = True
                continue
            if not any(s.get("id") == sid for s in self._suggestions):
                self._suggestions.append(sug)
        self._last_suggestions = {s["id"]: s for s in self._suggestions if s.get("id")}
        for sid, sug in self._pending_gated.items():
            self._last_suggestions[sid] = sug
        if pruned_legacy:
            self._save_state()

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
            "plan_transcript_len": len(self._plan_transcript),
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

    def report_progress(
        self,
        task_line: int | None,
        summary: str,
        *,
        delivery_profile: str | None = None,
    ) -> dict[str, Any]:
        """Main agent reports completing a task. Verify with file output check."""
        import time as _time

        profile = normalize_delivery_profile(delivery_profile or "solo")

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
        toggle_result: dict[str, Any] | None = None
        if isinstance(resolved_line, int) and resolved_line >= 0:
            try:
                toggle_result = self.toggle_task(resolved_line, True)
            except Exception:
                # Fall through to change log so UI still sees the report attempt.
                pass
        if toggle_result:
            self._emit_milestone_review_if_needed(
                toggle_result, delivery_profile=profile
            )
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


def clear_plan_chat_on_enter(paths: AgentPaths, project_id: str) -> PlanAgent:
    """C6 / S-192: entering or switching to a project clears Plan transcript."""
    agent = get_plan_agent(paths, project_id)
    agent.clear_plan_transcript()
    return agent
