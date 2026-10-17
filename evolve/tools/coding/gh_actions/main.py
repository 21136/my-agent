"""Controlled GitHub Actions operations via gh CLI."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_READ_ACTIONS = frozenset({"wait", "logs", "required_checks", "classify"})
_WRITE_ACTIONS = frozenset({"run", "rerun", "repair_decision", "repair_complete"})


def _agent_root() -> Path:
    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        marker = directory / "evolve"
        if (marker / "_index.core.toml").is_file() or (marker / "_index.toml").is_file():
            return directory
    raise RuntimeError("could not locate agent root")


def _load_paths():
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from paths import AgentPaths
    return AgentPaths


def _require_gh() -> str | None:
    if not shutil.which("gh"):
        return "gh CLI not found on PATH; install GitHub CLI and run gh auth login"
    return None


def _resolve_cwd(paths, raw: Any) -> Path:
    text = str(raw or "").strip()
    if not text:
        return paths.agent_root
    return paths.resolve_under_agent(text.replace("\\", "/"), must_exist=True)


def _run(args: list[str], *, cwd: Path, timeout_sec: int) -> tuple[int, str, str]:
    completed = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(1, timeout_sec),
        check=False,
    )
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _number(payload: dict[str, Any], key: str) -> tuple[int | None, str | None]:
    value = payload.get(key)
    if not isinstance(value, int) or value < 1:
        return None, f"{key} must be a positive int"
    return value, None




def parse_check_lines(text: str) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("\u001b"):
            continue
        parts = [part.strip() for part in line.split("\t") if part.strip()]
        if len(parts) < 2:
            parts = [part.strip() for part in __import__("re").split(r"\s{2,}", line) if part.strip()]
        if len(parts) < 2:
            continue
        name = parts[0]
        state = parts[1].casefold()
        if state in {"pass", "passed", "success", "successful", "complete", "completed"}:
            bucket = "passed"
        elif state in {"pending", "queued", "in_progress", "running", "expected"}:
            bucket = "pending"
        elif state in {"fail", "failed", "failure", "error", "cancelled", "canceled", "timed_out"}:
            bucket = "failed"
        else:
            bucket = "unknown"
        checks.append({"name": name, "state": state, "bucket": bucket, "raw": line})
    return checks


def summarize_checks(checks: list[dict[str, str]]) -> dict[str, int | str]:
    counts = {"passed": 0, "pending": 0, "failed": 0, "unknown": 0}
    for check in checks:
        bucket = check.get("bucket", "unknown")
        counts[bucket if bucket in counts else "unknown"] += 1
    if not checks or counts["unknown"]:
        status = "unknown"
    elif counts["failed"]:
        status = "failed"
    elif counts["pending"]:
        status = "pending"
    else:
        status = "passed"
    return {"status": status, "total": len(checks), **counts}



def classify_ci_failure(log_text: str) -> dict[str, object]:
    text = (log_text or "").casefold()
    if any(token in text for token in ("permission denied", "unauthorized", "forbidden", "http 401", "http 403", "token", "secret")):
        return {"category": "auth", "retryable": False, "reason": "credential or permission failure"}
    if any(token in text for token in ("rate limit", "service unavailable", "http 502", "http 503", "connection reset", "network error", "timed out", "timeout")):
        return {"category": "external", "retryable": False, "reason": "external service or network failure"}
    if any(token in text for token in ("npm install", "pip install", "module not found", "could not resolve", "lockfile", "dependency")):
        return {"category": "dependency", "retryable": True, "reason": "dependency or lockfile failure"}
    if any(token in text for token in ("pytest", "assertionerror", "test failed", "lint", "typescript", "tsc", "build failed", "compilation failed")):
        return {"category": "test", "retryable": True, "reason": "code, test, lint, or build failure"}
    return {"category": "unknown", "retryable": False, "reason": "failure could not be safely classified"}


def repair_decision(log_text: str, *, attempt: int, max_attempts: int = 3) -> dict[str, object]:
    classification = classify_ci_failure(log_text)
    attempt_value = max(0, int(attempt))
    budget = max(0, int(max_attempts))
    can_retry = bool(classification["retryable"]) and attempt_value < budget
    return {
        **classification,
        "attempt": attempt_value,
        "max_attempts": budget,
        "action": "repair_and_rerun" if can_retry else "blocked",
    }

def _load_ci_repair():
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from ci_repair import CIRepairState, CIRepairStatus, decide_repair, load_ci_repair, save_ci_repair
    return CIRepairState, CIRepairStatus, decide_repair, load_ci_repair, save_ci_repair


def run_gh_actions(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip().lower()
    if action not in _READ_ACTIONS | _WRITE_ACTIONS:
        return {"ok": False, "error": "action must be run|wait|logs|rerun|required_checks|classify|repair_decision|repair_complete"}
    paths = _load_paths().discover(start=_agent_root())
    if action == "repair_complete":
        repair_id = str(payload.get("repair_id") or "").strip()
        if not repair_id or payload.get("tests_ok") is not True:
            return {"ok": False, "error": "repair_complete requires repair_id and tests_ok=true"}
        _CIRepairState, _CIRepairStatus, _decide_repair, load_ci_repair, save_ci_repair = _load_ci_repair()
        try:
            state = load_ci_repair(paths, repair_id)
            next_state = state.complete_after_verification(tests_ok=True)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        save_ci_repair(paths, next_state)
        return {"ok": True, "action": action, "state": next_state.to_dict()}
    if action == "repair_decision":
        repair_id = str(payload.get("repair_id") or "").strip()
        repository = str(payload.get("repository") or "").strip()
        head_sha = str(payload.get("head_sha") or "").strip()
        log_text = payload.get("log")
        if not repair_id or not repository or not head_sha or not isinstance(log_text, str) or not log_text.strip():
            return {"ok": False, "error": "repair_decision requires repair_id, repository, head_sha, and log"}
        CIRepairState, CIRepairStatus, decide_repair, load_ci_repair, save_ci_repair = _load_ci_repair()
        try:
            state = load_ci_repair(paths, repair_id)
        except Exception:
            state = CIRepairState(
                repair_id=repair_id,
                repository=repository,
                pr_number=int(payload.get("pr_number", 0) or 0),
                head_sha=head_sha,
                attempt=0,
                max_attempts=max(0, int(payload.get("max_attempts", 3) or 3)),
                category="",
                status=CIRepairStatus.CREATED,
                log_digest="",
            )
        next_state = decide_repair(state, log_text)
        save_ci_repair(paths, next_state)
        return {"ok": True, "action": action, "state": next_state.to_dict()}
    missing = _require_gh()
    if missing:
        return {"ok": False, "error": missing}
    try:
        cwd = _resolve_cwd(paths, payload.get("working_dir", payload.get("cwd")))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    dry_run = bool(payload.get("dry_run", False))
    timeout_sec = int(payload.get("timeout_sec", 900) or 900)

    if action == "classify":
        log_text = payload.get("log")
        if not isinstance(log_text, str) or not log_text.strip():
            return {"ok": False, "error": "classify requires non-empty log"}
        return {"ok": True, "action": action, "decision": repair_decision(
            log_text,
            attempt=int(payload.get("attempt", 0) or 0),
            max_attempts=int(payload.get("max_attempts", 3) or 3),
        )}

    if action == "run":
        workflow = str(payload.get("workflow") or "").strip()
        if not workflow:
            return {"ok": False, "error": "workflow is required"}
        args = ["gh", "workflow", "run", workflow]
        ref = str(payload.get("ref") or "").strip()
        if ref:
            args += ["--ref", ref]
    elif action in {"wait", "logs", "rerun"}:
        run_id, error = _number(payload, "run_id")
        if error:
            return {"ok": False, "error": error}
        assert run_id is not None
        if action == "wait":
            args = ["gh", "run", "watch", str(run_id), "--exit-status"]
        elif action == "logs":
            args = ["gh", "run", "view", str(run_id), "--log"]
        else:
            args = ["gh", "run", "rerun", str(run_id)]
            if bool(payload.get("failed_only", True)):
                args.append("--failed")
    else:
        number, error = _number(payload, "number")
        if error:
            return {"ok": False, "error": error}
        assert number is not None
        args = ["gh", "pr", "checks", str(number), "--required"]

    if dry_run:
        return {"ok": True, "dry_run": True, "action": action, "args": args}
    try:
        code, out, err = _run(args, cwd=cwd, timeout_sec=timeout_sec)
    except subprocess.TimeoutExpired:
        return {"ok": False, "action": action, "error": "GitHub Actions command timed out", "args": args}
    if code != 0:
        return {"ok": False, "action": action, "error": err.strip() or out.strip() or "gh actions command failed", "args": args}
    result: dict[str, Any] = {"ok": True, "action": action, "args": args, "output": out.strip(), "status": "completed"}
    if action == "required_checks":
        checks = parse_check_lines(out)
        result["checks"] = checks
        result["summary"] = summarize_checks(checks)
    return result


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_gh_actions)


if __name__ == "__main__":
    main()
