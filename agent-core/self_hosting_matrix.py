from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import shutil
import socket
import time
import sys
import tempfile
from pathlib import Path

from release_manager import ReleaseController, build_artifact
from self_hosting_verify import ScenarioResult, ScenarioStatus, SelfHostingReport, VerificationLevel

ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str], cwd: Path) -> tuple[int, str]:
    completed = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return completed.returncode, ((completed.stdout or "") + (completed.stderr or ""))[-3000:]


def run_local_matrix(*, model_id: str = "offline-local", skill_identity: str = "self-hosting-verification@local", runtime_version: str = "local") -> SelfHostingReport:
    report = SelfHostingReport(
        run_id=f"local-{__import__('uuid').uuid4().hex[:12]}",
        model_id=model_id,
        skill_identity=skill_identity,
        runtime_version=runtime_version,
    )
    with tempfile.TemporaryDirectory(prefix="my-agent-self-hosting-") as directory:
        root = Path(directory)
        (root / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (root / "test_app.py").write_text("from app import add\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8")
        code, output = _run([sys.executable, "-m", "pytest", "-q"], root)
        report.add(ScenarioResult(
            "S1",
            ScenarioStatus.PASSED if code == 0 else ScenarioStatus.FAILED,
            VerificationLevel.E3,
            evidence=(f"cwd={root}", f"exit_code={code}", output[-500:]),
            reason="local generated project pytest",
        ))

    code, output = _run([sys.executable, str(ROOT / "tools" / "run_main_tests.py"), "--scope", "collect"], ROOT.parent)
    report.add(ScenarioResult(
        "S2",
        ScenarioStatus.PASSED if code == 0 else ScenarioStatus.FAILED,
        VerificationLevel.E2,
        evidence=(f"exit_code={code}", output[-500:]),
        reason="main project verification scope",
    ))
    report.add(ScenarioResult("S3", ScenarioStatus.NOT_RUN, VerificationLevel.E0, reason="requires GitHub remote and PR"))
    report.add(ScenarioResult("S4", ScenarioStatus.NOT_RUN, VerificationLevel.E0, reason="requires GitHub Actions run"))
    report.add(ScenarioResult("S5", ScenarioStatus.NOT_RUN, VerificationLevel.E0, reason="requires release/deploy target"))
    report.add(ScenarioResult("S6", ScenarioStatus.NOT_RUN, VerificationLevel.E0, reason="requires candidate Host Agent process"))
    return report






def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _cleanup_probe_directory(path: Path) -> None:
    # Windows can keep a just-exited child cwd locked for a short interval.
    for _ in range(20):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.1)
    shutil.rmtree(path, ignore_errors=True)


