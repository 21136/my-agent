"""patch_file — replace text by line range or unique anchor (P3 coding)."""

from __future__ import annotations

import json
import hashlib
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_MAX_BYTES = 512 * 1024
_MAX_HUNKS = 128
_PATCH_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _agent_root() -> Path:
    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        evolve_marker = directory / "evolve"
        if (evolve_marker / "_index.core.toml").is_file() or (evolve_marker / "_index.toml").is_file():
            return directory
    raise RuntimeError("could not locate agent root")


def _agent_core_dir() -> Path:
    return _agent_root() / "agent-core"


def _load_paths():
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from paths import AgentPaths, PathOutOfBoundsError

    return AgentPaths, PathOutOfBoundsError


def _is_binary(sample: bytes) -> bool:
    return b"\0" in sample


def _apply_line_patch(lines: list[str], start: int, end: int, replacement: str) -> list[str]:
    if start < 1 or end < start or end > len(lines):
        raise ValueError(f"invalid line range {start}-{end} for {len(lines)} lines")
    chunk = replacement if replacement.endswith("\n") else replacement + "\n"
    return lines[: start - 1] + [chunk] + lines[end:]


def _read_text_lines(target: Path) -> list[str]:
    with target.open(encoding="utf-8", newline="") as handle:
        return handle.readlines()


