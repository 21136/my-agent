from __future__ import annotations
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

_CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CORE))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server
from session import Session
from test_work_card_recall_agent import setup_case
from work_card_presentations import PresentationResult, WorkCardPresentations
from work_card_ledger import WorkCardLedger
from work_records import WorkRecordStore


def test_uncertain_after_commit_keeps_history_and_pending_recovery(tmp_path, monkeypatch):
    agent, session, store, ledger, card_id = setup_case(tmp_path, monkeypatch)
    run_id = str(uuid.uuid4())
    pid = server._work_card_presentation_id(session.conversation_id, run_id, card_id)
    original = WorkCardPresentations.create
    def commit_then_uncertain(self, *args, **kwargs):
        assert original(self, *args, **kwargs).status == 'created'
        return PresentationResult('uncertain')
    monkeypatch.setattr(WorkCardPresentations, 'create', commit_then_uncertain)
    events = []
    bridge = server.WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=agent)
    assert not server._emit_work_card_reference(repl, bridge, session, pid, card_id, run_id, active_run_id=None)
    assert not any(e.get('type') == 'work-card.present' for e in events)
    restored = Session.load(session.paths, session.conversation_id)
    assert len([r for r in restored.messages if r.get('presentation_id') == pid]) == 1
    monkeypatch.setattr(WorkCardPresentations, 'create', original)
    restored_repl = SimpleNamespace(session=restored, agent=SimpleNamespace(session=restored))
    replay = []
    assert server._emit_pending_work_card_references(restored_repl, server.WsBridge(emit=replay.append, paths=restored.paths)) == 1
    assert [e['presentation_id'] for e in replay if e.get('type') == 'work-card.present'] == [pid]
    reopened_store = WorkRecordStore(restored)
    fact = WorkCardPresentations(restored, reopened_store, WorkCardLedger(restored, reopened_store)).get(pid).fact
    assert fact.received_at is None


def test_uncertain_before_commit_preserves_history_but_warns_no_auto_recovery(tmp_path, monkeypatch):
    agent, session, _store, _ledger, card_id = setup_case(tmp_path, monkeypatch)
    run_id = str(uuid.uuid4())
    pid = server._work_card_presentation_id(session.conversation_id, run_id, card_id)
    monkeypatch.setattr(WorkCardPresentations, 'create', lambda *_a, **_k: PresentationResult('uncertain'))
    events = []
    bridge = server.WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=agent)
    assert not server._emit_work_card_reference(repl, bridge, session, pid, card_id, run_id, active_run_id=None)
    restored = Session.load(session.paths, session.conversation_id)
    assert len([r for r in restored.messages if r.get('presentation_id') == pid]) == 1
    assert any('自动恢复未能确认' in e.get('text', '') for e in events)
    replay = []
    restored_repl = SimpleNamespace(session=restored, agent=SimpleNamespace(session=restored))
    assert server._emit_pending_work_card_references(restored_repl, server.WsBridge(emit=replay.append, paths=restored.paths)) == 0
    assert not any(e.get('type') == 'work-card.present' for e in replay)


def test_uncertain_readback_unavailable_does_not_append_unverified_reference(tmp_path, monkeypatch):
    agent, session, _store, _ledger, card_id = setup_case(tmp_path, monkeypatch)
    run_id = str(uuid.uuid4())
    pid = server._work_card_presentation_id(session.conversation_id, run_id, card_id)
    monkeypatch.setattr(WorkCardPresentations, 'create', lambda *_a, **_k: PresentationResult('uncertain'))
    monkeypatch.setattr(WorkCardPresentations, 'get', lambda *_a, **_k: PresentationResult('corrupt'))
    events = []
    bridge = server.WsBridge(emit=events.append, paths=session.paths)
    assert not server._emit_work_card_reference(SimpleNamespace(session=session, agent=agent), bridge,
                                                session, pid, card_id, run_id, active_run_id=None)
    assert not any(r.get('presentation_id') == pid for r in session.messages)
    assert not any(e.get('type') == 'work-card.present' for e in events)
    assert any('自动恢复未能确认' in e.get('text', '') for e in events)


def test_reference_only_old_history_is_not_evidence_for_mass_resend(tmp_path, monkeypatch):
    agent, session, _store, _ledger, card_id = setup_case(tmp_path, monkeypatch)
    references = []
    for index in range(999):
        run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'old-reference-{index}'))
        references.append({'role': 'work_card_presentation', '_work_card_reference': 1,
                           'presentation_id': server._work_card_presentation_id(session.conversation_id, run_id, card_id),
                           'card_id': card_id, 'run_id': run_id})
    session.messages.extend(references)
    session.save()
    events = []
    bridge = server.WsBridge(emit=events.append, paths=session.paths)
    assert server._emit_pending_work_card_references(SimpleNamespace(session=session, agent=agent), bridge) == 0
    assert not any(e.get('type') == 'work-card.present' for e in events)
    assert [r['presentation_id'] for r in session.messages if r.get('_work_card_reference') == 1] == [r['presentation_id'] for r in references]
