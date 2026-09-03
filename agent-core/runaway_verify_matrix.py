"""T↔V matrix linter for runaway verification (Phase 59 · UI-5961)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from paths import AgentPaths
from project_mode import (
    formal_task_id_from_checkbox_line,
    parse_acceptance_spec,
    parse_tasks_metadata,
    read_project_artifacts,
    _TASK_DONE_RE,
    _TASK_OPEN_RE,
)
from runaway_flow import verify_task_documented

MatrixSeverity = Literal["error", "warn"]

_TASK_ID_RE = re.compile(r"\bT-\d+(?:-\d+)*\b", re.IGNORECASE)
_VERIFY_ID_RE = re.compile(r"\bV-\d+(?:-\d+)*\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class MatrixIssue:
    rule_id: str
    severity: MatrixSeverity
    message: str


@dataclass
class MatrixLintResult:
    errors: list[MatrixIssue] = field(default_factory=list)
    warnings: list[MatrixIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [
                {"rule_id": item.rule_id, "message": item.message}
                for item in self.errors
            ],
            "warnings": [
                {"rule_id": item.rule_id, "message": item.message}
                for item in self.warnings
            ],
        }


def _open_formal_task_ids(tasks_text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for line in (tasks_text or "").splitlines():
        if not _TASK_OPEN_RE.match(line):
            continue
        task_id = formal_task_id_from_checkbox_line(line)
        if not task_id:
            continue
        token = task_id.upper()
        if token in seen:
            continue
        seen.add(token)
        found.append(token)
    return found


def _verify_orphan_lines(verify_text: str) -> list[str]:
    """V-* lines in VERIFY.md that do not reference any T-* task id."""
    orphans: list[str] = []
    for line in (verify_text or "").splitlines():
        if not _VERIFY_ID_RE.search(line):
            continue
        if _TASK_ID_RE.search(line):
            continue
        match = _VERIFY_ID_RE.search(line)
        if match:
            orphans.append(match.group(0).upper())
    return orphans


def lint_verify_matrix(paths: AgentPaths, project_id: str) -> MatrixLintResult:
    """Deterministic TASKS / VERIFY / PROJECT acceptance checks (MX-1～MX-3)."""
    pid = str(project_id or "").strip()
    result = MatrixLintResult()
    if not pid:
        result.errors.append(
            MatrixIssue("MX-0", "error", "未绑定项目，无法校验验收矩阵")
        )
        return result

    artifacts = read_project_artifacts(paths, pid)
    tasks_text = artifacts.get("TASKS.md", "")
    verify_text = artifacts.get("VERIFY.md", "")
    project_text = artifacts.get("PROJECT.md", "")

    for task_id in _open_formal_task_ids(tasks_text):
        if not verify_task_documented(verify_text, task_id):
            result.errors.append(
                MatrixIssue(
                    "MX-1",
                    "error",
                    f"开放任务 {task_id} 在 VERIFY.md 中缺少对口证据",
                )
            )

    for verify_id in _verify_orphan_lines(verify_text):
        result.errors.append(
            MatrixIssue(
                "MX-2",
                "error",
                f"VERIFY 记录 {verify_id} 未绑定 T-* 任务",
            )
        )

    if parse_acceptance_spec(project_text) is None:
        result.errors.append(
            MatrixIssue(
                "MX-3",
                "error",
                "PROJECT.md 未定义可执行验收命令（## 验收标准 + 命令：`…`）",
            )
        )

    env_text = artifacts.get("ENV.md", "")
    from project_quality import parse_quality_commands_from_env_text

    if not parse_quality_commands_from_env_text(env_text):
        result.errors.append(
            MatrixIssue(
                "MX-5",
                "error",
                "ENV.md 未定义 quality.commands（run_quality 无法执行）",
            )
        )

    done_formal = 0
    documented = 0
    lines = tasks_text.splitlines()
    for task in parse_tasks_metadata(tasks_text):
        task_id = str(task.get("id") or "").strip().upper()
        if not task_id or not task_id.startswith("T-"):
            continue
        line_no = int(task.get("line") or -1)
        if line_no < 0 or line_no >= len(lines):
            continue
        if not _TASK_DONE_RE.match(lines[line_no]):
            continue
        done_formal += 1
        if verify_task_documented(verify_text, task_id):
            documented += 1

    if done_formal and documented < done_formal:
        gap = done_formal - documented
        result.warnings.append(
            MatrixIssue(
                "MX-4",
                "warn",
                f"TASKS 已勾选 {done_formal} 项正式任务，VERIFY 仅覆盖 {documented} 项（差 {gap}）",
            )
        )

    strict = str(
        __import__("os").environ.get("MY_AGENT_VERIFY_MATRIX_STRICT", "1")
    ).strip().lower() not in {"0", "false", "no"}
    if strict:
        result.errors.extend(
            MatrixIssue(item.rule_id, "error", item.message)
            for item in result.warnings
        )
        result.warnings = []

    return result


def format_matrix_summary(lint: MatrixLintResult) -> str:
    if lint.ok and not lint.warnings:
        return "验收矩阵：通过"
    parts: list[str] = []
    for item in lint.errors:
        parts.append(f"[{item.rule_id}] {item.message}")
    for item in lint.warnings:
        parts.append(f"[{item.rule_id}] {item.message}")
    return "验收矩阵：" + "；".join(parts) if parts else "验收矩阵：未通过"


def matrix_blockers_for_release(paths: AgentPaths, project_id: str) -> MatrixLintResult:
    """Return lint result; callers gate release_wait on ``errors``."""
    return lint_verify_matrix(paths, project_id)
