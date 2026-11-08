import logging

from request_timing import is_valid_user_request_id, log_main_request_timing


def test_main_request_timing_logs_only_allowlisted_phase_and_opaque_correlation(caplog):
    request_id = "3c45ee8a-4c19-438a-a287-87dab817ae31"
    with caplog.at_level(logging.INFO, logger="my_agent.sidecar"):
        log_main_request_timing("user_message_persisted", request_id, 1250)

    assert "phase=user_message_persisted" in caplog.text
    assert "duration_ms=1250" in caplog.text
    assert request_id not in caplog.text
    assert "text=" not in caplog.text


def test_main_request_timing_suppresses_missing_or_invalid_request_ids(caplog):
    with caplog.at_level(logging.INFO, logger="my_agent.sidecar"):
        log_main_request_timing("turn_lock_acquired", None, 0)
        log_main_request_timing("turn_lock_acquired", "not-a-uuid", 12)

    assert caplog.text == ""


def test_main_request_timing_rejects_uncontrolled_phase_and_negative_duration(caplog):
    with caplog.at_level(logging.INFO, logger="my_agent.sidecar"):
        log_main_request_timing("prompt=secret", "3c45ee8a-4c19-438a-a287-87dab817ae31", -5)

    assert caplog.text == ""


def test_timing_eligibility_is_limited_to_canonical_user_message_ids():
    request_id = "3c45ee8a-4c19-438a-a287-87dab817ae31"
    assert is_valid_user_request_id("user.message", request_id)
    assert not is_valid_user_request_id("command", request_id)
    assert not is_valid_user_request_id("user.message", "not-a-uuid")
    assert not is_valid_user_request_id("user.message", None)
