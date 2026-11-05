from __future__ import annotations

import errno
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class JournalError(RuntimeError):
    """Base class for day-journal storage errors."""


class JournalConflict(JournalError):
    """The expected revision or idempotent payload conflicts with stored data."""


class JournalCorrupt(JournalError):
    """Stored data is malformed. Corrupt records are never silently replaced."""


class JournalPathError(JournalError):
    """A record or lock path is a symlink or escapes the selected root."""


class JournalLockTimeout(JournalError):
    """A Windows record lock remained contended past the bounded wait."""


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_RECORD_FIELDS = {"date", "summary", "projects", "segments", "revision"}
_SEGMENT_FIELDS = {"scope", "project_id", "date", "segment_id", "summary", "source", "occurred_at"}
_LEGACY_SEGMENT_FIELDS = {"project_id", "date", "segment_id", "summary", "source", "occurred_at"}
SegmentScope = Literal["ordinary", "project"]
_SUMMARY_METADATA_FIELDS = {"kind", "verified", "source_ids", "source_fingerprint", "context_fingerprint"}
_LEGACY_SUMMARY_METADATA_FIELDS = {"kind", "verified", "source_ids", "snapshot_sha256", "source_snapshot"}
_WINDOWS_LOCK_TIMEOUT_SECONDS = 5.0
_WINDOWS_LOCK_RETRY_SECONDS = 0.025


def _acquire_windows_lock(fd: int, msvcrt_module=None) -> None:
    """Retry only CRT lock-contention errors and enforce a finite deadline."""
    if msvcrt_module is None:
        import msvcrt as msvcrt_module
    deadline = time.monotonic() + _WINDOWS_LOCK_TIMEOUT_SECONDS
    while True:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt_module.locking(fd, msvcrt_module.LK_NBLCK, 1)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EDEADLK):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise JournalLockTimeout("timed out waiting for day-journal lock") from exc
            time.sleep(min(_WINDOWS_LOCK_RETRY_SECONDS, remaining))


