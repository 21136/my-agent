"""Host-scoped read/write helpers (HOST-SCOPE T-1005, T-1006)."""

from __future__ import annotations

import fnmatch
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from host_scope import (
    HostPathDeniedError,
    HostRootNotFoundError,
    HostScopePermissionError,
    ResolvedHostPath,
    add_host_root,
    empty_host_scope,
    host_scope_file,
    load_host_scope,
    resolve_host_path,
    save_host_scope,
)
from paths import AgentPaths, PathOutOfBoundsError
from tools.schema import ToolErrorCode

MAX_BYTES = 512 * 1024
DEFAULT_MAX_RESULTS = 50
_VALID_CONFLICTS = frozenset({"skip", "overwrite", "rename"})
_VALID_OPS = frozenset({"copy", "move"})


@dataclass(frozen=True, slots=True)
class WorkflowDir:
    """Resolved workflow target directory (workspace or host scope)."""

    paths: AgentPaths
    absolute: Path
    label: str
    is_host: bool
    host_id: str | None = None
    host_root: Path | None = None

    def display_path(self, path: Path) -> str:
        if self.is_host:
            if self.host_id is None or self.host_root is None:
                raise RuntimeError("host workflow dir missing host metadata")
            rel = path.resolve().relative_to(self.host_root.resolve()).as_posix()
            return f"host:{self.host_id}/{rel}"
        return self.paths.to_agent_relative(path)

    def log_fields(self) -> dict[str, str]:
        if self.is_host and self.host_id:
            return {"host_root_id": self.host_id}
        return {}


def resolve_workflow_dir(
    paths: AgentPaths,
    path_arg: str,
    *,
    write: bool = True,
) -> WorkflowDir:
    """Resolve workspace-relative or ``host:<id>/…`` directory for workflow tools."""
    text = path_arg.strip()
    if not text:
        raise ValueError("path is required")

    if text.lower().startswith("host:"):
        config = load_host_scope(paths)
        resolved = resolve_host_path(
            _host_uri(text),
            config=config,
            write=write,
            must_exist=True,
        )
        if not resolved.absolute.is_dir():
            raise FileNotFoundError(f"not a directory: {path_arg}")
        label = (
            f"host:{resolved.host_id}"
            if resolved.relative == "."
            else f"host:{resolved.host_id}/{resolved.relative}"
        )
        return WorkflowDir(
            paths=paths,
            absolute=resolved.absolute,
            label=label,
            is_host=True,
            host_id=resolved.host_id,
            host_root=resolved.host_root,
        )

    source_dir = paths.resolve_under_agent(text, must_exist=True)
    if not source_dir.is_dir():
        raise FileNotFoundError(f"not a directory: {text}")
    return WorkflowDir(
        paths=paths,
        absolute=source_dir,
        label=paths.to_agent_relative(source_dir),
        is_host=False,
    )


def _host_uri(path_arg: str) -> str:
    text = path_arg.strip()
    if not text.lower().startswith("host:"):
        raise ValueError(f"path must use host:<id>/relative form: {path_arg!r}")
    return text


def _resolve_read(paths: AgentPaths, path_arg: str) -> tuple[ResolvedHostPath, str]:
    config = load_host_scope(paths)
    resolved = resolve_host_path(
        _host_uri(path_arg),
        config=config,
        write=False,
        must_exist=False,
    )
    display = (
        f"host:{resolved.host_id}"
        if resolved.relative == "."
        else f"host:{resolved.host_id}/{resolved.relative}"
    )
    return resolved, display


def _resolve_host_endpoints(
    paths: AgentPaths,
    source_arg: str,
    dest_arg: str,
    *,
    source_must_exist: bool = True,
) -> tuple[ResolvedHostPath, ResolvedHostPath]:
    config = load_host_scope(paths)
    source = resolve_host_path(
        _host_uri(source_arg),
        config=config,
        write=False,
        must_exist=source_must_exist,
    )
    dest = resolve_host_path(
        _host_uri(dest_arg),
        config=config,
        write=True,
        must_exist=False,
    )
    return source, dest


