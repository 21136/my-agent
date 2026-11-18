from __future__ import annotations

import io
import json
import os
import tarfile
import subprocess
import unittest
from unittest import mock

from self_evolution_auto_runner import DockerVerifier, Verification, _BOOTSTRAP, _format_result_sentinel


IMAGE_ID = "sha256:" + "a" * 64


def _inspect(image_id: str = IMAGE_ID, volumes=None) -> list[dict]:
    return [{
        "Id": image_id,
        "Os": "linux",
        "Config": {
            "Volumes": volumes,
            "Entrypoint": ["/bin/sh", "-c", "echo unsafe"],
            "Cmd": ["echo", "unsafe"],
        },
    }]


def _archive(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class DockerVerifierTests(unittest.TestCase):
    def verifier(self, **kwargs) -> DockerVerifier:
        allowed = kwargs.pop("allowed_paths", ("agent-core/tests/test_candidate.py",))
        return DockerVerifier(IMAGE_ID, ("agent-core/tests/test_candidate.py",), executable=r"C:\Docker\docker.exe", allowed_paths=allowed, **kwargs)

    @mock.patch("self_evolution_auto_runner.subprocess.run")
    def test_preflight_uses_absolute_cli_local_endpoint_and_never_pulls(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "linux\n", ""),
            subprocess.CompletedProcess([], 0, json.dumps(_inspect()), ""),
        ]
        verifier = self.verifier()
        self.assertEqual(verifier.tests, ("agent-core/tests/test_candidate.py",))
        with mock.patch("self_evolution_auto_runner.os.name", "nt"):
            verifier.preflight()
        calls = [call.args[0] for call in run.call_args_list]
        self.assertTrue(all(args[0] == r"C:\Docker\docker.exe" for args in calls))
        self.assertTrue(all("--host" in args for args in calls))
        self.assertTrue(all("pull" not in args for args in calls))
        self.assertTrue(all("npipe:////./pipe/dockerDesktopLinuxEngine" in args for args in calls))

    def test_rejects_non_immutable_image_reference_before_docker(self):
        with self.assertRaises(ValueError):
            DockerVerifier("ubuntu:latest", ("tests/test_candidate.py",), executable=r"C:\Docker\docker.exe")

    def test_legacy_relative_pytest_path_normalizes_to_canonical_agent_core_path(self):
        verifier = DockerVerifier(IMAGE_ID, ("tests/test_candidate.py",), executable=r"C:\Docker\docker.exe", allowed_paths=("tests/test_candidate.py",))
        self.assertEqual(verifier.tests, ("agent-core/tests/test_candidate.py",))
        self.assertEqual(verifier.allowed_paths, ("agent-core/tests/test_candidate.py",))

    def test_rejects_shell_or_option_injection_in_test_paths(self):
        for path in ("; echo pwned", "../outside.py", "--help", "tests/a;id.py"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                DockerVerifier(IMAGE_ID, (path,), executable=r"C:\Docker\docker.exe")

    @mock.patch("self_evolution_auto_runner.subprocess.run")
    def test_preflight_rejects_non_linux_image_metadata(self, run):
        image = _inspect()
        image[0]["Os"] = "windows"
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "linux\n", ""),
            subprocess.CompletedProcess([], 0, json.dumps(image), ""),
        ]
        with mock.patch("self_evolution_auto_runner.os.name", "nt"):
            with self.assertRaises(RuntimeError):
                self.verifier().preflight()

    @mock.patch("self_evolution_auto_runner.subprocess.run")
    def test_preflight_rejects_image_declared_volumes(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "linux\n", ""),
            subprocess.CompletedProcess([], 0, json.dumps(_inspect(volumes={"/var/run/docker.sock": {}})), ""),
        ]
        with mock.patch("self_evolution_auto_runner.os.name", "nt"):
            with self.assertRaises(RuntimeError):
                self.verifier().preflight()

    @mock.patch("self_evolution_auto_runner.subprocess.run")
    def test_preflight_rejects_remote_or_non_linux_daemon(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "windows\n", "")
        with mock.patch("self_evolution_auto_runner.os.name", "nt"):
            with self.assertRaises(RuntimeError):
                self.verifier().preflight()

    def test_archive_accepts_exact_configured_allowlist(self):
        verifier = self.verifier(allowed_paths=("agent-core/self_evolution.py", "agent-core/tests/test_candidate.py"))
        names = verifier._validate_archive(_archive({
            "agent-core/self_evolution.py": b"value = 2\n",
            "agent-core/tests/test_candidate.py": b"def test_candidate(): assert True\n",
        }))
        self.assertEqual(names, ("agent-core/self_evolution.py", "agent-core/tests/test_candidate.py"))

    def test_archive_allowlist_is_required_and_must_match_exactly(self):
        archive = _archive({"agent-core/tests/test_candidate.py": b"pass\n"})
        without_allowlist = DockerVerifier(IMAGE_ID, ("agent-core/tests/test_candidate.py",), executable=r"C:\Docker\docker.exe")
        with self.assertRaises(ValueError):
            without_allowlist._validate_archive(archive)
        verifier = self.verifier(allowed_paths=("agent-core/tests/test_candidate.py", "agent-core/extra.py"))
        with self.assertRaises(ValueError):
            verifier._validate_archive(archive)

    def test_archive_rejects_untracked_non_python_path_and_non_regular_entries(self):
        verifier = self.verifier()
        for payload in (
            _archive({"agent-core/new_module.py": b"x=1\n"}),
            _archive({"agent-core/tests/test_candidate.txt": b"x"}),
        ):
            with self.subTest(payload=len(payload)), self.assertRaises(ValueError):
                verifier._validate_archive(payload)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo("agent-core/tests/test_candidate.py")
            info.type = tarfile.SYMTYPE
            info.linkname = "../../outside"
            tf.addfile(info)
        with self.assertRaises(ValueError):
            verifier._validate_archive(buf.getvalue())

    @mock.patch("self_evolution_auto_runner.subprocess.Popen")
    @mock.patch.object(DockerVerifier, "preflight")
    def test_run_passes_archive_to_isolated_fixed_pytest_container(self, preflight, popen):
        archive = _archive({"agent-core/tests/test_candidate.py": b"def test_candidate(): assert True\n"})
        class FakeProcess:
            def __init__(self, *args, **kwargs):
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO(b"2 passed\n")
                self.stderr = io.BytesIO()
                self.returncode = 0
            def poll(self): return self.returncode
            def wait(self, timeout=None): return self.returncode
            def terminate(self): self.returncode = -15
            def kill(self): self.returncode = -9
        popen.side_effect = FakeProcess
        verifier = self.verifier()
        verifier._create_container = mock.Mock(return_value="container-id-123")
        verifier._remove_container = mock.Mock()
        verifier._docker_endpoint = mock.Mock(return_value="npipe:////./pipe/dockerDesktopLinuxEngine")
        verifier._start_container = mock.Mock(return_value=Verification(True, "2 passed"))
        verifier.run(archive)
        argv = verifier._create_container.call_args.args[0]
        self.assertEqual(verifier._start_container.call_args.args[1], archive)

    def test_pytest_success_requires_trusted_sentinel_zero_exit_and_at_least_one_pass(self):
        marker = _format_result_sentinel("a" * 32, 0, 1)
        self.assertTrue(DockerVerifier._has_success_sentinel(marker, "a" * 32, 0))
        self.assertFalse(DockerVerifier._has_success_sentinel("", "a" * 32, 0))
        self.assertFalse(DockerVerifier._has_success_sentinel(_format_result_sentinel("a" * 32, 0, 0), "a" * 32, 0))
        self.assertFalse(DockerVerifier._has_success_sentinel(_format_result_sentinel("a" * 32, 1, 1), "a" * 32, 0))

    def test_trusted_bootstrap_clears_image_environment_and_uses_fixed_pytest_settings(self):
        self.assertIn("os.environ.clear()", _BOOTSTRAP)
        self.assertIn("PYTEST_DISABLE_PLUGIN_AUTOLOAD", _BOOTSTRAP)
        self.assertIn("-c", _BOOTSTRAP)
        self.assertIn("/dev/null", _BOOTSTRAP)
        self.assertIn("no:cacheprovider", _BOOTSTRAP)
        self.assertIn("--import-mode=importlib", _BOOTSTRAP)
        self.assertIn('archive.extract(member, root, filter="data")', _BOOTSTRAP)
        self.assertNotIn("set_attrs=False", _BOOTSTRAP)
        self.assertIn("pytest_runtest_logreport", _BOOTSTRAP)
        self.assertIn("CODEX_VERIFY_RESULT:", _BOOTSTRAP)

    @mock.patch("self_evolution_auto_runner.subprocess.run")
    def test_create_uses_hardened_mount_free_container_options(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, b"a" * 64, b"")
        verifier = self.verifier()
        token = "a" * 32
        self.assertEqual(verifier._create_container("codex-evo-" + token, token), "a" * 64)
        argv = run.call_args.args[0]
        for option in ("--interactive", "--log-driver=none", "--network=none", "--read-only", "--cap-drop=ALL", "--pull=never", "--security-opt=no-new-privileges:true"):
            self.assertIn(option, argv)
        self.assertFalse(any(arg in ("-v", "--mount") for arg in argv))
        self.assertEqual(argv[-2:], [token, "tests/test_candidate.py"])
        self.assertFalse(run.call_args.kwargs["shell"])


    @mock.patch.object(DockerVerifier, "preflight")
    def test_run_reports_create_failure_and_does_not_clean_unknown_container(self, preflight):
        verifier = self.verifier()
        verifier._create_container = mock.Mock(side_effect=RuntimeError("daemon unavailable"))
        verifier._remove_container = mock.Mock()
        result = verifier.run(_archive({"agent-core/tests/test_candidate.py": b""}))
        self.assertFalse(result.passed)
        self.assertIn("daemon unavailable", result.summary)
        verifier._remove_container.assert_not_called()
    @mock.patch.object(DockerVerifier, "preflight")
    def test_run_preserves_pytest_failure_as_failed_verification(self, preflight):
        verifier = self.verifier()
        verifier._create_container = mock.Mock(return_value="created-by-this-run")
        verifier._start_container = mock.Mock(return_value=Verification(False, "1 failed", 1))
        verifier._remove_container = mock.Mock()
        result = verifier.run(_archive({"agent-core/tests/test_candidate.py": b"def test_x(): assert False\n"}))
        self.assertFalse(result.passed)
        self.assertEqual(result.return_code, 1)
        self.assertEqual(result.summary, "1 failed")
        verifier._remove_container.assert_called_once_with("created-by-this-run")

    @mock.patch.object(DockerVerifier, "preflight")
    def test_run_cancellation_before_creation_does_not_touch_docker(self, preflight):
        verifier = self.verifier()
        verifier._create_container = mock.Mock()
        result = verifier.run(_archive({"agent-core/tests/test_candidate.py": b""}), cancelled=lambda: True)
        self.assertFalse(result.passed)
        self.assertIn("cancelled", result.summary)
        verifier._create_container.assert_not_called()
        verifier._remove_container = mock.Mock()
        verifier._remove_container.assert_not_called()

    @mock.patch("self_evolution_auto_runner.subprocess.run")
    def test_cleanup_by_name_requires_exact_owner_label_and_valid_docker_id(self, run):
        token = "b" * 32
        verifier = self.verifier()
        verifier._remove_container = mock.Mock()
        run.return_value = subprocess.CompletedProcess([], 0, ("c" * 64 + " other-owner").encode(), b"")
        verifier._remove_owned_name("codex-evo-" + token, token)
        verifier._remove_container.assert_not_called()
        run.return_value = subprocess.CompletedProcess([], 0, ("c" * 64 + " " + token).encode(), b"")
        verifier._remove_owned_name("codex-evo-" + token, token)
        verifier._remove_container.assert_called_once_with("c" * 64)

    @mock.patch.object(DockerVerifier, "preflight")
    def test_run_cleans_ambiguous_create_only_after_matching_owned_label(self, preflight):
        verifier = self.verifier()
        verifier._create_container = mock.Mock(side_effect=RuntimeError("create response timed out"))
        verifier._remove_owned_name = mock.Mock()
        result = verifier.run(_archive({"agent-core/tests/test_candidate.py": b""}))
        self.assertFalse(result.passed)
        verifier._remove_owned_name.assert_called_once()
        name, token = verifier._remove_owned_name.call_args.args
        self.assertEqual(name, "codex-evo-" + token)

    @mock.patch.object(DockerVerifier, "preflight")
    def test_run_cleans_only_container_id_it_created_after_timeout(self, preflight):
        verifier = self.verifier(timeout=1)
        verifier._create_container = mock.Mock(return_value="created-by-this-run")
        verifier._remove_container = mock.Mock()
        verifier._start_container = mock.Mock(return_value=Verification(False, "timed out"))
        verifier.run(_archive({"agent-core/tests/test_candidate.py": b""}))
        verifier._remove_container.assert_called_once_with("created-by-this-run")


if __name__ == "__main__":
    unittest.main()
