"""Soft and hard disposition for immutable work-card ledger rows and linked bindings.

Ledger snapshots stay append-only; this sidecar only records visibility tombstones.
Soft delete is recoverable. Hard delete requires an explicit confirm_permanent flag.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal

from project_mode import ProjectModeError, normalize_project_id
from session import Session
from work_card_ledger import WorkCardLedger, _CARD_ID, _canonical_request_id, _server_timestamp, _validate_timestamp
from work_records import (
    WorkRecordCorrupt,
    WorkRecordError,
    WorkRecordStore,
    WorkRecordWriteError,
    _MAX_BYTES,
    _file_identity,
    _file_lock,
    _require_private_regular_file,
)

DISPOSITION_FILENAME = "work-card-disposition.v1.json"
_SCHEMA_VERSION = 1
_MAX_ENTRIES = 10_000
_BINDING_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REFRESH_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)
_ENTRY_KINDS = frozenset({"card", "binding", "refresh"})
_STATES = frozenset({"trashed", "purged"})


class WorkCardDispositionError(RuntimeError):
    """Disposition store unavailable or request invalid."""


@dataclass(frozen=True, slots=True)
class DispositionMutationResult:
    status: Literal[
        "trashed", "already_trashed", "restored", "already_active",
        "purged", "already_purged", "rejected", "failed", "uncertain",
    ]
    affected_card_ids: tuple[str, ...] = ()
    affected_binding_ids: tuple[str, ...] = ()
    affected_refresh_ids: tuple[str, ...] = ()
    reason: str = ""


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkRecordCorrupt("disposition JSON has duplicate keys")
        result[key] = value
    return result


def _valid_entry_id(kind: str, entry_id: str) -> bool:
    if kind == "card":
        return _CARD_ID.fullmatch(entry_id) is not None
    if kind == "binding":
        return _BINDING_ID.fullmatch(entry_id) is not None
    if kind == "refresh":
        return _REFRESH_ID.fullmatch(entry_id) is not None
    return False


def snapshot_project_id(snapshot: Any) -> str | None:
    if type(snapshot) is not dict:
        return None
    kind = snapshot.get("kind")
    if kind == "independent":
        return None
    if kind == "linked_item_copy":
        project_id = snapshot.get("project_id")
        return project_id if type(project_id) is str and project_id else None
    if kind == "linked_project_session_copy":
        source = snapshot.get("source")
        if type(source) is not dict:
            return None
        project_id = source.get("project_id")
        return project_id if type(project_id) is str and project_id else None
    project_id = snapshot.get("project_id")
    if type(project_id) is not str or not project_id:
        return None
    try:
        return normalize_project_id(project_id)
    except ProjectModeError:
        return None


def card_matches_project(record: dict[str, Any], project_id: str) -> bool:
    snapshot = record.get("snapshot")
    matched = snapshot_project_id(snapshot)
    if matched is None:
        return False
    try:
        return matched == normalize_project_id(project_id)
    except ProjectModeError:
        return False


class _DispositionStore(WorkRecordStore):
    def __init__(self, session: Session, source: WorkRecordStore) -> None:
        super().__init__(session, lock_timeout=source._lock_timeout)
        self._path = self._session_dir / DISPOSITION_FILENAME
        self._lock_path = source._lock_path

    def _empty(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "conversation_id": self._conversation_id,
            "project_id": self._bound_project_id,
            "revision": 0,
            "items": [],
            "receipts": [],
        }

    def _read(self) -> dict[str, Any]:
        self._validate_scope()
        if not self._path.exists() and not self._path.is_symlink():
            return self._empty()
        try:
            before = self._path.lstat()
            _require_private_regular_file(before, error=WorkRecordCorrupt, label="disposition")
            if before.st_size > _MAX_BYTES:
                raise WorkRecordCorrupt("disposition oversized")
            fd = os.open(self._path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
            try:
                opened = os.fstat(fd)
                _require_private_regular_file(opened, error=WorkRecordCorrupt, label="disposition")
                if (_file_identity(before) != _file_identity(opened)
                        or _file_identity(opened) != _file_identity(self._path.lstat())
                        or opened.st_size > _MAX_BYTES):
                    raise WorkRecordCorrupt("disposition path changed")
                with os.fdopen(fd, "rb") as stream:
                    fd = -1
                    raw = stream.read(_MAX_BYTES + 1)
                if len(raw) > _MAX_BYTES:
                    raise WorkRecordCorrupt("disposition oversized")
                data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
            finally:
                if fd >= 0:
                    os.close(fd)
            self._validate_payload(data)
            return data
        except WorkRecordCorrupt:
            raise
        except Exception as exc:
            raise WorkRecordCorrupt("disposition unreadable") from exc

    def _validate_payload(self, data: Any) -> None:
        if (type(data) is not dict
                or set(data) != {"schema_version", "conversation_id", "project_id", "revision", "items", "receipts"}
                or data["schema_version"] != _SCHEMA_VERSION
                or data["conversation_id"] != self._conversation_id
                or data["project_id"] != self._bound_project_id
                or type(data["revision"]) is not int or data["revision"] < 0
                or type(data["items"]) is not list or len(data["items"]) > _MAX_ENTRIES
                or type(data["receipts"]) is not list or len(data["receipts"]) > _MAX_ENTRIES):
            raise WorkRecordCorrupt("invalid disposition schema or scope")
        seen: set[tuple[str, str]] = set()
        for row in data["items"]:
            keys = {"kind", "entry_id", "state", "changed_at"}
            if (type(row) is not dict or set(row) != keys
                    or row["kind"] not in _ENTRY_KINDS
                    or row["state"] not in _STATES
                    or not _valid_entry_id(row["kind"], row["entry_id"])
                    or (row["kind"], row["entry_id"]) in seen):
                raise WorkRecordCorrupt("invalid disposition entry")
            seen.add((row["kind"], row["entry_id"]))
            _validate_timestamp(row["changed_at"], "changed_at")
        request_ids: set[str] = set()
        for row in data["receipts"]:
            keys = {"request_id", "operation", "targets", "recorded_at"}
            if (type(row) is not dict or set(row) != keys
                    or row["operation"] not in {"trash", "purge", "restore"}):
                raise WorkRecordCorrupt("invalid disposition receipt")
            try:
                request = _canonical_request_id(row["request_id"])
            except Exception as exc:
                raise WorkRecordCorrupt("invalid disposition request identity") from exc
            if request in request_ids:
                raise WorkRecordCorrupt("duplicate disposition request identity")
            request_ids.add(request)
            _validate_timestamp(row["recorded_at"], "recorded_at")
            targets = row["targets"]
            if type(targets) is not list or len(targets) > _MAX_ENTRIES:
                raise WorkRecordCorrupt("invalid disposition receipt targets")
            for target in targets:
                if (type(target) is not list or len(target) != 2
                        or target[0] not in _ENTRY_KINDS
                        or type(target[1]) is not str
                        or not _valid_entry_id(target[0], target[1])):
                    raise WorkRecordCorrupt("invalid disposition receipt target")

    def _mutate_disposition(self, change: Callable[[dict[str, Any]], tuple[Any, bool]]) -> Any:
        with _file_lock(self._lock_path, self._lock_timeout):
            data = self._read()
            previous = json.loads(json.dumps(data))
            result, changed = change(data)
            if changed:
                data["revision"] += 1
                self._validate_payload(data)
                self._publish(data, previous=previous)
            return result


class WorkCardDisposition:
    """Per-session tombstone index for work cards and linked binding identities."""

    def __init__(self, session: Session, store: WorkRecordStore, ledger: WorkCardLedger) -> None:
        if store._session is not session or ledger._session is not session:
            raise WorkCardDispositionError("session binding mismatch")
        self._session = session
        self._store = store
        self._ledger = ledger
        self._store_path = _DispositionStore(session, store)

    def _index(self, data: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
        return {(row["kind"], row["entry_id"]): row for row in data.get("items", [])}

    def state(self, kind: str, entry_id: str) -> str | None:
        if kind not in _ENTRY_KINDS:
            return None
        with _file_lock(self._store_path._lock_path, self._store_path._lock_timeout):
            row = self._index(self._store_path._read()).get((kind, entry_id))
            return None if row is None else row["state"]

    def is_card_visible(self, card_id: str) -> bool:
        return self.state("card", card_id) not in {"trashed", "purged"}

    def is_binding_visible(self, binding_id: str) -> bool:
        return self.state("binding", binding_id) not in {"trashed", "purged"}

    def is_refresh_visible(self, refresh_id: str) -> bool:
        return self.state("refresh", refresh_id) not in {"trashed", "purged"}

    def hidden_card_ids(self) -> set[str]:
        with _file_lock(self._store_path._lock_path, self._store_path._lock_timeout):
            rows = self._store_path._read()["items"]
        return {row["entry_id"] for row in rows if row["kind"] == "card" and row["state"] in _STATES}

    def hidden_binding_ids(self) -> set[str]:
        with _file_lock(self._store_path._lock_path, self._store_path._lock_timeout):
            rows = self._store_path._read()["items"]
        return {row["entry_id"] for row in rows if row["kind"] == "binding" and row["state"] in _STATES}

    def hidden_refresh_ids(self) -> set[str]:
        with _file_lock(self._store_path._lock_path, self._store_path._lock_timeout):
            rows = self._store_path._read()["items"]
        return {row["entry_id"] for row in rows if row["kind"] == "refresh" and row["state"] in _STATES}

    def filter_visible_cards(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        hidden = self.hidden_card_ids()
        return [row for row in records if row.get("card_id") not in hidden]

    def _set_state(
        self, request_id: str, *, target_state: str, entries: list[tuple[str, str]],
    ) -> DispositionMutationResult:
        try:
            request = _canonical_request_id(request_id)
        except Exception:
            return DispositionMutationResult("rejected", reason="invalid_request_id")
        if target_state not in _STATES or not entries:
            return DispositionMutationResult("rejected", reason="invalid_target")
        target_keys = frozenset(entries)
        operation = "purge" if target_state == "purged" else "trash"
        serialized_targets = [[kind, entry_id] for kind, entry_id in entries]
        outcome: dict[str, Any] = {"status": "failed", "cards": [], "bindings": [], "refreshes": []}

        def change(data: dict[str, Any]):
            indexed = self._index(data)
            prior = next((row for row in data["receipts"] if row["request_id"] == request), None)
            if prior is not None:
                if (prior["operation"] != operation
                        or frozenset((row[0], row[1]) for row in prior["targets"]) != target_keys):
                    outcome["status"] = "rejected"
                    outcome["reason"] = "request_id_conflict"
                    return None, False
                for kind, entry_id in entries:
                    if kind == "card":
                        outcome["cards"].append(entry_id)
                    elif kind == "binding":
                        outcome["bindings"].append(entry_id)
                    else:
                        outcome["refreshes"].append(entry_id)
                outcome["status"] = (
                    "already_purged" if target_state == "purged" else "already_trashed"
                )
                return None, False
            changed_any = False
            timestamp = _server_timestamp()
            for kind, entry_id in entries:
                key = (kind, entry_id)
                existing = indexed.get(key)
                if existing is not None and existing["state"] == "purged" and target_state == "trashed":
                    continue
                if existing is not None and existing["state"] == target_state:
                    continue
                if existing is None:
                    data["items"].append({
                        "kind": kind,
                        "entry_id": entry_id,
                        "state": target_state,
                        "changed_at": timestamp,
                    })
                else:
                    existing["state"] = target_state
                    existing["changed_at"] = timestamp
                changed_any = True
                if kind == "card":
                    outcome["cards"].append(entry_id)
                elif kind == "binding":
                    outcome["bindings"].append(entry_id)
                else:
                    outcome["refreshes"].append(entry_id)
            if not changed_any:
                outcome["status"] = (
                    "already_purged" if target_state == "purged" else "already_trashed"
                )
                return None, False
            data["receipts"].append({
                "request_id": request,
                "operation": operation,
                "targets": serialized_targets,
                "recorded_at": timestamp,
            })
            outcome["status"] = "purged" if target_state == "purged" else "trashed"
            return None, True

        try:
            self._store_path._mutate_disposition(change)
            return DispositionMutationResult(
                outcome.get("status", "failed"),
                tuple(outcome.get("cards", ())),
                tuple(outcome.get("bindings", ())),
                tuple(outcome.get("refreshes", ())),
                outcome.get("reason", ""),
            )
        except WorkRecordCorrupt:
            return DispositionMutationResult("failed", reason="disposition_corrupt")
        except WorkRecordError:
            return DispositionMutationResult("failed", reason="disposition_write_failed")
        except Exception:
            return DispositionMutationResult("uncertain", reason="disposition_write_not_confirmed")

    def soft_delete(
        self, request_id: str, *, card_id: str | None = None, project_id: str | None = None,
        include_bindings: bool = True,
    ) -> DispositionMutationResult:
        return self._delete(request_id, permanent=False, confirm_permanent=False,
                           card_id=card_id, project_id=project_id, include_bindings=include_bindings)

    def hard_delete(
        self, request_id: str, *, card_id: str | None = None, project_id: str | None = None,
        confirm_permanent: bool = False, include_bindings: bool = True,
    ) -> DispositionMutationResult:
        if confirm_permanent is not True:
            return DispositionMutationResult("rejected", reason="confirm_permanent_required")
        return self._delete(request_id, permanent=True, confirm_permanent=True,
                           card_id=card_id, project_id=project_id, include_bindings=include_bindings)

    def restore(self, request_id: str, *, card_id: str) -> DispositionMutationResult:
        if type(card_id) is not str or _CARD_ID.fullmatch(card_id) is None:
            return DispositionMutationResult("rejected", reason="invalid_card_id")
        try:
            request = _canonical_request_id(request_id)
        except Exception:
            return DispositionMutationResult("rejected", reason="invalid_request_id")
        outcome: dict[str, Any] = {"status": "failed"}

        def change(data: dict[str, Any]):
            indexed = self._index(data)
            row = indexed.get(("card", card_id))
            prior = next((item for item in data["receipts"] if item["request_id"] == request), None)
            if prior is not None:
                if prior["operation"] != "restore" or prior["targets"] != [["card", card_id]]:
                    outcome["status"] = "rejected"
                    outcome["reason"] = "request_id_conflict"
                    return None, False
                outcome["status"] = "restored"
                return None, False
            if row is None or row["state"] != "trashed":
                if row is not None and row["state"] == "purged":
                    outcome["status"] = "rejected"
                    outcome["reason"] = "purged_not_restorable"
                else:
                    outcome["status"] = "already_active"
                return None, False
            data["items"] = [item for item in data["items"] if (item["kind"], item["entry_id"]) != ("card", card_id)]
            data["receipts"].append({
                "request_id": request,
                "operation": "restore",
                "targets": [["card", card_id]],
                "recorded_at": _server_timestamp(),
            })
            outcome["status"] = "restored"
            return None, True

        try:
            self._store_path._mutate_disposition(change)
            cards = (card_id,) if outcome.get("status") == "restored" else ()
            return DispositionMutationResult(outcome.get("status", "failed"), cards, reason=outcome.get("reason", ""))
        except WorkRecordCorrupt:
            return DispositionMutationResult("failed", reason="disposition_corrupt")
        except WorkRecordError:
            return DispositionMutationResult("failed", reason="disposition_write_failed")
        except Exception:
            return DispositionMutationResult("uncertain", reason="disposition_write_not_confirmed")

    def _delete(
        self, request_id: str, *, permanent: bool, confirm_permanent: bool,
        card_id: str | None, project_id: str | None, include_bindings: bool,
    ) -> DispositionMutationResult:
        if card_id is not None and _CARD_ID.fullmatch(card_id) is None:
            return DispositionMutationResult("rejected", reason="invalid_card_id")
        if card_id is None and not project_id:
            return DispositionMutationResult("rejected", reason="card_or_project_required")
        if card_id is not None and project_id:
            return DispositionMutationResult("rejected", reason="ambiguous_scope")
        target_state = "purged" if permanent else "trashed"
        entries: list[tuple[str, str]] = []
        if card_id is not None:
            if self._ledger.get_card(card_id) is None:
                return DispositionMutationResult("rejected", reason="unknown_card")
            entries.append(("card", card_id))
        else:
            try:
                normalized = normalize_project_id(project_id or "")
            except ProjectModeError:
                return DispositionMutationResult("rejected", reason="invalid_project_id")
            for record in self._ledger.list_cards():
                if card_matches_project(record, normalized):
                    card_identity = record.get("card_id")
                    if type(card_identity) is str:
                        entries.append(("card", card_identity))
            if include_bindings:
                from multi_project_work_record_bindings import ConversationWorkRecordBindings
                bindings = ConversationWorkRecordBindings(self._session, lock_timeout=self._store._lock_timeout)
                with _file_lock(bindings._dialog_lock_path, bindings._lock_timeout):
                    data = bindings._read()
                for batch in data.get("batches", []):
                    for row in batch.get("bindings", []):
                        if row.get("project_id") == normalized and _BINDING_ID.fullmatch(row["binding_id"]):
                            entries.append(("binding", row["binding_id"]))
                for refresh in bindings._refresh_rows_locked(data):
                    source = refresh.get("copy", {}).get("snapshot", {}).get("source", {})
                    if type(source) is dict and source.get("project_id") == normalized:
                        refresh_id = refresh.get("refresh_id")
                        if type(refresh_id) is str and _REFRESH_ID.fullmatch(refresh_id):
                            entries.append(("refresh", refresh_id))
        if not entries:
            return DispositionMutationResult("rejected", reason="nothing_matched")
        return self._set_state(request_id, target_state=target_state, entries=entries)