def _host_log_fields(
    source: ResolvedHostPath,
    dest: ResolvedHostPath,
) -> dict[str, str]:
    fields = {
        "host_src_id": source.host_id,
        "host_src_rel": source.relative,
        "host_dst_id": dest.host_id,
        "host_dst_rel": dest.relative,
        "source_absolute": source.absolute.as_posix(),
        "dest_absolute": dest.absolute.as_posix(),
    }
    if source.host_id == dest.host_id:
        fields["host_root_id"] = source.host_id
    return fields


def host_path_confirm_line(
    paths: AgentPaths,
    label: str,
    raw: str,
    *,
    write: bool,
) -> str:
    config = load_host_scope(paths)
    resolved = resolve_host_path(
        _host_uri(raw),
        config=config,
        write=write,
        must_exist=False,
    )
    return f"{label}: {resolved.absolute} (host:{resolved.host_id})"


def _renamed_target(target: Path) -> Path:
    parent = target.parent
    stem = target.stem
    suffix = target.suffix
    index = 1
    while True:
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _resolve_dest(
    source: Path,
    dest: Path,
    *,
    on_conflict: str,
) -> tuple[Path | None, bool]:
    if not dest.exists():
        return dest, False

    if source.is_dir() and dest.is_dir():
        if on_conflict == "skip":
            return None, True
        if on_conflict == "overwrite":
            return dest, False
        return _renamed_target(dest.parent / source.name), False

    if dest.is_dir():
        candidate = dest / source.name
        if not candidate.exists():
            return candidate, False
        dest = candidate

    if on_conflict == "skip":
        return None, True
    if on_conflict == "rename":
        return _renamed_target(dest), False
    return dest, False


def run_host_copy_move(payload: dict[str, Any], *, paths: AgentPaths | None = None) -> dict[str, Any]:
    paths = paths or AgentPaths.discover()

    operation = payload.get("operation")
    if not isinstance(operation, str) or operation.strip().lower() not in _VALID_OPS:
        return {
            "ok": False,
            "error": f"operation must be one of {sorted(_VALID_OPS)}",
            "code": ToolErrorCode.VALIDATION_ERROR,
        }
    operation = operation.strip().lower()

    source_arg = payload.get("source")
    dest_arg = payload.get("dest")
    if not isinstance(source_arg, str) or not source_arg.strip():
        return {"ok": False, "error": "source is required", "code": ToolErrorCode.VALIDATION_ERROR}
    if not isinstance(dest_arg, str) or not dest_arg.strip():
        return {"ok": False, "error": "dest is required", "code": ToolErrorCode.VALIDATION_ERROR}

    on_conflict = payload.get("on_conflict", "skip")
    if not isinstance(on_conflict, str):
        return {"ok": False, "error": "on_conflict must be a string", "code": ToolErrorCode.VALIDATION_ERROR}
    on_conflict = on_conflict.strip().lower()
    if on_conflict not in _VALID_CONFLICTS:
        return {
            "ok": False,
            "error": f"on_conflict must be one of {sorted(_VALID_CONFLICTS)}",
            "code": ToolErrorCode.VALIDATION_ERROR,
        }

    dry_run = bool(payload.get("dry_run", False))

    try:
        source_resolved, dest_resolved = _resolve_host_endpoints(
            paths, source_arg, dest_arg, source_must_exist=True
        )
    except (ValueError, HostPathDeniedError, HostRootNotFoundError, HostScopePermissionError, PathOutOfBoundsError, FileNotFoundError) as exc:
        return _error_dict(exc, path=source_arg)

    source = source_resolved.absolute
    dest = dest_resolved.absolute

    if source.resolve() == dest.resolve():
        return {"ok": False, "error": "source and dest must differ", "code": ToolErrorCode.VALIDATION_ERROR}

    if source.is_dir():
        try:
            dest.resolve().relative_to(source.resolve())
            return {
                "ok": False,
                "error": "dest cannot be inside source",
                "code": ToolErrorCode.VALIDATION_ERROR,
            }
        except ValueError:
            pass

    resolved_dest, skipped = _resolve_dest(source, dest, on_conflict=on_conflict)
    log_fields = _host_log_fields(source_resolved, dest_resolved)
    src_uri = (
        f"host:{source_resolved.host_id}"
        if source_resolved.relative == "."
        else f"host:{source_resolved.host_id}/{source_resolved.relative}"
    )
    if resolved_dest is None or skipped:
        return {
            "ok": True,
            "operation": operation,
            "source": src_uri,
            "dest": dest_arg,
            "skipped": True,
            **log_fields,
        }

    dest_rel = resolved_dest.relative_to(dest_resolved.host_root.resolve()).as_posix()
    dest_uri = f"host:{dest_resolved.host_id}/{dest_rel}"

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "operation": operation,
            "source": src_uri,
            "dest": dest_uri,
            "skipped": False,
            **log_fields,
        }

    try:
        resolved_dest.parent.mkdir(parents=True, exist_ok=True)
        if operation == "copy":
            if source.is_dir():
                if resolved_dest.exists() and on_conflict == "overwrite":
                    shutil.rmtree(resolved_dest)
                shutil.copytree(source, resolved_dest)
            else:
                shutil.copy2(source, resolved_dest)
        else:
            if resolved_dest.exists() and on_conflict == "overwrite":
                if resolved_dest.is_dir():
                    shutil.rmtree(resolved_dest)
                else:
                    resolved_dest.unlink()
            shutil.move(str(source), str(resolved_dest))
    except OSError as exc:
        return {"ok": False, "error": str(exc), "code": ToolErrorCode.PERMISSION_DENIED, **log_fields}

    return {
        "ok": True,
        "operation": operation,
        "source": src_uri,
        "dest": dest_uri,
        "skipped": False,
        **log_fields,
    }


