"""Runaway v2 checklist — build, merge, persist."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from paths import AgentPaths
from project_mode import (
    _TASK_DONE_RE,
    formal_task_id_from_checkbox_line,
    parse_acceptance_spec,
    parse_tasks_metadata,
    project_dir,
    read_project_artifacts,
)
from runaway_flow import verify_task_documented
from runaway_verify_matrix import lint_verify_matrix

ChecklistStatus = Literal["pending", "running", "passed", "failed", "blocked"]
ChecklistKind = Literal["matrix", "acceptance", "task"]

_MATRIX_WRITE_SCOPE: dict[str, tuple[str, ...]] = {
    "MX-1": ("VERIFY.md",),
    "MX-2": ("VERIFY.md",),
    "MX-3": ("PROJECT.md",),
    "MX-4": ("VERIFY.md",),
    "MX-5": ("ENV.md",),
}

_PREPARE_WRITE_SCOPE = (
    "PROJECT.md",
    "DESIGN.md",
    "TASKS.md",
    "VERIFY.md",
    "ENV.md",
    "MAP.md",
)


@dataclass
class ChecklistItem:
    id: str
    kind: ChecklistKind
    title: str
    status: ChecklistStatus = "pending"
    stable_key: str = ""
    write_scope: list[str] = field(default_factory=list)
    attempts: int = 0
    directed_used: bool = False
    acceptance: dict[str, Any] = field(default_factory=dict)
    last_run_at: str = ""
    last_failure: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "stable_key": self.stable_key,
            "write_scope": list(self.write_scope),
            "attempts": self.attempts,
            "directed_used": self.directed_used,
            "acceptance": dict(self.acceptance),
        }
        if self.last_run_at:
            payload["last_run_at"] = self.last_run_at
        if self.last_failure:
            payload["last_failure"] = dict(self.last_failure)
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ChecklistItem:
        return cls(
            id=str(raw.get("id") or ""),
            kind=str(raw.get("kind") or "matrix"),  # type: ignore[arg-type]
            title=str(raw.get("title") or ""),
            status=str(raw.get("status") or "pending"),  # type: ignore[arg-type]
            stable_key=str(raw.get("stable_key") or ""),
            write_scope=[str(x) for x in (raw.get("write_scope") or [])],
            attempts=int(raw.get("attempts") or 0),
            directed_used=bool(raw.get("directed_used", False)),
            acceptance=dict(raw.get("acceptance") or {}),
            last_run_at=str(raw.get("last_run_at") or ""),
            last_failure=(
                dict(raw.get("last_failure"))
                if isinstance(raw.get("last_failure"), dict)
                else None
            ),
        )


@dataclass
class Checklist:
    version: int
    project_id: str
    generated_at: str
    source_fingerprint: str
    items: list[ChecklistItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "project_id": self.project_id,
            "generated_at": self.generated_at,
            "source_fingerprint": self.source_fingerprint,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Checklist:
        items = [
            ChecklistItem.from_dict(item)
            for item in (raw.get("items") or [])
            if isinstance(item, dict)
        ]
        return cls(
            version=int(raw.get("version") or 1),
            project_id=str(raw.get("project_id") or ""),
            generated_at=str(raw.get("generated_at") or ""),
            source_fingerprint=str(raw.get("source_fingerprint") or ""),
            items=items,
        )


def checklist_path(paths: AgentPaths, project_id: str) -> Path:
    return project_dir(paths, project_id) / ".agent" / "runaway-checklist.json"


def progress_path(paths: AgentPaths, project_id: str) -> Path:
    return project_dir(paths, project_id) / "RUNAWAY-PROGRESS.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _acceptance_section(project_md: str) -> str:
    match = re.search(r"(?ms)^##\s*验收标准\s*$.*?(?=^##\s|\Z)", project_md or "")
    return match.group(0) if match else ""


def _quality_section(env_md: str) -> str:
    match = re.search(r"(?ms)^quality:\s*$.*?(?=^\S|\Z)", env_md or "")
    return match.group(0) if match else ""


def compute_source_fingerprint(artifacts: dict[str, str]) -> str:
    payload = "\n---\n".join(
        [
            artifacts.get("TASKS.md", ""),
            artifacts.get("VERIFY.md", ""),
            _acceptance_section(artifacts.get("PROJECT.md", "")),
            _quality_section(artifacts.get("ENV.md", "")),
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _matrix_rule_prefix(rule_id: str) -> str:
    return rule_id.split(":", 1)[0].upper()


def _matrix_write_scope(rule_id: str) -> list[str]:
    prefix = _matrix_rule_prefix(rule_id)
    return list(_MATRIX_WRITE_SCOPE.get(prefix, ()))


def _matrix_item_id(issue) -> str:
    rule_id = str(issue.rule_id or "").upper()
    message = str(issue.message or "")
    task_match = re.search(r"\bT-\d+(?:-\d+)*\b", message, re.IGNORECASE)
    if rule_id == "MX-1" and task_match:
        return f"MX-1:{task_match.group(0).upper()}"
    return rule_id


def _matrix_stable_key(item_id: str) -> str:
    return f"matrix:{item_id.upper()}"


def _done_tasks_missing_verify(tasks_text: str, verify_text: str) -> list[str]:
    lines = tasks_text.splitlines()
    missing: list[str] = []
    for task in parse_tasks_metadata(tasks_text):
        line_no = int(task.get("line", -1))
        if line_no < 0 or line_no >= len(lines):
            continue
        line = lines[line_no]
        if not _TASK_DONE_RE.match(line):
            continue
        task_id = formal_task_id_from_checkbox_line(line) or str(task.get("id") or "").upper()
        if not task_id:
            continue
        token = task_id.upper()
        if verify_task_documented(verify_text, token):
            continue
        if token not in missing:
            missing.append(token)
    return missing


def build_checklist(paths: AgentPaths, project_id: str) -> Checklist:
    pid = str(project_id or "").strip()
    artifacts = read_project_artifacts(paths, pid)
    fingerprint = compute_source_fingerprint(artifacts)
    items: list[ChecklistItem] = []
    seen_keys: set[str] = set()

    lint = lint_verify_matrix(paths, pid)
    for issue in lint.errors:
        item_id = _matrix_item_id(issue)
        stable_key = _matrix_stable_key(item_id)
        if stable_key in seen_keys:
            continue
        seen_keys.add(stable_key)
        items.append(
            ChecklistItem(
                id=item_id,
                kind="matrix",
                title=str(issue.message or item_id),
                status="pending",
                stable_key=stable_key,
                write_scope=_matrix_write_scope(item_id),
                acceptance={"type": "matrix_rule", "rule_id": _matrix_rule_prefix(item_id)},
            )
        )

    project_text = artifacts.get("PROJECT.md", "")
    acceptance = parse_acceptance_spec(project_text)
    if acceptance is not None:
        stable_key = "acceptance:AC-PROJECT"
        if stable_key not in seen_keys:
            seen_keys.add(stable_key)
            items.append(
                ChecklistItem(
                    id="AC-PROJECT",
                    kind="acceptance",
                    title="PROJECT 硬验收命令",
                    status="pending",
                    stable_key=stable_key,
                    write_scope=[],
                    acceptance={
                        "type": "command",
                        "display": acceptance.display,
                        "expected_exit_code": acceptance.expected_exit_code,
                        "script_rel": acceptance.script_rel,
                        "argv": list(acceptance.argv),
                    },
                )
            )

    tasks_text = artifacts.get("TASKS.md", "")
    verify_text = artifacts.get("VERIFY.md", "")
    for task_id in _done_tasks_missing_verify(tasks_text, verify_text):
        stable_key = f"task:{task_id}"
        if stable_key in seen_keys:
            continue
        if any(item.stable_key == f"matrix:MX-1:{task_id}" for item in items):
            continue
        seen_keys.add(stable_key)
        items.append(
            ChecklistItem(
                id=task_id,
                kind="task",
                title=f"{task_id} 缺少 VERIFY 证据",
                status="pending",
                stable_key=stable_key,
                write_scope=["VERIFY.md"],
                acceptance={"type": "verify_doc", "task_id": task_id},
            )
        )

    return Checklist(
        version=1,
        project_id=pid,
        generated_at=_utc_now(),
        source_fingerprint=fingerprint,
        items=items,
    )


def _item_still_failing(paths: AgentPaths, project_id: str, item: ChecklistItem) -> bool:
    # Command acceptance items describe a check that must be executed; their
    # presence in PROJECT.md is not evidence that the check is failing.
    if item.kind == "acceptance":
        return False
    fresh = build_checklist(paths, project_id)
    for candidate in fresh.items:
        if candidate.stable_key == item.stable_key:
            return True
    return False


def merge_checklist(
    existing: Checklist | None,
    fresh: Checklist,
    *,
    paths: AgentPaths,
    project_id: str,
) -> Checklist:
    if existing is None:
        return fresh

    if existing.source_fingerprint == fresh.source_fingerprint:
        merged_items: list[ChecklistItem] = []
        old_by_key = {item.stable_key: item for item in existing.items}
        for fresh_item in fresh.items:
            old = old_by_key.get(fresh_item.stable_key)
            if old is None:
                merged_items.append(fresh_item)
                continue
            merged = ChecklistItem.from_dict(fresh_item.to_dict())
            merged.attempts = old.attempts
            merged.directed_used = old.directed_used
            merged.last_failure = old.last_failure
            merged.last_run_at = old.last_run_at
            if old.status == "passed":
                if _item_still_failing(paths, project_id, merged):
                    merged.status = "failed" if merged.attempts else "pending"
                else:
                    merged.status = "passed"
            elif old.status in {"failed", "pending", "blocked"}:
                merged.status = old.status if old.status != "running" else "pending"
            merged_items.append(merged)
        fresh.items = merged_items
        fresh.generated_at = _utc_now()
        return fresh

    old_by_key = {item.stable_key: item for item in existing.items}
    merged_items: list[ChecklistItem] = []
    for fresh_item in fresh.items:
        old = old_by_key.get(fresh_item.stable_key)
        if old is None:
            merged_items.append(fresh_item)
            continue
        merged = ChecklistItem.from_dict(fresh_item.to_dict())
        merged.attempts = old.attempts
        merged.directed_used = old.directed_used
        merged.last_failure = old.last_failure
        merged.last_run_at = old.last_run_at
        if old.status == "passed":
            if merged.kind == "acceptance":
                # A changed source fingerprint requires the command to run
                # again, but it is not a known failure until it is executed.
                merged.status = "pending"
            elif _item_still_failing(paths, project_id, merged):
                merged.status = "failed" if merged.attempts else "pending"
        elif old.status in {"failed", "pending", "blocked", "passed"}:
            merged.status = old.status if old.status != "running" else "pending"
        merged_items.append(merged)
    fresh.items = merged_items
    fresh.generated_at = _utc_now()
    return fresh


def load_checklist(paths: AgentPaths, project_id: str) -> Checklist | None:
    path = checklist_path(paths, project_id)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return Checklist.from_dict(raw)


def save_checklist(paths: AgentPaths, checklist: Checklist) -> None:
    path = checklist_path(paths, checklist.project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(checklist.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_or_build_checklist(paths: AgentPaths, project_id: str) -> Checklist:
    existing = load_checklist(paths, project_id)
    fresh = build_checklist(paths, project_id)
    return merge_checklist(existing, fresh, paths=paths, project_id=project_id)


def checklist_counts(checklist: Checklist) -> tuple[int, int]:
    total = len(checklist.items)
    passed = sum(1 for item in checklist.items if item.status == "passed")
    return passed, total


def checklist_all_passed(checklist: Checklist) -> bool:
    return not checklist.items or all(item.status == "passed" for item in checklist.items)


def first_open_item(checklist: Checklist) -> ChecklistItem | None:
    for item in checklist.items:
        if item.status in {"pending", "failed"}:
            return item
    return None


def reset_failed_item_attempts(checklist: Checklist) -> bool:
    item = first_open_item(checklist)
    if item is None or item.status != "failed":
        return False
    item.attempts = 0
    item.directed_used = False
    return True


def reset_failed_item_directed_retry(checklist: Checklist) -> bool:
    """Prepare failed item for one directed retry (RUNAWAY-V2 §6.3 resume buttons)."""
    from runaway_v2.config import directed_after

    item = first_open_item(checklist)
    if item is None or item.status != "failed":
        return False
    item.directed_used = False
    item.attempts = directed_after()
    return True


def prepare_write_scope() -> tuple[str, ...]:
    return _PREPARE_WRITE_SCOPE
