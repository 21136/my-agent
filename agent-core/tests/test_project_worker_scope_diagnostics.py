"""Tests for worker tool scope diagnostics."""
from __future__ import annotations

import json

import pytest

from project_worker_scope_diagnostics import (
    aggregate_recovered_scope_error_detail,
    batch_outbound_undeclared_read_correction_matches,
    batch_outbound_undeclared_write_correction_matches,
    build_recovered_scope_correction_detail,
    build_scope_error_detail,
    build_target_scope_tool_error,
    build_worker_tool_scope_error,
    executor_scope_payload,
    patch_read_prerequisite_error,
    permission_error_scope_payload,
    serialize_scope_error_detail,
    validate_scope_error_detail,
    validate_scope_recovery_entry,
    validate_scope_tool_error,
)

_PID = "project-epsilon"


def test_read_not_declared_shows_both_declared_lists():
    payload = build_worker_tool_scope_error(
        error_code="read_target_not_declared",
        operation="read_project_file",
        requested_path="NOTES.md",
        project_id=_PID,
        read_targets=("PROJECT.md",),
        write_targets=("NOTES.md",),
    )
    assert payload["requested_path"] == "NOTES.md"
    assert payload["declared_read_targets"] == ["PROJECT.md"]
    assert payload["declared_write_targets"] == ["NOTES.md"]
    assert payload["allowed_targets"] == ["PROJECT.md"]
    assert payload["match_result"] == "declared_for_write_only"
    assert validate_scope_tool_error(payload)


def test_write_not_declared_case_mismatch():
    payload = build_target_scope_tool_error(
        "write_target_not_declared", "write_project_file", "notes.md",
        ("NOTES.md",),
        project_id=_PID,
    )
    assert payload["match_result"] == "case_mismatch"
    assert payload["closest_declared_target"] == "NOTES.md"


def test_leading_dot_normalized_to_declared():
    payload = build_worker_tool_scope_error(
        error_code="read_target_not_declared",
        operation="read_project_file",
        requested_path="./PROJECT.md",
        project_id=_PID,
        read_targets=("PROJECT.md",),
        write_targets=(),
    )
    assert payload["match_result"] == "leading_dot_segment"
    assert payload["normalized_path"] == "PROJECT.md"


def test_absolute_path_redacted_without_leaking():
    payload = build_worker_tool_scope_error(
        error_code="read_target_not_declared",
        operation="read_project_file",
        requested_path=r"C:\Users\secret\PROJECT.md",
        project_id=_PID,
        read_targets=("PROJECT.md",),
        write_targets=(),
    )
    assert "requested_path" not in payload
    assert payload["path_redacted"] is True
    assert payload["match_result"] == "backslash_separator"
    assert r"C:\Users" not in json.dumps(payload)


def test_cross_project_path_detected():
    payload = build_worker_tool_scope_error(
        error_code="read_target_not_declared",
        operation="read_project_file",
        requested_path="workspace/project-delta/NOTES.md",
        project_id=_PID,
        read_targets=("PROJECT.md",),
        write_targets=(),
    )
    assert payload["match_result"] == "cross_project_path"
    assert "workspace" in payload["hint"]


def test_patch_requires_prior_read_when_never_read():
    payload = patch_read_prerequisite_error(
        operation="patch_project_file",
        path="NOTES.md",
        project_id=_PID,
        read_targets=("NOTES.md",),
        write_targets=("NOTES.md",),
        read_snapshots=set(),
    )
    assert payload["error"] == "patch_requires_prior_authorized_read"
    assert payload["match_result"] == "missing_prior_read_snapshot"


def test_patch_requires_read_target_when_not_declared():
    payload = patch_read_prerequisite_error(
        operation="patch_project_file",
        path="NOTES.md",
        project_id=_PID,
        read_targets=("PROJECT.md",),
        write_targets=("NOTES.md",),
        read_snapshots=set(),
    )
    assert payload["match_result"] == "read_not_declared_for_patch"


