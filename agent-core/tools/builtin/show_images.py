"""Present local image files in the Desktop chat UI (read-only; no API cost)."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[2]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths, PathOutOfBoundsError
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok

TOOL_NAME = "show_images"
MAX_PATHS = 12
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def _is_image_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES


def _resolve_image_path(paths: AgentPaths, raw: str) -> Path:
    from tools.builtin.read_file import resolve_read_path

    resolved = resolve_read_path(paths, raw)
    if not _is_image_file(resolved):
        raise ValueError(f"not an image file: {raw}")
    return resolved


def run(
    arguments: dict[str, Any],
    *,
    paths: AgentPaths,
    current_session_dir: Path | None = None,
) -> ToolResult:
    started = time.perf_counter()
    raw_paths = arguments.get("paths")
    path_list: list[str] = []
    if isinstance(raw_paths, list):
        path_list = [str(item).strip() for item in raw_paths if isinstance(item, str) and item.strip()]
    elif isinstance(raw_paths, str) and raw_paths.strip():
        path_list = [raw_paths.strip()]

    reference_id = arguments.get("reference_id")
    reference_context = arguments.get("reference_context")
    if reference_id or reference_context:
        from image_references import find_image_references

        search = find_image_references(
            str(reference_context or ""),
            paths=paths,
            current_session_dir=current_session_dir,
            reference_id=str(reference_id) if reference_id else None,
        )
        if search.status == "ambiguous":
            return tool_ok(
                TOOL_NAME,
                {
                    "status": "reference_selection_required",
                    "candidates": [c.to_public_dict() for c in search.candidates],
                    "display_paths": [],
                    "displayed": [],
                },
                duration_ms=_elapsed_ms(started),
            )
        if search.status == "selected" and search.selected is not None:
            path_list.append(search.selected.path)

    if not path_list:
        return tool_fail(
            TOOL_NAME,
            ToolErrorCode.VALIDATION_ERROR,
            "paths or reference_id/reference_context is required",
            duration_ms=_elapsed_ms(started),
        )
    if len(path_list) > MAX_PATHS:
        return tool_fail(
            TOOL_NAME,
            ToolErrorCode.VALIDATION_ERROR,
            f"at most {MAX_PATHS} images per call",
            duration_ms=_elapsed_ms(started),
        )

    displayed: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    display_paths: list[str] = []
    for raw in path_list:
        try:
            resolved = _resolve_image_path(paths, raw)
            rel = paths.to_agent_relative(resolved)
            displayed.append({"path": rel})
            display_paths.append(rel)
        except (FileNotFoundError, PathOutOfBoundsError, ValueError, OSError) as exc:
            errors.append({"path": raw, "error": str(exc)})

    if not displayed:
        return tool_fail(
            TOOL_NAME,
            "no_images",
            "no valid image paths to display",
            duration_ms=_elapsed_ms(started),
            details={"errors": errors},
        )

    payload: dict[str, Any] = {
        "displayed": displayed,
        "display_paths": display_paths,
        "path": display_paths[0],
        "count": len(displayed),
    }
    if errors:
        payload["skipped"] = errors
    return tool_ok(TOOL_NAME, payload, duration_ms=_elapsed_ms(started))


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
