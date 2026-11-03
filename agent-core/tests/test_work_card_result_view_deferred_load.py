from __future__ import annotations

import asyncio
import json
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import server
import session as session_module
from paths import AgentPaths
from session import Session, SessionMeta, create_new
from work_records import WorkRecordStore
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations
from work_record_cards import build_independent_work_record_card


def _deferred_result_view_case(tmp_path: Path):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    paths.workspace.mkdir(parents=True)
    session = create_new(paths, conversation_id="20261030-aabbccdd")
    store = WorkRecordStore(session)
    item = store.create_item(title="synthetic result", scope={"kind": "independent"})
    requirement = store.add_requirement(item["item_id"], "synthetic requirement", event_id="req-event")
    run_id = str(uuid.uuid4())
    result_id = str(uuid.uuid4())
    store.begin_requirement_run(item["item_id"], requirement["requirement_id"],
                               run_id=run_id, expected_current_run_id=None)
    store.record_result_if_current_run(
        item["item_id"], requirement["requirement_id"], run_id=run_id,
        result_id=result_id, status="completed", summary="synthetic result summary",
        verification={"status": "not_verified"}, sources=[],
    )
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(str(uuid.uuid4()), build_independent_work_record_card(store, item["item_id"]))
    assert saved.status == "saved"
    presentation_id = str(uuid.uuid4())
    session.append_sent_work_card_reference(presentation_id, saved.card_id, run_id=run_id)
    presentations = WorkCardPresentations(session, store, ledger)
    assert presentations.create(presentation_id, saved.card_id, run_id=run_id).status == "created"
    session.save()

    deferred = Session.load(paths, session.conversation_id, message_cap=0)
    assert deferred._messages_fully_loaded is False
    bridge = server.WsBridge(emit=lambda _event: None, paths=paths)
    repl = server._build_repl(deferred, paths, bridge)
    handler = server.WsSessionHandler(paths)
    request = {
        "type": "work-card.result.view", "session_id": deferred.conversation_id,
        "presentation_id": presentation_id, "card_id": saved.card_id,
        "result_id": result_id, "requirement_id": requirement["requirement_id"],
    }
    return handler, repl, bridge, deferred, presentations, presentation_id, request


def test_deferred_history_result_view_loads_before_lock_and_records_exact_ack(tmp_path):
    async def run():
        handler, repl, bridge, deferred, presentations, presentation_id, request = _deferred_result_view_case(tmp_path)
        events = []
        bridge.emit = events.append

        await asyncio.wait_for(handler._handle_incoming(request, repl, bridge), timeout=8)

        assert deferred._messages_fully_loaded is True
        assert events[-1]["type"] == "work-card.result.view.done"
        assert events[-1]["status"] == "acknowledged"
        fact = presentations.get(presentation_id).fact
        assert [(view.result_id, view.requirement_id) for view in fact.result_views] == [
            (request["result_id"], request["requirement_id"]),
        ]

    asyncio.run(run())


def test_deferred_history_result_view_fails_closed_if_disk_history_changes_after_load(tmp_path, monkeypatch):
    async def run():
        handler, repl, bridge, deferred, presentations, presentation_id, request = _deferred_result_view_case(tmp_path)
        events = []
        bridge.emit = events.append
        original_lock = session_module._session_messages_lock
        entries = 0

        @contextmanager
        def mutate_history_between_load_and_recheck(session_dir):
            nonlocal entries
            entries += 1
            with original_lock(session_dir):
                if entries == 2:
                    with deferred.messages_path.open("a", encoding="utf-8", newline="\n") as stream:
                        stream.write(json.dumps({"role": "user", "content": "synthetic concurrent history"}) + "\n")
                yield

        monkeypatch.setattr(session_module, "_session_messages_lock", mutate_history_between_load_and_recheck)
        await asyncio.wait_for(handler._handle_incoming(request, repl, bridge), timeout=8)

        assert events[-1]["type"] == "work-card.result.view.done"
        assert events[-1]["status"] == "history_unavailable"
        assert presentations.get(presentation_id).fact.result_views == ()

    asyncio.run(run())
