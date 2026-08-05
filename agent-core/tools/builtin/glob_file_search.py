"""Builtin glob_file_search — find files by glob pattern (Phase 42 Track I · TOOLS §7.3.1)."""

from __future__ import annotations

import fnmatch
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[2]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths, PathOutOfBoundsError
from tools.builtin.read_file import resolve_read_path
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok, to_json

TOOL_NAME = "glob_file_search"
DEFAULT_MAX_RESULTS = 200
HARD_MAX_RESULTS = 1000

_DEFAULT_IGNORE_DIR_NAMES = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        ".pytest_cache",
        ".mypy_cache",
        ".tox",
    }
)


def _path_has_ignored_segment(rel_posix: str) -> bool:
    parts = rel_posix.replace("\\", "/").split("/")
    return any(part in _DEFAULT_IGNORE_DIR_NAMES for part in parts)


def _load_gitignore_patterns(search_root: Path) -> list[str]:
    patterns: list[str] = []
    cur = search_root.resolve()
    while True:
        gi = cur / ".gitignore"
        if gi.is_file():
            try:
                for line in gi.read_text(encoding="utf-8", errors="replace").splitlines():
                    raw = line.strip()
                    if not raw or raw.startswith("#"):
                        continue
                    if raw.startswith("!"):
                        continue
                    patterns.append(raw.rstrip("/"))
            except OSError:
                pass
        if cur.parent == cur:
            break
        cur = cur.parent
    return patterns


def _matches_gitignore_pattern(rel_posix: str, pattern: str) -> bool:
    norm = rel_posix.replace("\\", "/")
    pat = pattern.strip().rstrip("/")
    if not pat:
        return False
    anchored = pat.startswith("/")
    if anchored:
        pat = pat[1:]
    candidates = [pat]
    if "/" not in pat:
        candidates.extend((f"**/{pat}", pat))
    for candidate in candidates:
        if fnmatch.fnmatch(norm, candidate):
            return True
        if fnmatch.fnmatch(Path(norm).name, candidate.split("/")[-1]):
            return True
    return False


def _is_ignored_path(rel_posix: str, *, search_root: Path) -> bool:
    if _path_has_ignored_segment(rel_posix):
        return True
    for pattern in _load_gitignore_patterns(search_root):
        if _matches_gitignore_pattern(rel_posix, pattern):
            return True
    return False


def run(arguments: dict[str, Any], *, paths: AgentPaths | None = None) -> ToolResult:
    """List files under *path* matching glob *pattern*."""
    started = time.perf_counter()
    paths = paths or AgentPaths.discover()

    pattern = arguments.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        return _fail("pattern is required", ToolErrorCode.VALIDATION_ERROR, started)

    path_arg = arguments.get("path", ".")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return _fail("path must be a non-empty string", ToolErrorCode.VALIDATION_ERROR, started)

    ignore_case = arguments.get("ignore_case", False)
    if not isinstance(ignore_case, bool):
        return _fail("ignore_case must be a boolean", ToolErrorCode.VALIDATION_ERROR, started, path=path_arg)

    max_results = arguments.get("max_results", DEFAULT_MAX_RESULTS)
    if not isinstance(max_results, int) or max_results < 1:
        return _fail("max_results must be a positive integer", ToolErrorCode.VALIDATION_ERROR, started, path=path_arg)
    max_results = min(max_results, HARD_MAX_RESULTS)

    glob_pattern = pattern.strip()
    try:
        resolved = resolve_read_path(paths, path_arg)
    except PathOutOfBoundsError as exc:
        return _fail(str(exc), exc.code, started, path=path_arg)
    except (TypeError, ValueError) as exc:
        return _fail(str(exc), ToolErrorCode.VALIDATION_ERROR, started, path=path_arg)
    except FileNotFoundError:
        return _fail(f"path does not exist: {path_arg}", ToolErrorCode.NOT_FOUND, started, path=path_arg)

    search_root = resolved if resolved.is_dir() else resolved.parent
    try:
        if shutil.which("rg"):
            rel_paths, truncated = _glob_rg(
                search_root,
                glob_pattern,
                single_file=resolved if resolved.is_file() else None,
                ignore_case=ignore_case,
                max_results=max_results,
            )
        else:
            rel_paths, truncated = _glob_python(
                search_root,
                glob_pattern,
                single_file=resolved if resolved.is_file() else None,
                ignore_case=ignore_case,
                max_results=max_results,
            )
        rel_paths = [p for p in rel_paths if not _is_ignored_path(p, search_root=search_root)]
    except OSError as exc:
        return _fail(str(exc), ToolErrorCode.PERMISSION_DENIED, started, path=path_arg)
    except subprocess.SubprocessError as exc:
        return _fail(str(exc), ToolErrorCode.VALIDATION_ERROR, started, path=path_arg)

    return tool_ok(
        TOOL_NAME,
        {
            "path": paths.to_agent_relative(search_root),
            "paths": rel_paths,
            "truncated": truncated,
        },
        truncated=truncated,
        duration_ms=_elapsed_ms(started),
    )


