from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class VerificationLevel(StrEnum):
    E0 = "E0"
    E1 = "E1"
    E2 = "E2"
    E3 = "E3"
    E4 = "E4"
    E5 = "E5"
    E6 = "E6"


class ScenarioStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NOT_RUN = "not_run"


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    scenario_id: str
    status: ScenarioStatus
    level: VerificationLevel
    evidence: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "status": self.status.value,
            "level": self.level.value,
            "evidence": list(self.evidence),
            "reason": self.reason,
        }


@dataclass
class SelfHostingReport:
    run_id: str
    model_id: str
    skill_identity: str
    runtime_version: str
    scenarios: list[ScenarioResult] = field(default_factory=list)

    def add(self, result: ScenarioResult) -> None:
        if any(item.scenario_id == result.scenario_id for item in self.scenarios):
            raise ValueError(f"duplicate scenario: {result.scenario_id}")
        self.scenarios.append(result)

    @property
    def disposition(self) -> str:
        if not self.scenarios:
            return "not_run"
        if any(item.status == ScenarioStatus.FAILED for item in self.scenarios):
            return "failed"
        if any(item.status == ScenarioStatus.BLOCKED for item in self.scenarios):
            return "blocked"
        if any(item.status == ScenarioStatus.NOT_RUN for item in self.scenarios):
            return "partial"
        if all(item.status == ScenarioStatus.PASSED for item in self.scenarios):
            return "verified"
        return "partial"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "model_id": self.model_id,
            "skill_identity": self.skill_identity,
            "runtime_version": self.runtime_version,
            "disposition": self.disposition,
            "scenarios": [item.to_dict() for item in self.scenarios],
        }

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path