def _write_text_lines(target: Path, lines: list[str]) -> None:
    from evolve_tool_io import write_utf8_text

    write_utf8_text(target, "".join(lines))


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _normalized_bytes(text: str) -> bytes:
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _operation_hash(payload: dict[str, Any], rel: str) -> str:
    identity = {
        "path": rel,
        "start_line": payload.get("start_line"),
        "end_line": payload.get("end_line"),
        "find": payload.get("find"),
        "replacement": payload.get("replacement"),
        "hunks": payload.get("hunks", payload.get("replacements")),
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256(encoded)


def _patch_id(payload: dict[str, Any], rel: str, operation_hash: str) -> tuple[str | None, str | None]:
    explicit = payload.get("patch_id")
    if explicit is not None:
        if not isinstance(explicit, str) or not _PATCH_ID_RE.fullmatch(explicit.strip()):
            return None, "patch_id must match /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/"
        return explicit.strip(), None
    return f"auto-{operation_hash[:24]}", None


def _ledger_path(paths) -> Path:
    return paths.data / "patch-ledger.jsonl"


def _find_ledger_entry(ledger: Path, patch_id: str) -> dict[str, Any] | None:
    if not ledger.is_file():
        return None
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("patch_id") == patch_id:
            return item
    return None


def _append_ledger(ledger: Path, entry: dict[str, Any]) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def _apply_hunks(text: str, hunks: Any) -> tuple[str, int]:
    if not isinstance(hunks, list) or not hunks:
        raise ValueError("hunks must be a non-empty array")
    if len(hunks) > _MAX_HUNKS:
        raise ValueError(f"hunks exceeds limit of {_MAX_HUNKS}")
    current = text
    for index, hunk in enumerate(hunks, start=1):
        if not isinstance(hunk, dict):
            raise ValueError(f"hunks[{index - 1}] must be an object")
        find = hunk.get("find")
        replacement = hunk.get("replacement")
        if not isinstance(find, str) or not find:
            raise ValueError(f"hunks[{index - 1}].find must be a non-empty string")
        if not isinstance(replacement, str):
            raise ValueError(f"hunks[{index - 1}].replacement must be a string")
        count = current.count(find)
        if count == 0:
            raise ValueError(f"hunks[{index - 1}] anchor not found")
        if count > 1:
            raise ValueError(f"hunks[{index - 1}] anchor matched {count} times; must be unique")
        current = current.replace(find, replacement, 1)
    return current, len(hunks)


def _apply_find_patch(text: str, find: str, replacement: str) -> tuple[str, int]:
    count = text.count(find)
    if count == 0:
        raise ValueError("find anchor not found")
    if count > 1:
        raise ValueError(f"find anchor matched {count} times; must be unique")
    return text.replace(find, replacement, 1), 1


def run_patch(payload: dict[str, Any]) -> dict[str, Any]:
    AgentPaths, PathOutOfBoundsError = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    path_arg = payload.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return {"ok": False, "error": "path is required"}

    replacement = payload.get("replacement")
    start_line = payload.get("start_line")
    end_line = payload.get("end_line")
    find = payload.get("find")
    hunks = payload.get("hunks", payload.get("replacements"))
    base_hash = payload.get("base_hash")
    dry_run = bool(payload.get("dry_run", False))

    try:
        target = paths.resolve_under_agent(path_arg, must_exist=True)
    except PathOutOfBoundsError as exc:
        return {"ok": False, "error": str(exc)}
    except (TypeError, ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}

    if not target.is_file():
        return {"ok": False, "error": f"not a file: {paths.to_agent_relative(target)}"}

    size = target.stat().st_size
    if size > _MAX_BYTES:
        return {"ok": False, "error": f"file exceeds limit of {_MAX_BYTES} bytes"}

    raw = target.read_bytes()
    if _is_binary(raw[:8192]):
        return {"ok": False, "error": "binary file not supported"}

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "error": "file is not valid UTF-8"}
    rel = paths.to_agent_relative(target)
    current_hash = _sha256(raw)
    if base_hash is not None:
        if not isinstance(base_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", base_hash.strip()):
            return {"ok": False, "error": "base_hash must be a SHA-256 hex digest"}
        base_hash = base_hash.lower()

    operation_hash = _operation_hash(payload, rel)
    patch_id, patch_id_error = _patch_id(payload, rel, operation_hash)
    if patch_id_error:
        return {"ok": False, "error": patch_id_error}
    assert patch_id
    ledger = _ledger_path(paths)
    if dry_run:
        previous = _find_ledger_entry(ledger, patch_id) if ledger.is_file() else None
    else:
        with _file_lock(ledger.with_suffix(".lock")):
            previous = _find_ledger_entry(ledger, patch_id)
    if previous is not None:
        if previous.get("path") != rel:
            return {"ok": False, "error": f"patch_id already belongs to {previous.get('path')}"}
        if previous.get("operation_hash") not in {None, operation_hash}:
            return {"ok": False, "error": "patch_id is already associated with a different patch payload"}
        result_hash = previous.get("result_hash")
        if result_hash == current_hash:
            return {
                "ok": True,
                "path": rel,
                "patch_id": patch_id,
                "idempotent": True,
                "skipped": True,
                "base_hash": previous.get("base_hash"),
                "result_hash": result_hash,
            }
        return {
            "ok": False,
            "error": "patch_id was already applied but the file now has a different hash",
            "path": rel,
            "patch_id": patch_id,
            "current_hash": current_hash,
            "expected_hash": result_hash,
        }
    if base_hash is not None and base_hash != current_hash:
        return {
            "ok": False,
            "error": "base_hash mismatch; file changed since the patch was prepared",
            "path": rel,
            "base_hash": base_hash,
            "current_hash": current_hash,
        }

    has_range = start_line is not None or end_line is not None
    if hunks is not None:
        if replacement is not None or has_range or find:
            return {"ok": False, "error": "hunks cannot be combined with replacement, line range or find"}
        try:
            new_text, hunks_applied = _apply_hunks(text, hunks)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "patch_id": patch_id}
        mode = "hunks"
        lines_changed = hunks_applied
    elif not isinstance(replacement, str):
        return {"ok": False, "error": "replacement is required unless hunks is provided"}
    elif has_range:
        if not isinstance(start_line, int) or not isinstance(end_line, int):
            return {"ok": False, "error": "start_line and end_line are required together"}
        lines = _read_text_lines(target)
        try:
            new_lines = _apply_line_patch(lines, start_line, end_line, replacement)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        new_text = "".join(new_lines)
        mode = "line_range"
        lines_changed = end_line - start_line + 1
    elif isinstance(find, str) and find:
        try:
            new_text, _ = _apply_find_patch(text, find, replacement)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        mode = "find"
        lines_changed = 1
    else:
        return {"ok": False, "error": "provide start_line+end_line or find"}

    if new_text == text:
        return {
            "ok": True,
            "path": rel,
            "patch_id": patch_id,
            "base_hash": current_hash,
            "result_hash": current_hash,
            "mode": mode,
            "lines_changed": 0,
            "skipped": True,
        }

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "path": rel,
            "patch_id": patch_id,
            "base_hash": current_hash,
            "result_hash": _sha256(_normalized_bytes(new_text)),
            "mode": mode,
            "lines_changed": lines_changed,
            "hunks_applied": lines_changed if mode == "hunks" else None,
        }

    result_raw = _normalized_bytes(new_text)
    if len(result_raw) > _MAX_BYTES:
        return {"ok": False, "error": f"result exceeds limit of {_MAX_BYTES} bytes", "patch_id": patch_id}

    try:
        target_lock = paths.data / "patch-locks" / f"{_sha256(rel.encode('utf-8'))[:32]}.lock"
        with _file_lock(target_lock), _file_lock(ledger.with_suffix(".lock")):
            previous = _find_ledger_entry(ledger, patch_id)
            locked_raw = target.read_bytes()
            locked_hash = _sha256(locked_raw)
            if previous is not None and previous.get("operation_hash") not in {None, operation_hash}:
                return {"ok": False, "error": "patch_id ledger conflict after write", "patch_id": patch_id}
            if previous is not None and previous.get("result_hash") == locked_hash:
                return {
                    "ok": True,
                    "path": rel,
                    "patch_id": patch_id,
                    "idempotent": True,
                    "skipped": True,
                    "base_hash": previous.get("base_hash"),
                    "result_hash": locked_hash,
                }
            if locked_hash != current_hash:
                return {
                    "ok": False,
                    "error": "file changed while patch was being prepared",
                    "path": rel,
                    "patch_id": patch_id,
                    "current_hash": locked_hash,
                    "expected_hash": current_hash,
                }
            if has_range:
                _write_text_lines(target, new_lines)
            else:
                from evolve_tool_io import write_utf8_text

                write_utf8_text(target, new_text)
            result_hash = _sha256(result_raw)
            entry = {
                "patch_id": patch_id,
                "source": "patch_file",
                "path": rel,
                "mode": mode,
                "base_hash": current_hash,
                "result_hash": result_hash,
                "old_hash": current_hash,
                "new_hash": result_hash,
                "operation_hash": operation_hash,
                "recorded_at": _utc_now(),
            }
            _append_ledger(ledger, entry)
    except OSError as exc:
        return {"ok": False, "error": f"patch or ledger write failed: {exc}", "patch_id": patch_id}

    return {
        "ok": True,
        "path": rel,
        "patch_id": patch_id,
        "base_hash": current_hash,
        "result_hash": result_hash,
        "mode": mode,
        "lines_changed": lines_changed,
        "hunks_applied": lines_changed if mode == "hunks" else None,
    }


