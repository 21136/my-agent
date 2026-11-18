from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from self_evolution_selected_sync_verify import SelectedSyncVerifier

IMAGE = "sha256:" + "a" * 64
PY_TEST = "agent-core/tests/test_self_evolution_selected_sync_git.py"
JS_TEST = "desktop/tests/managed-review-worker.test.ts"


def snapshot(extra=None):
    files = {
        PY_TEST: b"def test_trusted_python(): assert True\n",
        JS_TEST: b"import { test } from 'node:test'; test('trusted desktop', () => {});\n",
        "agent-core/self_evolution_selected_sync_git.py": b"value = 1\n",
        "desktop/electron/managed-review-worker.mjs": b"export {};\n",
        "desktop/theme.css": b"body { color: red }\n",
        "desktop/config.json": b'{"ok":true}\n',
        "docs/VERIFY.md": b"verify\n",
        "tools/incident-repair-verifier/Dockerfile": b"FROM pinned-image\n",
        "evolve/_index.core.toml": b"# pinned core marker\n",
        "evolve/_index.toml": b"# pinned marker\n",
        "agent-core/tests/selected_case.source": b"trusted pinned test source\n",
    }
    if extra:
        files.update(extra)
    return files


class SelectedSyncVerifierTests(unittest.TestCase):
    def verifier(self, **kwargs):
        return SelectedSyncVerifier(IMAGE, (PY_TEST, JS_TEST), (PY_TEST, JS_TEST), executable=r"C:\\Docker\\docker.exe", **kwargs)

    def test_uses_trusted_check_path_tuples_and_result_has_passed_reason(self):
        verifier = self.verifier()
        self.assertEqual(verifier.baseline_checks, (PY_TEST, JS_TEST))
        self.assertEqual(verifier.candidate_checks, (PY_TEST, JS_TEST))
        from self_evolution_selected_sync_verify import VerificationReceipt
        self.assertIn("passed", VerificationReceipt.__dataclass_fields__)
        self.assertIn("reason", VerificationReceipt.__dataclass_fields__)

    def test_rejects_empty_or_arbitrary_check_paths(self):
        for baseline, candidate in (((), (JS_TEST,)), (("../x.py",), (JS_TEST,)), ((PY_TEST,), ("--help",))):
            with self.subTest(baseline=baseline, candidate=candidate), self.assertRaises(ValueError):
                SelectedSyncVerifier(IMAGE, baseline, candidate, executable=r"C:\\Docker\\docker.exe")

    def test_accepts_mixed_source_snapshot_bytes_and_changed_feature(self):
        verifier = self.verifier()
        verifier._validate_snapshot(snapshot({"desktop/new-feature.css": b"h1 { color: blue }"}), verifier.baseline_checks)

    def test_rejects_missing_empty_or_modified_trusted_tests(self):
        verifier = self.verifier()
        baseline = snapshot()
        missing = snapshot(); del missing[PY_TEST]
        empty = snapshot({PY_TEST: b"  \n"})
        for files in (missing, empty):
            with self.subTest(files=files), self.assertRaises(ValueError):
                verifier._validate_snapshot(files, verifier.baseline_checks)

    def test_rejects_unsafe_paths_wrong_extensions_and_oversized_files(self):
        verifier = self.verifier()
        for path, data in (("../escape.py", b"x"), ("agent-core/bad.bin", b"x"), ("desktop/CON.ts", b"x")):
            files = snapshot({path: data})
            with self.subTest(path=path), self.assertRaises(ValueError):
                verifier._validate_snapshot(files, verifier.baseline_checks)
        files = snapshot({"agent-core/large.py": b"x" * (1024 * 1024 + 1)})
        with self.assertRaises(ValueError):
            verifier._validate_snapshot(files, verifier.baseline_checks)

    def test_each_phase_accepts_only_its_relevant_fixed_suite(self):
        python_only = SelectedSyncVerifier(IMAGE, (PY_TEST,), (PY_TEST,), executable=r"C:\\Docker\\docker.exe")
        desktop_only = SelectedSyncVerifier(IMAGE, (JS_TEST,), (JS_TEST,), executable=r"C:\\Docker\\docker.exe")
        self.assertEqual(python_only.baseline_checks, (PY_TEST,))
        self.assertEqual(desktop_only.candidate_checks, (JS_TEST,))
        with self.assertRaises(ValueError):
            SelectedSyncVerifier(IMAGE, ("docs/VERIFY.md",), (JS_TEST,), executable=r"C:\\Docker\\docker.exe")

    @mock.patch.object(SelectedSyncVerifier, "preflight")
    def test_cancelled_verify_does_not_create_a_container(self, _preflight):
        verifier = self.verifier()
        verifier._create_container = mock.Mock()
        result = verifier.verify(snapshot(), snapshot(), cancelled=lambda: True)
        self.assertFalse(result.passed)
        self.assertIn("cancel", result.reason.lower())
        verifier._create_container.assert_not_called()

    def test_baseline_and_candidate_run_their_separate_fixed_check_tuples(self):
        verifier = self.verifier()
        calls = []
        def run(checks, files, cancelled):
            calls.append((checks, files))
            from self_evolution_auto_runner import Verification
            return Verification(True, "ok", 0)
        with mock.patch.object(verifier, "preflight"), mock.patch.object(verifier, "_run_snapshot", side_effect=run):
            result = verifier.verify(snapshot(), snapshot({"desktop/theme.css": b"changed"}))
        self.assertTrue(result.passed)
        self.assertEqual(result.stage, "passed")
        self.assertEqual([call[1] for call in calls], [verifier.baseline_checks, verifier.candidate_checks])

    @mock.patch("self_evolution_selected_sync_verify.subprocess.run")
    @mock.patch.object(SelectedSyncVerifier.__bases__[0], "preflight")
    def test_preflight_rejects_credential_like_environment_baked_into_the_image(self, _base_preflight, run):
        import json, subprocess
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps([{"Config": {"Env": ["PATH=/usr/bin", "OPENAI_API_KEY=not-a-real-key"]}}]), "")
        with self.assertRaisesRegex(RuntimeError, "credential-like"):
            self.verifier().preflight()

    def test_scope_only_files_and_pinned_source_blobs_are_allowed_but_not_executable_checks(self):
        verifier = self.verifier()
        files = snapshot()
        verifier._validate_snapshot(files, verifier.baseline_checks)
        with self.assertRaises(ValueError):
            SelectedSyncVerifier(IMAGE, ("tools/incident-repair-verifier/Dockerfile", JS_TEST),
                (PY_TEST, JS_TEST), executable=r"C:\\Docker\\docker.exe")
        changed_markers = snapshot({"evolve/_index.toml": b"model-edited marker\n"})
        marker_receipt = verifier.verify(files, changed_markers)
        self.assertFalse(marker_receipt.passed)
        self.assertEqual(marker_receipt.stage, "preflight")
        self.assertEqual(marker_receipt.reason, "Snapshot or trusted-test validation failed.")
        changed_source = snapshot({"agent-core/tests/selected_case.source": b"candidate changed trusted fixture\n"})
        self.assertEqual(verifier.verify(files, changed_source).stage, "preflight")

    def test_candidate_cannot_replace_any_trusted_baseline_or_candidate_test(self):
        verifier = self.verifier()
        candidate = snapshot({JS_TEST: b"import { test } from 'node:test';"})
        receipt = verifier.verify(snapshot(), candidate)
        self.assertFalse(receipt.passed)
        self.assertEqual(receipt.stage, "preflight")
        self.assertEqual(receipt.reason, "Snapshot or trusted-test validation failed.")

    def test_archive_contains_mixed_snapshot_files_as_regular_files(self):
        import io, tarfile
        verifier = self.verifier()
        archive_bytes = verifier._archive(snapshot())
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
            self.assertEqual(set(archive.getnames()), set(snapshot()))
            self.assertTrue(all(member.isfile() for member in archive.getmembers()))

    @mock.patch("self_evolution_selected_sync_verify.subprocess.run")
    def test_container_is_networkless_mountless_readonly_and_runs_only_fixed_check_paths(self, run):
        run.return_value = __import__("subprocess").CompletedProcess([], 0, b"a" * 64, b"")
        verifier = self.verifier()
        token = "a" * 32
        verifier._create_snapshot_container("codex-evo-" + token, token, verifier.candidate_checks)
        argv = run.call_args.args[0]
        for option in ("--network=none", "--read-only", "--cap-drop=ALL", "--pull=never", "--security-opt=no-new-privileges:true"):
            self.assertIn(option, argv)
        self.assertFalse(any(arg in ("-v", "--mount") for arg in argv))
        self.assertEqual(argv[-len(verifier.candidate_checks):], list(verifier.candidate_checks))
        self.assertFalse(run.call_args.kwargs["shell"])

    def test_baseline_failure_stops_before_candidate_and_reports_baseline_stage(self):
        from self_evolution_auto_runner import Verification
        verifier = self.verifier()
        run = mock.Mock(return_value=Verification(False, "secret-bearing raw runner output", 1))
        with mock.patch.object(verifier, "preflight"), mock.patch.object(verifier, "_run_snapshot", run):
            result = verifier.verify(snapshot(), snapshot())
        self.assertFalse(result.passed)
        self.assertEqual(result.stage, "baseline")
        self.assertEqual(result.reason, "Baseline checks failed; candidate checks were not run.")
        self.assertNotIn("secret-bearing", result.reason)
        run.assert_called_once()

    def test_candidate_failure_is_reported(self):
        verifier = self.verifier()
        from self_evolution_auto_runner import Verification
        outcomes = iter((Verification(True, "baseline ok", 0), Verification(False, "desktop tests failed", 1)))
        with mock.patch.object(verifier, "preflight"), mock.patch.object(verifier, "_run_snapshot", side_effect=lambda *args: next(outcomes)):
            result = verifier.verify(snapshot(), snapshot())
        self.assertFalse(result.passed)
        self.assertEqual(result.stage, "candidate")
        self.assertEqual(result.reason, "Candidate checks failed.")
        self.assertNotIn("desktop tests failed", result.reason)

    def test_bootstrap_fixes_python_and_node_test_runners_and_checks_nonzero_tests(self):
        from self_evolution_selected_sync_verify import _MIXED_BOOTSTRAP
        self.assertIn("pytest.main", _MIXED_BOOTSTRAP)
        self.assertIn("node", _MIXED_BOOTSTRAP)
        self.assertIn("--test", _MIXED_BOOTSTRAP)
        self.assertIn("PYTEST_DISABLE_PLUGIN_AUTOLOAD", _MIXED_BOOTSTRAP)
        self.assertIn("--noconftest", _MIXED_BOOTSTRAP)
        self.assertIn("sys.path.append(root)", _MIXED_BOOTSTRAP)
        self.assertIn("createRequire(\'/opt/selected-sync-runtime/verify.cjs\')", _MIXED_BOOTSTRAP)
        self.assertIn("typescript", _MIXED_BOOTSTRAP)
        self.assertNotIn("sys.path.insert(0, root)", _MIXED_BOOTSTRAP)
        self.assertIn("CODEX_VERIFY_RESULT:", _MIXED_BOOTSTRAP)


if __name__ == "__main__":
    unittest.main()
