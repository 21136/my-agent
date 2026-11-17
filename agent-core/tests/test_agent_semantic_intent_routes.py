from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

import dialogue_recall
from agent import Agent
from llm_client import LLMClient, LLMResponse
from session import Session, SessionMeta, utc_now_iso
from tests.isolation_helpers import temporary_agent_paths
from work_records import WorkRecordStore
from work_records_luna_recall import WorkRecordsLunaRecall


class _ScriptedMainModel:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, *, tools=None, **kwargs):
        self.calls.append({"messages": messages, "tools": tools, **kwargs})
        return self.responses.pop(0)


def _response(*, text: str | None = None, tool: str | None = None, arguments: dict | None = None):
    tool_calls = [] if tool is None else [{
        "id": f"call-{tool}",
        "type": "function",
        "function": {"name": tool, "arguments": json.dumps(arguments or {})},
    }]
    return LLMResponse(
        model="mock", content=text, tool_calls=tool_calls,
        finish_reason="tool_calls" if tool_calls else "stop", usage=None, raw={},
    )


def _new_session(paths, session_id: str, *, mode: str) -> Session:
    session = Session(
        conversation_id=session_id,
        session_dir=paths.data / "sessions" / session_id,
        goal="semantic route regression",
        meta=SessionMeta(
            topics=[], llm_model="mock", updated_at=utc_now_iso(),
            phase="S4", turn_mode=mode,
        ),
        messages=[], paths=paths,
    )
    session.save()
    return session


def _with_recall(agent_session: Session, paths, model, confirm):
    controller = SimpleNamespace(
        _store=WorkRecordStore(agent_session),
        _semantic_matcher=WorkRecordsLunaRecall(LLMClient(paths=paths)),
    )
    return Agent.create(
        agent_session, llm=model, confirm_fn=confirm,
        enable_work_record_recall=True, work_record_recall_controller=controller,
    )


def test_recall_word_in_discussion_does_not_force_history_tool_or_confirmation():
    with temporary_agent_paths() as paths:
        session = _new_session(paths, "recall-discussion", mode="ask")
        model = _ScriptedMainModel([_response(text="It describes remembering earlier information.")])
        confirmations = []
        agent = _with_recall(session, paths, model,
                             lambda *args: confirmations.append(args) or "y")

        result = agent.run_turn("\u521a\u624d\u7684\u56de\u5fc6\u673a\u5236\u662f\u4ec0\u4e48\u610f\u601d\uff1f",
                                spawn_explore=False)

        names = {tool["function"]["name"] for tool in model.calls[0]["tools"]}
        assert "recall_current_dialogue" in names
        assert "list_dir" in names
        assert result.assistant_text == "It describes remembering earlier information."
        assert confirmations == []
        assert len(model.calls) == 1


def test_recall_word_in_execution_request_keeps_general_tools_for_model_choice():
    with temporary_agent_paths() as paths:
        session = _new_session(paths, "recall-execution", mode="ask")
        model = _ScriptedMainModel([
            _response(tool="list_dir", arguments={"path": "docs"}),
            _response(text="The docs directory is listed."),
        ])
        agent = _with_recall(session, paths, model, lambda *_args: "y")
        (paths.workspace / "docs").mkdir(parents=True, exist_ok=True)

        result = agent.run_turn(
            "\u521a\u624d\u6211\u4eec\u8ba8\u8bba\u4e86\u4ec0\u4e48\uff1f\u53e6\u5916\u8bf7\u5217\u51fa docs \u76ee\u5f55",
            spawn_explore=False,
        )

        names = {tool["function"]["name"] for tool in model.calls[0]["tools"]}
        assert "recall_current_dialogue" in names
        assert "list_dir" in names
        assert result.assistant_text == "The docs directory is listed."
        assert len(model.calls) == 2
        tool_result = next(message for message in session.messages if message.get("role") == "tool")
        assert '"ok": true' in tool_result["content"]
        assert "recall_current_dialogue" not in {
            call["function"]["name"]
            for call in session.messages[1].get("tool_calls", [])
        }


def test_keyword_explore_is_not_predispatched_but_explicit_true_is_preserved(monkeypatch):
    import subagent
    from subagent import SubagentResult

    with temporary_agent_paths() as paths:
        session = _new_session(paths, "explore-semantic-default", mode="agent")
        model = _ScriptedMainModel([_response(text="I can inspect that if you want.")])
        calls = []

        def fake_explore(self, task, **kwargs):
            calls.append((task, kwargs))
            return SubagentResult(
                kind="explore", summary="synthetic", paths_cited=[], tool_rounds=1,
                truncated=False, task=task,
            ), False

        monkeypatch.setattr(subagent.SubagentRunner, "run_explore_with_continue", fake_explore)
        agent = Agent.create(session, llm=model, confirm_fn=lambda *_args: "y")

        agent.run_turn("\u6587\u6863\u548c\u4ee3\u7801\u53ef\u80fd\u8131\u8282\u4e86\uff0c\u4f60\u770b\u770b",
                       spawn_explore=None)
        assert calls == []
        assert len(model.calls) == 1

        explicit_model = _ScriptedMainModel([_response(text="Explicit explore completed.")])
        explicit_session = _new_session(paths, "explore-explicit", mode="agent")
        explicit_agent = Agent.create(explicit_session, llm=explicit_model,
                                      confirm_fn=lambda *_args: "y")
        explicit_agent.run_turn("A plain request with no explore markers.", spawn_explore=True)
        assert len(calls) == 1
        assert len(explicit_model.calls) == 1
