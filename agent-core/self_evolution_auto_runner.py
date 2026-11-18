from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Callable

_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_TEST_PATH_RE = re.compile(r"^(?:agent-core/)?tests/[A-Za-z0-9_][A-Za-z0-9_./-]*\.py$")
_ALLOWED_PATH_RE = re.compile(r"^agent-core/[A-Za-z0-9_][A-Za-z0-9_./-]*\.py$")
_CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{12,64}$")
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_EXTRACTED_BYTES = 96 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 4096
_MAX_LOG_BYTES = 64 * 1024

# Trusted launcher; archive members are independently constrained on the host.
_BOOTSTRAP = r'''import os, sys, tarfile
root = "/tmp/candidate"
os.makedirs(root, mode=0o700)
total = 0
with tarfile.open(fileobj=sys.stdin.buffer, mode="r|*") as archive:
    for index, member in enumerate(archive):
        if index >= 4096:
            raise SystemExit("candidate archive has too many entries")
        name = member.name.replace("\\", "/")
        parts = name.split("/")
        if not name.startswith("agent-core/") or not name.endswith(".py") or name.startswith("/") or any(part in ("", ".", "..") for part in parts):
            raise SystemExit("candidate archive contains a disallowed path")
        if not member.isfile():
            raise SystemExit("candidate archive may contain only regular files")
        total += member.size
        if total > 100663296:
            raise SystemExit("candidate archive expands beyond the extraction limit")
        destination = os.path.realpath(os.path.join(root, *parts))
        if os.path.commonpath((root, destination)) != root:
            raise SystemExit("candidate archive path escaped extraction root")
        archive.extract(member, root, filter="data")
work = os.path.join(root, "agent-core")
os.chdir(work)
os.environ.clear()
os.environ.update({"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "TMPDIR": "/tmp", "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONPATH": work})
import pytest
sys.path.append(work)
class _ResultPlugin:
    passed = 0
    def pytest_runtest_logreport(self, report):
        if report.when == "call" and report.passed:
            self.passed += 1
sentinel_token, *test_paths = sys.argv[1:]
args = ["-q", "-c", "/dev/null", "-p", "no:cacheprovider", "--import-mode=importlib", *test_paths]
result = _ResultPlugin()
pytest_exit = int(pytest.main(args, plugins=[result]))
print(f"CODEX_VERIFY_RESULT:{sentinel_token}:{pytest_exit}:{result.passed}", flush=True)
raise SystemExit(pytest_exit if pytest_exit != 0 else (0 if result.passed > 0 else 5))
'''


def _format_result_sentinel(token: str, pytest_exit: int, passed: int) -> str:
    return f"CODEX_VERIFY_RESULT:{token}:{pytest_exit}:{passed}"


@dataclass(frozen=True)
class Verification:
    passed: bool
    summary: str
    return_code: int | None = None


