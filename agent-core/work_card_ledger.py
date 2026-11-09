"""Per-Session immutable work-card ledger with explicit viewed acknowledgements.

Only trusted host code may submit project or independent snapshots constructed
from the exact bound WorkRecordStore; the dataclass type itself is not
authentication. The private _LedgerStore reuses WorkRecordStore's OS lock,
validated backup, and atomic publisher while using a separate fixed ledger path
and independent schema validator. This ledger does not read messages or invoke
tools/models. Its publication is not atomic with source-sidecar updates or
Session message writes, and a saved card is not proof of execution or verification.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from evolution.contracts import is_conversation_id
from project_mode import ProjectModeError, normalize_project_id
from session import Session
from work_record_cards import (
    ProjectWorkRecordCard,
    IndependentWorkRecordCardsSnapshot,
    WorkRecordCardsError,
    _build_independent_snapshot,
    _build_snapshot,
    ProjectWorkRecordCardsSnapshot,
    RequirementWorkRecordCard,
    SourceReference,
    StoredResultCard,
)
from work_card_project_scope import (ProjectScopedWorkRecordCardsSnapshot, _build_project_snapshot)
from work_records import (
    WorkRecordCorrupt,
    WorkRecordError,
    WorkRecordStore,
    WorkRecordWriteError,
    _canonical_session,
    _file_identity,
    _file_lock,
    _require_private_regular_file,
)

LEDGER_FILENAME = "work-card-ledger.v1.json"
_LEDGER_ENTRY_PREFIX = LEDGER_FILENAME + ".entry-"
_LEDGER_ENTRY_RE = re.compile(r"^" + re.escape(_LEDGER_ENTRY_PREFIX) + r"(wc-[0-9a-f]{64})\.json$")
_SCHEMA_VERSION = 1
_MAX_LEDGER_BYTES = 4 * 1024 * 1024
_MAX_CARDS = 2_000
_MAX_ITEMS = 500
_MAX_REQUIREMENTS = 5_000
_MAX_SOURCES = 128
_MAX_TEXT_BYTES = 4_096
_MAX_ID_BYTES = 256
_CARD_ID = re.compile(r"^wc-[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_KINDS = {"project", "work_record", "requirement", "result"}
_SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PROJECT_STATUSES = {"active", "completed", "in_progress", "unknown", "blocked", "stopped"}
_REQUIREMENT_STATUSES = {"pending", "completed", "in_progress", "unknown", "blocked", "stopped"}
_RESULT_STATUSES = {"completed", "blocked", "stopped", "unknown", "in_progress"}


class WorkCardLedgerError(RuntimeError):
    """Base error for an unavailable or invalid current-Session ledger."""


class WorkCardLedgerCorrupt(WorkCardLedgerError):
    """The ledger or one of its immutable snapshots failed validation."""


class WorkCardLedgerScopeError(WorkCardLedgerError):
    """The supplied Session/WorkRecordStore pair is not the exact same binding."""


@dataclass(frozen=True, slots=True)
class LinkedItemCopySnapshot:
    """Confirmed historical single-requirement copy; not a project overview."""
    kind: str
    session_id: str
    captured_at: str
    copy_id: str
    completeness_scope: str
    project_id: str
    source_session_id: str
    source_item_digest: str
    binding_id: str
    item_id: str
    item_title: str
    item_status_at_capture: str
    requirement_id: str
    requirement_text: str
    requirement_status_at_capture: str
    worker_id: str
    worker_run_id: str
    host_start: dict[str, Any]
    host_lifecycle: dict[str, Any]
    operation_result: str
    verification: str


def linked_item_copy_request_id(session_id: str, copy_id: str) -> str:
    if type(session_id) is not str or not session_id or type(copy_id) is not str or re.fullmatch(r"[0-9a-f]{64}", copy_id) is None:
        raise ValueError("invalid linked copy identity")
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"my-agent-linked-item-copy:{session_id}:{copy_id}"))


def build_linked_item_copy_snapshot(session_id: str, copy: dict[str, Any]) -> LinkedItemCopySnapshot:
    if (type(copy) is not dict or set(copy) != {"snapshot", "intent", "confirmed_at"}
            or type(copy.get("snapshot")) is not dict or type(copy.get("intent")) is not dict
            or copy["intent"].get("state") != "pending"):
        raise ValueError("confirmed timed copy required")
    row = copy["snapshot"]
    try:
        return LinkedItemCopySnapshot(
            kind="linked_item_copy", session_id=session_id, captured_at=copy["confirmed_at"],
            copy_id=copy["intent"]["copy_id"], completeness_scope="item_requirement",
            project_id=row["project_id"], source_session_id=row["source_session_id"],
            source_item_digest=row["source_item_digest"], binding_id=row["binding_id"],
            item_id=row["item_id"], item_title=row["item_title"], item_status_at_capture=row["item_status"],
            requirement_id=row["requirement_id"], requirement_text=row["requirement_text"],
            requirement_status_at_capture=row["requirement_status"], worker_id=row["worker_id"],
            worker_run_id=row["worker_run_id"], host_start=_json_copy(row["host_start"]),
            host_lifecycle=_json_copy(row["host_lifecycle"]), operation_result=row["operation_result"],
            verification=row["verification"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("incomplete linked copy") from exc


@dataclass(frozen=True, slots=True)
class LinkedProjectSessionCopySnapshot:
    kind: str
    session_id: str
    captured_at: str
    copy_id: str
    binding_id: str
    source: dict[str, Any]


def linked_project_session_copy_request_id(session_id: str, copy_id: str) -> str:
    if type(session_id) is not str or not session_id or type(copy_id) is not str or _SHA256.fullmatch(copy_id) is None:
        raise ValueError("invalid copy identity")
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"my-agent-linked-project-session-copy:{session_id}:{copy_id}"))


def linked_project_refresh_request_id(session_id: str, refresh_id: str) -> str:
    if type(session_id) is not str or not session_id or type(refresh_id) is not str or str(uuid.UUID(refresh_id)) != refresh_id:
        raise ValueError("invalid refresh identity")
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"my-agent-linked-project-refresh:{session_id}:{refresh_id}"))


def build_linked_project_session_copy_snapshot(session_id: str, binding_id: str, copy: dict[str, Any]) -> LinkedProjectSessionCopySnapshot:
    if (type(copy) is not dict or set(copy) != {"snapshot", "intent", "confirmed_at"}
            or type(copy.get("intent")) is not dict or copy["intent"].get("state") != "pending"
            or type(copy.get("snapshot")) is not dict
            or copy["snapshot"].get("mapping_claim") != "not_asserted"):
        raise ValueError("confirmed scope-labelled source copy required")
    return LinkedProjectSessionCopySnapshot("linked_project_session_copy", session_id, copy["confirmed_at"],
                                            copy["intent"]["copy_id"], binding_id, _json_copy(copy["snapshot"]))


@dataclass(frozen=True, slots=True)
class WorkCardLedgerResult:
    status: Literal["saved", "already_saved", "conflict", "rejected", "corrupt", "failed", "uncertain"]
    card_id: str | None = None
    record: dict[str, Any] | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class WorkCardViewAckResult:
    status: Literal["acknowledged", "already_acknowledged", "rejected", "corrupt", "failed", "uncertain"]
    card_id: str | None = None
    viewed_at: str | None = None
    reason: str = ""


_LEDGER_KEYS = frozenset({"schema_version", "session_id", "project_id", "cards"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RECORD_KEYS = frozenset({"card_id", "request_id", "snapshot_sha256", "saved_at", "snapshot", "viewed_at"})
_SNAPSHOT_KEYS = frozenset({
    "project_id", "session_id", "captured_at", "cards", "completeness",
    "item_count", "requirement_count", "result_count", "omitted_item_count",
    "omitted_requirement_count", "omitted_result_count", "incomplete_reason",
    "persisted", "history_complete", "missing_current_result_count",
})
_SNAPSHOT_KEYS_WITH_FOCUS = _SNAPSHOT_KEYS | {"change_focus"}
_PROJECT_CARD_KEYS = frozenset({"project_id", "session_id", "item_id", "title", "status", "source_refs", "requirements"})
_REQUIREMENT_CARD_KEYS = frozenset({
    "project_id", "session_id", "item_id", "requirement_id", "text", "status",
    "source_ref", "latest_result", "result_count", "omitted_result_count",
    "current_result_missing", "current_run_id", "latest_result_is_current",
})
_RESULT_CARD_KEYS = frozenset({
    "project_id", "session_id", "item_id", "requirement_id", "result_id", "run_id",
    "status", "summary", "verification_status", "sources",
})
_RESULT_CARD_KEYS_WITH_PROJECT_TEST = _RESULT_CARD_KEYS | {"project_test"}


def _raise_corrupt(message: str):
    raise WorkCardLedgerCorrupt(message)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkCardLedgerCorrupt("ledger JSON has duplicate keys")
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8", errors="strict")


def _json_copy(value: Any) -> Any:
    return json.loads(_canonical_json(value).decode("utf-8"))


def _require_text(value: Any, label: str, *, max_bytes: int = _MAX_TEXT_BYTES, nonempty: bool = True) -> str:
    if type(value) is not str or (nonempty and not value):
        raise WorkCardLedgerCorrupt(f"{label} is invalid")
    try:
        if len(value.encode("utf-8", errors="strict")) > max_bytes:
            raise WorkCardLedgerCorrupt(f"{label} is oversized")
    except UnicodeError as exc:
        raise WorkCardLedgerCorrupt(f"{label} is invalid UTF-8") from exc
    return value


def _require_identity(value: Any, label: str) -> str:
    return _require_text(value, label, max_bytes=_MAX_ID_BYTES)


def _require_count(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise WorkCardLedgerCorrupt(f"{label} is invalid")
    return value


def _validate_timestamp(value: Any, label: str) -> str:
    text = _require_text(value, label, max_bytes=64)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise WorkCardLedgerCorrupt(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise WorkCardLedgerCorrupt(f"{label} must include a timezone")
    return text


def _server_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_request_id(value: Any) -> str:
    if type(value) is not str or not value:
        raise WorkCardLedgerError("request_id is invalid")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise WorkCardLedgerError("request_id is invalid") from exc
    if str(parsed) != value:
        raise WorkCardLedgerError("request_id must be canonical UUID text")
    return value


def _stable_card_id(session_id: str, request_id: str) -> str:
    digest = hashlib.sha256(f"{session_id}\0{request_id}".encode("utf-8")).hexdigest()
    return "wc-" + digest


def _validate_source_payload(source: Any, store: WorkRecordStore, label: str) -> None:
    if type(source) is not dict or set(source) != {"kind", "id"}:
        raise WorkCardLedgerCorrupt(f"{label} source shape is invalid")
    kind = _require_text(source.get("kind"), f"{label} source kind", max_bytes=32)
    source_id = _require_text(source.get("id"), f"{label} source id", max_bytes=128)
    if kind not in _SOURCE_KINDS or _SOURCE_ID.fullmatch(source_id) is None:
        raise WorkCardLedgerCorrupt(f"{label} source identity is invalid")
    try:
        store._validate_source_ref({"kind": kind, "id": source_id}, corrupt=True)
    except WorkRecordError as exc:
        raise WorkCardLedgerCorrupt(f"{label} source identity is invalid") from exc


def _validate_source_collection(sources: Any, store: WorkRecordStore, label: str) -> None:
    if type(sources) is not list or len(sources) > _MAX_SOURCES:
        raise WorkCardLedgerCorrupt(f"{label} source collection is invalid")
    for source in sources:
        _validate_source_payload(source, store, label)


def _validate_project_snapshot_payload(
    snapshot: Any, *, store: WorkRecordStore, session_id: str, project_id: str,
    allow_scoped_hidden_history: bool = False,
) -> None:
    """Validate the full persisted projection, retaining run/history distinction fields."""
    if type(snapshot) is not dict or set(snapshot) not in (_SNAPSHOT_KEYS, _SNAPSHOT_KEYS_WITH_FOCUS):
        raise WorkCardLedgerCorrupt("snapshot field set is invalid")
    if snapshot.get("project_id") != project_id or snapshot.get("session_id") != session_id:
        raise WorkCardLedgerCorrupt("snapshot Session/project binding does not match")
    _validate_timestamp(snapshot.get("captured_at"), "snapshot captured_at")
    if snapshot.get("completeness") != "complete":
        raise WorkCardLedgerCorrupt("only a complete current snapshot can be saved")
    if snapshot.get("persisted") is not False or snapshot.get("incomplete_reason") is not None:
        raise WorkCardLedgerCorrupt("snapshot completeness metadata is inconsistent")
    if type(snapshot.get("history_complete")) is not bool:
        raise WorkCardLedgerCorrupt("history_complete must be bool")

    counts = {
        field: _require_count(snapshot.get(field), field)
        for field in (
            "item_count", "requirement_count", "result_count", "omitted_item_count",
            "omitted_requirement_count", "omitted_result_count", "missing_current_result_count",
        )
    }
    if counts["omitted_item_count"] or counts["omitted_requirement_count"] or counts["missing_current_result_count"]:
        raise WorkCardLedgerCorrupt("current snapshot is incomplete")
    if snapshot["history_complete"] != (counts["omitted_result_count"] == 0):
        raise WorkCardLedgerCorrupt("history_complete and omitted_result_count disagree")

    cards = snapshot.get("cards")
    if type(cards) is not list or len(cards) > _MAX_ITEMS:
        raise WorkCardLedgerCorrupt("snapshot card collection is invalid or oversized")
    if counts["item_count"] != len(cards):
        raise WorkCardLedgerCorrupt("item_count does not match snapshot cards")

    item_ids: set[str] = set()
    requirement_ids: set[str] = set()
    total_requirements = total_results = total_omitted_results = 0
    for card in cards:
        if type(card) is not dict or set(card) != _PROJECT_CARD_KEYS:
            raise WorkCardLedgerCorrupt("project card field set is invalid")
        if (card.get("project_id") != project_id or card.get("session_id") != session_id
                or card.get("status") not in _PROJECT_STATUSES):
            raise WorkCardLedgerCorrupt("project card identity/status is invalid")
        item_id = _require_identity(card.get("item_id"), "item_id")
        if item_id in item_ids:
            raise WorkCardLedgerCorrupt("snapshot contains a duplicate item")
        item_ids.add(item_id)
        _require_text(card.get("title"), "item title")
        _validate_source_collection(card.get("source_refs"), store, "item")
        requirements = card.get("requirements")
        if type(requirements) is not list or len(requirements) > _MAX_REQUIREMENTS:
            raise WorkCardLedgerCorrupt("requirement collection is invalid or oversized")
        total_requirements += len(requirements)
        for requirement in requirements:
            if type(requirement) is not dict or set(requirement) != _REQUIREMENT_CARD_KEYS:
                raise WorkCardLedgerCorrupt("requirement card field set is invalid")
            if (requirement.get("project_id") != project_id or requirement.get("session_id") != session_id
                    or requirement.get("item_id") != item_id
                    or requirement.get("status") not in _REQUIREMENT_STATUSES):
                raise WorkCardLedgerCorrupt("requirement binding/status is invalid")
            requirement_id = _require_identity(requirement.get("requirement_id"), "requirement_id")
            if requirement_id in requirement_ids:
                raise WorkCardLedgerCorrupt("snapshot contains a duplicate requirement")
            requirement_ids.add(requirement_id)
            _require_text(requirement.get("text"), "requirement text")
            if requirement.get("source_ref") is not None:
                _validate_source_payload(requirement["source_ref"], store, "requirement")

            if type(requirement.get("current_result_missing")) is not bool or requirement["current_result_missing"]:
                raise WorkCardLedgerCorrupt("snapshot current result is missing")
            current_run_id = requirement.get("current_run_id")
            if current_run_id is not None:
                _require_identity(current_run_id, "current_run_id")
            latest_is_current = requirement.get("latest_result_is_current")
            if latest_is_current is not None and type(latest_is_current) is not bool:
                raise WorkCardLedgerCorrupt("latest_result_is_current must be bool or null")

            result_count = _require_count(requirement.get("result_count"), "requirement result_count")
            omitted_count = _require_count(requirement.get("omitted_result_count"), "requirement omitted_result_count")
            if omitted_count > result_count:
                raise WorkCardLedgerCorrupt("omitted result count exceeds result count")
            total_results += result_count
            total_omitted_results += omitted_count
            latest = requirement.get("latest_result")
            if result_count == 0:
                if latest is not None or omitted_count != 0:
                    raise WorkCardLedgerCorrupt("empty result history has latest-result facts")
            elif (allow_scoped_hidden_history and latest is None
                  and requirement.get("status") in {"pending", "in_progress"}
                  and omitted_count == result_count
                  and latest_is_current is (False if current_run_id is not None else None)):
                # Project projection deliberately hides stale results from a new run.
                pass
            else:
                if type(latest) is not dict or set(latest) not in (_RESULT_CARD_KEYS, _RESULT_CARD_KEYS_WITH_PROJECT_TEST) or omitted_count != result_count - 1:
                    raise WorkCardLedgerCorrupt("latest-result/history shape is invalid")
                if (latest.get("project_id") != project_id or latest.get("session_id") != session_id
                        or latest.get("item_id") != item_id or latest.get("requirement_id") != requirement_id
                        or latest.get("status") not in _RESULT_STATUSES):
                    raise WorkCardLedgerCorrupt("latest-result binding/status is invalid")
                _require_identity(latest.get("result_id"), "result_id")
                latest_run_id = _require_identity(latest.get("run_id"), "latest result run_id")
                _require_text(latest.get("summary"), "latest result summary")
                verification_status = latest.get("verification_status")
                if verification_status is not None:
                    _require_text(verification_status, "verification status", max_bytes=128)
                _validate_source_collection(latest.get("sources"), store, "result")
                if "project_test" in latest and latest["project_test"] is not None:
                    report = latest["project_test"]
                    target = report.get("pytest_target") if type(report) is dict else None
                    if (type(report) is not dict or set(report) != {"status", "source", "pytest_target", "exit_code"}
                            or report.get("status") != "passed"
                            or report.get("source") != "user_authorized_project_self_report"
                            or type(target) is not str or not target or target != target.strip()
                            or len(target.encode("utf-8")) > 512
                            or re.fullmatch(r"[A-Za-z0-9_./-]+\.py::test_[A-Za-z0-9_]+", target) is None
                            or ".." in target.split("::", 1)[0].split("/")
                            or type(report.get("exit_code")) is not int or report["exit_code"] != 0
                            or latest.get("status") != "unknown" or verification_status != "not_verified"):
                        raise WorkCardLedgerCorrupt("project self-report test shape/result is invalid")
                expected_latest_current = None if current_run_id is None else latest_run_id == current_run_id
                if latest_is_current is not expected_latest_current:
                    raise WorkCardLedgerCorrupt("latest result/current run distinction is inconsistent")
            if result_count == 0 and current_run_id is not None and latest_is_current is not False:
                raise WorkCardLedgerCorrupt("current run without a result must not appear current")

    if (counts["requirement_count"] != total_requirements
            or counts["result_count"] != total_results
            or counts["omitted_result_count"] != total_omitted_results):
        raise WorkCardLedgerCorrupt("snapshot aggregate counts disagree")
    focus = snapshot.get("change_focus")
    if focus is not None:
        if type(focus) is not dict or set(focus) != {"item_id", "requirement_id", "result_id"}:
            raise WorkCardLedgerCorrupt("snapshot change focus shape is invalid")
        focus_item = _require_identity(focus.get("item_id"), "change_focus item_id")
        focus_requirement = _require_identity(focus.get("requirement_id"), "change_focus requirement_id")
        focus_result = _require_identity(focus.get("result_id"), "change_focus result_id")
        matches = []
        for card in cards:
            if card["item_id"] != focus_item:
                continue
            for requirement in card["requirements"]:
                latest = requirement.get("latest_result")
                if (requirement["requirement_id"] == focus_requirement
                        and type(latest) is dict and latest.get("result_id") == focus_result
                        and latest.get("item_id") == focus_item
                        and latest.get("requirement_id") == focus_requirement
                        and latest.get("session_id") == session_id
                        and latest.get("project_id") == project_id
                        and latest.get("run_id") == requirement.get("current_run_id")
                        and requirement.get("latest_result_is_current") is True):
                    matches.append((focus_item, focus_requirement, focus_result))
        if len(matches) != 1:
            raise WorkCardLedgerCorrupt("snapshot change focus does not identify one current result")


_INDEPENDENT_SNAPSHOT_KEYS = (_SNAPSHOT_KEYS - {"cards"}) | {"kind", "item_id", "card"}
_INDEPENDENT_CARD_KEYS = (_PROJECT_CARD_KEYS - {"project_id"}) | {"kind", "project_id"}
_INDEPENDENT_REQUIREMENT_KEYS = _REQUIREMENT_CARD_KEYS - {"project_id"}
_INDEPENDENT_RESULT_KEYS = _RESULT_CARD_KEYS - {"project_id"}


def _validate_independent_payload(snapshot: Any, *, store: WorkRecordStore, session_id: str, project_id: str) -> None:
    """Validate independent shape, then reuse strict nested project projection checks.

    The project IDs added below exist only in a temporary validation projection;
    the durable independent payload always retains project_id=None.
    """
    if (type(snapshot) is not dict or set(snapshot) not in (
            _INDEPENDENT_SNAPSHOT_KEYS, _INDEPENDENT_SNAPSHOT_KEYS | {"change_focus"},
        )
            or snapshot.get("kind") != "independent" or snapshot.get("project_id") is not None
            or snapshot.get("session_id") != session_id or snapshot.get("item_count") != 1):
        raise WorkCardLedgerCorrupt("independent snapshot identity is invalid")
    item_id = _require_identity(snapshot.get("item_id"), "independent item_id")
    card = snapshot.get("card")
    if (type(card) is not dict or set(card) != _INDEPENDENT_CARD_KEYS
            or card.get("kind") != "independent" or card.get("project_id") is not None
            or card.get("session_id") != session_id or card.get("item_id") != item_id):
        raise WorkCardLedgerCorrupt("independent card identity is invalid")
    requirements = card.get("requirements")
    if type(requirements) is not list:
        raise WorkCardLedgerCorrupt("independent requirements invalid")
    converted_requirements = []
    for requirement in requirements:
        if type(requirement) is not dict or set(requirement) != _INDEPENDENT_REQUIREMENT_KEYS:
            raise WorkCardLedgerCorrupt("independent requirement shape invalid")
        converted = dict(requirement, project_id=project_id)
        latest = requirement.get("latest_result")
        if latest is not None:
            if type(latest) is not dict or set(latest) != _INDEPENDENT_RESULT_KEYS:
                raise WorkCardLedgerCorrupt("independent result shape invalid")
            converted["latest_result"] = dict(latest, project_id=project_id)
        converted_requirements.append(converted)
    converted_card = {key: value for key, value in card.items() if key != "kind"}
    converted_card["project_id"] = project_id
    converted_card["requirements"] = converted_requirements
    converted_snapshot = {key: value for key, value in snapshot.items() if key not in {"kind", "item_id", "card"}}
    converted_snapshot["project_id"] = project_id
    converted_snapshot["cards"] = [converted_card]
    _validate_project_snapshot_payload(converted_snapshot, store=store, session_id=session_id, project_id=project_id)


def _validate_project_scoped_payload(snapshot: Any, *, store: WorkRecordStore, session_id: str) -> None:
    if type(snapshot) is not dict or set(snapshot) not in (
            _SNAPSHOT_KEYS | {"kind"}, _SNAPSHOT_KEYS_WITH_FOCUS | {"kind"},
        ):
        raise WorkCardLedgerCorrupt("project-scoped snapshot field set is invalid")
    if snapshot.get("kind") != "project_scoped" or snapshot.get("session_id") != session_id:
        raise WorkCardLedgerCorrupt("project-scoped snapshot dialogue identity is invalid")
    project_id = snapshot.get("project_id")
    try:
        if type(project_id) is not str or normalize_project_id(project_id) != project_id:
            raise WorkCardLedgerCorrupt("project-scoped project identity is invalid")
    except ProjectModeError as exc:
        raise WorkCardLedgerCorrupt("project-scoped project identity is invalid") from exc
    ordinary = {key: value for key, value in snapshot.items() if key != "kind"}
    _validate_project_snapshot_payload(ordinary, store=store, session_id=session_id, project_id=project_id, allow_scoped_hidden_history=True)


def _validate_linked_item_copy_payload(snapshot: Any, *, session_id: str) -> None:
    fields = set(LinkedItemCopySnapshot.__dataclass_fields__)
    if (type(snapshot) is not dict or set(snapshot) != fields or snapshot.get("kind") != "linked_item_copy"
            or snapshot.get("session_id") != session_id or snapshot.get("completeness_scope") != "item_requirement"
            or snapshot.get("operation_result") != "unknown" or snapshot.get("verification") != "not_verified"):
        raise WorkCardLedgerCorrupt("linked item scope or certainty invalid")
    _validate_timestamp(snapshot.get("captured_at"), "linked captured_at")
    for name in ("project_id", "source_session_id", "binding_id", "item_id", "requirement_id", "worker_id", "worker_run_id"):
        if type(snapshot.get(name)) is not str or _SOURCE_ID.fullmatch(snapshot[name]) is None:
            raise WorkCardLedgerCorrupt("linked item identity invalid")
    for name in ("copy_id", "source_item_digest"):
        if type(snapshot.get(name)) is not str or _SHA256.fullmatch(snapshot[name]) is None:
            raise WorkCardLedgerCorrupt("linked item digest invalid")
    _require_text(snapshot.get("item_title"), "linked item title")
    _require_text(snapshot.get("requirement_text"), "linked requirement")
    if (type(snapshot.get("item_status_at_capture")) is not str
            or snapshot["item_status_at_capture"] not in _PROJECT_STATUSES
            or type(snapshot.get("requirement_status_at_capture")) is not str
            or snapshot["requirement_status_at_capture"] not in _REQUIREMENT_STATUSES):
        raise WorkCardLedgerCorrupt("linked historical status invalid")
    start, lifecycle = snapshot.get("host_start"), snapshot.get("host_lifecycle")
    if (type(start) is not dict or set(start) != {"reservation_id", "parent_run_id", "turn_epoch"}
            or type(start.get("turn_epoch")) is not int or start["turn_epoch"] < 1
            or any(type(start.get(k)) is not str or _SOURCE_ID.fullmatch(start[k]) is None for k in ("reservation_id", "parent_run_id"))
            or type(lifecycle) is not dict or set(lifecycle) != {"sequence", "status", "finish_reason"}
            or type(lifecycle.get("sequence")) is not int or lifecycle["sequence"] != start["turn_epoch"]
            or type(lifecycle.get("status")) is not str
            or lifecycle["status"] not in {"model_turn_finished", "blocked", "failed", "cancelled", "limit_reached", "unknown"}
            or type(lifecycle.get("finish_reason")) is not str
            or lifecycle["finish_reason"] not in {"completed", "cancelled", "timeout", "error", "paused", "unknown"}):
        raise WorkCardLedgerCorrupt("linked lifecycle invalid")
    if lifecycle["status"] == "unknown" and lifecycle["finish_reason"] != "unknown":
        raise WorkCardLedgerCorrupt("linked unknown lifecycle inconsistent")
    from work_records import _digest
    raw = {key: snapshot[key] for key in ("binding_id", "project_id", "source_session_id", "item_id",
            "item_title", "requirement_id", "requirement_text", "source_item_digest", "worker_id",
            "worker_run_id", "host_start", "host_lifecycle", "operation_result", "verification")}
    raw["item_status"] = snapshot["item_status_at_capture"]
    raw["requirement_status"] = snapshot["requirement_status_at_capture"]
    if _digest(raw) != snapshot["copy_id"]:
        raise WorkCardLedgerCorrupt("linked snapshot digest mismatch")


def _matches_locked_linked_item_copy(snapshot: dict[str, Any], store: WorkRecordStore, request_id: str) -> bool:
    # Caller already holds the same .work-records.lock. Never open the source Session.
    from multi_project_work_record_bindings import ConversationWorkRecordBindings
    namespace = ConversationWorkRecordBindings(store._session, lock_timeout=store._lock_timeout)
    data = namespace._read()
    rows = [row for batch in data["batches"] for row in batch["bindings"]
            if row["binding_id"] == snapshot["binding_id"]]
    if len(rows) != 1 or type(rows[0].get("card_copy")) is not dict:
        return False
    try:
        current = _json_copy(asdict(build_linked_item_copy_snapshot(store._conversation_id, rows[0]["card_copy"])))
        return current == snapshot and linked_item_copy_request_id(store._conversation_id, snapshot["copy_id"]) == request_id
    except (ValueError, TypeError, KeyError):
        return False


def _validate_linked_project_session_copy(snapshot: Any, session_id: str) -> None:
    from work_records import _digest
    if (type(snapshot) is not dict or set(snapshot) != set(LinkedProjectSessionCopySnapshot.__dataclass_fields__)
            or snapshot.get("kind") != "linked_project_session_copy" or snapshot.get("session_id") != session_id
            or type(snapshot.get("binding_id")) is not str or _SOURCE_ID.fullmatch(snapshot["binding_id"]) is None
            or type(snapshot.get("copy_id")) is not str or _SHA256.fullmatch(snapshot["copy_id"]) is None):
        raise WorkCardLedgerCorrupt("invalid source-session card identity")
    _validate_timestamp(snapshot.get("captured_at"), "captured_at")
    source = snapshot.get("source")
    if (type(source) is not dict or set(source) != {"scope", "mapping_claim", "project_id", "source_session_id", "source_revision", "items", "worker_facts"}
            or source.get("scope") != "authorized_source_session_only" or source.get("mapping_claim") != "not_asserted"
            or any(type(source.get(k)) is not str or _SOURCE_ID.fullmatch(source[k]) is None for k in ("project_id", "source_session_id"))
            or type(source.get("source_revision")) is not int or source["source_revision"] < 0
            or type(source.get("items")) is not list or len(source["items"]) > _MAX_ITEMS
            or type(source.get("worker_facts")) is not list or not 1 <= len(source["worker_facts"]) <= 20
            or _digest(source) != snapshot["copy_id"] or len(_canonical_json(source)) > 65536):
        raise WorkCardLedgerCorrupt("invalid source-session card scope")
    items = {}
    count = 0
    for item in source["items"]:
        if (type(item) is not dict or set(item) != {"item_id", "title", "status", "requirements"}
                or type(item.get("item_id")) is not str or _SOURCE_ID.fullmatch(item["item_id"]) is None
                or item["item_id"] in items or item.get("status") not in _PROJECT_STATUSES
                or type(item.get("requirements")) is not list):
            raise WorkCardLedgerCorrupt("invalid source item")
        _require_text(item.get("title"), "item title")
        reqs = {}
        for req in item["requirements"]:
            if (type(req) is not dict or set(req) != {"requirement_id", "text", "status"}
                    or type(req.get("requirement_id")) is not str or _SOURCE_ID.fullmatch(req["requirement_id"]) is None
                    or req["requirement_id"] in reqs or req.get("status") not in _REQUIREMENT_STATUSES):
                raise WorkCardLedgerCorrupt("invalid source requirement")
            _require_text(req.get("text"), "requirement")
            reqs[req["requirement_id"]] = req
            count += 1
        items[item["item_id"]] = reqs
    if count > _MAX_REQUIREMENTS:
        raise WorkCardLedgerCorrupt("too many requirements")
    seen = set()
    for fact in source["worker_facts"]:
        if (type(fact) is not dict or set(fact) != {"binding_id", "item_id", "requirement_id", "worker_id", "worker_run_id", "host_start", "host_lifecycle", "operation_result", "verification"}
                or any(type(fact.get(k)) is not str or _SOURCE_ID.fullmatch(fact[k]) is None for k in ("binding_id", "item_id", "requirement_id", "worker_id", "worker_run_id"))
                or fact["binding_id"] in seen or fact["requirement_id"] not in items.get(fact["item_id"], {})
                or fact["operation_result"] != "unknown" or fact["verification"] != "not_verified"):
            raise WorkCardLedgerCorrupt("invalid worker facts")
        seen.add(fact["binding_id"])
        start, life = fact["host_start"], fact["host_lifecycle"]
        if (type(start) is not dict or set(start) != {"reservation_id", "parent_run_id", "turn_epoch"}
                or any(type(start.get(k)) is not str or _SOURCE_ID.fullmatch(start[k]) is None for k in ("reservation_id", "parent_run_id"))
                or type(start.get("turn_epoch")) is not int or start["turn_epoch"] < 1
                or type(life) is not dict or set(life) != {"sequence", "status", "finish_reason"}
                or type(life.get("sequence")) is not int or life["sequence"] != start["turn_epoch"]
                or life.get("status") not in {"model_turn_finished", "blocked", "failed", "cancelled", "limit_reached", "unknown"}
                or life.get("finish_reason") not in {"completed", "cancelled", "timeout", "error", "paused", "unknown"}
                or life.get("status") == "unknown" and life.get("finish_reason") != "unknown"):
            raise WorkCardLedgerCorrupt("invalid lifecycle")
    if snapshot["binding_id"] not in seen:
        raise WorkCardLedgerCorrupt("anchor missing")


def _matches_locked_linked_project_session_copy(snapshot, store, request_id):
    from multi_project_work_record_bindings import ConversationWorkRecordBindings
    namespace = ConversationWorkRecordBindings(store._session, lock_timeout=store._lock_timeout)
    data = namespace._read()
    rows = [r for b in data["batches"] for r in b["bindings"] if r["binding_id"] == snapshot["binding_id"]]
    try:
        if len(rows) == 1 and asdict(build_linked_project_session_copy_snapshot(store._conversation_id, snapshot["binding_id"], rows[0]["project_session_copy"])) == snapshot and linked_project_session_copy_request_id(store._conversation_id, snapshot["copy_id"]) == request_id:
            return True
    except (KeyError, ValueError, TypeError):
        pass
    for refresh in namespace._refresh_rows_locked(data):
        try:
            if (refresh["binding_id"] == snapshot["binding_id"]
                    and linked_project_refresh_request_id(store._conversation_id, refresh["refresh_id"]) == request_id
                    and asdict(build_linked_project_session_copy_snapshot(store._conversation_id, refresh["binding_id"], refresh["copy"])) == snapshot):
                return True
        except (KeyError, ValueError, TypeError):
            continue
    return False


def _validate_snapshot_payload(snapshot: Any, *, store: WorkRecordStore, session_id: str, project_id: str) -> None:
    if type(snapshot) is dict and snapshot.get("kind") == "linked_project_session_copy":
        _validate_linked_project_session_copy(snapshot, session_id)
    elif type(snapshot) is dict and snapshot.get("kind") == "linked_item_copy":
        _validate_linked_item_copy_payload(snapshot, session_id=session_id)
    elif type(snapshot) is dict and snapshot.get("kind") == "independent":
        _validate_independent_payload(snapshot, store=store, session_id=session_id, project_id=project_id)
    elif type(snapshot) is dict and snapshot.get("kind") == "project_scoped":
        _validate_project_scoped_payload(snapshot, store=store, session_id=session_id)
    elif project_id:
        _validate_project_snapshot_payload(snapshot, store=store, session_id=session_id, project_id=project_id)
    else:
        raise WorkCardLedgerCorrupt("unbound Session may contain only independent snapshots")


def _matches_pending_change_focus(
    data: dict[str, Any], snapshot: dict[str, Any], focus: Any,
    intent_key: str, request_id: str,
) -> bool:
    if focus is None:
        return True
    if type(focus) is not dict or set(focus) != {"item_id", "requirement_id", "result_id"}:
        return False
    latest_run_id = None
    cards = ([snapshot.get("card")] if snapshot.get("kind") == "independent"
             else snapshot.get("cards", ()))
    for card in cards:
        if type(card) is not dict or card.get("item_id") != focus.get("item_id"):
            continue
        for requirement in card.get("requirements", ()):
            latest = requirement.get("latest_result") if type(requirement) is dict else None
            if (type(requirement) is dict
                    and requirement.get("requirement_id") == focus.get("requirement_id")
                    and type(latest) is dict and latest.get("result_id") == focus.get("result_id")):
                latest_run_id = latest.get("run_id")
    if type(latest_run_id) is not str or not latest_run_id:
        return False
    matches = [row for row in data.get(intent_key, []) if type(row) is dict
               and row.get("state") == "pending"
               and row.get("request_id") == request_id
               and row.get("latest_item_id") == focus.get("item_id")
               and row.get("latest_requirement_id") == focus.get("requirement_id")
               and row.get("latest_run_id") == latest_run_id
               and row.get("result_id") == focus.get("result_id")]
    return len(matches) == 1


def _matches_locked_project_source(
    snapshot: dict[str, Any], store: WorkRecordStore, session_id: str,
    project_id: str, request_id: str,
) -> bool:
    """Compare a project projection with its exact current sidecar under the shared lock."""
    data = store._read()
    try:
        current = _json_copy(asdict(_build_snapshot(store, project_id, session_id, data["items"])))
    except (KeyError, WorkRecordCardsError, TypeError, ValueError, UnicodeError):
        return False
    # Capture time is projection metadata, not a persisted work-record fact.
    current["captured_at"] = snapshot["captured_at"]
    focus = snapshot.get("change_focus")
    current.pop("change_focus", None)
    candidate = {key: value for key, value in snapshot.items() if key != "change_focus"}
    return current == candidate and _matches_pending_change_focus(
        data, snapshot, focus, "publication_intents", request_id,
    )


def _matches_locked_project_scoped_source(
    snapshot: dict[str, Any], store: WorkRecordStore, session_id: str,
    request_id: str | None = None, expected_generation: int | None = None,
) -> bool:
    """Validate project-scoped source, intent identity and generation under the shared lock."""
    data = store._read()
    project_id = snapshot.get("project_id")
    rows = data.get("project_scoped_card_intents", [])
    intent = None
    if expected_generation is not None:
        if (type(expected_generation) is not int or expected_generation < 1
                or type(request_id) is not str):
            return False
        matches = [row for row in rows if type(row) is dict
                   and row.get("session_id") == session_id
                   and row.get("project_id") == project_id
                   and row.get("request_id") == request_id
                   and row.get("generation") == expected_generation]
        if len(matches) != 1:
            return False
        intent = matches[0]
    elif snapshot.get("change_focus") is not None:
        # New focus requires a durable generation-bound publication intent.
        return False
    try:
        current = _build_project_snapshot(store, session_id, project_id, data["items"])
        if intent is not None:
            focus_keys = {"latest_item_id", "latest_requirement_id", "latest_run_id", "latest_result_id"}
            has_focus = focus_keys.issubset(intent)
            if has_focus:
                from work_record_cards import bind_work_card_change_focus
                current = bind_work_card_change_focus(current, intent)
            elif snapshot.get("change_focus") is not None:
                return False
        elif snapshot.get("change_focus") is not None:
            return False
        if request_id is not None and intent is not None:
            from work_card_project_scope import project_scoped_snapshot_request_id
            if project_scoped_snapshot_request_id(current) != request_id:
                return False
        payload = _json_copy(asdict(current))
    except (KeyError, WorkRecordCardsError, TypeError, ValueError, UnicodeError):
        return False
    payload["captured_at"] = snapshot["captured_at"]
    if payload.get("change_focus") is None:
        payload.pop("change_focus", None)
    candidate = {key: value for key, value in snapshot.items()
                 if key != "change_focus" or value is not None}
    return payload == candidate


def _matches_locked_independent_source(
    snapshot: dict[str, Any], store: WorkRecordStore, session_id: str, request_id: str,
) -> bool:
    """Compare trusted projection with the exact current source under the shared lock."""
    data = store._read()
    matches = [item for item in data["items"] if item.get("item_id") == snapshot["item_id"]]
    if len(matches) != 1 or matches[0].get("scope", {}).get("kind") != "independent":
        return False
    try:
        current = _json_copy(asdict(_build_independent_snapshot(session_id, snapshot["item_id"], matches[0])))
    except (WorkRecordCardsError, TypeError, ValueError, UnicodeError):
        return False
    # Capture time is not a source fact and may differ between the two locked reads.
    current["captured_at"] = snapshot["captured_at"]
    focus = snapshot.get("change_focus")
    current.pop("change_focus", None)
    candidate = {key: value for key, value in snapshot.items() if key != "change_focus"}
    return current == candidate and _matches_pending_change_focus(
        data, snapshot, focus, "independent_publication_intents", request_id,
    )

def _snapshot_payload(
    snapshot: Any, *, store: WorkRecordStore, session_id: str, project_id: str,
    allow_project_scoped: bool = False, allow_linked_item_copy: bool = False,
) -> dict[str, Any]:
    allowed_types = (ProjectWorkRecordCardsSnapshot, IndependentWorkRecordCardsSnapshot)
    if allow_project_scoped:
        allowed_types += (ProjectScopedWorkRecordCardsSnapshot,)
    if allow_linked_item_copy:
        allowed_types += (LinkedItemCopySnapshot, LinkedProjectSessionCopySnapshot)
    if type(snapshot) not in allowed_types:
        raise WorkCardLedgerCorrupt("snapshot must be a trusted host projection")
    try:
        payload = _json_copy(asdict(snapshot))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise WorkCardLedgerCorrupt("snapshot is not safely serializable") from exc
    if payload.get("change_focus") is None:
        payload.pop("change_focus", None)
    _validate_snapshot_payload(payload, store=store, session_id=session_id, project_id=project_id)
    return payload



class _LedgerStore(WorkRecordStore):
    """Use WorkRecordStore's lock, backup and atomic publisher with a separate schema."""

    def __init__(self, session: Session, source_store: WorkRecordStore):
        super().__init__(session, lock_timeout=source_store._lock_timeout)
        self._source_store = source_store
        self._path = self._session_dir / LEDGER_FILENAME
        # Same per-Session OS lock serializes sidecar and ledger publications.
        self._lock_path = source_store._lock_path

    def _empty_ledger(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "conversation_id": self._conversation_id,
            "project_id": self._bound_project_id,
            "revision": 0,
            "items": [],
        }

    def _read_at(self, path: Path) -> dict[str, Any]:
        """Read one canonical ledger shard using the base no-follow/identity checks."""
        if path.parent != self._session_dir or path.name != LEDGER_FILENAME and _LEDGER_ENTRY_RE.fullmatch(path.name) is None:
            raise WorkCardLedgerCorrupt("card ledger shard path is not canonical")
        # A read of one shard must not mutate this shared store object's canonical
        # path: concurrent callers perform binding checks before taking the OS lock.
        reader = copy.copy(self)
        reader._path = path
        try:
            return WorkRecordStore._read(reader)
        except WorkRecordCorrupt as exc:
            raise WorkCardLedgerCorrupt("card ledger is corrupt or unsafe") from exc

    @staticmethod
    def _immutable_record(record: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in record.items() if key != "viewed_at"}

    def _read(self) -> dict[str, Any]:
        """Merge the legacy monolith and bounded one-card records under the Session lock."""
        self._validate_scope()
        base = self._read_at(self._path)
        by_card = {row["card_id"]: row for row in base["items"]}
        request_ids = {row["request_id"] for row in base["items"]}
        revisions = [base["revision"]]
        try:
            paths = sorted(self._session_dir.iterdir(), key=lambda value: value.name)
        except OSError as exc:
            raise WorkCardLedgerCorrupt("card ledger directory cannot be enumerated") from exc
        for path in paths:
            if not path.name.startswith(_LEDGER_ENTRY_PREFIX):
                continue
            match = _LEDGER_ENTRY_RE.fullmatch(path.name)
            if match is None:
                raise WorkCardLedgerCorrupt("card ledger entry filename is invalid")
            shard = self._read_at(path)
            if len(shard["items"]) != 1:
                raise WorkCardLedgerCorrupt("card ledger entry must contain exactly one card")
            record = shard["items"][0]
            if record["card_id"] != match.group(1):
                raise WorkCardLedgerCorrupt("card ledger entry filename does not match card identity")
            prior = by_card.get(record["card_id"])
            if prior is not None:
                if (self._immutable_record(prior) != self._immutable_record(record)
                        or (prior["viewed_at"] is not None and prior["viewed_at"] != record["viewed_at"])
                        or record["viewed_at"] is None):
                    raise WorkCardLedgerCorrupt("card ledger entry conflicts with immutable legacy card")
            elif record["request_id"] in request_ids:
                raise WorkCardLedgerCorrupt("card ledger has duplicate request identity")
            by_card[record["card_id"]] = record
            request_ids.add(record["request_id"])
            revisions.append(shard["revision"])
        combined = self._empty_ledger()
        combined["revision"] = max(revisions, default=0)
        combined["items"] = sorted(by_card.values(), key=lambda row: (row["saved_at"], row["card_id"]))
        self._validate_aggregate(combined)
        return combined

    def _validate_payload(self, data: Any) -> None:
        if (type(data) is not dict
                or set(data) != {"schema_version", "conversation_id", "project_id", "revision", "items"}
                or type(data.get("schema_version")) is not int or data["schema_version"] != _SCHEMA_VERSION
                or data.get("conversation_id") != self._conversation_id
                or data.get("project_id") != self._bound_project_id
                or type(data.get("revision")) is not int or data["revision"] < 0
                or type(data.get("items")) is not list or len(data["items"]) > _MAX_CARDS):
            raise WorkCardLedgerCorrupt("card ledger schema or Session binding is invalid")
        card_ids: set[str] = set()
        request_ids: set[str] = set()
        for record in data["items"]:
            if type(record) is not dict or set(record) != _RECORD_KEYS:
                raise WorkCardLedgerCorrupt("card record shape is invalid")
            try:
                request_id = _canonical_request_id(record.get("request_id"))
            except WorkCardLedgerError as exc:
                raise WorkCardLedgerCorrupt("stored request identity is invalid") from exc
            card_id = record.get("card_id")
            if (type(card_id) is not str or _CARD_ID.fullmatch(card_id) is None
                    or card_id != _stable_card_id(self._conversation_id, request_id)):
                raise WorkCardLedgerCorrupt("stored card identity is invalid")
            if card_id in card_ids or request_id in request_ids:
                raise WorkCardLedgerCorrupt("ledger has duplicate card/request identity")
            card_ids.add(card_id)
            request_ids.add(request_id)
            digest = record.get("snapshot_sha256")
            if type(digest) is not str or _SHA256.fullmatch(digest) is None:
                raise WorkCardLedgerCorrupt("snapshot digest is invalid")
            _validate_timestamp(record.get("saved_at"), "saved_at")
            if record.get("viewed_at") is not None:
                _validate_timestamp(record["viewed_at"], "viewed_at")
            snapshot = record.get("snapshot")
            _validate_snapshot_payload(
                snapshot, store=self._source_store,
                session_id=self._conversation_id, project_id=self._bound_project_id,
            )
            if hashlib.sha256(_canonical_json(snapshot)).hexdigest() != digest:
                raise WorkCardLedgerCorrupt("snapshot digest mismatch")

    def _validate_aggregate(self, data: Any) -> None:
        if (type(data) is not dict
                or set(data) != {"schema_version", "conversation_id", "project_id", "revision", "items"}
                or type(data.get("schema_version")) is not int or data["schema_version"] != _SCHEMA_VERSION
                or data.get("conversation_id") != self._conversation_id
                or data.get("project_id") != self._bound_project_id
                or type(data.get("revision")) is not int or data["revision"] < 0
                or type(data.get("items")) is not list):
            raise WorkCardLedgerCorrupt("card ledger aggregate identity is invalid")
        card_ids: set[str] = set()
        request_ids: set[str] = set()
        for record in data["items"]:
            shard = self._empty_ledger()
            shard["revision"] = data["revision"]
            shard["items"] = [record]
            self._validate_payload(shard)
            if record["card_id"] in card_ids or record["request_id"] in request_ids:
                raise WorkCardLedgerCorrupt("card ledger aggregate has duplicate identities")
            card_ids.add(record["card_id"])
            request_ids.add(record["request_id"])

    def _publish_at(self, path: Path, data: dict[str, Any], previous: dict[str, Any] | None) -> None:
        # Same rule for writes/backups: never expose a transient shard path through
        # the shared WorkRecordStore instance observed by another thread.
        publisher = copy.copy(self)
        publisher._path = path
        WorkRecordStore._publish(publisher, data, previous=previous)

    def _publish(self, data: dict[str, Any], *, previous: dict[str, Any] | None = None) -> None:
        """Atomically publish changed records without rewriting or truncating old history.

        The legacy monolith is retained as-is for compatibility. New cards and mutable
        viewed ACKs are each stored in their own fixed-name, one-record shard; one
        replace therefore commits each normal ledger operation atomically.
        """
        self._validate_aggregate(data)
        if previous is None:
            previous = self._read()
        self._validate_aggregate(previous)
        prior_by_id = {row["card_id"]: row for row in previous["items"]}
        current_by_id = {row["card_id"]: row for row in data["items"]}
        removed = set(prior_by_id) - set(current_by_id)
        added_or_changed = [row for row in data["items"]
                            if prior_by_id.get(row["card_id"]) != row]
        if removed:
            # Work cards are historical records. No ledger mutation path may remove
            # records: fail before publishing any root or entry shard.
            raise WorkRecordWriteError("work-card history removal is not supported")
        for record in added_or_changed:
            card_id = record["card_id"]
            entry = self._session_dir / f"{_LEDGER_ENTRY_PREFIX}{card_id}.json"
            entry_previous = self._read_at(entry) if entry.exists() or entry.is_symlink() else None
            if entry_previous is None and card_id not in prior_by_id and not self._path.exists():
                shard_path = self._path
                shard_previous = None
            else:
                shard_path = entry
                shard_previous = entry_previous
            shard = self._empty_ledger()
            shard["revision"] = data["revision"]
            shard["items"] = [record]
            self._publish_at(shard_path, shard, shard_previous)


