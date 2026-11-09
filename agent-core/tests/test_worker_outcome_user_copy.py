from worker_outcome_user_copy import format_worker_batch_failure_notice, format_worker_tool_row_failure_notice


def test_provider_failure_includes_message_and_scope_hint():
    row = {
        "project_id": "random-number-test",
        "status": "failed",
        "model_turn_status": "failed",
        "error_code": "failed",
        "model_error_code": "provider_request_failed",
        "model_error_message": "confirmed request transport failed; it was not retried",
    }
    assert "random-number-test" in format_worker_tool_row_failure_notice(row)
    assert "transport failed" in format_worker_tool_row_failure_notice(row)


def test_read_scope_detail_surfaces_hint():
    row = {
        "project_id": "random-number-4",
        "status": "failed",
        "model_turn_status": "failed",
        "error_code": "failed",
        "model_error_code": "project_read_failed",
        "scope_error_available": True,
        "scope_error_detail": {
            "operation": "read_project_file",
            "requested_path": "index.html",
            "error": "project_read_failed",
            "hint": "index.html 不在本 worker 的 read_targets 中，或文件尚不存在。",
        },
    }
    text = format_worker_tool_row_failure_notice(row)
    assert text is not None
    assert "index.html" in text


def test_batch_notice_joins_multiple_projects():
    rows = [
        {"project_id": "a", "status": "failed", "model_turn_status": "failed", "model_error_code": "provider_request_failed"},
        {"project_id": "b", "status": "failed", "model_turn_status": "failed", "model_error_code": "provider_request_failed"},
    ]
    notice = format_worker_batch_failure_notice(rows)
    assert notice is not None
    assert "项目 a" in notice and "项目 b" in notice
