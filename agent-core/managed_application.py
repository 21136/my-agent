"""Closed managed sessions and durable lifecycle over the fenced ManagedStore.

Application records never enter the legacy session/model/tool dispatch path.
The process audit policy is logical separation for trusted code, not an OS sandbox.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from managed_readonly import ManagedReadonlyAuditGuard, ManagedReadonlyContext
from managed_store import ManagedStore, ManagedStoreError, MaintenanceRequired

MAX_MESSAGE_BYTES = 64 * 1024
MAX_EVENTS = 8192
MAX_LOG_BYTES = 32 * 1024 * 1024


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def valid_uuid(value: Any) -> bool:
    try:
        return type(value) is str and str(uuid.UUID(value)) == value and uuid.UUID(value).version in range(1, 9)
    except (ValueError, AttributeError):
        return False


def valid_hash(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def bounded_text(value: Any, limit: int) -> bool:
    return type(value) is str and len(value.encode("utf-8")) <= limit


@dataclass(frozen=True, slots=True)
class ManagedActivationContext:
    """Identity established by the validated fenced controller path."""
    origin: str
    operation_id: str
    nonce: str
    runtime_digest: str
    host_nonce: str
    profile_intent_digest: str
    namespace_root: str
    namespace_digest: str
    installation_id: str
    data_store_id: str
    release_id: str
    binding_digest: str
    access: str


class ManagedApplicationAuditGuard(ManagedReadonlyAuditGuard):
    """Permit store IO only while the closed controller calls its fenced API."""
    def __init__(self, read_roots, store: ManagedStore):
        super().__init__(read_roots)
        self.store = store
        self._local = threading.local()

    @contextlib.contextmanager
    def store_io(self):
        previous = getattr(self._local, "authorized", False)
        self._local.authorized = True
        try:
            yield
        finally:
            self._local.authorized = previous

    def _store_target(self, value):
        if not isinstance(value, (str, bytes, os.PathLike)):
            return False
        raw = os.fsdecode(value)
        if raw.startswith("file:"):
            return raw.split("?", 1)[0] in {self.store.control_path.as_uri(), self.store.data_db_path.as_uri()}
        path = Path(raw)
        if not path.is_absolute() or any(part == ".." for part in path.parts):
            return False
        if path.parent == self.store.state_dir:
            return path.name in {"managed-store.lock", "managed-store-control.sqlite3", "managed-store-control.sqlite3-journal"}
        if path.parent == self.store.data_dir:
            return path.name in {"managed-data.sqlite3", "managed-data.sqlite3-journal", "application"}
        return path == self.store.data_dir / "application/events" or path.parent == self.store.data_dir / "application/events"

    def audit(self, event, event_args):
        args = event_args if isinstance(event_args, tuple) else (event_args,)
        authorized = getattr(self._local, "authorized", False)
        if event == "sqlite3.connect":
            if not authorized or not args or not self._store_target(args[0]):
                raise PermissionError("managed application denied unconverted database access")
            return
        writing = event in self._MUTATION_EVENTS or event == "_winapi.CreateFile"
        if event == "open":
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 and type(args[2]) is int else 0
            writing = (isinstance(mode, str) and any(char in mode for char in "wax+")) or bool(flags & self._WRITE_FLAGS)
        if writing and authorized:
            targets = args[:2] if event in {"os.rename", "os.link"} else args[:1]
            if targets and all(self._store_target(target) for target in targets):
                return
        super().audit(event, args)


class ManagedApplication:
    @classmethod
    def open(cls, store: ManagedStore, *, operation_id: str, nonce: str, runtime_digest: str,
             control_token: str, activation_context: ManagedActivationContext | None = None,
             acceptance_scope: str = "isolated_native_host", audit_guard=None):
        if (not valid_uuid(operation_id) or not valid_uuid(nonce) or not valid_hash(runtime_digest)
                or not valid_hash(control_token) or acceptance_scope not in {"isolated_native_host", "engineering_source_child"}):
            raise ManagedStoreError("managed application launch identity rejected")
        if type(activation_context) is not ManagedActivationContext:
            raise ManagedStoreError("managed application activation context is required")
        context_identity = (activation_context.operation_id, activation_context.nonce, activation_context.runtime_digest,
                            activation_context.namespace_root, activation_context.installation_id, activation_context.data_store_id,
                            activation_context.release_id, activation_context.binding_digest)
        expected_identity = (operation_id, nonce, runtime_digest, str(store.root), store.installation_id, store.data_store_id,
                             store.release_id, store.binding_digest)
        if (context_identity != expected_identity or not valid_uuid(activation_context.host_nonce)
                or not valid_hash(activation_context.profile_intent_digest) or not valid_hash(activation_context.namespace_digest)
                or activation_context.namespace_digest != store.binding["namespace"]["digest"]
                or activation_context.access != "fenced_application"
                or activation_context.origin not in {"validated_fenced_profile_intent_v1", "synthetic_fixture_only_v1"}):
            raise ManagedStoreError("managed application activation context binding rejected")
        self = cls()
        self.store, self.nonce, self.runtime_digest = store, nonce, runtime_digest
        self.activation_context = activation_context
        self._token, self.acceptance_scope, self.guard = control_token, acceptance_scope, audit_guard
        self.lease, self.closed, self.failed = None, False, False
        self._mutex = threading.RLock()
        try:
            with self._io():
                before = store.inspect()
                if before["state"] == "maintenance_required" or before.get("activeLease"):
                    raise MaintenanceRequired("managed application requires maintenance")
                if before["initialized"]:
                    preflight = store.application_snapshot()
                    self._load(preflight)
                    if not self.records or self.state != "clean_shutdown":
                        raise MaintenanceRequired("managed application has no completed clean shutdown")
                    last = self.records[-1]
                    matching = [receipt for receipt in self.releases if receipt["generation"] == last["generation"]
                                and receipt["revision"] == last["revision"]]
                    if (preflight["generation"] != last["generation"] or len(matching) != 1):
                        raise MaintenanceRequired("application cold generation or release receipt mismatch")
                    if acceptance_scope == "isolated_native_host" and (self.synthetic_lifecycle or self.unproven_lifecycle):
                        raise MaintenanceRequired("unproven legacy lifecycle cannot be promoted to production")
                if ((activation_context.origin == "validated_fenced_profile_intent_v1" and acceptance_scope != "isolated_native_host")
                        or (activation_context.origin == "synthetic_fixture_only_v1" and acceptance_scope != "engineering_source_child")):
                    raise ManagedStoreError("managed application activation origin does not match its acceptance scope")
                self.lease = store.acquire(operation_id)
                if not before["initialized"]:
                    if self.lease.generation != 1:
                        raise MaintenanceRequired("application initialization skipped an owner generation")
                    store.initialize(self.lease, str(uuid.uuid4()))
                self._load(store.application_snapshot(self.lease))
                if acceptance_scope == "isolated_native_host" and (self.synthetic_lifecycle or self.unproven_lifecycle):
                    raise MaintenanceRequired("unproven legacy lifecycle cannot be promoted to production")
                if before["initialized"] and (not self.records or self.state != "clean_shutdown"):
                    raise MaintenanceRequired("managed application has no completed clean shutdown")
                if self.records:
                    last = self.records[-1]
                    if self.lease.generation != last["generation"] + 1:
                        raise MaintenanceRequired("application reopen skipped an unrepresented owner generation")
                    matching = [receipt for receipt in self.releases if receipt["generation"] == last["generation"]
                                and receipt["revision"] == last["revision"]]
                    if len(matching) != 1:
                        raise MaintenanceRequired("clean shutdown release receipt mismatch")
                self._write("active_observing", operation_id, {
                    "activation_operation_id": activation_context.operation_id,
                    "active_release_id": activation_context.release_id,
                    "previous_shutdown_digest": self.latest_shutdown,
                    "access": activation_context.access,
                    "origin": activation_context.origin,
                    "host_nonce": activation_context.host_nonce,
                    "profile_intent_digest": activation_context.profile_intent_digest,
                    "namespace_digest": activation_context.namespace_digest,
                })
            return self
        except BaseException:
            self.abandon()
            raise

    def _io(self):
        return self.guard.store_io() if self.guard is not None else contextlib.nullcontext()

    def _load(self, snapshot):
        self.records, self.sessions, self.operations = [], {}, {}
        self.state, self.latest_shutdown, self.previous_shutdown = None, None, None
        self.active_release_id, self.activation_origin = None, None
        self.synthetic_lifecycle, self.unproven_lifecycle = False, False
        self.last_known_good, self.smoke, self.previous_smoke = None, False, False
        self.revision, self.releases = snapshot["revision"], snapshot["releases"]
        previous_digest, generation, run_nonce = None, 0, None
        self.log_bytes = 0
        for index, item in enumerate(snapshot["events"], 1):
            raw, receipt = item["raw"], item["receipt"]
            self.log_bytes += len(raw)
            if not raw.endswith(b"\n") or b"\n" in raw[:-1] or b"\r" in raw:
                raise MaintenanceRequired("application event framing rejected")
            record = json.loads(raw)
            keys = {"format", "mode", "installation_id", "data_store_id", "release_id", "binding_digest", "runtime_digest",
                    "sequence", "revision", "generation", "run_nonce", "operation_id", "request_operation_id", "previous_digest", "kind", "payload"}
            if (type(record) is not dict or set(record) != keys or canonical(record) + b"\n" != raw
                    or record["format"] != 1 or record["mode"] != "managed_application_event_v1"
                    or record["sequence"] != index or record["revision"] != index + 1
                    or record["previous_digest"] != previous_digest or record["operation_id"] != receipt["operationId"]
                    or record["generation"] != receipt["generation"] or not valid_uuid(record["run_nonce"])
                    or record["installation_id"] != self.store.installation_id or record["data_store_id"] != self.store.data_store_id
                    or record["release_id"] != self.store.release_id or record["binding_digest"] != self.store.binding_digest
                    or record["runtime_digest"] != self.runtime_digest or not valid_uuid(record["request_operation_id"])
                    or record["request_operation_id"] in self.operations):
                raise MaintenanceRequired("application event identity or sequence mismatch")
            kind, payload = record["kind"], record["payload"]
            if type(payload) is not dict:
                raise MaintenanceRequired("application event payload rejected")
            if kind in {"observing", "active_observing"}:
                expected_payload = {"previous_shutdown_digest": self.latest_shutdown}
                if kind == "active_observing":
                    expected_payload = {
                        **expected_payload,
                        "activation_operation_id": record["request_operation_id"],
                        "active_release_id": self.store.release_id,
                        "access": "fenced_application",
                    }
                    if (not valid_uuid(payload.get("activation_operation_id"))
                            or payload["activation_operation_id"] != record["request_operation_id"]
                            or type(payload.get("origin")) is not str
                            or payload["origin"] not in {"validated_fenced_profile_intent_v1", "synthetic_fixture_only_v1"}
                            or not valid_uuid(payload.get("host_nonce"))
                            or not valid_hash(payload.get("profile_intent_digest"))
                            or payload.get("namespace_digest") != self.store.binding["namespace"]["digest"]):
                        raise MaintenanceRequired("managed application activation evidence rejected")
                    expected_payload.update({
                        "origin": payload["origin"], "host_nonce": payload["host_nonce"],
                        "profile_intent_digest": payload["profile_intent_digest"],
                        "namespace_digest": payload["namespace_digest"],
                    })
                if (self.state not in {None, "clean_shutdown"} or payload != expected_payload
                        or record["generation"] != generation + 1 or record["run_nonce"] == run_nonce):
                    raise MaintenanceRequired("application lifecycle observing transition rejected")
                if kind == "active_observing":
                    if payload != expected_payload:
                        raise MaintenanceRequired("managed application active pointer rejected")
                    self.active_release_id, self.activation_origin = payload["active_release_id"], payload["origin"]
                    if payload["origin"] == "synthetic_fixture_only_v1":
                        self.synthetic_lifecycle = True
                else:
                    self.unproven_lifecycle = True
                self.previous_shutdown, self.previous_smoke = self.latest_shutdown, self.smoke
                generation, run_nonce, self.smoke = record["generation"], record["run_nonce"], False
                self.state = "observing"
            else:
                if self.state != "observing" or record["generation"] != generation or record["run_nonce"] != run_nonce:
                    raise MaintenanceRequired("application event has no live observing owner")
                if kind == "session.create":
                    if (set(payload) != {"session_id", "title"} or not valid_uuid(payload["session_id"])
                            or not bounded_text(payload["title"], 256) or payload["session_id"] in self.sessions or len(self.sessions) >= 128):
                        raise MaintenanceRequired("application session creation rejected")
                    self.sessions[payload["session_id"]] = {**payload, "documents": []}
                elif kind == "session.append":
                    session = self.sessions.get(payload.get("session_id"))
                    if (set(payload) != {"session_id", "text"} or session is None or not bounded_text(payload["text"], 32 * 1024)
                            or len(session["documents"]) >= 32):
                        raise MaintenanceRequired("application document event rejected")
                    session["documents"].append({"document_id": record["request_operation_id"], "text": payload["text"]})
                elif kind == "smoke_read":
                    if set(payload) != {"session_id"} or payload["session_id"] not in self.sessions:
                        raise MaintenanceRequired("application smoke read rejected")
                    self.smoke = True
                elif kind == "last_known_good":
                    if (set(payload) != {"shutdown_digest", "host_nonce", "scope"} or not self.smoke or not self.previous_smoke
                            or payload["shutdown_digest"] != self.previous_shutdown or not valid_uuid(payload["host_nonce"])
                            or payload["scope"] not in {"isolated_native_host", "engineering_source_child"}):
                        raise MaintenanceRequired("application LKG acceptance rejected")
                    self.last_known_good = {"release_id": self.store.release_id, "binding_digest": self.store.binding_digest,
                                           "generation": generation, "scope": payload["scope"], "acceptance_digest": digest(record)}
                    if payload["scope"] == "engineering_source_child":
                        self.synthetic_lifecycle = True
                elif kind == "clean_shutdown":
                    if payload != {"smoke": self.smoke}:
                        raise MaintenanceRequired("application clean shutdown evidence rejected")
                    self.state, self.latest_shutdown = "clean_shutdown", digest(record)
                else:
                    raise MaintenanceRequired("application event kind rejected")
            self.records.append(record)
            self.operations[record["request_operation_id"]] = record
            previous_digest = digest(record)
        self._last_digest, self._last_nonce, self._last_generation = previous_digest, run_nonce, generation

    def _refresh(self):
        if self.closed or self.failed or self.lease is None:
            raise MaintenanceRequired("managed application owner is unavailable")
        with self._io():
            self._load(self.store.application_snapshot(self.lease))
        if self.state != "observing" or self._last_nonce != self.nonce or self._last_generation != self.lease.generation:
            raise MaintenanceRequired("managed application observing owner mismatch")

    def _write(self, kind, operation_id, payload):
        if len(self.records) >= MAX_EVENTS or not valid_uuid(operation_id):
            raise ManagedStoreError("managed application event bound rejected")
        record = {"format": 1, "mode": "managed_application_event_v1", "installation_id": self.store.installation_id,
                  "data_store_id": self.store.data_store_id, "release_id": self.store.release_id, "binding_digest": self.store.binding_digest,
                  "runtime_digest": self.runtime_digest, "sequence": len(self.records) + 1, "revision": self.revision + 1,
                  "generation": self.lease.generation, "run_nonce": self.nonce, "operation_id": operation_id, "request_operation_id": operation_id,
                  "previous_digest": self._last_digest, "kind": kind, "payload": payload}
        raw = canonical(record) + b"\n"
        if self.log_bytes + len(raw) > MAX_LOG_BYTES:
            raise ManagedStoreError("managed application log byte limit exceeded")
        # The acquiring operation already occupies the store's operation table.
        store_operation = str(uuid.uuid5(uuid.UUID(operation_id), "managed-application-event"))
        record["operation_id"] = store_operation
        raw = canonical(record) + b"\n"
        with self._io():
            self.store.write_bytes(self.lease, store_operation, f"application/events/{record['sequence']:08d}.json", raw)
            self._load(self.store.application_snapshot(self.lease))
        return record

    def metadata(self):
        self._refresh()
        return self._metadata()

    def _metadata(self):
        return {"installation_id": self.store.installation_id, "data_store_id": self.store.data_store_id,
                "release_id": self.store.release_id, "binding_digest": self.store.binding_digest, "runtime_digest": self.runtime_digest,
                "active_release_id": self.active_release_id, "activation_origin": self.activation_origin,
                "history_provenance": self._history_provenance(),
                "production_activation": (self.activation_origin == "validated_fenced_profile_intent_v1"
                                          and not self.synthetic_lifecycle and not self.unproven_lifecycle),
                "nonce": self.nonce, "generation": self.lease.generation if self.lease else self._last_generation,
                "revision": self.revision, "lifecycle": self.state, "last_known_good": self.last_known_good}

    def _history_provenance(self):
        if self.unproven_lifecycle:
            return "unproven_legacy"
        if self.activation_origin == "synthetic_fixture_only_v1" or self.synthetic_lifecycle:
            return "synthetic_fixture_only"
        if self.activation_origin == "validated_fenced_profile_intent_v1":
            return "validated_fenced_profile_intent"
        return "none"

    def _mutate(self, kind, operation_id, payload):
        previous = self.operations.get(operation_id)
        if previous is not None:
            if previous["kind"] != kind or previous["payload"] != payload:
                raise ValueError("operation ID input changed")
            return previous
        return self._write(kind, operation_id, payload)

    def handle(self, request):
        response = {"type": "error", "code": "managed_application_rejected"}
        request_id = request.get("request_id") if type(request) is dict else None
        try:
            if type(request) is not dict or (request_id is not None and not bounded_text(request_id, 128)):
                return response
            if type(request.get("token")) is not str or not hmac.compare_digest(request["token"], self._token):
                return response
            kind = request.get("type")
            fields = {"managed.health": set(), "managed.lifecycle": set(), "session.list": set(),
                      "session.create": {"operation_id", "session_id", "title"}, "session.read": {"session_id"},
                      "session.append": {"operation_id", "session_id", "text"}, "document.read": {"session_id", "document_id"}}
            if kind not in fields or set(request) - {"type", "token", "request_id"} != fields[kind]:
                return response
            with self._mutex:
                self._refresh()
                if kind.startswith("managed."):
                    response = {"type": kind, "ready": True, "access": "fenced_application", **self._metadata()}
                elif kind == "session.list":
                    response = {"type": kind, "sessions": [{"session_id": value["session_id"], "title": value["title"],
                                "document_count": len(value["documents"])} for value in self.sessions.values()]}
                else:
                    session_id = request["session_id"]
                    if not valid_uuid(session_id):
                        return response
                    session = self.sessions.get(session_id)
                    if kind == "session.create":
                        if not valid_uuid(request["operation_id"]) or not bounded_text(request["title"], 256):
                            return response
                        if request["operation_id"] not in self.operations and (session is not None or len(self.sessions) >= 128):
                            return response
                        event = self._mutate(kind, request["operation_id"], {"session_id": session_id, "title": request["title"]})
                        response = {"type": "session.created", "session_id": session_id, "revision": event["revision"]}
                    elif session is None:
                        return response
                    elif kind == "session.append":
                        if not valid_uuid(request["operation_id"]) or not bounded_text(request["text"], 32 * 1024):
                            return response
                        if request["operation_id"] not in self.operations and len(session["documents"]) >= 32:
                            return response
                        event = self._mutate(kind, request["operation_id"], {"session_id": session_id, "text": request["text"]})
                        response = {"type": "session.appended", "session_id": session_id, "document_id": event["request_operation_id"], "revision": event["revision"]}
                    elif kind == "session.read":
                        if not self.smoke:
                            self._write("smoke_read", str(uuid.uuid4()), {"session_id": session_id})
                        response = {"type": kind, "session": self.sessions[session_id], "revision": self.revision}
                    elif kind == "document.read":
                        # Public document IDs are the caller's append operation UUID.
                        document_id = request["document_id"]
                        if not valid_uuid(document_id):
                            return response
                        document = next((value for value in session["documents"] if value["document_id"] == document_id), None)
                        if document is None:
                            return response
                        response = {"type": kind, "document": document}
        except (MaintenanceRequired, ManagedStoreError, OSError):
            self.failed = True
            self.abandon()
            response = {"type": "error", "code": "managed_application_maintenance_required"}
        except (ValueError, TypeError, KeyError, UnicodeError):
            pass
        if request_id is not None and bounded_text(request_id, 128):
            response["request_id"] = request_id
        return response

    def control(self, frame):
        common = {"protocol", "type", "nonce", "control_token", "operation_id"}
        if (type(frame) is not dict or frame.get("protocol") != "my-agent-managed-application-control-v1"
                or frame.get("nonce") != self.nonce or type(frame.get("control_token")) is not str
                or not hmac.compare_digest(frame["control_token"], self._token) or not valid_uuid(frame.get("operation_id"))):
            raise ManagedStoreError("managed application control rejected")
        with self._mutex:
            self._refresh()
            if frame["type"] == "shutdown" and set(frame) == common:
                event = self._write("clean_shutdown", frame["operation_id"], {"smoke": self.smoke})
                with self._io():
                    self.store.release(self.lease, str(uuid.uuid4()))
                self.closed = True
                return {"type": "managed.application.stopped", "clean": True, **self._metadata(), "shutdown_digest": digest(event)}
            if (frame["type"] == "accept_reopen" and set(frame) == common | {"shutdown_digest", "host_nonce"}
                    and frame["shutdown_digest"] == self.previous_shutdown and valid_hash(frame["shutdown_digest"])
                    and valid_uuid(frame["host_nonce"]) and self.smoke and self.previous_smoke):
                self._write("last_known_good", frame["operation_id"], {"shutdown_digest": frame["shutdown_digest"],
                    "host_nonce": frame["host_nonce"], "scope": self.acceptance_scope})
                return {"type": "managed.application.accepted", **self._metadata()}
            raise ManagedStoreError("managed application lifecycle transition rejected")

    def abandon(self):
        self.failed = True
        if self.lease is not None:
            self.lease._close_process_lock()


@dataclass(frozen=True, slots=True)
class ManagedHostReadonlyContext(ManagedReadonlyContext):
    """Opt-in native Host context; the default readonly context is unchanged."""
    host_nonce: str = ""
    profile_intent_digest: str = ""
    _control_token: str = field(default="", repr=False, compare=False)

    def control(self, frame):
        if (type(frame) is not dict or set(frame) != {"protocol", "type", "nonce", "control_token", "operation_id"}
                or frame["protocol"] != "my-agent-managed-host-readonly-control-v1" or frame["type"] != "shutdown"
                or frame["nonce"] != self.nonce or not valid_uuid(frame["operation_id"])
                or type(frame["control_token"]) is not str or not hmac.compare_digest(frame["control_token"], self._control_token)):
            raise ManagedStoreError("managed Host readonly control rejected")
        state = self.revalidate()
        readonly_state = {key: state[key] for key in ("data_store_id", "generation", "revision", "lifecycle", "last_known_good")}
        return {"type": "managed.host-readonly.stopped", "clean": True, "nonce": self.nonce,
                "host_nonce": self.host_nonce, "profile_intent_digest": self.profile_intent_digest,
                "installation_id": self.installation_id, "release_id": self.release_id,
                "binding_digest": self.binding_digest, "runtime_digest": self.runtime_digest, **readonly_state}

    def revalidate(self):
        import managed_host_entry as entry
        namespace = self.namespace_root
        marker = entry._read_record(namespace / "state/managed-namespace.json")
        binding = entry._read_record(namespace / "state/production-binding.json")
        entry._verify_operational_binding_stdlib(namespace, marker, binding, self.binding_digest)
        entry.validate_host_profile_intent(namespace, binding, self.runtime_digest, self.host_nonce, self.profile_intent_digest, None)
        entry._validate_operational_records(namespace, binding, self.runtime_digest)
        store = ManagedStore(str(namespace), installation_id=self.installation_id, data_store_id=binding["data"]["storeId"],
                             release_id=self.release_id, binding_digest=self.binding_digest)
        metadata = inspect_application(store, self.runtime_digest)
        metadata["data_store_id"] = store.data_store_id
        return metadata


def inspect_application(store, runtime_digest):
    snapshot = store.application_snapshot()
    if not snapshot["initialized"]:
        return {"revision": 0, "lifecycle": "empty_uninitialized", "active_release_id": None,
                "activation_origin": None, "history_provenance": "none", "production_activation": False,
                "last_known_good": None, "generation": 0}
    app = ManagedApplication()
    app.store, app.runtime_digest = store, runtime_digest
    app._load(snapshot)
    if not app.records or app.state != "clean_shutdown":
        raise MaintenanceRequired("managed application has no reconciled clean shutdown")
    last = app.records[-1]
    if snapshot["generation"] != last["generation"]:
        raise MaintenanceRequired("application readonly inspection found an unrepresented owner generation")
    if len([item for item in app.releases if item["generation"] == last["generation"] and item["revision"] == last["revision"]]) != 1:
        raise MaintenanceRequired("managed application clean release is missing")
    return {"revision": app.revision, "lifecycle": "clean_shutdown", "active_release_id": app.active_release_id,
            "activation_origin": app.activation_origin, "history_provenance": app._history_provenance(),
            "production_activation": (app.activation_origin == "validated_fenced_profile_intent_v1"
                                      and not app.synthetic_lifecycle and not app.unproven_lifecycle),
            "last_known_good": app.last_known_good,
            "generation": last["generation"]}
