"""Builtin grep — local content search (TOOLS.md §7.3, TASKS T-104a)."""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator

_AGENT_CORE = Path(__file__).resolve().parents[2]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths, PathOutOfBoundsError
from tools.builtin.read_file import MAX_BYTES, resolve_read_path
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok, to_json

TOOL_NAME = "grep"
DEFAULT_MAX_RESULTS = 50


def run(arguments: dict[str, Any], *, paths: AgentPaths | None = None) -> ToolResult:
    """Search local files under agent root for *pattern*."""
    started = time.perf_counter()
    paths = paths or AgentPaths.discover()

    pattern = arguments.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        return _fail("pattern is required", ToolErrorCode.VALIDATION_ERROR, started)

    path_arg = arguments.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return _fail("path is required", ToolErrorCode.VALIDATION_ERROR, started)

    glob_pat = arguments.get("glob")
    if glob_pat is not None and (not isinstance(glob_pat, str) or not glob_pat.strip()):
        return _fail("glob must be a non-empty string when provided", ToolErrorCode.VALIDATION_ERROR, started)

    ignore_case = arguments.get("ignore_case", False)
    if not isinstance(ignore_case, bool):
        return _fail("ignore_case must be a boolean", ToolErrorCode.VALIDATION_ERROR, started, path=path_arg)

    max_results = arguments.get("max_results", DEFAULT_MAX_RESULTS)
    if not isinstance(max_results, int) or max_results < 1:
        return _fail("max_results must be a positive integer", ToolErrorCode.VALIDATION_ERROR, started, path=path_arg)

    try:
        resolved = resolve_read_path(paths, path_arg)
    except PathOutOfBoundsError as exc:
        return _fail(str(exc), exc.code, started, path=path_arg)
    except (TypeError, ValueError) as exc:
        return _fail(str(exc), ToolErrorCode.VALIDATION_ERROR, started, path=path_arg)
    except FileNotFoundError:
        return _fail(f"path does not exist: {path_arg}", ToolErrorCode.NOT_FOUND, started, path=path_arg)

    try:
        if shutil.which("rg"):
            matches, truncated = _grep_rg(
                resolved,
                pattern,
                glob_pat=glob_pat,
                ignore_case=ignore_case,
                max_results=max_results,
                paths=paths,
            )
        else:
            matches, truncated = _grep_python(
                resolved,
                pattern,
                glob_pat=glob_pat,
                ignore_case=ignore_case,
                max_results=max_results,
                paths=paths,
            )
    except _RegexError as exc:
        return _fail(str(exc), ToolErrorCode.VALIDATION_ERROR, started, path=path_arg)
    except OSError as exc:
        return _fail(str(exc), ToolErrorCode.PERMISSION_DENIED, started, path=path_arg)
    except subprocess.SubprocessError as exc:
        return _fail(str(exc), ToolErrorCode.VALIDATION_ERROR, started, path=path_arg)

    return tool_ok(
        TOOL_NAME,
        {"matches": matches, "truncated": truncated},
        truncated=truncated,
        duration_ms=_elapsed_ms(started),
    )


class _RegexError(ValueError):
    pass


def _grep_rg(
    target: Path,
    pattern: str,
    *,
    glob_pat: str | None,
    ignore_case: bool,
    max_results: int,
    paths: AgentPaths,
) -> tuple[list[dict[str, Any]], bool]:
    cmd = ["rg", "--json", "--line-number", "-e", pattern, str(target)]
    if ignore_case:
        cmd.insert(1, "-i")
    if glob_pat:
        cmd.extend(["--glob", glob_pat])

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(paths.agent_root),
    )

    if proc.returncode == 2:
        message = proc.stderr.strip() or "invalid search pattern"
        raise _RegexError(message)

    matches: list[dict[str, Any]] = []
    truncated = False

    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue

        if len(matches) >= max_results:
            truncated = True
            break

        data = event.get("data", {})
        file_path = Path(data.get("path", {}).get("text", ""))
        line_number = data.get("line_number")
        line_text = data.get("lines", {}).get("text", "")
        if not file_path or line_number is None:
            continue

        rel = paths.to_agent_relative(file_path.resolve())
        matches.append(
            {
                "path": rel,
                "line": int(line_number),
                "text": line_text.rstrip("\r\n"),
            }
        )

    return matches, truncated


