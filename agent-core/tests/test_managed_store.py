from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

SANDBOX = Path(__file__).resolve().parents[2]
FIXTURE_BASE = SANDBOX / "artifacts" / "managed-production-context-20261025" / "store-unit-fixtures"
FIXTURE_MARKER = ".managed-store-synthetic-production-fixture"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from managed_store import ManagedStore, ManagedStoreError, MaintenanceRequired, StoreBusyError
except ImportError:  # First RED run: keep the missing implementation as an assertion failure.
    ManagedStore = ManagedStoreError = MaintenanceRequired = StoreBusyError = None


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _child_hold_lease(root: str, identity: dict[str, str], ready: object, release: object) -> None:
    store = ManagedStore(root, **identity)
    lease = store.acquire(str(uuid.uuid4()))
    ready.put({"acquired": True, "generation": lease.generation})
    release.wait(10)
    store.release(lease, str(uuid.uuid4()))


def _child_crash_with_lease(root: str, identity: dict[str, str], ready: object) -> None:
    store = ManagedStore(root, **identity)
    store.acquire(str(uuid.uuid4()))
    ready.put({"acquired": True})
    ready.close()
    ready.join_thread()
    os._exit(0)


class ManagedStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertIsNotNone(ManagedStore, "managed_store.py must provide the production store API")
        FIXTURE_BASE.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.mkdtemp(prefix="synthetic-production-store-", dir=FIXTURE_BASE)
        self.root = Path(self.temp) / "project" / ".worktrees" / "sandbox" / ".managed-runtime" / "12345678-1234-4234-8234-123456789abc"
        self.root.mkdir(parents=True)
        self.identity = {
            "installation_id": "12345678-1234-4234-8234-123456789abc",
            "data_store_id": "22345678-1234-4234-8234-123456789abc",
            "release_id": "3" * 64,
            "binding_digest": "a" * 64,
        }
        self._write_identity_records()

    def tearDown(self) -> None:
        if getattr(self, "temp", None):
            shutil.rmtree(self.temp)

    def _write_identity_records(self) -> None:
        root = self.root
        for relative in (
            "launcher", "state", "releases", "host/state", "host/profile", "host/workspace", "data/store",
            f"releases/{self.identity['release_id']}/app",
        ):
            (root / relative).mkdir(parents=True, exist_ok=True)
        (Path(self.temp) / FIXTURE_MARKER).write_text("synthetic test identity only\n", encoding="utf-8")
        stable_root = root.parents[3]
        sandbox_root = root.parents[1]
        common_dir = stable_root / ".git"
        sandbox_git_dir = common_dir / "worktrees" / "sandbox"
        sandbox_root.mkdir(parents=True, exist_ok=True)
        sandbox_git_dir.mkdir(parents=True, exist_ok=True)
        source_roots = {"sandbox": str(sandbox_root), "stable": str(stable_root)}
        marker = {
            "format": 1,
            "mode": "managed_project_local_v1",
            "stage": "prepared_empty",
            "data_policy": "empty_no_migration",
            "security_policy": "logical_separation_only",
            "credentials_policy": "forbidden",
            "installation_id": self.identity["installation_id"],
            "root": str(root),
            "source_roots": source_roots,
            "paths": {
                "launcher": str(root / "launcher"), "state": str(root / "state"), "releases": str(root / "releases"),
                "host_state": str(root / "host/state"), "host_profile": str(root / "host/profile"),
                "host_workspace": str(root / "host/workspace"), "data": str(root / "data/store"),
            },
        }
        (root / "state/managed-namespace.json").write_bytes(_canonical(marker) + b"\n")
        release_id = self.identity["release_id"]
        body = {
            "format": 1,
            "mode": "managed_production_binding_v1",
            "installationId": self.identity["installation_id"],
            "namespace": {
                "mode": marker["mode"], "root": str(root), "digest": _sha(_canonical(marker)),
                "securityPolicy": marker["security_policy"], "credentialsPolicy": marker["credentials_policy"],
            },
            "source": {
                "operationId": "42345678-1234-4234-8234-123456789abc", "planDigest": "b" * 64,
                "receiptDigest": "c" * 64, "snapshotDigest": "d" * 64, "mergedHead": "e" * 40,
                "mergedTree": "f" * 64,
                "roots": {
                    "stable": {"worktreeRoot": str(stable_root), "gitDir": str(common_dir), "commonDir": str(common_dir), "branch": "main"},
                    "sandbox": {"worktreeRoot": str(sandbox_root), "gitDir": str(sandbox_git_dir), "commonDir": str(common_dir), "branch": "experiment/sandbox"},
                },
            },
            "release": {
                "releaseId": release_id, "manifestDigest": release_id,
                "root": str(root / "releases" / release_id), "buildReceiptDigest": "5" * 64,
                "toolchainDigest": "6" * 64, "buildPolicy": "configless_snapshot_v2",
            },
            "paths": {
                "launcher": str(root / "launcher"), "state": str(root / "state"),
                "hostState": str(root / "host/state"), "hostProfile": str(root / "host/profile"),
                "hostWorkspace": str(root / "host/workspace"), "data": str(root / "data/store"),
                "code": str(root / "releases" / release_id / "app"),
            },
            "data": {"storeId": self.identity["data_store_id"], "schema": "managed_empty_store_v1", "revision": 0, "policy": "empty_no_migration"},
            "access": "read_only", "runtimeRegistration": None, "writerLease": None, "readyForLaunch": False,
        }
        binding = {**body, "bindingDigest": _sha(_canonical(body))}
        self.identity["binding_digest"] = binding["bindingDigest"]
        (root / "state/production-binding.json").write_bytes(_canonical(binding) + b"\n")

    def make_store(self, **overrides: str):
        return ManagedStore(str(self.root), **(self.identity | overrides))

    def test_empty_store_initializes_then_fenced_bytes_survive_reopen(self) -> None:
        store = self.make_store()
        self.assertEqual(store.inspect()["revision"], 0)
        self.assertFalse((self.root / "state/managed-store-control.sqlite3").exists(), "constructor and inspect must be read-only")
        self.assertFalse((self.root / "state/managed-store.lock").exists(), "constructor and inspect must not create lock files")
        lease = store.acquire("52345678-1234-4234-8234-123456789abc")
        self.assertEqual(lease.generation, 1)
        initialized = store.initialize(lease, "62345678-1234-4234-8234-123456789abc")
        self.assertEqual(store.initialize(lease, "62345678-1234-4234-8234-123456789abc"), initialized)
        self.assertEqual(initialized["revision"], 1)
        content = b"synthetic fixture payload\n"
        written = store.write_bytes(lease, "72345678-1234-4234-8234-123456789abc", "records/one.bin", content)
        self.assertEqual(store.write_bytes(lease, "72345678-1234-4234-8234-123456789abc", "records/one.bin", content), written)
        self.assertEqual(written["revision"], 2)
        self.assertEqual((self.root / "data/store/records/one.bin").read_bytes(), content)
        self.assertNotIn("token", json.dumps(initialized) + json.dumps(written))
        released = store.release(lease, "82345678-1234-4234-8234-123456789abc")
        self.assertEqual(store.release(lease, "82345678-1234-4234-8234-123456789abc"), released)
        reopened = self.make_store()
        metadata = reopened.inspect()
        self.assertEqual(metadata["revision"], 2)
        self.assertEqual(metadata["schema"], "managed_empty_store_v1")
        self.assertFalse(metadata["activeLease"])

    def test_wrong_identity_fixture_mode_and_binding_digest_refuse_before_writes(self) -> None:
        before = set((self.root / "state").iterdir())
        with self.assertRaises(ManagedStoreError):
            self.make_store(data_store_id="92345678-1234-4234-8234-123456789abc")
        with self.assertRaises(ManagedStoreError):
            self.make_store(binding_digest="0" * 64)
        self.assertEqual(set((self.root / "state").iterdir()), before)
        binding_path = self.root / "state/production-binding.json"
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        binding["namespace"]["mode"] = "launcher_fixture_v1"
        binding_body = {key: value for key, value in binding.items() if key != "bindingDigest"}
        binding["bindingDigest"] = _sha(_canonical(binding_body))
        self.identity["binding_digest"] = binding["bindingDigest"]
        binding_path.write_bytes(_canonical(binding) + b"\n")
        with self.assertRaises(ManagedStoreError):
            self.make_store()

    def test_binding_access_schema_release_and_root_substitutions_refuse(self) -> None:
        binding_path = self.root / "state/production-binding.json"
        for field, value, expected_identity in (
            ("access", "read_write", {}),
            ("readyForLaunch", True, {}),
            ("data", {"storeId": self.identity["data_store_id"], "schema": "other_schema", "revision": 0, "policy": "empty_no_migration"}, {}),
            ("release", None, {"release_id": "9" * 64}),
            ("namespace", None, {}),
        ):
            with self.subTest(field=field):
                self._write_identity_records()
                binding = json.loads(binding_path.read_text(encoding="utf-8"))
                if field == "release":
                    binding[field]["releaseId"] = value if value is not None else "9" * 64
                    expected_identity.setdefault("release_id", binding[field]["releaseId"])
                elif field == "namespace":
                    binding[field]["root"] = str(self.root.parent)
                else:
                    binding[field] = value
                body = {key: item for key, item in binding.items() if key != "bindingDigest"}
                binding["bindingDigest"] = _sha(_canonical(body))
                self.identity["binding_digest"] = binding["bindingDigest"]
                binding_path.write_bytes(_canonical(binding) + b"\n")
                with self.assertRaises(ManagedStoreError):
                    self.make_store(**expected_identity)
                expected_identity.clear()
        self.assertFalse((self.root / "state" / "managed-store-control.sqlite3").exists())

    def test_process_lock_blocks_second_writer_and_crashed_owner_requires_maintenance(self) -> None:
        context = multiprocessing.get_context("spawn")
        ready = context.Queue()
        release = context.Event()
        process = context.Process(target=_child_hold_lease, args=(str(self.root), self.identity, ready, release))
        process.start()
        self.assertEqual(ready.get(timeout=10)["acquired"], True)
        with self.assertRaises(StoreBusyError):
            self.make_store().acquire("a2345678-1234-4234-8234-123456789abc")
        release.set()
        process.join(10)
        self.assertEqual(process.exitcode, 0)

        crashed = context.Process(target=_child_crash_with_lease, args=(str(self.root), self.identity, ready))
        crashed.start()
        self.assertEqual(ready.get(timeout=10)["acquired"], True)
        crashed.join(10)
        self.assertEqual(crashed.exitcode, 0)
        with self.assertRaises(MaintenanceRequired):
            self.make_store().acquire("b2345678-1234-4234-8234-123456789abc")

    def test_stale_lease_traversal_and_duplicate_operation_drift_refuse(self) -> None:
        store = self.make_store()
        lease = store.acquire("c2345678-1234-4234-8234-123456789abc")
        store.initialize(lease, "d2345678-1234-4234-8234-123456789abc")
        with self.assertRaises(ManagedStoreError):
            store.write_bytes(lease, "e2345678-1234-4234-8234-123456789abc", "../escape.bin", b"x")
        operation = "f2345678-1234-4234-8234-123456789abc"
        store.write_bytes(lease, operation, "records/item.bin", b"first")
        with self.assertRaises(ManagedStoreError):
            store.write_bytes(lease, operation, "records/item.bin", b"different")
        stale = type(lease)(**{**lease.__dict__, "generation": lease.generation + 1})
        with self.assertRaises(ManagedStoreError):
            store.write_bytes(stale, "a3456789-1234-4234-8234-123456789abc", "records/stale.bin", b"x")

    def test_interrupted_file_write_leaves_durable_pending_intent(self) -> None:
        store = self.make_store()
        lease = store.acquire("b3456789-1234-4234-8234-123456789abc")
        store.initialize(lease, "c3456789-1234-4234-8234-123456789abc")
        with patch.object(store, "_write_exclusive", side_effect=OSError("synthetic interruption")):
            with self.assertRaises(OSError):
                store.write_bytes(lease, "d3456789-1234-4234-8234-123456789abc", "records/pending.bin", b"payload")
        lease._close_process_lock()
        with self.assertRaises(MaintenanceRequired):
            self.make_store().acquire("e3456789-1234-4234-8234-123456789abc")

    def test_tampered_sqlite_identity_record_requires_maintenance(self) -> None:
        store = self.make_store()
        lease = store.acquire("f3456789-1234-4234-8234-123456789abc")
        init_operation = "a456789a-1234-4234-8234-123456789abc"
        store.initialize(lease, init_operation)
        data_db = self.root / "data/store/managed-data.sqlite3"
        def query_record() -> str:
            database = sqlite3.connect(data_db)
            try:
                return database.execute("SELECT record FROM _managed_store_identity WHERE singleton=1").fetchone()[0]
            finally:
                database.close()

        def replace_record(value: str) -> None:
            database = sqlite3.connect(data_db)
            try:
                database.execute("UPDATE _managed_store_identity SET record=? WHERE singleton=1", (value,))
                database.commit()
            finally:
                database.close()

        original_text = query_record()
        original = json.loads(original_text)
        variants = []
        extra = original | {"unexpected": "field"}
        variants.append(_canonical(extra).decode("utf-8"))
        variants.append(json.dumps(original, ensure_ascii=False, separators=(", ", ": ")))
        variants.append(original_text.replace('"revision":1', '"revision":1,"revision":1'))
        variants.append(_canonical(original | {"revision": True}).decode("utf-8"))
        variants.append(_canonical(original | {"revision": 0}).decode("utf-8"))
        for tampered in variants:
            replace_record(tampered)
            self.assertEqual(store.inspect()["state"], "maintenance_required")
            with self.assertRaises(MaintenanceRequired):
                store.initialize(lease, init_operation)
            replace_record(original_text)
        store.release(lease, "b456789a-1234-4234-8234-123456789abc")

    def test_cached_store_refuses_binding_drift_without_advancing_revisions(self) -> None:
        store = self.make_store()
        lease = store.acquire("c456789a-1234-4234-8234-123456789abc")
        store.initialize(lease, "d456789a-1234-4234-8234-123456789abc")
        binding_path = self.root / "state/production-binding.json"
        changed = json.loads(binding_path.read_text(encoding="utf-8"))
        changed["release"]["buildReceiptDigest"] = "7" * 64
        body = {key: value for key, value in changed.items() if key != "bindingDigest"}
        changed["bindingDigest"] = _sha(_canonical(body))
        binding_path.write_bytes(_canonical(changed) + b"\n")

        with self.assertRaises(ManagedStoreError):
            store.write_bytes(lease, "e456789a-1234-4234-8234-123456789abc", "records/drift.bin", b"must not write")
        self.assertEqual(store.inspect()["state"], "maintenance_required")
        self.assertFalse((self.root / "data/store/records/drift.bin").exists())
        database = sqlite3.connect(self.root / "state/managed-store-control.sqlite3")
        try:
            metadata = dict(database.execute("SELECT key,value FROM meta"))
            pending = database.execute("SELECT COUNT(*) FROM operations WHERE status='pending'").fetchone()[0]
        finally:
            database.close()
        self.assertEqual(metadata["revision"], "1")
        self.assertEqual(metadata["initialized"], "1")
        self.assertEqual(pending, 0)
        database = sqlite3.connect(self.root / "data/store/managed-data.sqlite3")
        try:
            schema_record = json.loads(database.execute("SELECT record FROM _managed_store_identity WHERE singleton=1").fetchone()[0])
        finally:
            database.close()
        self.assertEqual(schema_record["revision"], 1)
        with self.assertRaises(ManagedStoreError):
            store.write_bytes(lease, "f456789a-1234-4234-8234-123456789abc", "records/again.bin", b"still refused")


if __name__ == "__main__":
    unittest.main()
