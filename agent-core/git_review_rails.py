"""Git change-review completion rails — avoid half-done reviews that waste user turns."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from tools.builtin.run_evolved import coalesce_tool_arguments
from tools.schema import ToolResult

_REVIEW_INTENT_RE = re.compile(
    r"(去看看|看我改|审查|审一下|review|diff|改了什么|调整的代码|检查改动|帮我看.*改|代码改动)",
    re.IGNORECASE,
)

_SKIP_PREFIXES = ("artifacts/", "artifacts\\")
_DEFAULT_MAX_GIT_REVIEW_NUDGES = 1


def max_git_review_nudges() -> int:
    raw = os.environ.get("GIT_REVIEW_MAX_NUDGES", str(_DEFAULT_MAX_GIT_REVIEW_NUDGES))
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_MAX_GIT_REVIEW_NUDGES


def user_wants_git_review(user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False
    return bool(_REVIEW_INTENT_RE.search(text))


def _normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/").lstrip("./")


def _should_track_path(path: str) -> bool:
    norm = _normalize_path(path)
    if not norm:
        return False
    return not any(norm.startswith(prefix) for prefix in _SKIP_PREFIXES)


def _split_porcelain_entry(raw_tail: str) -> list[str]:
    """Handle ``R  old -> new`` as well as single paths."""
    tail = raw_tail.strip()
    if " -> " in tail:
        parts = [p.strip().strip('"') for p in tail.split(" -> ") if p.strip()]
        return [p for p in parts[-1:] if p]
    return [tail.strip('"')] if tail else []


def paths_from_status_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        text = line.rstrip("\r\n")
        if len(text) < 4:
            continue
        for candidate in _split_porcelain_entry(text[3:]):
            path = _normalize_path(candidate)
            if _should_track_path(path):
                out.append(path)
    return out


def paths_from_name_status(diff_text: str) -> list[str]:
    out: list[str] = []
    for line in (diff_text or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        path = _normalize_path(parts[-1])
        if _should_track_path(path):
            out.append(path)
    return out


def _unwrap_tool_data(result: ToolResult) -> dict[str, Any]:
    data = result.data if isinstance(result.data, dict) else {}
    if data.get("spilled") and isinstance(data.get("preview"), str):
        preview = data["preview"].strip()
        if preview.startswith("{"):
            try:
                parsed = json.loads(preview)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
    return data


def _evolved_inner(tool_name: str, arguments: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    if tool_name != "run_evolved":
        return None, {}
    raw = arguments.get("tool_name")
    if not isinstance(raw, str) or not raw.strip():
        return None, {}
    inner = coalesce_tool_arguments(arguments)
    return raw.strip(), inner if isinstance(inner, dict) else {}


def _merge_pending(session: Any, paths: list[str]) -> None:
    if not paths:
        return
    reviewed_done = {
        _normalize_path(p)
        for p in (getattr(session, "git_review_completed_paths", None) or [])
    }
    existing = list(getattr(session, "git_review_pending_paths", None) or [])
    seen = {_normalize_path(p) for p in existing} | reviewed_done
    for path in paths:
        norm = _normalize_path(path)
        if norm and norm not in seen:
            existing.append(norm)
            seen.add(norm)
    session.git_review_pending_paths = existing
    session.git_review_listed = True


def _mark_reviewed(session: Any, paths: list[str]) -> None:
    if not paths:
        return
    reviewed = {_normalize_path(p) for p in paths if _normalize_path(p)}
    if not reviewed:
        return
    completed = list(getattr(session, "git_review_completed_paths", None) or [])
    completed_set = {_normalize_path(p) for p in completed}
    for r in reviewed:
        if r not in completed_set:
            completed.append(r)
            completed_set.add(r)
    session.git_review_completed_paths = completed

    pending = list(getattr(session, "git_review_pending_paths", None) or [])
    if pending:
        session.git_review_pending_paths = [
            p for p in pending if _normalize_path(p) not in reviewed
        ]
    if not getattr(session, "git_review_pending_paths", None):
        session.git_review_must_continue = False


def _record_spill_for_paths(session: Any, output_path: str | None, paths: list[str]) -> None:
    if not output_path or not paths:
        return
    mapping = dict(getattr(session, "git_review_spill_paths", None) or {})
    mapping[_normalize_path(output_path)] = [_normalize_path(p) for p in paths if p.strip()]
    session.git_review_spill_paths = mapping


def update_git_review_state(
    session: Any,
    tool_name: str,
    arguments: dict[str, Any],
    result: ToolResult,
) -> None:
    if not result.ok or not getattr(session, "git_review_turn", False):
        return

    if tool_name in {"read_file", "read_utf8_text"}:
        raw_path = arguments.get("path") or arguments.get("file")
        if isinstance(raw_path, str) and raw_path.strip():
            norm = _normalize_path(raw_path)
            r_data = result.data if isinstance(result.data, dict) else {}
            still_truncated = bool(r_data.get("truncated_lines"))
            spill_map = dict(getattr(session, "git_review_spill_paths", None) or {})
            if norm in spill_map:
                if not still_truncated:
                    _mark_reviewed(session, spill_map[norm])
                    spill_map.pop(norm, None)
                    session.git_review_spill_paths = spill_map
            elif not still_truncated:
                _mark_reviewed(session, [norm])
            if (
                "tool_outputs/" in norm
                and not still_truncated
                and not getattr(session, "git_review_pending_paths", None)
                and not getattr(session, "git_review_spill_paths", None)
            ):
                session.git_review_must_continue = False
        return

    inner_name, inner = _evolved_inner(tool_name, arguments)
    if inner_name not in {"git_diff", "git_snapshot"}:
        return

    data = _unwrap_tool_data(result)
    if inner_name == "git_snapshot":
        status_lines = data.get("status_lines")
        if isinstance(status_lines, list):
            _merge_pending(session, paths_from_status_lines([str(x) for x in status_lines]))

    if inner_name == "git_diff" and inner.get("name_status"):
        diff_body = data.get("diff")
        if isinstance(diff_body, str):
            _merge_pending(session, paths_from_name_status(diff_body))

    raw_data = result.data if isinstance(result.data, dict) else {}
    spilled = bool(raw_data.get("spilled")) or bool(result.output_path)
    truncated = bool(
        spilled or data.get("truncated") or data.get("diff_truncated") or result.truncated
    )

    path_specs = inner.get("paths")
    scoped_paths: list[str] = []
    if inner_name == "git_diff" and isinstance(path_specs, list):
        scoped_paths = [p for p in path_specs if isinstance(p, str) and p.strip()]
        if scoped_paths:
            if not truncated:
                _mark_reviewed(session, scoped_paths)
            else:
                _merge_pending(session, scoped_paths)
                _record_spill_for_paths(session, result.output_path, scoped_paths)
                session.git_review_must_continue = True

    whole_tree = (
        inner_name == "git_diff"
        and not scoped_paths
        and not inner.get("name_status")
        and not inner.get("stat_only")
    )
    include_diff = bool(inner.get("include_diff")) if inner_name == "git_snapshot" else False
    if (whole_tree or include_diff) and (spilled or truncated):
        if not getattr(session, "git_review_pending_paths", None) and not getattr(
            session, "git_review_completed_paths", None
        ):
            session.git_review_must_continue = True


def git_review_incomplete(session: Any) -> bool:
    if not getattr(session, "git_review_turn", False):
        return False
    pending = list(getattr(session, "git_review_pending_paths", None) or [])
    if pending:
        return True
    return bool(getattr(session, "git_review_must_continue", False))


def pending_git_review_nudge(session: Any) -> str | None:
    if not git_review_incomplete(session):
        return None
    nudges = int(getattr(session, "git_review_nudge_count", 0) or 0)
    if nudges >= max_git_review_nudges():
        return None
    pending = list(getattr(session, "git_review_pending_paths", None) or [])
    if pending:
        sample = ", ".join(pending[:6])
        extra = len(pending) - 6
        tail = f" 等 {extra} 个" if extra > 0 else ""
        return (
            "[内核] git 改动审阅未完成：尚有 "
            f"{len(pending)} 个文件未逐路径查看（如 {sample}{tail}）。"
            "请继续 git_diff paths=[…] 或 read_file，最后再合并成**一次**完整结论（勿分段重复输出）。"
        )
    return (
        "[内核] 整仓 git diff 被截断且尚未按文件拆看。请 git_diff name_status 列清单后按 paths 查看，"
        "最后再给一次完整结论。"
    )


def format_git_review_discipline_line(session: Any) -> str:
    if not getattr(session, "git_review_turn", False):
        return ""
    pending = list(getattr(session, "git_review_pending_paths", None) or [])
    if pending:
        return (
            f"git_review: incomplete — 尚有 {len(pending)} 个改动文件待查看；"
            "先调完 git_diff/read_file，最后只输出一次完整结论。"
        )
    if getattr(session, "git_review_must_continue", False):
        return (
            "git_review: in_progress — 先按文件看全改动，最后只输出一次完整审查结论。"
        )
    return ""
