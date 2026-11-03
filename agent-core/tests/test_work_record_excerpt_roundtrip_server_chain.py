from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import server
from agent import Agent
from llm_client import LLMResponse
from main import ConversationRepl
from paths import AgentPaths
from session import Session, SessionMeta
from work_card_presentations import WorkCardPresentations
from work_card_ledger import WorkCardLedger
from work_record_cards import build_independent_work_record_card
from work_records import WorkRecordStore


class _SyntheticAgentLLM:
    def chat(self, *_args, **_kwargs):
        return LLMResponse(
            model="synthetic-agent", content="Synthetic explicit run reply",
            tool_calls=[], finish_reason="stop", usage=None, raw={},
        )


class _QueueWebSocket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.events: list[dict] = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        payload = await self.incoming.get()
        if payload is None:
            raise StopAsyncIteration
        return payload

    async def send(self, raw):
        self.events.append(json.loads(raw))


async def _wait_for_event(ws, *, event_type: str, request_id: str | None = None):
    for _ in range(500):
        match = next((event for event in reversed(ws.events)
                      if event.get("type") == event_type
                      and (request_id is None or event.get("request_id") == request_id)), None)
        if match is not None:
            return match
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {event_type} request_id={request_id!r}")


@pytest.mark.parametrize("save_excerpt", [True, False], ids=["approved", "rejected"])
def test_synthetic_host_run_excerpt_is_found_then_returned_only_for_same_session_request(
    tmp_path, monkeypatch, save_excerpt,
):
    """Exercise capture -> real host run settlement -> excerpt consent -> lookup -> selected detail."""
    import llm_client

    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    paths.workspace.mkdir()
    session_id = str(uuid.uuid4())
    session_dir = paths.data / "sessions" / session_id
    session_dir.mkdir(parents=True)
    session = Session(session_id, session_dir, "", SessionMeta(turn_mode="ask"), [], paths)
    session.save()
    foreign_id = str(uuid.uuid4())
    foreign_dir = paths.data / "sessions" / foreign_id
    foreign_dir.mkdir(parents=True)
    foreign = Session(foreign_id, foreign_dir, "", SessionMeta(turn_mode="ask"), [], paths)
    foreign.save()

    agent = Agent.create(session, llm=_SyntheticAgentLLM(), confirm_fn=lambda *_: "n")
    monkeypatch.setattr(server, "resume_desktop_or_create", lambda _paths: session)
    monkeypatch.setattr(server, "load_session_for_harness", lambda *args, **kwargs: foreign)
    monkeypatch.setattr(server.WsBridge, "emit_session_state", lambda *_args: None)
    monkeypatch.setattr(server, "emit_corruption_notices", lambda *_args: None)
    monkeypatch.setattr(server, "_emit_pending_work_card_references", lambda *_args: None)

    holders = {}

    def build_repl(_session, _paths, bridge, **_kwargs):
        repl = ConversationRepl(
            paths, session, agent, input_fn=lambda _prompt: "", output_fn=lambda _text: None,
            assistant_output_fn=bridge.emit_assistant,
        )
        repl._rebind_agent = lambda: None
        bridge._session_provider = lambda: repl.session
        bridge.init_post_turn_send_gate(session)
        holders.update(bridge=bridge, repl=repl)
        return repl

    monkeypatch.setattr(server, "_build_repl", build_repl)

    class _Registry:
        def get(self, _model_id):
            return SimpleNamespace(
                id="0x567-flash", vendor="0x567", provider_model="gpt-6-luna",
                max_input_tokens=96000,
            )

    monkeypatch.setattr("work_records_service.get_registry", lambda *args, **kwargs: _Registry())
    monkeypatch.setattr("work_records_luna_intent.get_registry", lambda *args, **kwargs: _Registry())

    class _RegistryProvider:
        paths = None

        def __init__(self, **_kwargs):
            self.cancel_event = None

        def set_cancel_event(self, event):
            self.cancel_event = event

        def chat(self, _messages, **_kwargs):
            return SimpleNamespace(content=json.dumps({
                "action": "record", "kind": "requirement", "title": "Synthetic tracked work",
                "requirement": "Run the synthetic task", "scope": "independent", "item_id": None,
            }))

    # The capture service constructs llm_client.LLMClient; the runtime provider is
    # registry-resolved by the existing service path. Keep both boundaries synthetic.
    provider = _RegistryProvider()
    monkeypatch.setattr(llm_client, "LLMClient", lambda **_kwargs: provider)

    handler = server.WsSessionHandler(paths)
    original_user_request_id = str(uuid.uuid4())
    detail_request_id = str(uuid.uuid4())
    foreign_detail_request_id = str(uuid.uuid4())

    async def run_chain():
        ws = _QueueWebSocket()
        task = asyncio.create_task(handler.handle(ws))
        seen_confirmations: set[str] = set()
        post_turn_confirmation = None

        async def send(message):
            await ws.incoming.put(json.dumps(message))

        await send({
            "type": "user.message", "text": "记录一下 synthetic task 并运行它",
            "request_id": original_user_request_id,
        })

        # Approve explicit item creation, then independently choose whether to save
        # the exact host-produced reply excerpt after its terminal run has settled.
        for _ in range(500):
            for event in tuple(ws.events):
                if event.get("type") != "confirm.request" or event.get("request_id") in seen_confirmations:
                    continue
                seen_confirmations.add(event["request_id"])
                if event.get("confirm_subtype") == "work_record_reply_excerpt":
                    post_turn_confirmation = event
                    choice = "y" if save_excerpt else "n"
                else:
                    choice = "y"
                await send({"type": "confirm.response", "request_id": event["request_id"], "choice": choice})
            if any(event.get("type") == "work-record.post-turn.result"
                   and event.get("request_id") == original_user_request_id for event in ws.events):
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("host capture/run/post-turn consent chain did not finish")

        assert post_turn_confirmation is not None
        post_turn_result = next(event for event in ws.events
                                if event.get("type") == "work-record.post-turn.result"
                                and event.get("request_id") == original_user_request_id)
        store = WorkRecordStore(session)
        items = store.list_items()
        assert len(items) == 1
        item = items[0]
        requirement = item["requirements"][0]
        run_row = item["runs"][0]
        assert run_row["status"] == "unknown"
        assert run_row["verification"]["status"] == "not_verified"
        assert type(run_row["verification"]["host_execution"]["sequence"]) is int
        assert run_row["verification"]["host_execution"]["sequence"] > 0
        turn_end_index = next(index for index, event in enumerate(ws.events)
                              if event.get("type") == "turn.end")
        excerpt_confirm_index = next(index for index, event in enumerate(ws.events)
                                     if event.get("type") == "confirm.request"
                                     and event.get("confirm_subtype") == "work_record_reply_excerpt")
        assert ws.events[turn_end_index]["ok"] is True
        assert turn_end_index < excerpt_confirm_index  # Consent is requested after host turn termination.
        consent_payload = json.loads(post_turn_confirmation["preview"].split("\n", 2)[2])
        if save_excerpt:
            assert post_turn_result["outcome"] == "recorded"
            assert run_row["assistant_reply_excerpt"]["text"] == consent_payload["assistant_reply_excerpt"]
            assert "Synthetic explicit run reply" in run_row["assistant_reply_excerpt"]["text"]
        else:
            assert post_turn_result["outcome"] == "not_recorded"
            assert "assistant_reply_excerpt" not in run_row

        # Find the exact persisted result through the existing same-session overview query.
        overview_request_id = str(uuid.uuid4())
        await send({
            "type": "work-records.overview.query", "session_id": session_id,
            "request_id": overview_request_id,
        })
        overview = await _wait_for_event(
            ws, event_type="work-records.overview.result", request_id=overview_request_id,
        )
        assert overview["session_id"] == session_id and overview["state"] == "visible"
        projected_item = next(row for group in overview["groups"] for row in group["items"]
                              if row["item_id"] == item["item_id"])
        overview_result = next(result for result in projected_item["current_results"]
                               if result["requirement_id"] == requirement["requirement_id"])
        result_id = overview_result["result_id"]
        assert result_id == run_row["result_id"]

        # Provide the same excerpt-free durable snapshot that the existing card
        # result-view route consumes. Result identity itself came from the real
        # same-session overview response above.
        ledger = WorkCardLedger(session, store)
        saved_card = ledger.save_snapshot(
            str(uuid.uuid4()), build_independent_work_record_card(store, item["item_id"]),
        )
        assert saved_card.status == "saved"
        card_id = saved_card.card_id
        assert "Synthetic explicit run reply" not in json.dumps(saved_card.record["snapshot"])

        # Register the normal same-session presentation receipt, then select the
        # exact overview result with a fresh request_id.
        presentation_id = str(uuid.uuid4())
        session.append_sent_work_card_reference(presentation_id, card_id, run_id=str(uuid.uuid4()))
        assert WorkCardPresentations(session, store, ledger).create(presentation_id, card_id).status == "created"
        await send({
            "type": "work-card.result.view", "request_id": detail_request_id,
            "session_id": session_id, "presentation_id": presentation_id,
            "card_id": card_id, "result_id": result_id,
            "requirement_id": requirement["requirement_id"],
        })
        detail_response = await _wait_for_event(
            ws, event_type="work-card.result.view.done", request_id=detail_request_id,
        )
        assert detail_response["request_id"] == detail_request_id
        assert detail_response["status"] == "acknowledged"
        detail = detail_response.get("local_assistant_reply_excerpt_detail")
        if save_excerpt:
            assert detail is not None
            assert detail["session_id"] == session_id
            assert detail["item_id"] == item["item_id"]
            assert detail["requirement_id"] == requirement["requirement_id"]
            assert detail["result_id"] == result_id
            assert "助理回复摘录" in detail["label"] and "未核验" in detail["label"]
            assert "原始来源不可定位" in detail["label"]
            assert detail["result_status"] == "unknown"
            assert detail["verification_status"] == "not_verified"
            assert "Synthetic explicit run reply" in detail["escaped_excerpt_text"]
        else:
            assert detail is None

        # A foreign-session request cannot retrieve an excerpt that exists locally.
        await send({
            "type": "work-card.result.view", "request_id": foreign_detail_request_id,
            "session_id": foreign_id, "presentation_id": presentation_id,
            "card_id": card_id, "result_id": result_id,
            "requirement_id": requirement["requirement_id"],
        })
        foreign_response = await _wait_for_event(
            ws, event_type="work-card.result.view.done", request_id=foreign_detail_request_id,
        )
        assert foreign_response["request_id"] == foreign_detail_request_id
        assert foreign_response["status"] == "session_changed"
        assert "local_assistant_reply_excerpt_detail" not in foreign_response

        # Error/foreign request leaves both current-session excerpt and snapshot unchanged.
        if save_excerpt:
            assert "Synthetic explicit run reply" in WorkRecordStore(session).get_item(item["item_id"])["runs"][0]["assistant_reply_excerpt"]["text"]
        assert "Synthetic explicit run reply" not in json.dumps(ledger.get_card(card_id)["snapshot"])
        await ws.incoming.put(None)
        await asyncio.wait_for(task, timeout=5)
        return ws.events

    asyncio.run(run_chain())




