from __future__ import annotations

import sys
import uuid
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from work_card_disposition import WorkCardDisposition, card_matches_project
from work_card_ledger import WorkCardLedger
from tests.test_work_card_ledger import make_session, REQUEST_ID


def _save_card(session, store, request_id: str | None = None):
    from work_record_cards import build_project_work_record_cards
    ledger = WorkCardLedger(session, store)
    snapshot = build_project_work_record_cards(store, "synthetic-project")
    rid = request_id or str(uuid.uuid4())
    saved = ledger.save_snapshot(rid, snapshot)
    assert saved.status == "saved"
    return ledger, saved.card_id


def test_soft_delete_hides_from_list_and_restore_recovers(tmp_path):
    session, store, _ = make_session(tmp_path)
    ledger, card_id = _save_card(session, store)
    assert len(ledger.list_visible_cards()) == 1
    disposition = WorkCardDisposition(session, store, ledger)
    trash_id = str(uuid.uuid4())
    result = disposition.soft_delete(trash_id, card_id=card_id)
    assert result.status == "trashed"
    assert ledger.get_card(card_id) is not None
    assert ledger.get_visible_card(card_id) is None
    assert ledger.list_visible_cards() == []
    restore_id = str(uuid.uuid4())
    restored = disposition.restore(restore_id, card_id=card_id)
    assert restored.status == "restored"
    assert ledger.get_visible_card(card_id) is not None


def test_hard_delete_requires_confirm_and_is_not_restorable(tmp_path):
    session, store, _ = make_session(tmp_path)
    ledger, card_id = _save_card(session, store)
    disposition = WorkCardDisposition(session, store, ledger)
    rejected = disposition.hard_delete(str(uuid.uuid4()), card_id=card_id, confirm_permanent=False)
    assert rejected.status == "rejected"
    assert rejected.reason == "confirm_permanent_required"
    purge_id = str(uuid.uuid4())
    purged = disposition.hard_delete(purge_id, card_id=card_id, confirm_permanent=True)
    assert purged.status == "purged"
    assert ledger.get_visible_card(card_id) is None
    restore = disposition.restore(str(uuid.uuid4()), card_id=card_id)
    assert restore.status == "rejected"
    assert restore.reason == "purged_not_restorable"


def test_ambiguous_delete_scope_rejected(tmp_path):
    session, store, _ = make_session(tmp_path)
    ledger, card_id = _save_card(session, store)
    disposition = WorkCardDisposition(session, store, ledger)
    result = disposition.soft_delete(
        str(uuid.uuid4()), card_id=card_id, project_id="synthetic-project",
    )
    assert result.status == "rejected"
    assert result.reason == "ambiguous_scope"


def test_project_cascade_soft_deletes_matching_cards(tmp_path):
    session, store, _ = make_session(tmp_path)
    ledger, _ = _save_card(session, store, REQUEST_ID)
    other_id = str(uuid.uuid4())
    _save_card(session, store, other_id)
    assert len(ledger.list_cards()) == 2
    disposition = WorkCardDisposition(session, store, ledger)
    cascade_id = str(uuid.uuid4())
    result = disposition.soft_delete(cascade_id, project_id="synthetic-project", include_bindings=False)
    assert result.status == "trashed"
    assert len(result.affected_card_ids) == 2
    assert ledger.list_visible_cards() == []


def test_card_matches_project_independent_false():
    record = {"card_id": "wc-" + "a" * 64, "snapshot": {"kind": "independent", "item_id": "item-1"}}
    assert card_matches_project(record, "synthetic-project") is False
