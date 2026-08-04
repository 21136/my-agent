"""Workspace project mode: roots, plan gate, task stats (PROJECT-MODE T-1102–T-1107)."""

from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths

ShellId = Literal["grow", "daily", "govern", "project"]
PlanStatus = Literal["", "draft", "confirmed", "plan_dirty"]
VALID_SHELLS = frozenset({"grow", "daily", "govern", "project"})
VALID_PLAN_STATUSES = frozenset({"", "draft", "confirmed", "plan_dirty"})

PROJECT_ARTIFACTS = frozenset({"PROJECT.md", "MAP.md", "TASKS.md"})
PLAN_DOMAIN_FILES = frozenset({"TASKS.md", "MAP.md", "PROJECT.md", "ENV.md"})
PLAN_DOMAIN_WRITE_BLOCK_MSG = (
    "计划域文件须通过 plan_partner 提案 + 侧栏采纳；"
    "或使用 report_progress 勾选已完成任务。"
)
TASKS_ARCHIVE_NAME = "TASKS.archive.md"
TASKS_INJECTION_OPEN_CAP = 20
CLOSE_REASONS = frozenset({"done", "wontfix", "duplicate", "moved"})
_TEMPLATE_DIRNAME = "_template"
_PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_TASK_OPEN_RE = re.compile(r"^\s*-\s*\[\s\]\s+", re.MULTILINE)
_TASK_DONE_RE = re.compile(r"^\s*-\s*\[x\]\s+", re.IGNORECASE | re.MULTILINE)
_TASK_ID_RE = re.compile(r"\bT-(\d+)\b", re.IGNORECASE)
_TASK_CHECKBOX_RE = re.compile(r"^\s*-\s*\[[ xX]\]\s+(.*)$")
_CLOSED_SECTION_TITLE_RE = re.compile(
    r"^(?:[\d.]+\s*)?(已关闭|归档|archive|closed|archives)\b",
    re.IGNORECASE,
)
_ACCEPT_CMD_RE = re.compile(r"命令[：:]\s*`([^`]+)`", re.IGNORECASE)
_ACCEPT_EXIT_RE = re.compile(r"退出码\s*(\d+)", re.IGNORECASE)
_PYTHON_SCRIPT_RE = re.compile(r"python(?:3)?\s+([^\s`]+\.py)", re.IGNORECASE)

_CODING_TOOLS = frozenset(
    {"run_python", "run_command", "run_tests", "run_demo", "patch_file"}
)
_WRITE_TOOLS = frozenset({"write_text", "append_text", "copy_move", "move_to_trash"})


class ProjectModeError(Exception):
    """Invalid project id or state."""


@dataclass(frozen=True, slots=True)
class TaskStats:
    done: int
    total: int

    @property
    def open_count(self) -> int:
        return max(0, self.total - self.done)

    @property
    def all_done(self) -> bool:
        return self.total > 0 and self.open_count == 0


@dataclass(frozen=True, slots=True)
class AcceptanceSpec:
    display: str
    script_rel: str
    expected_exit_code: int = 0


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_project_id(project_id: str) -> str:
    text = project_id.strip().lower().replace("_", "-")
    if not text or not _PROJECT_ID_RE.fullmatch(text):
        raise ProjectModeError(
            "project id must be lowercase letters, digits, hyphens; start with a letter"
        )
    if text in {_TEMPLATE_DIRNAME, "workspace"}:
        raise ProjectModeError(f"reserved project id: {text}")
    return text


def project_root_rel(project_id: str) -> str:
    pid = normalize_project_id(project_id)
    return f"workspace/{pid}"


def project_dir(paths: AgentPaths, project_id: str) -> Path:
    return paths.workspace / normalize_project_id(project_id)


def template_dir(paths: AgentPaths) -> Path:
    return paths.workspace / _TEMPLATE_DIRNAME


def list_projects(paths: AgentPaths) -> list[str]:
    root = paths.workspace
    if not root.is_dir():
        return []
    found: list[str] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name == _TEMPLATE_DIRNAME:
            continue
        if (entry / "TASKS.md").is_file():
            found.append(entry.name)
    return found


def ensure_template(paths: AgentPaths) -> Path:
    target = template_dir(paths)
    if target.is_dir() and (target / "TASKS.md").is_file():
        return target
    target.mkdir(parents=True, exist_ok=True)
    for name in sorted(PROJECT_ARTIFACTS):
        dest = target / name
        if not dest.is_file():
            dest.write_text(f"# template {name}\n", encoding="utf-8")
    return target


def create_project(paths: AgentPaths, project_id: str) -> Path:
    pid = normalize_project_id(project_id)
    dest = project_dir(paths, pid)
    if dest.exists() and any(dest.iterdir()):
        raise ProjectModeError(f"project already exists: workspace/{pid}")
    src = ensure_template(paths)
    dest.mkdir(parents=True, exist_ok=True)
    for name in (*PROJECT_ARTIFACTS, TASKS_ARCHIVE_NAME):
        src_file = src / name
        if src_file.is_file():
            shutil.copy2(src_file, dest / name)
        elif name in PROJECT_ARTIFACTS:
            (dest / name).write_text(f"# {pid} · {name}\n", encoding="utf-8")
    try:
        from project_env import ensure_project_env

        ensure_project_env(paths, pid)
    except Exception:
        pass
    return dest


def read_task_stats(tasks_path: Path) -> TaskStats:
    if not tasks_path.is_file():
        return TaskStats(done=0, total=0)
    text = tasks_path.read_text(encoding="utf-8")
    # Open / legacy [x] only outside closed sections (PLAN-ARCH)
    open_count = 0
    legacy_done = 0
    for _i, line in iter_tasks_lines_skipping_closed(text):
        if _TASK_OPEN_RE.match(line):
            open_count += 1
        elif _TASK_DONE_RE.match(line):
            legacy_done += 1
    archive_done = count_archive_entries(
        tasks_path.parent / TASKS_ARCHIVE_NAME,
        reason="done",
    )
    done = legacy_done + archive_done
    return TaskStats(done=done, total=done + open_count)


def normalize_close_reason(reason: str) -> str:
    key = (reason or "").strip().lower().replace("-", "").replace("_", "")
    mapping = {
        "done": "done",
        "wontfix": "wontfix",
        "wont": "wontfix",
        "duplicate": "duplicate",
        "dup": "duplicate",
        "moved": "moved",
        "move": "moved",
    }
    # also accept closed:done style
    raw = (reason or "").strip().lower()
    if raw.startswith("closed:"):
        raw = raw.split(":", 1)[1].strip()
    if raw in CLOSE_REASONS:
        return raw
    if key in mapping:
        return mapping[key]
    raise ProjectModeError(
        f"invalid close reason {reason!r}; expected one of {sorted(CLOSE_REASONS)}"
    )


def format_archive_entry(
    *,
    body: str,
    reason: str,
    phase: str = "",
    source: str = "",
    closed_at: str | None = None,
) -> str:
    reason_n = normalize_close_reason(reason)
    ts = closed_at or utc_now_iso()
    parts = [f"- {body.strip()}", f"closed:{reason_n}", ts]
    if phase.strip():
        parts.append(f"phase:{phase.strip()}")
    if source.strip():
        parts.append(f"src:{source.strip()[:40]}")
    return " · ".join(parts)


def count_archive_entries(archive_path: Path, *, reason: str | None = None) -> int:
    if not archive_path.is_file():
        return 0
    try:
        text = archive_path.read_text(encoding="utf-8")
    except OSError:
        return 0
    n = 0
    want = f"closed:{reason}" if reason else None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        if want is None:
            if "closed:" in stripped:
                n += 1
        elif want in stripped:
            n += 1
    return n


