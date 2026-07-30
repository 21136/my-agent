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
_TEMPLATE_DIRNAME = "_template"
_PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_TASK_OPEN_RE = re.compile(r"^\s*-\s*\[\s\]\s+", re.MULTILINE)
_TASK_DONE_RE = re.compile(r"^\s*-\s*\[x\]\s+", re.IGNORECASE | re.MULTILINE)
_ACCEPT_CMD_RE = re.compile(r"命令[：:]\s*`([^`]+)`", re.IGNORECASE)
_ACCEPT_EXIT_RE = re.compile(r"退出码\s*(\d+)", re.IGNORECASE)
_PYTHON_SCRIPT_RE = re.compile(r"python(?:3)?\s+([^\s`]+\.py)", re.IGNORECASE)

_CODING_TOOLS = frozenset({"run_python", "run_tests", "run_demo", "patch_file"})
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
    for name in PROJECT_ARTIFACTS:
        shutil.copy2(src / name, dest / name)
    return dest


def read_task_stats(tasks_path: Path) -> TaskStats:
    if not tasks_path.is_file():
        return TaskStats(done=0, total=0)
    text = tasks_path.read_text(encoding="utf-8")
    done = len(_TASK_DONE_RE.findall(text))
    open_count = len(_TASK_OPEN_RE.findall(text))
    return TaskStats(done=done, total=done + open_count)