def test_executor_path_out_of_bounds_payload():
    payload = executor_scope_payload(
        operation="write_project_file",
        path="NOTES.md",
        project_id=_PID,
        read_targets=(),
        write_targets=("NOTES.md",),
        executor_error_code="path_out_of_bounds",
    )
    assert payload["error"] == "path_out_of_bounds"
    assert payload["match_result"] == "executor_path_out_of_bounds"


def test_permission_error_maps_binding_change():
    payload = permission_error_scope_payload(
        operation="write_project_file",
        path="NOTES.md",
        project_id=_PID,
        read_targets=(),
        write_targets=("NOTES.md",),
        permission_message="host project/session binding changed",
    )
    assert payload["error"] == "write_scope_denied"
    assert payload["match_result"] == "host_binding_changed"


def test_scope_error_detail_is_host_safe_subset():
    tool_error = build_worker_tool_scope_error(
        error_code="read_target_not_declared",
        operation="read_project_file",
        requested_path="secret.md",
        project_id=_PID,
        read_targets=("PROJECT.md",),
        write_targets=(),
    )
    detail = build_scope_error_detail(tool_error)
    assert detail["project_id"] == _PID
    assert detail["requested_path"] == "secret.md"
    assert validate_scope_error_detail(detail)


def test_recovered_scope_detail_aggregates_for_success():
    read_tool_error = build_worker_tool_scope_error(
        error_code="read_target_not_declared",
        operation="read_project_file",
        requested_path="secret.md",
        project_id=_PID,
        read_targets=("PROJECT.md",),
        write_targets=("NOTES.md",),
    )
    write_tool_error = build_worker_tool_scope_error(
        error_code="write_target_not_declared",
        operation="write_project_file",
        requested_path="draft.md",
        project_id=_PID,
        read_targets=("PROJECT.md",),
        write_targets=("NOTES.md",),
    )
    read_correction = build_recovered_scope_correction_detail(read_tool_error)
    write_correction = build_recovered_scope_correction_detail(write_tool_error)
    assert validate_scope_recovery_entry(read_correction)
    aggregated = aggregate_recovered_scope_error_detail([read_correction, write_correction])
    assert aggregated["recovered"] is True
    assert aggregated["requested_path"] == "secret.md"
    assert aggregated["recovered_corrections"][1]["operation"] == "write_project_file"
    assert validate_scope_error_detail(aggregated)
    assert serialize_scope_error_detail(type("Turn", (), {"scope_error_detail": aggregated})())[0]


def test_batch_matcher_accepts_in_loop_undeclared_write_scope_error():
    payload = build_worker_tool_scope_error(
        error_code="write_target_not_declared",
        operation="write_project_file",
        requested_path="draft.md",
        project_id=_PID,
        read_targets=("PROJECT.md",),
        write_targets=("NOTES.md",),
    )
    assert batch_outbound_undeclared_write_correction_matches(
        attempted_path="draft.md",
        tool_result=payload,
        project_id=_PID,
        read_targets=("PROJECT.md",),
        write_targets=("NOTES.md",),
    )


def test_batch_matcher_accepts_in_loop_undeclared_read_scope_error():
    payload = build_worker_tool_scope_error(
        error_code="read_target_not_declared",
        operation="read_project_file",
        requested_path="secret.md",
        project_id=_PID,
        read_targets=("PROJECT.md",),
        write_targets=("NOTES.md",),
    )
    assert batch_outbound_undeclared_read_correction_matches(
        attempted_path="secret.md",
        tool_result=payload,
        project_id=_PID,
        read_targets=("PROJECT.md",),
        write_targets=("NOTES.md",),
    )
    assert not batch_outbound_undeclared_read_correction_matches(
        attempted_path="PROJECT.md",
        tool_result=payload,
        project_id=_PID,
        read_targets=("PROJECT.md",),
        write_targets=("NOTES.md",),
    )


def test_build_rejects_unsafe_declared_targets():
    with pytest.raises(ValueError):
        build_worker_tool_scope_error(
            error_code="read_target_not_declared",
            operation="read_project_file",
            requested_path="x",
            project_id=_PID,
            read_targets=("../bad",),
            write_targets=(),
        )