def append_tasks_archive(
    paths: AgentPaths,
    project_id: str,
    *,
    body: str,
    reason: str,
    phase: str = "",
    source: str = "",
) -> Path:
    """Append one closed task to TASKS.archive.md (create file if needed)."""
    root = project_dir(paths, project_id)
    archive_path = root / TASKS_ARCHIVE_NAME
    entry = format_archive_entry(body=body, reason=reason, phase=phase, source=source)
    if archive_path.is_file():
        existing = archive_path.read_text(encoding="utf-8")
        if not existing.endswith("\n"):
            existing += "\n"
        archive_path.write_text(existing + entry + "\n", encoding="utf-8")
    else:
        header = (
            f"# {project_id} · 任务归档\n\n"
            "> 只追加。默认不注入 LLM（PLAN-ARCH Q2）。"
            "关闭理由：done / wontfix / duplicate / moved。\n\n"
        )
        archive_path.write_text(header + entry + "\n", encoding="utf-8")
    return archive_path


def _phase_for_line(file_lines: list[str], line: int) -> str:
    current = ""
    for i in range(0, min(line + 1, len(file_lines))):
        stripped = file_lines[i].strip()
        if stripped.startswith("## "):
            current = stripped.lstrip("#").strip()
    return current


def archive_and_remove_task_line(
    paths: AgentPaths,
    project_id: str,
    line: int,
    *,
    reason: str,
    source: str = "",
) -> dict[str, Any]:
    """Remove a checkbox line from TASKS.md and append to TASKS.archive.md."""
    tasks_path = project_dir(paths, project_id) / "TASKS.md"
    if not tasks_path.is_file():
        raise ProjectModeError(f"TASKS.md not found for project {project_id}")
    file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
    if line < 0 or line >= len(file_lines):
        raise ProjectModeError(f"line {line} out of range (0–{len(file_lines) - 1})")
    target = file_lines[line]
    if not (_TASK_OPEN_RE.match(target) or _TASK_DONE_RE.match(target)):
        raise ProjectModeError(f"line {line} is not a task checkbox")
    m = _TASK_CHECKBOX_RE.match(target)
    body = m.group(1).strip() if m else target.strip()
    phase = _phase_for_line(file_lines, line)
    reason_n = normalize_close_reason(reason)
    append_tasks_archive(
        paths,
        project_id,
        body=body,
        reason=reason_n,
        phase=phase,
        source=source,
    )
    file_lines.pop(line)
    content = "\n".join(file_lines)
    if not content.endswith("\n"):
        content += "\n"
    tasks_path.write_text(content, encoding="utf-8")
    stats = read_task_stats(tasks_path)
    return {
        "type": "project.task.archive.done",
        "line": line,
        "body": body,
        "phase": phase,
        "reason": reason_n,
        "removed": target.strip(),
        "tasks_done": stats.done,
        "tasks_total": stats.total,
        "open_line": f"- [ ] {body}",
    }


def migrate_closed_sections_to_archive(paths: AgentPaths, project_id: str) -> int:
    """Move ## 已关闭 / Archive section tasks into TASKS.archive.md."""
    tasks_path = project_dir(paths, project_id) / "TASKS.md"
    if not tasks_path.is_file():
        return 0
    file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
    keep: list[str] = []
    moved = 0
    in_closed = False
    current_phase = ""
    for line in file_lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            title = stripped.lstrip("#").strip()
            if is_closed_section_title(title):
                in_closed = True
                current_phase = title
                continue  # drop closed header
            in_closed = False
            current_phase = title
            keep.append(line)
            continue
        if in_closed:
            if _TASK_OPEN_RE.match(line) or _TASK_DONE_RE.match(line):
                m = _TASK_CHECKBOX_RE.match(line)
                body = m.group(1).strip() if m else line.strip()
                reason = "done" if _TASK_DONE_RE.match(line) else "wontfix"
                append_tasks_archive(
                    paths,
                    project_id,
                    body=body,
                    reason=reason,
                    phase=current_phase,
                    source="migrate_closed",
                )
                moved += 1
            # skip non-task lines inside closed section too
            continue
        keep.append(line)
    if moved:
        content = "\n".join(keep)
        if not content.endswith("\n"):
            content += "\n"
        tasks_path.write_text(content, encoding="utf-8")
    return moved


def build_project_goal(*, project_root: str, plan_status: str) -> str:
    return "\n".join(
        [
            f"项目根：{project_root}",
            f"进度真源：{project_root}/TASKS.md（开放队列）",
            f"归档：{project_root}/TASKS.archive.md",
            f"地图：{project_root}/MAP.md",
            f"计划状态：{plan_status or 'draft'}",
        ]
    )


def normalize_meta_path(path: str) -> str:
    return path.strip().replace("\\", "/").strip("/")


def _path_text(value: object) -> str:
    return value.strip().replace("\\", "/").lstrip("/") if isinstance(value, str) else ""


def project_id_from_root(project_root: str) -> str:
    root = normalize_meta_path(project_root)
    if root.startswith("workspace/"):
        return root[len("workspace/") :].split("/", 1)[0]
    return root.split("/", 1)[0] if root else ""


def project_path_rel(path: str, project_root: str) -> str | None:
    """Return path relative to project root, or None if outside.

    Accepts both ``workspace/<id>/…`` (meta style) and ``<id>/…``
    (write_text workspace-relative style).
    """
    normalized = _path_text(path)
    root = normalize_meta_path(project_root)
    if not normalized or not root:
        return None
    if normalized == root:
        return ""
    if normalized.startswith(f"{root}/"):
        return normalized[len(root) + 1 :]
    pid = project_id_from_root(root)
    if pid:
        if normalized == pid:
            return ""
        if normalized.startswith(f"{pid}/"):
            return normalized[len(pid) + 1 :]
    return None


def is_active_project(meta: object) -> bool:
    root = getattr(meta, "project_root", "") or ""
    shell = getattr(meta, "active_shell", "") or ""
    return bool(root.strip()) and shell == "project"


def project_plan_gate_open(meta: object) -> bool:
    """True when session is bound to a project but plan is not confirmed."""
    root = getattr(meta, "project_root", "") or ""
    if not str(root).strip():
        return False
    status = getattr(meta, "project_plan_status", "") or "draft"
    return not plan_allows_code_writes(str(status))


def plan_allows_code_writes(plan_status: str) -> bool:
    return plan_status == "confirmed"


def is_under_project_root(path: str, project_root: str) -> bool:
    return project_path_rel(path, project_root) is not None


def is_project_artifact_path(path: str, project_root: str) -> bool:
    rel = project_path_rel(path, project_root)
    if rel is None or not rel or "/" in rel:
        return False
    return rel in PROJECT_ARTIFACTS


def is_project_tasks_path(path: str, project_root: str) -> bool:
    rel = project_path_rel(path, project_root)
    return rel in {"TASKS.md", TASKS_ARCHIVE_NAME}


def is_plan_domain_path(path: str, project_root: str) -> bool:
    """True when path targets a plan-domain basename under project root (Phase 39 B5)."""
    rel = project_path_rel(path, project_root)
    if rel is None or not rel or "/" in rel:
        return False
    return rel in PLAN_DOMAIN_FILES or rel == TASKS_ARCHIVE_NAME


def main_agent_plan_domain_write_block(
    *,
    project_root: str,
    tool_name: str,
    arguments: dict[str, object],
) -> str | None:
    """B5: main Agent must not write TASKS/MAP/PROJECT/ENV directly."""
    root = project_root.strip()
    if not root or tool_name != "run_evolved":
        return None
    evolved = arguments.get("tool_name")
    evolved_name = evolved.strip() if isinstance(evolved, str) else ""
    if evolved_name in _WRITE_TOOLS:
        for path in extract_run_evolved_paths(tool_name, arguments):
            if path and (is_plan_domain_path(path, root) or is_project_tasks_path(path, root)):
                return PLAN_DOMAIN_WRITE_BLOCK_MSG
    if evolved_name == "patch_file":
        for path in extract_run_evolved_paths(tool_name, arguments):
            if path and is_plan_domain_path(path, root):
                return PLAN_DOMAIN_WRITE_BLOCK_MSG
    return None


