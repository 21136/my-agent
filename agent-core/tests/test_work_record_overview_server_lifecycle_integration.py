from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import server
import work_record_visibility_clock as visibility_clock
import work_records_luna_recall as luna_recall
from agent import Agent
from llm_client import LLMResponse
from paths import AgentPaths
from session import Session, SessionMeta, load_session_for_harness
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations
from work_record_cards import build_independent_work_record_card
from work_record_visibility_clock import ElapsedClockSample
from work_records import WorkRecordStore
from work_records_experiment import WorkRecordExperimentController
from work_records_luna_recall import WorkRecordsLunaRecall


_NS = 1_000_000_000
_WAIT = 30 * 60 * _NS
_BOOT_A = "synthetic-boot-before-restart"
_BOOT_B = "synthetic-boot-after-restart"
_WALL_A = datetime(2031, 4, 5, 12, 0, tzinfo=timezone.utc)


def _sample(boot_id: str, monotonic_ns: int, wall: datetime) -> ElapsedClockSample:
    return ElapsedClockSample(
        boot_id, monotonic_ns,
        wall.isoformat(timespec="microseconds").replace("+00:00", "Z"),
    )


def _new_completed_item(store: WorkRecordStore, title: str) -> tuple[dict, dict, str]:
    item = store.create_item(title=title, scope={"kind": "independent"})
    requirement = store.add_requirement(
        item["item_id"], f"Requirement for {title}", event_id=str(uuid.uuid4()),
        idempotency_key=f"synthetic:{uuid.uuid4()}",
    )
    run_id, result_id = str(uuid.uuid4()), str(uuid.uuid4())
    store.begin_requirement_run(
        item["item_id"], requirement["requirement_id"], run_id=run_id,
        expected_current_run_id=None,
    )
    store.record_result_if_current_run(
        item["item_id"], requirement["requirement_id"], run_id=run_id,
        result_id=result_id, status="completed", summary=f"Synthetic result for {title}",
        verification={"status": "not_verified"},
    )
    return item, requirement, result_id


def _publish_result(session: Session, store: WorkRecordStore, item: dict) -> tuple[str, str]:
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(
        str(uuid.uuid4()), build_independent_work_record_card(store, item["item_id"]),
    )
    assert saved.status == "saved"
    presentation_id = str(uuid.uuid4())
    presentations = WorkCardPresentations(session, store, ledger)
    assert presentations.create(presentation_id, saved.card_id).status == "created"
    session.append_sent_work_card_reference(presentation_id, saved.card_id, run_id=str(uuid.uuid4()))
    return saved.card_id, presentation_id


def _overview_request(session: Session, request_id: str) -> dict:
    return {
        "type": "work-records.overview.query", "session_id": session.conversation_id,
        "request_id": request_id, "page_size": 20, "detail_paging": True,
    }


def _visible_titles(event: dict) -> set[str]:
    return {
        row["title"]
        for group in event["groups"]
        for row in group["items"]
    }


