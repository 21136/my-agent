from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import Agent, build_llm_tools
from llm_client import LLMResponse
from session import Session, SessionMeta, utc_now_iso
from tests.isolation_helpers import temporary_agent_paths


class _ScriptedLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.tool_payloads: list[list[dict[str, Any]]] = []

    def set_cancel_event(self, _event: Any) -> None:
        pass

    def chat(self, _messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        self.calls += 1
        self.tool_payloads.append(kwargs.get("tools") or [])
        return self.responses.pop(0)


def _response(*, text: str | None = None, tool: str | None = None) -> LLMResponse:
    calls = [] if tool is None else [{
        "id": f"call-{tool}", "type": "function",
        "function": {"name": tool, "arguments": "{}"},
    }]
    return LLMResponse(model="mock", content=text, tool_calls=calls,
                       finish_reason="tool_calls" if calls else "stop", usage=None, raw={})


def _make_agent(paths: Any, llm: _ScriptedLLM, mode: str = "agent") -> tuple[Agent, Session]:
    session = Session(
        conversation_id="git-review-semantic",
        session_dir=paths.data / "sessions" / "git-review-semantic",
        goal="semantic git review entry",
        meta=SessionMeta(topics=[], llm_model="mock", updated_at=utc_now_iso(),
                         phase="S4", turn_mode=mode),
        messages=[], paths=paths,
    )
    session.save()
    return Agent.create(session, llm=llm, confirm_fn=lambda *_args: "y"), session


def test_model_selectable_review_tool_only_in_agent_mode():
    with temporary_agent_paths() as paths:
        agent_mode = Session("agent", paths.data / "sessions" / "agent", "",
            SessionMeta(topics=[], llm_model="mock", updated_at=utc_now_iso(), phase="S4", turn_mode="agent"), [], paths)
        ask_mode = Session("ask", paths.data / "sessions" / "ask", "",
            SessionMeta(topics=[], llm_model="mock", updated_at=utc_now_iso(), phase="S4", turn_mode="ask"), [], paths)
        names = lambda session: {x["function"]["name"] for x in build_llm_tools(session)}
        assert "start_git_review" in names(agent_mode)
        assert "start_git_review" not in names(ask_mode)


def test_review_selected_mid_segment_expands_budget_and_gates_premature_final(monkeypatch):
    monkeypatch.setenv("PARENT_EXECUTE_SEGMENT_MAX", "1")
    monkeypatch.setenv("PARENT_EXECUTE_TOTAL_MAX", "1")
    monkeypatch.setenv("GIT_REVIEW_SEGMENT_MAX", "3")
    monkeypatch.setenv("GIT_REVIEW_TOTAL_MAX", "4")
    from goal_runtime import GoalRuntime
    observed = []
    original_record = GoalRuntime.record_segment_usage

    def record_and_observe(runtime, **kwargs):
        result = original_record(runtime, **kwargs)
        if kwargs["tool_turns"]:
            observed.append((runtime._run_auto_tool_turns_used,
                             runtime._run_auto_tool_turns_limit,
                             runtime.should_auto_continue()))
        return result

    monkeypatch.setattr(GoalRuntime, "record_segment_usage", record_and_observe)
    with temporary_agent_paths() as paths:
        premature = "The review is complete. No findings."
        llm = _ScriptedLLM([
            _response(tool="start_git_review"),
            _response(text=premature),
            _response(text=premature),
        ])
        agent, session = _make_agent(paths, llm)
        runtime = GoalRuntime(Path(session.session_dir))
        runtime.ensure_goal(goal_text=session.goal)
        runtime.mark_multi_project_unverified(["alpha", "beta"])

        result = agent.run_turn("Please inspect the changes in this workspace.", spawn_explore=False)

        assert observed[0] == (1, 4, True)
        assert llm.calls == 3, (result.assistant_text, result.finish_reason, result.tool_rounds, result.total_tool_rounds, session.turn_intent, agent.executor.session.git_review_turn)
        assert result.execute_segments >= 2  # review budget is adopted after in-turn activation
        assert result.finish_reason != "total_cap_exceeded"
        assert "The review is complete. No findings." not in result.assistant_text
        assert "审查未完成" in result.assistant_text
        assert session.git_review_turn is True
        assert agent.executor.session.git_review_turn is True
        assert agent.executor.session.git_review_must_continue is True
        assert agent.executor.session.git_review_completed_paths == []
        from git_review_rails import git_review_incomplete
        assert git_review_incomplete(agent.executor.session) is True
        state = GoalRuntime(Path(session.session_dir)).load()
        assert state is not None
        assert state.multi_project_unverified is True
        assert state.multi_project_ids == ["alpha", "beta"]
        assert state.status != "done"

def test_natural_language_and_quoted_mentions_do_not_activate_without_model_tool_call():
    prompts = [
        "Do not review the diff; explain what the phrase 'review the diff' means.",
        "The quoted request was: 'please review my changes'. I am only asking what it means.",
    ]
    for prompt in prompts:
        with temporary_agent_paths() as paths:
            llm = _ScriptedLLM([_response(text="Understood.")])
            agent, session = _make_agent(paths, llm)
            agent.run_turn(prompt, spawn_explore=False)
            assert llm.calls == 1
            assert session.git_review_turn is False
            assert agent.executor.session.git_review_turn is False
            assert any(tool["function"]["name"] == "start_git_review" for tool in llm.tool_payloads[0])


def test_unoffered_review_tool_call_cannot_activate_in_ask_mode():
    with temporary_agent_paths() as paths:
        llm = _ScriptedLLM([_response(tool="start_git_review"), _response(text="Not activated.")])
        agent, session = _make_agent(paths, llm, mode="ask")

        agent.run_turn("Explain what a diff review is.", spawn_explore=False)

        assert llm.calls == 2
        assert all(tool["function"]["name"] != "start_git_review" for tool in llm.tool_payloads[0])
        assert session.git_review_turn is False
        assert agent.executor.session.git_review_turn is False
        results = [m for m in session.messages if m.get("role") == "tool"]
        assert results and '"ok": false' in results[-1].get("content", "")


@pytest.mark.parametrize("gate", ["stop", "wall"])
def test_review_activation_keeps_stop_and_wall_gates(monkeypatch, gate):
    from goal_runtime import GoalRuntime

    monkeypatch.setenv("PARENT_EXECUTE_SEGMENT_MAX", "1")
    monkeypatch.setenv("PARENT_EXECUTE_TOTAL_MAX", "1")
    monkeypatch.setenv("GIT_REVIEW_TOTAL_MAX", "4")
    original_record = GoalRuntime.record_segment_usage
    observed = []

    def record_with_gate(runtime, **kwargs):
        if kwargs["tool_turns"]:
            if gate == "stop":
                runtime.request_stop("user")
            else:
                kwargs["wall_seconds"] = runtime._run_auto_wall_seconds_limit
        result = original_record(runtime, **kwargs)
        if kwargs["tool_turns"]:
            observed.append((runtime._run_auto_tool_turns_used,
                             runtime._run_auto_tool_turns_limit,
                             runtime.should_auto_continue()))
        return result

    monkeypatch.setattr(GoalRuntime, "record_segment_usage", record_with_gate)
    with temporary_agent_paths() as paths:
        llm = _ScriptedLLM([_response(tool="start_git_review")])
        agent, session = _make_agent(paths, llm)
        result = agent.run_turn("Inspect the workspace changes.", spawn_explore=False)

        assert llm.calls == 1
        assert observed == [(1, 4, False)]
        assert "\u5ba1\u67e5\u672a\u5b8c\u6210" in result.assistant_text
        state = GoalRuntime(Path(session.session_dir)).load()
        assert state is not None
        assert state.budgets.tool_turns_used == 1
        if gate == "stop":
            assert state.stop_requested is True
            assert state.status == "stopped"
            assert result.finish_reason == "stopped"
        else:
            assert state.budgets.wall_seconds_used >= 900
            assert state.stop_requested is False


def test_review_activation_keeps_earlier_tool_usage(monkeypatch):
    from goal_runtime import GoalRuntime

    monkeypatch.setenv("PARENT_EXECUTE_SEGMENT_MAX", "2")
    monkeypatch.setenv("PARENT_EXECUTE_TOTAL_MAX", "2")
    monkeypatch.setenv("GIT_REVIEW_TOTAL_MAX", "4")
    original_record = GoalRuntime.record_segment_usage
    observed = []

    def record_and_observe(runtime, **kwargs):
        result = original_record(runtime, **kwargs)
        if kwargs["tool_turns"]:
            observed.append((runtime._run_auto_tool_turns_used,
                             runtime._run_auto_tool_turns_limit,
                             runtime.should_auto_continue()))
        return result

    monkeypatch.setattr(GoalRuntime, "record_segment_usage", record_and_observe)
    first = _response(tool="list_dir")
    first.tool_calls[0]["function"]["arguments"] = '{"path": "."}'
    with temporary_agent_paths() as paths:
        llm = _ScriptedLLM([first, _response(tool="start_git_review"),
                            _response(text="Done."), _response(text="Done.")])
        agent, session = _make_agent(paths, llm)
        result = agent.run_turn("Inspect changes.", spawn_explore=False)

        assert observed[0] == (2, 4, True)
        assert llm.calls == 4
        assert result.total_tool_rounds >= 2
        assert "\u5ba1\u67e5\u672a\u5b8c\u6210" in result.assistant_text
        state = GoalRuntime(Path(session.session_dir)).load()
        assert state is not None and state.budgets.tool_turns_used >= 2


def test_review_limit_smaller_than_ordinary_keeps_status_consistent(monkeypatch):
    from goal_runtime import GoalRuntime

    monkeypatch.setenv("PARENT_EXECUTE_SEGMENT_MAX", "1")
    monkeypatch.setenv("PARENT_EXECUTE_TOTAL_MAX", "3")
    monkeypatch.setenv("GIT_REVIEW_TOTAL_MAX", "1")
    original_record = GoalRuntime.record_segment_usage
    observed = []

    def record_and_observe(runtime, **kwargs):
        result = original_record(runtime, **kwargs)
        if kwargs["tool_turns"]:
            observed.append((runtime._run_auto_tool_turns_used,
                             runtime._run_auto_tool_turns_limit,
                             runtime.should_auto_continue()))
        return result

    monkeypatch.setattr(GoalRuntime, "record_segment_usage", record_and_observe)
    with temporary_agent_paths() as paths:
        llm = _ScriptedLLM([_response(tool="start_git_review")])
        agent, _session = _make_agent(paths, llm)
        result = agent.run_turn("Inspect changes.", spawn_explore=False)
        assert llm.calls == 1
        assert observed == [(1, 1, False)]
        assert "\u5ba1\u67e5\u672a\u5b8c\u6210" in result.assistant_text