def _load_deploy_runner():
    tool_path = ROOT / "evolve" / "tools" / "coding" / "deploy" / "main.py"
    spec = importlib.util.spec_from_file_location("self_hosting_deploy_tool", tool_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load deploy tool")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_deploy


def run_local_deploy_probe() -> ScenarioResult:
    service_name = f"self-hosting-probe-{__import__('uuid').uuid4().hex[:8]}"
    parent = ROOT / "data" / "self-hosting"
    target_root = parent / f"deploy-target-{service_name[-8:]}"
    artifact_path = parent / f"{service_name}.zip"
    port = _free_port()
    deploy = None
    try:
        target_root.mkdir(parents=True, exist_ok=True)
        (target_root / "health.txt").write_text("healthy\n", encoding="utf-8")
        artifact = build_artifact(target_root, artifact_path, version="self-hosting-probe")
        deploy = _load_deploy_runner()
        relative_target = target_root.relative_to(ROOT).as_posix()
        relative_artifact = artifact_path.relative_to(ROOT).as_posix()
        start = deploy({
            "action": "start",
            "service_name": service_name,
            "command": [sys.executable, "-m", "http.server", str(port)],
            "working_dir": relative_target,
            "ready_port": port,
            "ready_timeout_sec": 8,
            "artifact_path": relative_artifact,
            "artifact_digest": artifact.digest,
            "dry_run": False,
        })
        health = deploy({
            "action": "health",
            "service_name": service_name,
            "ready_port": port,
            "ready_timeout_sec": 3,
            "dry_run": False,
        })
        healthy = bool(start.get("ok")) and bool(start.get("ready")) and bool(health.get("ok")) and bool(health.get("ready"))
        return ScenarioResult(
            "S5",
            ScenarioStatus.PASSED if healthy else ScenarioStatus.FAILED,
            VerificationLevel.E5 if healthy else VerificationLevel.E0,
            evidence=(
                f"service={service_name}",
                f"port={port}",
                f"artifact_digest={artifact.digest}",
                f"start_ready={bool(start.get('ready'))}",
                f"health_ready={bool(health.get('ready'))}",
            ),
            reason="controlled local deploy target health probe" if healthy else str(start.get("error") or health.get("error") or "deploy target health failed"),
        )
    except Exception as exc:
        return ScenarioResult("S5", ScenarioStatus.FAILED, VerificationLevel.E0, reason=f"deploy probe error: {exc}")
    finally:
        if deploy is not None:
            try:
                deploy({"action": "stop", "service_name": service_name, "dry_run": False})
            except Exception:
                pass
        _cleanup_probe_directory(target_root)
        try:
            artifact_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        for suffix in (".json", ".log"):
            try:
                (ROOT / "data" / "services" / f"{service_name}{suffix}").unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _stop_probe_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def run_host_rollback_probe() -> ScenarioResult:
    root = Path(tempfile.mkdtemp(prefix="my-agent-rollback-probe-"))
    old_port = _free_port()
    candidate_port = _free_port()
    old_process: subprocess.Popen[str] | None = None
    candidate_process: subprocess.Popen[str] | None = None
    try:
        old_process = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(old_port)],
            cwd=str(root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        old_state = {
            "ok": True,
            "version": "old",
            "pid": old_process.pid,
            "port": old_port,
            "process": old_process,
        }

        def health(state: dict[str, object]) -> bool:
            process = state.get("process")
            if not isinstance(process, subprocess.Popen):
                return False
            if process.poll() is not None:
                return False
            if state.get("version") == "candidate-broken":
                return False
            try:
                with socket.create_connection(("127.0.0.1", int(state["port"])), timeout=0.3):
                    return True
            except OSError:
                return False

        old_ready = False
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if health(old_state):
                old_ready = True
                break
            time.sleep(0.1)
        if not old_ready:
            return ScenarioResult("S6", ScenarioStatus.FAILED, VerificationLevel.E0, reason="old service did not become healthy")

        candidate_holder: dict[str, subprocess.Popen[str]] = {}

        def deploy(candidate: dict[str, object]) -> dict[str, object]:
            nonlocal candidate_process
            candidate_process = subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.exit(23)"],
                cwd=str(root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            candidate_holder["process"] = candidate_process
            return {
                "ok": True,
                "version": candidate["version"],
                "pid": candidate_process.pid,
                "port": candidate_port,
                "process": candidate_process,
            }

        def rollback(previous: dict[str, object]) -> dict[str, object]:
            _stop_probe_process(candidate_holder.get("process"))
            return {**previous, "ok": True}

        controller = ReleaseController(
            deploy=deploy,
            health=health,
            rollback=rollback,
        )
        result = controller.deploy_with_rollback(
            old_state,
            {"version": "candidate-broken", "port": candidate_port},
        )
        recovered_health = health(old_state)
        passed = (
            not result.ok
            and result.state == "rolled_back"
            and result.recovered
            and recovered_health
        )
        return ScenarioResult(
            "S6",
            ScenarioStatus.PASSED if passed else ScenarioStatus.FAILED,
            VerificationLevel.E6 if passed else VerificationLevel.E0,
            evidence=(
                f"old_port={old_port}",
                f"candidate_pid={candidate_process.pid if candidate_process else 0}",
                f"rollback_state={result.state}",
                f"old_health_after_rollback={recovered_health}",
            ),
            reason="candidate failure rollback recovered old service" if passed else "candidate failure rollback did not recover old service",
        )
    finally:
        _stop_probe_process(candidate_process)
        _stop_probe_process(old_process)
        _cleanup_probe_directory(root)


def _probe_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if handle:
            exit_code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return bool(ok) and int(exit_code.value) == 259
        return ctypes.windll.kernel32.GetLastError() == 5
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _clear_probe_interface_lock(pid: int) -> None:
    lock_path = ROOT / "data" / "sessions" / ".interface.lock"
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict) or payload.get("ui") != "electron":
        return
    owner_pid = int(payload.get("pid") or 0)
    # The Windows Python launcher may leave a child PID in the lock rather than
    # Popen.pid. Remove only a lock owned by a dead process; never touch a live
    # Electron/Host Agent lock.
    if owner_pid not in {pid} and _probe_pid_alive(owner_pid):
        return
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def run_host_candidate_probe() -> ScenarioResult:
    command = [sys.executable, str(ROOT / "agent-core" / "server.py"), "--port", "0"]
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
    )
    ready_line = ""
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            line = process.stdout.readline() if process.stdout is not None else ""
            if line:
                ready_line = line.strip()
                try:
                    payload = json.loads(ready_line)
                except json.JSONDecodeError:
                    continue
                if payload.get("ready") is True and isinstance(payload.get("port"), int):
                    return ScenarioResult(
                        "S6",
                        ScenarioStatus.PASSED,
                        VerificationLevel.E5,
                        evidence=(f"port={payload['port']}", f"pid={process.pid}"),
                        reason="temporary Host Agent sidecar ready probe",
                    )
            if process.poll() is not None:
                break
        return ScenarioResult("S6", ScenarioStatus.FAILED, VerificationLevel.E0, reason=ready_line or "candidate sidecar did not become ready")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        _clear_probe_interface_lock(process.pid)

