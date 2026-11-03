import sys, uuid
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server
from test_linked_item_server_delivery import _setup
from test_linked_project_session_delivery import setup_copy


@pytest.mark.parametrize('kind', ['item', 'project'])
def test_queued_copy_rechecks_card_presentation_run_and_liveness(tmp_path, kind):
    if kind == 'item':
        parent, ns, rows, run, store, events, bridge, repl = _setup(tmp_path)
        assert server._resume_linked_item_copy_cards(repl, bridge, parent, store, binding_ids=[rows[0]['binding_id']], emit_event=True) == 1
        row = ns.get_binding(rows[0]['binding_id']); copy = row['card_copy']
        method = ns.mark_item_copy_reference_queued
    else:
        parent, ns, rows, store, events, bridge, repl = setup_copy(tmp_path)
        assert server._resume_linked_project_session_copies(repl, bridge, parent, store, emit_event=True) == 1
        row = ns.get_binding(rows[0]['binding_id']); copy = row['project_session_copy']
        method = ns.mark_project_session_copy_reference_queued
        run = str(uuid.UUID(row['start_receipt']['parent_run_id']))
    event = next(e for e in events if e.get('type') == 'work-card.present')
    args = dict(binding_id=row['binding_id'], copy_id=copy['intent']['copy_id'],
                card_id=event['card_id'], presentation_id=event['presentation_id'], run_id=run)
    wrong = 'wc-' + '0'*64
    assert method(**dict(args, card_id=wrong, presentation_id=server._work_card_presentation_id(parent.conversation_id, run, wrong)), is_current=lambda: True) == 'rejected'
    assert method(**dict(args, run_id=str(uuid.uuid4())), is_current=lambda: True) == 'rejected'
    checks = iter([True, False])
    assert method(**args, is_current=lambda: next(checks)) == 'rejected'
    assert method(**args, is_current=lambda: True) == 'already_queued'
