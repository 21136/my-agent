from __future__ import annotations

import sys
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import ToolLoopSegmentResult
from goal_runtime import GoalRuntime
from server import WsBridge, _build_repl
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


def test_goal_cumulative_usage_includes_post_loop_ci_repair_and_checker_rounds(monkeypatch):
    monkeypatch.delenv("MY_AGENT_AUTO_CONTINUE", raising=False)
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        session = create_new(paths, conversation_id="20261107-goal-post-loop-accounting")
        session.meta.turn_mode = "agent"
        session.meta.harness = "desktop"
        session.next_work_intent = "execute"
        session.goal = "account all execution rounds"
        session.save()

        bridge = WsBridge(emit=lambda _event: None, paths=paths)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
        loop_results = iter((
            ToolLoopSegmentResult(final_text="Initial result", tool_rounds=1, finish_reason="stop"),
            ToolLoopSegmentResult(final_text="CI repair result", tool_rounds=2, finish_reason="stop"),
        ))
        repl.agent._run_parent_tool_loop = lambda **_kwargs: next(loop_results)
        repl.agent._ci_repair_continuation_text = lambda: "[CI repair continuation]"
        repl.agent._mark_ci_repair_continuation = lambda: None
        repl.agent._finalize_scaffold_checker = lambda *, final_text, finish_reason: (
            final_text, finish_reason, True, "passed", 3,
        )

        result = repl.agent.run_turn("Continue implementing", spawn_explore=False)

        loaded = GoalRuntime(Path(session.session_dir)).load()
        assert loaded is not None
        assert result.total_tool_rounds == 3  # checker remains separately surfaced
        assert result.checker_tool_rounds == 3
        assert loaded.budgets.tool_turns_used == 6