def main() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_patch)


def _demo() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from paths import AgentPaths
    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    paths = AgentPaths.discover()
    registry = ToolRegistry.load(paths)
    tool = registry.get_evolved("patch_file")
    assert tool is not None and tool.scope == "coding"
    print("[PASS] registry loads patch_file (coding, active)")

    rel = "workspace/_patch_demo.txt"
    target = paths.workspace / "_patch_demo.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    dry = run(
        {
            "tool_name": "patch_file",
            "arguments": {
                "path": rel,
                "start_line": 2,
                "end_line": 2,
                "replacement": "BETA\n",
            },
            "dry_run": True,
        },
        registry=registry,
    )
    assert dry.ok and target.read_text(encoding="utf-8") == "alpha\nbeta\ngamma\n"
    print("[PASS] dry_run does not write")

    live = run(
        {
            "tool_name": "patch_file",
            "arguments": {
                "path": rel,
                "start_line": 2,
                "end_line": 2,
                "replacement": "BETA\n",
            },
            "dry_run": False,
        },
        registry=registry,
    )
    assert live.ok and target.read_text(encoding="utf-8") == "alpha\nBETA\ngamma\n"
    print("[PASS] line_range patch")

    anchor = run(
        {
            "tool_name": "patch_file",
            "arguments": {
                "path": rel,
                "find": "gamma",
                "replacement": "GAMMA",
            },
            "dry_run": False,
        },
        registry=registry,
    )
    assert anchor.ok and "GAMMA" in target.read_text(encoding="utf-8")
    print("[PASS] find patch")

    crlf_path = paths.workspace / "_patch_crlf_demo.txt"
    crlf_path.write_bytes(b"line1\r\nline2\r\n")
    crlf_rel = "workspace/_patch_crlf_demo.txt"
    for i in range(2):
        step = run(
            {
                "tool_name": "patch_file",
                "arguments": {
                    "path": crlf_rel,
                    "find": f"line{i + 1}",
                    "replacement": f"LINE{i + 1}",
                },
                "dry_run": False,
            },
            registry=registry,
        )
        assert step.ok
        assert b"\r\r" not in crlf_path.read_bytes()
    print("[PASS] find patch on CRLF does not multiply carriage returns")

    dup = run(
        {
            "tool_name": "patch_file",
            "arguments": {"path": rel, "find": "a", "replacement": "x"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert not dup.ok
    print("[PASS] non-unique find rejected")

    target.unlink(missing_ok=True)
    crlf_path.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