_CONTINUE_UTTERANCE_RE = re.compile(
    r"^\s*(继续|下一\s*task|下一项|开始下一项|开始编码)\s*[。.!！]?$",
    re.IGNORECASE,
)


def is_project_continue_utterance(text: str) -> bool:
    """True when user asks to continue the next TASKS checkbox (TASK-STOP S6)."""
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped:
        return False
    if _CONTINUE_UTTERANCE_RE.fullmatch(stripped):
        return True
    # Short prefixes still count as continue intent.
    lowered = stripped.lower()
    for prefix in ("继续", "下一 task", "下一task", "下一项", "开始下一项", "开始编码"):
        if stripped.startswith(prefix) or lowered.startswith(prefix.lower()):
            if len(stripped) <= 24:
                return True
    return False


def first_open_task_line(tasks_text: str) -> str | None:
    for _i, line in iter_tasks_lines_skipping_closed(tasks_text):
        if _TASK_OPEN_RE.match(line):
            return line.strip()
    return None


def first_open_task(tasks_text: str) -> tuple[int | None, str | None, str | None]:
    """Return (line_idx, body, tid) for the first open checkbox, else (None, None, None)."""
    for i, line in iter_tasks_lines_skipping_closed(tasks_text):
        if not _TASK_OPEN_RE.match(line):
            continue
        m = _TASK_CHECKBOX_RE.match(line)
        body = m.group(1).strip() if m else line.strip()
        return i, body, extract_task_id(body)
    return None, None, None


def is_closed_section_title(title: str) -> bool:
    """True for PLAN-ARCH closed/archive section headers (LLM-invisible)."""
    key = (title or "").strip()
    if not key:
        return False
    return bool(_CLOSED_SECTION_TITLE_RE.match(key))


def is_tasks_archive_filename(name: str) -> bool:
    return Path(str(name or "")).name.replace("\\", "/") == TASKS_ARCHIVE_NAME


def iter_tasks_lines_skipping_closed(tasks_text: str) -> list[tuple[int, str]]:
    """Yield (original_line_index, line) skipping closed/archive sections."""
    out: list[tuple[int, str]] = []
    in_closed = False
    for i, line in enumerate((tasks_text or "").splitlines()):
        stripped = line.strip()
        if stripped.startswith("## "):
            title = stripped.lstrip("#").strip()
            in_closed = is_closed_section_title(title)
            if in_closed:
                continue
        if in_closed:
            continue
        out.append((i, line))
    return out


def _phase_title_at(lines: list[tuple[int, str]], pos: int) -> str | None:
    current: str | None = None
    for j in range(0, pos + 1):
        stripped = lines[j][1].strip()
        if stripped.startswith("## "):
            current = stripped.lstrip("#").strip()
    return current


def select_open_task_indices_for_injection(
    tasks_text: str,
    *,
    open_cap: int = TASKS_INJECTION_OPEN_CAP,
) -> list[int]:
    """Original line indices of open checkboxes to inject (active phase first)."""
    visible = iter_tasks_lines_skipping_closed(tasks_text)
    if not visible:
        return []
    active = active_phase_title_from_lines([ln for _, ln in visible])
    active_idxs: list[int] = []
    other_idxs: list[int] = []
    for vis_i, (orig_i, line) in enumerate(visible):
        if not _TASK_OPEN_RE.match(line):
            continue
        phase = _phase_title_at(visible, vis_i)
        if active and phase and phase.lower() == active.lower():
            active_idxs.append(orig_i)
        else:
            other_idxs.append(orig_i)
    ordered = active_idxs + other_idxs
    cap = max(0, int(open_cap))
    return ordered[:cap] if cap else []


def build_tasks_injection_slice(
    tasks_text: str,
    *,
    open_cap: int = TASKS_INJECTION_OPEN_CAP,
) -> str:
    """Open-queue markdown for LLM injection (PLAN-ARCH A3 · M1).

    Excludes closed sections and ``[x]`` lines. Caps open checkboxes.
    Never includes ``TASKS.archive.md`` (caller must not concatenate it).
    """
    selected = set(select_open_task_indices_for_injection(tasks_text, open_cap=open_cap))
    visible = iter_tasks_lines_skipping_closed(tasks_text)
    if not visible:
        return ""

    # done counts per phase (visible only) for omit notes
    done_by_phase: dict[str, int] = {}
    open_by_phase: dict[str, int] = {}
    for vis_i, (_orig_i, line) in enumerate(visible):
        phase = _phase_title_at(visible, vis_i) or ""
        if _TASK_DONE_RE.match(line):
            done_by_phase[phase] = done_by_phase.get(phase, 0) + 1
        elif _TASK_OPEN_RE.match(line):
            open_by_phase[phase] = open_by_phase.get(phase, 0) + 1

    out: list[str] = []
    emitted_phase: set[str] = set()
    omitted_open = max(0, sum(open_by_phase.values()) - len(selected))

    for vis_i, (orig_i, line) in enumerate(visible):
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            out.append(stripped)
            continue
        if stripped.startswith("## "):
            title = stripped.lstrip("#").strip()
            # Emit phase header only when we will show opens under it
            will_show = any(
                oi in selected
                and (_phase_title_at(visible, vi) or "") == title
                for vi, (oi, _) in enumerate(visible)
            )
            if will_show and title not in emitted_phase:
                out.append(f"## {title}")
                emitted_phase.add(title)
                done_n = done_by_phase.get(title, 0)
                if done_n:
                    out.append(f"（本 Phase {done_n} 条已完成已省略）")
            continue
        if orig_i in selected:
            phase = _phase_title_at(visible, vis_i) or ""
            if phase and phase not in emitted_phase:
                out.append(f"## {phase}")
                emitted_phase.add(phase)
                done_n = done_by_phase.get(phase, 0)
                if done_n:
                    out.append(f"（本 Phase {done_n} 条已完成已省略）")
            out.append(line.rstrip())

    if omitted_open:
        out.append(f"（另有 {omitted_open} 条开放项未注入 · cap={open_cap}）")
    out.append(
        "（注入切片：不含 [x] / 不含已关闭区 / 不含 TASKS.archive.md；"
        "全文以磁盘为准，按需 read_file）"
    )
    return "\n".join(out).strip()


def format_tasks_open_slice_numbered(
    tasks_text: str,
    *,
    open_cap: int = TASKS_INJECTION_OPEN_CAP,
) -> str:
    """Plan-Agent view: original line numbers for open items + phase headers only."""
    selected = set(select_open_task_indices_for_injection(tasks_text, open_cap=open_cap))
    visible = iter_tasks_lines_skipping_closed(tasks_text)
    if not visible:
        return "（无开放项）"

    done_by_phase: dict[str, int] = {}
    for vis_i, (_orig_i, line) in enumerate(visible):
        if _TASK_DONE_RE.match(line):
            phase = _phase_title_at(visible, vis_i) or ""
            done_by_phase[phase] = done_by_phase.get(phase, 0) + 1

    out: list[str] = []
    emitted_phase: set[str] = set()
    for vis_i, (orig_i, line) in enumerate(visible):
        stripped = line.strip()
        if stripped.startswith("## "):
            title = stripped.lstrip("#").strip()
            will_show = any(
                oi in selected
                and (_phase_title_at(visible, vi) or "") == title
                for vi, (oi, _) in enumerate(visible)
            )
            if will_show and title not in emitted_phase:
                out.append(f"{orig_i}|{line.rstrip()}")
                emitted_phase.add(title)
                done_n = done_by_phase.get(title, 0)
                if done_n:
                    out.append(f"#|（本 Phase {done_n} 条 [x] 已省略）")
            continue
        if orig_i in selected:
            phase = _phase_title_at(visible, vis_i) or ""
            if phase and phase not in emitted_phase:
                # synthetic: find header line index
                header_i = None
                for back in range(vis_i, -1, -1):
                    if visible[back][1].strip().startswith("## "):
                        header_i = visible[back][0]
                        break
                if header_i is not None:
                    out.append(f"{header_i}|## {phase}")
                else:
                    out.append(f"#|## {phase}")
                emitted_phase.add(phase)
                done_n = done_by_phase.get(phase, 0)
                if done_n:
                    out.append(f"#|（本 Phase {done_n} 条 [x] 已省略）")
            out.append(f"{orig_i}|{line.rstrip()}")

    if not selected:
        return "（无开放项；已关闭区与 [x] 未注入）"
    out.append(
        "#|（注入切片：行号=原 TASKS.md；不含已关闭区 / TASKS.archive.md）"
    )
    return "\n".join(out)


