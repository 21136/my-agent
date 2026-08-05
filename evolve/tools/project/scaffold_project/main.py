"""scaffold_project — run evolve/scaffolds/<recipe> against a workspace directory (Phase 43)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[4] / "agent-core"
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from scaffold_recipes import list_recipes, run_scaffold_project


def run(args: dict[str, Any]) -> dict[str, Any]:
    paths = AgentPaths.discover()
    recipe = str(args.get("recipe") or "").strip()
    target_dir = str(args.get("target_dir") or "").strip()
    phase = str(args.get("phase") or "init").strip()
    variables = args.get("variables")
    if variables is not None and not isinstance(variables, dict):
        return {"ok": False, "error": "variables must be an object"}
    dry_run = bool(args.get("dry_run", False))
    stop_on_error = bool(args.get("stop_on_error", True))

    if not recipe:
        known = list_recipes(paths)
        hint = f"; known recipes: {', '.join(known)}" if known else ""
        return {"ok": False, "error": f"recipe is required{hint}"}
    if not target_dir:
        return {"ok": False, "error": "target_dir is required (e.g. workspace/my-app)"}

    return run_scaffold_project(
        paths,
        recipe=recipe,
        target_dir=target_dir,
        phase=phase,
        variables=variables if isinstance(variables, dict) else None,
        dry_run=dry_run,
        stop_on_error=stop_on_error,
    )


def run_tool_main(fn):
    import json

    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    result = fn(payload)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    run_tool_main(run)
