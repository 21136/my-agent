from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping


class CIRepairError(ValueError):
    pass


class CIRepairStatus(StrEnum):
    CREATED = "created"
    REPAIR_ALLOWED = "repair_allowed"
    RERUN_PENDING = "rerun_pending"
    READY_TO_DELIVER = "ready_to_deliver"
    PASSED = "passed"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class CIRepairState:
    repair_id: str
    repository: str
    pr_number: int
    head_sha: str
    attempt: int
    max_attempts: int
    category: str
    status: CIRepairStatus
    log_digest: str
    failure_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "repair_id": self.repair_id,
            "repository": self.repository,
            "pr_number": self.pr_number,
            "head_sha": self.head_sha,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "category": self.category,
            "status": self.status.value,
            "log_digest": self.log_digest,
            "failure_reason": self.failure_reason,
        }

    def complete_after_verification(self, *, tests_ok: bool) -> "CIRepairState":
        if self.status != CIRepairStatus.REPAIR_ALLOWED:
            raise ValueError(f"repair completion requires repair_allowed, got {self.status.value}")
        if not tests_ok:
            raise ValueError("tests_ok must be true before delivery")
        return replace(self, status=CIRepairStatus.READY_TO_DELIVER)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CIRepairState":
        return cls(
            repair_id=str(payload["repair_id"]),
            repository=str(payload["repository"]),
            pr_number=int(payload["pr_number"]),
            head_sha=str(payload["head_sha"]),
            attempt=int(payload["attempt"]),
            max_attempts=int(payload["max_attempts"]),
            category=str(payload["category"]),
            status=CIRepairStatus(str(payload["status"])),
            log_digest=str(payload["log_digest"]),
            failure_reason=str(payload.get("failure_reason", "")),
        )


def digest_log(log: str) -> str:
    return hashlib.sha256((log or "").encode("utf-8", errors="replace")).hexdigest()


def classify_ci_failure(log: str) -> dict[str, Any]:
    text = (log or "").casefold()
    if any(token in text for token in ("permission denied", "unauthorized", "forbidden", "http 401", "http 403", "token", "secret")):
        return {"category": "auth", "retryable": False, "reason": "credential or permission failure"}
    if any(token in text for token in ("rate limit", "service unavailable", "http 502", "http 503", "connection reset", "network error", "timed out", "timeout")):
        return {"category": "external", "retryable": False, "reason": "external service or network failure"}
    if any(token in text for token in ("npm install", "pip install", "module not found", "could not resolve", "lockfile", "dependency")):
        return {"category": "dependency", "retryable": True, "reason": "dependency or lockfile failure"}
    if any(token in text for token in ("pytest", "assertionerror", "test failed", "lint", "typescript", "tsc", "build failed", "compilation failed")):
        return {"category": "test", "retryable": True, "reason": "code, test, lint, or build failure"}
    return {"category": "unknown", "retryable": False, "reason": "failure could not be safely classified"}


def decide_repair(state: CIRepairState, log: str) -> CIRepairState:
    classification = classify_ci_failure(log)
    attempt = state.attempt + 1
    if not classification["retryable"] or attempt > state.max_attempts:
        return replace(
            state,
            attempt=attempt,
            category=str(classification["category"]),
            status=CIRepairStatus.BLOCKED,
            log_digest=digest_log(log),
            failure_reason=str(classification["reason"]),
        )
    return replace(
        state,
        attempt=attempt,
        category=str(classification["category"]),
        status=CIRepairStatus.REPAIR_ALLOWED,
        log_digest=digest_log(log),
        failure_reason=str(classification["reason"]),
    )


def save_ci_repair(paths: Any, state: CIRepairState) -> Path:
    directory = Path(paths.data) / "ci_repairs"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{state.repair_id}.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination


def load_ci_repair(paths: Any, repair_id: str) -> CIRepairState:
    safe_id = str(repair_id).strip()
    if not safe_id or "/" in safe_id or "\\" in safe_id:
        raise CIRepairError("invalid repair_id")
    path = Path(paths.data) / "ci_repairs" / f"{safe_id}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CIRepairError(f"repair state not found: {safe_id}") from exc
    return CIRepairState.from_dict(payload)