def _error_dict(exc: Exception, *, path: str | None = None) -> dict[str, Any]:
    if isinstance(exc, HostPathDeniedError):
        code = ToolErrorCode.PATH_DENIED
    elif isinstance(exc, HostRootNotFoundError):
        code = ToolErrorCode.NOT_FOUND
    elif isinstance(exc, HostScopePermissionError):
        code = ToolErrorCode.PERMISSION_DENIED
    elif isinstance(exc, PathOutOfBoundsError):
        code = ToolErrorCode.PATH_OUT_OF_BOUNDS
    elif isinstance(exc, FileNotFoundError):
        code = ToolErrorCode.NOT_FOUND
    else:
        code = ToolErrorCode.VALIDATION_ERROR
    payload: dict[str, Any] = {"ok": False, "error": str(exc), "code": str(code)}
    if path is not None:
        payload["path"] = path
    return payload


def run_host_list(payload: dict[str, Any], *, paths: AgentPaths | None = None) -> dict[str, Any]:
    paths = paths or AgentPaths.discover()
    path_arg = payload.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return {"ok": False, "error": "path is required", "code": ToolErrorCode.VALIDATION_ERROR}

    recursive = payload.get("recursive", False)
    if not isinstance(recursive, bool):
        return {"ok": False, "error": "recursive must be a boolean", "code": ToolErrorCode.VALIDATION_ERROR}

    try:
        resolved, host_path = _resolve_read(paths, path_arg)
    except (ValueError, HostPathDeniedError, HostRootNotFoundError, HostScopePermissionError, PathOutOfBoundsError) as exc:
        return _error_dict(exc, path=path_arg)

    target = resolved.absolute
    if not target.exists():
        return {"ok": False, "error": f"path does not exist: {path_arg}", "code": ToolErrorCode.NOT_FOUND, "path": path_arg}
    if not target.is_dir():
        return {"ok": False, "error": f"not a directory: {path_arg}", "code": ToolErrorCode.NOT_FOUND, "path": path_arg}

    try:
        entries = _collect_entries(target, recursive=recursive)
    except OSError as exc:
        return {"ok": False, "error": str(exc), "code": ToolErrorCode.PERMISSION_DENIED, "path": host_path}

    return {"ok": True, "path": host_path, "absolute": target.as_posix(), "entries": entries}