def read_tasks_text_for_injection(tasks_path: Path | None) -> str:
    """Read TASKS.md only — never TASKS.archive.md (PLAN-ARCH Q2 / IT-180)."""
    if tasks_path is None:
        return ""
    path = Path(tasks_path)
    if is_tasks_archive_filename(path.name):
        return ""
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def task_stop_block_reason(
    *,
    active_shell: str,
    project_root: str,
    task_stop_armed: bool,
    tool_name: str,
    arguments: dict[str, object],
) -> str | None:
    """Block product writes after a TASKS checkbox was completed this turn (S5/S10)."""
    if not task_stop_armed or active_shell != "project":
        return None
    root = project_root.strip()
    if not root:
        return None
    if tool_name != "run_evolved":
        return None
    evolved = arguments.get("tool_name")
    evolved_name = evolved.strip() if isinstance(evolved, str) else ""
    if evolved_name not in _WRITE_TOOLS and evolved_name != "patch_file":
        return None
    for path in extract_run_evolved_paths(tool_name, arguments):
        if not path:
            continue
        rel = project_path_rel(path, root)
        if rel is None:
            continue
        if is_project_artifact_path(path, root):
            continue
        return (
            "[guard] 本轮已完成一条 TASKS 勾选（task 一停门）；"
            "请结束本回合，用户回复「继续」后再写下一 task 产物。"
            f" 被拒路径: {path}"
        )
    return None


def extract_run_evolved_paths(tool_name: str, arguments: dict[str, object]) -> list[str]:
    if tool_name != "run_evolved":
        return []
    paths: list[str] = []
    outer_path = arguments.get("path")
    if isinstance(outer_path, str) and outer_path.strip():
        paths.append(outer_path)
    inner = arguments.get("arguments")
    if isinstance(inner, dict):
        inner_path = inner.get("path")
        if isinstance(inner_path, str) and inner_path.strip():
            paths.append(inner_path)
        inner_dest = inner.get("dest")
        if isinstance(inner_dest, str) and inner_dest.strip():
            paths.append(inner_dest)
    return paths


def project_mode_block_reason(
    *,
    active_shell: str,
    project_root: str,
    plan_status: str,
    tool_name: str,
    arguments: dict[str, object],
) -> str | None:
    """Return user-facing block reason, or None if allowed."""
    root = project_root.strip()
    evolved = arguments.get("tool_name") if tool_name == "run_evolved" else None
    evolved_name = evolved.strip() if isinstance(evolved, str) else ""

    if active_shell == "project" and tool_name == "run_evolved" and evolved_name == "write_evolve":
        return "project 模式禁止 write_evolve；请切换到 grow 壳沉淀能力"

    if active_shell == "project" and tool_name == "run_evolved" and evolved_name == "git_clone":
        inner = arguments.get("arguments")
        if isinstance(inner, dict) and inner.get("target") == "evolve_tools":
            return "project 模式禁止向 evolve/tools clone；请切换到 grow 壳沉淀能力"

    if not root:
        return None

    plan_domain_block = main_agent_plan_domain_write_block(
        project_root=root,
        tool_name=tool_name,
        arguments=arguments,
    )
    if plan_domain_block:
        return plan_domain_block

    if plan_allows_code_writes(plan_status):
        if tool_name == "run_evolved" and evolved_name == "patch_file":
            for path in extract_run_evolved_paths(tool_name, arguments):
                if path and not is_under_project_root(path, project_root):
                    return f"patch_file 仅限项目目录内：{project_root}"

        return None

    # Plan gate: any session bound to project_root — even if router switched shell.
    if tool_name == "run_evolved":
        if evolved_name in _CODING_TOOLS:
            return (
                f"计划未确认（{plan_status or 'draft'}）；"
                "请先完成 PROJECT/TASKS 并请用户「项目 确认」后再写代码或 run_command"
            )
        if evolved_name in _WRITE_TOOLS or evolved_name == "git_clone":
            for path in extract_run_evolved_paths(tool_name, arguments):
                if not path:
                    continue
                if is_under_project_root(path, project_root) and not is_project_artifact_path(
                    path, project_root
                ):
                    return (
                        "计划未确认：仅可修改 PROJECT.md / MAP.md / TASKS.md；"
                        "确认后请「项目 确认」"
                    )
        if evolved_name == "patch_file":
            return "计划未确认：patch_file 已禁用；请先「项目 确认」"

    return None


def format_project_overlay(
    *,
    project_root: str,
    project_id: str,
    plan_status: str,
    task_stats: TaskStats | None = None,
    continue_turn: bool = False,
    next_open_task: str | None = None,
    armed_task_id: str | None = None,
    open_tasks_slice: str | None = None,
) -> str:
    lines = [
        "[项目模式 · project]",
        f"project_root: {project_root}",
        f"project_id: {project_id}",
        f"project_plan_status: {plan_status or 'draft'}",
    ]
    if plan_status != "confirmed":
        lines.append(
            "plan_gate: 未确认 — 仅可编辑三件套；用户须「项目 确认」后才可写源码/run_python"
        )
    else:
        lines.append(
            "plan_gate: 已确认 — 可写项目内代码；"
            "用 report_progress 勾选，禁止直写 TASKS.md"
        )
        lines.append(
            "task_stop: 每完成一条 TASKS 勾选必须停；用户「继续」后再做下一项"
        )
    if task_stats is not None:
        lines.append(f"tasks: {task_stats.done}/{task_stats.total} done")
    if plan_status == "confirmed":
        if next_open_task:
            lines.append(f"current_task: {next_open_task}")
        armed = (armed_task_id or "").strip() or extract_task_id(next_open_task or "")
        if armed:
            lines.append(f"armed_task_id: {armed}")
            lines.append(
                "report_progress: 勾选须本回合对口工具成功证据；"
                "身份由内核注入 armed_task_id/task_text；勿只信 task_line / 口头旧凭证"
            )
        slice_text = (open_tasks_slice or "").strip()
        if slice_text:
            lines.append("open_queue:")
            lines.append(slice_text)
        lines.append(
            "plan_inject: 默认只注入开放队列切片；"
            "不含 [x]、已关闭区、TASKS.archive.md"
        )
    if continue_turn and plan_status == "confirmed":
        lines.append(
            "continue_turn: 本轮为「继续」— 只做第一条未勾选 task，完成后标 [x] 并停"
        )
    return "\n".join(lines)


def load_project_prompt(evolve_dir: Path) -> str:
    path = evolve_dir / "prompts" / "project.md"
    if not path.is_file():
        return "[project.md missing at evolve/prompts/project.md]"
    return path.read_text(encoding="utf-8").strip()