def test_ws_result_view_overview_maturity_restart_and_current_session_recall(tmp_path, monkeypatch):
    """Real server handlers over temporary sidecars; only the monotonic clock is synthetic."""
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    paths.workspace.mkdir(parents=True)
    session_id = str(uuid.uuid4())
    session_dir = paths.data / "sessions" / session_id
    session_dir.mkdir(parents=True)
    session = Session(session_id, session_dir, "synthetic lifecycle", SessionMeta(), [], paths)
    session.save()
    store = WorkRecordStore(session)

    viewed, viewed_req, viewed_result = _new_completed_item(store, "Mature viewed synthetic task")
    late_viewed, late_req, late_result = _new_completed_item(store, "Restarted viewed synthetic task")
    unread, unread_req, unread_result = _new_completed_item(store, "Never viewed synthetic task")
    card_ids = {
        item["item_id"]: _publish_result(session, store, item)
        for item in (viewed, late_viewed, unread)
    }

    now = [_sample(_BOOT_A, 5 * _NS, _WALL_A)]
    monkeypatch.setattr(visibility_clock, "sample_elapsed_clock", lambda: now[0])
    events: list[dict] = []
    bridge = server.WsBridge(emit=events.append, paths=paths)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    handler = server.WsSessionHandler(paths)

    async def dispatch(target_handler, target_repl, request):
        await target_handler._handle_incoming(request, target_repl, bridge)
        return next(
            event for event in reversed(events)
            if event.get("request_id") == request["request_id"]
            and event.get("type") in {"work-card.result.view.done", "work-records.overview.result"}
        )

    def result_view(item, requirement, result_id, *, request_id):
        card_id, presentation_id = card_ids[item["item_id"]]
        return {
            "type": "work-card.result.view", "request_id": request_id,
            "session_id": session_id, "presentation_id": presentation_id,
            "card_id": card_id, "result_id": result_id,
            "requirement_id": requirement["requirement_id"],
        }

    async def exercise_server_chain():
        ack = await dispatch(handler, repl, result_view(
            viewed, viewed_req, viewed_result, request_id=str(uuid.uuid4()),
        ))
        assert ack["status"] == "acknowledged"

        now[0] = _sample(_BOOT_A, 5 * _NS + 29 * 60 * _NS, _WALL_A + timedelta(minutes=29))
        at_29 = await dispatch(handler, repl, _overview_request(session, str(uuid.uuid4())))
        assert at_29["state"] == "visible"
        assert {"Mature viewed synthetic task", "Never viewed synthetic task"} <= _visible_titles(at_29)
        assert at_29["visibility_recheck_after_ms"] == 10 * 60 * 1000
        unread_row = next(
            result for group in at_29["groups"] for row in group["items"]
            if row["item_id"] == unread["item_id"] for result in row["current_results"]
            if result["result_id"] == unread_result
        )
        assert unread_row["read_status"] == "unread"

        # Start a second item's clock at the first item's 30-minute boundary;
        # it must remain visible through restart and clock anomalies.
        now[0] = _sample(_BOOT_A, 5 * _NS + _WAIT, _WALL_A + timedelta(minutes=30))
        late_ack = await dispatch(handler, repl, result_view(
            late_viewed, late_req, late_result, request_id=str(uuid.uuid4()),
        ))
        assert late_ack["status"] == "acknowledged"
        at_30 = await dispatch(handler, repl, _overview_request(session, str(uuid.uuid4())))
        assert at_30["state"] == "visible"
        assert "Mature viewed synthetic task" not in _visible_titles(at_30)
        assert {"Restarted viewed synthetic task", "Never viewed synthetic task"} <= _visible_titles(at_30)
        assert at_30["visibility_counts"]["collapsed_groups"] == 1

        # Projection only changes what the overview presents; neither records nor cards are deleted.
        persisted_store = WorkRecordStore(session)
        assert {row["item_id"] for row in persisted_store.list_items()} == {
            viewed["item_id"], late_viewed["item_id"], unread["item_id"],
        }
        assert len(WorkCardLedger(session, persisted_store).list_cards()) == 3
        return at_30

    asyncio.run(exercise_server_chain())

    # Simulate application restart by reopening only the isolated Session directory.
    reopened = load_session_for_harness(paths, session_id, expected="desktop", full_messages=True)
    reopened_store = WorkRecordStore(reopened)
    bridge = server.WsBridge(emit=events.append, paths=paths)
    repl = SimpleNamespace(session=reopened, agent=SimpleNamespace(session=reopened))
    handler = server.WsSessionHandler(paths)

    # Reboot + extreme forward wall-clock shift cannot count the unobserved gap.
    _boot_b_start = 2 * _NS
    _wall_b_start = _WALL_A + timedelta(days=4000)
    now[0] = _sample(_BOOT_B, _boot_b_start, _wall_b_start)
    after_restart = asyncio.run(dispatch(handler, repl, _overview_request(reopened, str(uuid.uuid4()))))
    assert after_restart["state"] == "visible"
    assert "Mature viewed synthetic task" not in _visible_titles(after_restart)
    assert {"Restarted viewed synthetic task", "Never viewed synthetic task"} <= _visible_titles(after_restart)
    evidence_path = reopened.session_dir / "work-card-presentations.v1.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    late_view = next(
        view for row in evidence["items"] if row["card_id"] == card_ids[late_viewed["item_id"]][0]
        for view in row["result_views"] if view["result_id"] == late_result
    )
    assert late_view["elapsed_evidence"]["checkpoint_boot_id"] == _BOOT_B
    assert late_view["elapsed_evidence"]["accrued_ns"] == 0

    # A wall-clock rollback during the new boot invalidates that interval; it cannot
    # hide the item. Only a fresh, internally consistent 30-minute interval counts.
    anomaly_wall = _wall_b_start - timedelta(days=2)
    now[0] = _sample(_BOOT_B, _boot_b_start + 29 * 60 * _NS, anomaly_wall)
    after_anomaly = asyncio.run(dispatch(handler, repl, _overview_request(reopened, str(uuid.uuid4()))))
    assert after_anomaly["state"] == "visible"
    assert "Restarted viewed synthetic task" in _visible_titles(after_anomaly)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    late_view = next(
        view for row in evidence["items"] if row["card_id"] == card_ids[late_viewed["item_id"]][0]
        for view in row["result_views"] if view["result_id"] == late_result
    )
    assert late_view["elapsed_evidence"]["accrued_ns"] == 0
    assert late_view["elapsed_evidence"]["checkpoint_monotonic_ns"] == _boot_b_start + 29 * 60 * _NS

    now[0] = _sample(_BOOT_B, _boot_b_start + 58 * 60 * _NS, anomaly_wall + timedelta(minutes=29))
    at_new_boot_29 = asyncio.run(dispatch(handler, repl, _overview_request(reopened, str(uuid.uuid4()))))
    assert "Restarted viewed synthetic task" in _visible_titles(at_new_boot_29)
    now[0] = _sample(_BOOT_B, _boot_b_start + 59 * 60 * _NS, anomaly_wall + timedelta(minutes=30))
    at_new_boot_30 = asyncio.run(dispatch(handler, repl, _overview_request(reopened, str(uuid.uuid4()))))
    assert "Restarted viewed synthetic task" not in _visible_titles(at_new_boot_30)
    assert "Never viewed synthetic task" in _visible_titles(at_new_boot_30)
    assert {row["item_id"] for row in reopened_store.list_items()} == {
        viewed["item_id"], late_viewed["item_id"], unread["item_id"],
    }

    # Conversational recall reads the exact same current Session store, not the
    # visibility-filtered overview. The fake matcher picks the matured/hidden item.
    matcher_calls = []

    class FakeRecallClient:
        def chat(self, messages, **_kwargs):
            payload = json.loads(messages[1]["content"])
            matcher_calls.append(payload)
            target = next(candidate for candidate in payload["candidates"]
                          if candidate["title"] == "Mature viewed synthetic task")
            return SimpleNamespace(content=json.dumps({
                "status": "match", "candidate_ids": [target["id"]],
            }), tool_calls=[])

    monkeypatch.setattr(luna_recall, "get_registry", lambda *_args, **_kwargs: SimpleNamespace(
        get=lambda model_id: SimpleNamespace(
            id=model_id, vendor="0x567", provider_model="gpt-6-luna", max_input_tokens=100_000,
        ),
    ))
    recall_agent = Agent.create(
        reopened,
        llm=SimpleNamespace(chat=lambda *_args, **_kwargs: LLMResponse(
            model="synthetic", content="unused", tool_calls=[], finish_reason="stop", usage=None, raw={},
        )),
        confirm_fn=lambda *_args: "y",
        enable_work_record_recall=True,
    )
    recall_client = FakeRecallClient()
    recall_client.paths = paths
    matcher = WorkRecordsLunaRecall(recall_client)
    recall_agent.work_record_recall_controller = WorkRecordExperimentController(
        agent=recall_agent, store=reopened_store,
        recognizer=lambda *_args: {"action": "none"}, semantic_matcher=matcher,
    )
    question = "查一下之前那条工作记录说了什么"
    recalled = recall_agent.run_turn(question, spawn_explore=False)
    assert recalled.finish_reason == "work_record_recall"
    assert matcher_calls and "Mature viewed synthetic task" in recalled.assistant_text
    assert "Requirement for Mature viewed synthetic task" in recalled.assistant_text


