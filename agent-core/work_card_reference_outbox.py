"""Durable exact reference intents, using the work-record atomic mutation path.

An intent proves a trusted host attempted a specific presentation, not delivery.
It is committed before touching presentation storage and removed only after the
same presentation and history reference have both been durably established.
"""
from __future__ import annotations

import copy
import bisect
import re
import uuid
from typing import Any
from work_records import WorkRecordStore, WorkRecordCorrupt, _file_lock

KEY = 'work_card_reference_intents'
CURSOR_KEY = 'work_card_reference_scan_cursor'
MAX_INTENTS = 4096


def validate_reference_intents(data: dict[str, Any], session_id: str, project_id: str) -> list[dict[str, str]]:
    rows = data.get(KEY, [])
    if type(rows) is not list or len(rows) > MAX_INTENTS:
        raise WorkRecordCorrupt('reference intent collection invalid')
    cursor = data.get(CURSOR_KEY)
    if cursor is not None:
        try:
            if type(cursor) is not str or str(uuid.UUID(cursor)) != cursor:
                raise ValueError('noncanonical cursor')
        except (ValueError, TypeError, AttributeError) as exc:
            raise WorkRecordCorrupt('reference intent cursor invalid') from exc
    seen = set()
    for row in rows:
        if type(row) is not dict or set(row) != {'session_id', 'project_id', 'presentation_id', 'card_id', 'run_id'}:
            raise WorkRecordCorrupt('reference intent shape invalid')
        try:
            run = str(uuid.UUID(row['run_id']))
            pid = str(uuid.UUID(row['presentation_id']))
            expected = str(uuid.uuid5(uuid.NAMESPACE_URL, f"my-agent-work-card:{session_id}:{uuid.UUID(run).hex}:{row['card_id']}"))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise WorkRecordCorrupt('reference intent identity invalid') from exc
        if (row['session_id'] != session_id or row['project_id'] != project_id
                or run != row['run_id'] or pid != row['presentation_id'] or pid != expected
                or type(row['card_id']) is not str or re.fullmatch(r'wc-[0-9a-f]{64}', row['card_id']) is None
                or pid in seen):
            raise WorkRecordCorrupt('reference intent binding invalid')
        seen.add(pid)
    return rows


class WorkCardReferenceOutbox:
    def __init__(self, session, store, ledger):
        if type(store) is not WorkRecordStore or store._session is not session or ledger._session is not session or ledger._store is not store:
            raise ValueError('exact reference scope required')
        self.session, self.store, self.ledger = session, store, ledger

    def _check(self):
        self.ledger._validate_binding()
        if self.store._session is not self.session or self.ledger._store is not self.store:
            raise ValueError('reference scope changed')

    def list(self):
        self._check()
        with _file_lock(self.store._lock_path, self.store._lock_timeout):
            self._check()
            data = self.store._read()
            return copy.deepcopy(validate_reference_intents(data, self.session.conversation_id, self.store._bound_project_id))

    def claim_recovery_batch(self, limit):
        """Advance a durable circular cursor; failed rows remain in the outbox.

        Each call attempts at most limit rows. The snapshot is bounded by the
        validated MAX_INTENTS, and a crash after cursor advance cannot delete work.
        """
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError('invalid reference recovery batch limit')
        def change(data):
            self._check()
            rows = validate_reference_intents(data, self.session.conversation_id, self.store._bound_project_id)
            ordered = sorted(rows, key=lambda row: row['presentation_id'])
            if not ordered:
                return [], False
            ids = [row['presentation_id'] for row in ordered]
            cursor = data.get(CURSOR_KEY)
            start = bisect.bisect_right(ids, cursor) if cursor is not None else 0
            selected = [ordered[(start + offset) % len(ordered)]
                        for offset in range(min(limit, len(ordered)))]
            next_cursor = selected[-1]['presentation_id']
            changed = cursor != next_cursor
            if changed:
                data[CURSOR_KEY] = next_cursor
                self.store._validate_payload(data)
            return copy.deepcopy(selected), changed
        self._check()
        return self.store._mutate(change)

    def begin(self, presentation_id, card_id, run_id):
        row = {'session_id': self.session.conversation_id, 'project_id': self.store._bound_project_id,
               'presentation_id': presentation_id, 'card_id': card_id, 'run_id': run_id}
        def change(data):
            self._check()
            rows = validate_reference_intents(data, self.session.conversation_id, self.store._bound_project_id)
            cards = self.ledger._ledger_store._read()['items']
            if not any(item['card_id'] == card_id for item in cards):
                raise WorkRecordCorrupt('reference card missing')
            existing = next((item for item in rows if item['presentation_id'] == presentation_id), None)
            if existing is not None:
                if existing != row:
                    raise WorkRecordCorrupt('reference identity conflict')
                return None, False
            if len(rows) >= MAX_INTENTS:
                raise WorkRecordCorrupt('reference intent capacity reached')
            data.setdefault(KEY, []).append(row)
            validate_reference_intents(data, self.session.conversation_id, self.store._bound_project_id)
            self.store._validate_payload(data)
            return None, True
        self._check()
        self.store._mutate(change)

    def clear(self, presentation_id, card_id, run_id):
        def change(data):
            self._check()
            rows = validate_reference_intents(data, self.session.conversation_id, self.store._bound_project_id)
            found = next((item for item in rows if item['presentation_id'] == presentation_id), None)
            if found is None:
                return None, False
            if found['card_id'] != card_id or found['run_id'] != run_id:
                raise WorkRecordCorrupt('reference intent conflict')
            rows.remove(found)
            self.store._validate_payload(data)
            return None, True
        self._check()
        self.store._mutate(change)