def run_host_read(payload: dict[str, Any], *, paths: AgentPaths | None = None) -> dict[str, Any]:
    paths = paths or AgentPaths.discover()
    path_arg = payload.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return {"ok": False, "error": "path is required", "code": ToolErrorCode.VALIDATION_ERROR}

    try:
        resolved, host_path = _resolve_read(paths, path_arg)
    except (ValueError, HostPathDeniedError, HostRootNotFoundError, HostScopePermissionError, PathOutOfBoundsError) as exc:
        return _error_dict(exc, path=path_arg)

    target = resolved.absolute
    if not target.is_file():
        return {"ok": False, "error": f"not a file: {path_arg}", "code": ToolErrorCode.NOT_FOUND, "path": path_arg}

    try:
        size = target.stat().st_size
    except OSError as exc:
        return {"ok": False, "error": str(exc), "code": ToolErrorCode.PERMISSION_DENIED, "path": host_path}

    if size > MAX_BYTES:
        return {
            "ok": False,
            "error": f"file exceeds limit of {MAX_BYTES} bytes",
            "code": ToolErrorCode.FILE_TOO_LARGE,
            "path": host_path,
            "size": size,
        }

    try:
        raw = target.read_bytes()
    except OSError as exc:
        return {"ok": False, "error": str(exc), "code": ToolErrorCode.PERMISSION_DENIED, "path": host_path}

    if b"\0" in raw:
        return {"ok": False, "error": "binary file rejected", "code": ToolErrorCode.BINARY_FILE, "path": host_path}

    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "error": "file is not valid UTF-8 text", "code": ToolErrorCode.BINARY_FILE, "path": host_path}

    return {
        "ok": True,
        "path": host_path,
        "absolute": target.as_posix(),
        "content": content,
        "size": size,
    }


def run_host_grep(payload: dict[str, Any], *, paths: AgentPaths | None = None) -> dict[str, Any]:
    paths = paths or AgentPaths.discover()
    pattern = payload.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        return {"ok": False, "error": "pattern is required", "code": ToolErrorCode.VALIDATION_ERROR}

    path_arg = payload.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return {"ok": False, "error": "path is required", "code": ToolErrorCode.VALIDATION_ERROR}

    glob_pat = payload.get("glob")
    if glob_pat is not None and (not isinstance(glob_pat, str) or not glob_pat.strip()):
        return {"ok": False, "error": "glob must be a non-empty string when provided", "code": ToolErrorCode.VALIDATION_ERROR}

    ignore_case = payload.get("ignore_case", False)
    if not isinstance(ignore_case, bool):
        return {"ok": False, "error": "ignore_case must be a boolean", "code": ToolErrorCode.VALIDATION_ERROR}

    max_results = payload.get("max_results", DEFAULT_MAX_RESULTS)
    if not isinstance(max_results, int) or max_results < 1:
        return {"ok": False, "error": "max_results must be a positive integer", "code": ToolErrorCode.VALIDATION_ERROR}

    try:
        resolved, host_path = _resolve_read(paths, path_arg)
    except (ValueError, HostPathDeniedError, HostRootNotFoundError, HostScopePermissionError, PathOutOfBoundsError) as exc:
        return _error_dict(exc, path=path_arg)

    target = resolved.absolute
    if not target.exists():
        return {"ok": False, "error": f"path does not exist: {path_arg}", "code": ToolErrorCode.NOT_FOUND, "path": path_arg}

    try:
        flags = re.IGNORECASE if ignore_case else 0
        regex = re.compile(pattern, flags)
    except re.error as exc:
        return {"ok": False, "error": f"invalid regex: {exc}", "code": ToolErrorCode.VALIDATION_ERROR}

    host_root = resolved.host_root.resolve()
    host_id = resolved.host_id
    matches: list[dict[str, Any]] = []
    truncated = False
    for file_path in _iter_files(target):
        if glob_pat and not fnmatch.fnmatch(file_path.name, glob_pat):
            continue
        if file_path.stat().st_size > MAX_BYTES:
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel_file = file_path.resolve().relative_to(host_root).as_posix()
        rel_uri = f"host:{host_id}/{rel_file}"
        for line_no, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                matches.append({"path": rel_uri, "line": line_no, "text": line[:500]})
                if len(matches) >= max_results:
                    truncated = True
                    break
        if truncated:
            break

    return {
        "ok": True,
        "path": host_path,
        "pattern": pattern,
        "matches": matches,
        "truncated": truncated,
        "count": len(matches),
    }


def _iter_files(path: Path):
    if path.is_file():
        yield path
        return
    for child in sorted(path.rglob("*")):
        if child.is_file():
            yield child


def _collect_entries(directory: Path, *, recursive: bool) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for child in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
        entries.append(_entry_dict(child))
        if recursive and child.is_dir():
            for grandchild in sorted(child.iterdir(), key=lambda item: item.name.lower()):
                entries.append(_entry_dict(grandchild, name_prefix=f"{child.name}/"))
    return entries


