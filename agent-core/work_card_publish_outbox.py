"""Session-bound view and conservative one-time migration for work-card intents."""
from __future__ import annotations

import copy
import hashlib
import logging
import json
import math
import re
import time
import uuid
from typing import Any

from project_mode import ProjectModeError, normalize_project_id
from work_records import (
    LEGACY_OUTBOX_DIGEST_KEY,
    LEGACY_OUTBOX_MIGRATIONS_KEY,
    WorkRecordCorrupt,
    WorkRecordConflict,
    WorkRecordScopeError,
    WorkRecordStore,
    _server_settlement_result_id,
    publication_card_request_id,
)

LEGACY_OUTBOX_FILENAME = "work-card-publish-outbox.v1.json"
_LEGACY_OUTBOX_KEYS = frozenset({"schema_version", "conversation_id", "project_id", "revision", "items"})
_LEGACY_INTENT_KEYS = frozenset({
    "intent_id", "project_id", "first_run_id", "latest_item_id", "latest_requirement_id",
    "latest_run_id", "generation", "request_id", "card_id", "state", "window_owner_id",
    "window_started_monotonic", "deadline_monotonic", "window_seconds",
})
_CARD_ID = re.compile(r"wc-[0-9a-f]{64}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_LOGGER = logging.getLogger(__name__)


class WorkCardPublishOutboxError(RuntimeError):
    """An atomic work-record publication intent cannot be safely read or updated."""


def _uuid_hex(value: Any, label: str) -> str:
    if type(value) is not str:
        raise WorkCardPublishOutboxError(f"{label} identity is invalid")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise WorkCardPublishOutboxError(f"{label} identity is invalid") from exc
    if parsed.hex != value.replace("-", "").lower():
        raise WorkCardPublishOutboxError(f"{label} identity is not canonical")
    return parsed.hex


def _legacy_digest(data: dict[str, Any]) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class _LegacyOutboxReader(WorkRecordStore):
    """Read-only strict reader for the previous separate outbox schema."""

    def __init__(self, session, source_store: WorkRecordStore) -> None:
        super().__init__(session, lock_timeout=source_store._lock_timeout)
        self._path = self._session_dir / LEGACY_OUTBOX_FILENAME
        self._lock_path = source_store._lock_path
        if (self._conversation_id != source_store._conversation_id
                or self._bound_project_id != source_store._bound_project_id):
            raise WorkCardPublishOutboxError("legacy outbox Session/project binding mismatch")

    def _has_source(self) -> bool:
        return self._path.exists() or self._path.is_symlink()

    def _read(self) -> dict[str, Any]:
        self._validate_scope()
        if not self._has_source():
            return {
                "schema_version": 1, "conversation_id": self._conversation_id,
                "project_id": self._bound_project_id, "revision": 0, "items": [],
            }
        return super()._read()

    def _read(self) -> dict[str, Any]:
        self._validate_scope()
        if not self._path.exists() and not self._path.is_symlink():
            return {
                "schema_version": 1, "conversation_id": self._conversation_id,
                "project_id": self._bound_project_id, "revision": 0, "items": [],
            }
        return super()._read()

    def _validate_payload(self, data: Any) -> None:
        if (type(data) is not dict or set(data) != _LEGACY_OUTBOX_KEYS
                or type(data.get("schema_version")) is not int or data["schema_version"] != 1
                or data.get("conversation_id") != self._conversation_id
                or data.get("project_id") != self._bound_project_id
                or type(data.get("revision")) is not int or data["revision"] < 0
                or type(data.get("items")) is not list or len(data["items"]) > 2):
            raise WorkRecordCorrupt("legacy outbox schema/Session/project binding is invalid")
        seen_ids: set[str] = set()
        seen_states: set[str] = set()
        for row in data["items"]:
            if type(row) is not dict or set(row) != _LEGACY_INTENT_KEYS:
                raise WorkRecordCorrupt("legacy outbox intent shape is invalid")
            try:
                intent_id = _uuid_hex(row.get("intent_id"), "legacy intent")
                _uuid_hex(row.get("first_run_id"), "legacy first run")
                _uuid_hex(row.get("latest_run_id"), "legacy latest run")
                _uuid_hex(row.get("latest_item_id"), "legacy item")
                _uuid_hex(row.get("latest_requirement_id"), "legacy requirement")
                owner_id = _uuid_hex(row.get("window_owner_id"), "legacy timer owner")
                canonical_request_id = str(uuid.UUID(row.get("request_id")))
            except (WorkCardPublishOutboxError, ValueError, TypeError, AttributeError) as exc:
                raise WorkRecordCorrupt("legacy outbox intent identity is invalid") from exc
            state = row.get("state")
            window = row.get("window_seconds")
            started = row.get("window_started_monotonic")
            deadline = row.get("deadline_monotonic")
            expected_card_id = "wc-" + hashlib.sha256(
                f"{self._conversation_id}\0{canonical_request_id}".encode("utf-8"),
            ).hexdigest()
            if (intent_id in seen_ids or state not in {"pending", "published"}
                    or state in seen_states or row.get("project_id") != self._bound_project_id
                    or canonical_request_id != row.get("request_id")
                    or canonical_request_id != publication_card_request_id(
                        self._conversation_id, self._bound_project_id,
                        row["intent_id"], row["generation"],
                    )
                    or type(row.get("generation")) is not int or row["generation"] < 1
                    or type(window) not in (int, float) or not math.isfinite(window)
                    or window <= 0 or window > 30.0
                    or type(started) not in (int, float) or not math.isfinite(started) or started < 0
                    or type(deadline) not in (int, float) or not math.isfinite(deadline)
                    or abs((deadline - started) - window) > 0.001
                    or (row.get("card_id") is not None and
                        (type(row["card_id"]) is not str or _CARD_ID.fullmatch(row["card_id"]) is None))
                    or (row.get("card_id") is not None and row["card_id"] != expected_card_id)
                    or (state == "published" and row.get("card_id") is None)):
                raise WorkRecordCorrupt("legacy outbox intent is inconsistent")
            seen_ids.add(intent_id)
            seen_states.add(state)


_PROJECT_SCOPED_KEY = "project_scoped_card_intents"
_PROJECT_SCOPED_FOCUS_KEYS = frozenset({
    "latest_item_id", "latest_requirement_id", "latest_run_id", "latest_result_id",
})
_PROJECT_SCOPED_INTENT_KEYS = frozenset({
    "session_id", "project_id", "request_id", "generation", "state", "card_id",
    "window_seconds", "window_started_monotonic", "deadline_monotonic", "boot_marker",
    "source_result_ids",
})
_PROJECT_SCOPED_INTENT_KEYS_WITH_FOCUS = _PROJECT_SCOPED_INTENT_KEYS | _PROJECT_SCOPED_FOCUS_KEYS
_PROJECT_SCOPED_LEGACY_INTENT_KEYS = _PROJECT_SCOPED_INTENT_KEYS - {"source_result_ids"}
_PROJECT_SCOPED_LEGACY_INTENT_KEYS_WITH_FOCUS = _PROJECT_SCOPED_LEGACY_INTENT_KEYS | _PROJECT_SCOPED_FOCUS_KEYS
_PROJECT_SCOPED_RESULT_ID = re.compile(r"worker-result-[0-9a-f]{64}\Z")
_PROJECT_SCOPED_CARD_ID = re.compile(r"wc-[0-9a-f]{64}\Z")


class ProjectScopedCardOutbox:
    """Durable timer/card state for current-dialogue project-scoped projections.

    Rows are stored through WorkRecordStore's locked, backed-up atomic mutation path.
    They identify presentation work only; they never authorize project execution.
    """

    def __init__(self, session, store: WorkRecordStore) -> None:
        if type(store) is not WorkRecordStore or store._session is not session:
            raise WorkCardPublishOutboxError("project-scoped outbox requires the exact bound WorkRecordStore")
        self._session = session
        self._store = store
        self._session_id = store._conversation_id

    @staticmethod
    def _validate_rows(rows: Any, session_id: str) -> list[dict[str, Any]]:
        if type(rows) is not list or len(rows) > 4096:
            raise WorkCardPublishOutboxError("project-scoped intent collection is invalid")
        seen: set[tuple[str, int]] = set()
        latest_generation: dict[str, int] = {}
        for row in rows:
            if type(row) is not dict or frozenset(row) not in (
                    _PROJECT_SCOPED_INTENT_KEYS, _PROJECT_SCOPED_LEGACY_INTENT_KEYS,
                    _PROJECT_SCOPED_INTENT_KEYS_WITH_FOCUS,
                    _PROJECT_SCOPED_LEGACY_INTENT_KEYS_WITH_FOCUS,
            ):
                raise WorkCardPublishOutboxError("project-scoped intent shape is invalid")
            has_focus = _PROJECT_SCOPED_FOCUS_KEYS.issubset(row)
            row.setdefault("source_result_ids", [])
            project_id = row.get("project_id")
            try:
                if normalize_project_id(project_id) != project_id:
                    raise ValueError
            except (ProjectModeError, TypeError, ValueError) as exc:
                raise WorkCardPublishOutboxError("project-scoped intent project identity is invalid") from exc
            try:
                request_id = str(uuid.UUID(row.get("request_id")))
            except (TypeError, ValueError, AttributeError) as exc:
                raise WorkCardPublishOutboxError("project-scoped intent request identity is invalid") from exc
            window = row.get("window_seconds")
            started = row.get("window_started_monotonic")
            deadline = row.get("deadline_monotonic")
            marker = row.get("boot_marker")
            card_id = row.get("card_id")
            generation = row.get("generation")
            identity = (project_id, generation) if type(generation) is int else None
            if (row.get("session_id") != session_id or identity in seen
                    or request_id != row["request_id"]
                    or type(generation) is not int or generation < 1
                    or generation <= latest_generation.get(project_id, 0)
                    or row.get("state") not in {"pending", "card_saved", "reference_queued"}
                    or type(window) not in (int, float) or isinstance(window, bool)
                    or not math.isfinite(window) or window <= 0 or window > 30.0
                    or type(started) not in (int, float) or isinstance(started, bool)
                    or not math.isfinite(started) or started < 0
                    or type(deadline) not in (int, float) or isinstance(deadline, bool)
                    or not math.isfinite(deadline) or abs((deadline - started) - window) > 0.001
                    or type(marker) not in (int, float) or isinstance(marker, bool) or not math.isfinite(marker)
                    or (row.get("state") == "pending" and card_id is not None)
                    or (row.get("state") in {"card_saved", "reference_queued"}
                        and (type(card_id) is not str or _PROJECT_SCOPED_CARD_ID.fullmatch(card_id) is None))
                    or type(row.get("source_result_ids")) is not list
                    or len(row["source_result_ids"]) > 10_000
                    or any(type(value) is not str or _PROJECT_SCOPED_RESULT_ID.fullmatch(value) is None
                           for value in row["source_result_ids"])
                    or len(set(row["source_result_ids"])) != len(row["source_result_ids"])
                    or (has_focus and (
                        any(type(row.get(key)) is not str or not row[key]
                            for key in ("latest_item_id", "latest_requirement_id", "latest_run_id"))
                        or type(row.get("latest_result_id")) is not str
                        or _PROJECT_SCOPED_RESULT_ID.fullmatch(row["latest_result_id"]) is None
                        or row["latest_result_id"] not in row["source_result_ids"]
                    ))):
                raise WorkCardPublishOutboxError("project-scoped intent identity/window is inconsistent")
            seen.add(identity)
            latest_generation[project_id] = generation
        return rows

    def list_intents(self) -> list[dict[str, Any]]:
        def read(data):
            rows = self._validate_rows(data.get(_PROJECT_SCOPED_KEY, []), self._session_id)
            return copy.deepcopy(rows), False
        try:
            return self._store._mutate(read)
        except Exception as exc:
            if isinstance(exc, WorkCardPublishOutboxError):
                raise
            raise WorkCardPublishOutboxError("project-scoped intent storage cannot be read") from exc

    def record_update(
        self, project_id: str, request_id: str, *, window_seconds: float = 30.0,
        now_monotonic: float | None = None, now_wall: float | None = None,
    ) -> dict[str, Any]:
        try:
            if type(project_id) is not str or normalize_project_id(project_id) != project_id:
                raise WorkCardPublishOutboxError("project-scoped update identity is invalid")
        except ProjectModeError as exc:
            raise WorkCardPublishOutboxError("project-scoped update identity is invalid") from exc
        try:
            canonical_request_id = str(uuid.UUID(request_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise WorkCardPublishOutboxError("project-scoped request identity is invalid") from exc
        if (type(window_seconds) not in (int, float) or isinstance(window_seconds, bool)
                or not math.isfinite(window_seconds) or window_seconds <= 0 or window_seconds > 30.0):
            raise WorkCardPublishOutboxError("project-scoped window must be positive and at most 30 seconds")
        delay = float(window_seconds)
        mono = time.monotonic() if now_monotonic is None else now_monotonic
        wall = time.time() if now_wall is None else now_wall
        if (type(mono) not in (int, float) or isinstance(mono, bool) or not math.isfinite(mono) or mono < 0
                or type(wall) not in (int, float) or isinstance(wall, bool) or not math.isfinite(wall)):
            raise WorkCardPublishOutboxError("project-scoped clock sample is invalid")
        marker = float(wall) - float(mono)
        outcome: dict[str, Any] = {}
        def change(data):
            rows = self._validate_rows(data.setdefault(_PROJECT_SCOPED_KEY, []), self._session_id)
            project_rows = [entry for entry in rows if entry["project_id"] == project_id]
            matching = next((entry for entry in reversed(project_rows)
                             if entry["request_id"] == canonical_request_id), None)
            latest_project_intent = project_rows[-1] if project_rows else None
            if (latest_project_intent is not None
                    and _PROJECT_SCOPED_FOCUS_KEYS.issubset(latest_project_intent)
                    and latest_project_intent["request_id"] != canonical_request_id):
                outcome["row"] = copy.deepcopy(latest_project_intent)
                return latest_project_intent, False
            if matching is not None:
                outcome["row"] = copy.deepcopy(matching)
                return matching, False
            pending = next((entry for entry in reversed(project_rows)
                            if entry["state"] == "pending"), None)
            if pending is not None:
                pending["request_id"] = canonical_request_id
                outcome["row"] = copy.deepcopy(pending)
                return pending, True
            # reference_queued means the durable history/presentation retry record
            # exists, not that the client received it; history owns those retries.
            # Retire only those rows after allocating a strictly later generation.
            # Never overwrite an unqueued card_saved row.
            generation = max((entry["generation"] for entry in project_rows), default=0) + 1
            rows[:] = [entry for entry in rows
                       if not (entry["project_id"] == project_id and entry["state"] == "reference_queued")]
            if len(rows) >= 4096:
                raise WorkCardPublishOutboxError("project-scoped intent limit reached; refusing to discard pending cards")
            fresh = {
                "session_id": self._session_id, "project_id": project_id,
                "request_id": canonical_request_id, "generation": generation,
                "state": "pending", "card_id": None, "window_seconds": delay,
                "window_started_monotonic": float(mono),
                "deadline_monotonic": float(mono) + delay, "boot_marker": marker,
                "source_result_ids": [],
            }
            rows.append(fresh)
            outcome["row"] = copy.deepcopy(fresh)
            return fresh, True
        try:
            self._store._mutate(change)
            return outcome["row"]
        except Exception as exc:
            if isinstance(exc, WorkCardPublishOutboxError):
                raise
            raise WorkCardPublishOutboxError("project-scoped intent was not durably committed") from exc

    def remaining_seconds(
        self, intent: dict[str, Any], *, now_monotonic: float | None = None,
        now_wall: float | None = None,
    ) -> float:
        if type(intent) is not dict or intent not in self.list_intents():
            raise WorkCardPublishOutboxError("project-scoped intent changed before deadline check")
        mono = time.monotonic() if now_monotonic is None else now_monotonic
        wall = time.time() if now_wall is None else now_wall
        if (type(mono) not in (int, float) or isinstance(mono, bool) or not math.isfinite(mono) or mono < 0
                or type(wall) not in (int, float) or isinstance(wall, bool) or not math.isfinite(wall)):
            raise WorkCardPublishOutboxError("project-scoped clock sample is invalid")
        marker = float(wall) - float(mono)
        if mono < intent["window_started_monotonic"] or abs(marker - intent["boot_marker"]) > 2.0:
            outcome = {"updated": False}
            def rebase(data):
                rows = self._validate_rows(data.get(_PROJECT_SCOPED_KEY, []), self._session_id)
                current = next((row for row in rows if row["project_id"] == intent["project_id"]
                                and row["generation"] == intent["generation"]), None)
                if current != intent:
                    raise WorkCardPublishOutboxError("project-scoped intent changed during clock recovery")
                if current["state"] != "pending":
                    return current, False
                current["window_started_monotonic"] = float(mono)
                current["deadline_monotonic"] = float(mono) + float(current["window_seconds"])
                current["boot_marker"] = marker
                outcome["updated"] = True
                return current, True
            try:
                self._store._mutate(rebase)
            except Exception as exc:
                if isinstance(exc, WorkCardPublishOutboxError):
                    raise
                raise WorkCardPublishOutboxError("uncertain clock window could not be durably rebased") from exc
            return float(intent["window_seconds"])
        return max(0.0, min(float(intent["window_seconds"]), intent["deadline_monotonic"] - float(mono)))

    def mark_card_saved(self, *, project_id: str, generation: int, request_id: str, card_id: str) -> str:
        if (type(card_id) is not str or _PROJECT_SCOPED_CARD_ID.fullmatch(card_id) is None
                or type(generation) is not int or generation < 1
                or type(project_id) is not str or type(request_id) is not str):
            return "rejected"
        try:
            if str(uuid.UUID(request_id)) != request_id:
                return "rejected"
        except (TypeError, ValueError, AttributeError):
            return "rejected"
        expected = "wc-" + hashlib.sha256(
            f"{self._session_id}\0{request_id}".encode("utf-8"),
        ).hexdigest()
        if expected != card_id:
            return "rejected"
        outcome = {"status": "missing"}
        def change(data):
            rows = self._validate_rows(data.get(_PROJECT_SCOPED_KEY, []), self._session_id)
            row = next((entry for entry in rows if entry["project_id"] == project_id
                        and entry["generation"] == generation), None)
            if row is None:
                return None, False
            if row["generation"] != generation or row["request_id"] != request_id:
                outcome["status"] = "stale"
                return row, False
            if row["state"] == "card_saved":
                outcome["status"] = "already_saved" if row["card_id"] == card_id else "conflict"
                return row, False
            row["state"] = "card_saved"
            row["card_id"] = card_id
            outcome["status"] = "saved"
            return row, True
        try:
            self._store._mutate(change)
        except Exception:
            return "uncertain"
        return outcome["status"]


    def mark_reference_queued(self, *, project_id: str, generation: int, request_id: str, card_id: str) -> str:
        if (type(project_id) is not str or type(request_id) is not str or type(generation) is not int
                or generation < 1 or type(card_id) is not str
                or _PROJECT_SCOPED_CARD_ID.fullmatch(card_id) is None):
            return "rejected"
        outcome = {"status": "missing"}
        def change(data):
            rows = self._validate_rows(data.get(_PROJECT_SCOPED_KEY, []), self._session_id)
            row = next((entry for entry in rows if entry["project_id"] == project_id
                        and entry["generation"] == generation), None)
            if row is None:
                return None, False
            if row["request_id"] != request_id or row["card_id"] != card_id:
                outcome["status"] = "stale"
                return row, False
            if row["state"] == "reference_queued":
                outcome["status"] = "already_queued"
                return row, False
            if row["state"] != "card_saved":
                outcome["status"] = "not_saved"
                return row, False
            row["state"] = "reference_queued"
            outcome["status"] = "queued"
            return row, True
        try:
            self._store._mutate(change)
        except Exception:
            return "uncertain"
        return outcome["status"]


class WorkCardPublicationOutbox:
    """Read/advance intents committed atomically with server-bound settlements."""

    def __init__(self, session, store: WorkRecordStore) -> None:
        if type(store) is not WorkRecordStore or store._session is not session:
            raise WorkCardPublishOutboxError("outbox requires the exact bound WorkRecordStore")
        self._session = session
        self._store = store
        self._session_id = store._conversation_id
        self._project_id = store._bound_project_id
        if not self._project_id:
            raise WorkCardPublishOutboxError("project-bound Session is required")
        try:
            if normalize_project_id(self._project_id) != self._project_id:
                raise WorkCardPublishOutboxError("project identity is not canonical")
        except ProjectModeError as exc:
            raise WorkCardPublishOutboxError("project identity is invalid") from exc

    def _migrate_legacy_locked(self, data: dict[str, Any]) -> bool:
        """Import only explicit, current server-settlement pending rows, without touching old bytes."""
        legacy = _LegacyOutboxReader(self._session, self._store)
        present = legacy._has_source()
        watermark = data.get(LEGACY_OUTBOX_DIGEST_KEY)
        if not present:
            if watermark is not None:
                raise WorkCardPublishOutboxError("legacy outbox disappeared after migration")
            return False
        source = legacy._read()  # Called by the shared work-record mutation while its OS lock is held.
        digest = _legacy_digest(source)
        if watermark is not None:
            if watermark != digest:
                raise WorkCardPublishOutboxError("legacy outbox changed after migration; refusing reconciliation")
            receipts = data.get(LEGACY_OUTBOX_MIGRATIONS_KEY, [])
            for old in source["items"]:
                if old["state"] != "pending":
                    continue
                receipt = next((row for row in receipts
                                if row["intent_id"] == old["intent_id"]
                                and row["source_digest"] == digest), None)
                if receipt is None:
                    raise WorkCardPublishOutboxError("legacy pending row has no stable migration receipt")
                if receipt["state"] == "pending":
                    migrated = next((row for row in data.get("publication_intents", [])
                                     if row["intent_id"] == old["intent_id"]
                                     and row.get("legacy_source_digest") == digest), None)
                    if migrated is None:
                        raise WorkCardPublishOutboxError("unpublished legacy migration lost its atomic intent")
            return False

        legacy_pending = [row for row in source["items"] if row["state"] == "pending"]
        existing = data.get("publication_intents", [])
        for old in legacy_pending:
            if any(row["state"] == "pending" for row in existing):
                raise WorkCardPublishOutboxError("legacy pending conflicts with an existing atomic pending intent")
            item_id = next((row["item_id"] for row in data["items"]
                            if _uuid_hex(row.get("item_id"), "source item") == old["latest_item_id"]), None)
            item = next((row for row in data["items"] if row.get("item_id") == item_id), None)
            if (type(item) is not dict or type(item.get("scope")) is not dict
                    or item["scope"].get("kind") != "project"
                    or item["scope"].get("project_id") != self._project_id):
                raise WorkCardPublishOutboxError("legacy pending item is not in the current project")
            requirement_id = next((row["requirement_id"] for row in item["requirements"]
                                   if _uuid_hex(row.get("requirement_id"), "source requirement")
                                   == old["latest_requirement_id"]), None)
            requirement = next((row for row in item["requirements"]
                                if row.get("requirement_id") == requirement_id), None)
            if (type(requirement) is not dict
                    or _uuid_hex(requirement.get("current_run_id"), "current run")
                    != old["latest_run_id"]):
                raise WorkCardPublishOutboxError("legacy pending run is no longer current")
            matches = [run for run in item["runs"]
                       if run["requirement_id"] == requirement_id
                       and _uuid_hex(run.get("run_id"), "source run") == old["latest_run_id"]]
            if len(matches) != 1:
                raise WorkCardPublishOutboxError("legacy pending settlement is missing or ambiguous")
            run = matches[0]
            host_execution = run.get("verification", {}).get("host_execution")
            expected_result = _server_settlement_result_id(
                self._session_id, item_id, requirement_id, run["run_id"],
            )
            if (run.get("status") != "unknown" or run.get("result_id") != expected_result
                    or type(host_execution) is not dict
                    or type(host_execution.get("sequence")) is not int
                    or host_execution["sequence"] < 1):
                raise WorkCardPublishOutboxError("legacy pending has no current server-bound settlement proof")
            already_published = next((row for row in existing
                                      if row["state"] == "published"
                                      and row["latest_item_id"] == item_id
                                      and row["latest_requirement_id"] == requirement_id
                                      and row["latest_run_id"] == run["run_id"]
                                      and row["result_id"] == run["result_id"]), None)
            if already_published is not None:
                receipts = data.setdefault(LEGACY_OUTBOX_MIGRATIONS_KEY, [])
                receipts.append({"intent_id": old["intent_id"],
                                 "source_digest": digest, "state": "published"})
                continue
            migrated = {
                "intent_id": old["intent_id"],
                "session_id": self._session_id,
                "project_id": self._project_id,
                "first_run_id": old["first_run_id"],
                "latest_item_id": item_id,
                "latest_requirement_id": requirement_id,
                "latest_run_id": run["run_id"],
                "result_id": run["result_id"],
                "generation": old["generation"],
                "request_id": old["request_id"],
                "card_id": old["card_id"],
                "state": "pending",
                "window_owner_id": old["window_owner_id"],
                "window_started_monotonic": old["window_started_monotonic"],
                "deadline_monotonic": old["deadline_monotonic"],
                "window_seconds": old["window_seconds"],
                "legacy_source_digest": digest,
            }
            if any(row["intent_id"] == migrated["intent_id"] for row in existing):
                raise WorkCardPublishOutboxError("legacy intent identity conflicts with the atomic source")
            existing.append(migrated)
            receipts = data.setdefault(LEGACY_OUTBOX_MIGRATIONS_KEY, [])
            receipts.append({"intent_id": migrated["intent_id"],
                             "source_digest": digest, "state": "pending"})
        if legacy_pending:
            data["publication_intents"] = existing
        data[LEGACY_OUTBOX_DIGEST_KEY] = digest
        # Re-run strict source+intent validation before the single atomic replace.
        self._store._validate_payload(data)
        return True

    def list_pending(self) -> list[dict[str, Any]]:
        try:
            def change(data):
                changed = self._migrate_legacy_locked(data)
                rows = copy.deepcopy([row for row in data.get("publication_intents", [])
                                      if row["state"] == "pending"])
                return rows, changed
            # Read legacy source, reconcile and return the canonical pending snapshot
            # under one lock; the old producer uses this same Session lock.
            return self._store._mutate(change)
        except Exception as exc:
            if isinstance(exc, WorkCardPublishOutboxError):
                category = str(exc)
                wrapped = exc
            elif isinstance(exc, WorkRecordCorrupt):
                category = "legacy_outbox_corrupt_or_binding_mismatch"
                wrapped = WorkCardPublishOutboxError(category)
            elif isinstance(exc, WorkRecordScopeError):
                category = "session_or_project_scope_mismatch"
                wrapped = WorkCardPublishOutboxError(category)
            elif isinstance(exc, WorkRecordConflict):
                category = "legacy_pending_conflicts_with_atomic_pending"
                wrapped = WorkCardPublishOutboxError(category)
            else:
                category = f"storage_{type(exc).__name__}"
                wrapped = WorkCardPublishOutboxError("legacy/atomic pending state cannot be confirmed")
            _LOGGER.warning("work-card outbox migration failed closed (%s)", category)
            if wrapped is exc:
                raise
            raise wrapped from exc

    def remaining_seconds(self, intent: dict[str, Any]) -> float:
        if (type(intent) is not dict or intent.get("session_id") != self._session_id
                or intent.get("project_id") != self._project_id):
            raise WorkCardPublishOutboxError("pending intent Session/project binding is invalid")
        try:
            return self._store.publication_intent_remaining_seconds(intent)
        except Exception as exc:
            raise WorkCardPublishOutboxError("pending intent deadline cannot be confirmed") from exc

    def validate_pending(self, intent: dict[str, Any]) -> bool:
        if (type(intent) is not dict or intent.get("session_id") != self._session_id
                or intent.get("project_id") != self._project_id):
            return False
        try:
            return self._store.validate_publication_intent(intent)
        except Exception:
            return False

    def bind_card(self, *, intent_id: str, generation: int, card_id: str) -> str:
        if type(intent_id) is not str or type(generation) is not int or generation < 1:
            return "rejected"
        try:
            return self._store.bind_publication_intent_card(
                intent_id=intent_id, generation=generation, card_id=card_id,
            )
        except Exception:
            return "uncertain"

    def clear_published(self, *, intent_id: str, generation: int, card_id: str) -> str:
        if type(intent_id) is not str or type(generation) is not int or generation < 1:
            return "rejected"
        try:
            return self._store.mark_publication_intent_enqueued(
                intent_id=intent_id, generation=generation, card_id=card_id,
            )
        except Exception:
            return "uncertain"

    def clear_matching_card(self, *, card_id: str, run_id: str) -> str:
        try:
            run_id = _uuid_hex(run_id, "run")
            pending = self.list_pending()
        except Exception:
            return "uncertain"
        row = next((item for item in pending
                    if item["card_id"] == card_id and uuid.UUID(item["latest_run_id"]).hex == run_id), None)
        if row is None:
            return "not_found"
        return self.clear_published(
            intent_id=row["intent_id"], generation=row["generation"], card_id=card_id,
        )

