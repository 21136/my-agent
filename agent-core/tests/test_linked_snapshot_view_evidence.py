from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_linked_project_session_delivery import setup_copy
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations
from linked_snapshot_view_evidence import LinkedSnapshotViewEvidence
from work_record_visibility_clock import ElapsedClockSample
import work_record_visibility_clock as clock
import server


def sample(seconds, boot='boot-a', wall_seconds=None):
    wall = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds if wall_seconds is None else wall_seconds)
    return ElapsedClockSample(boot, seconds * 1_000_000_000, wall.isoformat().replace('+00:00', 'Z'))


def test_explicit_snapshot_view_and_elapsed_are_distinct_from_receipt(tmp_path, monkeypatch):
    parent, ns, rows, store, events, bridge, repl = setup_copy(tmp_path)
    assert server._resume_linked_project_session_copies(repl, bridge, parent, store, emit_event=True) == 1
    present = next(e for e in events if e.get('type') == 'work-card.present')
    ledger = WorkCardLedger(parent, store); p = LinkedSnapshotViewEvidence(parent, store, ledger)
    pid, card = present['presentation_id'], present['card_id']
    monkeypatch.setattr(clock, 'sample_elapsed_clock', lambda: sample(100))
    assert p.elapsed(pid, card).status == 'not_viewed'
    assert p.record_view(pid, card, explicit_user_action=False).status == 'rejected'
    assert p.elapsed(pid, card).status == 'not_viewed'
    assert p.record_view(pid, card, explicit_user_action=True).status == 'rejected'
    assert WorkCardPresentations(parent, store, p._ledger).acknowledge(pid, card, explicit_user_action=True).status == 'acknowledged'
    assert p.record_view(pid, card, explicit_user_action=True).status == 'acknowledged'
    monkeypatch.setattr(clock, 'sample_elapsed_clock', lambda: sample(1899))
    assert p.elapsed(pid, card).status == 'waiting'
    monkeypatch.setattr(clock, 'sample_elapsed_clock', lambda: sample(1900))
    assert p.elapsed(pid, card).status == 'elapsed'
    assert LinkedSnapshotViewEvidence(parent, store, ledger).elapsed(pid, card).status == 'elapsed'
    assert p.elapsed(pid, 'wc-'+'0'*64).status != 'elapsed'


def test_clock_reboot_and_wall_rollback_are_conservative(tmp_path, monkeypatch):
    parent, ns, rows, store, events, bridge, repl = setup_copy(tmp_path)
    server._resume_linked_project_session_copies(repl, bridge, parent, store, emit_event=True)
    event = next(e for e in events if e.get('type') == 'work-card.present')
    p = LinkedSnapshotViewEvidence(parent, store, WorkCardLedger(parent, store)); pid, card = event['presentation_id'], event['card_id']
    monkeypatch.setattr(clock, 'sample_elapsed_clock', lambda: sample(100))
    assert p.record_view(pid, card, explicit_user_action=True).status == 'rejected'
    assert WorkCardPresentations(parent, store, p._ledger).acknowledge(pid, card, explicit_user_action=True).status == 'acknowledged'
    assert p.record_view(pid, card, explicit_user_action=True).status == 'acknowledged'
    monkeypatch.setattr(clock, 'sample_elapsed_clock', lambda: sample(2000, wall_seconds=50))
    assert p.elapsed(pid, card).status != 'elapsed'
    monkeypatch.setattr(clock, 'sample_elapsed_clock', lambda: sample(10, boot='boot-b', wall_seconds=2001))
    assert p.elapsed(pid, card).status != 'elapsed'



def test_unavailable_clock_and_legacy_ack_never_invent_elapsed(tmp_path, monkeypatch):
    from work_card_presentations import WorkCardPresentations
    parent, ns, rows, store, events, bridge, repl = setup_copy(tmp_path)
    server._resume_linked_project_session_copies(repl, bridge, parent, store, emit_event=True)
    event = next(e for e in events if e.get('type') == 'work-card.present')
    ledger = WorkCardLedger(parent, store); pid, card = event['presentation_id'], event['card_id']
    monkeypatch.setattr(clock, 'sample_elapsed_clock', lambda: sample(100))
    assert WorkCardPresentations(parent, store, ledger).acknowledge(pid, card, explicit_user_action=True).status == 'acknowledged'
    p = LinkedSnapshotViewEvidence(parent, store, ledger)
    assert p.elapsed(pid, card).status == 'not_viewed'  # old ack did not record elapsed proof
    monkeypatch.setattr(clock, 'sample_elapsed_clock', lambda: None)
    assert p.record_view(pid, card, explicit_user_action=True).status == 'unavailable'
    monkeypatch.setattr(clock, 'sample_elapsed_clock', lambda: sample(5000))
    assert p.elapsed(pid, card).status == 'not_viewed'
    assert p.record_view(pid, card, explicit_user_action=True).status == 'acknowledged'
    assert p.elapsed(pid, card).status == 'waiting'


def test_persist_failure_never_reports_viewed_or_elapsed(tmp_path, monkeypatch):
    parent, ns, rows, store, events, bridge, repl = setup_copy(tmp_path)
    server._resume_linked_project_session_copies(repl, bridge, parent, store, emit_event=True)
    event = next(e for e in events if e.get('type') == 'work-card.present')
    p = LinkedSnapshotViewEvidence(parent, store, WorkCardLedger(parent, store))
    monkeypatch.setattr(clock, 'sample_elapsed_clock', lambda: sample(100))
    assert WorkCardPresentations(parent, store, p._ledger).acknowledge(event['presentation_id'], event['card_id'], explicit_user_action=True).status == 'acknowledged'
    monkeypatch.setattr(p._evidence, '_publish', lambda *a, **k: (_ for _ in ()).throw(OSError('disk failure')))
    assert p.record_view(event['presentation_id'], event['card_id'], explicit_user_action=True).status == 'unavailable'
    assert LinkedSnapshotViewEvidence(parent, store, WorkCardLedger(parent, store)).elapsed(event['presentation_id'], event['card_id']).status == 'not_viewed'



def test_legacy_missing_clock_evidence_requires_new_explicit_view_to_retry(tmp_path, monkeypatch):
    parent, ns, rows, store, events, bridge, repl = setup_copy(tmp_path)
    server._resume_linked_project_session_copies(repl, bridge, parent, store, emit_event=True)
    event = next(e for e in events if e.get('type') == 'work-card.present')
    pid, card = event['presentation_id'], event['card_id']
    ledger = WorkCardLedger(parent, store)
    p = LinkedSnapshotViewEvidence(parent, store, ledger)
    assert WorkCardPresentations(parent, store, ledger).acknowledge(pid, card, explicit_user_action=True).status == 'acknowledged'
    def add_legacy(data):
        data['items'].append({'presentation_id':pid, 'card_id':card, 'evidence':None})
        return None, True
    p._evidence._mutate(add_legacy)
    monkeypatch.setattr(clock, 'sample_elapsed_clock', lambda: None)
    assert p.record_view(pid, card, explicit_user_action=True).status == 'unavailable'
    monkeypatch.setattr(clock, 'sample_elapsed_clock', lambda: sample(200))
    assert p.elapsed(pid, card).status == 'unavailable'
    assert p.record_view(pid, card, explicit_user_action=True).status == 'acknowledged'
    assert p.elapsed(pid, card).status == 'waiting'
