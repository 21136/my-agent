from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import ToolLoopSegmentResult
from goal_runtime import GoalRuntime
from server import WsBridge, _build_repl
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


def _agent_with_goal(paths, conversation_id: str, *, goal_text: str = "current request"):
    session = create_new(paths, conversation_id=conversation_id)
    session.meta.turn_mode = "agent"
    session.meta.harness = "desktop"
    session.meta.active_shell = "daily"
    session.next_work_intent = "execute"
    session.goal = goal_text
    session.save()
    repl = _build_repl(
        session, paths, WsBridge(emit=lambda _event: None, paths=paths),
        work_record_controller_factory=lambda *_args: None,
    )
    return session, repl.agent


def test_old_multi_project_gate_is_not_reannounced_after_an_unrelated_turn():
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        session, agent = _agent_with_goal(paths, "20261106-stale-multiproject")
        runtime = GoalRuntime(Path(session.session_dir))
        state = runtime.ensure_goal(goal_text="previous multi-project request")
        state.multi_project_unverified = True
        state.multi_project_ids = ["alpha", "beta"]
        runtime.save()

        agent._run_parent_tool_loop = lambda **_kwargs: ToolLoopSegmentResult(
            final_text="Handled the unrelated request.", tool_rounds=1, finish_reason="stop",
        )
        result = agent.run_turn("Tell me a short joke unrelated to those projects.", spawn_explore=False)

        repeated = "多项目 worker 批次未逐项验收"
        assert repeated not in result.assistant_text
        assert not any(repeated in notice for notice in result.notices)
        assert not any(
            repeated in (message.get("content") or "")
            for message in session.messages
            if message.get("role") == "assistant"
        )
        persisted = GoalRuntime(Path(session.session_dir)).load()
        assert persisted is not None
        assert persisted.multi_project_unverified is True
        assert persisted.multi_project_ids == ["alpha", "beta"]


def test_recovered_legacy_budget_pause_is_explicitly_marked_historical_in_turn_context():
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        session, agent = _agent_with_goal(paths, "20261106-stale-budget")
        session.append_message({
            "role": "assistant",
            "content": "[Goal] 本回合工具执行额度已用尽；自动续段已停止。",
        })
        session.save()
        runtime = GoalRuntime(Path(session.session_dir))
        state = runtime.ensure_goal(goal_text="previous task")
        state.status = "paused"
        state.pause_reason = "budget_exhausted"
        runtime.save()

        observed_context = []

        def one_agent_loop(**_kwargs):
            observed_context.append([dict(item) for item in session.messages])
            return ToolLoopSegmentResult(
                final_text="The new request is underway.", tool_rounds=1, finish_reason="stop",
            )

        agent._run_parent_tool_loop = one_agent_loop
        result = agent.run_turn("Please explain what a hash table is.", spawn_explore=False)

        assert result.assistant_text == "The new request is underway."
        assert observed_context
        context_text = "\n".join(message.get("content", "") for message in observed_context[0])
        assert "上一回合" in context_text
        assert "当前回合已由新的用户请求恢复并正在执行" in context_text
        persisted = json.loads((Path(session.session_dir) / "goal-state.json").read_text(encoding="utf-8"))
        assert persisted["status"] == "active"
        assert persisted["pause_reason"] is None
