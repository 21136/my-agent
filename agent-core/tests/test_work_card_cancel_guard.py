from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

_CORE = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_CORE))
sys.path.insert(0, str(_TESTS))

import server
from test_work_card_presentations import setup_case


def test_cancelled_active_run_cannot_enqueue_or_persist_card_reference(tmp_path):
    session, _store, _ledger, card_id, _other = setup_case(tmp_path)
    original_messages = list(session.messages)
    run_id = str(uuid.uuid4())
    presentation_id = server._work_card_presentation_id(
        session.conversation_id, run_id, card_id,
    )
    events = []
    bridge = server.WsBridge(emit=events.append, paths=session.paths)
    bridge.lifecycle = server.ExecutionLifecycle(run_id_factory=lambda: run_id)
    bridge.lifecycle.start_or_activate("ordinary")
    bridge.lifecycle_conversation_id = session.conversation_id
    bridge.cancel_event.set()
    repl = SimpleNamespace(
        session=session,
        agent=SimpleNamespace(session=session),
    )

    accepted = server._emit_work_card_reference(
        repl, bridge, session, presentation_id, card_id, run_id,
        active_run_id=run_id,
    )

    assert accepted is False
    assert session.messages == original_messages
    assert not (session.session_dir / "work-card-presentations.v1.json").exists()
    assert _ledger.get_card(card_id)["viewed_at"] is None
    assert not any(event.get("type") == "work-card.present" for event in events)



