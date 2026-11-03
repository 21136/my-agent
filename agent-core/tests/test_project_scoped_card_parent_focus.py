from __future__ import annotations

import copy
import json
import uuid
from dataclasses import asdict, replace
from pathlib import Path
import sys

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from server import _current_dialogue_card_parent_run_id
from work_card_project_scope import build_project_scoped_work_record_cards
from tests import test_work_card_project_scope as fixture


def _two_parent_snapshot(tmp_path, monkeypatch):
    session, store, item_id, _, _ = fixture._source_fixture(tmp_path)
    requirement = store.add_requirement(
        item_id, 'later synthetic task', event_id='later-focus-task',
        source_ref={'kind': 'project', 'id': 'synthetic-alpha'},
    )
    focused_parent = str(uuid.uuid4())
    monkeypatch.setattr(fixture, '_PARENT_RUN_ID', focused_parent)
    fixture._atomic_worker_result(session, store, item_id, requirement['requirement_id'])
    snapshot = build_project_scoped_work_record_cards(store, 'synthetic-alpha')
    assert snapshot.change_focus.requirement_id == requirement['requirement_id']
    return snapshot, focused_parent


@pytest.mark.parametrize('wire', [False, True])
def test_focused_result_selects_its_own_parent_and_legacy_keeps_first(tmp_path, monkeypatch, wire):
    snapshot, parent = _two_parent_snapshot(tmp_path, monkeypatch)
    selected = asdict(snapshot) if wire else snapshot
    assert _current_dialogue_card_parent_run_id(selected) == parent
    legacy = replace(snapshot, change_focus=None)
    assert _current_dialogue_card_parent_run_id(asdict(legacy) if wire else legacy) == 'c6b23429-fc35-4ded-aa0c-5873ab8f0a11'


@pytest.mark.parametrize('mutation', ['unknown_result', 'duplicate', 'wrong_project', 'wrong_source', 'wrong_run', 'bad_receipt', 'bad_focus'])
def test_focused_parent_never_falls_back_to_other_result(tmp_path, monkeypatch, mutation):
    snapshot, _ = _two_parent_snapshot(tmp_path, monkeypatch)
    value = asdict(snapshot)
    focus = value['change_focus']
    card = next(c for c in value['cards'] if c['item_id'] == focus['item_id'])
    req = next(r for r in card['requirements'] if r['requirement_id'] == focus['requirement_id'])
    result = req['latest_result']
    if mutation == 'unknown_result': focus['result_id'] = 'worker-result-' + 'a' * 64
    elif mutation == 'duplicate': card['requirements'] = (*card['requirements'], copy.deepcopy(req))
    elif mutation == 'wrong_project':
        receipt = json.loads(result['summary']); receipt['project_id'] = 'other-project'; result['summary'] = json.dumps(receipt)
    elif mutation == 'wrong_source': req['source_ref']['id'] = 'other-project'
    elif mutation == 'wrong_run': req['current_run_id'] = 'other-run'
    elif mutation == 'bad_receipt': result['summary'] = '{"parent_run_id":"broken"}'
    elif mutation == 'bad_focus': value['change_focus'] = {'item_id': focus['item_id']}
    assert _current_dialogue_card_parent_run_id(value) is None


def test_real_scoped_publication_uses_focused_parent(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from server import WsBridge, _publish_current_dialogue_project_work_card
    from work_card_publish_outbox import ProjectScopedCardOutbox
    _, expected_parent = _two_parent_snapshot(tmp_path, monkeypatch)
    # Reopen the same explicitly temporary source so the real publication path
    # rebuilds and validates its own immutable snapshot.
    # Obtain a fresh bound Session from the synthetic fixture root without user data.
    from paths import AgentPaths
    from session import Session, SessionMeta
    root = tmp_path / 'agent-root'
    paths = AgentPaths(root, root / 'workspace', root / 'data', root / 'evolve')
    session = Session(fixture._SESSION_ID, paths.data / 'sessions' / fixture._SESSION_ID, '', SessionMeta(project_id=''), [], paths)
    from work_records import WorkRecordStore
    store = WorkRecordStore(session)
    bridge = WsBridge(emit=lambda _event: None, paths=paths)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    monkeypatch.setattr(ProjectScopedCardOutbox, 'remaining_seconds', lambda *_args: 0.0)
    captured = []
    def capture(*args, **kwargs):
        captured.append(args)
        return False
    monkeypatch.setattr('server._emit_work_card_reference', capture)
    assert not _publish_current_dialogue_project_work_card(repl, bridge, session, store, 'synthetic-alpha')
    assert len(captured) == 1 and captured[0][5] == expected_parent
    assert ProjectScopedCardOutbox(session, store).list_intents()[0]['state'] == 'card_saved'
