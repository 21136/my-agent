"""run_project_tests — structured pytest/jest/mvn output for bound projects (Phase 44)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[4] / "agent-core"
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from project_verify import run_project_tests


def run(args: dict[str, Any]) -> dict[str, Any]:
    paths = AgentPaths.discover()
    working_dir = str(args.get("working_dir") or "").strip()
    if not working_dir:
        return {"ok": False, "error": "working_dir is required"}

    suite = str(args.get("suite") or "auto")
    extra = args.get("extra_args")
    if extra is not None and not isinstance(extra, list):
        return {"ok": False, "error": "extra_args must be an array of strings"}

    timeout_sec = int(args.get("timeout_sec", 600))
    max_failures = int(args.get("max_failures", 20))
    dry_run = bool(args.get("dry_run", False))

    return run_project_tests(
        paths,
        working_dir=working_dir,
        suite=suite,
        extra_args=[str(x) for x in extra] if isinstance(extra, list) else None,
        timeout_sec=timeout_sec,
        max_failures=max_failures,
        dry_run=dry_run,
    )


def run_tool_main(fn):
    import json

    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    result = fn(payload)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    run_tool_main(run)
