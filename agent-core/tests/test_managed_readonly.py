from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import queue
import uuid
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import managed_readonly as readonly

SANDBOX = Path(__file__).resolve().parents[2]
ARTIFACTS = SANDBOX / "artifacts" / "managed-runtime-readonly-20261025"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
PYTHON = Path("F:/my-agent-main/.venv/Scripts/python.exe")
SITE_PACKAGES = Path("F:/my-agent-main/.venv/Lib/site-packages")


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def seal(record: dict, field: str) -> dict:
    return {**record, field: hashlib.sha256(canonical(record)).hexdigest()}


def make_source_probe_records(temp_root: Path) -> tuple[Path, Path, dict, dict, dict, dict, dict]:
    installation_id = "12345678-1234-4234-8234-123456789abc"
    data_store_id = "22345678-1234-4234-8234-123456789abc"
    operation_id = "32345678-1234-4234-8234-123456789abc"
    release_id = "c" * 64
    source_sandbox = temp_root / "sandbox-root"
    source_stable = temp_root / "stable-root"
    common = temp_root / "git-common"
    sandbox_git = common / "worktrees/sandbox"
    stable_git = common / "worktrees/stable"
    for directory in (source_sandbox, source_stable, common, sandbox_git, stable_git):
        directory.mkdir(parents=True, exist_ok=True)
    namespace = source_sandbox / ".managed-runtime" / installation_id
    for relative in ("launcher", "state", "releases/" + release_id + "/app", "host/state", "host/profile", "host/workspace", "data/store"):
        (namespace / relative).mkdir(parents=True, exist_ok=True)
    marker = {
        "format": 1, "mode": "managed_project_local_v1", "stage": "prepared_empty",
        "data_policy": "empty_no_migration", "security_policy": "logical_separation_only", "credentials_policy": "forbidden",
        "installation_id": installation_id, "root": str(namespace),
        "source_roots": {"stable": str(source_stable), "sandbox": str(source_sandbox)},
        "paths": {"launcher": str(namespace / "launcher"), "state": str(namespace / "state"), "releases": str(namespace / "releases"),
                  "host_state": str(namespace / "host/state"), "host_profile": str(namespace / "host/profile"),
                  "host_workspace": str(namespace / "host/workspace"), "data": str(namespace / "data/store")},
    }
    binding_body = {
        "format": 1, "mode": "managed_production_binding_v1", "installationId": installation_id,
        "namespace": {"mode": marker["mode"], "root": str(namespace), "digest": hashlib.sha256(canonical(marker)).hexdigest(),
                      "securityPolicy": marker["security_policy"], "credentialsPolicy": marker["credentials_policy"]},
        "source": {"operationId": operation_id, "planDigest": "1" * 64, "receiptDigest": "2" * 64, "snapshotDigest": "3" * 64,
                   "mergedHead": "4" * 40, "mergedTree": "5" * 40,
                   "roots": {
                       "stable": {"worktreeRoot": str(source_stable), "gitDir": str(stable_git), "commonDir": str(common), "branch": "main"},
                       "sandbox": {"worktreeRoot": str(source_sandbox), "gitDir": str(sandbox_git), "commonDir": str(common), "branch": "experiment/sandbox"},
                   }},
        "release": {"releaseId": release_id, "manifestDigest": release_id, "root": str(namespace / "releases" / release_id),
                    "buildReceiptDigest": "6" * 64, "toolchainDigest": "7" * 64, "buildPolicy": "configless_snapshot_v2"},
        "paths": {"launcher": str(namespace / "launcher"), "state": str(namespace / "state"), "hostState": str(namespace / "host/state"),
                  "hostProfile": str(namespace / "host/profile"), "hostWorkspace": str(namespace / "host/workspace"),
                  "data": str(namespace / "data/store"), "code": str(SANDBOX)},
        "data": {"storeId": data_store_id, "schema": "managed_empty_store_v1", "revision": 0, "policy": "empty_no_migration"},
        "access": "read_only", "runtimeRegistration": None, "writerLease": None, "readyForLaunch": False,
    }
    binding = seal(binding_body, "bindingDigest")
    context_intent = seal({"format": 1, "mode": "managed_production_context_intent_v1", "installationId": installation_id,
                           "operationId": operation_id, "releaseId": release_id, "namespaceDigest": hashlib.sha256(canonical(marker)).hexdigest(),
                           "bindingDigest": binding["bindingDigest"], "dataStoreId": data_store_id, "state": "copy_started", "readyForLaunch": False}, "intentDigest")
    context_receipt = seal({"format": 1, "mode": "managed_production_context_receipt_v1", "installationId": installation_id,
                            "operationId": operation_id, "releaseId": release_id, "bindingDigest": binding["bindingDigest"],
                            "namespaceDigest": hashlib.sha256(canonical(marker)).hexdigest(), "manifestDigest": release_id,
                            "dataStoreId": data_store_id, "filesDigest": "8" * 64, "payloadBytes": 0, "allocationBytes": 0,
                            "readyForLaunch": False}, "receiptDigest")
    (namespace / "state/managed-namespace.json").write_bytes(canonical(marker) + b"\n")
    (namespace / "state/production-context-intent.json").write_bytes(canonical(context_intent) + b"\n")
    (namespace / "state/production-binding.json").write_bytes(canonical(binding) + b"\n")
    (namespace / "state/production-context-receipt.json").write_bytes(canonical(context_receipt) + b"\n")
    runtimes = {}
    runtime_roots = {}
    specs = {"node": ("single_executable_v1", "node.exe"), "electron": ("complete_tree_v1", "electron.exe"),
             "python": ("complete_tree_v1", "Scripts/python.exe"), "pythonBase": ("complete_tree_v1", "python.exe")}
    for name, (policy, entry) in specs.items():
        root = temp_root / "synthetic-runtime" / name
        file_path = root / entry
        file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = ("synthetic " + name).encode()
        runtime_roots[name] = root
        directories = [] if name in ("node", "electron", "pythonBase") else ["Scripts"]
        runtimes[name] = {"root": str(root), "entry": entry, "policy": policy,
                          "files": [{"path": entry, "sha256": hashlib.sha256(payload).hexdigest(), "byteLength": len(payload)}],
                          "directories": directories}
    registration = seal({"format": 1, "mode": "managed_runtime_registration_v1", "installationId": installation_id,
                         "releaseId": release_id, "bindingDigest": binding["bindingDigest"], "runtimes": runtimes,
                         "readyForLaunch": False}, "runtimeDigest")
    runtime_intent = seal({"format": 1, "mode": "managed_runtime_intent_v1", "installationId": installation_id,
                           "releaseId": release_id, "bindingDigest": binding["bindingDigest"], "runtimeDigest": registration["runtimeDigest"],
                           "state": "registration_started", "readyForLaunch": False}, "intentDigest")
    runtime_receipt = seal({"format": 1, "mode": "managed_runtime_receipt_v1", "installationId": installation_id,
                            "releaseId": release_id, "bindingDigest": binding["bindingDigest"], "runtimeDigest": registration["runtimeDigest"],
                            "registrationDigest": hashlib.sha256(canonical(registration)).hexdigest(), "readyForLaunch": False}, "receiptDigest")
    (namespace / "launcher/runtime-intent.json").write_bytes(canonical(runtime_intent) + b"\n")
    (namespace / "launcher/runtime-registration.json").write_bytes(canonical(registration) + b"\n")
    (namespace / "launcher/runtime-receipt.json").write_bytes(canonical(runtime_receipt) + b"\n")
    return namespace, Path(SANDBOX), marker, binding, registration, runtime_intent, runtime_receipt


class ManagedReadonlyFrameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.frame = {"protocol": "my-agent-managed-readonly-v1", "namespace_root": str(Path(__file__).resolve().parents[1]),
                      "binding_digest": "a" * 64, "runtime_digest": "b" * 64,
                      "nonce": "12345678-1234-4234-8234-123456789abc"}

    def test_frame_is_exact_canonical_single_bounded_line(self) -> None:
        raw = canonical(self.frame) + b"\n"
        self.assertEqual(readonly.parse_managed_readonly_frame(raw), self.frame)
        for invalid in (canonical({**self.frame, "code_root": "C:/caller/code"}) + b"\n",
                        raw.replace(b'"protocol":', b'"protocol":"wrong","protocol":', 1),
                        json.dumps(self.frame).encode() + b"\n", raw + b"{}\n", raw[:-1] + b"\r\n",
                        b" " * (readonly.MAX_FRAME_BYTES + 1)):
            with self.subTest(raw=invalid[:50]), self.assertRaises(ValueError):
                readonly.parse_managed_readonly_frame(invalid)

    def test_digest_and_nonce_validation_is_strict(self) -> None:
        for nonce in (str(uuid.uuid4()), "12345678-1234-4abc-8abc-123456789abc"):
            with self.subTest(valid_nonce=nonce):
                self.assertEqual(readonly.parse_managed_readonly_frame(canonical({**self.frame, "nonce": nonce}) + b"\n")["nonce"], nonce)
        for field, value in (("binding_digest", "A" * 64), ("runtime_digest", "0" * 63), ("nonce", "not-a-uuid")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                readonly.parse_managed_readonly_frame(canonical({**self.frame, field: value}) + b"\n")

    def test_runtime_receipt_registration_digest_excludes_newline_and_binds_all_identities(self) -> None:
        registration = seal({"format": 1, "mode": "managed_runtime_registration_v1", "installationId": "12345678-1234-4234-8234-123456789abc",
                             "releaseId": "c" * 64, "bindingDigest": "a" * 64, "runtimes": {}, "readyForLaunch": False}, "runtimeDigest")
        intent = seal({"format": 1, "mode": "managed_runtime_intent_v1", "installationId": registration["installationId"],
                       "releaseId": registration["releaseId"], "bindingDigest": "a" * 64, "runtimeDigest": registration["runtimeDigest"],
                       "state": "registration_started", "readyForLaunch": False}, "intentDigest")
        receipt = seal({"format": 1, "mode": "managed_runtime_receipt_v1", "installationId": registration["installationId"],
                        "releaseId": registration["releaseId"], "bindingDigest": "a" * 64, "runtimeDigest": registration["runtimeDigest"],
                        "registrationDigest": hashlib.sha256(canonical(registration)).hexdigest(), "readyForLaunch": False}, "receiptDigest")
        with self.assertRaises(ValueError):
            readonly.validate_runtime_record_chain(registration, intent, receipt, "a" * 64)
        fake_root = Path(__file__).resolve().parents[1] / "synthetic-runtime"
        runtimes = {
            "node": {"root": str(fake_root / "node"), "entry": "node.exe", "policy": "single_executable_v1", "files": [{"path": "node.exe", "sha256": "0" * 64, "byteLength": 1}], "directories": []},
            "electron": {"root": str(fake_root / "electron"), "entry": "electron.exe", "policy": "complete_tree_v1", "files": [{"path": "electron.exe", "sha256": "0" * 64, "byteLength": 1}], "directories": []},
            "python": {"root": str(fake_root / "python"), "entry": "Scripts/python.exe", "policy": "complete_tree_v1", "files": [{"path": "Scripts/python.exe", "sha256": "0" * 64, "byteLength": 1}], "directories": ["Scripts"]},
            "pythonBase": {"root": str(fake_root / "python-base"), "entry": "python.exe", "policy": "complete_tree_v1", "files": [{"path": "python.exe", "sha256": "0" * 64, "byteLength": 1}], "directories": []},
        }
        registration = seal({**{key: value for key, value in registration.items() if key != "runtimeDigest"}, "runtimes": runtimes}, "runtimeDigest")
        intent = seal({**{key: value for key, value in intent.items() if key != "intentDigest"}, "runtimeDigest": registration["runtimeDigest"]}, "intentDigest")
        receipt_body = {**{key: value for key, value in receipt.items() if key not in {"receiptDigest", "registrationDigest"}},
                        "runtimeDigest": registration["runtimeDigest"], "registrationDigest": hashlib.sha256(canonical(registration)).hexdigest()}
        receipt = seal(receipt_body, "receiptDigest")
        self.assertTrue(readonly.validate_runtime_record_chain(registration, intent, receipt, "a" * 64,
                                                               installation_id=registration["installationId"], release_id=registration["releaseId"]))
        newline_digest = {**receipt, "registrationDigest": hashlib.sha256(canonical(registration) + b"\n").hexdigest()}
        newline_digest = seal({key: value for key, value in newline_digest.items() if key != "receiptDigest"}, "receiptDigest")
        with self.assertRaises(ValueError):
            readonly.validate_runtime_record_chain(registration, intent, newline_digest, "a" * 64)
        with self.assertRaises(ValueError):
            readonly.validate_runtime_record_chain(registration, intent, receipt, "a" * 64, installation_id="92345678-1234-4234-8234-123456789abc")


class ManagedReadonlyContextTests(unittest.TestCase):
    def test_paths_bind_only_from_production_records_and_map_managed_roots(self) -> None:
        from paths import AgentPaths

        with tempfile.TemporaryDirectory(prefix="context-", dir=ARTIFACTS) as temp:
            namespace, source_code, marker, binding, registration, intent, receipt = make_source_probe_records(Path(temp))
            context = readonly._validate_context_records_for_source_probe(namespace, marker, binding, registration, intent, receipt, code_root=source_code)
            paths = AgentPaths.from_managed_readonly(context)
            self.assertEqual(paths.agent_root, source_code)
            self.assertEqual(paths.evolve, source_code / "evolve")
            self.assertEqual(paths.workspace, namespace / "host/workspace")
            self.assertEqual(paths.data, namespace / "data/store")
            self.assertIs(paths._managed_readonly_context, context)

    def test_runtime_tree_verification_allows_registered_empty_directories_and_rejects_extra_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="inventory-", dir=ARTIFACTS) as temp:
            root = Path(temp)
            python_root = root / "python-runtime"
            (python_root / "Scripts/empty").mkdir(parents=True)
            data = b"synthetic python"
            executable = python_root / "Scripts/python.exe"
            executable.write_bytes(data)
            runtime_records = {}
            for name, filename, policy in (("node", "node.exe", "single_executable_v1"),
                                           ("electron", "electron.exe", "complete_tree_v1"),
                                           ("pythonBase", "python.exe", "complete_tree_v1")):
                other_root = root / name
                other_root.mkdir()
                content = ("synthetic " + name).encode()
                (other_root / filename).write_bytes(content)
                runtime_records[name] = {"root": str(other_root), "entry": filename, "policy": policy,
                                         "files": [{"path": filename, "sha256": hashlib.sha256(content).hexdigest(), "byteLength": len(content)}],
                                         "directories": []}
            node_neighbor = root / "node" / "unrelated-directory"
            node_neighbor.mkdir()
            (node_neighbor / "outside-node-inventory.txt").write_text("synthetic", encoding="utf-8")
            registration = {"runtimes": {
                "node": runtime_records["node"],
                "electron": runtime_records["electron"],
                "python": {"root": str(python_root), "entry": "Scripts/python.exe", "policy": "complete_tree_v1", "files": [{"path": "Scripts/python.exe", "sha256": hashlib.sha256(data).hexdigest(), "byteLength": len(data)}], "directories": ["Scripts", "Scripts/empty"]},
                "pythonBase": runtime_records["pythonBase"],
            }}
            readonly.verify_runtime_inventory(registration)
            (python_root / "unexpected.txt").write_text("synthetic", encoding="utf-8")
            with self.assertRaises(ValueError):
                readonly.verify_runtime_inventory(registration)

    def test_single_executable_runtime_does_not_enumerate_unregistered_root_sidecars(self) -> None:
        with tempfile.TemporaryDirectory(prefix="single-runtime-", dir=ARTIFACTS) as temp:
            root = Path(temp)
            registration = self._make_minimal_runtime_registration(root)
            node_root = Path(registration["runtimes"]["node"]["root"])
            (node_root / "npm").mkdir()
            (node_root / "npm/npm.cmd").write_text("synthetic sidecar", encoding="utf-8")
            original_scandir = os.scandir
            scanned: list[Path] = []

            def track_scandir(path):
                scanned.append(Path(path))
                return original_scandir(path)

            with patch.object(readonly.os, "scandir", side_effect=track_scandir):
                readonly.verify_runtime_inventory(registration)

            self.assertNotIn(node_root, scanned)

    def test_complete_tree_scan_caps_aggregate_entries_before_retaining_them(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bounded-runtime-tree-", dir=ARTIFACTS) as temp:
            root = Path(temp)
            registration = self._make_minimal_runtime_registration(root)
            electron_root = Path(registration["runtimes"]["electron"]["root"])
            for index in range(8):
                (electron_root / f"unregistered-{index}.bin").write_bytes(b"x")

            with patch.object(readonly, "MAX_RUNTIME_FILES", 6):
                with self.assertRaisesRegex(readonly.ManagedReadonlyError, "actual tree entry limit exceeded"):
                    readonly.verify_runtime_inventory(registration)

    @staticmethod
    def _make_minimal_runtime_registration(root: Path) -> dict:
        runtimes = {}
        specifications = (("node", "node.exe", "single_executable_v1"),
                          ("electron", "electron.exe", "complete_tree_v1"),
                          ("python", "Scripts/python.exe", "complete_tree_v1"),
                          ("pythonBase", "python.exe", "complete_tree_v1"))
        for name, entry, policy in specifications:
            runtime_root = root / name
            executable = runtime_root / entry
            executable.parent.mkdir(parents=True, exist_ok=True)
            payload = ("synthetic " + name).encode()
            executable.write_bytes(payload)
            directories = ["Scripts"] if name == "python" else []
            if name == "python":
                (runtime_root / "Scripts/empty").mkdir()
                directories.append("Scripts/empty")
            runtimes[name] = {"root": str(runtime_root), "entry": entry, "policy": policy,
                              "files": [{"path": entry, "sha256": hashlib.sha256(payload).hexdigest(), "byteLength": len(payload)}],
                              "directories": directories}
        return {"runtimes": runtimes}

    def test_source_probe_server_is_real_websocket_subprocess_and_readonly(self) -> None:
        with tempfile.TemporaryDirectory(prefix="websocket-source-probe-", dir=ARTIFACTS) as temp:
            namespace, source_code, marker, binding, registration, intent, receipt = make_source_probe_records(Path(temp))
            probe_nonce = str(uuid.uuid4())

            def snapshot() -> list[tuple]:
                entries = []
                for path in namespace.rglob("*"):
                    relative = str(path.relative_to(namespace)).replace("\\", "/")
                    if path.is_dir():
                        entries.append(("directory", relative))
                    elif path.is_file():
                        entries.append(("file", relative, path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest()))
                    else:
                        entries.append(("other", relative))
                return sorted(entries)

            before = snapshot()
            external = Path(temp) / "outside-private.txt"
            external.write_text("synthetic private marker", encoding="utf-8")
            child = f'''import asyncio, os, pathlib, socket, subprocess, sys
sys.path.insert(0, {str(source_code / "agent-core")!r})
sys.path.insert(0, {str(SITE_PACKAGES)!r})
import managed_readonly as mr
ns = pathlib.Path({str(namespace)!r})
marker = mr._parse_canonical_record(ns / "state/managed-namespace.json")
binding = mr._parse_canonical_record(ns / "state/production-binding.json")
registration = mr._parse_canonical_record(ns / "launcher/runtime-registration.json")
intent = mr._parse_canonical_record(ns / "launcher/runtime-intent.json")
receipt = mr._parse_canonical_record(ns / "launcher/runtime-receipt.json")
context = mr._validate_context_records_for_source_probe(ns, marker, binding, registration, intent, receipt, code_root=pathlib.Path({str(source_code)!r}), nonce={probe_nonce!r})
from managed_readonly import ManagedReadonlyAuditGuard, read_roots_for_context
roots = read_roots_for_context(context, [pathlib.Path(sys.base_prefix), pathlib.Path(sys.prefix)])
ManagedReadonlyAuditGuard(roots).install()
checks = []
for operation in (lambda: open({str(external)!r}, "r"), lambda: os.listdir({str(Path(temp))!r}), lambda: os.scandir({str(Path(temp))!r}), lambda: open({str(namespace / "write.txt")!r}, "w"), lambda: subprocess.run([sys.executable, "-V"]), lambda: socket.socket().connect(("203.0.113.7", 9))):
    try: operation(); checks.append(False)
    except (PermissionError, OSError): checks.append(True)
if checks != [True, True, True, True, True, True]: raise RuntimeError("audit guard source probe failed")
import server
asyncio.run(server.run_server("127.0.0.1", 0, managed_readonly_context=context))
'''
            system_root = os.environ.get("SystemRoot", r"C:\Windows")
            runtime_path = os.pathsep.join((str(PYTHON.parent), str(PYTHON.parents[1]), str(Path(system_root) / "System32")))
            env = {"SystemRoot": system_root, "WINDIR": os.environ.get("WINDIR", system_root),
                   "TEMP": temp, "TMP": temp, "PATH": runtime_path}
            process = subprocess.Popen([str(PYTHON), "-I", "-B", "-S", "-c", child], cwd=temp, env=env,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, encoding="utf-8", errors="replace", bufsize=1)
            try:
                ready_queue: queue.Queue[str] = queue.Queue(maxsize=1)
                reader = threading.Thread(target=lambda: ready_queue.put(process.stdout.readline()), daemon=True)
                reader.start()
                try:
                    ready_line = ready_queue.get(timeout=20)
                except queue.Empty:
                    process.terminate()
                    self.fail("source probe child did not become ready within 20 seconds")
                self.assertTrue(ready_line, process.stderr.read() if process.poll() is not None else "source probe did not become ready")
                ready = json.loads(ready_line)
                self.assertEqual(ready["mode"], "managed_readonly")
                self.assertEqual(ready["access"], "read_only")
                self.assertEqual(ready["binding_digest"], binding["bindingDigest"])
                self.assertEqual(ready["runtime_digest"], registration["runtimeDigest"])
                self.assertEqual(ready["nonce"], probe_nonce)
                from websockets.asyncio.client import connect

                async def websocket_probe() -> list[dict]:
                    async with connect(f"ws://127.0.0.1:{ready['port']}") as websocket:
                        responses = []
                        for request in ({"type": "managed.health", "request_id": "health"}, {"type": "managed.ping"},
                                        {"type": "session.list"}, {"type": "user.message", "text": "must not dispatch"}):
                            await websocket.send(json.dumps(request))
                            responses.append(json.loads(await websocket.recv()))
                        return responses

                responses = asyncio.run(websocket_probe())
                self.assertEqual(responses[0]["type"], "managed.health")
                self.assertNotIn("healthReceipt", responses[0])
                self.assertEqual(responses[1]["type"], "managed.pong")
                self.assertEqual(responses[2], {"type": "session.list", "sessions": []})
                self.assertEqual(responses[3]["code"], "managed_readonly_rejected")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                stderr = process.stderr.read()
                process.stdout.close()
                process.stderr.close()
            if process.returncode not in (0, -15, 1):
                self.fail(f"source probe child exit {process.returncode}: {stderr[-2000:]}")
            after = snapshot()
            self.assertEqual(after, before)
            self.assertFalse((namespace / "data/store/state.json").exists())
            inventory_digest_before = hashlib.sha256(canonical(before)).hexdigest()
            inventory_digest_after = hashlib.sha256(canonical(after)).hexdigest()
            evidence = {
                "format": 1,
                "scope": "engineering_source_server_synthetic_records",
                "ready": True,
                "installationId": binding["installationId"],
                "releaseId": binding["release"]["releaseId"],
                "bindingDigest": binding["bindingDigest"],
                "runtimeDigest": registration["runtimeDigest"],
                "nonce": probe_nonce,
                "requestTypes": ["managed.health", "managed.ping", "session.list", "user.message"],
                "rejectedMutations": ["user.message"],
                "auditDenials": ["unregistered_read", "filesystem_write", "subprocess", "non_loopback_connect"],
                "beforeInventoryDigest": inventory_digest_before,
                "afterInventoryDigest": inventory_digest_after,
                "inventoryUnchanged": inventory_digest_before == inventory_digest_after,
                "shutdownObserved": process.returncode is not None,
                "nativeApplication": False,
                "sealedPostmergeHealth": False,
                "writerLeaseAcquired": False,
            }
            (ARTIFACTS / "source-server-probe.json").write_bytes(canonical(evidence) + b"\n")


if __name__ == "__main__":
    unittest.main()
