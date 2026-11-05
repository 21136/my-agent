"""Present local image and video files in the Desktop chat UI (read-only; no API cost)."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[2]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from display_media import (
    is_showable_suffix,
    resolve_display_path,
    search_user_media,
    to_display_ref,
)
from host_scope import HostPathDeniedError
from image_references import find_image_references
from paths import AgentPaths, PathOutOfBoundsError
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok

TOOL_NAME = "show_images"
MAX_PATHS = 12


def _is_showable_file(path: Path) -> bool:
    return path.is_file() and is_showable_suffix(path)


def _resolve_showable_path(paths: AgentPaths, raw: str) -> Path:
    resolved = resolve_display_path(paths, raw)
    if not _is_showable_file(resolved):
        raise ValueError(f"not a displayable file: {raw}")
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
            path_list.append(str(search.selected.path))

    query = arguments.get("query")
    if not path_list and isinstance(query, str) and query.strip():
        found = search_user_media(query)
        if found.status == "too_broad":
            return tool_ok(
                TOOL_NAME,
                {
                    "status": "query_too_broad",
                    "display_paths": [],
                    "displayed": [],
                    "llm_hint": "Ask the user for a file name or a more specific phrase; do not scan further.",
                },
                duration_ms=_elapsed_ms(started),
            )
        if found.status == "ambiguous":
            return tool_ok(
                TOOL_NAME,
                {
                    "status": "local_selection_required",
                    "candidates": [item.to_public_dict() for item in found.candidates],
                    "display_paths": [],
                    "displayed": [],
                    "llm_hint": "Ask the user which file to show, then retry show_images with that candidate path.",
                },
                duration_ms=_elapsed_ms(started),
            )
        if found.status == "selected" and found.selected is not None:
            path_list.append(str(found.selected.path))
        else:
            return tool_fail(
                TOOL_NAME,
                "no_images",
                "no matching file in Desktop, Downloads, Documents, Pictures, or Videos",
                duration_ms=_elapsed_ms(started),
            )

    if not path_list:
        return tool_fail(
            TOOL_NAME,
            ToolErrorCode.VALIDATION_ERROR,
            "paths, query, or reference_id/reference_context is required",
            duration_ms=_elapsed_ms(started),
        )
    if len(path_list) > MAX_PATHS:
        return tool_fail(
            TOOL_NAME,
            ToolErrorCode.VALIDATION_ERROR,
            f"at most {MAX_PATHS} files per call",
            duration_ms=_elapsed_ms(started),
        )

    displayed: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    display_paths: list[str] = []
    for raw in path_list:
        try:
            resolved = _resolve_showable_path(paths, raw)
            ref = to_display_ref(paths, resolved)
            displayed.append({"path": ref})
            display_paths.append(ref)
        except (FileNotFoundError, PathOutOfBoundsError, HostPathDeniedError, ValueError, OSError) as exc:
            errors.append({"path": raw, "error": str(exc)})

    if not displayed:
        return tool_fail(
            TOOL_NAME,
            "no_images",
            "no valid files to display",
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