class WorkCardLedger:
    """Fixed-path per-Session ledger using WorkRecordStore's OS lock and atomic publisher.

    Only trusted host code may call ``save_snapshot`` with a snapshot constructed
    from this exact bound WorkRecordStore. The dataclass is not authentication:
    websocket/model snapshot bodies must never be passed here. Cards are immutable;
    viewed acknowledgement is separate metadata. Session messages and this ledger
    are independent writes and are not presented as an atomic dual write.
    """

    def __init__(self, session: Session, store: WorkRecordStore) -> None:
        if type(session) is not Session or type(store) is not WorkRecordStore:
            raise WorkCardLedgerScopeError("real Session and WorkRecordStore are required")
        if store._session is not session:
            raise WorkCardLedgerScopeError("WorkRecordStore must be bound to the exact Session")
        self._session = session
        self._store = store
        try:
            self._session_dir, self._agent_root, self._conversation_id = _canonical_session(session)
            raw_project_id = getattr(session.meta, "project_id", "")
            if type(raw_project_id) is not str:
                raise WorkCardLedgerScopeError("Session project binding is invalid")
            self._project_id = normalize_project_id(raw_project_id) if raw_project_id else ""
        except (WorkRecordError, ProjectModeError, TypeError, AttributeError) as exc:
            raise WorkCardLedgerScopeError("Session binding is invalid") from exc
        if (self._project_id != getattr(session.meta, "project_id", "")
                or self._project_id != getattr(store, "_bound_project_id", "")):
            raise WorkCardLedgerScopeError("a matching Session project binding is required")
        self._ledger_store = _LedgerStore(session, store)
        self._validate_binding()

    def _validate_binding(self) -> None:
        if self._store._session is not self._session or self._ledger_store._session is not self._session:
            raise WorkCardLedgerScopeError("ledger Session object changed")
        if (getattr(self._session.meta, "project_id", "") != self._project_id
                or getattr(self._store, "_bound_project_id", "") != self._project_id):
            raise WorkCardLedgerScopeError("ledger project binding changed")
        self._store._validate_scope()
        self._ledger_store._validate_scope()
        session_dir, agent_root, conversation_id = _canonical_session(self._session)
        if (session_dir, agent_root, conversation_id) != (
            self._session_dir, self._agent_root, self._conversation_id,
        ):
            raise WorkCardLedgerScopeError("ledger Session identity or root changed")
        if (getattr(self._session.meta, "project_id", "") != self._project_id
                or getattr(self._store, "_bound_project_id", "") != self._project_id):
            raise WorkCardLedgerScopeError("ledger project binding changed")
        if self._ledger_store._path != self._session_dir / LEDGER_FILENAME:
            raise WorkCardLedgerScopeError("ledger path is not canonical")

    def save_snapshot(self, request_id: str, snapshot: ProjectWorkRecordCardsSnapshot | IndependentWorkRecordCardsSnapshot) -> WorkCardLedgerResult:
        """Persist one legacy project or independent immutable projection."""
        return self._save_snapshot(request_id, snapshot, allow_project_scoped=False)

    def save_project_scoped_snapshot(
        self, request_id: str, snapshot: ProjectScopedWorkRecordCardsSnapshot, *,
        generation: int | None = None,
    ) -> WorkCardLedgerResult:
        """Persist one current-dialogue projection bound to its current publication generation."""
        return self._save_snapshot(
            request_id, snapshot, allow_project_scoped=True,
            expected_generation=generation,
        )

    def save_linked_project_session_copy_snapshot(self, request_id: str, snapshot: LinkedProjectSessionCopySnapshot) -> WorkCardLedgerResult:
        return self._save_snapshot(request_id, snapshot, allow_project_scoped=False, allow_linked_item_copy=True)

    def save_linked_item_copy_snapshot(self, request_id: str, snapshot: LinkedItemCopySnapshot) -> WorkCardLedgerResult:
        """Save only a parent-confirmed item copy; never read its source Session."""
        return self._save_snapshot(request_id, snapshot, allow_project_scoped=False, allow_linked_item_copy=True)

    def _save_snapshot(
        self, request_id: str, snapshot: Any, *, allow_project_scoped: bool,
        expected_generation: int | None = None, allow_linked_item_copy: bool = False,
    ) -> WorkCardLedgerResult:
        """Persist one immutable projection; this method performs no project work."""
        try:
            self._validate_binding()
            request = _canonical_request_id(request_id)
            snapshot_payload = _snapshot_payload(
                snapshot, store=self._store,
                session_id=self._conversation_id, project_id=self._project_id,
                allow_project_scoped=allow_project_scoped, allow_linked_item_copy=allow_linked_item_copy,
            )
            digest = hashlib.sha256(_canonical_json(snapshot_payload)).hexdigest()
            card_id = _stable_card_id(self._conversation_id, request)
        except WorkCardLedgerCorrupt:
            return WorkCardLedgerResult("rejected", None, None, "snapshot_invalid")
        except (WorkCardLedgerError, WorkRecordError):
            return WorkCardLedgerResult("rejected", None, None, "binding_or_request_invalid")
        except Exception:
            return WorkCardLedgerResult("rejected", None, None, "snapshot_invalid")

        outcome: dict[str, Any] = {}
        def change(data: dict[str, Any]):
            if (snapshot_payload.get("kind") == "linked_project_session_copy"
                    and not _matches_locked_linked_project_session_copy(snapshot_payload, self._store, request)):
                outcome.update(status="rejected", record=None)
                return None, False
            if (snapshot_payload.get("kind") == "linked_item_copy"
                    and not _matches_locked_linked_item_copy(snapshot_payload, self._store, request)):
                outcome.update(status="rejected", record=None)
                return None, False
            if (snapshot_payload.get("kind") == "project_scoped"
                    and (expected_generation is not None
                         or snapshot_payload.get("change_focus") is not None)
                    and not _matches_locked_project_scoped_source(
                        snapshot_payload, self._store, self._conversation_id,
                        request, expected_generation,
                    )):
                outcome.update(status="rejected", record=None)
                return None, False
            prior = next((row for row in data["items"] if row["request_id"] == request), None)
            if prior is not None:
                if (prior["card_id"] == card_id and prior["snapshot_sha256"] == digest
                        and prior["snapshot"] == snapshot_payload):
                    outcome.update(status="already_saved", record=prior)
                else:
                    outcome.update(status="conflict", record=prior)
                return prior, False
            if any(row["card_id"] == card_id for row in data["items"]):
                outcome.update(status="conflict", record=None)
                return None, False
            self._validate_binding()
            if snapshot_payload.get("kind") == "linked_project_session_copy":
                source_matches = _matches_locked_linked_project_session_copy(snapshot_payload, self._store, request)
            elif snapshot_payload.get("kind") == "linked_item_copy":
                source_matches = _matches_locked_linked_item_copy(snapshot_payload, self._store, request)
            elif snapshot_payload.get("kind") == "independent":
                source_matches = _matches_locked_independent_source(
                    snapshot_payload, self._store, self._conversation_id, request,
                )
            elif snapshot_payload.get("kind") == "project_scoped":
                source_matches = _matches_locked_project_scoped_source(
                    snapshot_payload, self._store, self._conversation_id,
                    request, expected_generation,
                )
            else:
                source_matches = _matches_locked_project_source(
                    snapshot_payload, self._store, self._conversation_id, self._project_id, request,
                )
            if not source_matches:
                outcome.update(status="rejected", record=None)
                return None, False
            record = {
                "card_id": card_id,
                "request_id": request,
                "snapshot_sha256": digest,
                "saved_at": _server_timestamp(),
                "snapshot": snapshot_payload,
                "viewed_at": None,
            }
            data["items"].append(record)
            self._ledger_store._validate_aggregate(data)
            outcome.update(status="saved", record=record)
            return record, True

        try:
            self._validate_binding()
            self._ledger_store._mutate(change)
            record = outcome.get("record")
            status = outcome.get("status", "failed")
            return WorkCardLedgerResult(
                status, None if status == "rejected" else (card_id if status != "conflict" or record is None else record["card_id"]),
                None if record is None else _json_copy(record),
                "request_id_conflict" if status == "conflict" else ("snapshot_source_mismatch" if status == "rejected" else ""),
            )
        except WorkCardLedgerCorrupt:
            return WorkCardLedgerResult("corrupt", None, None, "ledger_corrupt")
        except WorkCardLedgerScopeError:
            return WorkCardLedgerResult("rejected", None, None, "session_scope_invalid")
        except WorkRecordError:
            return WorkCardLedgerResult("failed", card_id, None, "ledger_write_failed_no_retry")
        except Exception:
            return WorkCardLedgerResult("uncertain", card_id, None, "ledger_write_not_confirmed_no_retry")

    def _read_cards(self) -> list[dict[str, Any]]:
        self._validate_binding()
        return self._ledger_store.list_items()

    def get_card(self, card_id: str) -> dict[str, Any] | None:
        if type(card_id) is not str or _CARD_ID.fullmatch(card_id) is None:
            return None
        return next((row for row in self._read_cards() if row["card_id"] == card_id), None)

    def get_by_request_id(self, request_id: str) -> dict[str, Any] | None:
        try:
            request = _canonical_request_id(request_id)
        except WorkCardLedgerError:
            return None
        return next((row for row in self._read_cards() if row["request_id"] == request), None)

    def list_cards(self) -> list[dict[str, Any]]:
        return self._read_cards()

    def disposition(self):
        from work_card_disposition import WorkCardDisposition
        return WorkCardDisposition(self._session, self._store, self)

    def list_visible_cards(self) -> list[dict[str, Any]]:
        return self.disposition().filter_visible_cards(self.list_cards())

    def get_visible_card(self, card_id: str) -> dict[str, Any] | None:
        record = self.get_card(card_id)
        if record is None or not self.disposition().is_card_visible(card_id):
            return None
        return record

    def acknowledge_viewed(self, card_id: str) -> WorkCardViewAckResult:
        if type(card_id) is not str or _CARD_ID.fullmatch(card_id) is None:
            return WorkCardViewAckResult("rejected", None, None, "invalid_card_id")
        outcome: dict[str, Any] = {}
        def change(data: dict[str, Any]):
            record = next((row for row in data["items"] if row["card_id"] == card_id), None)
            if record is None:
                outcome.update(status="rejected", record=None)
                return None, False
            if record["viewed_at"] is not None:
                outcome.update(status="already_acknowledged", record=record)
                return record, False
            record["viewed_at"] = _server_timestamp()
            self._ledger_store._validate_aggregate(data)
            outcome.update(status="acknowledged", record=record)
            return record, True
        try:
            self._validate_binding()
            self._ledger_store._mutate(change)
            record = outcome.get("record")
            status = outcome.get("status", "failed")
            return WorkCardViewAckResult(
                status, card_id, None if record is None else record.get("viewed_at"),
                "unknown_card" if status == "rejected" else "",
            )
        except WorkCardLedgerCorrupt:
            return WorkCardViewAckResult("corrupt", card_id, None, "ledger_corrupt")
        except WorkCardLedgerScopeError:
            return WorkCardViewAckResult("rejected", card_id, None, "session_scope_invalid")
        except WorkRecordError:
            return WorkCardViewAckResult("failed", card_id, None, "ledger_write_failed_no_retry")
        except Exception:
            return WorkCardViewAckResult("uncertain", card_id, None, "ack_write_not_confirmed_no_retry")
