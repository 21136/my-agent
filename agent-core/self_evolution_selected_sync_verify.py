from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import uuid
from dataclasses import dataclass
from typing import Callable, Literal, Mapping

from self_evolution_auto_runner import DockerVerifier, Verification

_MAX_FILE_BYTES = 1 * 1024 * 1024
_MAX_FILES = 4096
_ALLOWED_SUFFIXES = {".py", ".mjs", ".js", ".ts", ".json", ".md", ".css", ".source"}
_SCOPE_ONLY_PATHS = {"tools/incident-repair-verifier/Dockerfile", "evolve/_index.core.toml", "evolve/_index.toml"}
_PATH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|mjs|js|ts|json|md|css|source)$")
_JS_SUFFIXES = {".mjs", ".js", ".ts"}

# This launcher and the command selection are trusted verifier code. Snapshot
# content can provide tests, but cannot choose executables, arguments, or env.
_MIXED_BOOTSTRAP = r'''import os, re, sys, tarfile, subprocess
from pathlib import PurePosixPath
root = "/tmp/selected-sync"
os.makedirs(root, mode=0o700)
with tarfile.open(fileobj=sys.stdin.buffer, mode="r|*") as archive:
    total = 0
    for index, member in enumerate(archive):
        if index >= 4096:
            raise SystemExit("snapshot has too many files")
        name = member.name
        path = PurePosixPath(name)
        if path.is_absolute() or not name or any(part in ("", ".", "..") for part in path.parts):
            raise SystemExit("snapshot contains unsafe path")
        if not member.isfile():
            raise SystemExit("snapshot may contain regular files only")
        total += member.size
        if member.size < 0 or member.size > 1048576 or total > 100663296:
            raise SystemExit("snapshot exceeds extraction limits")
        if any(part.lower() in (".git", "node_modules", ".venv", "venv") for part in path.parts):
            raise SystemExit("snapshot contains forbidden runtime or repository metadata")
        destination = os.path.realpath(os.path.join(root, *path.parts))
        if os.path.commonpath((root, destination)) != root:
            raise SystemExit("snapshot path escaped extraction root")
        archive.extract(member, root, filter="data")
os.chdir(root)
sys.path.append(root)
sys.path.append(os.path.join(root, "agent-core"))
os.environ.clear()
os.environ.update({"PATH":"/usr/local/bin:/usr/bin:/bin", "HOME":"/tmp", "TMPDIR":"/tmp", "PYTHONDONTWRITEBYTECODE":"1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD":"1", "PYTHONPATH":root + os.pathsep + os.path.join(root, "agent-core"), "NODE_PATH":"/opt/node_modules", "CI":"1"})
suite, token, *checks = sys.argv[1:]
if not re.fullmatch(r"[0-9a-f]{32}", token):
    raise SystemExit("invalid verifier token")
if suite not in ("baseline", "candidate") or not checks:
    raise SystemExit("invalid trusted suite configuration")
python_checks = [p for p in checks if p.endswith(".py")]
node_checks = [p for p in checks if p.endswith((".mjs", ".js", ".ts"))]
other_checks = [p for p in checks if p not in python_checks and p not in node_checks]
if other_checks or not (python_checks or node_checks):
    raise SystemExit("trusted checks must contain at least one supported test path")
passed = 0
exit_code = 0
if python_checks:
    import pytest
    class ResultPlugin:
        passed = 0
        def pytest_runtest_logreport(self, report):
            if report.when == "call" and report.passed:
                self.passed += 1
    plugin = ResultPlugin()
    rc = int(pytest.main(["-q", "-c", "/dev/null", "-p", "no:cacheprovider", "--noconftest", "--import-mode=importlib", *python_checks], plugins=[plugin]))
    passed += plugin.passed
    exit_code = max(exit_code, rc)
    if plugin.passed < 1:
        exit_code = max(exit_code, 5)
if node_checks:
    if any(p.endswith(".ts") for p in node_checks):
        probe = subprocess.run(["node", "-e", "const {createRequire}=require('node:module'); const r=createRequire('/opt/selected-sync-runtime/verify.cjs'); const ts=r('typescript'); if(ts.version!=='5.7.2') process.exit(71);"], cwd=root, env=os.environ.copy(), shell=False, capture_output=True, text=True)
        if probe.returncode != 0:
            print("pinned external TypeScript runtime unavailable", file=sys.stderr, flush=True)
            print(f"CODEX_VERIFY_RESULT:{token}:71:{passed}", flush=True)
            raise SystemExit(71)
    proc = subprocess.run(["node", "--test", "--test-reporter=tap", *node_checks], cwd=root, env=os.environ.copy(), shell=False, capture_output=True, text=True, timeout=600)
    print(proc.stdout[-65536:], flush=True)
    if proc.stderr:
        print(proc.stderr[-65536:], file=sys.stderr, flush=True)
    ok_lines = [line for line in proc.stdout.splitlines() if re.match(r"^ok [0-9]+ -", line) and "# SKIP" not in line]
    passed += len(ok_lines)
    exit_code = max(exit_code, int(proc.returncode or 0))
    if len(ok_lines) < 1:
        exit_code = max(exit_code, 5)
print(f"CODEX_VERIFY_RESULT:{token}:{exit_code}:{passed}", flush=True)
raise SystemExit(exit_code)
'''


