from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import server
from server import WsBridge, WsSessionHandler
from session import Session
from tests.test_work_card_ledger import make_session
from work_card_ledger import WorkCardLedger


def _save_card(session, store):
    from work_record_cards import build_project_work_record_cards
    ledger = WorkCardLedger(session, store)
    snapshot = build_project_work_record_cards(store, "synthetic-project")
    saved = ledger.save_snapshot(str(uuid.uuid4()), snapshot)
    assert saved.status == "saved"
    return saved.card_id


def latest(events, event_type):
    return next((event for event in reversed(events) if event.get("type") == event_type), None)


def test_protocol_trash_and_purge_require_explicit_confirm(tmp_path):
    async def _run():
        session, store, _ = make_session(tmp_path)
        card_id = _save_card(session, store)
        events: list[dict] = []
        bridge = WsBridge(emit=events.append, paths=session.paths)
        repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
        handler = WsSessionHandler(session.paths)
        trash_request = str(uuid.uuid4())
        await handler._dispatch_work_card_protocol(
            {"type": "work-card.trash", "request_id": trash_request, "card_id": card_id},
            repl, bridge,
        )
        trash_done = latest(events, "work-card.trash.done")
        assert trash_done["status"] == "trashed"
        list_request = str(uuid.uuid4())
        await handler._dispatch_work_card_protocol(
            {"type": "work-card.list", "request_id": list_request},
            repl, bridge,
        )
        list_done = latest(events, "work-card.list.done")
        assert list_done["status"] == "ok"
        assert list_done["items"] == []
        purge_without = str(uuid.uuid4())
        await handler._dispatch_work_card_protocol(
            {"type": "work-card.purge", "request_id": purge_without, "card_id": card_id},
            repl, bridge,
        )
        purge_fail = latest(events, "work-card.purge.done")
        assert purge_fail["status"] == "invalid_request"
        purge_request = str(uuid.uuid4())
        await handler._dispatch_work_card_protocol(
            {
                "type": "work-card.purge", "request_id": purge_request,
                "card_id": card_id, "confirm_permanent": True,
            },
            repl, bridge,
        )
        purge_done = latest(events, "work-card.purge.done")
        assert purge_done["status"] == "purged"

    asyncio.run(_run())