def phase_fingerprint_from_text(text: str) -> str:
    """Fingerprint ## Phase headers only (checkbox toggles do not change this)."""
    headers = [line.strip() for line in text.splitlines() if line.strip().startswith("## ")]
    return json.dumps(headers, ensure_ascii=False)


def project_doc_fingerprint(path: Path) -> str:
    """Fingerprint structural plan fields only (not every PROJECT.md edit)."""
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    headers = [line.strip() for line in text.splitlines() if line.strip().startswith("## ")]
    acceptance = _acceptance_section(text).strip()
    return json.dumps({"headers": headers, "acceptance": acceptance}, ensure_ascii=False)


def read_project_artifacts(paths: AgentPaths, project_id: str) -> dict[str, str]:
    root = project_dir(paths, project_id)
    out: dict[str, str] = {}
    for name in PROJECT_ARTIFACTS:
        file_path = root / name
        if file_path.is_file():
            try:
                out[name] = file_path.read_text(encoding="utf-8")
            except OSError:
                out[name] = ""
    return out


def _acceptance_section(project_md: str) -> str:
    marker = "## 验收标准"
    idx = project_md.find(marker)
    if idx < 0:
        return project_md
    return project_md[idx:]


def parse_acceptance_spec(project_md: str) -> AcceptanceSpec | None:
    """Parse first `命令：`…`` line under ## 验收标准."""
    section = _acceptance_section(project_md)
    for line in section.splitlines():
        match = _ACCEPT_CMD_RE.search(line)
        if not match:
            continue
        command = match.group(1).strip()
        script_match = _PYTHON_SCRIPT_RE.search(command)
        if not script_match:
            continue
        script = script_match.group(1).strip().replace("\\", "/").lstrip("/")
        if script.startswith("workspace/"):
            script = script.removeprefix("workspace/")
        exit_match = _ACCEPT_EXIT_RE.search(line)
        expected = int(exit_match.group(1)) if exit_match else 0
        return AcceptanceSpec(
            display=command,
            script_rel=script,
            expected_exit_code=expected,
        )
    return None


def acceptance_workspace_path(project_id: str, spec: AcceptanceSpec) -> str:
    script = spec.script_rel.replace("\\", "/").lstrip("/")
    pid = normalize_project_id(project_id)
    if script.startswith(f"{pid}/"):
        return script
    return f"{pid}/{script}"


def acceptance_script_exists(paths: AgentPaths, project_id: str, spec: AcceptanceSpec) -> bool:
    rel = acceptance_workspace_path(project_id, spec)
    return (paths.workspace / rel).is_file()


def run_acceptance_check(
    paths: AgentPaths,
    project_id: str,
    spec: AcceptanceSpec,
) -> dict[str, Any]:
    """Run PROJECT.md acceptance via run_command (python script)."""
    import sys

    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    rel = acceptance_workspace_path(project_id, spec)
    script_path = paths.workspace / rel
    if not script_path.is_file():
        return {
            "ok": False,
            "passed": False,
            "error": f"验收脚本不存在：workspace/{rel}",
            "command": spec.display,
            "path": f"workspace/{rel}",
            "expected_exit_code": spec.expected_exit_code,
        }

    pid = normalize_project_id(project_id)
    registry = ToolRegistry.load(paths)
    # Prefer quoting that works under PowerShell -Command and bash -lc.
    script_abs = str(script_path.resolve())
    py = sys.executable
    if sys.platform == "win32":
        command = f'& "{py}" "{script_abs}"'
    else:
        command = f'"{py}" "{script_abs}"'
    tool_result = run(
        {
            "tool_name": "run_command",
            "arguments": {
                "command": command,
                "working_dir": f"workspace/{pid}",
            },
        },
        registry=registry,
    )
    if not tool_result.ok:
        message = tool_result.error.message if tool_result.error else "run_command failed"
        return {
            "ok": False,
            "passed": False,
            "error": message,
            "command": spec.display,
            "path": f"workspace/{rel}",
            "expected_exit_code": spec.expected_exit_code,
        }

    data = tool_result.data or {}
    exit_code = int(data.get("exit_code", -1))
    passed = exit_code == spec.expected_exit_code
    return {
        "ok": True,
        "passed": passed,
        "exit_code": exit_code,
        "expected_exit_code": spec.expected_exit_code,
        "command": spec.display,
        "path": f"workspace/{rel}",
        "stdout": data.get("stdout", ""),
        "stderr": data.get("stderr", ""),
    }


def snapshot_plan_fingerprints(session: object, paths: AgentPaths, project_id: str) -> None:
    """Store phase / PROJECT.md fingerprints on plan confirm."""
    artifacts = read_project_artifacts(paths, project_id)
    tasks_text = artifacts.get("TASKS.md", "")
    meta = session.meta  # type: ignore[attr-defined]
    meta.project_phase_fingerprint = phase_fingerprint_from_text(tasks_text)
    meta.project_doc_fingerprint = project_doc_fingerprint(project_dir(paths, project_id) / "PROJECT.md")


def sync_plan_dirty_if_structure_changed(session: object, paths: AgentPaths) -> bool:
    """If confirmed plan structure drifted, set plan_dirty. Returns True if changed."""
    meta = session.meta  # type: ignore[attr-defined]
    if meta.project_plan_status != "confirmed":
        return False
    pid = (meta.project_id or "").strip()
    if not pid:
        return False
    artifacts = read_project_artifacts(paths, pid)
    tasks_fp = phase_fingerprint_from_text(artifacts.get("TASKS.md", ""))
    doc_fp = project_doc_fingerprint(project_dir(paths, pid) / "PROJECT.md")
    stored_phase = getattr(meta, "project_phase_fingerprint", "") or ""
    stored_doc = getattr(meta, "project_doc_fingerprint", "") or ""
    if tasks_fp != stored_phase or doc_fp != stored_doc:
        meta.project_plan_status = "plan_dirty"
        return True
    return False


def normalize_task_id(raw: str | None) -> str | None:
    """Return canonical `T-011` from `T-011` / `t-11` / bare digits, else None."""
    if not raw or not str(raw).strip():
        return None
    text = str(raw).strip()
    m = _TASK_ID_RE.search(text)
    if m:
        return f"T-{m.group(1)}"
    if text.isdigit():
        return f"T-{text}"
    return None


def extract_task_id(*texts: str | None) -> str | None:
    """First `T-NNN` token across texts, preserving original digit width (T-011)."""
    for text in texts:
        if not text:
            continue
        m = _TASK_ID_RE.search(str(text))
        if m:
            return f"T-{m.group(1)}"
    return None


def find_task_line_by_id(
    paths: AgentPaths,
    project_id: str,
    task_id: str,
    *,
    prefer_open: bool = True,
) -> int | None:
    """0-indexed TASKS.md line whose checkbox text contains task_id (e.g. T-011)."""
    tid = extract_task_id(task_id) or normalize_task_id(task_id)
    if not tid:
        return None
    tasks_path = project_dir(paths, project_id) / "TASKS.md"
    if not tasks_path.is_file():
        return None
    file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
    open_hit: int | None = None
    any_hit: int | None = None
    token_re = re.compile(rf"\b{re.escape(tid)}\b", re.IGNORECASE)
    for i, raw in enumerate(file_lines):
        m = _TASK_CHECKBOX_RE.match(raw)
        if not m:
            continue
        body = m.group(1)
        if not token_re.search(body):
            continue
        if any_hit is None:
            any_hit = i
        if prefer_open and _TASK_OPEN_RE.match(raw):
            open_hit = i
            break
    if prefer_open and open_hit is not None:
        return open_hit
    return any_hit