@dataclass(frozen=True)
class VerificationReceipt:
    passed: bool
    reason: str
    baseline: Verification
    candidate: Verification
    baseline_digest: str
    candidate_digest: str
    stage: Literal["preflight", "baseline", "candidate", "passed"]
    diagnostics: tuple[str, ...] = ()


class SelectedSyncVerifier(DockerVerifier):
    """Compare trusted baseline and candidate snapshots in hardened Docker.

    ``baseline_checks`` and ``candidate_checks`` are fixed check-path tuples
    supplied by the trusted policy broker. Check files come from the baseline
    snapshot (the model cannot replace them in the candidate); all commands
    and runners are selected by this module, never by snapshot content.
    """

    def __init__(
        self,
        image: str,
        baseline_checks: tuple[str, ...],
        candidate_checks: tuple[str, ...],
        *,
        timeout: int = 300,
        executable: str | None = None,
    ) -> None:
        if not isinstance(baseline_checks, tuple) or not baseline_checks:
            raise ValueError("baseline_checks must be a non-empty trusted tuple")
        if not isinstance(candidate_checks, tuple) or not candidate_checks:
            raise ValueError("candidate_checks must be a non-empty trusted tuple")
        normalized_baseline = tuple(self._check_path(path) for path in baseline_checks)
        normalized_candidate = tuple(self._check_path(path) for path in candidate_checks)
        if len(set(normalized_baseline)) != len(normalized_baseline) or len(set(normalized_candidate)) != len(normalized_candidate):
            raise ValueError("trusted check paths must be unique")
        for label, checks in (("baseline", normalized_baseline), ("candidate", normalized_candidate)):
            if any(os.path.splitext(path)[1].lower() not in ({".py"} | _JS_SUFFIXES) for path in checks):
                raise ValueError(f"{label}_checks may contain only Python or Desktop test paths")
        super().__init__(image, ("agent-core/tests/test_candidate.py",), timeout=timeout,
                         executable=executable, allowed_paths=("agent-core/tests/test_candidate.py",), minimum_passes=1)
        self.baseline_checks = normalized_baseline
        self.candidate_checks = normalized_candidate

    def preflight(self) -> None:
        super().preflight()
        inspected = subprocess.run(self._cli("image", "inspect", self.image), shell=False,
            capture_output=True, text=True, timeout=min(self.timeout, 15), env=self._docker_env(), check=False)
        if inspected.returncode != 0:
            raise RuntimeError("could not re-inspect pinned image environment")
        try:
            image = json.loads(inspected.stdout)[0]
            image_env = (image.get("Config") or {}).get("Env") or []
        except (ValueError, IndexError, TypeError, AttributeError) as exc:
            raise RuntimeError("Docker returned invalid pinned-image environment metadata") from exc
        if not isinstance(image_env, list) or any(not isinstance(item, str) or "=" not in item for item in image_env):
            raise RuntimeError("pinned image has invalid environment metadata")
        forbidden = re.compile(r"(?:TOKEN|SECRET|PASSWORD|CREDENTIAL|API.?KEY|PRIVATE.?KEY|AUTH)", re.IGNORECASE)
        if any(forbidden.search(item.split("=", 1)[0]) for item in image_env):
            raise RuntimeError("pinned image declares a credential-like environment variable")

    @staticmethod
    def _check_path(path: str) -> str:
        if not isinstance(path, str) or not _PATH_RE.fullmatch(path):
            raise ValueError(f"unsafe trusted check path: {path!r}")
        canonical = path.replace("\\", "/")
        if canonical.startswith("/") or any(part in ("", ".", "..") for part in canonical.split("/")):
            raise ValueError(f"unsafe trusted check path: {path!r}")
        if any(part.lower() in (".git", "node_modules", ".venv", "venv") for part in canonical.split("/")):
            raise ValueError(f"unsafe trusted check path: {path!r}")
        if any(re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part, re.IGNORECASE) for part in canonical.split("/")):
            raise ValueError(f"unsafe reserved Windows path: {path!r}")
        return canonical

    @staticmethod
    def _snapshot_digest(snapshot: Mapping[str, bytes]) -> str:
        digest = hashlib.sha256()
        for path in sorted(snapshot):
            name = path.encode("utf-8")
            data = snapshot[path]
            digest.update(len(name).to_bytes(4, "big")); digest.update(name)
            digest.update(len(data).to_bytes(8, "big")); digest.update(data)
        return digest.hexdigest()

    @classmethod
    def _snapshot_path(cls, path: str) -> str:
        if isinstance(path, str) and path in _SCOPE_ONLY_PATHS:
            return path
        return cls._check_path(path)

    def _validate_snapshot(self, snapshot: Mapping[str, bytes], checks: tuple[str, ...]) -> None:
        if not isinstance(snapshot, Mapping) or not snapshot:
            raise ValueError("snapshot must be a non-empty path-to-bytes mapping")
        if len(snapshot) > _MAX_FILES:
            raise ValueError(f"snapshot contains more than {_MAX_FILES} files")
        seen: set[str] = set()
        total = 0
        for raw_path, data in snapshot.items():
            path = self._snapshot_path(raw_path)
            if path in seen:
                raise ValueError(f"snapshot contains duplicate normalized path: {path}")
            seen.add(path)
            suffix = os.path.splitext(path)[1].lower()
            if path not in _SCOPE_ONLY_PATHS and suffix not in _ALLOWED_SUFFIXES:
                raise ValueError(f"snapshot file type is not allowed: {path}")
            if not isinstance(data, bytes):
                raise ValueError(f"snapshot value must be bytes: {path}")
            if len(data) > _MAX_FILE_BYTES:
                raise ValueError(f"snapshot file exceeds {_MAX_FILE_BYTES} bytes: {path}")
            total += len(data)
            if total > 64 * 1024 * 1024:
                raise ValueError("snapshot exceeds the 64 MiB input limit")
            if any(part.lower() in (".git", "node_modules", ".venv", "venv") for part in path.split("/")):
                raise ValueError(f"snapshot contains forbidden repository/runtime path: {path}")
            if path in _SCOPE_ONLY_PATHS and path in checks:
                raise ValueError(f"scope-only files cannot be executed as tests: {path}")
        missing = sorted(set(checks) - seen)
        if missing:
            raise ValueError("snapshot is missing trusted test files: " + ", ".join(missing))
        if any(not snapshot[path].strip() for path in checks):
            raise ValueError("trusted test files must be non-empty")

    @staticmethod
    def _validate_scope_artifacts(baseline: Mapping[str, bytes], candidate: Mapping[str, bytes]) -> None:
        for path in _SCOPE_ONLY_PATHS:
            if (path in baseline) != (path in candidate):
                raise ValueError(f"scope artifact must be present in both snapshots or neither: {path}")
            if path in baseline and baseline[path] != candidate[path]:
                raise ValueError(f"scope artifact bytes must remain pinned across snapshots: {path}")

    @staticmethod
    def _archive(snapshot: Mapping[str, bytes]) -> bytes:
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for path in sorted(snapshot):
                info = tarfile.TarInfo(path)
                info.size = len(snapshot[path])
                info.mode = 0o444
                info.uid = info.gid = 65532
                info.uname = info.gname = ""
                archive.addfile(info, io.BytesIO(snapshot[path]))
        data = output.getvalue()
        if len(data) > 64 * 1024 * 1024:
            raise ValueError("snapshot archive exceeds the 64 MiB input limit")
        return data

    def _create_snapshot_container(self, name: str, owner_token: str, checks: tuple[str, ...]) -> str:
        if not re.fullmatch(r"[0-9a-f]{32}", owner_token) or name != "codex-evo-" + owner_token:
            raise ValueError("container name must be verifier-generated")
        if not self._docker_endpoint():
            raise RuntimeError("trusted local Docker endpoint unavailable")
        argv = self._cli("create", "--pull=never", "--interactive", "--log-driver=none",
            "--label", "codex.evo.owner=" + owner_token, "--name", name, "--network=none", "--read-only",
            "--user=65532:65532", "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
            "--memory=768m", "--memory-swap=768m", "--cpus=1", "--pids-limit=128", "--ulimit=nofile=64:64",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=192m", "--workdir=/tmp",
            "--entrypoint", "/usr/local/bin/python", self.image, "-I", "-c", _MIXED_BOOTSTRAP,
            "candidate", owner_token, *checks)
        result = subprocess.run(argv, shell=False, capture_output=True, timeout=min(self.timeout, 20), env=self._docker_env(), check=False)
        if result.returncode != 0:
            raise RuntimeError("snapshot container creation failed: " + self._bounded(result.stderr or result.stdout))
        container_id = (result.stdout or b"").decode("ascii", errors="ignore").strip()
        if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
            raise RuntimeError("Docker returned an invalid container ID")
        return container_id

    def _run_snapshot(self, snapshot: Mapping[str, bytes], checks: tuple[str, ...], cancelled: Callable[[], bool]) -> Verification:
        token = uuid.uuid4().hex
        name = "codex-evo-" + token
        container_id = None
        create_attempted = False
        try:
            if cancelled():
                return Verification(False, "verification cancelled")
            archive = self._archive(snapshot)
            create_attempted = True
            container_id = self._create_snapshot_container(name, token, checks)
            return self._start_container(container_id, archive, cancelled, token)
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
            return Verification(False, self._bounded(str(exc)) or type(exc).__name__)
        finally:
            if container_id is not None:
                self._remove_container(container_id)
            elif create_attempted:
                self._remove_owned_name(name, token)

    def verify(
        self,
        baseline: Mapping[str, bytes],
        candidate: Mapping[str, bytes],
        *,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> VerificationReceipt:
        all_checks = tuple(dict.fromkeys((*self.baseline_checks, *self.candidate_checks)))
        if not callable(cancelled):
            raise ValueError("cancelled must be callable")
        try:
            self._validate_snapshot(baseline, all_checks)
            self._validate_snapshot(candidate, all_checks)
            self._validate_scope_artifacts(baseline, candidate)
            untrusted_checks = [path for path in all_checks if baseline[path] != candidate[path]]
            if untrusted_checks:
                raise ValueError("trusted test bytes differ")
            baseline_sources = {path: data for path, data in baseline.items() if path.endswith(".source")}
            candidate_sources = {path: data for path, data in candidate.items() if path.endswith(".source")}
            if baseline_sources != candidate_sources:
                raise ValueError("pinned .source inputs differ")
            baseline_digest = self._snapshot_digest(baseline)
            candidate_digest = self._snapshot_digest(candidate)
        except (TypeError, ValueError):
            invalid = Verification(False, "snapshot or trusted-test validation failed", None)
            return VerificationReceipt(False, "Snapshot or trusted-test validation failed.", invalid, invalid,
                                       "", "", "preflight")
        if cancelled():
            stopped = Verification(False, "verification cancelled", None)
            return VerificationReceipt(False, "Verification cancelled before checks started.", stopped, stopped,
                                       baseline_digest, candidate_digest, "preflight")
        try:
            self.preflight()
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
            failed = Verification(False, "Docker preflight failed", None)
            return VerificationReceipt(False, "Trusted verification environment is unavailable.", failed, failed,
                                       baseline_digest, candidate_digest, "preflight")
        baseline_raw = self._run_snapshot(baseline, self.baseline_checks, cancelled)
        baseline_result = Verification(baseline_raw.passed,
            "baseline checks passed" if baseline_raw.passed else "baseline checks failed", baseline_raw.return_code)
        if not baseline_raw.passed:
            not_run = Verification(False, "candidate checks not run", None)
            reason = "Baseline checks failed; candidate checks were not run."
            if cancelled():
                reason = "Baseline verification was cancelled or interrupted."
            return VerificationReceipt(False, reason, baseline_result, not_run, baseline_digest, candidate_digest, "baseline")
        candidate_raw = self._run_snapshot(candidate, self.candidate_checks, cancelled)
        candidate_result = Verification(candidate_raw.passed,
            "candidate checks passed" if candidate_raw.passed else "candidate checks failed", candidate_raw.return_code)
        if candidate_raw.passed:
            return VerificationReceipt(True, "Baseline and candidate checks passed.", baseline_result, candidate_result,
                                       baseline_digest, candidate_digest, "passed")
        reason = "Candidate checks failed."
        if cancelled():
            reason = "Candidate verification was cancelled or interrupted."
        # Return only bounded diagnostic identifiers, not paths or raw test logs.
        diagnostics = []
        for error, name in re.findall(
            r"\b(AttributeError|NameError|ImportError|ModuleNotFoundError):[^\r\n]{0,180}?'([A-Za-z_][A-Za-z0-9_.]{0,120})'",
            candidate_raw.summary,
        ):
            value = f"{error}: missing identifier {name}"
            if value not in diagnostics:
                diagnostics.append(value)
            if len(diagnostics) == 6:
                break
        for suite in re.findall(r"^FAILED [^\r\n]{0,160}?::(test_[A-Za-z0-9_]{1,100})", candidate_raw.summary, re.M):
            value = f"Failed regression: {suite}"
            if value not in diagnostics and len(diagnostics) < 8:
                diagnostics.append(value)
        if not diagnostics:
            diagnostics.append("Fixed candidate regression failed; review the chosen conflict replacements.")
        return VerificationReceipt(False, reason, baseline_result, candidate_result, baseline_digest, candidate_digest,
                                   "candidate", tuple(diagnostics) if not cancelled() else ())
