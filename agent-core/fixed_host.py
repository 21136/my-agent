"""Strict private bootstrap parsing for the fixed Host data layout."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

MAX_FIXED_HOST_LAYOUT_BYTES = 4096
FIXED_HOST_LAYOUT_PROTOCOL = "my-agent-fixed-layout-v1"


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("fixed Host layout contains duplicate fields")
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    """Match the parent launcher's compact, recursively key-sorted canonical JSON format."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def parse_fixed_host_layout(raw: bytes) -> dict[str, Any]:
    """Parse exactly one bounded canonical UTF-8 JSON line from private stdin."""
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_FIXED_HOST_LAYOUT_BYTES:
        raise ValueError("fixed Host layout is empty or exceeds the frame limit")
    if not raw.endswith(b"\n") or b"\n" in raw[:-1] or b"\r" in raw:
        raise ValueError("fixed Host layout must be one newline-terminated frame")
    try:
        payload = json.loads(
            raw[:-1].decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("fixed Host layout is invalid JSON") from exc
    fields = {"protocol", "code_root", "instance_root", "runtime"}
    if type(payload) is not dict or set(payload) != fields:
        raise ValueError("fixed Host layout fields are invalid")
    if _canonical_json(payload) + b"\n" != raw:
        raise ValueError("fixed Host layout must use canonical JSON")
    if payload.get("protocol") != FIXED_HOST_LAYOUT_PROTOCOL:
        raise ValueError("fixed Host layout protocol is invalid")
    for key in ("code_root", "instance_root"):
        value = payload.get(key)
        if not isinstance(value, str) or not value or not Path(value).is_absolute():
            raise ValueError("fixed Host layout roots must be absolute paths")
    runtime = payload.get("runtime")
    runtime_fields = {"python_executable", "python_root", "python_base_root"}
    if type(runtime) is not dict or set(runtime) != runtime_fields:
        raise ValueError("fixed Host runtime fields are invalid")
    for key in runtime_fields:
        value = runtime.get(key)
        if not isinstance(value, str) or not value or not Path(value).is_absolute():
            raise ValueError("fixed Host runtime paths must be absolute")
    return payload


def read_fixed_host_layout(stream: Any) -> dict[str, Any]:
    """Read exactly one bounded frame, rejecting trailing bytes/frames."""
    raw = stream.readline(MAX_FIXED_HOST_LAYOUT_BYTES + 1)
    if len(raw) > MAX_FIXED_HOST_LAYOUT_BYTES or stream.read(1):
        raise ValueError("fixed Host layout must contain exactly one bounded frame")
    return parse_fixed_host_layout(raw)


def canonical_layout_root(raw: str | os.PathLike[str], *, label: str) -> Path:
    """Resolve an existing directory and reject links/junction aliases in its path."""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be absolute")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} does not exist") from exc
    if not resolved.is_dir():
        raise ValueError(f"{label} must be a directory")

    # Refuse any reparse/link component rather than silently trusting its target.
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            is_junction = getattr(current, "is_junction", lambda: False)()
            if current.is_symlink() or is_junction:
                raise ValueError(f"{label} cannot contain links or junctions")
        except OSError as exc:
            raise ValueError(f"{label} cannot be inspected safely") from exc
    if os.path.normcase(str(path)) != os.path.normcase(str(resolved)):
        raise ValueError(f"{label} is not a canonical path")
    return resolved


def paths_overlap(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False