def resolve_progress_task_line(
    paths: AgentPaths,
    project_id: str,
    *,
    task_line: int | None = None,
    task_id: str | None = None,
    summary: str = "",
    task_text: str | None = None,
) -> tuple[int | None, str]:
    """Resolve which TASKS.md line to toggle for report_progress.

    Prefer stable ``T-NNN`` / ``task_text`` over raw ``task_line`` (Plan Partner
    inserts shift line numbers; LLMs also pass stale indices after re-read).
    When identity is known, never toggle a different line because of task_line.
    """
    tid = extract_task_id(task_id, summary, task_text)
    id_line = find_task_line_by_id(paths, project_id, tid) if tid else None

    line_text = ""
    if isinstance(task_line, int) and task_line >= 0:
        tasks_path = project_dir(paths, project_id) / "TASKS.md"
        if tasks_path.is_file():
            file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
            if 0 <= task_line < len(file_lines):
                line_text = file_lines[task_line]

    if id_line is not None:
        if isinstance(task_line, int) and task_line >= 0 and task_line != id_line:
            return (
                id_line,
                f"resolved {tid} at line {id_line} (ignored stale task_line={task_line})",
            )
        return id_line, f"resolved {tid} at line {id_line}"

    if task_text and str(task_text).strip():
        needle = str(task_text).strip()
        tasks_path = project_dir(paths, project_id) / "TASKS.md"
        if tasks_path.is_file():
            file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
            exact_hit: int | None = None
            contains_hit: int | None = None
            for i, raw in enumerate(file_lines):
                if not _TASK_OPEN_RE.match(raw):
                    continue
                m = _TASK_CHECKBOX_RE.match(raw)
                if not m:
                    continue
                body = m.group(1).strip()
                if body.lower() == needle.lower():
                    exact_hit = i
                    break
                if contains_hit is None and needle.lower() in body.lower():
                    contains_hit = i
            hit = exact_hit if exact_hit is not None else contains_hit
            if hit is not None:
                if isinstance(task_line, int) and task_line >= 0 and task_line != hit:
                    return (
                        hit,
                        f"resolved by task_text at line {hit} "
                        f"(ignored stale task_line={task_line})",
                    )
                return hit, f"resolved by task_text at line {hit}"

    if isinstance(task_line, int) and task_line >= 0:
        if tid and line_text and not re.search(rf"\b{re.escape(tid)}\b", line_text, re.I):
            return (
                None,
                f"refused: task_line={task_line} does not contain {tid} "
                f"and no matching id line found",
            )
        return task_line, ""

    return None, "no task_line/task_id/task_text to resolve"


def toggle_task_line(paths: AgentPaths, project_id: str, line: int, done: bool) -> dict[str, Any]:
    """Toggle a checkbox. When marking done, archive+remove (PLAN-ARCH M3)."""
    if done:
        result = archive_and_remove_task_line(
            paths,
            project_id,
            line,
            reason="done",
            source="toggle",
        )
        result["type"] = "project.task.toggle.done"
        result["done"] = True
        return result

    tasks_path = project_dir(paths, project_id) / "TASKS.md"
    if not tasks_path.is_file():
        raise ProjectModeError(f"TASKS.md not found for project {project_id}")

    file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
    if line < 0 or line >= len(file_lines):
        raise ProjectModeError(f"line {line} out of range (0–{len(file_lines) - 1})")

    target = file_lines[line]
    new_line = _TASK_DONE_RE.sub("- [ ] ", target, count=1)
    if new_line == target:
        raise ProjectModeError(f"line {line} is not a completed task checkbox")

    file_lines[line] = new_line
    content = "\n".join(file_lines)
    if not content.endswith("\n"):
        content += "\n"
    tasks_path.write_text(content, encoding="utf-8")

    stats = read_task_stats(tasks_path)
    return {
        "type": "project.task.toggle.done",
        "line": line,
        "done": False,
        "tasks_done": stats.done,
        "tasks_total": stats.total,
    }


def find_task_line_range(tasks_path: Path, line: int) -> tuple[int, int] | None:
    """Find the range of contiguous task lines containing `line`. Returns (start, end) or None."""
    file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
    if line < 0 or line >= len(file_lines):
        return None
    if not (_TASK_OPEN_RE.match(file_lines[line]) or _TASK_DONE_RE.match(file_lines[line])):
        return None

    start = line
    while start > 0 and (_TASK_OPEN_RE.match(file_lines[start - 1]) or _TASK_DONE_RE.match(file_lines[start - 1])):
        start -= 1

    end = line
    while end < len(file_lines) - 1 and (_TASK_OPEN_RE.match(file_lines[end + 1]) or _TASK_DONE_RE.match(file_lines[end + 1])):
        end += 1

    return (start, end)


def reorder_task_line(paths: AgentPaths, project_id: str, line: int, direction: str) -> dict[str, Any]:
    """Move a task one position up or down within its contiguous task group."""
    tasks_path = project_dir(paths, project_id) / "TASKS.md"
    if not tasks_path.is_file():
        raise ProjectModeError(f"TASKS.md not found for project {project_id}")

    file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
    if line < 0 or line >= len(file_lines):
        raise ProjectModeError(f"line {line} out of range (0–{len(file_lines) - 1})")

    if direction == "up":
        if line == 0:
            raise ProjectModeError("already at top")
        # Check both lines are task checkboxes
        if not (_TASK_OPEN_RE.match(file_lines[line]) or _TASK_DONE_RE.match(file_lines[line])):
            raise ProjectModeError(f"line {line} is not a task checkbox")
        if not (_TASK_OPEN_RE.match(file_lines[line - 1]) or _TASK_DONE_RE.match(file_lines[line - 1])):
            raise ProjectModeError("cannot move across phase boundary")
        file_lines[line], file_lines[line - 1] = file_lines[line - 1], file_lines[line]
    elif direction == "down":
        if line >= len(file_lines) - 1:
            raise ProjectModeError("already at bottom")
        if not (_TASK_OPEN_RE.match(file_lines[line]) or _TASK_DONE_RE.match(file_lines[line])):
            raise ProjectModeError(f"line {line} is not a task checkbox")
        if not (_TASK_OPEN_RE.match(file_lines[line + 1]) or _TASK_DONE_RE.match(file_lines[line + 1])):
            raise ProjectModeError("cannot move across phase boundary")
        file_lines[line], file_lines[line + 1] = file_lines[line + 1], file_lines[line]
    else:
        raise ProjectModeError(f"unknown direction: {direction}")

    content = "\n".join(file_lines)
    if not content.endswith("\n"):
        content += "\n"
    tasks_path.write_text(content, encoding="utf-8")

    stats = read_task_stats(tasks_path)
    return {
        "type": "project.task.reorder.done",
        "line": line,
        "direction": direction,
        "tasks_done": stats.done,
        "tasks_total": stats.total,
    }


def drop_task_line(
    paths: AgentPaths,
    project_id: str,
    line: int,
    *,
    reason: str = "wontfix",
    source: str = "drop",
) -> dict[str, Any]:
    """Archive (default wontfix) then remove a task line from TASKS.md."""
    result = archive_and_remove_task_line(
        paths,
        project_id,
        line,
        reason=reason,
        source=source,
    )
    result["type"] = "project.task.drop.done"
    return result


def skip_task_line(paths: AgentPaths, project_id: str, line: int) -> dict[str, Any]:
    """Move a task to end of its Phase (contiguous task block)."""
    tasks_path = project_dir(paths, project_id) / "TASKS.md"
    if not tasks_path.is_file():
        raise ProjectModeError(f"TASKS.md not found for project {project_id}")

    file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
    if line < 0 or line >= len(file_lines):
        raise ProjectModeError(f"line {line} out of range (0–{len(file_lines) - 1})")

    task_range = find_task_line_range(tasks_path, line)
    if task_range is None:
        raise ProjectModeError(f"line {line} is not a task checkbox")

    start, end = task_range
    if end - start == 0:
        raise ProjectModeError("only one task in group; nothing to skip past")

    task_line = file_lines.pop(line)
    # Insert at end of task group
    file_lines.insert(end, task_line)

    content = "\n".join(file_lines)
    if not content.endswith("\n"):
        content += "\n"
    tasks_path.write_text(content, encoding="utf-8")

    stats = read_task_stats(tasks_path)
    return {
        "type": "project.task.skip.done",
        "line": line,
        "new_position": end,
        "tasks_done": stats.done,
        "tasks_total": stats.total,
    }