def build_project_goal(*, project_root: str, plan_status: str) -> str:
    return "\n".join(
        [
            f"项目根：{project_root}",
            f"进度真源：{project_root}/TASKS.md",
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
    return rel == "TASKS.md"


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
    for line in tasks_text.splitlines():
        if _TASK_OPEN_RE.match(line):
            return line.strip()
    return None


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

    if plan_allows_code_writes(plan_status):
        if tool_name == "run_evolved" and evolved_name == "patch_file":
            for path in extract_run_evolved_paths(tool_name, arguments):
                if path and not is_under_project_root(path, project_root):
                    return f"patch_file 仅限项目目录内：{project_root}"

        # Block direct writes to TASKS.md — must use report_progress
        if tool_name == "run_evolved" and evolved_name in _WRITE_TOOLS:
            for path in extract_run_evolved_paths(tool_name, arguments):
                if path and is_project_tasks_path(path, project_root):
                    return (
                        "不要直接写 TASKS.md。已完成一条任务后，请调用 "
                        "run_evolved(tool_name=\"report_progress\", arguments={"
                        "project_id: \"<项目id>\", summary: \"<做了什么>\", "
                        "task_line: <勾选的行号>, subtasks: [<实际拆出的子任务>], "
                        "add_tasks: [<新发现的任务>]})。"
                        "项目管理器会自动更新 TASKS.md 并检查质量。"
                    )

        return None

    # Plan gate: any session bound to project_root — even if router switched shell.
    if tool_name == "run_evolved":
        if evolved_name in _CODING_TOOLS:
            return (
                f"计划未确认（{plan_status or 'draft'}）；"
                "请先完成 PROJECT/TASKS 并请用户「项目 确认」后再写代码或 run_python"
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
        lines.append("plan_gate: 已确认 — 可写项目内代码；每步更新 TASKS.md")
        lines.append(
            "task_stop: 每完成一条 TASKS 勾选必须停；用户「继续」后再做下一项"
        )
    if task_stats is not None:
        lines.append(f"tasks: {task_stats.done}/{task_stats.total} done")
    if continue_turn and plan_status == "confirmed":
        lines.append(
            "continue_turn: 本轮为「继续」— 只做第一条未勾选 task，完成后标 [x] 并停"
        )
        if next_open_task:
            lines.append(f"current_task: {next_open_task}")
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
    """Run PROJECT.md acceptance via run_python (no confirm gate)."""
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

    registry = ToolRegistry.load(paths)
    tool_result = run(
        {"tool_name": "run_python", "arguments": {"path": rel}},
        registry=registry,
    )
    if not tool_result.ok:
        message = tool_result.error.message if tool_result.error else "run_python failed"
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
        "path": data.get("path", f"workspace/{rel}"),
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


def toggle_task_line(paths: AgentPaths, project_id: str, line: int, done: bool) -> dict[str, Any]:
    """Toggle a single checkbox line in TASKS.md. Returns {type, line, done, tasks_done, tasks_total}."""
    tasks_path = project_dir(paths, project_id) / "TASKS.md"
    if not tasks_path.is_file():
        raise ProjectModeError(f"TASKS.md not found for project {project_id}")

    file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
    if line < 0 or line >= len(file_lines):
        raise ProjectModeError(f"line {line} out of range (0–{len(file_lines) - 1})")

    target = file_lines[line]
    if done:
        new_line = _TASK_OPEN_RE.sub("- [x] ", target, count=1)
    else:
        new_line = _TASK_DONE_RE.sub("- [ ] ", target, count=1)

    if new_line == target:
        raise ProjectModeError(f"line {line} is not a task checkbox")

    file_lines[line] = new_line
    content = "\n".join(file_lines)
    if not content.endswith("\n"):
        content += "\n"
    tasks_path.write_text(content, encoding="utf-8")

    stats = read_task_stats(tasks_path)
    return {
        "type": "project.task.toggle.done",
        "line": line,
        "done": done,
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


def drop_task_line(paths: AgentPaths, project_id: str, line: int) -> dict[str, Any]:
    """Remove a task line from TASKS.md."""
    tasks_path = project_dir(paths, project_id) / "TASKS.md"
    if not tasks_path.is_file():
        raise ProjectModeError(f"TASKS.md not found for project {project_id}")

    file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
    if line < 0 or line >= len(file_lines):
        raise ProjectModeError(f"line {line} out of range (0–{len(file_lines) - 1})")

    if not (_TASK_OPEN_RE.match(file_lines[line]) or _TASK_DONE_RE.match(file_lines[line])):
        raise ProjectModeError(f"line {line} is not a task checkbox")

    removed = file_lines.pop(line)
    content = "\n".join(file_lines)
    if not content.endswith("\n"):
        content += "\n"
    tasks_path.write_text(content, encoding="utf-8")

    stats = read_task_stats(tasks_path)
    return {
        "type": "project.task.drop.done",
        "line": line,
        "removed": removed.strip(),
        "tasks_done": stats.done,
        "tasks_total": stats.total,
    }


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


def add_task_to_tasks_md(paths: AgentPaths, project_id: str, phase_title: str, description: str) -> dict[str, Any]:
    """Add a new task line under the given Phase in TASKS.md."""
    tasks_path = project_dir(paths, project_id) / "TASKS.md"
    if not tasks_path.is_file():
        raise ProjectModeError(f"TASKS.md not found for project {project_id}")

    file_lines = tasks_path.read_text(encoding="utf-8").splitlines()
    insert_idx = -1

    for i, line in enumerate(file_lines):
        if line.strip().startswith("## ") and phase_title.lower() in line.lower():
            # Find the last task line under this phase
            j = i + 1
            while j < len(file_lines):
                if file_lines[j].strip().startswith("## "):
                    break
                if _TASK_OPEN_RE.match(file_lines[j]) or _TASK_DONE_RE.match(file_lines[j]):
                    insert_idx = j
                j += 1
            if insert_idx >= 0:
                insert_idx += 1
            else:
                # No tasks yet, insert after phase header
                insert_idx = i + 1
                # Skip blank lines after header
                while insert_idx < len(file_lines) and not file_lines[insert_idx].strip():
                    insert_idx += 1
            break

    if insert_idx < 0:
        # Phase not found, append to end
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
        arguments={"tool_name": "run_python", "arguments": {"path": "demo.py"}},
    )
    assert reason and "未确认" in reason
    print("[PASS] plan gate blocks run_python")

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
    assert reason2 is None
    print("[PASS] plan gate allows TASKS.md")

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
