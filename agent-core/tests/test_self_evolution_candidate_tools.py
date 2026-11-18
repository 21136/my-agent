from __future__ import annotations

import os
import base64
import hashlib
import json
import sys
import tempfile
import unittest
import stat
import subprocess
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_evolution_candidate_tools import build_self_evolution_candidate_tools
from self_evolution_verification import VerificationRunner, WorkspaceBinding


class SelfEvolutionCandidateToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "candidate"
        self.root.mkdir()
        self.outside = self.base / "stable"
        self.outside.mkdir()
        self.stable_file = self.outside / "keep.txt"
        self.stable_file.write_text("stable", encoding="utf-8")
        self.existing = self.root / "src" / "module.py"
        self.existing.parent.mkdir()
        self.existing.write_text("VALUE = 1\n", encoding="utf-8")
        self.live = True
        self.binding = WorkspaceBinding(
            task_id="task-1", candidate_root=self.root, candidate_id="candidate-1",
            config_identity="config-1", source_paths=("src/module.py",),
        )
        self.context = SimpleNamespace(
            session_id="session-1", task_id="task-1", attempt_id="attempt-1",
            run_id="run-1", candidate_id="candidate-1", candidate_digest="digest-before",
            cancel_event=__import__("threading").Event(),
            current_candidate_digest=lambda: "digest-current",
        )
        self.runner = VerificationRunner(
            {"failing-check": (sys.executable, "-c", "raise SystemExit(7)")},
            workspace_resolver=lambda task_id: self.binding if task_id == "task-1" else None,
            output_limit_chars=128,
        )
        self.bindings = {"task-1": self.binding}
        self.tools = build_self_evolution_candidate_tools(
            resolve_candidate_root=lambda context: self.bindings[context.task_id].candidate_root
            if context.task_id in self.bindings else None,
            validate_binding=lambda context, root: self.live and context.task_id in self.bindings
            and Path(root).resolve() == Path(self.bindings[context.task_id].candidate_root).resolve(),
            verification_runner=self.runner,
            registered_check_ids={"failing-check"},
        )
        self.by_name = {tool.name: tool for tool in self.tools}

    def invoke(self, name: str, arguments: dict | None = None):
        return self.by_name[name].invoke(self.context, arguments or {})

    def test_observe_read_edit_and_add_only_inside_candidate(self) -> None:
        observed = self.invoke("observe_candidate")
        self.assertEqual(observed["status"], "observed")
        self.assertIn("src/module.py", [item["path"] for item in observed["files"]])
        self.assertNotIn(str(self.root), repr(observed))

        read = self.invoke("read_candidate_file", {"path": "src/module.py"})
        self.assertEqual(read["content"].replace("\r\n", "\n"), "VALUE = 1\n")
        edited = self.invoke("write_candidate_file", {"path": "src/module.py", "content": "VALUE = 2\n"})
        added = self.invoke("write_candidate_file", {"path": "new/nested.txt", "content": "candidate only"})
        self.assertEqual(edited["status"], "written")
        self.assertTrue(edited["readback_verified"])
        self.assertTrue(added["created"])
        self.assertEqual(self.existing.read_text(encoding="utf-8"), "VALUE = 2\n")
        self.assertEqual((self.root / "new/nested.txt").read_text(encoding="utf-8"), "candidate only")
        self.assertEqual(self.stable_file.read_text(encoding="utf-8"), "stable")
        self.assertNotIn(str(self.root), repr(edited))

    def test_whole_write_rejects_concurrent_same_size_content_change(self) -> None:
        target = self.existing
        original = target.read_bytes()
        changed = b"X" * len(original)
        before = target.stat()
        real_mkstemp = tempfile.mkstemp

        def race(*args, **kwargs):
            fd, path = real_mkstemp(*args, **kwargs)
            target.write_bytes(changed)
            os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
            return fd, path

        with mock.patch("self_evolution_candidate_tools.tempfile.mkstemp", side_effect=race):
            with self.assertRaises(PermissionError):
                self.invoke("write_candidate_file", {"path": "src/module.py", "content": "worker edit"})
        self.assertEqual(target.read_bytes(), changed)
        self.assertEqual(self.stable_file.read_text(encoding="utf-8"), "stable")

    def test_whole_write_rejects_concurrent_creation(self) -> None:
        target = self.root / "src" / "new.py"
        real_mkstemp = tempfile.mkstemp

        def race(*args, **kwargs):
            fd, path = real_mkstemp(*args, **kwargs)
            target.write_bytes(b"other writer")
            return fd, path

        with mock.patch("self_evolution_candidate_tools.tempfile.mkstemp", side_effect=race):
            with self.assertRaises(PermissionError):
                self.invoke("write_candidate_file", {"path": "src/new.py", "content": "worker edit"})
        self.assertEqual(target.read_bytes(), b"other writer")

    def test_existing_file_mode_is_applied_to_replacement(self) -> None:
        target = self.existing
        original_mode = stat.S_IMODE(target.stat().st_mode)
        real_chmod = os.chmod
        with mock.patch("self_evolution_candidate_tools.os.chmod", wraps=real_chmod) as chmod:
            self.invoke("write_candidate_file", {"path": "src/module.py", "content": "worker edit"})
        self.assertTrue(any(call.args[1] == original_mode for call in chmod.call_args_list))
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), original_mode)

    @unittest.skipIf(os.name == "nt", "POSIX executable mode bits are not represented by Windows chmod")
    def test_existing_executable_mode_is_preserved(self) -> None:
        os.chmod(self.existing, 0o751)
        self.invoke("write_candidate_file", {"path": "src/module.py", "content": "worker edit"})
        self.assertEqual(stat.S_IMODE(self.existing.stat().st_mode), 0o751)

    @unittest.skipUnless(os.name == "nt", "ReplaceFileW and Windows DACLs require Windows")
    def test_windows_existing_file_replacements_preserve_explicit_dacl(self) -> None:
        import ctypes
        from ctypes import wintypes

        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        get_security = advapi.GetFileSecurityW
        get_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                 ctypes.POINTER(wintypes.DWORD)]
        get_security.restype = wintypes.BOOL
        to_sddl = advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW
        to_sddl.argtypes = [wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                            ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.ULONG)]
        to_sddl.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel.LocalFree.restype = wintypes.HLOCAL

        def dacl(path: Path) -> str:
            required = wintypes.DWORD()
            get_security(str(path), 4, None, 0, ctypes.byref(required))
            descriptor = ctypes.create_string_buffer(required.value)
            if not get_security(str(path), 4, descriptor, required, ctypes.byref(required)):
                raise ctypes.WinError(ctypes.get_last_error())
            out = wintypes.LPWSTR()
            if not to_sddl(descriptor, 1, 4, ctypes.byref(out), None):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                return out.value
            finally:
                kernel.LocalFree(ctypes.cast(out, wintypes.HLOCAL))

        for operation in ("whole", "chunk", "edit"):
            with self.subTest(operation=operation):
                target = self.root / "src" / f"acl-{operation}.py"
                target.write_bytes(b"VALUE = 1\n")
                inherited = dacl(target)
                command = subprocess.run(["icacls", str(target), "/grant", "*S-1-1-0:(R)"],
                                         capture_output=True, check=False)
                self.assertEqual(command.returncode, 0, command.stderr)
                original_dacl = dacl(target)
                self.assertNotEqual(original_dacl, inherited, "test needs a distinct explicit DACL")
                if operation == "whole":
                    content = "VALUE = 2\n"
                elif operation == "edit":
                    content = json.dumps({"$candidate_edit": 1,
                        "base_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                        "old": "VALUE = 1", "new": "VALUE = 2"})
                else:
                    updated = b"VALUE = 2\n"
                    content = json.dumps({"$candidate_write_chunk": 1, "write_id": "acl-test",
                        "index": 0, "total_bytes": len(updated),
                        "sha256": hashlib.sha256(updated).hexdigest(),
                        "data": base64.b64encode(updated).decode("ascii")})
                result = self.invoke("write_candidate_file", {"path": f"src/acl-{operation}.py", "content": content})
                self.assertEqual(result["status"], "written")
                self.assertEqual(target.read_bytes(), b"VALUE = 2\n")
                self.assertEqual(dacl(target), original_dacl)
                self.assertEqual(list(target.parent.glob(".worker-write-*")), [])

    @unittest.skipUnless(os.name == "nt", "ReplaceFileW requires Windows")
    def test_windows_replace_failure_never_falls_back_to_os_replace(self) -> None:
        original = self.existing.read_bytes()
        with mock.patch("self_evolution_candidate_tools._replace_existing_windows", side_effect=OSError("failure"), create=True), \
             mock.patch("self_evolution_candidate_tools.os.replace") as fallback:
            with self.assertRaises(PermissionError):
                self.invoke("write_candidate_file", {"path": "src/module.py", "content": "changed"})
        fallback.assert_not_called()
        self.assertEqual(self.existing.read_bytes(), original)

    @unittest.skipUnless(os.name == "nt", "ReplaceFileW requires Windows")
    def test_windows_replace_failure_retains_moved_original_and_replacement(self) -> None:
        original = self.existing.read_bytes()
        def partial_failure(target, replacement, backup):
            target.rename(backup)
            raise OSError("simulated partial ReplaceFileW failure")
        with mock.patch("self_evolution_candidate_tools._replace_existing_windows", side_effect=partial_failure):
            with self.assertRaisesRegex(PermissionError, "write_failed_recovery_required"):
                self.invoke("write_candidate_file", {"path": "src/module.py", "content": "changed"})
        self.assertFalse(self.existing.exists())
        recoveries = list(self.existing.parent.glob(".worker-write-*"))
        self.assertEqual(len(recoveries), 2)
        self.assertEqual(sorted(path.read_bytes() for path in recoveries), sorted([original, b"changed"]))

    @unittest.skipUnless(os.name == "nt", "ReplaceFileW requires Windows")
    def test_windows_replace_uses_no_ignore_acl_flags(self) -> None:
        import ctypes
        from self_evolution_candidate_tools import _replace_existing_windows
        replacement = mock.Mock(return_value=1)
        library = mock.Mock()
        library.ReplaceFileW = replacement
        with mock.patch.object(ctypes, "WinDLL", return_value=library):
            _replace_existing_windows(Path("C:/target"), "C:/replacement", "C:/backup")
        self.assertEqual(replacement.call_args.args[3:], (0, None, None))

    def test_read_redacts_common_secret_formats(self) -> None:
        secret_source = (
            'OPENAI_API_KEY=sk-test-secret-value\n'
            '{"api_key": "json-secret-value"}\n'
            '-----BEGIN PRIVATE KEY-----\nprivate-key-material\n-----END PRIVATE KEY-----\n'
        )
        # Sensitive path names cannot be requested; use a normal source file instead.
        target = self.root / "src" / "module.py"
        target.write_text(secret_source, encoding="utf-8")
        read = self.invoke("read_candidate_file", {"path": "src/module.py"})
        for secret in ("sk-test-secret-value", "json-secret-value", "private-key-material"):
            self.assertNotIn(secret, read["content"])
        self.assertIn("[REDACTED]", read["content"])

    def test_read_candidate_file_returns_bounded_digest_bound_segments(self) -> None:
        source = ("line-0123456789\\n" * 3000).encode("utf-8")
        target = self.root / "src" / "large.py"
        target.write_bytes(source)
        digest = hashlib.sha256(source).hexdigest()
        collected = bytearray()
        offset = 0
        while True:
            encoded_path = f"@read:{offset}:{digest if offset else '-'}:src/large.py"
            result = self.invoke("read_candidate_file", {"path": encoded_path})
            chunk = result["content"].encode("utf-8")
            self.assertEqual(result["offset"], offset)
            self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 14 * 1024)
            self.assertEqual(result["sha256"], digest)
            collected.extend(chunk)
            if result["complete"]:
                break
            offset = result["next_offset"]
        self.assertEqual(bytes(collected), source)

    def test_reads_and_redacts_real_file_larger_than_680_kib_in_bounded_segments(self) -> None:
        secret = b"OPENAI_API_KEY=sk-large-file-secret-value"
        source = b"A" * 8188 + b"\n" + secret + b"\n" + ((b"B" * 4095 + b"\n") * 175)
        target = self.root / "src" / "server.py"
        target.write_bytes(source)
        expected_digest = hashlib.sha256(source).hexdigest()
        pieces = []
        offset = 0
        while True:
            path = "src/server.py" if offset == 0 else f"@read:{offset}:{expected_digest}:src/server.py"
            result = self.invoke("read_candidate_file", {"path": path})
            self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 14 * 1024)
            self.assertLessEqual(len(result["content"].encode("utf-8")), 8 * 1024)
            self.assertEqual(result["sha256"], expected_digest)
            pieces.append(result["content"])
            if result["complete"]:
                break
            offset = result["next_offset"]
        joined = "".join(pieces)
        self.assertEqual(len(joined.encode("utf-8")), len(source) - len(secret.split(b"=", 1)[1]) + len("[REDACTED]"))
        self.assertNotIn(secret.decode("ascii"), joined)
        self.assertIn("OPENAI_API_KEY=[REDACTED]", joined)

    def test_writes_real_file_larger_than_680_kib_without_early_or_external_mutation(self) -> None:
        source = (b"# candidate large source line\n" * ((700 * 1024) // 30 + 1))[:700 * 1024]
        target = self.root / "src" / "agent.py"
        target.write_bytes(b"ORIGINAL CANDIDATE CONTENT")
        prior = target.read_bytes()
        stable_prior = self.stable_file.read_bytes()
        digest = hashlib.sha256(source).hexdigest()
        chunks = [source[index:index + 9000] for index in range(0, len(source), 9000)]
        for index, chunk in enumerate(chunks):
            envelope = {
                "$candidate_write_chunk": 1, "write_id": "write-700k", "index": index,
                "total_bytes": len(source), "sha256": digest,
                "data": base64.b64encode(chunk).decode("ascii"),
            }
            result = self.invoke("write_candidate_file", {
                "path": "src/agent.py", "content": json.dumps(envelope, separators=(",", ":")),
            })
            if index < len(chunks) - 1:
                self.assertEqual(target.read_bytes(), prior)
        self.assertEqual(result["status"], "written")
        self.assertEqual(result["bytes_written"], len(source))
        self.assertEqual(result["sha256"], digest)
        self.assertEqual(target.read_bytes(), source)
        self.assertEqual(self.stable_file.read_bytes(), stable_prior)

    def test_read_shrinks_segments_when_json_escaping_would_exceed_response_limit(self) -> None:
        source = b'"' * 20_000
        target = self.root / "src" / "escaped.py"
        target.write_bytes(source)
        digest = hashlib.sha256(source).hexdigest()
        offset = 0
        received = bytearray()
        while True:
            path = "src/escaped.py" if offset == 0 else f"@read:{offset}:{digest}:src/escaped.py"
            result = self.invoke("read_candidate_file", {"path": path})
            self.assertLessEqual(len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))), 14 * 1024)
            received.extend(result["content"].encode("utf-8"))
            if result["complete"]:
                break
            offset = result["next_offset"]
        self.assertEqual(bytes(received), source)

    def test_read_continuation_rejects_changed_file_snapshot(self) -> None:
        target = self.root / "src" / "large-read.py"
        target.write_bytes(b"a" * 9000)
        first = self.invoke("read_candidate_file", {"path": "src/large-read.py"})
        target.write_bytes(b"b" * 9000)
        with self.assertRaises(PermissionError):
            self.invoke("read_candidate_file", {
                "path": f"@read:{first['next_offset']}:{first['sha256']}:src/large-read.py",
            })

    def test_find_locates_literal_near_end_of_large_file_in_one_call(self) -> None:
        marker = "UNIQUE_END_MARKER_42"
        source = b"preamble\n" + (b"x" * 4095 + b"\n") * 175 + f"\n{marker}\npostamble\n".encode()
        target = self.root / "src" / "index.ts"
        target.write_bytes(source)
        encoded_query = base64.urlsafe_b64encode(marker.encode("utf-8")).decode("ascii").rstrip("=")
        result = self.invoke("read_candidate_file", {"path": f"@find:{encoded_query}:src/index.ts"})
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["match_count"], 1)
        self.assertFalse(result["multiple_matches"])
        self.assertEqual(result["sha256"], hashlib.sha256(source).hexdigest())
        self.assertIn(marker, result["matches"][0]["context"])
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 14 * 1024)

    def test_find_reports_multiple_matches_but_never_returns_unredacted_secret(self) -> None:
        source = b"OPENAI_API_KEY=sk-never-return-this-value\nmark mark\n"
        target = self.root / "src" / "safe-find.py"
        target.write_bytes(source)
        encoded_query = base64.urlsafe_b64encode(b"mark").decode("ascii").rstrip("=")
        result = self.invoke("read_candidate_file", {"path": f"@find:{encoded_query}:src/safe-find.py"})
        self.assertEqual(result["match_count"], 2)
        self.assertTrue(result["multiple_matches"])
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("sk-never-return-this-value", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_find_rejects_invalid_or_oversized_query(self) -> None:
        for path in ("@find:***:src/module.py", f"@find:{base64.urlsafe_b64encode(b'x' * 513).decode().rstrip('=')}:src/module.py"):
            with self.subTest(path=path), self.assertRaises(PermissionError):
                self.invoke("read_candidate_file", {"path": path})

    def test_read_and_write_enforce_four_mib_and_aggregate_buffer_limits(self) -> None:
        oversized = self.root / "src" / "oversized.py"
        oversized.write_bytes(b"z" * (4 * 1024 * 1024 + 1))
        with self.assertRaises(PermissionError):
            self.invoke("read_candidate_file", {"path": "src/oversized.py"})
        too_large = {
            "$candidate_write_chunk": 1, "write_id": "over-limit", "index": 0,
            "total_bytes": 4 * 1024 * 1024 + 1, "sha256": "0" * 64,
            "data": base64.b64encode(b"x").decode("ascii"),
        }
        with self.assertRaises(PermissionError):
            self.invoke("write_candidate_file", {
                "path": "src/too-large.py", "content": json.dumps(too_large, separators=(",", ":")),
            })
        chunk = b"x"
        first = {
            "$candidate_write_chunk": 1, "write_id": "reserve-max", "index": 0,
            "total_bytes": 4 * 1024 * 1024, "sha256": "0" * 64,
            "data": base64.b64encode(chunk).decode("ascii"),
        }
        accepted = self.invoke("write_candidate_file", {
            "path": "src/reserved.py", "content": json.dumps(first, separators=(",", ":")),
        })
        self.assertEqual(accepted["chunk_status"], "accepted")
        second = {
            "$candidate_write_chunk": 1, "write_id": "reserve-extra", "index": 0,
            "total_bytes": 1, "sha256": hashlib.sha256(chunk).hexdigest(),
            "data": base64.b64encode(chunk).decode("ascii"),
        }
        with self.assertRaises(PermissionError):
            self.invoke("write_candidate_file", {
                "path": "src/other.py", "content": json.dumps(second, separators=(",", ":")),
            })
        self.assertFalse((self.root / "src" / "reserved.py").exists())
        self.assertFalse((self.root / "src" / "other.py").exists())

    def test_write_candidate_file_commits_large_payload_only_after_digest_verified_chunks(self) -> None:
        source = ("large-source-0123456789\\n" * 900).encode("utf-8")
        target = self.root / "src" / "large.py"
        digest = hashlib.sha256(source).hexdigest()
        chunks = [source[index:index + 9000] for index in range(0, len(source), 9000)]
        for index, chunk in enumerate(chunks):
            envelope = {
                "$candidate_write_chunk": 1,
                "write_id": "write-large-1",
                "index": index,
                "total_bytes": len(source),
                "sha256": digest,
                "data": base64.b64encode(chunk).decode("ascii"),
            }
            result = self.invoke("write_candidate_file", {
                "path": "src/large.py", "content": json.dumps(envelope, separators=(",", ":")),
            })
        self.assertEqual(result["status"], "written")
        self.assertEqual(result["sha256"], digest)
        self.assertEqual(target.read_bytes(), source)

    def test_large_file_exact_edit_uses_base_digest_and_returns_new_digest(self) -> None:
        before = b"# head\n" + b"x" * (700 * 1024) + b"\nTARGET_LINE = 1\n" + b"y" * 100
        target = self.root / "src" / "large-edit.py"
        target.write_bytes(before)
        base_digest = hashlib.sha256(before).hexdigest()
        after = before.replace(b"TARGET_LINE = 1", b"TARGET_LINE = 2")
        envelope = {
            "$candidate_edit": 1,
            "base_sha256": base_digest,
            "old": "TARGET_LINE = 1",
            "new": "TARGET_LINE = 2",
        }
        stable_before = self.stable_file.read_bytes()
        result = self.invoke("write_candidate_file", {
            "path": "src/large-edit.py", "content": json.dumps(envelope, separators=(",", ":")),
        })
        self.assertEqual(target.read_bytes(), after)
        self.assertEqual(result["sha256"], hashlib.sha256(after).hexdigest())
        self.assertNotEqual(result["sha256"], base_digest)
        self.assertEqual(self.stable_file.read_bytes(), stable_before)

    def test_exact_edit_rejects_digest_mismatch_or_non_unique_old_text_without_mutation(self) -> None:
        target = self.root / "src" / "module.py"
        target.write_bytes(b"VALUE = 1\nVALUE = 1\n")
        original = target.read_bytes()
        cases = (
            {"$candidate_edit": 1, "base_sha256": "0" * 64, "old": "VALUE = 1", "new": "VALUE = 2"},
            {"$candidate_edit": 1, "base_sha256": hashlib.sha256(original).hexdigest(), "old": "VALUE", "new": "VALUE2"},
            {"$candidate_edit": 1, "base_sha256": hashlib.sha256(original).hexdigest(), "old": "", "new": "x"},
            {"$candidate_edit": 1, "base_sha256": hashlib.sha256(original).hexdigest(), "old": "VALUE", "new": ""},
        )
        for envelope in cases:
            with self.subTest(envelope=envelope), self.assertRaises(PermissionError):
                self.invoke("write_candidate_file", {
                    "path": "src/module.py", "content": json.dumps(envelope, separators=(",", ":")),
                })
            self.assertEqual(target.read_bytes(), original)

    def test_chunked_write_rejects_same_size_timestamp_restored_change(self) -> None:
        target = self.existing
        old = target.read_bytes()
        before = target.stat()
        content = b"replacement content"
        digest = hashlib.sha256(content).hexdigest()
        envelope = {
            "$candidate_write_chunk": 1, "write_id": "same-size-race", "index": 0,
            "total_bytes": len(content), "sha256": digest,
            "data": base64.b64encode(content[:5]).decode("ascii"),
        }
        self.invoke("write_candidate_file", {
            "path": "src/module.py", "content": json.dumps(envelope, separators=(",", ":")),
        })
        changed = b"Q" * len(old)
        target.write_bytes(changed)
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        envelope.update(index=1, data=base64.b64encode(content[5:]).decode("ascii"))
        with self.assertRaises(PermissionError):
            self.invoke("write_candidate_file", {
                "path": "src/module.py", "content": json.dumps(envelope, separators=(",", ":")),
            })
        self.assertEqual(target.read_bytes(), changed)

    def test_exact_edit_and_chunked_write_apply_existing_mode(self) -> None:
        target = self.existing
        original_mode = stat.S_IMODE(target.stat().st_mode)
        original = target.read_bytes()
        edit = {"$candidate_edit": 1, "base_sha256": hashlib.sha256(original).hexdigest(),
                "old": "VALUE = 1", "new": "VALUE = 2"}
        with mock.patch("self_evolution_candidate_tools.os.chmod", wraps=os.chmod) as chmod:
            self.invoke("write_candidate_file", {
                "path": "src/module.py", "content": json.dumps(edit, separators=(",", ":")),
            })
        self.assertTrue(any(call.args[1] == original_mode for call in chmod.call_args_list))
        content = b"chunked content"
        envelope = {"$candidate_write_chunk": 1, "write_id": "mode-test", "index": 0,
                    "total_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                    "data": base64.b64encode(content).decode("ascii")}
        with mock.patch("self_evolution_candidate_tools.os.chmod", wraps=os.chmod) as chmod:
            self.invoke("write_candidate_file", {
                "path": "src/module.py", "content": json.dumps(envelope, separators=(",", ":")),
            })
        self.assertTrue(any(call.args[1] == original_mode for call in chmod.call_args_list))
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), original_mode)

    def test_chunked_write_rejects_target_changed_after_first_chunk(self) -> None:
        target = self.root / "src" / "module.py"
        original = b"REPLACED!!!"
        digest = hashlib.sha256(original).hexdigest()
        first = original[:10]
        envelope = {
            "$candidate_write_chunk": 1, "write_id": "write-race", "index": 0,
            "total_bytes": len(original), "sha256": digest,
            "data": base64.b64encode(first).decode("ascii"),
        }
        self.invoke("write_candidate_file", {
            "path": "src/module.py", "content": json.dumps(envelope, separators=(",", ":")),
        })
        target.write_text("MUTATED!!!!", encoding="utf-8")
        second = original[10:]
        envelope.update(index=1, data=base64.b64encode(second).decode("ascii"))
        with self.assertRaises(PermissionError):
            self.invoke("write_candidate_file", {
                "path": "src/module.py", "content": json.dumps(envelope, separators=(",", ":")),
            })
        self.assertEqual(target.read_text(encoding="utf-8"), "MUTATED!!!!")

    def test_write_candidate_file_rejects_out_of_order_chunk_without_touching_target(self) -> None:
        target = self.root / "src" / "module.py"
        original = target.read_bytes()
        chunk = b"new contents"
        envelope = {
            "$candidate_write_chunk": 1, "write_id": "write-bad-order", "index": 1,
            "total_bytes": 24, "sha256": hashlib.sha256(chunk).hexdigest(),
            "data": base64.b64encode(chunk).decode("ascii"),
        }
        with self.assertRaises(PermissionError):
            self.invoke("write_candidate_file", {
                "path": "src/module.py", "content": json.dumps(envelope, separators=(",", ":")),
            })
        self.assertEqual(target.read_bytes(), original)

    def test_resolves_candidate_root_for_each_task_without_caching(self) -> None:
        other_root = self.base / "candidate-two"
        other_root.mkdir()
        self.bindings["task-2"] = WorkspaceBinding(
            task_id="task-2", candidate_root=other_root, candidate_id="candidate-2",
            config_identity="config-2", source_paths=(),
        )
        context = SimpleNamespace(**vars(self.context))
        context.task_id = "task-2"
        result = self.by_name["write_candidate_file"].invoke(
            context, {"path": "separate.txt", "content": "task two"},
        )
        self.assertEqual(result["status"], "written")
        self.assertEqual((other_root / "separate.txt").read_text(encoding="utf-8"), "task two")
        self.assertFalse((self.root / "separate.txt").exists())
    def test_rejects_absolute_parent_and_windows_escape_paths(self) -> None:
        for path in ("../stable/keep.txt", str(self.stable_file), r"..\stable\keep.txt", r"C:\temp\x"):
            with self.subTest(path=path), self.assertRaises(PermissionError):
                self.invoke("read_candidate_file", {"path": path})
        with self.assertRaises(PermissionError):
            self.invoke("write_candidate_file", {"path": "../stable/new.txt", "content": "bad"})
        self.assertEqual(self.stable_file.read_text(encoding="utf-8"), "stable")

    def test_rejects_link_traversal_for_reads_and_writes(self) -> None:
        link = self.root / "linked"
        try:
            link.symlink_to(self.outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation unavailable: {type(exc).__name__}")
        with self.assertRaises(PermissionError):
            self.invoke("read_candidate_file", {"path": "linked/keep.txt"})
        with self.assertRaises(PermissionError):
            self.invoke("write_candidate_file", {"path": "linked/keep.txt", "content": "bad"})
        self.assertEqual(self.stable_file.read_text(encoding="utf-8"), "stable")

    def test_rejects_hard_link_reads_and_writes(self) -> None:
        linked_file = self.root / "src" / "linked.py"
        try:
            os.link(self.stable_file, linked_file)
        except OSError as exc:
            self.skipTest(f"hard-link creation unavailable: {type(exc).__name__}")
        with self.assertRaises(PermissionError):
            self.invoke("read_candidate_file", {"path": "src/linked.py"})
        with self.assertRaises(PermissionError):
            self.invoke("write_candidate_file", {"path": "src/linked.py", "content": "bad"})
        self.assertEqual(self.stable_file.read_text(encoding="utf-8"), "stable")

    def test_revalidates_identity_before_each_operation(self) -> None:
        self.live = False
        with self.assertRaises(PermissionError):
            self.invoke("observe_candidate")
        with self.assertRaises(PermissionError):
            self.invoke("write_candidate_file", {"path": "src/module.py", "content": "bad"})
        self.assertEqual(self.existing.read_text(encoding="utf-8"), "VALUE = 1\n")

    def test_only_registered_check_runs_and_real_failure_is_propagated(self) -> None:
        with self.assertRaises(PermissionError):
            self.invoke("run_registered_check", {"check_id": "arbitrary-command"})

        result = self.invoke("run_registered_check", {"check_id": "failing-check"})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["check_id"], "failing-check")
        self.assertEqual(result["candidate_digest"], "digest-current")
        self.assertTrue(result["evidence_ref"])
        self.assertIn(result["process_tree_state"], {"stopped", "unknown", "not_started"})
        self.assertIs(type(result["requires_reconciliation"]), bool)
        self.assertNotIn(str(self.root), repr(result))


if __name__ == "__main__":
    unittest.main()
