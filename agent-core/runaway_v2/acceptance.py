"""Runaway v2 acceptance hook — command exit code is truth."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from paths import AgentPaths
from project_mode import AcceptanceSpec, run_acceptance_check
from runaway_flow import verify_task_documented
from runaway_verify_matrix import lint_verify_matrix

from runaway_v2.checklist import ChecklistItem
from runaway_v2.config import hook_timeout_sec


@dataclass(frozen=True, slots=True)
class HookResult:
    ok: bool
    exit_code: int | None = None
    command: str = ""
    tail: str = ""


def _tail_lines(text: str, *, limit: int = 20) -> str:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    return "\n".join(lines[-limit:])


def _matrix_rule_passes(paths: AgentPaths, project_id: str, rule_id: str) -> tuple[bool, str]:
    lint = lint_verify_matrix(paths, project_id)
    rule = str(rule_id or "").upper()
    for issue in lint.errors:
        issue_rule = str(issue.rule_id or "").upper()
        if issue_rule == rule or issue_rule.startswith(f"{rule}:"):
            return False, str(issue.message or issue_rule)
    return True, ""


def run_acceptance(
    paths: AgentPaths,
    project_id: str,
    item: ChecklistItem | None,
    *,
    timeout_sec: float | None = None,
) -> HookResult:
    if item is None:
        return HookResult(ok=True)

    acceptance = item.acceptance or {}
    kind = str(acceptance.get("type") or "").strip()
    _ = timeout_sec if timeout_sec is not None else hook_timeout_sec()

    if kind == "prepare_ready":
        from project_mode import count_archive_entries, project_dir, read_project_artifacts
        from runaway_v2.phase import _documentation_ready

        # Do not reuse the legacy preparation gate here. It requires an open
        # ``- [ ]`` task, which makes PREPARE impossible to pass after a
        # project has legitimately completed its entire formal queue.
        artifacts = read_project_artifacts(paths, project_id)
        archived_done = count_archive_entries(
            project_dir(paths, project_id) / "TASKS.archive.md",
            reason="done",
        )
        ready, missing = _documentation_ready(artifacts, archived_done=archived_done)
        return HookResult(
            ok=ready,
            exit_code=0 if ready else 1,
            command="documentation_ready_for_design",
            tail=", ".join(missing) if missing else "",
        )

    if kind == "matrix_rule":
        rule_id = str(acceptance.get("rule_id") or item.id.split(":", 1)[0])
        ok, message = _matrix_rule_passes(paths, project_id, rule_id)
        command = f"lint_verify_matrix {rule_id}"
        return HookResult(
            ok=ok,
            exit_code=0 if ok else 1,
            command=command,
            tail="" if ok else message,
        )

    if kind == "verify_doc":
        task_id = str(acceptance.get("task_id") or item.id)
        artifacts = __import__("project_mode", fromlist=["read_project_artifacts"]).read_project_artifacts(
            paths, project_id
        )
        ok = verify_task_documented(artifacts.get("VERIFY.md", ""), task_id)
        return HookResult(
            ok=ok,
            exit_code=0 if ok else 1,
            command=f"verify_doc {task_id}",
            tail="" if ok else f"{task_id} 在 VERIFY.md 中缺少对口证据",
        )

    if kind == "command":
        spec = AcceptanceSpec(
            display=str(acceptance.get("display") or ""),
            expected_exit_code=int(acceptance.get("expected_exit_code") or 0),
            script_rel=str(acceptance.get("script_rel") or ""),
            argv=tuple(acceptance.get("argv") or ()),
        )
        result: dict[str, Any] = run_acceptance_check(paths, project_id, spec)
        passed = bool(result.get("passed"))
        tail = _tail_lines(
            "\n".join(
                part
                for part in (
                    str(result.get("stdout") or ""),
                    str(result.get("stderr") or ""),
                    str(result.get("error") or ""),
                )
                if part
            )
        )
        return HookResult(
            ok=passed,
            exit_code=int(result.get("exit_code") or (0 if passed else 1)),
            command=str(result.get("command") or spec.display),
            tail=tail,
        )

    return HookResult(ok=False, exit_code=1, command="unknown", tail="未知验收类型")


def format_hook_failure(hook: HookResult) -> dict[str, Any]:
    return {
        "exit_code": hook.exit_code,
        "command": hook.command,
        "tail": hook.tail,
    }