def _grep_python(
    target: Path,
    pattern: str,
    *,
    glob_pat: str | None,
    ignore_case: bool,
    max_results: int,
    paths: AgentPaths,
) -> tuple[list[dict[str, Any]], bool]:
    flags = re.IGNORECASE if ignore_case else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as exc:
        raise _RegexError(f"invalid search pattern: {exc}") from exc

    matches: list[dict[str, Any]] = []
    truncated = False

    for file_path in _iter_search_files(target, glob_pat):
        if truncated:
            break
        try:
            if file_path.stat().st_size > MAX_BYTES:
                continue
        except OSError:
            continue

        rel = paths.to_agent_relative(file_path)
        for line_no, text in _iter_text_lines(file_path):
            if regex.search(text):
                matches.append({"path": rel, "line": line_no, "text": text.rstrip("\r\n")})
                if len(matches) >= max_results:
                    truncated = True
                    break

    return matches, truncated


def _iter_search_files(target: Path, glob_pat: str | None) -> Iterator[Path]:
    if target.is_file():
        if _glob_matches(target.name, glob_pat):
            yield target
        return

    for path in sorted(target.rglob("*")):
        if not path.is_file():
            continue
        if _glob_matches(path.name, glob_pat):
            yield path


def _glob_matches(name: str, glob_pat: str | None) -> bool:
    if not glob_pat:
        return True
    return fnmatch.fnmatch(name, glob_pat)


def _iter_text_lines(file_path: Path) -> Iterator[tuple[int, str]]:
    try:
        with file_path.open("rb") as handle:
            sample = handle.read(8192)
            if b"\0" in sample:
                return
            handle.seek(0)
            for line_no, raw in enumerate(handle, start=1):
                yield line_no, raw.decode("utf-8", errors="replace")
    except OSError:
        return


def _fail(
    message: str,
    code: str,
    started: float,
    *,
    path: str | None = None,
    details: dict[str, Any] | None = None,
) -> ToolResult:
    merged = dict(details or {})
    if path is not None:
        merged.setdefault("path", path)
    return tool_fail(
        TOOL_NAME,
        code,
        message,
        duration_ms=_elapsed_ms(started),
        details=merged or None,
    )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _demo() -> None:
    paths = AgentPaths.discover()
    demo_dir = paths.workspace / "_grep_demo"
    demo_dir.mkdir(parents=True, exist_ok=True)
    (demo_dir / "one.txt").write_text("alpha line\nbeta LINE\n", encoding="utf-8")
    (demo_dir / "two.py").write_text("print('alpha')\n", encoding="utf-8")
    (demo_dir / "skip.bin").write_bytes(b"\x00alpha\n")

    cases: list[tuple[dict[str, Any], bool, str]] = [
        ({"pattern": "alpha", "path": "workspace/_grep_demo"}, True, "basic search"),
        ({"pattern": "LINE", "path": "workspace/_grep_demo", "ignore_case": True}, True, "ignore_case"),
        ({"pattern": "alpha", "path": "workspace/_grep_demo", "glob": "*.py"}, True, "glob filter"),
        ({"pattern": "alpha", "path": "workspace/_grep_demo", "max_results": 1}, True, "max_results cap"),
        ({"pattern": "[", "path": "workspace/_grep_demo"}, False, "invalid regex"),
        ({"pattern": "x", "path": "../outside-agent"}, False, "out of bounds"),
        ({"pattern": "x", "path": "missing-grep-dir"}, False, "not found"),
    ]

    for arguments, should_ok, label in cases:
        result = run(arguments, paths=paths)
        if result.ok != should_ok:
            print(f"[FAIL] {label}: expected ok={should_ok}, got {result.to_dict()}")
            raise SystemExit(1)
        status = "ok" if result.ok else result.error.code if result.error else "?"
        print(f"[PASS] {label}: {status}")

    hits = run({"pattern": "alpha", "path": "workspace/_grep_demo"}, paths=paths)
    capped = run(
        {"pattern": "alpha", "path": "workspace/_grep_demo", "max_results": 1},
        paths=paths,
    )
    assert hits.ok and capped.ok
    assert len(hits.data["matches"]) >= 2
    assert len(capped.data["matches"]) == 1 and capped.data["truncated"] is True
    backend = "rg" if shutil.which("rg") else "python"
    print(f"[PASS] backend={backend} matches={len(hits.data['matches'])} truncated={capped.data['truncated']}")
    print(to_json(hits, indent=2))

    import shutil as shutil_mod

    shutil_mod.rmtree(demo_dir, ignore_errors=True)


if __name__ == "__main__":
    _demo()