def _glob_rg(
    root: Path,
    pattern: str,
    *,
    single_file: Path | None,
    ignore_case: bool,
    max_results: int,
) -> tuple[list[str], bool]:
    if single_file is not None:
        rel = single_file.relative_to(root).as_posix()
        if _path_matches_glob(rel, pattern, ignore_case=ignore_case):
            return [rel], False
        return [], False

    cmd = ["rg", "--files", "-g", pattern, str(root)]
    if ignore_case:
        cmd.insert(1, "--glob-case-insensitive")
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode not in {0, 1}:
        raise subprocess.SubprocessError(proc.stderr.strip() or f"rg exited {proc.returncode}")

    rel_paths: list[str] = []
    truncated = False
    prefix = root.resolve().as_posix().rstrip("/") + "/"
    for line in proc.stdout.splitlines():
        raw = line.strip()
        if not raw:
            continue
        normalized = Path(raw).resolve().as_posix()
        if normalized.startswith(prefix):
            rel = normalized[len(prefix) :]
        else:
            try:
                rel = Path(raw).resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
        if not rel or rel.endswith("/"):
            continue
        rel_paths.append(rel)
        if len(rel_paths) >= max_results:
            truncated = len(proc.stdout.splitlines()) > len(rel_paths)
            break
    rel_paths.sort(key=str.lower)
    return rel_paths, truncated


def _glob_python(
    root: Path,
    pattern: str,
    *,
    single_file: Path | None,
    ignore_case: bool,
    max_results: int,
) -> tuple[list[str], bool]:
    if single_file is not None:
        rel = single_file.relative_to(root).as_posix()
        if _path_matches_glob(rel, pattern, ignore_case=ignore_case):
            return [rel], False
        return [], False

    rel_paths: list[str] = []
    truncated = False
    for path in sorted(root.rglob("*"), key=lambda p: p.as_posix().lower()):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if _path_has_ignored_segment(rel):
            continue
        if not _path_matches_glob(rel, pattern, ignore_case=ignore_case):
            continue
        rel_paths.append(rel)
        if len(rel_paths) >= max_results:
            truncated = True
            break
    return rel_paths, truncated


def _path_matches_glob(rel_posix: str, pattern: str, *, ignore_case: bool) -> bool:
    from pathlib import PurePosixPath

    candidate = PurePosixPath(rel_posix)
    pat = pattern
    if ignore_case:
        return candidate.match(pat) or candidate.match(pat.lower()) or PurePosixPath(rel_posix.lower()).match(pat.lower())
    return candidate.match(pat)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _fail(
    message: str,
    code: str,
    started: float,
    *,
    path: str | None = None,
) -> ToolResult:
    details: dict[str, Any] = {}
    if path is not None:
        details["path"] = path
    return tool_fail(TOOL_NAME, code, message, details=details or None, duration_ms=_elapsed_ms(started))


def _demo() -> None:
    paths = AgentPaths.discover()
    demo_dir = paths.workspace / "_glob_demo"
    demo_dir.mkdir(parents=True, exist_ok=True)
    (demo_dir / "a.py").write_text("print('a')\n", encoding="utf-8")
    (demo_dir / "b.txt").write_text("b\n", encoding="utf-8")
    nested = demo_dir / "sub"
    nested.mkdir(exist_ok=True)
    (nested / "test_x.py").write_text("x\n", encoding="utf-8")

    cases: list[tuple[dict[str, Any], bool, str]] = [
        ({"pattern": "**/*.py", "path": "workspace/_glob_demo"}, True, "recursive py"),
        ({"pattern": "*.py", "path": "workspace/_glob_demo"}, True, "shallow py"),
        ({"pattern": "**/*.py", "path": "workspace/_glob_demo", "max_results": 1}, True, "truncate"),
        ({"pattern": "", "path": "workspace/_glob_demo"}, False, "missing pattern"),
        ({"pattern": "*.py", "path": "missing-glob-dir"}, False, "not found"),
    ]
    for args, expect_ok, label in cases:
        result = run(args, paths=paths)
        ok = result.ok == expect_ok
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: {to_json(result)[:200]}")

    hits = run({"pattern": "**/*.py", "path": "workspace/_glob_demo"}, paths=paths)
    assert hits.ok and len(hits.data.get("paths", [])) >= 2
    print("[PASS] glob_file_search demo")


if __name__ == "__main__":
    _demo()
