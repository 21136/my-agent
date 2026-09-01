from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_mode import next_open_task, project_mode_block_reason, task_dependency_blockers
from project_mode import create_project
from project_api import dispatch_project_message
from runaway_flow import (
    checkpoint_transition_allowed,
    normalize_checkpoint,
    task_fingerprint,
    transition_checkpoint,
)
from runaway_lease import acquire_runaway_lease, lease_path
from session import create_new
from tests.isolation_helpers import temporary_agent_paths
from tools.schema import tool_ok


class RunawayFlowTests(unittest.TestCase):
    def test_cross_process_lease_allows_one_owner_and_token_safe_release(self) -> None:
        with temporary_agent_paths() as paths:
            project_id = "runaway-lease-demo"
            create_project(paths, project_id)
            first = acquire_runaway_lease(paths, project_id, ttl_seconds=10)
            self.assertIsNotNone(first)
            self.assertIsNone(acquire_runaway_lease(paths, project_id, ttl_seconds=10))
            lease_file = lease_path(paths, project_id)
            lease_file.write_text(
                '{"project_id":"runaway-lease-demo","pid":1,"token":"new-owner","heartbeat_unix":9999999999}',
                encoding="utf-8",
            )
            assert first is not None
            self.assertFalse(first.release())
            self.assertTrue(lease_file.is_file())
            lease_file.unlink()

    def test_active_lease_can_refresh_its_heartbeat(self) -> None:
        with temporary_agent_paths() as paths:
            project_id = "runaway-lease-heartbeat"
            create_project(paths, project_id)
            lease = acquire_runaway_lease(paths, project_id, ttl_seconds=10)
            self.assertIsNotNone(lease)
            assert lease is not None
            before = json.loads(lease_path(paths, project_id).read_text(encoding="utf-8"))["heartbeat_unix"]
            self.assertTrue(lease.heartbeat())
            after = json.loads(lease_path(paths, project_id).read_text(encoding="utf-8"))["heartbeat_unix"]
            self.assertGreaterEqual(after, before)
            self.assertTrue(lease.release())

    def test_expired_dead_process_lease_can_be_taken_over(self) -> None:
        with temporary_agent_paths() as paths:
            project_id = "runaway-lease-reclaim"
            create_project(paths, project_id)
            lease_file = lease_path(paths, project_id)
            lease_file.parent.mkdir(parents=True, exist_ok=True)
            lease_file.write_text(
                '{"project_id":"runaway-lease-reclaim","pid":999999999,"token":"dead-owner","heartbeat_unix":0,"ttl_seconds":2}',
                encoding="utf-8",
            )
            lease = acquire_runaway_lease(paths, project_id, ttl_seconds=2)
            self.assertIsNotNone(lease)
            self.assertNotEqual(json.loads(lease_file.read_text(encoding="utf-8"))["token"], "dead-owner")
            assert lease is not None
            self.assertTrue(lease.release())

    def test_expired_live_process_lease_is_reclaimed_after_ttl(self) -> None:
        with temporary_agent_paths() as paths:
            project_id = "runaway-lease-ttl"
            create_project(paths, project_id)
            lease_file = lease_path(paths, project_id)
            lease_file.parent.mkdir(parents=True, exist_ok=True)
            lease_file.write_text(
                json.dumps(
                    {
                        "project_id": project_id,
                        "pid": os.getpid(),
                        "token": "stalled-owner",
                        "heartbeat_unix": 0,
                        "ttl_seconds": 2,
                    }
                ),
                encoding="utf-8",
            )
            lease = acquire_runaway_lease(paths, project_id, ttl_seconds=2)
            self.assertIsNotNone(lease)
            assert lease is not None
            self.assertTrue(lease.release())

    def test_legacy_checkpoints_are_normalized(self) -> None:
        self.assertEqual(normalize_checkpoint("implementation"), "implementing")
        self.assertEqual(normalize_checkpoint("verification"), "verifying")
        self.assertEqual(normalize_checkpoint("verification_passed"), "release_wait")

    def test_invalid_transition_is_rejected(self) -> None:
        self.assertTrue(checkpoint_transition_allowed("verifying", "repairing"))
        self.assertFalse(checkpoint_transition_allowed("implementing", "completed"))

        meta = type("Meta", (), {"project_runaway_checkpoint": "implementing"})()
        with self.assertRaises(ValueError):
            transition_checkpoint(meta, "completed")

    def test_task_selection_honors_dependencies(self) -> None:
        text = (
            "- [ ] T-002 second depends_on: T-001\n"
            "- [ ] T-001 first\n"
        )
        line, _body, task_id = next_open_task(text)
        self.assertEqual(line, 1)
        self.assertEqual(task_id, "T-001")
        self.assertEqual(task_dependency_blockers(text), {"T-002": ["T-001"]})

        completed = text.replace("- [ ] T-001", "- [x] T-001")
        _line, _body, task_id = next_open_task(completed)
        self.assertEqual(task_id, "T-002")
        self.assertEqual(task_dependency_blockers(completed), {})

    def test_runaway_cannot_write_code_while_preparing(self) -> None:
        reason = project_mode_block_reason(
            active_shell="project",
            project_root="workspace/demo",
            plan_status="draft",
            workflow_stage="requirements",
            runaway_enabled=True,
            tool_name="run_evolved",
            arguments={
                "tool_name": "write_text",
                "arguments": {"path": "workspace/demo/src/app.py"},
            },
        )
        self.assertIsNotNone(reason)

    def test_task_fingerprint_is_stable_and_task_specific(self) -> None:
        self.assertEqual(task_fingerprint("T-001", "Build API"), task_fingerprint("t-001", "Build API"))
        self.assertNotEqual(task_fingerprint("T-001", "Build API"), task_fingerprint("T-002", "Build API"))

    def test_toggle_pause_and_resume_preserves_error_context(self) -> None:
        with temporary_agent_paths() as paths:
            project_id = "runaway-resume-demo"
            create_project(paths, project_id)
            session = create_new(paths, conversation_id="_runaway_resume_")
            session.meta.active_shell = "project"
            session.meta.project_id = project_id
            session.meta.project_root = f"workspace/{project_id}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "implementation"
            session.meta.project_runaway_checkpoint = "implementing"
            session.meta.project_runaway_last_error = "保留这个错误"
            session.meta.project_runaway_task_fingerprint = "task-fingerprint"

            dispatch_project_message(
                session,
                paths,
                {"type": "project.runaway.set", "enabled": False},
            )
            self.assertEqual(session.meta.project_runaway_checkpoint, "paused")
            self.assertEqual(session.meta.project_runaway_last_error, "保留这个错误")

            dispatch_project_message(
                session,
                paths,
                {"type": "project.runaway.set", "enabled": True},
            )
            self.assertEqual(session.meta.project_runaway_checkpoint, "implementing")
            self.assertEqual(session.meta.project_runaway_task_fingerprint, "task-fingerprint")
            self.assertEqual(session.meta.project_runaway_last_error, "保留这个错误")

    def test_documentation_pause_can_resume_without_plan_confirmation(self) -> None:
        with temporary_agent_paths() as paths:
            project_id = "runaway-documentation-resume"
            create_project(paths, project_id)
            session = create_new(paths, conversation_id="_runaway_documentation_resume_")
            session.meta.active_shell = "project"
            session.meta.project_id = project_id
            session.meta.project_root = f"workspace/{project_id}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "documentation"
            session.meta.project_runaway_checkpoint = "paused"
            session.meta.project_runaway_paused_reason = "文档尚未达到设计确认标准"

            response = dispatch_project_message(
                session,
                paths,
                {"type": "project.runaway.set", "enabled": True, "resume": True},
            )

            self.assertEqual(session.meta.project_runaway_checkpoint, "preparing")
            self.assertEqual(session.meta.project_runaway_paused_reason, "")
            notice = next(item for item in response["_events"] if item.get("type") == "notice")
            self.assertIn("狂奔模式已恢复", notice["text"])

    def test_harness_verification_persists_real_acceptance_evidence(self) -> None:
        from runaway_verification import load_verification_evidence, run_harness_verification

        with temporary_agent_paths() as paths:
            project_id = "runaway-evidence-demo"
            root = create_project(paths, project_id)
            (root / "PROJECT.md").write_text(
                "# demo\n\n## 验收标准\n\n- 命令：`python acceptance.py`\n",
                encoding="utf-8",
            )
            (root / "acceptance.py").write_text("print('ok')\n", encoding="utf-8")
            with patch(
                "runaway_verification.run_acceptance_check",
                return_value={
                    "passed": True,
                    "exit_code": 0,
                    "expected_exit_code": 0,
                    "stdout": "ok",
                    "stderr": "",
                },
            ):
                record = run_harness_verification(paths, project_id)

            self.assertTrue(record["passed"])
            self.assertTrue(record["evidence_persisted"])
            self.assertEqual(record["exit_code"], 0)
            self.assertIn("AC-", " ".join(record["ac_ids"]) or "AC-001")
            loaded = load_verification_evidence(paths, project_id)
            self.assertIsNotNone(loaded)

    def test_review_pass_cannot_release_without_hard_evidence(self) -> None:
        from agent import Agent

        with temporary_agent_paths() as paths:
            project_id = "runaway-hard-gate-demo"
            create_project(paths, project_id)
            session = create_new(paths, conversation_id="_runaway_hard_gate_")
            session.meta.active_shell = "project"
            session.meta.project_id = project_id
            session.meta.project_root = f"workspace/{project_id}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "verification"
            session.meta.project_runaway_checkpoint = "verifying"
            agent = Agent.create(session)
            failed = tool_ok(
                "deliverable_review",
                {"verdict": "pass", "blockers_count": 0, "summary": "审查通过"},
            )
            with patch(
                "runaway_verification.run_harness_verification",
                return_value={
                    "project_id": project_id,
                    "passed": False,
                    "evidence_persisted": True,
                    "evidence_fingerprint": "hard-failure",
                    "error": "验收命令失败",
                },
            ):
                self.assertTrue(agent._record_runaway_review_result(failed))
            self.assertEqual(session.meta.project_runaway_checkpoint, "repairing")
            self.assertNotEqual(session.meta.project_runaway_checkpoint, "release_wait")


if __name__ == "__main__":
    unittest.main()