def _entry_dict(path: Path, *, name_prefix: str = "") -> dict[str, Any]:
    name = f"{name_prefix}{path.name}"
    entry_type: Literal["file", "directory"] = "directory" if path.is_dir() else "file"
    item: dict[str, Any] = {"name": name, "type": entry_type}
    if entry_type == "file":
        item["size"] = path.stat().st_size
    return item


def _demo_t1006(paths: AgentPaths, registry) -> None:
    from tools.executor import ExecutorSession, ToolExecutor, build_confirm_preview
    from tools.logging import EvolveLog, read_events

    assert registry.get_evolved("host_copy_move") is not None
    print("[PASS] T-1006: registry loads host_copy_move")

    scope_path = host_scope_file(paths)
    scope_backup = scope_path.read_text(encoding="utf-8") if scope_path.is_file() else None
    log_path = paths.data / "evolve_log.jsonl"
    log_backup = log_path.read_text(encoding="utf-8") if log_path.is_file() else None

    with tempfile.TemporaryDirectory(prefix="host-t1006-") as tmp:
        downloads_dir = Path(tmp) / "downloads"
        documents_dir = Path(tmp) / "documents"
        downloads_dir.mkdir()
        documents_dir.mkdir()
        (downloads_dir / "ship.txt").write_text("cargo", encoding="utf-8")
        (documents_dir / "keep.txt").write_text("stay", encoding="utf-8")

        config = empty_host_scope()
        add_host_root(
            paths, config, host_id="downloads", directory=downloads_dir,
            label="Downloads", read=True, write=False,
        )
        add_host_root(
            paths, config, host_id="documents", directory=documents_dir,
            label="Documents", read=True, write=True,
        )
        save_host_scope(paths, config)

        try:
            same = run_host_copy_move(
                {
                    "operation": "move",
                    "source": "host:documents/keep.txt",
                    "dest": "host:documents/archived/keep.txt",
                    "on_conflict": "skip",
                },
                paths=paths,
            )
            assert same["ok"] and same.get("host_root_id") == "documents"
            assert (documents_dir / "archived" / "keep.txt").is_file()
            print("[PASS] T-1006: same-root move")

            (downloads_dir / "ship.txt").write_text("cargo", encoding="utf-8")
            cross = run_host_copy_move(
                {
                    "operation": "copy",
                    "source": "host:downloads/ship.txt",
                    "dest": "host:documents/inbox/ship.txt",
                    "on_conflict": "skip",
                },
                paths=paths,
            )
            assert cross["ok"]
            assert cross["host_src_id"] == "downloads" and cross["host_dst_id"] == "documents"
            assert (documents_dir / "inbox" / "ship.txt").read_text(encoding="utf-8") == "cargo"
            print("[PASS] T-1006: cross-root copy")

            readonly_dest = run_host_copy_move(
                {
                    "operation": "copy",
                    "source": "host:documents/inbox/ship.txt",
                    "dest": "host:downloads/from-docs.txt",
                },
                paths=paths,
            )
            assert not readonly_dest["ok"]
            assert readonly_dest.get("code") == ToolErrorCode.PERMISSION_DENIED
            print("[PASS] T-1006: reject write to read-only root")

            dry = run_host_copy_move(
                {
                    "operation": "copy",
                    "source": "host:downloads/ship.txt",
                    "dest": "host:documents/dry.txt",
                    "dry_run": True,
                },
                paths=paths,
            )
            assert dry["ok"] and dry.get("dry_run") is True
            assert not (documents_dir / "dry.txt").exists()
            print("[PASS] T-1006: dry_run does not write")

            evolved = registry.get_evolved("host_copy_move")
            preview = build_confirm_preview(
                "run_evolved",
                {
                    "tool_name": "host_copy_move",
                    "arguments": {
                        "operation": "copy",
                        "source": "host:downloads/ship.txt",
                        "dest": "host:documents/inbox/ship.txt",
                    },
                },
                evolved=evolved,
            )
            assert "Source:" in preview and "Dest:" in preview
            print("[PASS] T-1006: confirm preview shows absolute host paths")

            evolve_log = EvolveLog.for_agent(paths)
            session_dir = paths.data / "sessions" / "_t1006"
            session_dir.mkdir(parents=True, exist_ok=True)

            def confirm_y(_preview: str, allow_all: bool) -> str:
                assert not allow_all
                return "y"

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(session_dir=session_dir),
                evolve_log=evolve_log,
                confirm_fn=confirm_y,
            )
            before = len(read_events(log_path))
            ok_run = executor.run(
                "run_evolved",
                {
                    "tool_name": "host_copy_move",
                    "arguments": {
                        "operation": "copy",
                        "source": "host:downloads/ship.txt",
                        "dest": "host:documents/exec-copy.txt",
                        "on_conflict": "overwrite",
                    },
                },
            )
            assert ok_run.ok
            events = read_events(log_path)[before:]
            success_logs = [
                e for e in events
                if e.get("event") == "tool_call"
                and e.get("evolved_tool") == "host_copy_move"
                and e.get("ok") is True
            ]
            assert success_logs and success_logs[-1].get("host_src_id") == "downloads"
            assert success_logs[-1].get("host_dst_id") == "documents"
            print("[PASS] T-1006: evolve_log host_src_id / host_dst_id after confirm y")

            (downloads_dir / "reject-me.txt").write_text("x", encoding="utf-8")
            executor_n = ToolExecutor(
                registry=registry,
                session=ExecutorSession(session_dir=session_dir),
                evolve_log=evolve_log,
                confirm_fn=lambda _p, _a: "n",
            )
            before_n = len(read_events(log_path))
            rejected = executor_n.run(
                "run_evolved",
                {
                    "tool_name": "host_copy_move",
                    "arguments": {
                        "operation": "move",
                        "source": "host:downloads/reject-me.txt",
                        "dest": "host:documents/reject-me.txt",
                    },
                },
            )
            assert not rejected.ok
            assert (downloads_dir / "reject-me.txt").is_file()
            events_n = read_events(log_path)[before_n:]
            assert not any(
                e.get("event") == "tool_call"
                and e.get("evolved_tool") == "host_copy_move"
                and e.get("ok") is True
                for e in events_n
            )
            print("[PASS] T-1006: confirm n leaves files unchanged")
        finally:
            if scope_backup is None:
                scope_path.unlink(missing_ok=True)
            else:
                scope_path.write_text(scope_backup, encoding="utf-8")
            if log_backup is None:
                log_path.unlink(missing_ok=True)
            else:
                log_path.write_text(log_backup, encoding="utf-8")


