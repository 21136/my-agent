"""Immutable, read-only project work-record card snapshots."""
from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape as _escape_html
from datetime import UTC, datetime
import re
from typing import Any, Literal

from evolution.contracts import is_conversation_id
from project_mode import ProjectModeError, normalize_project_id
from session import Session
from work_records import WorkRecordError, WorkRecordStore, _digest


_MAX_PROJECT_ITEMS = 500
_MAX_REQUIREMENTS = 5_000
_MAX_RESULTS = 10_000
_MAX_SOURCES_PER_RECORD = 128
_MAX_TEXT_BYTES = 4_096
_MAX_ID_BYTES = 256


class WorkRecordCardsError(ValueError):
    """The supplied store, binding, or validated sidecar cannot safely form cards."""


@dataclass(frozen=True, slots=True)
class WorkCardChangeFocus:
    item_id: str
    requirement_id: str
    result_id: str


@dataclass(frozen=True, slots=True)
class SourceReference:
    kind: str
    id: str


@dataclass(frozen=True, slots=True)
class LocalAssistantReplyExcerptDetail:
    """Ephemeral, read-only detail projection; not a persistable work-card snapshot."""
    session_id: str
    item_id: str
    requirement_id: str
    result_id: str
    label: str
    escaped_excerpt_text: str
    source_availability: str
    result_status: Literal["unknown"]
    verification_status: Literal["not_verified"]


@dataclass(frozen=True, slots=True)
class ProjectTestSelfReport:
    status: Literal["passed"]
    source: Literal["user_authorized_project_self_report"]
    pytest_target: str
    exit_code: Literal[0]


@dataclass(frozen=True, slots=True)
class StoredResultCard:
    project_id: str
    session_id: str
    item_id: str
    requirement_id: str
    result_id: str
    run_id: str
    status: str
    summary: str
    verification_status: str | None
    sources: tuple[SourceReference, ...]
    project_test: ProjectTestSelfReport | None = None


@dataclass(frozen=True, slots=True)
class RequirementWorkRecordCard:
    project_id: str
    session_id: str
    item_id: str
    requirement_id: str
    text: str
    status: str
    source_ref: SourceReference | None
    latest_result: StoredResultCard | None
    result_count: int
    omitted_result_count: int
    current_result_missing: bool = False
    current_run_id: str | None = None
    latest_result_is_current: bool | None = None


@dataclass(frozen=True, slots=True)
class ProjectWorkRecordCard:
    project_id: str
    session_id: str
    item_id: str
    title: str
    status: str
    source_refs: tuple[SourceReference, ...]
    requirements: tuple[RequirementWorkRecordCard, ...]


@dataclass(frozen=True, slots=True)
class ProjectWorkRecordCardsSnapshot:
    project_id: str
    session_id: str
    captured_at: str
    cards: tuple[ProjectWorkRecordCard, ...]
    completeness: Literal["complete", "incomplete"]
    item_count: int
    requirement_count: int
    result_count: int
    omitted_item_count: int = 0
    omitted_requirement_count: int = 0
    omitted_result_count: int = 0
    incomplete_reason: str | None = None
    persisted: bool = False
    # Covers stored result rows only; older rows are omitted when only the latest is shown.
    history_complete: bool = True
    missing_current_result_count: int | None = 0
    change_focus: WorkCardChangeFocus | None = None


@dataclass(frozen=True, slots=True)
class IndependentStoredResultCard:
    session_id: str
    item_id: str
    requirement_id: str
    result_id: str
    run_id: str
    status: str
    summary: str
    verification_status: str | None
    sources: tuple[SourceReference, ...]


@dataclass(frozen=True, slots=True)
class IndependentRequirementCard:
    session_id: str
    item_id: str
    requirement_id: str
    text: str
    status: str
    source_ref: SourceReference | None
    latest_result: IndependentStoredResultCard | None
    result_count: int
    omitted_result_count: int
    current_result_missing: bool
    current_run_id: str | None
    latest_result_is_current: bool | None


@dataclass(frozen=True, slots=True)
class IndependentWorkRecordCard:
    kind: Literal["independent"]
    session_id: str
    item_id: str
    title: str
    status: str
    source_refs: tuple[SourceReference, ...]
    requirements: tuple[IndependentRequirementCard, ...]
    project_id: None = None


