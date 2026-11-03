from __future__ import annotations

import html
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from agent import Agent
from llm_client import LLMClient, LLMResponse, load_config
from llm_models import get_registry
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_records_agent_recording import WorkRecordAgentRecording
from work_records_experiment import WorkRecordExperimentController
from work_records_luna_intent import (
    EXPECTED_PROVIDER_MODEL,
    EXPECTED_VENDOR,
    MODEL_ID,
    WorkRecordsLunaIntent,
)
from work_records_luna_recall import WorkRecordsLunaRecall
from turn_intent import classify_turn


LIVE_E2E_OPT_IN = "MY_AGENT_LIVE_WORK_RECORD_LUNA_E2E_OPTIN"


class NoToolsMainLLM:
    """The real Agent's main conversation dependency; it never emits tool calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, "model": kwargs.get("model"), "tool_calls": 0})
        return LLMResponse(
            model="synthetic-main-fake", content="synthetic main response", tool_calls=[],
            finish_reason="stop", usage=None, raw={},
        )


class TimedLiveClient:
    """Capture only synthetic prompt envelopes and safe call metadata, never secrets."""

    def __init__(self, client: LLMClient, *, api_key: str) -> None:
        self._client = client
        self.paths = client.paths
        self._api_key = api_key
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, **kwargs):
        assert kwargs.get("model") == MODEL_ID
        assert kwargs.get("tools") == []
        envelope = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
        assert str(self.paths.agent_root) not in envelope
        assert self._api_key not in envelope
        started = time.perf_counter()
        call = {
            "messages": messages,
            "model": kwargs.get("model"),
            "tools": kwargs.get("tools"),
            "elapsed_ms": None,
        }
        self.calls.append(call)
        try:
            return self._client.chat(messages, **kwargs)
        finally:
            call["elapsed_ms"] = (time.perf_counter() - started) * 1000


def _temporary_session(tmp_path: Path) -> Session:
    root = tmp_path / "synthetic-agent-root"
    paths = AgentPaths(
        agent_root=root,
        workspace=root / "workspace",
        data=root / "data",
        evolve=root / "evolve",
    )
    conversation_id = "synthetic-luna-e2e-session"
    session_dir = paths.data / "sessions" / conversation_id
    session_dir.mkdir(parents=True)
    session = Session(
        conversation_id=conversation_id,
        session_dir=session_dir,
        goal="",
        meta=SessionMeta(turn_mode="ask"),
        messages=[],
        paths=paths,
    )
    session.save()
    return session


def _assert_provider_payloads_are_minimal(calls, *, session: Session, api_key: str, provider_root: Path):
    forbidden = (str(session.session_dir), str(session.paths.agent_root), str(provider_root), session.conversation_id, api_key)
    for call in calls:
        serialized = json.dumps(call["messages"], ensure_ascii=False, separators=(",", ":"))
        assert all(value not in serialized for value in forbidden)
        assert call["model"] == MODEL_ID
        assert call["tools"] == []

    intent_payloads = [json.loads(call["messages"][1]["content"]) for call in calls[:2]]
    assert all(set(payload) == {"text", "recognition_view", "current_project_available"} for payload in intent_payloads)
    assert intent_payloads[0]["recognition_view"] == []
    assert len(intent_payloads[1]["recognition_view"]) == 1
    assert set(intent_payloads[1]["recognition_view"][0]) == {"item_id", "title", "scope"}
    assert intent_payloads[1]["recognition_view"][0]["scope"] == {"kind": "independent"}

    recall_payload = json.loads(calls[2]["messages"][1]["content"])
    assert set(recall_payload) == {"question", "candidates"}
    assert len(recall_payload["candidates"]) == 1
    assert set(recall_payload["candidates"][0]) == {"id", "title", "excerpt"}


@pytest.mark.skipif(
    os.environ.get(LIVE_E2E_OPT_IN) != "1",
    reason=f"set {LIVE_E2E_OPT_IN}=1 to allow a synthetic-only 0x567 Luna end-to-end run",
)
def test_live_luna_agent_recording_and_recall_end_to_end(tmp_path):
    """Three provider calls: two intent decisions, then one confirmed semantic recall."""
    provider_paths = AgentPaths.discover(start=Path(__file__).resolve())
    entry = get_registry(provider_paths).get(MODEL_ID)
    assert entry is not None
    assert (entry.id, entry.vendor, entry.provider_model) == (
        MODEL_ID, EXPECTED_VENDOR, EXPECTED_PROVIDER_MODEL,
    )
    api_key = entry.resolve_api_key(provider_paths)
    if not api_key:
        pytest.skip("0x567 credential unavailable; no request made")

    live = TimedLiveClient(LLMClient(load_config(), paths=provider_paths), api_key=api_key)
    session = _temporary_session(tmp_path)
    store = WorkRecordStore(session)
    confirmation_previews: list[dict[str, Any]] = []

    def preview_confirmation(preview: str, allow_approve_all: bool) -> str:
        assert allow_approve_all is False
        payload = json.loads(preview)
        assert payload["target_session_id"] == session.conversation_id
        assert len(payload["candidate_disclosure"]) == 1
        assert len(payload["candidate_disclosure"][0]["requirements"]) == 2
        assert len(payload["exact_provider_messages"]) == 2
        confirmation_previews.append(payload)
        return "y"

    agent = Agent.create(
        session,
        llm=NoToolsMainLLM(),
        confirm_fn=preview_confirmation,
    )
    intent = WorkRecordsLunaIntent(
        live,
        outbound_approved=True,
        current_project_id=None,
    )
    recall = WorkRecordsLunaRecall(live)
    first_request = (
        "Create one independent item: prepare a synthetic mission log for Zeta-41, "
        "delivering a short summary with two fictional observations."
    )
    second_request = (
        "Continue the only independent item titled 'Synthetic mission log Zeta-41': "
        "add a requirement that the summary include three fictional observations and an acceptance note."
    )
    agent.enable_work_record_recording = True
    agent.work_record_recording_controller = WorkRecordAgentRecording(
        agent=agent,
        store=store,
        recognizer=intent,
        # The synthetic test harness supplies one stable ID per submitted turn;
        # this does not claim the desktop transport currently supplies one.
        idempotency_key_provider=lambda text: (
            "synthetic-live-e2e:create-zeta-41" if text == first_request else
            "synthetic-live-e2e:append-zeta-41" if text == second_request else None
        ),
    )
    agent.enable_work_record_recall = True
    agent.work_record_recall_controller = WorkRecordExperimentController(
        agent=agent,
        store=store,
        recognizer=intent,
        semantic_matcher=recall,
    )

    recall_request = "Recap the requirements recorded for the synthetic Zeta-41 mission-log item."
    assert classify_turn(recall_request) == "recall"

    started = time.perf_counter()
    first_turn = agent.run_turn(first_request, spawn_explore=False)
    first_items = store.list_items()
    assert len(first_items) == 1 and len(first_items[0]["requirements"]) == 1
    assert live.calls and len(live.calls) == 1

    second_turn = agent.run_turn(second_request, spawn_explore=False)
    second_items = store.list_items()
    assert len(second_items) == 1 and len(second_items[0]["requirements"]) == 2
    assert len(live.calls) == 2

    recall_turn = agent.run_turn(recall_request, spawn_explore=False)
    elapsed_ms = (time.perf_counter() - started) * 1000
    recalled_item = store.list_items()[0]
    requirements = [row["text"] for row in recalled_item["requirements"]]
    assert len(requirements) == 2
    decoded_reply = html.unescape(recall_turn.assistant_text)
    assert all(text in decoded_reply for text in requirements)
    # Preserve and verify the renderer's safe entity escaping while comparing the
    # exact source text after decoding its presentation layer.
    assert "Zeta&#45;41" in recall_turn.assistant_text
    assert len(recalled_item["runs"]) == 2
    assert all(run["status"] == "unknown" for run in recalled_item["runs"])
    assert all(run["verification"]["status"] == "not_recorded" for run in recalled_item["runs"])
    assert len(confirmation_previews) == 1
    assert len(live.calls) == 3
    assert len(agent.llm.calls) == 2
    assert all(call["tool_calls"] == 0 for call in agent.llm.calls)

    recall_payload = json.loads(live.calls[2]["messages"][1]["content"])
    recall_excerpt = recall_payload["candidates"][0]["excerpt"]
    assert all(text in recall_excerpt for text in requirements)
    _assert_provider_payloads_are_minimal(
        live.calls,
        session=session,
        api_key=api_key,
        provider_root=provider_paths.agent_root,
    )
    assert confirmation_previews[0]["exact_provider_messages"] == live.calls[2]["messages"]
    print(
        f"live_work_record_e2e provider={EXPECTED_VENDOR}/{EXPECTED_PROVIDER_MODEL} model={MODEL_ID} "
        f"requests={len(live.calls)} intent_requests=2 recall_requests=1 "
        f"provider_elapsed_ms={[round(call['elapsed_ms'], 1) for call in live.calls]} "
        f"total_elapsed_ms={elapsed_ms:.1f} items={len(store.list_items())} requirements=2 "
        "synthetic_only=true main_tool_calls=0 records_unknown=true"
    )
