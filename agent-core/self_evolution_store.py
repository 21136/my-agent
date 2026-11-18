from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable

from self_evolution_types import CandidateBinding, EvolutionEvent, EvolutionIdentity, EvolutionSnapshot, VerificationEvidence


class StoreError(RuntimeError):
    """Base for fail-closed persistence errors."""


class StoreConflict(StoreError):
    """The revision, state transition, or event sequence is stale/invalid."""


class StoreCorrupt(StoreError):
    """Persisted content is malformed and must not be replaced implicitly."""


class StoreOwnershipError(StoreError):
    """A writer is already held, unknown, or cannot be safely released."""


class StoreCapacityError(StoreError):
    """A hard, non-reclaimable replay ledger capacity has been exhausted."""


@dataclass(frozen=True)
class StartRequestClaim:
    """Result of a durable (session, request) start reservation lookup."""

    session_id: str
    request_id: str
    request_digest: str
    status: str
    claim_token: str | None = None
    result: dict[str, Any] | None = None


@dataclass(frozen=True)
class EventPage:
    """Bounded event slice with explicit retention and sequence-gap facts."""

    events: tuple[dict[str, Any], ...]
    earliest_sequence: int | None
    latest_sequence: int | None
    watermark_sequence: int | None
    history_truncated: bool
    has_gap: bool


_ALLOWED: dict[str, frozenset[str]] = {
    "preparing": frozenset({"observing", "blocked", "cancelling", "failed", "unknown"}),
    "observing": frozenset({"awaiting_design_approval", "editing", "blocked", "cancelling", "failed", "unknown"}),
    "awaiting_design_approval": frozenset({"editing", "blocked", "cancelling", "failed", "unknown"}),
    "editing": frozenset({"checking", "blocked", "cancelling", "failed", "unknown"}),
    "checking": frozenset({"editing", "awaiting_review", "blocked", "cancelling", "failed", "unknown"}),
    "awaiting_review": frozenset({"editing", "checking", "blocked", "cancelling", "failed", "unknown", "merged"}),
    "blocked": frozenset({"observing", "editing", "checking", "cancelling", "failed", "unknown"}),
    "cancelling": frozenset({"cancelled", "failed", "unknown"}),
    "unknown": frozenset(),
    "cancelled": frozenset(),
    "failed": frozenset(),
    "merged": frozenset(),
}
_TERMINAL = frozenset({"cancelled", "failed", "merged"})
_MAX_EVENTS_PER_ATTEMPT = 256
_MAX_START_REQUESTS = 16384
_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_START_RESULT_KEYS = frozenset({"status", "task_id", "attempt_id", "run_id", "error_code", "worker_bound", "os_sandbox", "merge_authorized"})


def _require_private_storage_acl(path: str | Path, *, platform: str, verifier: Callable[[Path], bool] | None, expected_type: str) -> None:
    """Require a host-configured ACL verifier on Windows; never accept UI input.

    The host callback is only an additional policy check. Native Windows
    inspection audits owner, DACL, and identity through one path handle; it does
    not bind that handle to SQLite's private handle or prove sidecar/recovery
    safety. Callback/test data is never accepted as native ACL evidence.
    """
    if platform != "nt":
        return
    target = Path(path)
    if not callable(verifier):
        raise StoreError("Windows storage disabled: private ACL cannot be verified")
    try:
        verified = verifier(target)
    except Exception as exc:
        raise StoreError("Windows storage disabled: private ACL verification failed") from exc
    if verified is not True:
        raise StoreError("Windows storage disabled: private ACL cannot be verified")
    try:
        from self_evolution_windows_acl import audit_private_path

        audit_private_path(target, expected_type=expected_type)
    except Exception as exc:
        raise StoreError(f"Windows storage disabled: native private ACL audit failed for {target}") from exc


def _identity_dict(value: EvolutionIdentity) -> dict[str, Any]:
    return asdict(value)


def _snapshot_dict(value: EvolutionSnapshot) -> dict[str, Any]:
    data = asdict(value)
    data["candidate"] = asdict(value.candidate) if value.candidate else None
    data["evidence"] = [asdict(item) for item in value.evidence]
    return data


def _snapshot_from(data: Any) -> EvolutionSnapshot:
    if not isinstance(data, dict):
        raise ValueError("snapshot must be object")
    identity = EvolutionIdentity(**data.pop("identity"))
    candidate_data = data.pop("candidate")
    evidence_data = data.pop("evidence")
    if candidate_data is not None:
        candidate_data = CandidateBinding(**candidate_data)
    evidence = tuple(VerificationEvidence(**item) for item in evidence_data)
    data["discoveries"] = tuple(data["discoveries"])
    data["allowed_next_actions"] = tuple(data["allowed_next_actions"])
    return EvolutionSnapshot(identity=identity, candidate=candidate_data, evidence=evidence, **data)