class DayJournal:
    """Explicit local persistence for daily summaries and project-session pointers.

    Every write is caller-triggered. This class never scans sessions, reads
    session contents, infers time from metadata, or sends data elsewhere.
    Callers must supply truthful, authorized summaries and real stable source
    identifiers; the component validates their shape, not their provenance.
    """

    def __init__(self, root: str | Path, timezone: str = "Asia/Shanghai") -> None:
        try:
            self._zone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            if timezone == "Asia/Shanghai":
                # Windows Python may have neither IANA system data nor tzdata.
                # Modern Shanghai timestamps use UTC+08:00.
                from datetime import timedelta, timezone as fixed_timezone
                self._zone = fixed_timezone(timedelta(hours=8), "Asia/Shanghai")
            else:
                raise ValueError(f"unknown timezone: {timezone!r}") from exc
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {timezone!r}") from exc
        self.root = Path(root).expanduser().resolve()

    def save_day(
        self,
        at: datetime,
        summary: str | None,
        project_ids: list[str] | tuple[str, ...],
        *,
        summary_authorized: bool = False,
        expected_revision: int | None = None,
    ) -> dict:
        """Create/update a day index using an explicit timezone-aware instant."""
        day_key, _ = self._instant(at)
        self._validate_summary(summary)
        if summary is not None and summary_authorized is not True:
            raise ValueError("summary requires explicit caller authorization")
        if not isinstance(project_ids, (list, tuple)):
            raise ValueError("project_ids must be a list or tuple of stable IDs")
        projects = sorted(set(self._validate_id(value, "project_id") for value in project_ids))
        if len(projects) != len(project_ids):
            raise ValueError("project_ids must not contain duplicates")
        with self._locked(day_key):
            current = self._read_unlocked(day_key)
            if current is None:
                if expected_revision not in (None, 0):
                    raise JournalConflict("day does not exist at expected revision")
                record = {"date": day_key, "summary": summary, "projects": projects, "segments": [], "revision": 1}
            else:
                if current["summary"] == summary and current["projects"] == projects:
                    return current
                self._check_revision(current, expected_revision)
                removed_projects = set(current["projects"]) - set(projects)
                referenced_projects = {
                    segment["project_id"]
                    for segment in current["segments"]
                    if self._segment_scope(segment) == "project" and segment.get("project_id")
                }
                if removed_projects & referenced_projects:
                    raise JournalConflict("cannot remove a project_id referenced by a day segment")
                record = {**current, "summary": summary, "projects": projects, "revision": current["revision"] + 1}
            self._write_unlocked(day_key, record)
            return record

    def add_segment(
        self,
        at: datetime,
        project_id: str,
        summary: str | None,
        *,
        segment_id: str,
        source: dict,
        expected_revision: int,
        summary_authorized: bool = False,
        summary_metadata: dict | None = None,
    ) -> dict:
        """Append a compact project pointer; never dereference its session source."""
        day_key, instant = self._instant(at)
        project_id = self._validate_id(project_id, "project_id")
        segment_id = self._validate_id(segment_id, "segment_id")
        self._validate_summary(summary)
        if summary is not None and summary_authorized is not True:
            raise ValueError("summary requires explicit caller authorization")
        source = self._validate_source(source)
        if summary_metadata is not None:
            summary_metadata = self._validate_summary_metadata(summary_metadata)
        proposed = self._build_segment_proposed(
            day_key,
            instant,
            segment_id=segment_id,
            summary=summary,
            source=source,
            scope="project",
            project_id=project_id,
            summary_metadata=summary_metadata,
        )
        with self._locked(day_key):
            current = self._read_unlocked(day_key)
            if current is None:
                raise JournalConflict("create the day entry before adding a segment")
            for segment in current["segments"]:
                if segment["segment_id"] == segment_id:
                    if self._segments_equivalent(segment, proposed):
                        return current
                    raise JournalConflict("segment_id already exists with different content")
            self._check_revision(current, expected_revision)
            if project_id not in current["projects"]:
                raise ValueError("segment project_id must exist in the day's explicit project identities")
            record = {**current, "segments": [*current["segments"], proposed], "revision": current["revision"] + 1}
            self._write_unlocked(day_key, record)
            return record

    def record_segment(
        self,
        at: datetime,
        summary: str | None,
        *,
        segment_id: str,
        source: dict,
        scope: SegmentScope,
        project_id: str | None = None,
        summary_authorized: bool = False,
    ) -> dict:
        """Atomically ensure the day record exists and append one scoped segment."""
        day_key, instant = self._instant(at)
        segment_id = self._validate_id(segment_id, "segment_id")
        self._validate_summary(summary)
        if summary is not None and summary_authorized is not True:
            raise ValueError("summary requires explicit caller authorization")
        source = self._validate_source(source)
        scope = self._validate_scope(scope)
        project_id = self._validate_scope_project_id(scope, project_id)
        proposed = self._build_segment_proposed(
            day_key,
            instant,
            segment_id=segment_id,
            summary=summary,
            source=source,
            scope=scope,
            project_id=project_id,
        )
        with self._locked(day_key):
            current = self._read_unlocked(day_key)
            if current is not None:
                for segment in current["segments"]:
                    if segment["segment_id"] == segment_id:
                        if self._segments_equivalent(segment, proposed):
                            return current
                        raise JournalConflict("segment_id already exists with different content")
                projects = list(current["projects"])
                if scope == "project" and project_id and project_id not in projects:
                    projects = sorted(set(projects) | {project_id})
                record = {
                    **current,
                    "projects": projects,
                    "segments": [*current["segments"], proposed],
                    "revision": current["revision"] + 1,
                }
            else:
                projects = [project_id] if scope == "project" and project_id else []
                record = {
                    "date": day_key,
                    "summary": None,
                    "projects": projects,
                    "segments": [proposed],
                    "revision": 1,
                }
            self._write_unlocked(day_key, record)
            return record

    def patch_segment_summary(
        self,
        day: str | date,
        segment_id: str,
        summary: str | None,
        *,
        summary_authorized: bool = False,
        expected_revision: int,
    ) -> dict:
        """Update one segment summary without changing its stable source coordinate."""
        day_key = self._date_key(day)
        segment_id = self._validate_id(segment_id, "segment_id")
        self._validate_summary(summary)
        if summary is not None and summary_authorized is not True:
            raise ValueError("summary requires explicit caller authorization")
        with self._locked(day_key):
            current = self._read_unlocked(day_key)
            if current is None:
                raise JournalConflict("day does not exist")
            self._check_revision(current, expected_revision)
            updated_segments: list[dict] = []
            found = False
            for segment in current["segments"]:
                if segment["segment_id"] != segment_id:
                    updated_segments.append(segment)
                    continue
                found = True
                patched = {**segment, "summary": summary}
                updated_segments.append(patched)
            if not found:
                raise JournalConflict("segment_id not found")
            record = {**current, "segments": updated_segments, "revision": current["revision"] + 1}
            self._write_unlocked(day_key, record)
            return record

    def read_day(self, day: str | date) -> dict | None:
        day_key = self._date_key(day)
        with self._locked(day_key):
            return self._read_unlocked(day_key)

    def delete_day(self, day: str | date, *, expected_revision: int) -> bool:
        """Explicitly remove one record only when its current revision is known."""
        day_key = self._date_key(day)
        with self._locked(day_key):
            current = self._read_unlocked(day_key)
            if current is None:
                return False
            self._check_revision(current, expected_revision)
            self._record_path(day_key).unlink()
            return True

    def _instant(self, value: datetime) -> tuple[str, datetime]:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("at must be an explicit timezone-aware datetime")
        return value.astimezone(self._zone).date().isoformat(), value

    @staticmethod
    def _date_key(value: str | date) -> str:
        if isinstance(value, datetime):
            raise ValueError("read/delete require a date, not a datetime")
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, str) and _DATE_RE.fullmatch(value):
            try:
                return date.fromisoformat(value).isoformat()
            except ValueError as exc:
                raise ValueError("invalid calendar date") from exc
        raise ValueError("day must be an ISO date or date")

    @staticmethod
    def _validate_summary(value: str | None) -> None:
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 2000):
            raise ValueError("summary must be absent or non-empty text up to 2000 characters")

    @staticmethod
    def _validate_id(value: str, field: str) -> str:
        if not isinstance(value, str) or not _ID_RE.fullmatch(value):
            raise ValueError(f"{field} must be a stable identifier, not a display name or path")
        return value

    @staticmethod
    def _validate_scope(scope: str) -> SegmentScope:
        if scope not in ("ordinary", "project"):
            raise ValueError("scope must be ordinary or project")
        return scope  # type: ignore[return-value]

    @classmethod
    def _validate_scope_project_id(cls, scope: SegmentScope, project_id: str | None) -> str | None:
        if scope == "project":
            if not project_id:
                raise ValueError("project scope requires project_id")
            return cls._validate_id(project_id, "project_id")
        if project_id is not None:
            raise ValueError("ordinary scope must not carry project_id")
        return None

    @classmethod
    def _segment_scope(cls, segment: dict) -> SegmentScope:
        raw = segment.get("scope")
        if raw is None:
            pid = segment.get("project_id")
            if pid is None:
                return "ordinary"
            return "project"
        return cls._validate_scope(raw)

    @classmethod
    def _build_segment_proposed(
        cls,
        day_key: str,
        instant: datetime,
        *,
        segment_id: str,
        summary: str | None,
        source: dict,
        scope: SegmentScope,
        project_id: str | None,
        summary_metadata: dict | None = None,
    ) -> dict:
        proposed: dict = {
            "scope": scope,
            "project_id": project_id,
            "date": day_key,
            "segment_id": segment_id,
            "summary": summary,
            "source": source,
            "occurred_at": instant.isoformat(),
        }
        if summary_metadata is not None:
            proposed["summary_metadata"] = summary_metadata
        return proposed

    @staticmethod
    def _segments_equivalent(left: dict, right: dict) -> bool:
        return left == right

    @classmethod
    def _validate_source(cls, source: dict) -> dict:
        if not isinstance(source, dict):
            raise ValueError("source must be a dict")
        kind = source.get("kind")
        if kind == "session":
            if set(source) != {"kind", "session_id"}:
                raise ValueError("source must be an existing stable session coordinate {kind, session_id}")
            session_id = cls._validate_id(source.get("session_id"), "session_id")
            return {"kind": "session", "session_id": session_id}
        if kind == "work_record":
            session_id = cls._validate_id(source.get("session_id"), "session_id")
            item_id = cls._validate_id(source.get("item_id"), "item_id")
            out: dict = {"kind": "work_record", "session_id": session_id, "item_id": item_id}
            for optional in ("requirement_id", "result_id"):
                if optional in source:
                    out[optional] = cls._validate_id(source[optional], optional)
            if set(source) != set(out):
                raise ValueError("work_record source has unexpected fields")
            return out
        raise ValueError("source kind must be session or work_record")

    @classmethod
    def _validate_summary_metadata(cls, metadata: dict) -> dict:
        """Validate candidate provenance, including records written by the prior schema."""
        if not isinstance(metadata, dict):
            raise ValueError("summary_metadata has an invalid schema")
        if set(metadata) == _LEGACY_SUMMARY_METADATA_FIELDS:
            # Read-only compatibility for earlier sandbox records; never emitted by new writes.
            if (metadata.get("kind") != "model_summary_candidate" or metadata.get("verified") is not False
                    or not isinstance(metadata.get("snapshot_sha256"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", metadata["snapshot_sha256"])):
                raise ValueError("legacy summary metadata is invalid")
            source_ids = metadata.get("source_ids")
            excerpts = metadata.get("source_snapshot")
            if (not isinstance(source_ids, list) or not source_ids
                    or any(not isinstance(value, str) or not _ID_RE.fullmatch(value) for value in source_ids)
                    or len(set(source_ids)) != len(source_ids)
                    or not isinstance(excerpts, list) or not excerpts
                    or any(not isinstance(item, dict) or set(item) != {"source_id", "text"}
                           or not isinstance(item.get("source_id"), str)
                           or not _ID_RE.fullmatch(item["source_id"])
                           or not isinstance(item.get("text"), str) or not item["text"].strip()
                           for item in excerpts)):
                raise ValueError("legacy summary evidence is invalid")
            return metadata
        if set(metadata) != _SUMMARY_METADATA_FIELDS:
            raise ValueError("summary_metadata has an invalid schema")
        if metadata.get("kind") != "model_summary_candidate" or metadata.get("verified") is not False:
            raise ValueError("model summary candidates must remain explicitly unverified")
        source_ids = metadata.get("source_ids")
        if (not isinstance(source_ids, list) or not source_ids
                or any(not isinstance(value, str) or not _ID_RE.fullmatch(value) for value in source_ids)
                or len(set(source_ids)) != len(source_ids)):
            raise ValueError("summary candidate source IDs must be non-empty and unique")
        for field in ("source_fingerprint", "context_fingerprint"):
            value = metadata.get(field)
            if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
                raise ValueError(f"summary candidate requires a valid {field}")
        return {"kind": "model_summary_candidate", "verified": False,
                "source_ids": list(source_ids), "source_fingerprint": metadata["source_fingerprint"],
                "context_fingerprint": metadata["context_fingerprint"]}

    def _record_path(self, day_key: str) -> Path:
        return self.root / f"{day_key}.json"

    @contextmanager
    def _locked(self, day_key: str) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise JournalPathError("configured journal root is not a safe directory")
        lock_path = self.root / f".{day_key}.lock"
        if lock_path.is_symlink():
            raise JournalPathError("lock path is a symlink")
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            if lock_path.is_symlink():
                raise JournalPathError("lock path is a symlink") from exc
            raise
        locked = False
        try:
            if os.name == "nt":
                import msvcrt
                # CRT byte-range locks do not require an allocated byte at
                # the locked offset. Keep the lock file empty: writing a
                # sentinel here races before the region lock is acquired and
                # can fail with a Windows sharing violation for concurrent
                # first openers.
                _acquire_windows_lock(fd, msvcrt)
                locked = True
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
                locked = True
            if lock_path.is_symlink() or lock_path.resolve().parent != self.root:
                raise JournalPathError("lock path escapes the configured root")
            yield
        finally:
            if locked:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _assert_safe_record_path(self, path: Path) -> None:
        try:
            if path.is_symlink() or path.resolve().parent != self.root:
                raise JournalPathError("record path is a symlink or escapes the configured root")
        except OSError as exc:
            raise JournalPathError("cannot safely resolve record path") from exc

    def _read_unlocked(self, day_key: str) -> dict | None:
        path = self._record_path(day_key)
        if not path.exists() and not path.is_symlink():
            return None
        self._assert_safe_record_path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or set(data) != _RECORD_FIELDS or data["date"] != day_key:
                raise ValueError("schema/date mismatch")
            if data["summary"] is not None:
                self._validate_summary(data["summary"])
            if not isinstance(data["projects"], list) or not isinstance(data["segments"], list):
                raise ValueError("invalid list fields")
            if type(data["revision"]) is not int or data["revision"] < 1:
                raise ValueError("invalid revision")
            projects = [self._validate_id(item, "project_id") for item in data["projects"]]
            if projects != sorted(set(projects)):
                raise ValueError("project identities are not canonical")
            seen: set[str] = set()
            for segment in data["segments"]:
                if not isinstance(segment, dict):
                    raise ValueError("invalid segment fields")
                field_set = set(segment)
                if field_set not in (
                    _SEGMENT_FIELDS,
                    _SEGMENT_FIELDS | {"summary_metadata"},
                    _LEGACY_SEGMENT_FIELDS,
                    _LEGACY_SEGMENT_FIELDS | {"summary_metadata"},
                ):
                    raise ValueError("invalid segment fields")
                scope = self._segment_scope(segment)
                pid = segment.get("project_id")
                if scope == "project":
                    if pid is None:
                        raise ValueError("project segment requires project_id")
                    pid = self._validate_id(pid, "project_id")
                elif pid is not None:
                    raise ValueError("ordinary segment must not carry project_id")
                segment_id = self._validate_id(segment["segment_id"], "segment_id")
                self._validate_summary(segment["summary"])
                self._validate_source(segment["source"])
                if "summary_metadata" in segment:
                    self._validate_summary_metadata(segment["summary_metadata"])
                occurred_at = datetime.fromisoformat(segment["occurred_at"])
                if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
                    raise ValueError("segment timestamp must retain timezone")
                local_date = occurred_at.astimezone(self._zone).date().isoformat()
                if local_date != day_key:
                    raise ValueError("segment timestamp local date does not match day key")
                if segment["date"] != day_key or segment_id in seen:
                    raise ValueError("segment identity mismatch")
                if scope == "project" and pid not in projects:
                    raise ValueError("segment identity mismatch")
                seen.add(segment_id)
            return data
        except JournalPathError:
            raise
        except Exception as exc:
            raise JournalCorrupt(f"invalid journal record for {day_key}") from exc

    def _write_unlocked(self, day_key: str, record: dict) -> None:
        path = self._record_path(day_key)
        self._assert_safe_record_path(path)
        fd, temp_name = tempfile.mkstemp(prefix=f".{day_key}.", suffix=".tmp", dir=self.root)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(record, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _check_revision(current: dict, expected_revision: int | None) -> None:
        if type(expected_revision) is not int or current["revision"] != expected_revision:
            raise JournalConflict(f"expected revision {expected_revision!r}, found {current['revision']}")

