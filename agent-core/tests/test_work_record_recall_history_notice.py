from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import work_records_luna_recall as luna
from agent import Agent
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_records_experiment import WorkRecordExperimentController
from work_records_luna_recall import MODEL_ID, WorkRecordsLunaRecall

QUESTION = "\u67e5\u4e00\u4e0b\u4e4b\u524d\u90a3\u6761\u5de5\u4f5c\u8bb0\u5f55\u8bf4\u4e86\u4ec0\u4e48"
HISTORY_NOTICE = "\u540e\u7eed\u5bf9\u8bdd\u6a21\u578b\u53ef\u80fd\u8bfb\u53d6"


class NoCallAgentModel:
    def __init__(self):
        self.calls = []

    def chat(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("explicit work-record recall must bypass the ordinary Agent model")


class FakeRecallProvider:
    def __init__(self):
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        payload = json.loads(messages[1]["content"])
        item_id = payload["candidates"][0]["id"]
        return SimpleNamespace(
            content=json.dumps({"status": "match", "candidate_ids": [item_id]}),
            tool_calls=[],
        )


def make_agent(tmp_path: Path, monkeypatch, confirm_choice: str):
    root = tmp_path / "agent-root"
    root.mkdir()
    paths = AgentPaths(
        agent_root=root,
        workspace=root / "workspace",
        data=root / "data",
        evolve=root / "evolve",
    )
    session_dir = paths.data / "sessions" / "privacy-recall-session"
    session_dir.mkdir(parents=True)
    session = Session(
        conversation_id="privacy-recall-session",
        session_dir=session_dir,
        goal="synthetic privacy test",
        meta=SessionMeta(),
        messages=[],
        paths=paths,
    )
    session.save()
    store = WorkRecordStore(session)
    item = store.create_item(title="Synthetic record title", scope={"kind": "independent"})
    store.add_requirement(item["item_id"], "Synthetic requirement text", event_id="privacy-recall-requirement")

    ordinary_model = NoCallAgentModel()
    agent = Agent.create(
        session,
        llm=ordinary_model,
        confirm_fn=lambda _preview, _allow: confirm_choice,
        enable_work_record_recall=True,
    )
    provider = FakeRecallProvider()
    registry_entry = SimpleNamespace(
        id=MODEL_ID,
        vendor="0x567",
        provider_model="gpt-6-luna",
        max_input_tokens=100_000,
    )
    monkeypatch.setattr(luna, "get_registry", lambda *_args: SimpleNamespace(
        get=lambda model_id: registry_entry if model_id == MODEL_ID else None,
    ))
    agent.work_record_recall_controller = WorkRecordExperimentController(
        agent=agent,
        store=store,
        recognizer=lambda *_args: {"action": "recall"},
        semantic_matcher=WorkRecordsLunaRecall(provider),
        semantic_excerpt_policy=lambda _title, requirements: "\n".join(requirements),
    )
    return agent, session, ordinary_model, provider


def test_external_recall_preview_discloses_future_session_history_use_and_answer_repeats_it(tmp_path, monkeypatch):
    previews = []
    agent, session, ordinary_model, provider = make_agent(tmp_path, monkeypatch, "y")
    agent.executor.confirm_fn = lambda preview, _allow: previews.append(preview) or "y"

    result = agent.run_turn(QUESTION)

    assert ordinary_model.calls == []
    assert len(provider.calls) == 1
    assert len(previews) == 1
    preview = json.loads(previews[0])
    disclosure = preview.get("history_disclosure", "")
    assert HISTORY_NOTICE in disclosure
    assert "\u5bf9\u8bdd\u5386\u53f2" in disclosure
    assert HISTORY_NOTICE in result.assistant_text
    assert session.messages[-1]["role"] == "assistant"
    assert HISTORY_NOTICE in session.messages[-1]["content"]


@pytest.mark.parametrize("choice", ["n", "cancelled"])
def test_recall_refusal_or_cancel_discloses_history_but_never_calls_provider(tmp_path, monkeypatch, choice):
    previews = []
    agent, session, ordinary_model, provider = make_agent(tmp_path, monkeypatch, choice)
    agent.executor.confirm_fn = lambda preview, _allow: previews.append(preview) or choice

    result = agent.run_turn(QUESTION)

    assert ordinary_model.calls == []
    assert provider.calls == []
    assert len(previews) == 1
    assert HISTORY_NOTICE in json.loads(previews[0]).get("history_disclosure", "")
    assert HISTORY_NOTICE in result.assistant_text
    assert session.messages[-1]["role"] == "assistant"
    assert HISTORY_NOTICE in session.messages[-1]["content"]