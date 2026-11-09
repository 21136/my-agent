from __future__ import annotations

import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from active_work_multi_project import (
    build_companion_orchestration_instruction,
    is_companion_multi_project_orchestration_request,
)


def test_detects_simultaneous_multi_project_user_phrasing():
    text = "同时写一个能随机出4个数和随机出5个数的两个项目"
    assert is_companion_multi_project_orchestration_request(text)


def test_supplement_to_current_work_is_not_multi_project_orchestration():
    assert not is_companion_multi_project_orchestration_request("补充当前工作：请再添加一个边界测试")


def test_orchestration_instruction_mandates_workers_not_session_switch():
    built = build_companion_orchestration_instruction("同时写项目 A 和 B")
    assert "run_project_workers" in built
    assert "不要" in built and "project.open" in built
    assert "同时写项目 A 和 B" in built
