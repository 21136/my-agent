"""structured_test — protocol adapter over the existing project test runner."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[4] / "agent-core"
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from project_verify import run_project_tests
from structured_test import normalize_test_result


def _positive_int(args: dict[str, Any], key: str, default: int) -> tuple[int | None, str | None]:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        return None, f"{key} must be an integer"
    if value < 1:
        return None, f"{key} must be at least 1"
    return value, None


def run(args: dict[str, Any]) -> dict[str, Any]:
    paths = AgentPaths.discover()
    raw_working_dir = args.get("working_dir")
    if not isinstance(raw_working_dir, str) or not raw_working_dir.strip():
        return normalize_test_result({"ok": False, "error": "working_dir is required"})
    working_dir = raw_working_dir.strip()
    raw_suite = args.get("suite", "auto")
    if not isinstance(raw_suite, str) or not raw_suite.strip():
        return normalize_test_result({"ok": False, "error": "suite must be a non-empty string"})
    extra = args.get("extra_args")
    if extra is not None and (not isinstance(extra, list) or not all(isinstance(item, str) for item in extra)):
        return normalize_test_result({"ok": False, "error": "extra_args must be an array of strings"})
    dry_run = args.get("dry_run", False)
    if not isinstance(dry_run, bool):
        return normalize_test_result({"ok": False, "error": "dry_run must be a boolean"})
    timeout_sec, timeout_error = _positive_int(args, "timeout_sec", 600)
    max_failures, failures_error = _positive_int(args, "max_failures", 20)
    if timeout_error or failures_error:
        return normalize_test_result({"ok": False, "error": timeout_error or failures_error})
    payload = run_project_tests(
        paths,
        working_dir=working_dir,
        suite=raw_suite,
        extra_args=extra,
        timeout_sec=timeout_sec or 600,
        max_failures=max_failures or 20,
        dry_run=dry_run,
    )
    return normalize_test_result(payload, dry_run=dry_run)


def main() -> None:
    import json

    raw = sys.stdin.read()
    print(json.dumps(run(json.loads(raw) if raw.strip() else {}), ensure_ascii=False))


if __name__ == "__main__":
    main()
