from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping


class CompatibilityError(ValueError):
    pass


class CompatibilityStatus(StrEnum):
    UNSUPPORTED = "unsupported"
    UNTESTED = "untested"
    PARTIAL = "partial"
    LOCAL_VERIFIED = "local_verified"
    DELIVERY_VERIFIED = "delivery_verified"
    SELF_HOSTING_VERIFIED = "self_hosting_verified"
    STABLE = "stable"


@dataclass(frozen=True, slots=True)
class CompatibilityRecord:
    model_id: str
    skill_identity: str
    runtime_version: str
    status: CompatibilityStatus
    passed_scenarios: tuple[str, ...] = ()
    failed_scenarios: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "skill_identity": self.skill_identity,
            "runtime_version": self.runtime_version,
            "status": self.status.value,
            "passed_scenarios": list(self.passed_scenarios),
            "failed_scenarios": list(self.failed_scenarios),
            "evidence": list(self.evidence),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CompatibilityRecord":
        return cls(
            model_id=str(payload["model_id"]),
            skill_identity=str(payload["skill_identity"]),
            runtime_version=str(payload["runtime_version"]),
            status=CompatibilityStatus(str(payload["status"])),
            passed_scenarios=tuple(str(item) for item in payload.get("passed_scenarios", [])),
            failed_scenarios=tuple(str(item) for item in payload.get("failed_scenarios", [])),
            evidence=tuple(str(item) for item in payload.get("evidence", [])),
            reason=str(payload.get("reason", "")),
        )

    def promote(self, target: CompatibilityStatus) -> "CompatibilityRecord":
        target = CompatibilityStatus(target)
        if target == CompatibilityStatus.STABLE:
            if self.failed_scenarios or not self.passed_scenarios:
                raise CompatibilityError("stable requires passed scenarios and no failed scenarios")
            if self.status not in {CompatibilityStatus.SELF_HOSTING_VERIFIED, CompatibilityStatus.STABLE}:
                raise CompatibilityError("stable requires self_hosting_verified first")
        if target == CompatibilityStatus.SELF_HOSTING_VERIFIED:
            if self.failed_scenarios or not self.passed_scenarios:
                raise CompatibilityError("self_hosting_verified requires clean scenario evidence")
        return replace(self, status=target)


@dataclass
class CompatibilityMatrix:
    records: list[CompatibilityRecord] = field(default_factory=list)

    def upsert(self, record: CompatibilityRecord) -> None:
        key = (record.model_id, record.skill_identity, record.runtime_version)
        self.records = [
            item for item in self.records
            if (item.model_id, item.skill_identity, item.runtime_version) != key
        ]
        self.records.append(record)

    def get(self, model_id: str, skill_identity: str, runtime_version: str) -> CompatibilityRecord | None:
        for record in self.records:
            if (record.model_id, record.skill_identity, record.runtime_version) == (model_id, skill_identity, runtime_version):
                return record
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"records": [record.to_dict() for record in self.records]}

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "CompatibilityMatrix":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([CompatibilityRecord.from_dict(item) for item in payload.get("records", [])])
