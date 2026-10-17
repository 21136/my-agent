"""Explicit main-project test runner; never recursively discovers workspace projects."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]


def build_command(scope: str, paths: Sequence[str]) -> list[str]:
    normalized = scope.strip().lower()
    base = [sys.executable, "-m", "pytest"]
    if normalized == "core":
        return [*base, "-q", "agent-core/tests"]
    if normalized == "collect":
        return [*base, "--collect-only", "-q", "agent-core/tests"]
    if normalized == "focused":
        selected = [str(item).strip() for item in paths if str(item).strip()]
        if not selected:
            raise ValueError("focused scope requires at least one explicit test path")
        if any(path.replace("\\", "/").startswith("workspace/") for path in selected):
            raise ValueError("focused scope refuses workspace paths")
        return [*base, "-q", *selected]
    raise ValueError(f"unknown scope: {scope!r}")


def _count(pattern: str, text: str) -> int:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else 0


def parse_pytest_summary(output: str, *, exit_code: int) -> dict[str, int | str]:
    text = output or ""
    collection_errors = _count(r"(?:^|\s)(\d+) errors? during collection", text)
    if not collection_errors:
        collection_errors = _count(r"(?:^|\s)(\d+) errors? in", text)
    failed = _count(r"(?:^|\s)(\d+) failed", text)
    passed = _count(r"(?:^|\s)(\d+) passed", text)
    skipped = _count(r"(?:^|\s)(\d+) skipped", text)
    if exit_code == 0 and collection_errors == 0 and failed == 0:
        status = "passed"
    elif collection_errors:
        status = "collection_error"
    elif exit_code != 0:
        status = "failed"
    else:
        status = "completed_unknown"
    return {
        "status": status,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "collection_errors": collection_errors,
    }


def run(scope: str, paths: Sequence[str] = ()) -> dict[str, object]:
    command = build_command(scope, paths)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    summary = parse_pytest_summary(output, exit_code=completed.returncode)
    return {
        "scope": scope,
        "cwd": str(ROOT),
        "python": sys.executable,
        "command": command,
        "exit_code": completed.returncode,
        "stdout_tail": (completed.stdout or "")[-4000:],
        "stderr_tail": (completed.stderr or "")[-4000:],
        **summary,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("core", "collect", "focused"), default="core")
    parser.add_argument("paths", nargs="*", help="explicit test paths for --scope focused")
    args = parser.parse_args(argv)
    try:
        result = run(args.scope, args.paths)
    except (OSError, ValueError) as exc:
        result = {
            "scope": args.scope,
            "cwd": str(ROOT),
            "python": sys.executable,
            "status": "blocked",
            "error": str(exc),
            "exit_code": 2,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