def run_remote_matrix(
    *,
    repository: str,
    pr_number: int,
    runner=None,
    include_runtime: bool = False,
    model_id: str = "0x567-flash",
    skill_identity: str = "self-hosting-verification@local",
    runtime_version: str = "local",
) -> SelfHostingReport:
    report = run_local_matrix(
        model_id=model_id,
        skill_identity=skill_identity,
        runtime_version=runtime_version,
    )
    if runner is None:
        def runner(kind: str):
            if kind == "view":
                command = ["gh", "pr", "view", str(pr_number), "--repo", repository, "--json", "state,baseRefName,mergeCommit"]
            else:
                command = ["gh", "pr", "checks", str(pr_number), "--repo", repository, "--json", "name,state,bucket"]
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if completed.returncode != 0:
                return completed.returncode, {}, completed.stderr or completed.stdout
            return 0, json.loads(completed.stdout or ("{}" if kind == "view" else "[]")), ""
    code, view, error = runner("view")
    scenarios = {item.scenario_id: item for item in report.scenarios}
    if code == 0 and isinstance(view, dict) and view.get("state") == "MERGED" and view.get("baseRefName") == "main":
        scenarios["S3"] = ScenarioResult("S3", ScenarioStatus.PASSED, VerificationLevel.E4, (f"repository={repository}", f"pr={pr_number}", f"merge={view.get('mergeCommit', {}).get('oid', '')}"))
    else:
        scenarios["S3"] = ScenarioResult("S3", ScenarioStatus.FAILED, VerificationLevel.E0, reason=error or "PR is not merged to main")
    code, checks, error = runner("checks")
    checks_ok = code == 0 and isinstance(checks, list) and bool(checks) and all(str(item.get("bucket") or "").lower() == "pass" for item in checks if isinstance(item, dict))
    scenarios["S4"] = ScenarioResult("S4", ScenarioStatus.PASSED if checks_ok else ScenarioStatus.FAILED, VerificationLevel.E4, evidence=(f"checks={len(checks) if isinstance(checks, list) else 0}",), reason="required checks passed" if checks_ok else error or "required checks not all passed")
    if include_runtime:
        scenarios["S5"] = run_local_deploy_probe()
        candidate_probe = run_host_candidate_probe()
        rollback_probe = run_host_rollback_probe()
        candidate_ok = candidate_probe.status == ScenarioStatus.PASSED
        rollback_ok = rollback_probe.status == ScenarioStatus.PASSED
        scenarios["S6"] = ScenarioResult(
            "S6",
            ScenarioStatus.PASSED if candidate_ok and rollback_ok else ScenarioStatus.FAILED,
            VerificationLevel.E6 if candidate_ok and rollback_ok else VerificationLevel.E0,
            evidence=(
                *candidate_probe.evidence,
                *rollback_probe.evidence,
            ),
            reason=(
                "Host Agent candidate ready and rollback recovered old service"
                if candidate_ok and rollback_ok
                else f"candidate={candidate_probe.reason or candidate_probe.status.value}; rollback={rollback_probe.reason or rollback_probe.status.value}"
            ),
        )
    report.scenarios = [scenarios[item.scenario_id] for item in report.scenarios]
    return report

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the self-hosting verification matrix")
    parser.add_argument("--model", default="offline-local")
    parser.add_argument("--skill", default="self-hosting-verification@local")
    parser.add_argument("--runtime", default="local")
    parser.add_argument("--repository")
    parser.add_argument("--pr", type=int)
    parser.add_argument("--include-runtime", action="store_true")
    parser.add_argument(
        "--matrix",
        help="Optional path, relative to the repository root, for the JSON report",
    )
    args = parser.parse_args(argv)

    if (args.repository is None) != (args.pr is None):
        parser.error("--repository and --pr must be supplied together")

    if args.repository is not None:
        report = run_remote_matrix(
            repository=args.repository,
            pr_number=args.pr,
            include_runtime=args.include_runtime,
            model_id=args.model,
            skill_identity=args.skill,
            runtime_version=args.runtime,
        )
    else:
        report = run_local_matrix(
            model_id=args.model,
            skill_identity=args.skill,
            runtime_version=args.runtime,
        )

    if args.matrix:
        report.save(ROOT / args.matrix)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.disposition in {"partial", "verified"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
