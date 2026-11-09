"""Companion home SHOW/HIDE notice filters."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from companion_home_surface import (
    CONFIRM_ALREADY_IN_FLIGHT_COMPANION,
    STALE_CONFIRM_INGRESS_COMPANION,
    STALE_CONFIRM_INGRESS_DEFAULT,
    companion_home_silences_chat_notice,
    confirm_in_flight_notice,
    polish_notice_for_session_chat,
    stale_confirm_ingress_notice,
)
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


class CompanionHomeSurfaceTest(unittest.TestCase):
    def _companion_session(self, paths, name: str):
        session = create_new(paths, conversation_id=name)
        session.meta.active_shell = "grow"
        session.meta.harness = "desktop"
        session.meta.turn_mode = "agent"
        session.save()
        return session

    def test_silences_orchestration_and_confirm_routing(self) -> None:
        samples = [
            "需要在当前小魔王空间确认后继续；请查看聊天中的确认卡。",
            "已在主界面创建项目 workspace/random-number-4（未换线）",
            "[Goal] 多项目 worker 批次未逐项验收。",
            "正在为 3 个项目准备 worker 派工；授权与写入确认只会在当前小魔王空间出现，请留意聊天确认卡。",
            "忽略过期确认（请点最新一张工具确认卡）",
            "另有一项授权确认正在进行；请先在输入区下方的确认条完成现有授权。",
        ]
        for text in samples:
            self.assertTrue(companion_home_silences_chat_notice(text), text)

    def test_companion_session_drops_hidden_lines(self) -> None:
        with temporary_agent_paths() as paths:
            session = self._companion_session(paths, "surface-companion")
            self.assertIsNone(
                polish_notice_for_session_chat(
                    session,
                    "需要在当前小魔王空间确认后继续；请查看聊天中的确认卡。",
                ),
            )
            self.assertEqual(
                polish_notice_for_session_chat(session, "磁盘空间不足，无法写入。"),
                "磁盘空间不足，无法写入。",
            )

    def test_stale_and_in_flight_copy_mentions_composer_on_companion(self) -> None:
        with temporary_agent_paths() as paths:
            session = self._companion_session(paths, "surface-copy")
            self.assertIn("确认条", stale_confirm_ingress_notice(session))
            self.assertIn("确认条", confirm_in_flight_notice(session))
            self.assertEqual(confirm_in_flight_notice(session), CONFIRM_ALREADY_IN_FLIGHT_COMPANION)
            self.assertEqual(stale_confirm_ingress_notice(session), STALE_CONFIRM_INGRESS_COMPANION)
        self.assertIn("工具确认卡", stale_confirm_ingress_notice(None))
        self.assertEqual(stale_confirm_ingress_notice(None), STALE_CONFIRM_INGRESS_DEFAULT)


if __name__ == "__main__":
    unittest.main()