def _demo_t1007(paths: AgentPaths, registry) -> None:
    from tools.builtin.run_evolved import run as run_evolved_tool
    from tools.executor import ExecutorSession, ToolExecutor

    scope_path = host_scope_file(paths)
    scope_backup = scope_path.read_text(encoding="utf-8") if scope_path.is_file() else None

    with tempfile.TemporaryDirectory(prefix="host-t1007-") as tmp:
        host_dir = Path(tmp)
        (host_dir / "report.pdf").write_text("pdf", encoding="utf-8")
        (host_dir / "notes.txt").write_text("txt", encoding="utf-8")

        config = empty_host_scope()
        add_host_root(
            paths, config, host_id="downloads", directory=host_dir,
            label="Downloads", read=True, write=True,
        )
        save_host_scope(paths, config)

        try:
            dry = run_evolved_tool(
                {
                    "tool_name": "sort_by_extension",
                    "arguments": {"path": "host:downloads"},
                    "dry_run": True,
                },
                registry=registry,
            )
            assert dry.ok and dry.data.get("dry_run") is True and dry.data.get("count") == 2
            assert (host_dir / "report.pdf").is_file()
            print("[PASS] T-1007: host sort_by_extension dry_run")

            confirms = iter(["y"])

            def _confirm(_p: str, allow: bool) -> str:
                assert not allow
                return next(confirms)

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(),
                confirm_fn=_confirm,
            )
            live = executor.run(
                "run_evolved",
                {
                    "tool_name": "sort_by_extension",
                    "arguments": {"path": "host:downloads"},
                    "dry_run": False,
                },
            )
            assert live.ok and (host_dir / "pdf" / "report.pdf").is_file()
            assert (host_dir / "txt" / "notes.txt").is_file()
            assert live.data.get("host_root_id") == "downloads"
            print("[PASS] T-1007: host sort_by_extension live sort")

            (host_dir / "txt" / "notes.txt").rename(host_dir / "notes.txt")
            confirms = iter(["y"])
            renamed = executor.run(
                "run_evolved",
                {
                    "tool_name": "rename_batch",
                    "arguments": {
                        "path": "host:downloads",
                        "mode": "prefix",
                        "prefix": "vacation_",
                    },
                    "dry_run": False,
                },
            )
            assert renamed.ok and (host_dir / "vacation_notes.txt").is_file()
            print("[PASS] T-1007: host rename_batch prefix")

            bad = run_evolved_tool(
                {
                    "tool_name": "sort_by_extension",
                    "arguments": {"path": "host:unknown"},
                    "dry_run": True,
                },
                registry=registry,
            )
            assert not bad.ok
            print("[PASS] T-1007: host:unknown rejected")
        finally:
            if scope_backup is None:
                scope_path.unlink(missing_ok=True)
            else:
                scope_path.write_text(scope_backup, encoding="utf-8")


