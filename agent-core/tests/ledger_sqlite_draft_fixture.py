"""Test-only SQLite draft. It deliberately cannot activate or serve writes.

Not a migration implementation: legacy writers do not honor a migration gate.
All paths are restricted to a newly-created temporary fixture root.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from work_records import _file_lock


class MigrationUnavailable(RuntimeError):
    pass


class IsolatedLedgerDraft:
    def __init__(self, session, ledger, root):
        root = Path(root).resolve(strict=True)
        temp = Path(tempfile.gettempdir()).resolve(strict=True)
        if root == temp or not root.is_relative_to(temp):
            raise ValueError('draft root must be strictly inside system temp')
        path = Path(session.session_dir).resolve(strict=True)
        if not path.is_relative_to(root) or ledger._session is not session:
            raise ValueError('session and exact ledger must belong to draft root')
        self.session, self.ledger, self.root = session, ledger, root
        self.db = root / 'unpublished-ledger-draft.sqlite3'

    def prepare(self, *, after_snapshot=None, crash_phase=None):
        # This prototype refuses any existing draft, including an abandoned one.
        # No caller-supplied flag can make a draft authoritative.
        fd = os.open(self.db, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
        os.close(fd)
        with _file_lock(self.ledger._ledger_store._lock_path, self.ledger._ledger_store._lock_timeout):
            data = self.ledger._ledger_store._read()
            path = self.ledger._ledger_store._path
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400 or getattr(info, 'st_nlink', 1) != 1:
                raise MigrationUnavailable('unsafe legacy file')
            raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if after_snapshot is not None:
            after_snapshot()
        with sqlite3.connect(self.db) as db:
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE metadata (legacy_sha256 TEXT NOT NULL, state TEXT NOT NULL)')
            db.execute('CREATE TABLE cards (request_id TEXT PRIMARY KEY, card_id TEXT UNIQUE NOT NULL, record TEXT NOT NULL)')
            db.commit()
            db.execute('BEGIN IMMEDIATE')
            db.executemany('INSERT INTO cards VALUES (?, ?, ?)',
                [(r['request_id'], r['card_id'], json.dumps(r, ensure_ascii=False, sort_keys=True)) for r in data['items']])
            db.execute('INSERT INTO metadata VALUES (?, ?)', (digest, 'unpublished'))
            if crash_phase == 'before_commit':
                os._exit(73)
            # Check under the legacy lock. Even success is not a migration gate:
            # the old writer could write immediately after the lock is released.
            with _file_lock(self.ledger._ledger_store._lock_path, self.ledger._ledger_store._lock_timeout):
                if path.read_bytes() != raw:
                    raise MigrationUnavailable('legacy file changed during draft')
                db.commit()
            if crash_phase == 'after_commit':
                os._exit(74)
        return 'unpublished'

    def activate(self):
        raise MigrationUnavailable('legacy writer exclusion cannot be established; activation disabled')

    def save(self, *_args, **_kwargs):
        raise MigrationUnavailable('draft is not an authoritative ledger')
