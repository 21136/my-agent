"""Per-session durable presentation queue, not proof of WS delivery, display or elapsed time.

Trusted hosts allocate canonical UUIDs per presentation intent and reuse the same ID
for retries and reconnect recovery. Creation means only that the host recorded a
pending presentation intent. Only an authenticated, explicit user click may call acknowledge with
explicit_user_action=True. This module cannot authenticate a click itself. No
transaction spans this sidecar, the card ledger and chat message publication.

Instance ACK never implies result views. Only trusted host-attested actions for
an exact result represented in the immutable snapshot may create a result view.
The viewed_at timestamp is not trusted elapsed time. New result ACKs atomically bind monotonic uptime and boot identity; old ACKs remain readable but have no elapsed proof.
The host must not ACK merely because a card entered the viewport or was retrieved.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from session import Session
from work_records import (WorkRecordStore, WorkRecordCorrupt, _MAX_BYTES, _file_lock,
                          _file_identity, _require_private_regular_file)
import work_record_visibility_clock
from work_card_ledger import (WorkCardLedger, WorkCardLedgerCorrupt, _CARD_ID,
                              _canonical_request_id, _server_timestamp, _validate_timestamp)

PRESENTATIONS_FILENAME = 'work-card-presentations.v1.json'
_MAX_PRESENTATIONS = 10000
WORK_CARD_RECEIPT_RETRY_BATCH = 20
WORK_CARD_RECEIPT_MAX_SEND_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class ResultViewFact:
    result_id: str
    requirement_id: str
    viewed_at: str
    action: str
    elapsed_evidence: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PresentationFact:
    presentation_id: str
    card_id: str
    session_id: str
    created_at: str
    acknowledged_at: str | None
    result_views: tuple[ResultViewFact, ...] = ()
    run_id: str | None = None
    receipt_state: str = "legacy_untracked"
    receipt_attempts: int = 0
    last_offer_at: str | None = None
    received_at: str | None = None


@dataclass(frozen=True, slots=True)
class PresentationResult:
    status: str
    fact: PresentationFact | None = None
    reason: str = ''


def _pairs(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise WorkRecordCorrupt('duplicate keys')
        result[k] = v
    return result


def _valid_uuid(value: Any) -> bool:
    try:
        _canonical_request_id(value)
        return True
    except Exception:
        return False


def _valid_card(value: Any) -> bool:
    return type(value) is str and _CARD_ID.fullmatch(value) is not None


class _PresentationStore(WorkRecordStore):
    def __init__(self, session, source):
        super().__init__(session, lock_timeout=source._lock_timeout)
        self._path = self._session_dir / PRESENTATIONS_FILENAME
        self._lock_path = source._lock_path

    def _read(self):
        self._validate_scope()
        if not self._path.exists() and not self._path.is_symlink():
            return {'schema_version': 4, 'conversation_id': self._conversation_id,
                    'project_id': self._bound_project_id, 'revision': 0, 'items': []}
        try:
            before = self._path.lstat()
            _require_private_regular_file(before, error=WorkRecordCorrupt, label='presentations')
            if before.st_size > _MAX_BYTES:
                raise WorkRecordCorrupt('presentations oversized')
            fd = os.open(self._path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
            try:
                opened = os.fstat(fd)
                _require_private_regular_file(opened, error=WorkRecordCorrupt, label='presentations')
                if (_file_identity(before) != _file_identity(opened)
                        or _file_identity(opened) != _file_identity(self._path.lstat())
                        or opened.st_size > _MAX_BYTES):
                    raise WorkRecordCorrupt('presentations path changed')
                with os.fdopen(fd, 'rb') as stream:
                    fd = -1
                    raw = stream.read(_MAX_BYTES + 1)
                if len(raw) > _MAX_BYTES:
                    raise WorkRecordCorrupt('presentations oversized')
                data = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs)
            finally:
                if fd >= 0: os.close(fd)
            self._validate_payload(data)
            return data
        except WorkRecordCorrupt:
            raise
        except Exception as exc:
            raise WorkRecordCorrupt('presentations unreadable') from exc

    def _validate_payload(self, data):
        if (type(data) is not dict or set(data) != {'schema_version', 'conversation_id', 'project_id', 'revision', 'items'}
                or type(data['schema_version']) is not int or data['schema_version'] not in (1, 2, 3, 4)
                or data['conversation_id'] != self._conversation_id or data['project_id'] != self._bound_project_id
                or type(data['revision']) is not int or data['revision'] < 0
                or type(data['items']) is not list or len(data['items']) > _MAX_PRESENTATIONS):
            raise WorkRecordCorrupt('invalid presentation schema or scope')
        seen = set()
        for row in data['items']:
            keys = {'presentation_id', 'card_id', 'created_at', 'acknowledged_at'}
            if data['schema_version'] >= 2: keys.add('result_views')
            if data['schema_version'] >= 4: keys.update({'run_id', 'receipt_state', 'receipt_attempts', 'last_offer_at', 'received_at'})
            if (type(row) is not dict or set(row) != keys
                    or not _valid_uuid(row['presentation_id']) or not _valid_card(row['card_id'])
                    or row['presentation_id'] in seen):
                raise WorkRecordCorrupt('invalid presentation identity')
            seen.add(row['presentation_id'])
            try:
                _validate_timestamp(row['created_at'], 'created_at')
                if row['acknowledged_at'] is not None:
                    _validate_timestamp(row['acknowledged_at'], 'acknowledged_at')
            except Exception as exc:
                raise WorkRecordCorrupt('invalid timestamp') from exc
            if data['schema_version'] >= 2:
                views = row['result_views']
                if type(views) is not list or len(views) > 5000:
                    raise WorkRecordCorrupt('invalid result views')
                ids = set()
                for view in views:
                    view_keys = {'result_id', 'requirement_id', 'viewed_at', 'action'}
                    if data['schema_version'] >= 3: view_keys.add('elapsed_evidence')
                    if (type(view) is not dict or set(view) != view_keys
                            or type(view['result_id']) is not str or not view['result_id']
                            or len(view['result_id'].encode('utf-8')) > 256
                            or type(view['requirement_id']) is not str or not view['requirement_id']
                            or len(view['requirement_id'].encode('utf-8')) > 256
                            or view['result_id'] in ids or view['action'] not in ('result_detail', 'reliable_reply')):
                        raise WorkRecordCorrupt('invalid result view')
                    ids.add(view['result_id'])
                    try:
                        _validate_timestamp(view['viewed_at'], 'viewed_at')
                    except Exception as exc:
                        raise WorkRecordCorrupt('invalid result view time') from exc
                    if data['schema_version'] >= 3:
                        evidence = view['elapsed_evidence']
                        if evidence is not None:
                            evidence_keys = {'ack_boot_id', 'ack_monotonic_ns', 'ack_wall_utc', 'accrued_ns',
                                             'checkpoint_boot_id', 'checkpoint_monotonic_ns', 'checkpoint_wall_utc'}
                            if type(evidence) is not dict or set(evidence) != evidence_keys:
                                raise WorkRecordCorrupt('invalid elapsed evidence')
                            if (any(type(evidence[key]) is not str or not evidence[key]
                                    for key in ('ack_boot_id', 'checkpoint_boot_id', 'ack_wall_utc', 'checkpoint_wall_utc'))
                                    or any(type(evidence[key]) is not int or evidence[key] < 0
                                           for key in ('ack_monotonic_ns', 'accrued_ns', 'checkpoint_monotonic_ns'))):
                                raise WorkRecordCorrupt('invalid elapsed evidence values')
                            try:
                                _validate_timestamp(evidence['ack_wall_utc'], 'ack_wall_utc')
                                _validate_timestamp(evidence['checkpoint_wall_utc'], 'checkpoint_wall_utc')
                            except Exception as exc:
                                raise WorkRecordCorrupt('invalid elapsed evidence time') from exc
            if data['schema_version'] >= 4:
                run_id = row['run_id']
                state = row['receipt_state']
                attempts = row['receipt_attempts']
                last_offer = row['last_offer_at']
                received = row['received_at']
                if (state not in {'legacy_untracked', 'pending', 'received', 'deferred'}
                        or type(attempts) is not int or attempts < 0
                        or attempts > WORK_CARD_RECEIPT_MAX_SEND_ATTEMPTS
                        or (last_offer is not None and type(last_offer) is not str)
                        or (received is not None and type(received) is not str)):
                    raise WorkRecordCorrupt('invalid receipt state')
                if run_id is not None and (not _valid_uuid(run_id) or str(uuid.UUID(run_id)) != run_id):
                    raise WorkRecordCorrupt('invalid receipt run identity')
                try:
                    if last_offer is not None:
                        _validate_timestamp(last_offer, 'last_offer_at')
                    if received is not None:
                        _validate_timestamp(received, 'received_at')
                except Exception as exc:
                    raise WorkRecordCorrupt('invalid receipt timestamp') from exc
                if (state == 'legacy_untracked' and (run_id is not None or attempts != 0 or last_offer is not None or received is not None)
                        or state == 'pending' and (run_id is None or attempts >= WORK_CARD_RECEIPT_MAX_SEND_ATTEMPTS or received is not None)
                        or state == 'deferred' and (run_id is None or attempts != WORK_CARD_RECEIPT_MAX_SEND_ATTEMPTS or received is not None)
                        or state == 'received' and (run_id is None or received is None)
                        or (attempts == 0 and last_offer is not None)
                        or (attempts > 0 and last_offer is None)):
                    raise WorkRecordCorrupt('receipt fields conflict with receipt state')


class WorkCardPresentations:
    """Trusted-host-only operations, serialized with the ledger's OS lock.

    Instance and per-result ACKs are independent. The caller must authenticate
    each action and reliably map it to the exact result before calling.
    """
    def __init__(self, session: Session, store: WorkRecordStore, ledger: WorkCardLedger):
        if (type(session) is not Session or type(store) is not WorkRecordStore
                or type(ledger) is not WorkCardLedger or store._session is not session
                or ledger._session is not session or ledger._store is not store):
            raise ValueError('exact Session/store/ledger binding required')
        self._session, self._store, self._ledger = session, store, ledger
        self._presentations_store = _PresentationStore(session, store)
        self._validate_binding()

    def _validate_binding(self):
        self._ledger._validate_binding()
        self._presentations_store._validate_scope()
        if (self._store._session is not self._session or self._ledger._store is not self._store
                or self._presentations_store._session is not self._session
                or self._store._lock_path != self._ledger._session_dir / ".work-records.lock"
                or self._ledger._ledger_store._lock_path != self._store._lock_path
                or self._presentations_store._lock_path != self._store._lock_path
                or self._presentations_store._path != self._session.session_dir / PRESENTATIONS_FILENAME):
            raise ValueError('binding changed')

    def _fact(self, row):
        return PresentationFact(
            row['presentation_id'], row['card_id'], self._ledger._conversation_id,
            row['created_at'], row['acknowledged_at'],
            tuple(ResultViewFact(**v) for v in row.get('result_views', [])),
            row.get('run_id'), row.get('receipt_state', 'legacy_untracked'),
            row.get('receipt_attempts', 0), row.get('last_offer_at'), row.get('received_at'),
        )

    @staticmethod
    def _result_map(snapshot):
        if snapshot.get('kind') in {'linked_item_copy', 'linked_project_session_copy'}:
            return {}  # A copied item has no result identity or result-view ACK.
        cards = [snapshot['card']] if snapshot.get('kind') == 'independent' else snapshot['cards']
        results = {}
        for card in cards:
            for requirement in card['requirements']:
                result = requirement['latest_result']
                if result is not None:
                    results.setdefault(result['result_id'], []).append(requirement['requirement_id'])
        return results

    def _cards_locked(self, data):
        self._validate_binding()
        cards = {r['card_id']: r for r in self._ledger._ledger_store._read()['items']}
        for row in data['items']:
            card = cards.get(row['card_id'])
            if card is None:
                raise WorkRecordCorrupt('presentation card missing')
            identities = self._result_map(card['snapshot'])
            for view in row.get('result_views', []):
                if identities.get(view['result_id']) != [view['requirement_id']]:
                    raise WorkRecordCorrupt('result view does not bind to snapshot')
        return cards

    @staticmethod
    def _migrate(data):
        if data['schema_version'] == 1:
            for row in data['items']:
                row['result_views'] = []
            data['schema_version'] = 2
        if data['schema_version'] == 2:
            for row in data['items']:
                for view in row['result_views']:
                    view['elapsed_evidence'] = None
            data['schema_version'] = 3
        if data['schema_version'] == 3:
            # No historical record proves that the client received old presentations.
            # Keep them legacy/untracked; never auto-replay the full old history.
            for row in data['items']:
                row.update(run_id=None, receipt_state='legacy_untracked', receipt_attempts=0,
                           last_offer_at=None, received_at=None)
            data['schema_version'] = 4

    def create(self, presentation_id: str, card_id: str, *, run_id: str | None = None) -> PresentationResult:
        if (not _valid_uuid(presentation_id) or not _valid_card(card_id)
                or (run_id is not None and (not _valid_uuid(run_id) or str(uuid.UUID(run_id)) != run_id))):
            return PresentationResult('rejected', reason='invalid_identity')
        return self._change('create', presentation_id, card_id, run_id=run_id)

    def begin_receipt_tracking(self, presentation_id: str, card_id: str, run_id: str) -> PresentationResult:
        """Upgrade a legacy row only as the trusted host queues this exact run/card."""
        if (not _valid_uuid(presentation_id) or not _valid_card(card_id)
                or not _valid_uuid(run_id) or str(uuid.UUID(run_id)) != run_id):
            return PresentationResult('rejected', reason='invalid_identity')
        return self._change('track', presentation_id, card_id, run_id=run_id)

    def mark_transport_offer(self, presentation_id: str, card_id: str) -> PresentationResult:
        """Record one successful websocket.send call; this is not a renderer receipt."""
        if not _valid_uuid(presentation_id) or not _valid_card(card_id):
            return PresentationResult('rejected', reason='invalid_identity')
        return self._change('offer', presentation_id, card_id)

    def mark_received(self, presentation_id: str, card_id: str, session_id: str,
                      run_id: str) -> PresentationResult:
        """Persist a renderer receipt, strictly separate from user-view acknowledgement."""
        if (not _valid_uuid(presentation_id) or not _valid_card(card_id)
                or session_id != self._session.conversation_id
                or not _valid_uuid(run_id) or str(uuid.UUID(run_id)) != run_id):
            return PresentationResult('rejected', reason='invalid_identity_or_session')
        return self._change('received', presentation_id, card_id, run_id=run_id)

    def list_retryable_receipts(self, *, limit: int = WORK_CARD_RECEIPT_RETRY_BATCH,
                                exclude_ids: frozenset[str] = frozenset()) -> tuple[PresentationFact, ...]:
        if type(limit) is not int or not 1 <= limit <= WORK_CARD_RECEIPT_RETRY_BATCH:
            raise ValueError('receipt retry limit is invalid')
        if not isinstance(exclude_ids, frozenset):
            raise ValueError('receipt exclusions must be immutable')
        self._validate_binding()
        with _file_lock(self._presentations_store._lock_path, self._presentations_store._lock_timeout):
            self._validate_binding()
            data = self._presentations_store._read()
            self._cards_locked(data)
            if data['schema_version'] < 4:
                return ()
            rows = [row for row in data['items'] if row['receipt_state'] == 'pending'
                    and row['presentation_id'] not in exclude_ids
                    and row['receipt_attempts'] < WORK_CARD_RECEIPT_MAX_SEND_ATTEMPTS]
            rows.sort(key=lambda row: (row['receipt_attempts'], row['last_offer_at'] or row['created_at'], row['presentation_id']))
            return tuple(self._fact(row) for row in rows[:limit])

    def receipt_backlog_counts(self) -> dict[str, int]:
        self._validate_binding()
        with _file_lock(self._presentations_store._lock_path, self._presentations_store._lock_timeout):
            self._validate_binding()
            data = self._presentations_store._read()
            self._cards_locked(data)
            if data['schema_version'] < 4:
                return {'pending': 0, 'deferred': 0, 'legacy_untracked': len(data['items'])}
            return {
                'pending': sum(row['receipt_state'] == 'pending' for row in data['items']),
                'deferred': sum(row['receipt_state'] == 'deferred' for row in data['items']),
                'legacy_untracked': sum(row['receipt_state'] == 'legacy_untracked' for row in data['items']),
            }

    def acknowledge(self, presentation_id: str, card_id: str, *, explicit_user_action: bool) -> PresentationResult:
        if explicit_user_action is not True or not _valid_uuid(presentation_id) or not _valid_card(card_id):
            return PresentationResult('rejected', reason='explicit_action_or_identity_required')
        return self._change('ack', presentation_id, card_id)

    def acknowledge_result(self, presentation_id: str, card_id: str, result_id: str, *,
                           action: str, trusted_action: bool,
                           precommit_guard: Callable[[], bool] | None = None) -> PresentationResult:
        """Trusted host attests opening this result or a reliably mapped reply/question.

        This is not UI authentication; callers must verify the action and mapping.
        Only results actually present in this immutable snapshot are eligible.
        """
        if (not _valid_uuid(presentation_id) or not _valid_card(card_id)
                or type(result_id) is not str or not result_id or len(result_id) > 256
                or type(action) is not str or action not in ('result_detail', 'reliable_reply')
                or trusted_action is not True):
            return PresentationResult('rejected', reason='explicit_result_action_required')
        return self._change('result', presentation_id, card_id, result_id, action,
                            precommit_guard=precommit_guard)

    def _change(self, action, presentation_id, card_id, result_id=None, result_action=None, *,
                run_id=None, precommit_guard=None):
        try:
            self._validate_binding()
        except Exception:
            return PresentationResult('rejected', reason='invalid_scope')
        outcome = {}
        def change(data):
            self._validate_binding()
            if action == 'result' and precommit_guard is not None:
                try:
                    allowed = precommit_guard() is True
                except Exception:
                    allowed = False
                if not allowed:
                    outcome['result'] = PresentationResult('rejected', reason='precommit_guard_rejected')
                    return None, False
            cards = self._cards_locked(data)
            card_exists = card_id in cards
            self._migrate(data)
            found = next((r for r in data['items'] if r['presentation_id'] == presentation_id), None)
            if found is not None and found['card_id'] != card_id:
                outcome['result'] = PresentationResult('conflict', reason='identity_conflict')
                return None, False
            if not card_exists:
                outcome['result'] = PresentationResult('rejected', reason='unknown_card')
                return None, False
            if action == 'create':
                if found is not None:
                    if (run_id is not None and found.get('run_id') is not None
                            and found.get('run_id') != run_id):
                        outcome['result'] = PresentationResult('conflict', reason='run_identity_conflict')
                        return None, False
                    outcome['result'] = PresentationResult('already_created', self._fact(found))
                    return None, False
                if len(data['items']) >= _MAX_PRESENTATIONS:
                    outcome['result'] = PresentationResult('rejected', reason='capacity')
                    return None, False
                receipt_state = 'pending' if run_id is not None else 'legacy_untracked'
                found = {'presentation_id': presentation_id, 'card_id': card_id,
                         'created_at': _server_timestamp(), 'acknowledged_at': None, 'result_views': [],
                         'run_id': run_id, 'receipt_state': receipt_state, 'receipt_attempts': 0,
                         'last_offer_at': None, 'received_at': None}
                data['items'].append(found)
                outcome['result'] = PresentationResult('created', self._fact(found))
            elif found is None:
                outcome['result'] = PresentationResult('rejected', reason='unknown_presentation')
                return None, False
            elif action == 'offer':
                if found['receipt_state'] == 'legacy_untracked':
                    outcome['result'] = PresentationResult('untracked', self._fact(found))
                    return None, False
                if found['receipt_state'] == 'received':
                    outcome['result'] = PresentationResult('already_received', self._fact(found))
                    return None, False
                if found['receipt_state'] == 'deferred':
                    outcome['result'] = PresentationResult('deferred', self._fact(found))
                    return None, False
                if found['run_id'] is None or found['receipt_attempts'] >= WORK_CARD_RECEIPT_MAX_SEND_ATTEMPTS:
                    outcome['result'] = PresentationResult('rejected', reason='receipt_state_invalid')
                    return None, False
                found['receipt_attempts'] += 1
                found['last_offer_at'] = _server_timestamp()
                if found['receipt_attempts'] >= WORK_CARD_RECEIPT_MAX_SEND_ATTEMPTS:
                    found['receipt_state'] = 'deferred'
                    outcome['result'] = PresentationResult('deferred', self._fact(found))
                else:
                    outcome['result'] = PresentationResult('offered', self._fact(found))
            elif action == 'received':
                if found['receipt_state'] == 'received':
                    if found.get('run_id') != run_id:
                        outcome['result'] = PresentationResult('conflict', reason='receipt_run_conflict')
                    else:
                        outcome['result'] = PresentationResult('already_received', self._fact(found))
                    return None, False
                if found['receipt_state'] == 'legacy_untracked':
                    outcome['result'] = PresentationResult('untracked', self._fact(found))
                    return None, False
                if found.get('run_id') != run_id:
                    outcome['result'] = PresentationResult('conflict', reason='receipt_run_conflict')
                    return None, False
                if found['receipt_attempts'] < 1:
                    outcome['result'] = PresentationResult('offer_pending', self._fact(found))
                    return None, False
                found['receipt_state'] = 'received'
                found['received_at'] = _server_timestamp()
                outcome['result'] = PresentationResult('received', self._fact(found))
            elif action == 'track':
                if found['receipt_state'] == 'legacy_untracked':
                    found['run_id'] = run_id
                    found['receipt_state'] = 'pending'
                    found['receipt_attempts'] = 0
                    found['last_offer_at'] = None
                    found['received_at'] = None
                    outcome['result'] = PresentationResult('tracking', self._fact(found))
                elif found.get('run_id') != run_id:
                    outcome['result'] = PresentationResult('conflict', reason='receipt_run_conflict')
                    return None, False
                else:
                    outcome['result'] = PresentationResult('already_tracked', self._fact(found))
                    return None, False
            elif action == 'result':
                matches = self._result_map(cards[card_id]['snapshot']).get(result_id, [])
                if len(matches) != 1:
                    outcome['result'] = PresentationResult('rejected', reason='result_missing_or_ambiguous')
                    return None, False
                if any(v['result_id'] == result_id for v in found['result_views']):
                    outcome['result'] = PresentationResult('already_acknowledged', self._fact(found))
                    return None, False
                sample = work_record_visibility_clock.sample_elapsed_clock()
                evidence = None if sample is None else {
                    'ack_boot_id': sample.boot_id, 'ack_monotonic_ns': sample.monotonic_ns,
                    'ack_wall_utc': sample.wall_utc, 'accrued_ns': 0,
                    'checkpoint_boot_id': sample.boot_id, 'checkpoint_monotonic_ns': sample.monotonic_ns,
                    'checkpoint_wall_utc': sample.wall_utc,
                }
                found['result_views'].append({'result_id': result_id, 'requirement_id': matches[0],
                                              'viewed_at': _server_timestamp(), 'action': result_action,
                                              'elapsed_evidence': evidence})
                outcome['result'] = PresentationResult('acknowledged', self._fact(found))
            elif found['acknowledged_at'] is not None:
                outcome['result'] = PresentationResult('already_acknowledged', self._fact(found))
                return None, False
            else:
                found['acknowledged_at'] = _server_timestamp()
                outcome['result'] = PresentationResult('acknowledged', self._fact(found))
            self._presentations_store._validate_payload(data)
            return None, True
        try:
            self._presentations_store._mutate(change)
            self._validate_binding()
            return outcome.get('result', PresentationResult('uncertain', reason='commit_unknown'))
        except (WorkRecordCorrupt, WorkCardLedgerCorrupt):
            return PresentationResult('corrupt', reason='storage_invalid')
        except Exception:
            return PresentationResult('uncertain', reason='commit_unknown')

    def get(self, presentation_id: str) -> PresentationResult:
        if not _valid_uuid(presentation_id):
            return PresentationResult('rejected', reason='invalid_identity')
        try:
            self._validate_binding()
            with _file_lock(self._presentations_store._lock_path, self._presentations_store._lock_timeout):
                self._validate_binding()
                data = self._presentations_store._read()
                self._cards_locked(data)  # Validate every result fact against its immutable card.
                row = next ( (r for r in data['items'] if r['presentation_id'] == presentation_id), None)
                if row is None:
                    return PresentationResult('not_found')
                self._validate_binding()
                return PresentationResult('found', self._fact(row))
        except (WorkRecordCorrupt, WorkCardLedgerCorrupt):
            return PresentationResult('corrupt', reason='storage_invalid')
        except Exception:
            return PresentationResult('rejected', reason='read_unavailable')