def _demo() -> None:
    paths = AgentPaths.discover()
    scope_path = host_scope_file(paths)
    backup = scope_path.read_text(encoding="utf-8") if scope_path.is_file() else None

    from tools.builtin.run_evolved import run as run_evolved
    from tools.executor import ToolExecutor, ExecutorSession
    from tools.registry import ToolRegistry

    registry = ToolRegistry.load(paths)
    for name in ("host_list", "host_read", "host_grep"):
        assert registry.get_evolved(name) is not None, name
    print("[PASS] T-1005: registry loads host_list, host_read, host_grep")

    confirm_calls = 0

    def _no_confirm(_preview: str, _allow: bool) -> str:
        nonlocal confirm_calls
        confirm_calls += 1
        return "y"

    with tempfile.TemporaryDirectory(prefix="host-t1005-") as tmp:
        host_dir = Path(tmp)
        (host_dir / "notes.txt").write_text("hello host read", encoding="utf-8")
        (host_dir / "other.log").write_text("nope", encoding="utf-8")

        config = empty_host_scope()
        add_host_root(
            paths,
            config,
            host_id="downloads",
            directory=host_dir,
            label="Test",
            read=True,
            write=False,
        )
        save_host_scope(paths, config)

        try:
            listed = run_host_list({"path": "host:downloads"}, paths=paths)
            assert listed["ok"] and any(e["name"] == "notes.txt" for e in listed["entries"])
            print("[PASS] T-1005: host_list host:downloads")

            read_back = run_host_read({"path": "host:downloads/notes.txt"}, paths=paths)
            assert read_back["ok"] and "hello host" in read_back["content"]
            print("[PASS] T-1005: host_read host:downloads/notes.txt")

            grep_hit = run_host_grep(
                {"path": "host:downloads", "pattern": "hello", "max_results": 5},
                paths=paths,
            )
            assert grep_hit["ok"] and grep_hit["count"] >= 1
            print("[PASS] T-1005: host_grep finds pattern")

            unknown = run_host_read({"path": "host:unknown/x.txt"}, paths=paths)
            assert not unknown["ok"] and unknown.get("code") == ToolErrorCode.NOT_FOUND
            print("[PASS] T-1005: host:unknown rejected")

            denied = run_host_read({"path": "host:downloads/.env"}, paths=paths)
            (host_dir / ".env").write_text("KEY=x", encoding="utf-8")
            denied = run_host_read({"path": "host:downloads/.env"}, paths=paths)
            assert not denied["ok"] and denied.get("code") == ToolErrorCode.PATH_DENIED
            print("[PASS] T-1005: host:downloads/.env -> path_denied")

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(),
                confirm_fn=_no_confirm,
            )
            confirm_calls = 0
            evolved_list = executor.run(
                "run_evolved",
                {"tool_name": "host_list", "arguments": {"path": "host:downloads"}},
            )
            assert evolved_list.ok and confirm_calls == 0
            print("[PASS] T-1005: run_evolved host_list skips confirm")

            evolved_grep = executor.run(
                "run_evolved",
                {
                    "tool_name": "host_grep",
                    "arguments": {"path": "host:downloads", "pattern": "hello"},
                },
            )
            assert evolved_grep.ok and confirm_calls == 0
            print("[PASS] T-1005: run_evolved host_grep skips confirm")
        finally:
            if backup is None:
                scope_path.unlink(missing_ok=True)
            else:
                scope_path.write_text(backup, encoding="utf-8")

    _demo_t1006(paths, registry)
    _demo_t1007(paths, registry)


if __name__ == "__main__":
    _demo()
