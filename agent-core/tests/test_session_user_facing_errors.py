from session import SessionError, user_facing_session_error_message


def test_user_facing_session_error_hides_internal_reload_copy():
    message = user_facing_session_error_message(
        "session messages changed or contain damaged records; reload before appending"
    )
    assert "reload" not in message.casefold()
    assert "session messages" not in message.casefold()
    assert "会话" in message


def test_user_facing_session_error_preserves_chinese_validation():
    message = user_facing_session_error_message("invalid session identity")
    assert "无效" in message