def list_project_docs(paths: AgentPaths, project_id: str) -> list[dict[str, Any]]:
    """List all .md files in the project directory."""
    root = project_dir(paths, project_id)
    if not root.is_dir():
        return []
    docs: list[dict[str, Any]] = []
    for fpath in sorted(root.rglob("*.md")):
        rel = str(fpath.relative_to(root)).replace("\\", "/")
        try:
            size = fpath.stat().st_size
        except OSError:
            size = 0
        docs.append({
            "path": rel,
            "name": fpath.name,
            "size": size,
            "is_standard": rel in PROJECT_ARTIFACTS,
        })
    return docs


def read_project_doc(paths: AgentPaths, project_id: str, doc_path: str) -> dict[str, Any]:
    """Read a single .md file from the project directory. Returns {type, path, content}."""
    root = project_dir(paths, project_id)
    safe_path = doc_path.replace("\\", "/").lstrip("/")
    full = (root / safe_path).resolve()
    if not str(full).startswith(str(root.resolve())):
        raise ProjectModeError(f"path escapes project directory: {doc_path}")
    if not full.is_file():
        raise ProjectModeError(f"document not found: {doc_path}")
    content = full.read_text(encoding="utf-8")
    return {
        "type": "project.doc.read.done",
        "path": safe_path,
        "content": content,
        "size": len(content),
    }


def create_project_doc(paths: AgentPaths, project_id: str, doc_path: str, content: str = "") -> dict[str, Any]:
    """Create a new .md file in the project directory."""
    root = project_dir(paths, project_id)
    safe_path = doc_path.replace("\\", "/").lstrip("/")
    if not safe_path.endswith(".md"):
        safe_path += ".md"
    full = (root / safe_path).resolve()
    if not str(full).startswith(str(root.resolve())):
        raise ProjectModeError(f"path escapes project directory: {doc_path}")
    if full.exists():
        raise ProjectModeError(f"document already exists: {safe_path}")
    full.parent.mkdir(parents=True, exist_ok=True)
    default_content = content if content else f"# {full.stem}\n\n"
    full.write_text(default_content, encoding="utf-8")
    return {
        "type": "project.doc.create.done",
        "path": safe_path,
        "name": full.name,
    }


def iter_phase_headers(file_lines: list[str]) -> list[tuple[int, str]]:
    """Return [(line_index, phase_title), ...] for ``## `` headers."""
    out: list[tuple[int, str]] = []
    for i, line in enumerate(file_lines):
        if line.strip().startswith("## "):
            out.append((i, line.strip().lstrip("#").strip()))
    return out


def find_phase_title(file_lines: list[str], needle: str) -> str | None:
    """Match phase by substring (case-insensitive); None if no hit."""
    if not needle or not str(needle).strip():
        return None
    key = str(needle).strip().lower()
    for _, title in iter_phase_headers(file_lines):
        if key in title.lower() or title.lower() in key:
            return title
    return None


def phase_open_and_done_counts(file_lines: list[str], phase_title: str) -> tuple[int, int]:
    """Count open / done checkboxes under a phase header."""
    headers = iter_phase_headers(file_lines)
    start = -1
    end = len(file_lines)
    for idx, (line_i, title) in enumerate(headers):
        if title.lower() == phase_title.lower() or phase_title.lower() in title.lower():
            start = line_i + 1
            if idx + 1 < len(headers):
                end = headers[idx + 1][0]
            break
    if start < 0:
        return 0, 0
    open_n = 0
    done_n = 0
    for j in range(start, end):
        if _TASK_OPEN_RE.match(file_lines[j]):
            open_n += 1
        elif _TASK_DONE_RE.match(file_lines[j]):
            done_n += 1
    return open_n, done_n


def active_phase_title_from_lines(file_lines: list[str]) -> str | None:
    """Phase that contains the first open checkbox (current work frontier)."""
    current: str | None = None
    in_closed = False
    for line in file_lines:
        if line.strip().startswith("## "):
            current = line.strip().lstrip("#").strip()
            in_closed = is_closed_section_title(current)
            continue
        if in_closed:
            continue
        if _TASK_OPEN_RE.match(line) and current:
            return current
    return None


def add_task_to_tasks_md(
    paths: AgentPaths,
    project_id: str,
    phase_title: str,
    description: str,
) -> dict[str, Any]:
    """Add a new task under the Phase chosen by the caller (Plan Agent LLM).

    Trusts ``phase_title``: match existing ``## `` header, or create a new section
    at EOF. Never silently remap into Phase 1.
    """
    tasks_path = project_dir(paths, project_id) / "TASKS.md"
    if not tasks_path.is_file():
        raise ProjectModeError(f"TASKS.md not found for project {project_id}")

    file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
    matched = find_phase_title(file_lines, phase_title) if phase_title else None
    resolved = matched or (phase_title.strip() if isinstance(phase_title, str) and phase_title.strip() else "")
    insert_idx = -1

    if matched:
        headers = iter_phase_headers(file_lines)
        for idx, (line_i, title) in enumerate(headers):
            if title != matched:
                continue
            phase_end = headers[idx + 1][0] if idx + 1 < len(headers) else len(file_lines)
            last_task = -1
            for j in range(line_i + 1, phase_end):
                if _TASK_OPEN_RE.match(file_lines[j]) or _TASK_DONE_RE.match(file_lines[j]):
                    last_task = j
            if last_task >= 0:
                insert_idx = last_task + 1
            else:
                insert_idx = line_i + 1
                while insert_idx < len(file_lines) and not file_lines[insert_idx].strip():
                    insert_idx += 1
            break
    elif resolved:
        if file_lines and file_lines[-1].strip():
            file_lines.append("")
        file_lines.append(f"## {resolved}")
        insert_idx = len(file_lines)
    else:
        insert_idx = len(file_lines)
        if file_lines and file_lines[-1].strip():
            file_lines.append("")

    task_line = f"- [ ] {description}"
    file_lines.insert(insert_idx, task_line)
    content = "\n".join(file_lines)
    if not content.endswith("\n"):
        content += "\n"
    tasks_path.write_text(content, encoding="utf-8")

    stats = read_task_stats(tasks_path)
    return {
        "type": "project.task.add.done",
        "line": insert_idx,
        "description": description,
        "phase": resolved,
        "tasks_done": stats.done,
        "tasks_total": stats.total,
    }