@dataclass(frozen=True, slots=True)
class IndependentWorkRecordCardsSnapshot:
    kind: Literal["independent"]
    session_id: str
    item_id: str
    captured_at: str
    card: IndependentWorkRecordCard | None
    completeness: Literal["complete", "incomplete"]
    item_count: int
    requirement_count: int
    result_count: int
    omitted_item_count: int = 0
    omitted_requirement_count: int = 0
    omitted_result_count: int = 0
    incomplete_reason: str | None = None
    persisted: bool = False
    history_complete: bool = True
    missing_current_result_count: int | None = 0
    project_id: None = None
    change_focus: WorkCardChangeFocus | None = None


def bind_work_card_change_focus(snapshot: Any, intent: Any) -> Any:
    """Return a snapshot with focus only when one trusted intent result matches uniquely."""
    if type(intent) is not dict:
        return snapshot
    from work_card_project_scope import ProjectScopedWorkRecordCardsSnapshot
    project_scoped = type(snapshot) is ProjectScopedWorkRecordCardsSnapshot
    if project_scoped:
        if intent.get("state") not in {"pending", "card_saved", "reference_queued"}:
            return snapshot
    elif intent.get("state") != "pending":
        return snapshot
    if type(snapshot) is ProjectWorkRecordCardsSnapshot:
        if (snapshot.completeness != "complete" or intent.get("session_id") != snapshot.session_id
                or intent.get("project_id") != snapshot.project_id):
            return snapshot
        cards = snapshot.cards
    elif type(snapshot) is IndependentWorkRecordCardsSnapshot:
        if (snapshot.completeness != "complete" or intent.get("session_id") != snapshot.session_id
                or intent.get("latest_item_id") != snapshot.item_id):
            return snapshot
        cards = () if snapshot.card is None else (snapshot.card,)
    elif project_scoped:
        if (snapshot.completeness != "complete" or intent.get("session_id") != snapshot.session_id
                or intent.get("project_id") != snapshot.project_id):
            return snapshot
        cards = snapshot.cards
    else:
        return snapshot
    item_id = intent.get("latest_item_id")
    requirement_id = intent.get("latest_requirement_id")
    result_id = intent.get("latest_result_id", intent.get("result_id"))
    run_id = intent.get("latest_run_id")
    if any(type(value) is not str or not value for value in
           (item_id, requirement_id, result_id, run_id)):
        return snapshot
    matches = []
    for card in cards:
        if card.item_id != item_id:
            continue
        for requirement in card.requirements:
            result = requirement.latest_result
            if (requirement.requirement_id == requirement_id and result is not None
                    and result.item_id == item_id and result.requirement_id == requirement_id
                    and result.result_id == result_id and result.run_id == run_id
                    and requirement.current_run_id == run_id
                    and requirement.latest_result_is_current is True):
                matches.append((item_id, requirement_id, result_id))
    if len(matches) != 1:
        return snapshot
    return replace(snapshot, change_focus=WorkCardChangeFocus(*matches[0]))


def _project_test_self_report(value: Any, *, result_status: Any, verification_status: Any) -> ProjectTestSelfReport | None:
    if value is None:
        return None
    keys = {"status", "source", "pytest_target", "exit_code"}
    target = value.get("pytest_target") if type(value) is dict else None
    if (type(value) is not dict or set(value) != keys
            or result_status != "unknown" or verification_status != "not_verified"
            or value.get("status") != "passed"
            or value.get("source") != "user_authorized_project_self_report"
            or type(target) is not str or not target or target != target.strip()
            or len(target.encode("utf-8")) > 512
            or re.fullmatch(r"[A-Za-z0-9_./-]+\.py::test_[A-Za-z0-9_]+", target) is None
            or ".." in target.split("::", 1)[0].split("/")
            or type(value.get("exit_code")) is not int or value["exit_code"] != 0):
        raise WorkRecordCardsError("project self-report test shape or result state is invalid")
    return ProjectTestSelfReport("passed", "user_authorized_project_self_report", target, 0)


def _bounded_text(value: Any, field: str) -> str:
    if type(value) is not str:
        raise WorkRecordCardsError(f"{field} is missing or is not text")
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeError as exc:
        raise WorkRecordCardsError(f"{field} is not valid UTF-8 text") from exc
    if size > _MAX_TEXT_BYTES:
        raise WorkRecordCardsError(f"{field} exceeds the safe card size")
    return value


