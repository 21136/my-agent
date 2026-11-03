"""Safe user-facing reporting for current-dialogue worker preflight errors."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from worker_preflight_notice import (
    format_worker_preflight_notice, report_worker_preflight_error,
)


class WorkerPreflightNoticeTests(unittest.TestCase):
    def test_known_severe_host_errors_get_short_safe_chinese_notices(self):
        cases = {
            "worker_dispatch_submit_failed": "当前 worker 任务未能提交，暂时无法开始处理。",
            "worker_run_start_record_failed": "当前 worker 启动记录未能保存，暂时无法开始处理。",
            "project_binding_unavailable": "当前项目连接暂不可用，worker 未能启动。",
        }
        for error_code, expected_message in cases.items():
            with self.subTest(error_code=error_code):
                report = report_worker_preflight_error(error_code)
                self.assertEqual(report["category"], "system_error")
                self.assertEqual(report["message"], expected_message)
                self.assertTrue(report["severe"])
                self.assertNotIn(error_code, report["message"])
                self.assertNotIn("\\", report["message"])
                self.assertNotIn("/", report["message"])

    def test_scope_denied_is_authorization_not_system_failure(self):
        report = report_worker_preflight_error("scope_denied")
        self.assertEqual(report["category"], "authorization_denied")
        self.assertFalse(report["severe"])
        self.assertIn("授权", report["message"])
        self.assertNotIn("故障", report["message"])

    def test_unknown_code_does_not_claim_a_cause_or_echo_internal_value(self):
        report = report_worker_preflight_error("private-id-at-C:/internal/path")
        self.assertEqual(report["category"], "unknown")
        self.assertFalse(report["severe"])
        self.assertEqual(report["message"], "当前 worker 暂未能启动，具体原因尚未确认。")
        self.assertNotIn("private-id", report["message"])
        self.assertNotIn("C:/internal/path", report["message"])

    def test_notice_requires_a_safe_verified_display_name(self):
        self.assertEqual(
            format_worker_preflight_notice("worker_dispatch_submit_failed", "毕业设计"),
            "项目「毕业设计」：当前 worker 任务未能提交，暂时无法开始处理。",
        )
        self.assertIsNone(format_worker_preflight_notice("scope_denied", "毕业设计"))
        self.assertIsNone(format_worker_preflight_notice("future_unknown_error", "毕业设计"))
        fallback = "\u67d0\u4e2a\u9879\u76ee\uff08\u540d\u79f0\u6682\u4e0d\u53ef\u786e\u8ba4\uff09\uff1a"
        self.assertEqual(
            format_worker_preflight_notice("worker_dispatch_submit_failed", None),
            fallback + report_worker_preflight_error("worker_dispatch_submit_failed")["message"],
        )
        self.assertEqual(
            format_worker_preflight_notice("worker_dispatch_submit_failed", "C:/private/project"),
            fallback + report_worker_preflight_error("worker_dispatch_submit_failed")["message"],
        )

    def test_agent_dispatch_submit_failure_is_severe_and_project_bound(self):
        report = report_worker_preflight_error("agent_dispatch_pool_submit_failed")
        self.assertEqual(report["category"], "system_error")
        self.assertTrue(report["severe"])
        self.assertEqual(
            format_worker_preflight_notice("agent_dispatch_pool_submit_failed", "project-gamma"),
            "\u9879\u76ee\u300cproject-gamma\u300d\uff1a"
            "\u5f53\u524d worker \u8c03\u5ea6\u672a\u80fd\u63d0\u4ea4\uff0c"
            "\u6682\u65f6\u65e0\u6cd5\u5f00\u59cb\u5904\u7406\u3002",
        )

    def test_binding_unavailable_without_verified_name_uses_non_guessing_fallback(self):
        self.assertEqual(
            format_worker_preflight_notice("project_binding_unavailable", None),
            "某个项目（名称暂不可确认）：当前项目连接暂不可用，worker 未能启动。",
        )


if __name__ == "__main__":
    unittest.main()