class DockerVerifier:
    """Run candidate Python files in a restricted preloaded Linux container.

    Pytest plugin autoload is disabled: only core pytest features are available
    unless the pinned image explicitly bakes in trusted plugins.
    """

    def __init__(self, image: str, tests: tuple[str, ...], *, timeout: int = 90, executable: str | None = None, allowed_paths: tuple[str, ...] | None = None, minimum_passes: int = 1) -> None:
        if not isinstance(image, str) or not _IMAGE_ID_RE.fullmatch(image):
            raise ValueError("image must be an immutable sha256:<64 lowercase hex> image ID")
        if not isinstance(tests, tuple) or not tests:
            raise ValueError("tests must be a non-empty tuple of explicit pytest paths")
        normalized_tests = tuple(self._canonical_test_path(path) for path in tests)
        if len(set(normalized_tests)) != len(normalized_tests):
            raise ValueError("pytest paths must be unique after normalization")
        normalized_allowed = None
        if allowed_paths is not None:
            if not isinstance(allowed_paths, tuple) or not allowed_paths:
                raise ValueError("allowed_paths must be a non-empty tuple when supplied")
            normalized_allowed = tuple(self._canonical_allowed_path(path) for path in allowed_paths)
            if len(set(normalized_allowed)) != len(normalized_allowed):
                raise ValueError("allowed_paths must be unique after normalization")
            if not set(normalized_tests).issubset(normalized_allowed):
                raise ValueError("every explicit pytest path must be included in allowed_paths")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise ValueError("timeout must be a positive integer number of seconds")
        if executable is not None and not self._is_absolute_executable(executable):
            raise ValueError("Docker executable must be an absolute path")
        self.image, self.tests, self.timeout, self.executable = image, normalized_tests, timeout, executable
        self.allowed_paths = normalized_allowed
        if type(minimum_passes) is not int or minimum_passes < 1:
            raise ValueError("minimum_passes must be a positive integer")
        self.minimum_passes = minimum_passes
        self._endpoint: str | None = None

    @staticmethod
    def _canonical_test_path(path: str) -> str:
        if not isinstance(path, str) or not _TEST_PATH_RE.fullmatch(path):
            raise ValueError(f"unsafe explicit pytest path: {path!r}")
        canonical = path if path.startswith("agent-core/") else "agent-core/" + path
        if any(part in ("", ".", "..") for part in canonical.split("/")):
            raise ValueError(f"unsafe explicit pytest path: {path!r}")
        return canonical

    @staticmethod
    def _canonical_allowed_path(path: str) -> str:
        if not isinstance(path, str):
            raise ValueError(f"unsafe allowed path: {path!r}")
        canonical = path if path.startswith("agent-core/") else "agent-core/" + path
        if not _ALLOWED_PATH_RE.fullmatch(canonical) or any(part in ("", ".", "..") for part in canonical.split("/")):
            raise ValueError(f"unsafe allowed path: {path!r}")
        return canonical

    @staticmethod
    def _is_absolute_executable(value: str) -> bool:
        if not isinstance(value, str) or not value or "\0" in value:
            return False
        return os.path.isabs(value) or PureWindowsPath(value).is_absolute()

    def _docker_executable(self) -> str:
        if self.executable is not None:
            return self.executable
        found = shutil.which("docker")
        if not found:
            raise RuntimeError("Docker CLI was not found in the current user's PATH")
        return os.path.abspath(found)

    def _docker_endpoint(self) -> str:
        if self._endpoint:
            return self._endpoint
        if os.name == "nt":
            return "npipe:////./pipe/dockerDesktopLinuxEngine"
        if os.name == "posix":
            return "unix:///var/run/docker.sock"
        raise RuntimeError("unsupported host OS; only local Docker Desktop npipe or local Linux socket is allowed")

    @staticmethod
    def _docker_env() -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in {"DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"}}
        return env

    def _cli(self, *args: str) -> list[str]:
        return [self._docker_executable(), "--host", self._docker_endpoint(), *args]

    @staticmethod
    def _bounded(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        return value[-_MAX_LOG_BYTES:]

    @staticmethod
    def _has_success_sentinel(stdout: str | bytes, token: str, process_exit: int | None, *, minimum_passes: int = 1) -> bool:
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        expected = re.compile(r"^CODEX_VERIFY_RESULT:" + re.escape(token) + r":(\d+):(\d+)$")
        matches = [expected.fullmatch(line.strip()) for line in stdout.splitlines()]
        matches = [match for match in matches if match]
        if len(matches) != 1 or process_exit != 0:
            return False
        pytest_exit, passed = (int(value) for value in matches[0].groups())
        return pytest_exit == 0 and passed >= minimum_passes

    def preflight(self) -> None:
        exe, endpoint = self._docker_executable(), self._docker_endpoint()
        info = subprocess.run([exe, "--host", endpoint, "info", "--format", "{{.OSType}}"],
            shell=False, capture_output=True, text=True, timeout=min(self.timeout, 15), env=self._docker_env(), check=False)
        if info.returncode != 0:
            raise RuntimeError("local Docker daemon is unreachable: " + self._bounded(info.stderr or info.stdout))
        if info.stdout.strip().lower() != "linux":
            raise RuntimeError("candidate verification requires the local Linux Docker daemon")
        inspected = subprocess.run([exe, "--host", endpoint, "image", "inspect", self.image],
            shell=False, capture_output=True, text=True, timeout=min(self.timeout, 15), env=self._docker_env(), check=False)
        if inspected.returncode != 0:
            raise RuntimeError("pinned image is not already present locally; automatic pull is disabled")
        try:
            details = json.loads(inspected.stdout)
            image = details[0]
        except (ValueError, IndexError, TypeError, KeyError) as exc:
            raise RuntimeError("Docker returned invalid pinned-image metadata") from exc
        if image.get("Id") != self.image:
            raise RuntimeError("local image ID does not match the pinned immutable image ID")
        if str(image.get("Os", "")).lower() != "linux":
            raise RuntimeError("pinned candidate image must be a Linux image")
        if (image.get("Config") or {}).get("Volumes"):
            raise RuntimeError("pinned image declares volumes; refusing implicit or dangerous volume behavior")

    def _validate_archive(self, archive_bytes: bytes) -> tuple[str, ...]:
        if self.allowed_paths is None:
            raise ValueError("candidate verification requires an explicit broker-validated allowed_paths tuple")
        names: list[str] = []
        total = 0
        try:
            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as archive:
                members = archive.getmembers()
                if len(members) > _MAX_ARCHIVE_MEMBERS:
                    raise ValueError("candidate archive contains too many entries")
                for member in members:
                    name = member.name.replace("\\", "/")
                    parts = name.split("/")
                    if (not name.startswith("agent-core/") or not name.endswith(".py")
                            or name.startswith("/") or any(p in ("", ".", "..") for p in parts)):
                        raise ValueError(f"candidate archive path is not allowlisted: {name!r}")
                    if not member.isfile():
                        raise ValueError("candidate archive may contain only regular files")
                    if name not in self.allowed_paths:
                        raise ValueError(f"candidate archive path is not allowlisted: {name}")
                    if name in names:
                        raise ValueError(f"candidate archive contains duplicate path: {name}")
                    total += member.size
                    if member.size < 0 or total > _MAX_EXTRACTED_BYTES:
                        raise ValueError("candidate archive exceeds the expanded size limit")
                    names.append(name)
        except (tarfile.TarError, OSError) as exc:
            raise ValueError("candidate archive is not a valid tar archive") from exc
        if set(names) != set(self.allowed_paths):
            missing = sorted(set(self.allowed_paths) - set(names))
            extra = sorted(set(names) - set(self.allowed_paths))
            raise ValueError(f"candidate archive must exactly match allowed_paths (missing={missing}, extra={extra})")
        return tuple(sorted(names))

    def _create_container(self, name: str, owner_token: str | None = None) -> str:
        if owner_token is None:
            owner_token = name.rsplit("-", 1)[-1]
        if not re.fullmatch(r"[0-9a-f]{32}", owner_token) or name != "codex-evo-" + owner_token:
            raise ValueError("container name must be a verifier-generated owned name")
        pytest_paths = tuple(path.removeprefix("agent-core/") for path in self.tests)
        argv = self._cli("create", "--pull=never", "--interactive", "--log-driver=none", "--label", "codex.evo.owner=" + owner_token, "--name", name, "--network=none", "--read-only",
            "--user=65532:65532", "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
            "--memory=512m", "--memory-swap=512m", "--cpus=1", "--pids-limit=128", "--ulimit=nofile=64:64",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=128m", "--workdir=/tmp",
            "--entrypoint", "/usr/local/bin/python", self.image, "-I", "-c", _BOOTSTRAP, owner_token, *pytest_paths)
        result = subprocess.run(argv, shell=False, capture_output=True, timeout=min(self.timeout, 20), env=self._docker_env(), check=False)
        if result.returncode != 0:
            raise RuntimeError("container creation failed: " + self._bounded(result.stderr or result.stdout))
        container_id = (result.stdout or b"").decode("ascii", errors="ignore").strip()
        if not _CONTAINER_ID_RE.fullmatch(container_id):
            raise RuntimeError("Docker returned an invalid container ID")
        return container_id

    def _remove_container(self, container_id: str) -> None:
        try:
            subprocess.run(self._cli("rm", "--force", container_id), shell=False, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=10, env=self._docker_env(), check=False)
        except (OSError, subprocess.SubprocessError):
            pass

    def _remove_owned_name(self, name: str, owner_token: str) -> None:
        """Recover cleanup if create timed out after Docker accepted the request."""
        try:
            inspected = subprocess.run(
                self._cli("inspect", "--format", "{{.Id}} {{index .Config.Labels \"codex.evo.owner\"}}", name),
                shell=False, capture_output=True, timeout=5, env=self._docker_env(), check=False,
            )
            if inspected.returncode != 0:
                return
            fields = (inspected.stdout or b"").decode("ascii", errors="ignore").strip().split()
            if len(fields) == 2 and _CONTAINER_ID_RE.fullmatch(fields[0]) and fields[1] == owner_token:
                self._remove_container(fields[0])
        except (OSError, subprocess.SubprocessError):
            pass

    def _start_container(self, container_id: str, archive: bytes, cancelled: Callable[[], bool], sentinel_token: str) -> Verification:
        try:
            process = subprocess.Popen(self._cli("start", "--attach", "--interactive", container_id), shell=False,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self._docker_env(), bufsize=0)
        except OSError as exc:
            return Verification(False, f"could not start candidate container: {exc}")
        output = {"out": bytearray(), "err": bytearray()}
        def drain(stream, key):
            try:
                while True:
                    data = stream.read(4096)
                    if not data:
                        break
                    output[key].extend(data)
                    if len(output[key]) > _MAX_LOG_BYTES:
                        del output[key][:-_MAX_LOG_BYTES]
            finally:
                stream.close()
        def send():
            try:
                view = memoryview(archive)
                while view:
                    count = process.stdin.write(view[:65536])
                    if not count:
                        break
                    view = view[count:]
                process.stdin.close()
            except (BrokenPipeError, OSError, AttributeError):
                pass
        assert process.stdout is not None and process.stderr is not None
        readers = [threading.Thread(target=drain, args=(process.stdout, "out"), daemon=True),
                   threading.Thread(target=drain, args=(process.stderr, "err"), daemon=True),
                   threading.Thread(target=send, daemon=True)]
        for thread in readers:
            thread.start()
        deadline = time.monotonic() + self.timeout
        reason = ""
        while process.poll() is None:
            if cancelled():
                reason = "candidate verification cancelled"
                break
            if time.monotonic() >= deadline:
                reason = f"candidate verification timed out after {self.timeout}s"
                break
            time.sleep(0.05)
        if reason:
            try:
                process.terminate()
                process.wait(timeout=1)
            except (OSError, subprocess.SubprocessError):
                try:
                    process.kill()
                except OSError:
                    pass
            return Verification(False, reason)
        for thread in readers:
            thread.join(timeout=1)
        rc = process.returncode
        stdout = bytes(output["out"])
        passed = self._has_success_sentinel(stdout, sentinel_token, rc, minimum_passes=self.minimum_passes)
        text = stdout.decode("utf-8", errors="replace")
        text = "\n".join(line for line in text.splitlines() if not line.startswith("CODEX_VERIFY_RESULT:"))
        log = self._bounded((text + "\n" + bytes(output["err"]).decode("utf-8", errors="replace")).encode()).strip()
        if not passed:
            return Verification(False, log or "pytest did not produce a valid success sentinel with enough passed tests", rc)
        return Verification(True, log or "candidate tests passed", rc)

    def run(self, archive: bytes, *, cancelled: Callable[[], bool] = lambda: False) -> Verification:
        if not isinstance(archive, bytes) or not archive:
            return Verification(False, "candidate archive must be non-empty bytes")
        if len(archive) > _MAX_ARCHIVE_BYTES:
            return Verification(False, f"candidate archive exceeds {_MAX_ARCHIVE_BYTES} bytes")
        if not callable(cancelled):
            return Verification(False, "cancelled must be callable")
        container_id = None
        owner_token = uuid.uuid4().hex
        container_name = "codex-evo-" + owner_token
        create_attempted = False
        try:
            self._validate_archive(archive)
            self.preflight()
            if cancelled():
                return Verification(False, "candidate verification cancelled")
            create_attempted = True
            container_id = self._create_container(container_name, owner_token)
            return self._start_container(container_id, archive, cancelled, owner_token)
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
            return Verification(False, self._bounded(str(exc)) or type(exc).__name__)
        finally:
            if container_id is not None:
                self._remove_container(container_id)
            elif create_attempted:
                self._remove_owned_name(container_name, owner_token)
