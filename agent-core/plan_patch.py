"""Plan-domain file patches (PLAN-ARCH M6 · A8/A9).

LLM proposes search/replace hunks; humans accept via sidebar; then apply.
"""

from __future__ import annotations

import hashlib
import re
from difflib import unified_diff
from pathlib import Path
from typing import Any

from paths import AgentPaths
from project_mode import ProjectModeError, project_dir

PLAN_PATCH_ALLOWLIST = frozenset({"TASKS.md", "MAP.md", "PROJECT.md", "ENV.md"})

_PATH_SAFE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def normalize_plan_relpath(path: str) -> str:
    raw = str(path or "").strip().replace("\\", "/")
    if not raw:
        raise ProjectModeError("patch path required")
    name = Path(raw).name
    if name != raw and "/" in raw.strip("/"):
        # only bare filenames under project root
        raise ProjectModeError(f"patch path must be a plan-domain basename, got {path!r}")
    if not _PATH_SAFE.match(name):
        raise ProjectModeError(f"invalid patch path: {path!r}")
    if name not in PLAN_PATCH_ALLOWLIST:
        raise ProjectModeError(
            f"patch path not allowed: {name} (allowed: {', '.join(sorted(PLAN_PATCH_ALLOWLIST))})"
        )
    return name


def plan_file_path(paths: AgentPaths, project_id: str, relpath: str) -> Path:
    name = normalize_plan_relpath(relpath)
    return project_dir(paths, project_id) / name


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def apply_replacements(text: str, replacements: list[dict[str, Any]]) -> str:
    """Apply ordered exact substring replacements. Each old must occur exactly once."""
    out = text
    if not isinstance(replacements, list) or not replacements:
        raise ProjectModeError("patch requires non-empty replacements[]")
    for i, item in enumerate(replacements):
        if not isinstance(item, dict):
            raise ProjectModeError(f"replacements[{i}] must be an object")
        old = item.get("old")
        new = item.get("new")
        if not isinstance(old, str) or old == "":
            raise ProjectModeError(f"replacements[{i}].old must be a non-empty string")
        if not isinstance(new, str):
            raise ProjectModeError(f"replacements[{i}].new must be a string")
        count = out.count(old)
        if count == 0:
            raise ProjectModeError(f"replacements[{i}].old not found in file")
        if count > 1:
            raise ProjectModeError(
                f"replacements[{i}].old matches {count} times; must be unique"
            )
        out = out.replace(old, new, 1)
    return out


def diff_preview(before: str, after: str, *, max_lines: int = 60) -> str:
    """Unified diff snippet for sidebar (no file headers noise beyond ---/+++)."""
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    if not before_lines and before:
        before_lines = [before]
    if not after_lines and after:
        after_lines = [after]
    b = before.splitlines()
    a = after.splitlines()
    lines = list(
        unified_diff(b, a, fromfile="before", tofile="after", lineterm="")
    )
    if not lines:
        return "(no textual change)"
    kept: list[str] = []
    for ln in lines:
        if ln.startswith("---") or ln.startswith("+++"):
            continue
        kept.append(ln)
        if len(kept) >= max_lines:
            kept.append("…")
            break
    return "\n".join(kept) if kept else "\n".join(lines[:max_lines])


def build_patch_preview(
    paths: AgentPaths,
    project_id: str,
    *,
    relpath: str,
    replacements: list[dict[str, Any]],
    base_hash: str | None = None,
) -> dict[str, Any]:
    """Validate patch against current disk; return preview fields (does not write)."""
    name = normalize_plan_relpath(relpath)
    path = plan_file_path(paths, project_id, name)
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    current_hash = content_hash(current)
    if base_hash and str(base_hash).strip() and str(base_hash).strip() != current_hash:
        raise ProjectModeError(
            f"patch base_hash mismatch for {name} (file changed; re-propose)"
        )
    after = apply_replacements(current, replacements)
    if after == current:
        raise ProjectModeError(f"patch for {name} makes no change")
    return {
        "path": name,
        "base_hash": current_hash,
        "diff": diff_preview(current, after),
        "before": current,
        "after": after,
    }


def apply_plan_patch(
    paths: AgentPaths,
    project_id: str,
    *,
    relpath: str,
    replacements: list[dict[str, Any]],
    base_hash: str | None = None,
) -> dict[str, Any]:
    """Apply a gated patch to a plan-domain file."""
    preview = build_patch_preview(
        paths,
        project_id,
        relpath=relpath,
        replacements=replacements,
        base_hash=base_hash,
    )
    path = plan_file_path(paths, project_id, preview["path"])
    text = preview["after"]
    if not text.endswith("\n"):
        text += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {
        "ok": True,
        "path": preview["path"],
        "base_hash": preview["base_hash"],
        "diff": preview["diff"],
    }
