from __future__ import annotations

import contextlib
import sqlite3
import stat
from pathlib import Path

from paths import AgentPaths

DB_NAME = "auto-recognition-preference.sqlite3"
_SCHEMA_VERSION = 1
_TABLE = "auto_recognition_preference"


class AutoRecognitionPreferenceError(RuntimeError):
    """The durable automatic-recognition preference is unavailable or invalid."""


class AutoRecognitionPreferenceStore:
    """Persist a single global opt-in; only a missing DB initializes enabled.

    Existing malformed, empty, or unknown-version storage fails closed. SQLite
    transactions serialize preference reads and writes across WebSocket bridges.
    """

    def __init__(self, paths: AgentPaths, *, timeout: float = 1.0) -> None:
        if type(timeout) not in (int, float) or not 0 < timeout <= 5:
            raise ValueError("preference database timeout is out of range")
        self.path = paths.data / DB_NAME
        self.timeout = float(timeout)

    def read_enabled(self) -> bool:
        return self._transaction(None)

    def write_enabled(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        self._transaction(enabled)

    def _transaction(self, update: bool | None) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            before = self._file_identity()
            connection = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
        except (OSError, sqlite3.Error) as exc:
            raise AutoRecognitionPreferenceError("preference database is unavailable") from exc
        try:
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            self._verify_identity(before)
            self._ensure_schema(connection, initialize=before is None)
            if update is not None:
                cursor = connection.execute(f"UPDATE {_TABLE} SET enabled=? WHERE singleton=1", (int(update),))
                if cursor.rowcount != 1:
                    raise AutoRecognitionPreferenceError("preference row is missing")
            rows = connection.execute(f"SELECT singleton, enabled FROM {_TABLE} ORDER BY singleton").fetchall()
            if (len(rows) != 1 or rows[0] != (1, rows[0][1])
                    or type(rows[0][1]) is not int or rows[0][1] not in (0, 1)):
                raise AutoRecognitionPreferenceError("preference row is invalid")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise AutoRecognitionPreferenceError("preference database integrity check failed")
            self._verify_identity(before)
            enabled = rows[0][1] == 1
            connection.commit()
            return enabled
        except AutoRecognitionPreferenceError:
            if connection.in_transaction:
                with contextlib.suppress(sqlite3.Error):
                    connection.rollback()
            raise
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            if connection.in_transaction:
                with contextlib.suppress(sqlite3.Error):
                    connection.rollback()
            raise AutoRecognitionPreferenceError("preference transaction failed") from exc
        finally:
            try:
                connection.close()
            except sqlite3.Error as exc:
                raise AutoRecognitionPreferenceError("preference database close failed") from exc

    def _file_identity(self) -> tuple[int, int] | None:
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise AutoRecognitionPreferenceError("preference path cannot be inspected") from exc
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise AutoRecognitionPreferenceError("preference database must be a regular single-link file")
        return info.st_dev, info.st_ino

    def _verify_identity(self, before: tuple[int, int] | None) -> None:
        after = self._file_identity()
        if after is None or (before is not None and before != after):
            raise AutoRecognitionPreferenceError("preference database identity changed")

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection, *, initialize: bool) -> None:
        version = connection.execute("PRAGMA user_version").fetchone()
        objects = connection.execute(
            "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        if version == (0,) and not objects and initialize:
            connection.execute(
                f"CREATE TABLE {_TABLE} (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                "enabled INTEGER NOT NULL CHECK(enabled IN (0,1)))"
            )
            connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            connection.execute(f"INSERT INTO {_TABLE}(singleton, enabled) VALUES(1, 1)")
            return
        if version != (_SCHEMA_VERSION,) or objects != [("table", _TABLE)]:
            raise AutoRecognitionPreferenceError("preference database schema is invalid")
        columns = connection.execute(f"PRAGMA table_info({_TABLE})").fetchall()
        expected = [
            (0, "singleton", "INTEGER", 0, None, 1),
            (1, "enabled", "INTEGER", 1, None, 0),
        ]
        if columns != expected:
            raise AutoRecognitionPreferenceError("preference table shape is invalid")