def move_task_to_phase(
    paths: AgentPaths,
    project_id: str,
    line: int,
    phase_title: str,
) -> dict[str, Any]:
    """Move an existing checkbox line to another Phase (preserves [ ] / [x] text)."""
    tasks_path = project_dir(paths, project_id) / "TASKS.md"
    if not tasks_path.is_file():
        raise ProjectModeError(f"TASKS.md not found for project {project_id}")

    file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
    if line < 0 or line >= len(file_lines):
        raise ProjectModeError(f"line {line} out of range")
    raw = file_lines[line]
    if not (_TASK_OPEN_RE.match(raw) or _TASK_DONE_RE.match(raw)):
        raise ProjectModeError(f"line {line} is not a task checkbox")

    task_line = file_lines.pop(line)
    matched = find_phase_title(file_lines, phase_title) if phase_title else None
    resolved = matched or (phase_title.strip() if phase_title and str(phase_title).strip() else "")
    if not resolved:
        raise ProjectModeError("move_task_to_phase requires phase_title")

    insert_idx = -1
    if matched:
        headers = iter_phase_headers(file_lines)
        for idx, (line_i, title) in enumerate(headers):
            if title != matched:
                continue
            phase_end = headers[idx + 1][0] if idx + 1 < len(headers) else len(file_lines)
            last_task = -1
            for j in range(line_i + 1, phase_end):
                if _TASK_OPEN_RE.match(file_lines[j]) or _TASK_DONE_RE.match(file_lines[j]):
                    last_task = j
            if last_task >= 0:
                insert_idx = last_task + 1
            else:
                insert_idx = line_i + 1
                while insert_idx < len(file_lines) and not file_lines[insert_idx].strip():
                    insert_idx += 1
            break
    else:
        if file_lines and file_lines[-1].strip():
            file_lines.append("")
        file_lines.append(f"## {resolved}")
        insert_idx = len(file_lines)

    if insert_idx < 0:
        insert_idx = len(file_lines)
    file_lines.insert(insert_idx, task_line)
    content = "\n".join(file_lines)
    if not content.endswith("\n"):
        content += "\n"
    tasks_path.write_text(content, encoding="utf-8")

    m = re.match(r"^\s*-\s*\[[ xX]\]\s+(.*)", task_line)
    desc = m.group(1).strip() if m else task_line.strip()
    stats = read_task_stats(tasks_path)
    return {
        "type": "project.task.move.done",
        "from_line": line,
        "line": insert_idx,
        "phase": resolved,
        "description": desc,
        "tasks_done": stats.done,
        "tasks_total": stats.total,
    }


_SOURCE_EXTS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".go", ".rs", ".java",
    ".c", ".cpp", ".h", ".rb", ".php", ".swift", ".kt", ".scala",
    ".css", ".scss", ".html", ".md", ".json", ".yaml", ".yml", ".toml",
})


def detect_potential_project(paths: AgentPaths, current_project_id: str = "") -> dict[str, Any] | None:
    """Scan workspace for recently-modified dirs that look like projects.

    Returns {type: project.detect, project_id, reason, file_count} or None.
    Only detects directories NOT already bound to the current session.
    """
    root = paths.workspace
    if not root.is_dir():
        return None

    import time
    now = time.time()
    cutoff = now - 600  # 10 minutes

    candidates: list[tuple[str, float, int, bool]] = []  # (name, mtime, files, has_tasks)

    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        name = entry.name
        if name.startswith(".") or name == _TEMPLATE_DIRNAME:
            continue
        if name == current_project_id:
            continue

        # Count source files under this directory (shallow scan, max depth 3)
        file_count = 0
        has_tasks = (entry / "TASKS.md").is_file()
        max_mtime = entry.stat().st_mtime

        try:
            for sub in entry.rglob("*"):
                if sub.is_file():
                    if sub.suffix.lower() in _SOURCE_EXTS or sub.name.endswith(".md"):
                        file_count += 1
                    try:
                        mt = sub.stat().st_mtime
                        if mt > max_mtime:
                            max_mtime = mt
                    except OSError:
                        pass
                if file_count > 20:  # early cutoff
                    break
        except OSError:
            pass

        if file_count >= 2 and max_mtime >= cutoff:
            candidates.append((name, max_mtime, file_count, has_tasks))

    if not candidates:
        return None

    # Pick the most recently modified candidate
    candidates.sort(key=lambda x: x[1], reverse=True)
    best = candidates[0]

    reason = (
        f"检测到 workspace/{best[0]} 下有 {best[2]} 个源文件，"
        f"{'含 TASKS.md' if best[3] else '建议创建项目以追踪任务进度'}"
    )

    return {
        "type": "project.detect",
        "project_id": best[0],
        "reason": reason,
        "file_count": best[2],
        "has_tasks": best[3],
    }


def _demo() -> None:
    paths = AgentPaths.discover()
    ensure_template(paths)
    print("[PASS] ensure_template")

    pid = "project-mode-demo"
    dest = project_dir(paths, pid)
    if dest.is_dir():
        shutil.rmtree(dest)
    create_project(paths, pid)
    assert (dest / "TASKS.md").is_file()
    print("[PASS] create_project")

    stats = read_task_stats(dest / "TASKS.md")
    assert stats.open_count >= 2
    print("[PASS] read_task_stats")

    root = project_root_rel(pid)
    reason = project_mode_block_reason(
        active_shell="project",
        project_root=root,
        plan_status="draft",
        tool_name="run_evolved",
        arguments={"tool_name": "run_command", "arguments": {"command": "echo hi", "working_dir": root}},
    )
    assert reason and "未确认" in reason
    print("[PASS] plan gate blocks run_command")

    reason2 = project_mode_block_reason(
        active_shell="project",
        project_root=root,
        plan_status="draft",
        tool_name="run_evolved",
        arguments={
            "tool_name": "write_text",
            "arguments": {"path": f"{root}/TASKS.md", "content": "x"},
        },
    )
    assert reason2 and "plan_partner" in reason2
    print("[PASS] plan domain write block (B5) rejects TASKS.md")

    reason_grow = project_mode_block_reason(
        active_shell="grow",
        project_root=root,
        plan_status="draft",
        tool_name="run_evolved",
        arguments={
            "tool_name": "write_text",
            "arguments": {"path": f"{root}/src/demo.py", "content": "x"},
        },
    )
    assert reason_grow and "未确认" in reason_grow
    print("[PASS] plan gate blocks src even in grow shell")

    reason_evolve = project_mode_block_reason(
        active_shell="grow",
        project_root=root,
        plan_status="draft",
        tool_name="run_evolved",
        arguments={
            "tool_name": "write_text",
            "arguments": {"path": "evolve/tools/common/foo.txt", "content": "x"},
        },
    )
    assert reason_evolve is None
    print("[PASS] plan gate allows evolve write while project bound")

    reason3 = project_mode_block_reason(
        active_shell="project",
        project_root=root,
        plan_status="confirmed",
        tool_name="run_evolved",
        arguments={"tool_name": "write_evolve", "arguments": {}},
    )
    assert reason3 and "write_evolve" in reason3
    print("[PASS] write_evolve blocked in project")

    reason_clone_evolve = project_mode_block_reason(
        active_shell="project",
        project_root=root,
        plan_status="confirmed",
        tool_name="run_evolved",
        arguments={
            "tool_name": "git_clone",
            "arguments": {
                "url": "https://github.com/github/gitignore.git",
                "target": "evolve_tools",
                "dest": "evolve/tools/common/foo",
            },
        },
    )
    assert reason_clone_evolve and "evolve/tools" in reason_clone_evolve
    print("[PASS] git_clone evolve_tools blocked in project shell")

    reason_clone_draft = project_mode_block_reason(
        active_shell="project",
        project_root=root,
        plan_status="draft",
        tool_name="run_evolved",
        arguments={
            "tool_name": "git_clone",
            "arguments": {
                "url": "https://github.com/github/gitignore.git",
                "target": "workspace",
                "dest": f"{root}/vendor/foo",
            },
        },
    )
    assert reason_clone_draft and "未确认" in reason_clone_draft
    print("[PASS] plan gate blocks workspace git_clone before confirm")

    sample_md = (dest / "PROJECT.md").read_text(encoding="utf-8")
    spec = parse_acceptance_spec(sample_md)
    assert spec is not None and spec.script_rel == "demo.py"
    assert spec.expected_exit_code == 0
    print("[PASS] parse_acceptance_spec")

    shutil.rmtree(dest, ignore_errors=True)
    print("[PASS] T-1102/T-1107: project_mode demo")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        paths = AgentPaths.discover()
        for item_id in list_projects(paths):
            item_stats = read_task_stats(project_dir(paths, item_id) / "TASKS.md")
            print(f"{item_id}: {item_stats.done}/{item_stats.total}")
