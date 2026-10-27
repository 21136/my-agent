from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
import os
import queue
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
SANDBOX = CORE.parent
ARTIFACTS = SANDBOX / "artifacts/managed-final-integration-20261025"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(CORE / "tests"))

import managed_application as application
import managed_host_entry as entry
from managed_store import ManagedStore, ManagedStoreError, MaintenanceRequired
import test_managed_store as store_fixture

PYTHON = Path("F:/my-agent-main/.venv/Scripts/python.exe")
SITE = PYTHON.parents[1] / "Lib/site-packages"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def uid():
    return str(uuid.uuid4())


def fixture_at(directory):
    fixture = store_fixture.ManagedStoreTests()
    fixture.temp = str(directory)
    fixture.root = Path(directory) / "project/.worktrees/sandbox/.managed-runtime/12345678-1234-4234-8234-123456789abc"
    fixture.root.mkdir(parents=True)
    fixture.identity = {"installation_id": "12345678-1234-4234-8234-123456789abc",
                        "data_store_id": "22345678-1234-4234-8234-123456789abc",
                        "release_id": "3" * 64, "binding_digest": "a" * 64}
    fixture._write_identity_records()
    return fixture.root, fixture.identity


def synthetic_activation_context(store, operation_id, nonce, runtime_digest):
    """Explicit test-only authorization; never production acceptance evidence."""
    host_nonce = uid()
    return application.ManagedActivationContext(
        origin="synthetic_fixture_only_v1", operation_id=operation_id, nonce=nonce,
        runtime_digest=runtime_digest, host_nonce=host_nonce,
        profile_intent_digest=hashlib.sha256(canonical({"synthetic": True, "host_nonce": host_nonce})).hexdigest(),
        namespace_root=str(store.root), namespace_digest=store.binding["namespace"]["digest"],
        installation_id=store.installation_id, data_store_id=store.data_store_id,
        release_id=store.release_id, binding_digest=store.binding_digest, access="fenced_application")


class ManagedApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="application-unit-", dir=ARTIFACTS)
        self.addCleanup(self.temp.cleanup)
        self.root, self.identity = fixture_at(Path(self.temp.name))
        self.token = secrets.token_hex(32)
        self.app = None

    def tearDown(self):
        if self.app is not None:
            self.app.abandon()

    def open(self):
        store, operation_id, nonce, runtime_digest = ManagedStore(str(self.root), **self.identity), uid(), uid(), "b" * 64
        self.app = application.ManagedApplication.open(store,
            operation_id=operation_id, nonce=nonce, runtime_digest=runtime_digest, control_token=self.token,
            activation_context=synthetic_activation_context(store, operation_id, nonce, runtime_digest),
            acceptance_scope="engineering_source_child")
        return self.app

    def request(self, kind, **fields):
        return self.app.handle({"type": kind, "token": self.token, **fields})

    def control(self, kind, **fields):
        return self.app.control({"protocol": "my-agent-managed-application-control-v1", "type": kind,
            "nonce": self.app.nonce, "control_token": self.token, "operation_id": uid(), **fields})

    def test_strict_application_bootstrap_is_distinct_and_no_path_overrides(self):
        frame = {"protocol": "my-agent-managed-application-v1", "namespace_root": str(self.root),
                 "binding_digest": self.identity["binding_digest"], "runtime_digest": "b" * 64,
                 "nonce": uid(), "operation_id": uid(), "control_token": self.token,
                 "host_nonce": uid(), "profile_intent_digest": "c" * 64}
        self.assertEqual(entry.parse_managed_application_bootstrap(canonical(frame) + b"\n"), frame)
        for invalid in ({**frame, "data_root": str(self.root)}, {**frame, "protocol": "my-agent-managed-readonly-v1"}):
            with self.assertRaises(ValueError):
                entry.parse_managed_application_bootstrap(canonical(invalid) + b"\n")
        with self.assertRaises(ValueError):
            entry.parse_managed_readonly_bootstrap(canonical(frame) + b"\n")
        readonly_frame = {key: value for key, value in frame.items() if key != "operation_id"}
        readonly_frame["protocol"] = "my-agent-managed-host-readonly-v1"
        self.assertEqual(entry.parse_managed_host_readonly_bootstrap(canonical(readonly_frame) + b"\n"), readonly_frame)
        with self.assertRaises(ValueError):
            entry.parse_managed_host_readonly_bootstrap(canonical({**readonly_frame, "profile_policy": "anything"}) + b"\n")

    def test_controller_context_builder_binds_validated_profile_and_marks_fixture_synthetic(self):
        namespace = self.root
        marker = json.loads((namespace / "state/managed-namespace.json").read_bytes())
        binding = json.loads((namespace / "state/production-binding.json").read_bytes())
        operation_id, nonce, host_nonce, runtime_digest = uid(), uid(), uid(), "b" * 64
        profile_body = {"format": 1, "mode": "managed_host_profile_intent_v1",
            "installationId": binding["installationId"], "releaseId": binding["release"]["releaseId"],
            "bindingDigest": binding["bindingDigest"], "runtimeDigest": runtime_digest, "hostNonce": host_nonce,
            "profileRoot": binding["paths"]["hostProfile"], "access": "fenced_application"}
        profile_digest = hashlib.sha256(canonical(profile_body)).hexdigest()
        (namespace / "host/state/managed-host-profile-intent.json").write_bytes(
            canonical({**profile_body, "intentDigest": profile_digest}) + b"\n")
        frame = {"protocol": "my-agent-managed-application-v1", "namespace_root": str(namespace),
            "binding_digest": binding["bindingDigest"], "runtime_digest": runtime_digest, "nonce": nonce,
            "operation_id": operation_id, "control_token": self.token, "host_nonce": host_nonce,
            "profile_intent_digest": profile_digest}
        context = entry._build_controller_activation_context(frame, namespace, marker, binding,
            origin="synthetic_fixture_only_v1")
        self.assertEqual(context.origin, "synthetic_fixture_only_v1")
        self.assertEqual(context.access, "fenced_application")
        self.assertEqual(context.operation_id, operation_id)
        self.assertEqual(context.nonce, nonce)
        self.assertEqual(context.runtime_digest, runtime_digest)
        self.assertEqual(context.namespace_digest, binding["namespace"]["digest"])
        self.assertEqual(context.release_id, binding["release"]["releaseId"])

    def test_readonly_guard_denies_native_sqlite_creation_but_allows_registered_readonly_uri(self):
        database = Path(self.temp.name) / "readonly.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE synthetic(value TEXT)")
            connection.execute("INSERT INTO synthetic VALUES('fixture')")
        connection.close()
        forbidden = Path(self.temp.name) / "denied.sqlite3"
        script = f'''import json, pathlib, sqlite3, sys
sys.path.insert(0, {str(CORE)!r})
from managed_readonly import ManagedReadonlyAuditGuard
ManagedReadonlyAuditGuard((pathlib.Path({self.temp.name!r}), pathlib.Path(sys.base_prefix))).install()
denied = False
try: sqlite3.connect({str(forbidden)!r})
except PermissionError: denied = True
connection = sqlite3.connect({database.as_uri() + '?mode=ro'!r}, uri=True)
assert connection.execute("SELECT COUNT(*) FROM synthetic").fetchone()[0] == 1
try: connection.execute("CREATE TABLE forbidden(value)"); raise AssertionError("readonly database mutated")
except sqlite3.OperationalError: pass
connection.close()
assert denied, "native sqlite creation bypassed readonly guard"
print("sqlite_readonly_policy_ok")
'''
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        env = {"SystemRoot": system_root, "WINDIR": system_root, "TEMP": self.temp.name, "TMP": self.temp.name,
               "PATH": str(PYTHON.parent)}
        result = subprocess.run([str(PYTHON), "-I", "-B", "-S", "-c", script], cwd=self.temp.name,
                                env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertIn("sqlite_readonly_policy_ok", result.stdout)
        self.assertFalse(forbidden.exists())

    def test_real_store_session_document_persistence_reopen_lkg_and_stale_refusal(self):
        self.assertEqual(application.inspect_application(ManagedStore(str(self.root), **self.identity), "b" * 64)["revision"], 0)
        app = self.open()
        self.assertEqual(app.metadata()["active_release_id"], self.identity["release_id"])
        self.assertEqual(app.metadata()["activation_origin"], "synthetic_fixture_only_v1")
        self.assertIsNone(app.metadata()["last_known_good"])
        self.assertEqual(app.records[0]["kind"], "active_observing")
        with self.assertRaises(MaintenanceRequired):
            application.inspect_application(ManagedStore(str(self.root), **self.identity), "b" * 64)
        session_id, create_op, append_op = uid(), uid(), uid()
        created = self.request("session.create", operation_id=create_op, session_id=session_id, title="Synthetic session")
        self.assertEqual(created["type"], "session.created")
        self.assertEqual(self.request("session.create", operation_id=create_op, session_id=session_id, title="Synthetic session"), created)
        appended = self.request("session.append", operation_id=append_op, session_id=session_id, text="Synthetic persisted document")
        self.assertEqual(appended["type"], "session.appended")
        self.assertEqual(self.request("session.read", session_id=session_id)["session"]["documents"][0]["text"], "Synthetic persisted document")
        self.assertEqual(len(self.request("session.list")["sessions"]), 1)
        stopped = self.control("shutdown")
        self.assertTrue(stopped["clean"])
        self.assertEqual(application.inspect_application(ManagedStore(str(self.root), **self.identity), "b" * 64)["lifecycle"], "clean_shutdown")
        self.assertEqual(self.request("session.append", operation_id=uid(), session_id=session_id, text="stale")["type"], "error")
        old_generation = app.lease.generation
        reopened = self.open()
        self.assertGreater(reopened.lease.generation, old_generation)
        self.assertEqual(reopened.metadata()["active_release_id"], self.identity["release_id"])
        self.assertEqual(reopened.metadata()["activation_origin"], "synthetic_fixture_only_v1")
        self.assertIsNone(reopened.metadata()["last_known_good"])
        self.assertEqual(self.request("document.read", session_id=session_id, document_id=append_op)["document"]["text"], "Synthetic persisted document")
        self.request("session.read", session_id=session_id)
        accepted = self.control("accept_reopen", shutdown_digest=stopped["shutdown_digest"], host_nonce=uid())
        self.assertEqual(accepted["last_known_good"]["scope"], "engineering_source_child")
        self.assertEqual(accepted["active_release_id"], self.identity["release_id"])
        self.assertNotIn("active_release_id", accepted["last_known_good"])
        self.control("shutdown")
        cold = application.inspect_application(ManagedStore(str(self.root), **self.identity), "b" * 64)
        self.assertEqual(cold["activation_origin"], "synthetic_fixture_only_v1")
        self.assertFalse(cold["production_activation"])
        self.assertEqual(cold["last_known_good"]["scope"], "engineering_source_child")
        store, operation_id, nonce, runtime_digest = ManagedStore(str(self.root), **self.identity), uid(), uid(), "b" * 64
        with self.assertRaises(MaintenanceRequired):
            application.ManagedApplication.open(store, operation_id=operation_id, nonce=nonce,
                runtime_digest=runtime_digest, control_token=self.token,
                activation_context=synthetic_activation_context(store, operation_id, nonce, runtime_digest),
                acceptance_scope="isolated_native_host")
        self.assertFalse(store.inspect()["activeLease"])

    def test_missing_activation_context_refuses_before_lease_or_initialization(self):
        store = ManagedStore(str(self.root), **self.identity)
        with self.assertRaises(ManagedStoreError):
            application.ManagedApplication.open(store, operation_id=uid(), nonce=uid(),
                runtime_digest="b" * 64, control_token=self.token,
                acceptance_scope="engineering_source_child")
        state = store.inspect()
        self.assertEqual(state["state"], "empty_uninitialized")
        self.assertFalse(state["activeLease"])

    def test_mismatched_activation_context_refuses_before_lease_or_initialization(self):
        store = ManagedStore(str(self.root), **self.identity)
        operation_id, nonce, runtime_digest = uid(), uid(), "b" * 64
        context = synthetic_activation_context(store, operation_id, nonce, runtime_digest)
        mismatches = (("release_id", "4" * 64), ("operation_id", uid()), ("binding_digest", "5" * 64))
        for field, value in mismatches:
            with self.subTest(field=field):
                invalid = replace(context, **{field: value})
                with self.assertRaises(ManagedStoreError):
                    application.ManagedApplication.open(store, operation_id=operation_id, nonce=nonce,
                        runtime_digest=runtime_digest, control_token=self.token,
                        activation_context=invalid, acceptance_scope="engineering_source_child")
                state = store.inspect()
                self.assertEqual(state["state"], "empty_uninitialized")
                self.assertFalse(state["activeLease"])
                self.assertFalse(store.control_path.exists())

    def test_legacy_unproven_observing_history_refuses_isolated_replay_before_lease(self):
        store = ManagedStore(str(self.root), **self.identity)
        lease = store.acquire(uid())
        store.initialize(lease, uid())
        runtime_digest, previous_digest, run_nonce = "b" * 64, None, uid()
        for sequence, (kind, payload) in enumerate((
                ("observing", {"previous_shutdown_digest": None}),
                ("clean_shutdown", {"smoke": False})), 1):
            request_operation_id = uid()
            operation_id = str(uuid.uuid5(uuid.UUID(request_operation_id), "managed-application-event"))
            record = {"format": 1, "mode": "managed_application_event_v1",
                "installation_id": store.installation_id, "data_store_id": store.data_store_id,
                "release_id": store.release_id, "binding_digest": store.binding_digest,
                "runtime_digest": runtime_digest, "sequence": sequence, "revision": sequence + 1,
                "generation": lease.generation, "run_nonce": run_nonce,
                "operation_id": operation_id, "request_operation_id": request_operation_id,
                "previous_digest": previous_digest, "kind": kind, "payload": payload}
            raw = canonical(record) + b"\n"
            store.write_bytes(lease, operation_id, f"application/events/{sequence:08d}.json", raw)
            previous_digest = application.digest(record)
        store.release(lease, uid())
        cold = application.inspect_application(store, runtime_digest)
        self.assertEqual(cold["lifecycle"], "clean_shutdown")
        self.assertIsNone(cold["activation_origin"])
        self.assertEqual(cold["history_provenance"], "unproven_legacy")
        self.assertFalse(cold["production_activation"])
        self.assertIsNone(cold["last_known_good"])

        operation_id, nonce = uid(), uid()
        context = synthetic_activation_context(store, operation_id, nonce, runtime_digest)
        with self.assertRaisesRegex(MaintenanceRequired, "unproven legacy lifecycle"):
            application.ManagedApplication.open(store, operation_id=operation_id, nonce=nonce,
                runtime_digest=runtime_digest, control_token=self.token,
                activation_context=context, acceptance_scope="isolated_native_host")
        state = store.inspect()
        self.assertFalse(state["activeLease"])
        connection = sqlite3.connect(store.control_path)
        try:
            self.assertEqual(connection.execute("SELECT generation FROM lease WHERE singleton=1").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM operations WHERE kind='acquire'").fetchone()[0], 1)
        finally:
            connection.close()

    def test_closed_protocol_and_parallel_crashed_owner_refuse(self):
        self.open()
        for request in ({"type": "user.message", "token": self.token}, {"type": "settings.set", "token": self.token},
                        {"type": "session.list", "token": "wrong"}, {"type": "session.list", "token": self.token, "root": "C:/"}):
            self.assertEqual(self.app.handle(request)["code"], "managed_application_rejected")
        with self.assertRaises(ManagedStoreError):
            store, operation_id, nonce, runtime_digest = ManagedStore(str(self.root), **self.identity), uid(), uid(), "b" * 64
            application.ManagedApplication.open(store, operation_id=operation_id, nonce=nonce, runtime_digest=runtime_digest,
                control_token=self.token, activation_context=synthetic_activation_context(store, operation_id, nonce, runtime_digest),
                acceptance_scope="engineering_source_child")
        self.app.abandon()
        with self.assertRaises(MaintenanceRequired):
            self.open()

    def test_completed_unrepresented_controller_owner_requires_maintenance(self):
        self.open()
        self.assertEqual(self.app.lease.generation, 1)
        self.control("shutdown")
        controller = ManagedStore(str(self.root), **self.identity)
        unrepresented = controller.acquire(uid())
        self.assertEqual(unrepresented.generation, 2)
        controller.release(unrepresented, uid())
        with self.subTest(mode="readonly"), self.assertRaises(MaintenanceRequired):
            application.inspect_application(controller, "b" * 64)
        with self.subTest(mode="application"), self.assertRaises(MaintenanceRequired):
            self.open()

    def test_malformed_release_receipt_generation_requires_maintenance(self):
        self.open()
        self.control("shutdown")
        controller = ManagedStore(str(self.root), **self.identity)
        self.assertEqual(application.inspect_application(controller, "b" * 64)["lifecycle"], "clean_shutdown")
        database = self.root / "state/managed-store-control.sqlite3"
        connection = sqlite3.connect(database)
        try:
            operation_id, original = connection.execute("SELECT operation_id,receipt FROM operations WHERE kind='release'").fetchone()
            for generation in (1.0, True, 0, -1, "1", None, 2 ** 63):
                with self.subTest(generation=generation, value_type=type(generation).__name__):
                    receipt = {**json.loads(original), "generation": generation}
                    connection.execute("UPDATE operations SET receipt=? WHERE operation_id=?", (canonical(receipt).decode(), operation_id))
                    connection.commit()
                    with self.assertRaises(MaintenanceRequired):
                        controller.application_snapshot()
            connection.execute("UPDATE operations SET receipt=? WHERE operation_id=?", (original, operation_id))
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(application.inspect_application(controller, "b" * 64)["lifecycle"], "clean_shutdown")

    def test_tampered_or_pending_event_forces_maintenance(self):
        self.open()
        session_id = uid()
        self.request("session.create", operation_id=uid(), session_id=session_id, title="Synthetic")
        target = next((self.root / "data/store/application/events").glob("*.json"))
        target.write_bytes(b"{}\n")
        self.assertEqual(self.request("session.list")["code"], "managed_application_maintenance_required")
        with self.assertRaises(MaintenanceRequired):
            self.open()

    def test_host_readonly_profile_intent_is_bound_and_default_stays_strict(self):
        binding = json.loads((self.root / "state/production-binding.json").read_bytes())
        marker = json.loads((self.root / "state/managed-namespace.json").read_bytes())
        host_nonce = uid()
        body = {"format": 1, "mode": "managed_host_profile_intent_v1", "installationId": binding["installationId"],
                "releaseId": binding["release"]["releaseId"], "bindingDigest": binding["bindingDigest"],
                "runtimeDigest": "b" * 64, "hostNonce": host_nonce, "profileRoot": binding["paths"]["hostProfile"], "access": "read_only"}
        intent = {**body, "intentDigest": hashlib.sha256(canonical(body)).hexdigest()}
        (self.root / "host/state/managed-host-profile-intent.json").write_bytes(canonical(intent) + b"\n")
        (self.root / "host/profile/Synthetic Preferences").write_text("synthetic", encoding="utf-8")
        # Identity helper tests use synthetic context/runtime records added only for the shape check.
        entry.validate_host_profile_intent(self.root, binding, "b" * 64, host_nonce, intent["intentDigest"], "read_only")
        with self.assertRaises(ValueError):
            entry.validate_host_profile_intent(self.root, binding, "b" * 64, uid(), intent["intentDigest"], "read_only")
        from managed_readonly import validate_production_binding
        with self.assertRaises(ValueError):
            validate_production_binding(self.root, marker, binding, binding["bindingDigest"])

    def test_actual_host_readonly_source_child_health_binds_nonce_and_profile(self):
        binding = json.loads((self.root / "state/production-binding.json").read_bytes())
        marker = json.loads((self.root / "state/managed-namespace.json").read_bytes())

        def record(relative, body, digest_field):
            value = {**body, digest_field: hashlib.sha256(canonical(body)).hexdigest()}
            (self.root / relative).write_bytes(canonical(value) + b"\n")
            return value

        common = {"format": 1, "installationId": binding["installationId"], "releaseId": binding["release"]["releaseId"],
                  "bindingDigest": binding["bindingDigest"], "readyForLaunch": False}
        context = {**common, "operationId": binding["source"]["operationId"],
                   "namespaceDigest": hashlib.sha256(canonical(marker)).hexdigest(), "dataStoreId": binding["data"]["storeId"]}
        record("state/production-context-intent.json", {**context, "mode": "managed_production_context_intent_v1", "state": "copy_started"}, "intentDigest")
        record("state/production-context-receipt.json", {**context, "mode": "managed_production_context_receipt_v1",
            "manifestDigest": binding["release"]["releaseId"], "filesDigest": "8" * 64, "payloadBytes": 0, "allocationBytes": 0}, "receiptDigest")
        runtimes = {}
        for name, executable, policy in (("node", "node.exe", "single_executable_v1"), ("electron", "electron.exe", "complete_tree_v1"),
                                          ("python", "Scripts/python.exe", "complete_tree_v1"), ("pythonBase", "python.exe", "complete_tree_v1")):
            runtime_root = Path(self.temp.name) / "synthetic-runtime" / name
            runtime_root.mkdir(parents=True)
            runtimes[name] = {"root": str(runtime_root), "entry": executable, "policy": policy,
                "files": [{"path": executable, "sha256": "0" * 64, "byteLength": 1}], "directories": ["Scripts"] if name == "python" else []}
        registration = record("launcher/runtime-registration.json", {**common, "mode": "managed_runtime_registration_v1", "runtimes": runtimes}, "runtimeDigest")
        runtime_common = {**common, "runtimeDigest": registration["runtimeDigest"]}
        record("launcher/runtime-intent.json", {**runtime_common, "mode": "managed_runtime_intent_v1", "state": "registration_started"}, "intentDigest")
        record("launcher/runtime-receipt.json", {**runtime_common, "mode": "managed_runtime_receipt_v1",
            "registrationDigest": hashlib.sha256(canonical(registration)).hexdigest()}, "receiptDigest")
        host_nonce = uid()
        profile = record("host/state/managed-host-profile-intent.json", {"format": 1, "mode": "managed_host_profile_intent_v1",
            "installationId": binding["installationId"], "releaseId": binding["release"]["releaseId"], "bindingDigest": binding["bindingDigest"],
            "runtimeDigest": registration["runtimeDigest"], "hostNonce": host_nonce, "profileRoot": binding["paths"]["hostProfile"], "access": "read_only"}, "intentDigest")
        (Path(binding["paths"]["code"]) / "evolve").mkdir()
        frame = {"protocol": "my-agent-managed-host-readonly-v1", "namespace_root": str(self.root), "nonce": uid(),
                 "binding_digest": binding["bindingDigest"], "runtime_digest": registration["runtimeDigest"], "host_nonce": host_nonce,
                 "profile_intent_digest": profile["intentDigest"], "control_token": self.token}
        script = f"import sys;sys.path[:0]=[{str(CORE)!r},{str(CORE / 'tests')!r},{str(SITE)!r}];from test_managed_application import source_host_readonly_child;source_host_readonly_child()"
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        env = {"SystemRoot": system_root, "WINDIR": system_root, "TEMP": self.temp.name, "TMP": self.temp.name, "PATH": str(PYTHON.parent)}
        process = subprocess.Popen([str(PYTHON), "-I", "-B", "-S", "-c", script], cwd=self.temp.name, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        process.stdin.reconfigure(newline="\n")
        lines = queue.Queue()
        threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True).start()
        try:
            process.stdin.write(canonical(frame).decode() + "\n"); process.stdin.flush()
            ready = json.loads(lines.get(timeout=20))
            if not ready["ready"]:
                process.wait(timeout=10)
                self.fail(process.stderr.read()[-2500:])

            async def health_request():
                from websockets.asyncio.client import connect
                async with connect(f"ws://127.0.0.1:{ready['port']}") as websocket:
                    await websocket.send('{"type":"managed.health"}')
                    return json.loads(await asyncio.wait_for(websocket.recv(), 10))

            health = asyncio.run(health_request())
            for name in ("nonce", "host_nonce", "profile_intent_digest", "binding_digest", "runtime_digest"):
                with self.subTest(field=name):
                    self.assertEqual(health.get(name), frame[name])
                    self.assertEqual(health.get(name), ready[name])
            self.assertEqual(health["data_store_id"], binding["data"]["storeId"])
            for name, expected in (("installation_id", binding["installationId"]), ("release_id", binding["release"]["releaseId"]),
                                   ("data_store_id", binding["data"]["storeId"])):
                self.assertEqual(health[name], expected)
                self.assertEqual(health[name], ready[name])
            control = {"protocol": "my-agent-managed-host-readonly-control-v1", "type": "shutdown", "nonce": frame["nonce"],
                       "control_token": self.token, "operation_id": uid()}
            process.stdin.write(canonical(control).decode() + "\n"); process.stdin.flush()
            stopped = json.loads(lines.get(timeout=15))
            process.wait(timeout=10)
            self.assertEqual(process.returncode, 0, process.stderr.read()[-2500:])
            self.assertTrue(stopped["clean"])
            self.assertEqual(stopped["type"], "managed.host-readonly.stopped")
            self.assertEqual(set(stopped), {"type", "clean", "nonce", "host_nonce", "profile_intent_digest",
                "installation_id", "release_id", "binding_digest", "runtime_digest", "data_store_id",
                "generation", "revision", "lifecycle", "last_known_good"})
            for name in ("nonce", "host_nonce", "profile_intent_digest", "binding_digest", "runtime_digest", "data_store_id"):
                self.assertEqual(stopped[name], health[name])
            self.assertFalse((self.root / "state/managed-store-control.sqlite3").exists())
            identity_fields = ("nonce", "host_nonce", "profile_intent_digest", "installation_id", "release_id", "data_store_id",
                               "binding_digest", "runtime_digest", "revision", "lifecycle", "generation")
            for name in identity_fields:
                self.assertEqual(stopped[name], ready[name])
            evidence = {"format": 1, "scope": "engineering_source_server_host_readonly_synthetic_records",
                "serverSha256": hashlib.sha256((CORE / "server.py").read_bytes()).hexdigest(),
                "ready": {name: ready[name] for name in identity_fields}, "health": {name: health[name] for name in identity_fields},
                "stopped": {name: stopped[name] for name in identity_fields}, "identityMatches": True,
                "authenticatedCleanShutdown": stopped["clean"], "exitCode": process.returncode, "writerLeaseAcquired": False,
                "nativeApplication": False, "sealedPostmergeHealth": False}
            (ARTIFACTS / "source-host-readonly-health-probe.json").write_bytes(canonical(evidence) + b"\n")
        finally:
            if process.poll() is None:
                process.kill(); process.wait(timeout=10)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def test_actual_source_child_websocket_shutdown_reopen_and_crash_maintenance(self):
        # Retained source proof, explicitly synthetic binding; no production entry bypass exists.
        directory = Path(tempfile.mkdtemp(prefix="source-application-proof-", dir=ARTIFACTS))
        root, identity = fixture_at(directory)
        token = secrets.token_hex(32)

        def launch():
            frame = {"protocol": "my-agent-managed-application-v1", "namespace_root": str(root),
                     "binding_digest": identity["binding_digest"], "runtime_digest": "b" * 64,
                     "nonce": uid(), "operation_id": uid(), "control_token": token,
                     "host_nonce": uid(), "profile_intent_digest": "c" * 64}
            script = f"import sys;sys.path[:0]=[{str(CORE)!r},{str(CORE / 'tests')!r},{str(SITE)!r}];from test_managed_application import source_child;source_child()"
            system_root = os.environ.get("SystemRoot", r"C:\Windows")
            env = {"SystemRoot": system_root, "WINDIR": system_root, "TEMP": str(directory), "TMP": str(directory),
                   "PATH": os.pathsep.join((str(PYTHON.parent), str(Path(system_root) / "System32")))}
            process = subprocess.Popen([str(PYTHON), "-I", "-B", "-S", "-c", script], cwd=directory, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
            process.stdin.reconfigure(newline="\n")
            lines = queue.Queue()
            threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True).start()
            process.stdin.write(canonical(frame).decode() + "\n"); process.stdin.flush()
            try:
                ready = json.loads(lines.get(timeout=30))
            except queue.Empty:
                process.kill(); process.wait(timeout=10)
                self.fail("source child readiness timeout: " + process.stderr.read()[-2000:])
            return process, frame, ready, lines

        def stop(process, frame, lines):
            control = {"protocol": "my-agent-managed-application-control-v1", "type": "shutdown", "nonce": frame["nonce"],
                       "control_token": token, "operation_id": uid()}
            process.stdin.write(canonical(control).decode() + "\n"); process.stdin.flush()
            stopped = json.loads(lines.get(timeout=20))
            process.wait(timeout=20)
            self.assertEqual(process.returncode, 0, process.stderr.read()[-2000:])
            process.stdin.close(); process.stdout.close(); process.stderr.close()
            return stopped

        async def requests(ready, messages):
            from websockets.asyncio.client import connect
            async with connect(f"ws://127.0.0.1:{ready['port']}") as websocket:
                results = []
                for message in messages:
                    await websocket.send(json.dumps({"token": token, **message}))
                    results.append(json.loads(await asyncio.wait_for(websocket.recv(), 10)))
                return results

        processes = []
        try:
            process, frame, ready, lines = launch(); processes.append(process)
            if not ready["ready"]:
                process.wait(timeout=10)
                self.fail("synthetic source child refused startup: " + process.stderr.read()[-3000:])
            session_id, append_id = uid(), uid()
            results = asyncio.run(requests(ready, [{"type": "session.create", "operation_id": uid(), "session_id": session_id, "title": "Synthetic source session"},
                {"type": "session.append", "operation_id": append_id, "session_id": session_id, "text": "Synthetic source persistence"},
                {"type": "session.list"}, {"type": "session.read", "session_id": session_id}, {"type": "user.message"}]))
            self.assertEqual(results[-1]["code"], "managed_application_rejected")
            stopped = stop(process, frame, lines)
            before_control = hashlib.sha256((root / "state/managed-store-control.sqlite3").read_bytes()).hexdigest()
            readonly_script = f'''import json, pathlib, sys
sys.path.insert(0, {str(CORE)!r})
from managed_store import ManagedStore
from managed_application import inspect_application
from managed_readonly import ManagedReadonlyAuditGuard
store = ManagedStore({str(root)!r}, **{identity!r})
ManagedReadonlyAuditGuard((store.root, pathlib.Path({str(CORE)!r}), pathlib.Path(sys.base_prefix))).install()
print(json.dumps(inspect_application(store, {'b' * 64!r})))
'''
            system_root = os.environ.get("SystemRoot", r"C:\Windows")
            readonly_env = {"SystemRoot": system_root, "WINDIR": system_root, "TEMP": str(directory), "TMP": str(directory), "PATH": str(PYTHON.parent)}
            readonly = subprocess.run([str(PYTHON), "-I", "-B", "-S", "-c", readonly_script], cwd=directory,
                env=readonly_env, capture_output=True, text=True, timeout=15)
            self.assertEqual(readonly.returncode, 0, readonly.stderr[-2000:])
            self.assertEqual(json.loads(readonly.stdout)["lifecycle"], "clean_shutdown")
            self.assertEqual(hashlib.sha256((root / "state/managed-store-control.sqlite3").read_bytes()).hexdigest(), before_control)
            process, frame, reopened, lines = launch(); processes.append(process)
            persisted = asyncio.run(requests(reopened, [{"type": "session.read", "session_id": session_id}]))[0]
            self.assertEqual(persisted["session"]["documents"][0]["text"], "Synthetic source persistence")
            accept = {"protocol": "my-agent-managed-application-control-v1", "type": "accept_reopen", "nonce": frame["nonce"],
                      "control_token": token, "operation_id": uid(), "shutdown_digest": stopped["shutdown_digest"], "host_nonce": uid()}
            process.stdin.write(canonical(accept).decode() + "\n"); process.stdin.flush()
            accepted = json.loads(lines.get(timeout=20))
            self.assertEqual(accepted["last_known_good"]["scope"], "engineering_source_child")
            stop(process, frame, lines)
            process, frame, crashed, lines = launch(); processes.append(process)
            self.assertTrue(crashed["ready"])
            process.kill(); process.wait(timeout=10)
            process, frame, refused, lines = launch(); processes.append(process)
            self.assertFalse(refused["ready"])
            process.wait(timeout=10)
            self.assertEqual(process.returncode, 4)
            evidence = {"format": 1, "scope": "engineering_source_child_synthetic_binding", "fixture": str(directory),
                "realWebSocket": True, "created": True, "listed": True, "read": True, "appended": True,
                "persistedAfterReopen": True, "cleanShutdown": True, "readonlyReopenInspectionNoLeaseNoWrites": True,
                "reopenGenerationIncreased": reopened["generation"] > ready["generation"],
                "lkgScope": accepted["last_known_good"]["scope"], "crashedOwnerRefused": True,
                "nativeApplication": False, "sealedPostmergeHealth": False, "modelCalls": False}
            (ARTIFACTS / "source-application-probe.json").write_bytes(canonical(evidence) + b"\n")
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill(); process.wait(timeout=10)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None and not stream.closed:
                        stream.close()


def source_host_readonly_child():
    """Real source server/Host context over explicitly synthetic identity records."""
    try:
        from dataclasses import replace
        from managed_readonly import ManagedReadonlyAuditGuard
        frame = entry.parse_managed_host_readonly_bootstrap(sys.stdin.buffer.readline(entry.MAX_FRAME_BYTES + 1))
        namespace = Path(frame["namespace_root"])
        binding = json.loads((namespace / "state/production-binding.json").read_bytes())
        context = application.ManagedHostReadonlyContext._from_validated_records(namespace_root=namespace, binding=binding,
            runtime_digest=frame["runtime_digest"], nonce=frame["nonce"])
        context = replace(context, host_nonce=frame["host_nonce"], profile_intent_digest=frame["profile_intent_digest"], _control_token=frame["control_token"])
        ManagedReadonlyAuditGuard((namespace, CORE, SANDBOX / "evolve", Path(sys.base_prefix), SITE)).install()
        import server
        raise SystemExit(asyncio.run(server.run_server("127.0.0.1", 0, managed_readonly_context=context, managed_control_stream=sys.stdin.buffer)))
    except Exception:
        import traceback
        traceback.print_exc(file=sys.stderr)
        print('{"ready":false,"error":"synthetic_host_readonly_refused"}', flush=True)
        raise SystemExit(4)


def source_child():
    try:
        frame = entry.read_managed_application_bootstrap(sys.stdin.buffer)
        binding = json.loads((Path(frame["namespace_root"]) / "state/production-binding.json").read_bytes())
        store = ManagedStore(frame["namespace_root"], installation_id=binding["installationId"], data_store_id=binding["data"]["storeId"],
            release_id=binding["release"]["releaseId"], binding_digest=frame["binding_digest"])
        guard = application.ManagedApplicationAuditGuard((store.root, CORE, SANDBOX / "evolve", Path(sys.base_prefix), SITE), store)
        guard.install()
        activation_context = synthetic_activation_context(store, frame["operation_id"], frame["nonce"], frame["runtime_digest"])
        app = application.ManagedApplication.open(store, operation_id=frame["operation_id"], nonce=frame["nonce"], runtime_digest=frame["runtime_digest"],
            control_token=frame["control_token"], activation_context=activation_context,
            acceptance_scope="engineering_source_child", audit_guard=guard)
        import server
        raise SystemExit(asyncio.run(server.run_server("127.0.0.1", 0, managed_application=app, managed_control_stream=sys.stdin.buffer)))
    except Exception:
        import traceback
        traceback.print_exc(file=sys.stderr)
        print('{"ready":false,"error":"managed_application_maintenance_required"}', flush=True)
        raise SystemExit(4)
