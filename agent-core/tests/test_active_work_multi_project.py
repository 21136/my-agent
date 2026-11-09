from __future__ import annotations

import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from active_work_multi_project import (
    COMPANION_ORCHESTRATION_UI_SKIP_PREFIX,
    build_companion_orchestration_instruction,
    is_companion_multi_project_orchestration_request,
)
from session import build_session_chat_history


def test_detects_simultaneous_multi_project_user_phrasing():
    text = "同时写一个能随机出4个数和随机出5个数的两个项目"
    assert is_companion_multi_project_orchestration_request(text)


def test_supplement_to_current_work_is_not_multi_project_orchestration():
    assert not is_companion_multi_project_orchestration_request("补充当前工作：请再添加一个边界测试")


def test_orchestration_instruction_mandates_workers_not_session_switch():
    built = build_companion_orchestration_instruction("同时写项目 A 和 B")
    assert "run_project_workers" in built
    assert "不要" in built and "project.open" in built
    assert "workspace" in built
    assert "同时写项目 A 和 B" in built


def test_orchestration_model_preamble_is_hidden_from_chat_history(tmp_path):
    from tests.isolation_helpers import temporary_agent_paths
    from session import create_new

    user_text = "同时写一个能随机出4个数和随机出5个数的两个项目"
    with temporary_agent_paths() as paths:
        session = create_new(paths)
        session.append_message(
            {
                "role": "user",
                "content": build_companion_orchestration_instruction(user_text),
            }
        )
        assert not build_session_chat_history(session)

        session.messages.clear()
        session.append_message({"role": "user", "content": user_text})
        history = build_session_chat_history(session)
        assert len(history) == 1 and history[0]["text"] == user_text
        assert COMPANION_ORCHESTRATION_UI_SKIP_PREFIX not in history[0]["text"]