def _identity(value: Any, field: str) -> str:
    if type(value) is not str or not value:
        raise WorkRecordCardsError(f"{field} identity is missing")
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeError as exc:
        raise WorkRecordCardsError(f"{field} identity is invalid") from exc
    if size > _MAX_ID_BYTES:
        raise WorkRecordCardsError(f"{field} identity exceeds the safe card size")
    return value


def _source_ref(value: Any, field: str) -> SourceReference:
    if type(value) is not dict or set(value) != {"kind", "id"}:
        raise WorkRecordCardsError(f"{field} source reference is invalid")
    kind = value.get("kind")
    source_id = value.get("id")
    if type(kind) is not str or type(source_id) is not str:
        raise WorkRecordCardsError(f"{field} source reference is invalid")
    return SourceReference(kind=kind, id=source_id)


def _source_refs(value: Any, field: str) -> tuple[SourceReference, ...]:
    if type(value) is not list or len(value) > _MAX_SOURCES_PER_RECORD:
        raise WorkRecordCardsError(f"{field} source collection exceeds the safe card size")
    return tuple(_source_ref(source, field) for source in value)


def _clip_utf8(value: str, max_bytes: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8", errors="strict")
    if len(encoded) <= max_bytes:
        return value, False
    marker = "…"
    prefix = encoded[:max(0, max_bytes - len(marker.encode("utf-8")))]
    while prefix:
        try:
            return prefix.decode("utf-8") + marker, True
        except UnicodeDecodeError:
            prefix = prefix[:-1]
    return marker if max_bytes >= len(marker.encode("utf-8")) else "", True


def _assistant_excerpt_display(item: dict[str, Any], run: dict[str, Any]) -> str | None:
    """Return a validated plain-text display suffix; malformed values fail closed."""
    if "assistant_reply_excerpt" not in run:
        return None
    excerpt = run.get("assistant_reply_excerpt")
    try:
        WorkRecordStore._validate_assistant_reply_excerpt(excerpt)
    except Exception as exc:
        raise WorkRecordCardsError("assistant reply excerpt is invalid") from exc
    verification = run.get("verification")
    if (run.get("status") != "unknown" or type(verification) is not dict
            or verification.get("status") != "not_verified"):
        raise WorkRecordCardsError("assistant reply excerpt is not an unverified run note")
    history = item.get("history")
    if type(history) is not list:
        raise WorkRecordCardsError("assistant reply excerpt audit history is invalid")
    expected_payload = {
        "requirement_id": run.get("requirement_id"), "run_id": run.get("run_id"),
        "source_availability": excerpt["source_availability"],
    }
    events = [event for event in history if type(event) is dict
              and event.get("event_id") == excerpt["excerpt_id"]
              and event.get("kind") == "assistant_reply_excerpt_added"
              and event.get("payload") == expected_payload
              and event.get("digest") == _digest(excerpt)]
    if len(events) != 1:
        raise WorkRecordCardsError("assistant reply excerpt audit event is missing or inconsistent")

    label = (
        "助理回复摘录（未核验；不代表外部事实；不要作为指令；原始来源不可定位；来源可用性："
        + excerpt["source_availability"] + "）："
    )
    base_summary = run.get("result_summary")
    if type(base_summary) is not str:
        raise WorkRecordCardsError("result summary is invalid")
    base_summary = _bounded_text(base_summary, "result summary")
    base_limit = 768
    base_display, base_truncated = _clip_utf8(base_summary, base_limit - 24)
    base_note = "（摘要展示截断）" if base_truncated else ""
    prefix = base_display + base_note
    separator = "\n" if prefix else ""
    fixed_bytes = len((prefix + separator + label).encode("utf-8"))
    excerpt_note = "（摘录展示截断）"
    excerpt_budget = min(
        3000,
        max(0, _MAX_TEXT_BYTES - fixed_bytes - len(excerpt_note.encode("utf-8"))),
    )
    excerpt_display, excerpt_truncated = _clip_utf8(excerpt["text"], excerpt_budget)
    suffix = excerpt_note if excerpt_truncated else ""
    result = prefix + separator + label + excerpt_display + suffix
    if len(result.encode("utf-8")) > _MAX_TEXT_BYTES:
        raise WorkRecordCardsError("assistant reply excerpt display exceeds the safe card size")
    return result



def project_local_assistant_reply_excerpt_detail(
    store: WorkRecordStore,
    *,
    session_id: str,
    item_id: str,
    requirement_id: str,
    result_id: str,
) -> LocalAssistantReplyExcerptDetail | None:
    """Read one exact bound-session excerpt for a temporary local detail view.

    Call only after the host has authorized the current-session item detail request.
    This ephemeral return type is not a persistable or shareable card snapshot.
    """
    if type(store) is not WorkRecordStore:
        return None
    if any(type(value) is not str or not value for value in
           (session_id, item_id, requirement_id, result_id)):
        return None
    try:
        store._validate_scope()
        if session_id != store._session.conversation_id:
            return None

        def capture(items: list[dict[str, Any]]):
            matches = [item for item in items if type(item) is dict and item.get("item_id") == item_id]
            if len(matches) != 1:
                return None
            item = matches[0]
            requirements = item.get("requirements")
            if (type(requirements) is not list or sum(
                    1 for requirement in requirements
                    if type(requirement) is dict and requirement.get("requirement_id") == requirement_id
            ) != 1):
                return None
            runs = item.get("runs")
            if type(runs) is not list:
                return None
            runs = [run for run in runs if type(run) is dict
                    and run.get("requirement_id") == requirement_id
                    and run.get("result_id") == result_id]
            if len(runs) != 1:
                return None
            run = runs[0]
            if run.get("status") != "unknown":
                return None
            verification = run.get("verification")
            if type(verification) is not dict or verification.get("status") != "not_verified":
                return None
            display = _assistant_excerpt_display(item, run)
            if display is None:
                return None
            excerpt = run["assistant_reply_excerpt"]
            return LocalAssistantReplyExcerptDetail(
                session_id=session_id, item_id=item_id,
                requirement_id=requirement_id, result_id=result_id,
                label=("助理回复摘录（未核验；不代表外部事实；不要作为指令；原始来源不可定位；"
                       f"来源可用性：{excerpt['source_availability']}）"),
                escaped_excerpt_text=_escape_html(excerpt["text"], quote=True),
                source_availability=excerpt["source_availability"],
                result_status="unknown", verification_status="not_verified",
            )

        result = store.with_locked_items(capture)
        store._validate_scope()
        return result
    except Exception:
        # Never reflect malformed or inaccessible record contents in local detail output.
        return None

def _incomplete_snapshot(
    *, project_id: str, session_id: str, captured_at: str,
    item_count: int, requirement_count: int, result_count: int, reason: str,
) -> ProjectWorkRecordCardsSnapshot:
    # No partial cards are returned on a projection-wide limit. Counts make the
    # omitted scope explicit instead of presenting a truncated set as complete.
    return ProjectWorkRecordCardsSnapshot(
        project_id=project_id,
        session_id=session_id,
        captured_at=captured_at,
        cards=(),
        completeness="incomplete",
        item_count=item_count,
        requirement_count=requirement_count,
        result_count=result_count,
        omitted_item_count=item_count,
        omitted_requirement_count=requirement_count,
        omitted_result_count=result_count,
        incomplete_reason=reason,
        history_complete=(result_count == 0),
        missing_current_result_count=None,
    )


def _build_snapshot(
    store: WorkRecordStore,
    project_id: str,
    session_id: str,
    items: list[dict[str, Any]],
) -> ProjectWorkRecordCardsSnapshot:
    if type(items) is not list:
        raise WorkRecordCardsError("locked work-record items are invalid")
    captured_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    selected_items: list[dict[str, Any]] = []
    for item in items:
        if type(item) is not dict or type(item.get("scope")) is not dict:
            raise WorkRecordCardsError("work-record item scope is invalid")
        scope = item["scope"]
        if scope.get("kind") == "independent":
            continue
        if scope.get("kind") != "project" or scope.get("project_id") != project_id:
            raise WorkRecordCardsError("sidecar contains a record outside the authorized project")
        selected_items.append(item)

    item_count = len(selected_items)
    requirement_count = sum(
        len(item.get("requirements"))
        for item in selected_items
        if type(item.get("requirements")) is list
    )
    result_count = sum(
        len(item.get("runs"))
        for item in selected_items
        if type(item.get("runs")) is list
    )
    if any(type(item.get("requirements")) is not list or type(item.get("runs")) is not list for item in selected_items):
        raise WorkRecordCardsError("work-record item collections are invalid")

    if item_count > _MAX_PROJECT_ITEMS:
        return _incomplete_snapshot(
            project_id=project_id, session_id=session_id, captured_at=captured_at,
            item_count=item_count, requirement_count=requirement_count, result_count=result_count,
            reason="project_item_limit_exceeded",
        )
    if requirement_count > _MAX_REQUIREMENTS:
        return _incomplete_snapshot(
            project_id=project_id, session_id=session_id, captured_at=captured_at,
            item_count=item_count, requirement_count=requirement_count, result_count=result_count,
            reason="requirement_limit_exceeded",
        )
    if result_count > _MAX_RESULTS:
        return _incomplete_snapshot(
            project_id=project_id, session_id=session_id, captured_at=captured_at,
            item_count=item_count, requirement_count=requirement_count, result_count=result_count,
            reason="result_limit_exceeded",
        )

    cards: list[ProjectWorkRecordCard] = []
    omitted_result_count = 0
    missing_current_result_count = 0
    for item in selected_items:
        item_id = _identity(item.get("item_id"), "item")
        title = _bounded_text(item.get("title"), "item title")
        item_status = _identity(item.get("status"), "item status")
        item_sources = _source_refs(item.get("source_refs", []), "item")
        requirements = item["requirements"]
        runs = item["runs"]
        if len(requirements) > _MAX_REQUIREMENTS:
            return _incomplete_snapshot(
                project_id=project_id, session_id=session_id, captured_at=captured_at,
                item_count=item_count, requirement_count=requirement_count, result_count=result_count,
                reason="single_item_requirement_limit_exceeded",
            )

        requirement_ids = {
            _identity(requirement.get("requirement_id"), "requirement")
            for requirement in requirements
            if type(requirement) is dict
        }
        if len(requirement_ids) != len(requirements):
            raise WorkRecordCardsError("requirement identity is invalid")
        runs_by_requirement: dict[str, list[dict[str, Any]]] = {key: [] for key in requirement_ids}
        for run in runs:
            if type(run) is not dict:
                raise WorkRecordCardsError("stored result is invalid")
            requirement_id = _identity(run.get("requirement_id"), "result requirement")
            if requirement_id not in runs_by_requirement:
                raise WorkRecordCardsError("stored result is bound to an unknown requirement")
            runs_by_requirement[requirement_id].append(run)

        requirement_cards: list[RequirementWorkRecordCard] = []
        for requirement in requirements:
            if type(requirement) is not dict:
                raise WorkRecordCardsError("requirement is invalid")
            requirement_id = _identity(requirement.get("requirement_id"), "requirement")
            requirement_text = _bounded_text(requirement.get("text"), "requirement text")
            requirement_status = _identity(requirement.get("status"), "requirement status")
            source_ref_value = requirement.get("source_ref")
            requirement_source = None if source_ref_value is None else _source_ref(source_ref_value, "requirement")

            matching_runs = runs_by_requirement[requirement_id]
            current_run_value = requirement.get("current_run_id")
            current_run_id = None if current_run_value is None else _identity(current_run_value, "current run")
            latest_result: StoredResultCard | None = None
            if matching_runs:
                latest = matching_runs[-1]  # Last stored row; no execution-liveness inference.
                verification = latest.get("verification")
                if type(verification) is not dict:
                    raise WorkRecordCardsError("stored verification is invalid")
                verification_status = verification.get("status")
                if verification_status is not None and type(verification_status) is not str:
                    raise WorkRecordCardsError("stored verification status is invalid")
                if verification_status is not None and len(verification_status.encode("utf-8")) > 128:
                    raise WorkRecordCardsError("stored verification status is too large")
                project_test = _project_test_self_report(
                    verification.get("project_test"), result_status=latest.get("status"),
                    verification_status=verification_status,
                )
                latest_result = StoredResultCard(
                    project_id=project_id,
                    session_id=session_id,
                    item_id=item_id,
                    requirement_id=requirement_id,
                    result_id=_identity(latest.get("result_id"), "result"),
                    run_id=_identity(latest.get("run_id"), "run"),
                    status=_identity(latest.get("status"), "result status"),
                    summary=_bounded_text(latest.get("result_summary"), "result summary"),
                    verification_status=verification_status,
                    sources=_source_refs(latest.get("sources"), "result"),
                    project_test=project_test,
                )
            omitted_for_requirement = max(0, len(matching_runs) - (1 if latest_result else 0))
            omitted_result_count += omitted_for_requirement
            latest_result_is_current = (
                None if current_run_id is None
                else latest_result is not None and latest_result.run_id == current_run_id
            )
            terminal_status = requirement_status in {"completed", "blocked", "stopped", "unknown"}
            current_result_missing = terminal_status and (
                latest_result is None
                or (current_run_id is not None and latest_result_is_current is False)
            )
            if current_result_missing:
                missing_current_result_count += 1
            requirement_cards.append(RequirementWorkRecordCard(
                project_id=project_id,
                session_id=session_id,
                item_id=item_id,
                requirement_id=requirement_id,
                text=requirement_text,
                status=requirement_status,
                source_ref=requirement_source,
                latest_result=latest_result,
                result_count=len(matching_runs),
                omitted_result_count=omitted_for_requirement,
                current_result_missing=current_result_missing,
                current_run_id=current_run_id,
                latest_result_is_current=latest_result_is_current,
            ))

        cards.append(ProjectWorkRecordCard(
            project_id=project_id,
            session_id=session_id,
            item_id=item_id,
            title=title,
            status=item_status,
            source_refs=item_sources,
            requirements=tuple(requirement_cards),
        ))

    incomplete_reason = "current_result_missing" if missing_current_result_count else None
    return ProjectWorkRecordCardsSnapshot(
        project_id=project_id,
        session_id=session_id,
        captured_at=captured_at,
        cards=tuple(cards),
        completeness="incomplete" if missing_current_result_count else "complete",
        item_count=item_count,
        requirement_count=requirement_count,
        result_count=result_count,
        omitted_result_count=omitted_result_count,
        incomplete_reason=incomplete_reason,
        history_complete=(omitted_result_count == 0),
        missing_current_result_count=missing_current_result_count,
    )


def build_project_work_record_cards(
    store: WorkRecordStore,
    project_id: str,
) -> ProjectWorkRecordCardsSnapshot:
    """Build immutable, shareable cards without assistant reply excerpts.

    ``store`` must be the real WorkRecordStore already authorized by the caller.
    The function does not accept paths or other sessions, does not persist a read
    marker/snapshot, and uses only the project-bound items supplied under the
    store's existing OS lock. The lock implementation may create its minimal lock
    file when absent.
    """
    if type(store) is not WorkRecordStore:
        raise WorkRecordCardsError("a real WorkRecordStore is required")
    session = getattr(store, "_session", None)
    if not isinstance(session, Session) or getattr(session, "meta", None) is None:
        raise WorkRecordCardsError("store has no bound Session")
    if type(project_id) is not str:
        raise WorkRecordCardsError("project id must be a canonical string")
    try:
        normalized_project_id = normalize_project_id(project_id)
    except (ProjectModeError, TypeError, AttributeError) as exc:
        raise WorkRecordCardsError("project id is invalid") from exc
    if normalized_project_id != project_id:
        raise WorkRecordCardsError("project id must already be normalized")
    session_id = getattr(session, "conversation_id", None)
    if not is_conversation_id(session_id):
        raise WorkRecordCardsError("bound Session identity is invalid")
    if getattr(session.meta, "project_id", None) != project_id or getattr(store, "_bound_project_id", None) != project_id:
        raise WorkRecordCardsError("project id does not match the bound Session")

    try:
        store._validate_scope()

        def capture(items: list[dict[str, Any]]) -> ProjectWorkRecordCardsSnapshot:
            store._validate_scope()
            if getattr(session.meta, "project_id", None) != project_id:
                raise WorkRecordCardsError("Session project binding changed")
            result = _build_snapshot(store, project_id, session_id, items)
            store._validate_scope()
            return result

        snapshot = store.with_locked_items(capture)
        store._validate_scope()
        return snapshot
    except WorkRecordCardsError:
        raise
    except WorkRecordError as exc:
        raise WorkRecordCardsError("authorized project work-record snapshot is unavailable") from exc


def _incomplete_independent_snapshot(
    *, session_id: str, item_id: str, captured_at: str,
    requirement_count: int, result_count: int, reason: str,
) -> IndependentWorkRecordCardsSnapshot:
    return IndependentWorkRecordCardsSnapshot(
        kind="independent",
        session_id=session_id,
        item_id=item_id,
        captured_at=captured_at,
        card=None,
        completeness="incomplete",
        item_count=1,
        requirement_count=requirement_count,
        result_count=result_count,
        omitted_item_count=1,
        omitted_requirement_count=requirement_count,
        omitted_result_count=result_count,
        incomplete_reason=reason,
        history_complete=(result_count == 0),
        missing_current_result_count=None,
    )


def _build_independent_snapshot(
    session_id: str,
    item_id: str,
    item: dict[str, Any],
) -> IndependentWorkRecordCardsSnapshot:
    captured_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    if type(item.get("scope")) is not dict or item["scope"].get("kind") != "independent":
        raise WorkRecordCardsError("selected item is not independent")
    if _identity(item.get("item_id"), "item") != item_id:
        raise WorkRecordCardsError("selected independent item identity changed")
    requirements = item.get("requirements")
    runs = item.get("runs")
    if type(requirements) is not list or type(runs) is not list:
        raise WorkRecordCardsError("independent item collections are invalid")
    requirement_count, result_count = len(requirements), len(runs)
    if requirement_count > _MAX_REQUIREMENTS:
        return _incomplete_independent_snapshot(
            session_id=session_id, item_id=item_id, captured_at=captured_at,
            requirement_count=requirement_count, result_count=result_count,
            reason="requirement_limit_exceeded",
        )
    if result_count > _MAX_RESULTS:
        return _incomplete_independent_snapshot(
            session_id=session_id, item_id=item_id, captured_at=captured_at,
            requirement_count=requirement_count, result_count=result_count,
            reason="result_limit_exceeded",
        )

    item_title = _bounded_text(item.get("title"), "independent item title")
    item_status = _identity(item.get("status"), "independent item status")
    item_sources = _source_refs(item.get("source_refs", []), "independent item")
    requirement_ids = {
        _identity(requirement.get("requirement_id"), "requirement")
        for requirement in requirements
        if type(requirement) is dict
    }
    if len(requirement_ids) != len(requirements):
        raise WorkRecordCardsError("independent requirement identity is invalid")
    runs_by_requirement: dict[str, list[dict[str, Any]]] = {key: [] for key in requirement_ids}
    for run in runs:
        if type(run) is not dict:
            raise WorkRecordCardsError("independent stored result is invalid")
        requirement_id = _identity(run.get("requirement_id"), "result requirement")
        if requirement_id not in runs_by_requirement:
            raise WorkRecordCardsError("independent result references an unknown requirement")
        runs_by_requirement[requirement_id].append(run)

    cards: list[IndependentRequirementCard] = []
    omitted_result_count = 0
    missing_current_result_count = 0
    for requirement in requirements:
        if type(requirement) is not dict:
            raise WorkRecordCardsError("independent requirement is invalid")
        requirement_id = _identity(requirement.get("requirement_id"), "requirement")
        requirement_text = _bounded_text(requirement.get("text"), "independent requirement text")
        requirement_status = _identity(requirement.get("status"), "independent requirement status")
        current_run_value = requirement.get("current_run_id")
        current_run_id = None if current_run_value is None else _identity(current_run_value, "current run")
        source_ref_value = requirement.get("source_ref")
        requirement_source = None if source_ref_value is None else _source_ref(source_ref_value, "independent requirement")
        matching_runs = runs_by_requirement[requirement_id]
        latest_result: IndependentStoredResultCard | None = None
        if matching_runs:
            latest = matching_runs[-1]
            verification = latest.get("verification")
            if type(verification) is not dict:
                raise WorkRecordCardsError("independent stored verification is invalid")
            verification_status = verification.get("status")
            if verification_status is not None and type(verification_status) is not str:
                raise WorkRecordCardsError("independent verification status is invalid")
            if verification_status is not None and len(verification_status.encode("utf-8")) > 128:
                raise WorkRecordCardsError("independent verification status is too large")
            latest_result = IndependentStoredResultCard(
                session_id=session_id,
                item_id=item_id,
                requirement_id=requirement_id,
                result_id=_identity(latest.get("result_id"), "result"),
                run_id=_identity(latest.get("run_id"), "run"),
                status=_identity(latest.get("status"), "result status"),
                summary=_bounded_text(latest.get("result_summary"), "independent result summary"),
                verification_status=verification_status,
                sources=_source_refs(latest.get("sources"), "independent result"),
            )
        omitted_for_requirement = max(0, len(matching_runs) - (1 if latest_result else 0))
        omitted_result_count += omitted_for_requirement
        latest_result_is_current = (
            None if current_run_id is None
            else latest_result is not None and latest_result.run_id == current_run_id
        )
        terminal_status = requirement_status in {"completed", "blocked", "stopped", "unknown"}
        current_result_missing = terminal_status and (
            latest_result is None
            or (current_run_id is not None and latest_result_is_current is False)
        )
        if current_result_missing:
            missing_current_result_count += 1
        cards.append(IndependentRequirementCard(
            session_id=session_id,
            item_id=item_id,
            requirement_id=requirement_id,
            text=requirement_text,
            status=requirement_status,
            source_ref=requirement_source,
            latest_result=latest_result,
            result_count=len(matching_runs),
            omitted_result_count=omitted_for_requirement,
            current_result_missing=current_result_missing,
            current_run_id=current_run_id,
            latest_result_is_current=latest_result_is_current,
        ))

    card = IndependentWorkRecordCard(
        kind="independent",
        session_id=session_id,
        item_id=item_id,
        title=item_title,
        status=item_status,
        source_refs=item_sources,
        requirements=tuple(cards),
    )
    incomplete_reason = "current_result_missing" if missing_current_result_count else None
    return IndependentWorkRecordCardsSnapshot(
        kind="independent",
        session_id=session_id,
        item_id=item_id,
        captured_at=captured_at,
        card=card,
        completeness="incomplete" if missing_current_result_count else "complete",
        item_count=1,
        requirement_count=requirement_count,
        result_count=result_count,
        omitted_result_count=omitted_result_count,
        incomplete_reason=incomplete_reason,
        history_complete=(omitted_result_count == 0),
        missing_current_result_count=missing_current_result_count,
    )


def build_independent_work_record_card(
    store: WorkRecordStore,
    item_id: str,
) -> IndependentWorkRecordCardsSnapshot:
    """Build one independent item's immutable, shareable card without excerpts.

    The item id is exact and scoped to the real WorkRecordStore. No title search, path,
    session id, or cross-session lookup is accepted. The operation is
    read-only apart from the store's existing lock file when it is first needed.
    """
    if type(store) is not WorkRecordStore:
        raise WorkRecordCardsError("a real WorkRecordStore is required")
    session = getattr(store, "_session", None)
    if not isinstance(session, Session) or getattr(session, "meta", None) is None:
        raise WorkRecordCardsError("store has no bound Session")
    selected_item_id = _identity(item_id, "independent item")
    session_id = getattr(session, "conversation_id", None)
    if not is_conversation_id(session_id):
        raise WorkRecordCardsError("bound Session identity is invalid")

    def capture(items: list[dict[str, Any]]) -> IndependentWorkRecordCardsSnapshot:
        store._validate_scope()
        if getattr(session, "conversation_id", None) != session_id:
            raise WorkRecordCardsError("Session identity changed")
        matches = [
            item for item in items
            if type(item) is dict and item.get("item_id") == selected_item_id
        ]
        if len(matches) != 1:
            raise WorkRecordCardsError("independent item is missing or ambiguous")
        item = matches[0]
        if type(item.get("scope")) is not dict or item["scope"].get("kind") != "independent":
            raise WorkRecordCardsError("selected item is not independent")
        snapshot = _build_independent_snapshot(session_id, selected_item_id, item)
        store._validate_scope()
        return snapshot

    try:
        store._validate_scope()
        snapshot = store.with_locked_items(capture)
        store._validate_scope()
        return snapshot
    except WorkRecordCardsError:
        raise
    except WorkRecordError as exc:
        raise WorkRecordCardsError("authorized Session work-record snapshot is unavailable") from exc