class EvolutionStore:
    """SQLite-backed task snapshots and a persistent global single-writer slot.

    A held slot is deliberately not expired by time or process-local liveness. The
    trusted host must explicitly reconcile it; uncertainty retains the slot.
    """

    def __init__(self, path: str | Path, *, trusted_acl_verifier: Callable[[Path], bool] | None = None) -> None:
        supplied = Path(path).expanduser()
        if supplied.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            self.path = supplied.absolute()
        else:
            self.path = supplied.absolute() / "self-evolution.sqlite3"
        self._trusted_acl_verifier = trusted_acl_verifier
        if os.name == "nt":
            self._verify_private_storage_acls()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise StoreError("database path and its immediate parent cannot be symlinks")
        if self.path.parent.resolve() != self.path.parent:
            raise StoreError("database parent must be canonical; bind it through trusted AgentPaths")
        self._local = threading.local()
        # Serialize every process-local connection lifecycle for this store.
        # Native ACL checks cover path identities and SQLite may remove WAL/SHM
        # as connections close, so the lock must include preflight, connect,
        # operation, close, and postflight rather than only database statements.
        self._connection_lock = threading.RLock()
        self._initialize()
        if os.name == "nt":
            self._verify_private_storage_acls()
        else:
            try:
                os.chmod(self.path, 0o600)
            except OSError as exc:
                raise StoreError("cannot enforce private database permissions") from exc

    def _verify_private_storage_acls(self) -> None:
        """Audit the directory and all existing SQLite files after connection.

        Retry a complete audit for transient native sidecar disappearance or
        sharing/lock conflicts, then fail closed. This path-based preflight is
        not an atomic binding to SQLite handles; replacement races remain.
        """
        from self_evolution_windows_acl import WindowsAclError

        sidecars = (Path(f"{self.path}-wal"), Path(f"{self.path}-shm"))
        for attempt in range(30):
            _require_private_storage_acl(self.path.parent, platform=os.name, verifier=self._trusted_acl_verifier,
                                         expected_type="directory")
            retry = False
            for target in (self.path, *sidecars):
                try:
                    target.lstat()
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise StoreError(f"Windows storage disabled: cannot inspect {target}") from exc
                try:
                    _require_private_storage_acl(target, platform=os.name, verifier=self._trusted_acl_verifier,
                                                 expected_type="file")
                except StoreError as exc:
                    cause = exc.__cause__
                    if (target in sidecars and isinstance(cause, WindowsAclError)
                            and cause.winerror in (2, 3, 5, 32, 33) and attempt < 29):
                        retry = True
                        break
                    raise
            if not retry:
                return
            time.sleep(0.02)

    def _connect(self) -> sqlite3.Connection:
        if os.name == "nt":
            self._verify_private_storage_acls()
        conn = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=10000")
            if os.name == "nt":
                self._verify_private_storage_acls()
            return conn
        except BaseException:
            conn.close()
            raise

    @contextmanager
    def _connection(self):
        """Serialize ACL-audited SQLite handle lifetimes within this store."""
        with self._connection_lock:
            connection = self._connect()
            try:
                yield connection
            finally:
                connection.close()

    def _initialize(self) -> None:
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
                run_id TEXT NOT NULL, turn_epoch INTEGER NOT NULL, revision INTEGER NOT NULL,
                phase TEXT NOT NULL, payload TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS writer_slot (
                slot INTEGER PRIMARY KEY CHECK(slot=1), owner_id TEXT NOT NULL,
                task_id TEXT NOT NULL, attempt_id TEXT NOT NULL, state TEXT NOT NULL
                CHECK(state IN ('held','unknown')))""")
            db.execute("""CREATE TABLE IF NOT EXISTS event_watermarks (
                task_id TEXT NOT NULL, attempt_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                PRIMARY KEY(task_id, attempt_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS events (
                task_id TEXT NOT NULL, attempt_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                session_id TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(task_id, attempt_id, sequence))""")
            db.execute("""CREATE TABLE IF NOT EXISTS start_requests (
                session_id TEXT NOT NULL, request_id TEXT NOT NULL, request_digest TEXT NOT NULL,
                claim_token TEXT NOT NULL, state TEXT NOT NULL
                CHECK(state IN ('claimed','dispatching','completed','rejected','unknown')),
                result_json TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY(session_id, request_id))""")
            # Keep run/turn ownership in an additive side table. Extending the
            # positional start_requests schema could break an older live Host.
            db.execute("""CREATE TABLE IF NOT EXISTS start_request_bindings (
                session_id TEXT NOT NULL, request_id TEXT NOT NULL,
                run_id TEXT NOT NULL, turn_epoch INTEGER NOT NULL CHECK(turn_epoch >= 0),
                PRIMARY KEY(session_id, request_id))""")
            # A consumed request may create at most one durable task, even after
            # that task becomes terminal and its writer slot is safely released.
            # This additive ledger also survives Host restarts and lost responses.
            db.execute("""CREATE TABLE IF NOT EXISTS start_request_tasks (
                session_id TEXT NOT NULL, request_id TEXT NOT NULL, task_id TEXT NOT NULL UNIQUE,
                PRIMARY KEY(session_id, request_id))""")
            writer_columns = {row["name"] for row in db.execute("PRAGMA table_info(writer_slot)").fetchall()}
            if "state" not in writer_columns:
                # A different Host using the legacy schema could still be active.
                # Even adding a column may disrupt its inserts or mutate its owner.
                # Migration requires an independently established offline window.
                raise StoreOwnershipError("legacy writer schema requires offline migration; owner liveness is unconfirmed")

    def close(self) -> None:
        # Connections are operation-scoped; retained as a compatibility no-op.
        return None

    def audit_private_storage_path(self, path: str | Path):
        """Return a fresh native point-in-time ACL proof for this store root.

        Host code may reuse the same root-bound verifier for associated private
        files, but this proof is not attached to SQLite's internal handles and
        cannot attest WAL/recovery lifetime safety.
        """
        if os.name != "nt":
            raise StoreError("native Windows private ACL proof is unavailable on this platform")
        from self_evolution_windows_acl import PrivateStorageAclProof

        verifier = self._trusted_acl_verifier
        audit = getattr(verifier, "audit", None)
        if not callable(audit):
            raise StoreError("store was not created with a reusable native ACL verifier")
        try:
            proof = audit(path)
        except Exception as exc:
            raise StoreError("private storage ACL proof failed") from exc
        if (type(proof) is not PrivateStorageAclProof
                or proof.root_path != self.path.parent.resolve()
                or proof.target_path != Path(path)):
            raise StoreError("private storage ACL proof is not bound to this store root")
        return proof

    def reserve_start_request(self, session_id: str, request_id: str, request_digest: str, *,
                              run_id: str | None = None, turn_epoch: int | None = None) -> StartRequestClaim:
        """Atomically reserve one authenticated-session request identity.

        Rows are permanent replay tombstones. The hard global cap fails closed;
        no age-based eviction is safe for authenticated request identities.
        """
        if (type(session_id) is not str or not session_id or len(session_id) > 256
                or type(request_id) is not str or not _REQUEST_ID_RE.fullmatch(request_id)
                or type(request_digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", request_digest)):
            raise ValueError("invalid authenticated session, canonical request UUID, or SHA-256 digest")
        if ((run_id is None) != (turn_epoch is None)
                or (run_id is not None and (type(run_id) is not str or not run_id or len(run_id) > 256))
                or (turn_epoch is not None and (type(turn_epoch) is not int or turn_epoch < 0))):
            raise ValueError("run_id and a non-negative integer turn_epoch must be provided together")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT * FROM start_requests WHERE session_id=? AND request_id=?",
                                 (session_id, request_id)).fetchone()
                if row is not None:
                    claim = self._decode_start_request(row)
                    if claim.request_digest != request_digest:
                        raise StoreConflict("request identity payload mismatch")
                    binding = db.execute(
                        "SELECT run_id, turn_epoch FROM start_request_bindings WHERE session_id=? AND request_id=?",
                        (session_id, request_id),
                    ).fetchone()
                    expected = (run_id, turn_epoch) if run_id is not None else None
                    stored = (binding["run_id"], binding["turn_epoch"]) if binding is not None else None
                    if stored != expected:
                        raise StoreOwnershipError("request reservation belongs to a different or unbound run epoch")
                    db.commit()
                    return claim
                count = db.execute("SELECT COUNT(*) AS n FROM start_requests").fetchone()["n"]
                if count >= _MAX_START_REQUESTS:
                    raise StoreCapacityError("start request replay ledger capacity exhausted; refusing new starts")
                # Serialize global admission *after* the caller may perform
                # candidate creation or any other external side effect. A busy
                # request is a durable rejection, never a second live claim.
                pending = db.execute(
                    "SELECT * FROM start_requests WHERE state IN ('claimed','dispatching','unknown')"
                ).fetchall()
                for existing in pending:
                    self._decode_start_request(existing)
                active = db.execute(
                    "SELECT * FROM tasks WHERE phase NOT IN ('awaiting_review','cancelled','failed','merged')"
                ).fetchall()
                for task in active:
                    self._decode_row(task)
                slots = db.execute("SELECT * FROM writer_slot").fetchall()
                for slot in slots:
                    if slot["slot"] != 1 or slot["state"] not in {"held", "unknown"}:
                        raise StoreCorrupt("writer slot is malformed")
                    task = db.execute("SELECT * FROM tasks WHERE task_id=?", (slot["task_id"],)).fetchone()
                    if task is None or self._decode_row(task).identity.attempt_id != slot["attempt_id"]:
                        raise StoreCorrupt("writer slot references missing or mismatched task")
                token = secrets.token_urlsafe(32)
                now = time.time()
                if run_id is not None:
                    db.execute("INSERT INTO start_request_bindings VALUES (?, ?, ?, ?)",
                               (session_id, request_id, run_id, turn_epoch))
                if pending or active or slots:
                    result = {"status": "denied", "error_code": "start_busy"}
                    encoded, _ = self._encode_start_result(result, force_unknown=False)
                    db.execute("INSERT INTO start_requests VALUES (?, ?, ?, ?, 'rejected', ?, ?, ?)",
                               (session_id, request_id, request_digest, token, encoded, now, now))
                    db.commit()
                    return StartRequestClaim(session_id, request_id, request_digest, "rejected", None, result)
                db.execute("INSERT INTO start_requests VALUES (?, ?, ?, ?, 'claimed', NULL, ?, ?)",
                           (session_id, request_id, request_digest, token, now, now))
                db.commit()
                return StartRequestClaim(session_id, request_id, request_digest, "claimed", token, None)
            except Exception:
                db.rollback()
                raise


    def consume_start_request_claim(self, claim: StartRequestClaim, *, session_id: str,
                                    run_id: str, turn_epoch: int) -> None:
        """Atomically authorize exactly one pre-side-effect dispatch for a live claim.

        This is deliberately a consume operation, not a read/check API: BEGIN
        IMMEDIATE serializes competing Hosts and the durable claimed->dispatching
        transition prevents stale/replayed claims from passing a check/use gap.
        The run/epoch binding must have been persisted at reservation time.
        """
        if (type(claim) is not StartRequestClaim or claim.status != "claimed"
                or type(claim.claim_token) is not str or not claim.claim_token
                or claim.session_id != session_id
                or type(session_id) is not str or not session_id
                or type(run_id) is not str or not run_id or len(run_id) > 256
                or type(turn_epoch) is not int or turn_epoch < 0):
            raise StoreOwnershipError("a current claim and matching authenticated run context are required")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT * FROM start_requests WHERE session_id=? AND request_id=?",
                                 (claim.session_id, claim.request_id)).fetchone()
                if row is None:
                    raise StoreOwnershipError("start request claim is stale or unknown")
                current = self._decode_start_request(row)
                binding = db.execute(
                    "SELECT run_id, turn_epoch FROM start_request_bindings WHERE session_id=? AND request_id=?",
                    (claim.session_id, claim.request_id),
                ).fetchone()
                if (row["claim_token"] != claim.claim_token
                        or current.request_digest != claim.request_digest
                        or row["state"] != "claimed"
                        or binding is None
                        or binding["run_id"] != run_id
                        or binding["turn_epoch"] != turn_epoch):
                    raise StoreOwnershipError("start request claim no longer owns this run epoch")
                changed = db.execute(
                    "UPDATE start_requests SET state='dispatching', updated_at=? "
                    "WHERE session_id=? AND request_id=? AND claim_token=? AND state='claimed'",
                    (time.time(), claim.session_id, claim.request_id, claim.claim_token),
                )
                if changed.rowcount != 1:
                    raise StoreOwnershipError("start request claim changed after dispatch authorization")
                db.commit()
            except Exception:
                db.rollback()
                raise

    def finish_start_request(self, claim: StartRequestClaim, result: dict[str, Any]) -> None:
        """Persist a definitive original Host response; identical retries are safe."""
        self._finish_start_request(claim, result, force_unknown=False)

    def reject_start_request_after_dispatch(self, claim: StartRequestClaim,
                                             result: dict[str, Any]) -> None:
        """Reject a reservation only while its claim is still unconsumed."""
        if type(result) is not dict or result.get("status") != "denied":
            raise ValueError("pre-dispatch rejection requires status='denied'")
        self._finish_start_request(
            claim, result, force_unknown=False, allowed_states=frozenset({"claimed"}),
        )

    def mark_start_request_unknown(self, claim: StartRequestClaim, result: dict[str, Any]) -> None:
        """Persist an ambiguous outcome that must never be automatically replayed."""
        if type(result) is not dict or result.get("status") != "unknown":
            raise ValueError("unknown start outcome requires status='unknown'")
        self._finish_start_request(claim, result, force_unknown=True)

    def mark_unconsumed_start_request_unknown(self, claim: StartRequestClaim,
                                              result: dict[str, Any]) -> None:
        """Mark only an unconsumed claim unknown; never overwrite a concurrent dispatch."""
        if type(result) is not dict or result.get("status") != "unknown":
            raise ValueError("unknown start outcome requires status='unknown'")
        self._finish_start_request(
            claim, result, force_unknown=True, allowed_states=frozenset({"claimed"}),
        )

    def _finish_start_request(self, claim: StartRequestClaim, result: dict[str, Any], *, force_unknown: bool,
                              allowed_states: frozenset[str] | None = None) -> None:
        if type(claim) is not StartRequestClaim or not claim.claim_token:
            raise StoreOwnershipError("the original reservation claim is required")
        payload, state = self._encode_start_result(result, force_unknown=force_unknown)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT * FROM start_requests WHERE session_id=? AND request_id=?",
                                 (claim.session_id, claim.request_id)).fetchone()
                if row is None:
                    raise StoreCorrupt("start request reservation disappeared")
                current = self._decode_start_request(row)
                if (row["claim_token"] != claim.claim_token or current.request_digest != claim.request_digest):
                    raise StoreOwnershipError("reservation claim does not own this request")
                if allowed_states is not None and row["state"] not in allowed_states:
                    raise StoreOwnershipError("start request claim was consumed after pre-dispatch completion")
                if row["state"] in {"completed", "rejected", "unknown"}:
                    if current.result != json.loads(payload):
                        raise StoreConflict("start request result is immutable")
                    db.commit()
                    return
                changed = db.execute("UPDATE start_requests SET state=?, result_json=?, updated_at=? "
                                     "WHERE session_id=? AND request_id=? AND claim_token=? "
                                     "AND state IN ('claimed','dispatching')",
                                     (state, payload, time.time(), claim.session_id, claim.request_id, claim.claim_token))
                if changed.rowcount != 1:
                    raise StoreConflict("start request reservation changed concurrently")
                db.commit()
            except Exception:
                db.rollback()
                raise

    @staticmethod
    def _encode_start_result(result: dict[str, Any], *, force_unknown: bool) -> tuple[str, str]:
        if type(result) is not dict or set(result) - _START_RESULT_KEYS:
            raise ValueError("start result contains unsupported fields")
        status = result.get("status")
        if status not in {"started", "denied", "blocked", "unavailable", "unknown"}:
            raise ValueError("invalid start result status")
        if force_unknown and status != "unknown":
            raise ValueError("ambiguous start outcome must be unknown")
        for key, value in result.items():
            if key == "status":
                continue
            if key in {"worker_bound", "os_sandbox", "merge_authorized"}:
                if type(value) is not bool:
                    raise ValueError("start result flags must be booleans")
            elif type(value) is not str or not value or len(value) > 256:
                raise ValueError("start result identifiers/codes must be bounded strings")
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > 16_384:
            raise ValueError("start result exceeds the persistence limit")
        if force_unknown or status == "unknown":
            state = "unknown"
        elif status == "started":
            state = "completed"
        else:
            state = "rejected"
        return encoded, state

    @classmethod
    def _decode_start_request(cls, row: sqlite3.Row) -> StartRequestClaim:
        try:
            session_id, request_id = row["session_id"], row["request_id"]
            digest, token, state = row["request_digest"], row["claim_token"], row["state"]
            if (type(session_id) is not str or not session_id or len(session_id) > 256
                    or type(request_id) is not str or not _REQUEST_ID_RE.fullmatch(request_id)
                    or type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    or type(token) is not str or len(token) < 32
                    or state not in {"claimed", "dispatching", "completed", "rejected", "unknown"}):
                raise ValueError("invalid reservation fields")
            raw_result = row["result_json"]
            result = json.loads(raw_result) if raw_result is not None else None
            if state in {"completed", "rejected", "unknown"}:
                encoded, expected_state = cls._encode_start_result(result, force_unknown=(state == "unknown"))
                if encoded != raw_result or expected_state != state:
                    raise ValueError("result/state mismatch")
                public_status = "completed" if state == "completed" else state
                return StartRequestClaim(session_id, request_id, digest, public_status, None, result)
            if raw_result is not None:
                raise ValueError("nonterminal reservation contains a result")
            return StartRequestClaim(session_id, request_id, digest, "in_progress", None, None)
        except Exception as exc:
            if isinstance(exc, StoreCorrupt):
                raise
            raise StoreCorrupt("invalid persisted self-evolution start request") from exc


    def create_task(self, identity: EvolutionIdentity, snapshot: EvolutionSnapshot) -> EvolutionSnapshot:
        self._check_binding(identity, snapshot)
        if snapshot.snapshot_revision != 1 or snapshot.phase != "preparing":
            raise StoreConflict("new tasks must begin at revision 1 in preparing")
        payload = self._encode(snapshot)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (identity.task_id, identity.session_id, identity.attempt_id, identity.run_id,
                            identity.turn_epoch, 1, snapshot.phase, payload))
                db.execute("INSERT INTO event_watermarks VALUES (?, ?, 0)", (identity.task_id, identity.attempt_id))
                db.commit()
            except sqlite3.IntegrityError as exc:
                db.rollback()
                row = db.execute("SELECT * FROM tasks WHERE task_id=?", (identity.task_id,)).fetchone()
                if row:
                    self._decode_row(row)  # A malformed row is corruption, never an absent task.
                    raise StoreConflict("task_id already exists; refusing replacement") from exc
                raise StoreCorrupt("database integrity constraint failed") from exc
        return snapshot

    def create_task_with_writer(self, identity: EvolutionIdentity, snapshot: EvolutionSnapshot,
                                owner_id: str, *,
                                start_request_claim: StartRequestClaim | None = None) -> EvolutionSnapshot:
        """Atomically persist task/writer; any supplied start claim must already be consumed."""
        self._check_binding(identity, snapshot)
        if snapshot.snapshot_revision != 1 or snapshot.phase != "preparing":
            raise StoreConflict("new tasks must begin at revision 1 in preparing")
        if type(owner_id) is not str or not owner_id.strip():
            raise StoreOwnershipError("owner_id is required")
        payload = self._encode(snapshot)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                # An unresolved foreign start may still own a live process or
                # may have crashed between candidate creation and writer claim.
                # Never grant another writer on the basis of elapsed time/PID.
                pending = db.execute(
                    "SELECT * FROM start_requests WHERE state IN ('claimed','dispatching','unknown')"
                ).fetchall()
                for row in pending:
                    self._decode_start_request(row)
                    is_own_claim = (
                        type(start_request_claim) is StartRequestClaim
                        and start_request_claim.claim_token
                        and row["session_id"] == start_request_claim.session_id
                        and row["request_id"] == start_request_claim.request_id
                        and row["request_digest"] == start_request_claim.request_digest
                        and row["claim_token"] == start_request_claim.claim_token
                        and row["state"] in {"claimed", "dispatching"}
                    )
                    if not is_own_claim:
                        raise StoreOwnershipError("another start request has an unresolved owner")
                unresolved = db.execute(
                    "SELECT * FROM tasks WHERE phase NOT IN ('awaiting_review','cancelled','failed','merged')"
                ).fetchall()
                for previous in unresolved:
                    self._decode_row(previous)
                if unresolved:
                    raise StoreOwnershipError("another task has an unresolved owner")
                request_task_binding = None
                if start_request_claim is not None:
                    if type(start_request_claim) is not StartRequestClaim or not start_request_claim.claim_token:
                        raise StoreOwnershipError("valid original start request claim is required")
                    reservation = db.execute(
                        "SELECT * FROM start_requests WHERE session_id=? AND request_id=?",
                        (start_request_claim.session_id, start_request_claim.request_id),
                    ).fetchone()
                    if reservation is None:
                        raise StoreCorrupt("start request reservation disappeared before task creation")
                    stored_claim = self._decode_start_request(reservation)
                    if (start_request_claim.status != "claimed"
                            or reservation["claim_token"] != start_request_claim.claim_token
                            or stored_claim.request_digest != start_request_claim.request_digest
                            or start_request_claim.session_id != identity.session_id
                            or reservation["state"] != "dispatching"):
                        raise StoreOwnershipError("start request claim is not the current consumed claim")
                    binding = db.execute(
                        "SELECT run_id, turn_epoch FROM start_request_bindings WHERE session_id=? AND request_id=?",
                        (start_request_claim.session_id, start_request_claim.request_id),
                    ).fetchone()
                    if binding is None:
                        raise StoreOwnershipError("a run-bound start request claim is required for this writer")
                    if (binding["run_id"] != identity.run_id
                            or binding["turn_epoch"] != identity.turn_epoch):
                        raise StoreOwnershipError("start request claim is bound to a different task epoch")
                    existing_task = db.execute(
                        "SELECT task_id FROM start_request_tasks WHERE session_id=? AND request_id=?",
                        (start_request_claim.session_id, start_request_claim.request_id),
                    ).fetchone()
                    if existing_task is not None:
                        raise StoreOwnershipError("start request claim is already bound to a task")
                    # Only an unbound task in this run epoch is ambiguous: a
                    # terminal task bound to another request does not belong to
                    # this claim. Check every matching task, not just the first.
                    legacy_task = db.execute(
                        "SELECT t.task_id FROM tasks AS t "
                        "WHERE t.session_id=? AND t.run_id=? AND t.turn_epoch=? "
                        "AND NOT EXISTS (SELECT 1 FROM start_request_tasks AS r "
                        "WHERE r.task_id=t.task_id AND r.session_id=t.session_id) LIMIT 1",
                        (identity.session_id, identity.run_id, identity.turn_epoch),
                    ).fetchone()
                    if legacy_task is not None:
                        raise StoreOwnershipError(
                            "run epoch already has a task without a request binding; refusing replay"
                        )
                    request_task_binding = (start_request_claim.session_id,
                                            start_request_claim.request_id, identity.task_id)
                db.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (identity.task_id, identity.session_id, identity.attempt_id, identity.run_id,
                            identity.turn_epoch, 1, snapshot.phase, payload))
                db.execute("INSERT INTO event_watermarks VALUES (?, ?, 1)", (identity.task_id, identity.attempt_id))
                db.execute("INSERT INTO writer_slot VALUES (1, ?, ?, ?, 'held')",
                           (owner_id, identity.task_id, identity.attempt_id))
                event_data = {
                    "session_id": identity.session_id, "task_id": identity.task_id,
                    "attempt_id": identity.attempt_id, "run_id": identity.run_id,
                    "turn_epoch": identity.turn_epoch, "sequence": 1,
                    "phase": "preparing", "kind": "phase_changed",
                    "fact_codes": ["task_accepted"], "snapshot_revision": 1,
                    "evidence_refs": [],
                }
                event_payload = json.dumps(event_data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                           (identity.task_id, identity.attempt_id, 1, identity.session_id, event_payload))
                if request_task_binding is not None:
                    db.execute("INSERT INTO start_request_tasks VALUES (?, ?, ?)", request_task_binding)
                db.commit()
            except sqlite3.IntegrityError as exc:
                db.rollback()
                row = db.execute("SELECT * FROM tasks WHERE task_id=?", (identity.task_id,)).fetchone()
                if row:
                    self._decode_row(row)
                    raise StoreConflict("task_id already exists; refusing replacement") from exc
                if db.execute("SELECT 1 FROM writer_slot WHERE slot=1").fetchone():
                    raise StoreOwnershipError("persistent writer slot is occupied; reconcile after retry") from exc
                raise StoreCorrupt("task creation or initial event integrity constraint failed") from exc
            except Exception:
                db.rollback()
                raise
        return snapshot

    def read_task(self, session_id: str, task_id: str) -> EvolutionSnapshot | None:
        with self._connection() as db:
            row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None or row["session_id"] != session_id:
            return None
        return self._decode_row(row)

    def compare_and_swap(self, task_id: str, expected_revision: int, snapshot: EvolutionSnapshot) -> EvolutionSnapshot:
        if type(expected_revision) is not int or expected_revision < 1:
            raise StoreConflict("expected_revision must be a positive integer")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
                if row is None:
                    raise StoreConflict("task not found")
                current = self._decode_row(row)
                self._check_binding(current.identity, snapshot)
                if current.snapshot_revision != expected_revision:
                    raise StoreConflict("stale snapshot revision")
                if snapshot.snapshot_revision != expected_revision + 1:
                    raise StoreConflict("new snapshot revision must increment exactly once")
                if snapshot.identity != current.identity:
                    raise StoreConflict("attempt identity changes require begin_attempt")
                if snapshot.phase != current.phase and snapshot.phase not in _ALLOWED[current.phase]:
                    raise StoreConflict(f"illegal phase transition: {current.phase} -> {snapshot.phase}")
                if snapshot.phase in _TERMINAL and db.execute("SELECT 1 FROM writer_slot WHERE task_id=?", (task_id,)).fetchone():
                    raise StoreConflict("terminal state requires the persistent writer to be released after confirmed stop")
                if snapshot.candidate_digest != current.candidate_digest and snapshot.change_summary is not None:
                    # A summary is candidate-specific; never carry prose across a digest change.
                    snapshot = replace(snapshot, change_summary=None)
                payload = self._encode(snapshot)
                updated = db.execute("UPDATE tasks SET revision=?, phase=?, payload=? WHERE task_id=? AND revision=?",
                                     (snapshot.snapshot_revision, snapshot.phase, payload, task_id, expected_revision))
                if updated.rowcount != 1:
                    raise StoreConflict("revision compare-and-swap lost")
                db.commit()
                return snapshot
            except Exception:
                db.rollback()
                raise

    def begin_attempt(self, task_id: str, expected_revision: int, identity: EvolutionIdentity,
                      snapshot: EvolutionSnapshot) -> EvolutionSnapshot:
        self._check_binding(identity, snapshot)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
                if row is None:
                    raise StoreConflict("task not found")
                current = self._decode_row(row)
                if current.snapshot_revision != expected_revision:
                    raise StoreConflict("stale snapshot revision")
                if current.phase == "merged":
                    raise StoreConflict("merged tasks are immutable and cannot be retried")
                if current.phase not in {"cancelled", "failed", "blocked", "awaiting_review"}:
                    raise StoreConflict("cannot start another attempt while prior attempt is active or unknown")
                if identity.task_id != task_id or identity.session_id != current.identity.session_id:
                    raise StoreConflict("attempt must retain exact session and task identity")
                if identity.attempt_id == current.identity.attempt_id or identity.run_id == current.identity.run_id:
                    raise StoreConflict("new attempt and run identities must be unique")
                if db.execute("SELECT 1 FROM writer_slot WHERE slot=1").fetchone():
                    raise StoreOwnershipError("cannot begin an attempt until the persistent writer slot is confirmed released")
                if snapshot.snapshot_revision != expected_revision + 1 or snapshot.phase != "preparing":
                    raise StoreConflict("new attempt must start preparing at next task revision")
                if snapshot.candidate_digest != current.candidate_digest and snapshot.change_summary is not None:
                    snapshot = replace(snapshot, change_summary=None)
                payload = self._encode(snapshot)
                db.execute("UPDATE tasks SET attempt_id=?, run_id=?, turn_epoch=?, revision=?, phase=?, payload=? WHERE task_id=? AND revision=?",
                           (identity.attempt_id, identity.run_id, identity.turn_epoch, snapshot.snapshot_revision,
                            snapshot.phase, payload, task_id, expected_revision))
                db.execute("INSERT INTO event_watermarks VALUES (?, ?, 0)", (task_id, identity.attempt_id))
                db.commit()
                return snapshot
            except Exception:
                db.rollback()
                raise

    def commit_transition(
        self,
        identity: EvolutionIdentity,
        expected_revision: int,
        snapshot: EvolutionSnapshot,
        event: EvolutionEvent,
        *,
        release_owner_id: str | None = None,
        stop_confirmed: bool = False,
        summary_candidate_digest: str | None = None,
        quarantine_owner_id: str | None = None,
    ) -> EvolutionSnapshot:
        """Atomically persist one identity-bound snapshot transition and its fact.

        The revision and sequence are both preconditions: concurrent or stale
        writers cannot publish a snapshot without the corresponding event, or
        advance the event watermark independently of that snapshot.
        """
        if type(expected_revision) is not int or expected_revision < 1:
            raise StoreConflict("expected_revision must be a positive integer")
        if not isinstance(event, EvolutionEvent):
            raise StoreConflict("event must be an EvolutionEvent")
        self._check_binding(identity, snapshot)
        if snapshot.identity != identity:
            raise StoreConflict("snapshot identity does not match requested identity")
        if event.identity != identity:
            raise StoreConflict("event identity does not match requested identity")
        if event.phase != snapshot.phase or event.snapshot_revision != snapshot.snapshot_revision:
            raise StoreConflict("event facts must describe the committed snapshot")

        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT * FROM tasks WHERE task_id=?", (identity.task_id,)).fetchone()
                if row is None:
                    raise StoreConflict("task not found")
                current = self._decode_row(row)
                if current.identity != identity:
                    raise StoreConflict("transition belongs to a stale or foreign identity")
                if current.snapshot_revision != expected_revision:
                    raise StoreConflict("stale snapshot revision")
                if snapshot.snapshot_revision != expected_revision + 1:
                    raise StoreConflict("new snapshot revision must increment exactly once")
                if snapshot.phase != current.phase and snapshot.phase not in _ALLOWED[current.phase]:
                    raise StoreConflict(f"illegal phase transition: {current.phase} -> {snapshot.phase}")
                slot = db.execute("SELECT * FROM writer_slot WHERE slot=1").fetchone()
                if release_owner_id is not None and quarantine_owner_id is not None:
                    raise StoreOwnershipError("a transition cannot both release and quarantine its writer")
                if release_owner_id is not None:
                    if (stop_confirmed is not True or slot is None or slot["owner_id"] != release_owner_id
                            or slot["task_id"] != identity.task_id or slot["attempt_id"] != identity.attempt_id
                            or slot["state"] != "held"):
                        raise StoreOwnershipError("exact held writer and confirmed stop are required for atomic release")
                elif quarantine_owner_id is not None:
                    if (stop_confirmed is True or snapshot.phase != "unknown" or slot is None
                            or slot["owner_id"] != quarantine_owner_id or slot["task_id"] != identity.task_id
                            or slot["attempt_id"] != identity.attempt_id or slot["state"] != "held"):
                        raise StoreOwnershipError("exact held writer is required to quarantine an unknown transition")
                elif stop_confirmed is True:
                    raise StoreOwnershipError("release owner is required for confirmed stop")
                if snapshot.phase in _TERMINAL and slot is not None and release_owner_id is None:
                    raise StoreConflict("terminal state requires the persistent writer to be released after confirmed stop")
                if snapshot.candidate_digest != current.candidate_digest and snapshot.change_summary is not None:
                    # A summary is candidate-specific. Retain one across a digest
                    # change only when the trusted caller explicitly binds its newly
                    # authored summary to the new digest.
                    if summary_candidate_digest != snapshot.candidate_digest:
                        snapshot = replace(snapshot, change_summary=None)
                elif summary_candidate_digest is not None and summary_candidate_digest != snapshot.candidate_digest:
                    raise StoreConflict("summary digest does not match candidate")

                watermark = db.execute(
                    "SELECT sequence FROM event_watermarks WHERE task_id=? AND attempt_id=?",
                    (identity.task_id, identity.attempt_id),
                ).fetchone()
                if watermark is None:
                    raise StoreCorrupt("missing event watermark")
                if event.sequence != watermark["sequence"] + 1:
                    raise StoreConflict("event sequence must advance exactly once")
                if event.phase != snapshot.phase or event.snapshot_revision != snapshot.snapshot_revision:
                    raise StoreConflict("event facts do not match normalized snapshot")

                updated = db.execute(
                    "UPDATE tasks SET revision=?, phase=?, payload=? WHERE task_id=? AND revision=?",
                    (snapshot.snapshot_revision, snapshot.phase, self._encode(snapshot),
                     identity.task_id, expected_revision),
                )
                if updated.rowcount != 1:
                    raise StoreConflict("revision compare-and-swap lost")
                event_data = {
                    "session_id": identity.session_id, "task_id": identity.task_id,
                    "attempt_id": identity.attempt_id, "run_id": identity.run_id,
                    "turn_epoch": identity.turn_epoch, "sequence": event.sequence,
                    "phase": event.phase, "kind": event.kind,
                    "fact_codes": list(event.fact_codes),
                    "snapshot_revision": event.snapshot_revision,
                    "evidence_refs": list(event.evidence_refs),
                }
                payload = json.dumps(event_data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                           (identity.task_id, identity.attempt_id, event.sequence, identity.session_id, payload))
                advanced = db.execute(
                    "UPDATE event_watermarks SET sequence=? WHERE task_id=? AND attempt_id=? AND sequence=?",
                    (event.sequence, identity.task_id, identity.attempt_id, watermark["sequence"]),
                )
                if advanced.rowcount != 1:
                    raise StoreConflict("event sequence compare-and-swap lost")
                if release_owner_id is not None:
                    deleted = db.execute("DELETE FROM writer_slot WHERE slot=1 AND owner_id=? AND task_id=? AND attempt_id=?",
                                         (release_owner_id, identity.task_id, identity.attempt_id))
                    if deleted.rowcount != 1:
                        raise StoreOwnershipError("writer release lost its exact owner binding")
                elif quarantine_owner_id is not None:
                    quarantined = db.execute("UPDATE writer_slot SET state='unknown' WHERE slot=1 AND owner_id=? AND task_id=? AND attempt_id=? AND state='held'",
                                              (quarantine_owner_id, identity.task_id, identity.attempt_id))
                    if quarantined.rowcount != 1:
                        raise StoreOwnershipError("writer quarantine lost its exact owner binding")
                db.execute("DELETE FROM events WHERE task_id=? AND attempt_id=? AND sequence<=?",
                           (identity.task_id, identity.attempt_id, event.sequence - _MAX_EVENTS_PER_ATTEMPT))
                db.commit()
                return snapshot
            except Exception:
                db.rollback()
                raise

    def accept_sequence(self, identity: EvolutionIdentity, sequence: int) -> bool:
        """Compatibility check only; a watermark can advance only with its event."""
        if type(sequence) is not int or sequence < 1:
            raise StoreConflict("sequence must be a positive integer")
        with self._connection() as db:
            row = db.execute("SELECT * FROM tasks WHERE task_id=?", (identity.task_id,)).fetchone()
            if row is None:
                raise StoreConflict("task not found")
            current = self._decode_row(row)
            if current.identity != identity:
                raise StoreConflict("event belongs to a stale or foreign identity")
            watermark = db.execute("SELECT sequence FROM event_watermarks WHERE task_id=? AND attempt_id=?",
                                   (identity.task_id, identity.attempt_id)).fetchone()
            if watermark is None:
                raise StoreCorrupt("missing event watermark")
            if sequence <= watermark["sequence"]:
                return False
            raise StoreConflict("event watermark requires its atomically persisted event")

    def append_event(self, event: EvolutionEvent) -> bool:
        """Persist a bounded, identity-bound event; duplicates are idempotently rejected."""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tasks WHERE task_id=?", (event.identity.task_id,)).fetchone()
            if row is None:
                db.rollback()
                raise StoreConflict("task not found")
            current = self._decode_row(row)
            if current.identity != event.identity:
                db.rollback()
                raise StoreConflict("event belongs to stale or foreign identity")
            if current.snapshot_revision != event.snapshot_revision or current.phase != event.phase:
                db.rollback()
                raise StoreConflict("event does not describe the current persisted snapshot")
            watermark = db.execute("SELECT sequence FROM event_watermarks WHERE task_id=? AND attempt_id=?",
                                   (event.identity.task_id, event.identity.attempt_id)).fetchone()
            if watermark is None:
                db.rollback()
                raise StoreCorrupt("missing event watermark")
            if event.sequence <= watermark["sequence"]:
                db.rollback()
                return False
            if event.sequence != watermark["sequence"] + 1:
                db.rollback()
                raise StoreConflict("event sequence must advance exactly once")
            event_data = {"session_id": event.identity.session_id, "task_id": event.identity.task_id,
                          "attempt_id": event.identity.attempt_id, "run_id": event.identity.run_id,
                          "turn_epoch": event.identity.turn_epoch, "sequence": event.sequence,
                          "phase": event.phase, "kind": event.kind, "fact_codes": list(event.fact_codes),
                          "snapshot_revision": event.snapshot_revision, "evidence_refs": list(event.evidence_refs)}
            payload = json.dumps(event_data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                       (event.identity.task_id, event.identity.attempt_id, event.sequence,
                        event.identity.session_id, payload))
            db.execute("UPDATE event_watermarks SET sequence=? WHERE task_id=? AND attempt_id=?",
                       (event.sequence, event.identity.task_id, event.identity.attempt_id))
            db.execute("DELETE FROM events WHERE task_id=? AND attempt_id=? AND sequence<=?",
                       (event.identity.task_id, event.identity.attempt_id, event.sequence - _MAX_EVENTS_PER_ATTEMPT))
            db.commit()
            return True

    def read_events(self, session_id: str, task_id: str, attempt_id: str, *, after_sequence: int = 0) -> tuple[dict[str, Any], ...]:
        if type(after_sequence) is not int or after_sequence < 0:
            raise StoreConflict("after_sequence must be a non-negative integer")
        with self._connection() as db:
            rows = db.execute("SELECT payload FROM events WHERE session_id=? AND task_id=? AND attempt_id=? AND sequence>? ORDER BY sequence",
                              (session_id, task_id, attempt_id, after_sequence)).fetchall()
        return tuple(json.loads(row["payload"]) for row in rows)

    def read_event_page(self, session_id: str, task_id: str, attempt_id: str,
                        *, after_sequence: int = 0) -> EventPage:
        """Read the retained event window and report any missing history honestly."""
        if type(after_sequence) is not int or after_sequence < 0:
            raise StoreConflict("after_sequence must be a non-negative integer")
        with self._connection() as db:
            task = db.execute("SELECT session_id FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if task is None or task["session_id"] != session_id:
                return EventPage((), None, None, None, False, False)
            watermark = db.execute(
                "SELECT sequence FROM event_watermarks WHERE task_id=? AND attempt_id=?",
                (task_id, attempt_id),
            ).fetchone()
            if watermark is None:
                raise StoreCorrupt("missing event watermark")
            rows = db.execute(
                "SELECT sequence, payload FROM events WHERE session_id=? AND task_id=? AND attempt_id=? ORDER BY sequence",
                (session_id, task_id, attempt_id),
            ).fetchall()
        try:
            all_events = tuple(json.loads(row["payload"]) for row in rows)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StoreCorrupt("persisted event payload is malformed") from exc
        sequences = tuple(row["sequence"] for row in rows)
        if any(type(sequence) is not int or sequence < 1 for sequence in sequences):
            raise StoreCorrupt("persisted event sequence is malformed")
        earliest = sequences[0] if sequences else None
        latest = sequences[-1] if sequences else None
        stored_watermark = watermark["sequence"]
        retained_truncated = stored_watermark > 0 and (earliest is None or earliest > 1)
        events = tuple(event for event in all_events if event["sequence"] > after_sequence)
        expected_start = after_sequence + 1
        cursor_gap = after_sequence > stored_watermark
        prefix_gap = (expected_start < stored_watermark + 1 and earliest is not None
                      and expected_start < earliest)
        sequence_gap = any(right != left + 1 for left, right in zip(sequences, sequences[1:]))
        watermark_gap = latest != stored_watermark if stored_watermark > 0 else latest is not None
        has_gap = cursor_gap or prefix_gap or sequence_gap or watermark_gap
        return EventPage(events, earliest, latest, stored_watermark, retained_truncated, has_gap)

    def list_writer_slots(self) -> tuple[dict[str, str], ...]:
        """Enumerate all durable writer ownership records without treating age as death."""
        with self._connection() as db:
            rows = db.execute("SELECT slot, owner_id, task_id, attempt_id, state FROM writer_slot ORDER BY slot").fetchall()
            tasks = {row["task_id"]: row for row in db.execute("SELECT * FROM tasks").fetchall()}
        result = []
        for row in rows:
            if (row["slot"] != 1 or row["state"] not in {"held", "unknown"}
                    or not all(type(row[key]) is str and row[key] for key in ("owner_id", "task_id", "attempt_id"))):
                raise StoreCorrupt("legacy writer slot is malformed")
            task = tasks.get(row["task_id"])
            if task is None:
                raise StoreCorrupt("writer slot references missing task")
            snapshot = self._decode_row(task)
            if snapshot.identity.attempt_id != row["attempt_id"]:
                raise StoreCorrupt("writer slot attempt does not match task")
            result.append({key: row[key] for key in ("owner_id", "task_id", "attempt_id", "state")})
        return tuple(result)

    def reconcile_startup_writers(self) -> tuple[str, ...]:
        """Fail closed on unresolved state without mutating another Host's work.

        Opening a second Host, a PID, and elapsed time do not prove an owner has
        stopped. Even a crashed process may have surviving descendants. Until a
        separate trusted recovery mechanism exists, retain every row unchanged;
        callers must gate all mutating operations on StoreOwnershipError.
        """
        with self._connection() as db:
            db.execute("BEGIN")
            try:
                slots = db.execute("SELECT * FROM writer_slot ORDER BY slot").fetchall()
                for slot in slots:
                    if slot["slot"] != 1 or slot["state"] not in {"held", "unknown"}:
                        raise StoreCorrupt("writer slot is malformed")
                    task = db.execute("SELECT * FROM tasks WHERE task_id=?", (slot["task_id"],)).fetchone()
                    if task is None:
                        raise StoreCorrupt("writer slot references missing task")
                    snapshot = self._decode_row(task)
                    if snapshot.identity.attempt_id != slot["attempt_id"]:
                        raise StoreCorrupt("writer slot attempt does not match task")
                active = db.execute(
                    "SELECT * FROM tasks WHERE phase NOT IN ('awaiting_review','cancelled','failed','merged')"
                ).fetchall()
                for task in active:
                    self._decode_row(task)
                pending = db.execute(
                    "SELECT * FROM start_requests WHERE state IN ('claimed','dispatching','unknown')"
                ).fetchall()
                for request in pending:
                    self._decode_start_request(request)
                if slots or active or pending:
                    raise StoreOwnershipError(
                        "unresolved writer, task or start request; owner liveness is unconfirmed"
                    )
                db.commit()
                return ()
            except Exception:
                db.rollback()
                raise

    def claim_writer(self, owner_id: str, task_id: str, attempt_id: str) -> None:
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise StoreOwnershipError("owner_id is required")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                db.rollback()
                raise StoreOwnershipError("writer must reference an existing task")
            snap = self._decode_row(row)
            if snap.identity.attempt_id != attempt_id or snap.phase in _TERMINAL or snap.phase == "unknown":
                db.rollback()
                raise StoreOwnershipError("attempt is not eligible for writing")
            if snap.phase == "cancelling":
                db.rollback()
                raise StoreOwnershipError("cancelling attempt cannot acquire write ownership")
            try:
                db.execute("INSERT INTO writer_slot VALUES (1, ?, ?, ?, 'held')", (owner_id, task_id, attempt_id))
                db.commit()
            except sqlite3.IntegrityError as exc:
                db.rollback()
                raise StoreOwnershipError("persistent writer slot is occupied; reconcile after retry") from exc

    def mark_task_unknown(self, task_id: str, code: str) -> EvolutionSnapshot:
        """Atomically quarantine an active task when its process owner is unconfirmed."""
        if type(code) is not str or not code.strip() or len(code) > 256:
            raise StoreConflict("unknown-state blocker code is invalid")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
                if row is None:
                    raise StoreConflict("task not found")
                current = self._decode_row(row)
                if current.phase == "unknown":
                    db.commit()
                    return current
                if current.phase in _TERMINAL:
                    db.commit()
                    return current
                changed = replace(current, phase="unknown", snapshot_revision=current.snapshot_revision + 1,
                                  blocker_code=code, error_code=code, allowed_next_actions=("get_snapshot",))
                watermark = db.execute("SELECT sequence FROM event_watermarks WHERE task_id=? AND attempt_id=?",
                                       (task_id, current.identity.attempt_id)).fetchone()
                if watermark is None:
                    raise StoreCorrupt("missing event watermark")
                sequence = watermark["sequence"] + 1
                event_data = {"session_id": current.identity.session_id, "task_id": task_id,
                              "attempt_id": current.identity.attempt_id, "run_id": current.identity.run_id,
                              "turn_epoch": current.identity.turn_epoch, "sequence": sequence,
                              "phase": "unknown", "kind": "task_terminal", "fact_codes": ["unknown"],
                              "snapshot_revision": changed.snapshot_revision, "evidence_refs": []}
                payload = json.dumps(event_data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                updated = db.execute("UPDATE tasks SET revision=?, phase='unknown', payload=? WHERE task_id=? AND revision=?",
                                     (changed.snapshot_revision, self._encode(changed), task_id, current.snapshot_revision))
                if updated.rowcount != 1:
                    raise StoreConflict("unknown-state snapshot compare-and-swap lost")
                db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                           (task_id, current.identity.attempt_id, sequence, current.identity.session_id, payload))
                advanced = db.execute("UPDATE event_watermarks SET sequence=? WHERE task_id=? AND attempt_id=? AND sequence=?",
                                      (sequence, task_id, current.identity.attempt_id, watermark["sequence"]))
                if advanced.rowcount != 1:
                    raise StoreConflict("unknown-state event sequence compare-and-swap lost")
                db.execute("UPDATE writer_slot SET state='unknown' WHERE slot=1 AND task_id=?", (task_id,))
                db.execute("DELETE FROM events WHERE task_id=? AND attempt_id=? AND sequence<=?",
                           (task_id, current.identity.attempt_id, sequence - _MAX_EVENTS_PER_ATTEMPT))
                db.commit()
                return changed
            except Exception:
                db.rollback()
                raise

    def mark_orphaned_unknown(self, owner_id: str) -> tuple[str, ...]:
        """Explicitly record host-confirmed loss; does not infer from timeout."""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            slots = db.execute("SELECT * FROM writer_slot WHERE owner_id=?", (owner_id,)).fetchall()
            tasks: list[str] = []
            for slot in slots:
                row = db.execute("SELECT * FROM tasks WHERE task_id=?", (slot["task_id"],)).fetchone()
                if row is None:
                    db.rollback()
                    raise StoreCorrupt("writer slot references missing task")
                current = self._decode_row(row)
                if current.identity.attempt_id != slot["attempt_id"]:
                    db.rollback()
                    raise StoreCorrupt("writer slot attempt does not match task")
                if current.phase != "unknown":
                    revision = current.snapshot_revision + 1
                    changed = replace(current, phase="unknown", snapshot_revision=revision,
                                      blocker_code="writer_owner_unconfirmed")
                    updated = db.execute(
                        "UPDATE tasks SET revision=?, phase='unknown', payload=? WHERE task_id=? AND revision=?",
                        (revision, self._encode(changed), slot["task_id"], current.snapshot_revision),
                    )
                    if updated.rowcount != 1:
                        raise StoreConflict("orphan reconciliation lost snapshot revision")
                    watermark = db.execute(
                        "SELECT sequence FROM event_watermarks WHERE task_id=? AND attempt_id=?",
                        (slot["task_id"], slot["attempt_id"]),
                    ).fetchone()
                    if watermark is None:
                        raise StoreCorrupt("missing event watermark")
                    sequence = watermark["sequence"] + 1
                    event_data = {
                        "session_id": current.identity.session_id, "task_id": current.identity.task_id,
                        "attempt_id": current.identity.attempt_id, "run_id": current.identity.run_id,
                        "turn_epoch": current.identity.turn_epoch, "sequence": sequence,
                        "phase": "unknown", "kind": "task_terminal", "fact_codes": ["unknown"],
                        "snapshot_revision": revision, "evidence_refs": [],
                    }
                    event_payload = json.dumps(event_data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                               (slot["task_id"], slot["attempt_id"], sequence,
                                current.identity.session_id, event_payload))
                    advanced = db.execute(
                        "UPDATE event_watermarks SET sequence=? WHERE task_id=? AND attempt_id=? AND sequence=?",
                        (sequence, slot["task_id"], slot["attempt_id"], watermark["sequence"]),
                    )
                    if advanced.rowcount != 1:
                        raise StoreConflict("orphan reconciliation lost event sequence")
                    db.execute("DELETE FROM events WHERE task_id=? AND attempt_id=? AND sequence<=?",
                               (slot["task_id"], slot["attempt_id"], sequence - _MAX_EVENTS_PER_ATTEMPT))
                db.execute("UPDATE writer_slot SET state='unknown' WHERE slot=1")
                tasks.append(slot["task_id"])
            db.commit()
            return tuple(tasks)

    def release_writer(self, owner_id: str, *, stop_confirmed: bool) -> None:
        if stop_confirmed is not True:
            raise StoreOwnershipError("writer can only be released after stop is positively confirmed")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM writer_slot WHERE slot=1").fetchone()
            if row is None:
                db.rollback()
                raise StoreOwnershipError("writer slot is not held")
            if row["owner_id"] != owner_id:
                db.rollback()
                raise StoreOwnershipError("only the exact owner may release its slot")
            if row["state"] != "held":
                db.rollback()
                raise StoreOwnershipError("unknown writer ownership cannot be released without reconciliation")
            db.execute("DELETE FROM writer_slot WHERE slot=1")
            db.commit()

    @staticmethod
    def _check_binding(identity: EvolutionIdentity, snapshot: EvolutionSnapshot) -> None:
        if not isinstance(identity, EvolutionIdentity) or not isinstance(snapshot, EvolutionSnapshot):
            raise StoreConflict("identity/snapshot must be typed contract records")
        if identity != snapshot.identity:
            raise StoreConflict("snapshot identity does not match requested identity")
        candidate = snapshot.candidate
        if (candidate is not None and candidate.candidate_kind == "materialized_snapshot"
                and candidate.candidate_id != identity.task_id):
            raise StoreConflict("materialized snapshot candidate must belong to the persisted task")
        for evidence in snapshot.evidence:
            if snapshot.candidate_digest is None or evidence.candidate_digest != snapshot.candidate_digest:
                raise StoreConflict("verification evidence is not bound to the current candidate digest")

    @staticmethod
    def _encode(snapshot: EvolutionSnapshot) -> str:
        return json.dumps(_snapshot_dict(snapshot), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _decode_row(cls, row: sqlite3.Row) -> EvolutionSnapshot:
        try:
            snap = _snapshot_from(json.loads(row["payload"]))
            if (snap.identity.task_id != row["task_id"] or snap.identity.session_id != row["session_id"]
                    or snap.identity.attempt_id != row["attempt_id"] or snap.identity.run_id != row["run_id"]
                    or snap.identity.turn_epoch != row["turn_epoch"] or snap.snapshot_revision != row["revision"]
                    or snap.phase != row["phase"]):
                raise ValueError("indexed identity/revision does not match payload")
            return snap
        except Exception as exc:
            if isinstance(exc, StoreCorrupt):
                raise
            raise StoreCorrupt(f"invalid persisted task record: {row['task_id']}") from exc
