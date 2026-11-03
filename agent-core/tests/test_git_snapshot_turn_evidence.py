from __future__ import annotations

import sys
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from tools.schema import tool_ok


def _executor():
    paths_context = temporary_agent_paths()
    paths = paths_context.__enter__()
    executor = ToolExecutor(registry=ToolRegistry.load(paths), session=ExecutorSession())
    return paths_context, executor


def test_git_snapshot_does_not_attribute_existing_dirty_paths_to_current_turn():
    paths_context, executor = _executor()
    try:
        executor._record_turn_evidence(
            "run_evolved",
            {"tool_name": "git_snapshot", "arguments": {"working_dir": "workspace/demo"}},
            tool_ok(
                "run_evolved",
                {
                    "diff_stat": "2 files changed, 4 insertions(+), 1 deletion(-)",
                    "paths": ["src/existing.ts"],
                    "untracked_paths": ["src/untracked.ts"],
                    "status_lines": [" M src/dirty.ts", "?? src/new.ts"],
                },
            ),
        )
        evidence = executor.session.turn_evidence[-1]
        assert evidence["paths"] == []
        assert evidence["diff_summary"] == "工作树快照：2 files changed, 4 insertions(+), 1 deletion(-)"
    finally:
        paths_context.__exit__(None, None, None)


def test_git_diff_path_evidence_remains_unchanged():
    paths_context, executor = _executor()
    try:
        executor._record_turn_evidence(
            "run_evolved",
            {"tool_name": "git_diff", "arguments": {}},
            tool_ok(
                "run_evolved",
                {"diff_stat": "1 file changed", "paths": ["src/diffed.ts"], "status_lines": [" M src/worktree.ts"]},
            ),
        )
        evidence = executor.session.turn_evidence[-1]
        assert evidence["paths"] == ["src/diffed.ts", "src/worktree.ts"]
        assert evidence["diff_summary"] == "1 file changed"
    finally:
        paths_context.__exit__(None, None, None)
