from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import managed_host_entry as entry
import fixed_host
import managed_readonly

SANDBOX = Path(__file__).resolve().parents[2]
ARTIFACTS = SANDBOX / "artifacts" / "managed-runtime-readonly-20261025"
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


class ManagedHostEntryTests(unittest.TestCase):
    def test_runtime_audit_roots_do_not_resolve_absent_optional_python_zip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="runtime-audit-roots-", dir=ARTIFACTS) as temp:
            root = Path(temp)
            code, python, base = (root / name for name in ("app", "python", "base"))
            for directory in (code, python, base):
                directory.mkdir()
            absent_zip = base / "python314.zip"
            runtime = SimpleNamespace(allowed_roots=(code, python, base), allowed_archives=(absent_zip,))
            roots = entry._runtime_audit_roots(runtime)
            self.assertEqual(roots, (python, base))
            guard = managed_readonly.ManagedReadonlyAuditGuard(roots)
            guard.audit("os.listdir", (str(base),))
            with self.assertRaises(PermissionError):
                guard.audit("os.listdir", (str(code),))
            self.assertFalse(absent_zip.exists())

    def test_entry_frame_is_not_accepted_by_fixed_host_fixture_parser(self) -> None:
        frame = {
            "protocol": "my-agent-managed-readonly-v1",
            "namespace_root": str(Path(__file__).resolve().parents[1]),
            "binding_digest": "a" * 64,
            "runtime_digest": "b" * 64,
            "nonce": "12345678-1234-4234-8234-123456789abc",
        }
        raw = canonical(frame) + b"\n"
        self.assertEqual(entry.parse_managed_readonly_bootstrap(raw), frame)
        with self.assertRaises(ValueError):
            fixed_host.parse_fixed_host_layout(raw)
        with self.assertRaises(ValueError):
            entry.parse_managed_readonly_bootstrap(raw + b"{}\n")

    def test_valid_uuid_nonces_reach_entry_boundary_before_missing_entry_refusal(self) -> None:
        for nonce in (str(uuid.uuid4()), "12345678-1234-4abc-8abc-123456789abc"):
            frame = {"protocol": "my-agent-managed-readonly-v1", "namespace_root": str(Path(__file__).resolve().parents[1]),
                     "binding_digest": "a" * 64, "runtime_digest": "b" * 64, "nonce": nonce}
            raw = canonical(frame) + b"\n"
            self.assertEqual(entry.parse_managed_readonly_bootstrap(raw), frame)
            self.assertEqual(managed_readonly.parse_managed_readonly_frame(raw), frame)
            with tempfile.TemporaryDirectory(prefix="missing-managed-entry-", dir=ARTIFACTS) as temp:
                code_root = Path(temp)
                app_core = code_root / "agent-core"
                app_core.mkdir()
                (app_core / "managed_readonly.py").write_text("# synthetic helper only\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    entry.validate_registered_entry(app_core / "managed_host_entry.py", code_root)

    def test_production_entry_refuses_source_entry_outside_bound_code_root(self) -> None:
        with self.assertRaises(ValueError):
            entry.validate_registered_entry(Path("C:/outside/managed_host_entry.py"), Path("C:/bound/app"))

    def test_production_entry_rejects_alias_in_original_entry_path_components(self) -> None:
        with tempfile.TemporaryDirectory(prefix="entry-alias-", dir=ARTIFACTS) as temp:
            code_root = Path(temp)
            app_core = code_root / "agent-core"
            expected = app_core / "managed_host_entry.py"
            alias_directory = app_core / "junction-spelling"
            alias_entry = alias_directory / "managed_host_entry.py"
            alias_directory.mkdir(parents=True)
            expected.write_text("# synthetic sealed entry\n", encoding="utf-8")
            original_resolve = Path.resolve
            original_is_symlink = Path.is_symlink

            def resolve_with_synthetic_junction(path, *args, **kwargs):
                if path == alias_entry:
                    return original_resolve(expected, *args, **kwargs)
                return original_resolve(path, *args, **kwargs)

            def report_original_junction(path):
                if path == alias_directory:
                    return True
                return original_is_symlink(path)

            with patch.object(Path, "resolve", resolve_with_synthetic_junction), \
                    patch.object(Path, "is_symlink", report_original_junction):
                with self.assertRaisesRegex(ValueError, "outside the bound application"):
                    entry.validate_registered_entry(alias_entry, code_root)

    def test_registered_entry_requires_canonical_original_absolute_spelling(self) -> None:
        with tempfile.TemporaryDirectory(prefix="entry-spelling-", dir=ARTIFACTS) as temp:
            code_root = Path(temp)
            app_core = code_root / "agent-core"
            expected = app_core / "managed_host_entry.py"
            (app_core / "a").mkdir(parents=True)
            expected.write_text("# synthetic bound entry\n", encoding="utf-8")

            entry.validate_registered_entry(expected, code_root)

            parent_component_alias = app_core / "a" / ".." / "managed_host_entry.py"
            with self.assertRaisesRegex(ValueError, "outside the bound application"):
                entry.validate_registered_entry(parent_component_alias, code_root)

    def test_audit_hook_denies_process_and_non_loopback_network_events(self) -> None:
        guard = managed_readonly.ManagedReadonlyAuditGuard(())
        for event, args in (("subprocess.Popen", ()), ("os.system", ("echo x",)), ("os.chdir", ("C:/",)),
                            ("os.startfile", ("C:/outside.exe",)), ("os.startfile/2", ("C:/outside.exe", None, None)),
                            ("os.listdir", (str(Path.cwd()),)), ("os.scandir", (str(Path.cwd()),)),
                            ("socket.connect", (None, ("203.0.113.7", 443)))):
            with self.subTest(event=event), self.assertRaises(PermissionError):
                guard.audit(event, args)
        guard.audit("open", (0, "r", 0))
        guard.audit("open", (1, "w", 0))
        with self.assertRaises(PermissionError):
            guard.audit("open", (3, "w", 0))

    def test_audit_hook_fails_closed_for_windows_createfile_handles(self) -> None:
        guard = managed_readonly.ManagedReadonlyAuditGuard(())
        createfile_args = ("C:/registered/readme.txt", 0x40000000, 0x7, None, 2, 0x80, None)
        with self.assertRaises(PermissionError):
            guard.audit("_winapi.CreateFile", createfile_args)

    def test_production_bootstrap_checks_sealed_code_before_helper_imports(self) -> None:
        canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        with tempfile.TemporaryDirectory(prefix="sealed-app-", dir=ARTIFACTS) as temp:
            namespace = Path(temp)
            release_root = namespace / "releases" / ("0" * 64)
            app = release_root / "app" / "agent-core"
            app.mkdir(parents=True)
            payloads = {"agent-core/managed_host_entry.py": b"# synthetic entry\n", "agent-core/managed_readonly.py": b"# synthetic helper\n"}
            files = []
            for relative, payload in payloads.items():
                path = release_root / "app" / relative
                path.write_bytes(payload)
                files.append({"path": relative, "mode": "100644", "sha256": hashlib.sha256(payload).hexdigest(), "byteLength": len(payload)})
            source = {"snapshotDigest": "1" * 64, "planDigest": "2" * 64, "receiptDigest": "3" * 64, "mergedHead": "4" * 40, "mergedTree": "5" * 40}
            build = {"receiptDigest": "6" * 64, "toolchainDigest": "7" * 64, "policy": "configless_snapshot_v2"}
            manifest = {"format": 1, "mode": "managed_postmerge_release_v1", "protocol": 1, "source": source,
                        "build": build, "data": {"policy": "empty_no_migration", "schema": "managed_empty_store_v1", "revisionBefore": 0},
                        "files": files, "readyForLaunch": False}
            release_id = hashlib.sha256(canonical(manifest)).hexdigest()
            final_root = namespace / "releases" / release_id
            if final_root != release_root:
                release_root.rename(final_root)
            manifest_path = final_root / "manifest.json"
            manifest_path.write_bytes(canonical(manifest) + b"\n")
            operation_id = "12345678-1234-4234-8234-123456789abc"
            seal_intent = {"format": 1, "mode": "managed_postmerge_release_seal_intent_v1", "operationId": operation_id,
                           "releaseId": release_id, "manifestDigest": release_id, "state": "started"}
            seal_intent["intentDigest"] = hashlib.sha256(canonical(seal_intent)).hexdigest()
            seal_receipt = {"format": 1, "mode": "managed_postmerge_release_seal_receipt_v1", "operationId": operation_id,
                            "releaseId": release_id, "manifestDigest": release_id,
                            "filesDigest": hashlib.sha256(canonical(files)).hexdigest(), "readyForLaunch": False}
            seal_receipt["receiptDigest"] = hashlib.sha256(canonical(seal_receipt)).hexdigest()
            (final_root / "seal-intent.json").write_bytes(canonical(seal_intent) + b"\n")
            (final_root / "sealed.json").write_bytes(canonical(seal_receipt) + b"\n")
            binding = {"source": {**source, "operationId": operation_id},
                       "release": {"releaseId": release_id, "manifestDigest": release_id, "buildReceiptDigest": build["receiptDigest"],
                                   "toolchainDigest": build["toolchainDigest"], "buildPolicy": build["policy"]}}
            self.assertEqual(entry._verify_sealed_application(namespace, binding), final_root / "app")
            # Node context receipts bind the copied complete release, whereas
            # seal receipts bind the app manifest's file array.
            complete_inventory = [{**item, "path": "app/" + item["path"]} for item in files]
            for name in ("manifest.json", "seal-intent.json", "sealed.json"):
                path = final_root / name
                payload = path.read_bytes()
                complete_inventory.append({"path": name, "mode": "100755" if path.stat().st_mode & 0o111 else "100644",
                                           "sha256": hashlib.sha256(payload).hexdigest(), "byteLength": len(payload)})
            context_receipt = {"filesDigest": hashlib.sha256(canonical(complete_inventory)).hexdigest(),
                               "payloadBytes": sum(item["byteLength"] for item in files)}
            with self.subTest(context_receipt="complete sealed release"):
                self.assertEqual(entry._verify_sealed_application(namespace, binding, context_receipt), final_root / "app")
            for invalid in ({**context_receipt, "filesDigest": hashlib.sha256(canonical(files)).hexdigest()},
                            {**context_receipt, "payloadBytes": context_receipt["payloadBytes"] + 1}):
                with self.subTest(invalid_context=invalid), self.assertRaises(ValueError):
                    entry._verify_sealed_application(namespace, binding, invalid)
            (final_root / "app/agent-core/managed_readonly.py").write_bytes(b"# tampered\n")
            with self.assertRaises(ValueError):
                entry._verify_sealed_application(namespace, binding)


if __name__ == "__main__":
    unittest.main()
