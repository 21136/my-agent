from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from agent import Agent


class FakeSession:
    def __init__(self):
        self.messages = []
        self.turn_intent = None

    def ensure_messages_loaded(self):
        return None

    def append_message(self, message):
        self.messages.append(dict(message))


def make_agent(*, enabled=True, bound=True):
    agent = object.__new__(Agent)
    agent.session = FakeSession()
    agent.executor = SimpleNamespace(confirm_fn=None)
    agent.enable_work_record_recall = enabled
    agent.work_record_recall_controller = SimpleNamespace(
        _store=SimpleNamespace(_session=agent.session if bound else FakeSession())
    )
    agent.enable_work_record_recording = False
    agent.work_record_recording_controller = None
    agent.fallback_calls = []
    agent._run_turn_without_work_record_recording = lambda text, **kwargs: (
        agent.fallback_calls.append((text, kwargs)) or SimpleNamespace(
            assistant_text="ordinary turn", tool_rounds=1, finish_reason="completed",
        )
    )
    return agent


def install_overview(monkeypatch, result=None, error=None):
    render = Mock(side_effect=error if error is not None else None, return_value=result)
    module = ModuleType("work_records_overview")
    module.render_work_records_overview = render
    monkeypatch.setitem(sys.modules, "work_records_overview", module)
    return render


def test_explicit_current_work_overview_uses_bound_local_helper_and_saves_privacy_notice(monkeypatch):
    agent = make_agent()
    render = install_overview(monkeypatch, "\u672c\u5730\u6982\u51b5\uff1a2 \u9879\uff0c\u5176\u4e2d 1 \u9879\u5f85\u786e\u8ba4\u3002")

    result = agent.run_turn("\u8bf7\u7ed9\u6211\u5f53\u524d\u5de5\u4f5c\u6574\u4f53\u6982\u51b5")

    assert render.call_count == 1
    assert render.call_args.args == (agent.session,)
    assert agent.fallback_calls == []
    assert result.turn_intent == "recall"
    assert result.finish_reason == "work_record_overview"
    assert result.tool_rounds == result.total_tool_rounds == 0
    assert agent.session.turn_intent == "recall"
    assert agent.session.messages[0] == {
        "role": "user", "content": "\u8bf7\u7ed9\u6211\u5f53\u524d\u5de5\u4f5c\u6574\u4f53\u6982\u51b5",
    }
    assert agent.session.messages[1]["role"] == "assistant"
    assert "\u672c\u5730\u6982\u51b5\uff1a2 \u9879" in agent.session.messages[1]["content"]
    assert "\u5bf9\u8bdd\u5386\u53f2" in agent.session.messages[1]["content"]
    assert "\u540e\u7eed\u5bf9\u8bdd\u6a21\u578b\u53ef\u80fd\u8bfb\u53d6" in agent.session.messages[1]["content"]


def test_overview_helper_failure_is_safe_and_never_falls_through_to_agent(monkeypatch):
    agent = make_agent()
    render = install_overview(monkeypatch, error=RuntimeError("SENSITIVE_HELPER_DETAIL"))

    result = agent.run_turn("\u5f53\u524d\u5de5\u4f5c\u6574\u4f53\u6982\u51b5")

    assert render.call_count == 1
    assert agent.fallback_calls == []
    assert "\u672a\u80fd\u67e5\u6e05" in result.assistant_text
    assert "SENSITIVE_HELPER_DETAIL" not in result.assistant_text
    assert len(agent.session.messages) == 2
    assert "\u5bf9\u8bdd\u5386\u53f2" in agent.session.messages[-1]["content"]


@pytest.mark.parametrize("text", [
    "\u9879\u76eeB\u60c5\u51b5\u5982\u4f55",
    "\u8bf7\u6839\u636e\u5f53\u524d\u5de5\u4f5c\u6574\u4f53\u6982\u51b5\u4fee\u590d\u767b\u5f55\u9875",
])
def test_nonoverview_or_execution_intent_keeps_normal_agent_path(monkeypatch, text):
    agent = make_agent()
    render = install_overview(monkeypatch, "should not be used")

    result = agent.run_turn(text)

    assert render.call_count == 0
    assert len(agent.fallback_calls) == 1
    assert result.assistant_text == "ordinary turn"


@pytest.mark.parametrize("enabled,bound", [(False, True), (True, False)])
def test_disabled_or_unbound_recall_controller_does_not_intercept_overview(monkeypatch, enabled, bound):
    agent = make_agent(enabled=enabled, bound=bound)
    render = install_overview(monkeypatch, "should not be used")

    result = agent.run_turn("\u5f53\u524d\u5de5\u4f5c\u6574\u4f53\u6982\u51b5")

    assert render.call_count == 0
    assert len(agent.fallback_calls) == 1
    assert result.assistant_text == "ordinary turn"


def test_overview_intent_does_not_capture_historical_lookup(monkeypatch):
    agent = make_agent()
    render = install_overview(monkeypatch, "should not be used")

    result = agent.run_turn("\u8bf7\u56de\u67e5\u4ee5\u524d\u7684\u9879\u76ee\u8bb0\u5f55")

    assert render.call_count == 0
    assert result.finish_reason != "work_record_overview"

