"""Host-only evidence that one historical linked snapshot was viewed and aged.

This is not a completion, verification, receipt or overview-collapse decision.
Callers must authenticate the explicit view, and separately retain attention items.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from work_records import WorkRecordStore, WorkRecordCorrupt
from work_card_ledger import WorkCardLedger, _validate_timestamp
from work_card_presentations import WorkCardPresentations, _valid_uuid, _valid_card
import work_record_visibility_clock as clock
from work_record_visibility_projection import _advance_evidence


@dataclass(frozen=True)
class SnapshotViewEvidenceResult:
    status: str


class _SnapshotEvidenceStore(WorkRecordStore):
    def __init__(self, session, store):
        super().__init__(session, lock_timeout=store._lock_timeout)
        self._path = self._session_dir / 'linked-snapshot-views.v1.json'
        self._lock_path = store._lock_path

    def _read(self):
        self._validate_scope()
        if not self._path.exists() and not self._path.is_symlink():
            return {'schema_version': 1, 'conversation_id': self._conversation_id,
                    'project_id': self._bound_project_id, 'revision': 0, 'items': []}
        return super()._read()

    def _validate_payload(self, data):
        if (type(data) is not dict or set(data) != {'schema_version', 'conversation_id', 'project_id', 'revision', 'items'}
                or data['schema_version'] != 1 or data['conversation_id'] != self._conversation_id
                or data['project_id'] != self._bound_project_id or type(data['revision']) is not int
                or data['revision'] < 0 or type(data['items']) is not list or len(data['items']) > 2000):
            raise WorkRecordCorrupt('invalid snapshot evidence scope')
        seen = set()
        for row in data['items']:
            if (type(row) is not dict or set(row) != {'presentation_id', 'card_id', 'evidence'}
                    or not _valid_uuid(row['presentation_id']) or not _valid_card(row['card_id'])
                    or row['presentation_id'] in seen):
                raise WorkRecordCorrupt('invalid snapshot evidence identity')
            seen.add(row['presentation_id'])
            e = row['evidence']
            if e is None:
                continue
            if (type(e) is not dict or set(e) != {'ack_boot_id', 'ack_monotonic_ns', 'ack_wall_utc', 'accrued_ns',
                    'checkpoint_boot_id', 'checkpoint_monotonic_ns', 'checkpoint_wall_utc'}
                    or any(type(e[k]) is not str or not e[k] for k in ('ack_boot_id', 'checkpoint_boot_id'))
                    or any(type(e[k]) is not int or e[k] < 0 for k in ('ack_monotonic_ns', 'checkpoint_monotonic_ns', 'accrued_ns'))):
                raise WorkRecordCorrupt('invalid snapshot clock evidence')
            try:
                _validate_timestamp(e['ack_wall_utc'], 'ack_wall_utc')
                _validate_timestamp(e['checkpoint_wall_utc'], 'checkpoint_wall_utc')
            except Exception as exc:
                raise WorkRecordCorrupt('invalid snapshot clock time') from exc


class LinkedSnapshotViewEvidence:
    def __init__(self, session, store, ledger):
        if (type(store) is not WorkRecordStore or type(ledger) is not WorkCardLedger
                or store._session is not session or ledger._session is not session or ledger._store is not store):
            raise ValueError('exact Session/store/ledger required')
        self._session, self._store, self._ledger = session, store, ledger
        self._presentations = WorkCardPresentations(session, store, ledger)
        self._evidence = _SnapshotEvidenceStore(session, store)

    def record_view(self, presentation_id: str, card_id: str, *, explicit_user_action: bool) -> SnapshotViewEvidenceResult:
        if explicit_user_action is not True:
            return SnapshotViewEvidenceResult('rejected')
        return self._change(presentation_id, card_id, record=True)

    def elapsed(self, presentation_id: str, card_id: str) -> SnapshotViewEvidenceResult:
        return self._change(presentation_id, card_id, record=False)

    def _change(self, presentation_id, card_id, *, record):
        if not _valid_uuid(presentation_id) or not _valid_card(card_id):
            return SnapshotViewEvidenceResult('rejected')
        outcome = {'status': 'unavailable'}
        def change(data):
            self._presentations._validate_binding()
            cards = self._ledger._ledger_store._read()['items']
            matches = [r for r in cards if r['card_id'] == card_id]
            presentations = self._presentations._presentations_store._read()['items']
            facts = [r for r in presentations if r['presentation_id'] == presentation_id]
            if (len(matches) != 1 or matches[0]['snapshot'].get('kind') not in {'linked_item_copy', 'linked_project_session_copy'}
                    or len(facts) != 1 or facts[0]['card_id'] != card_id):
                outcome['status'] = 'rejected'
                return None, False
            rows = [r for r in data['items'] if r['presentation_id'] == presentation_id]
            if rows and rows[0]['card_id'] != card_id:
                outcome['status'] = 'rejected'
                return None, False
            if record:
                # Receipt is not a view; a fresh host-attested action is required.
                if facts[0]['acknowledged_at'] is None:
                    outcome['status'] = 'rejected'
                    return None, False
                if rows and rows[0]['evidence'] is not None:
                    outcome['status'] = 'already_acknowledged'
                    return None, False
                sample = clock.sample_elapsed_clock()
                if sample is None:
                    outcome['status'] = 'unavailable'
                    return None, False
                evidence = {'ack_boot_id': sample.boot_id, 'ack_monotonic_ns': sample.monotonic_ns,
                    'ack_wall_utc': sample.wall_utc, 'accrued_ns': 0, 'checkpoint_boot_id': sample.boot_id,
                    'checkpoint_monotonic_ns': sample.monotonic_ns, 'checkpoint_wall_utc': sample.wall_utc}
                if rows:
                    rows[0]['evidence'] = evidence
                else:
                    data['items'].append({'presentation_id': presentation_id, 'card_id': card_id, 'evidence': evidence})
                self._evidence._validate_payload(data)
                outcome['status'] = 'acknowledged'
                return None, True
            if not rows:
                outcome['status'] = 'not_viewed'
                return None, False
            sample = clock.sample_elapsed_clock()
            if sample is None or rows[0]['evidence'] is None:
                outcome['status'] = 'unavailable'
                return None, False
            changed, mature = _advance_evidence(rows[0]['evidence'], sample)
            outcome['status'] = 'elapsed' if mature else 'waiting'
            return None, changed
        try:
            self._evidence._mutate(change)
            return SnapshotViewEvidenceResult(outcome['status'])
        except Exception:
            return SnapshotViewEvidenceResult('unavailable')

