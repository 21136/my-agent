from __future__ import annotations

from compatibility_matrix import CompatibilityMatrix, CompatibilityRecord, CompatibilityStatus
from self_hosting_verify import SelfHostingReport, ScenarioStatus, VerificationLevel


def status_for_report(report: SelfHostingReport) -> CompatibilityStatus:
    scenarios = report.scenarios
    if not scenarios or any(item.status == ScenarioStatus.FAILED for item in scenarios):
        return CompatibilityStatus.PARTIAL if scenarios else CompatibilityStatus.UNTESTED
    if any(item.status in {ScenarioStatus.BLOCKED, ScenarioStatus.NOT_RUN} for item in scenarios):
        if any(item.status == ScenarioStatus.PASSED and item.level in {VerificationLevel.E2, VerificationLevel.E3} for item in scenarios):
            return CompatibilityStatus.LOCAL_VERIFIED
        return CompatibilityStatus.PARTIAL
    if all(item.status == ScenarioStatus.PASSED for item in scenarios):
        return CompatibilityStatus.SELF_HOSTING_VERIFIED
    return CompatibilityStatus.PARTIAL


def record_report(matrix: CompatibilityMatrix, report: SelfHostingReport) -> CompatibilityRecord:
    record = CompatibilityRecord(
        model_id=report.model_id,
        skill_identity=report.skill_identity,
        runtime_version=report.runtime_version,
        status=status_for_report(report),
        passed_scenarios=tuple(item.scenario_id for item in report.scenarios if item.status == ScenarioStatus.PASSED),
        failed_scenarios=tuple(item.scenario_id for item in report.scenarios if item.status == ScenarioStatus.FAILED),
        evidence=tuple(evidence for item in report.scenarios for evidence in item.evidence),
        reason=report.disposition,
    )
    matrix.upsert(record)
    return record
