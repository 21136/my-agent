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
    runaway_task_has_advance_evidence,
    revert_out_of_order_runaway_checkoffs,
    sanitize_runaway_tasks_artifact,
    sync_runaway_task_checkoff_from_verify,
    sync_all_runaway_task_checkoffs_from_verify,
    strip_nonformal_open_task_lines,
    task_fingerprint,
    transition_checkpoint,
)
from runaway_verification import ensure_project_acceptance_section
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

    def test_verification_stage_allows_run_command(self) -> None:
        reason = project_mode_block_reason(
            active_shell="project",
            project_root="workspace/demo",
            plan_status="confirmed",
            workflow_stage="verification",
            runaway_enabled=True,
            tool_name="run_evolved",
            arguments={
                "tool_name": "run_command",
                "arguments": {"command": "python verify.py", "working_dir": "workspace/demo"},
            },
        )
        self.assertIsNone(reason)

    def test_verification_stage_still_blocks_business_write(self) -> None:
        reason = project_mode_block_reason(
            active_shell="project",
            project_root="workspace/demo",
            plan_status="confirmed",
            workflow_stage="verification",
            runaway_enabled=True,
            tool_name="run_evolved",
            arguments={
                "tool_name": "write_text",
                "arguments": {"path": "workspace/demo/src/app.py", "content": "x"},
            },
        )
        self.assertIsNotNone(reason)
        self.assertIn("implementation", reason or "")

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

    def test_advance_runaway_moves_to_next_task_when_active_task_done(self) -> None:
        from agent import Agent
        from project_mode import project_dir

        with temporary_agent_paths() as paths:
            pid = "runaway-advance-next"
            create_project(paths, pid)
            tasks_path = project_dir(paths, pid) / "TASKS.md"
            tasks_path.write_text(
                "## Phase 1\n"
                "- [x] T-001 first task\n"
                "- [x] T-002 second task verify: V-001\n"
                "- [ ] T-003 third task\n",
                encoding="utf-8",
            )
            session = create_new(paths, conversation_id="_runaway_advance_next_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "implementation"
            session.meta.project_plan_status = "confirmed"
            session.meta.project_active_task_id = "T-002"
            session.meta.project_runaway_checkpoint = "implementing"
            session.meta.project_runaway_task_done_baseline = 1
            session.save()

            agent = Agent.create(session)
            agent.executor.begin_turn()
            self.assertFalse(agent.executor.session.task_stop_armed)
            self.assertTrue(agent._advance_runaway_checkpoint())
            self.assertEqual(session.meta.project_active_task_id, "T-003")
            self.assertEqual(session.meta.project_runaway_checkpoint, "implementing")

    def test_advance_runaway_enters_verifying_when_open_queue_empty(self) -> None:
        from agent import Agent
        from project_mode import project_dir

        with temporary_agent_paths() as paths:
            pid = "runaway-advance-verify"
            create_project(paths, pid)
            tasks_path = project_dir(paths, pid) / "TASKS.md"
            tasks_path.write_text("## Phase 1\n- [x] T-001 only task verify: V-001\n", encoding="utf-8")
            session = create_new(paths, conversation_id="_runaway_advance_verify_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "implementation"
            session.meta.project_plan_status = "confirmed"
            session.meta.project_active_task_id = "T-001"
            session.meta.project_runaway_checkpoint = "implementing"
            session.meta.project_runaway_task_done_baseline = 0
            session.save()

            agent = Agent.create(session)
            agent.executor.begin_turn()
            with patch.object(agent, "_run_runaway_hard_verification", return_value=True):
                self.assertTrue(agent._advance_runaway_checkpoint())
            self.assertEqual(session.meta.project_workflow_stage, "verification")
            self.assertEqual(session.meta.project_runaway_checkpoint, "verifying")
            self.assertEqual(session.meta.project_active_task_id, "")
            self.assertEqual(session.meta.project_runaway_checkpoint, "verifying")

    def test_advance_runaway_enters_verifying_when_queue_complete_without_active_task(
        self,
    ) -> None:
        """UI-5971: fresh session on finished project — no active T-* still → verifying."""
        from agent import Agent
        from project_mode import project_dir

        with temporary_agent_paths() as paths:
            pid = "runaway-queue-complete"
            create_project(paths, pid)
            tasks_path = project_dir(paths, pid) / "TASKS.md"
            tasks_path.write_text(
                "## Phase 1\n- [x] T-001 done\n- [x] T-002 done\n",
                encoding="utf-8",
            )
            (project_dir(paths, pid) / "VERIFY.md").write_text(
                "- V-001 T-001 pass\n",
                encoding="utf-8",
            )
            session = create_new(paths, conversation_id="_runaway_queue_complete_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "implementation"
            session.meta.project_plan_status = "confirmed"
            session.meta.project_active_task_id = ""
            session.meta.project_runaway_checkpoint = "implementing"
            session.meta.project_runaway_task_done_baseline = 0
            session.save()

            agent = Agent.create(session)
            agent.executor.begin_turn()
            with patch.object(agent, "_run_runaway_hard_verification", return_value=True):
                self.assertTrue(agent._advance_runaway_checkpoint())
            self.assertEqual(session.meta.project_workflow_stage, "verification")
            self.assertEqual(session.meta.project_runaway_checkpoint, "verifying")
            self.assertEqual(session.meta.project_active_task_id, "")

    def test_turn_start_syncs_harness_after_implementation_advance(self) -> None:
        """P2: queue-complete advance implementation→verification must still sync harness."""
        from agent import Agent
        from project_mode import project_dir

        with temporary_agent_paths() as paths:
            pid = "runaway-turn-start-sync"
            create_project(paths, pid)
            tasks_path = project_dir(paths, pid) / "TASKS.md"
            tasks_path.write_text(
                "## Phase 1\n- [x] T-001 done\n- [x] T-002 done\n",
                encoding="utf-8",
            )
            (project_dir(paths, pid) / "VERIFY.md").write_text(
                "- V-001 T-001 pass\n",
                encoding="utf-8",
            )
            session = create_new(paths, conversation_id="_runaway_turn_start_sync_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "implementation"
            session.meta.project_plan_status = "confirmed"
            session.meta.project_active_task_id = ""
            session.meta.project_runaway_checkpoint = "implementing"
            session.meta.project_runaway_task_done_baseline = 0
            session.save()

            agent = Agent.create(session)
            agent.executor.begin_turn()
            sync_calls = 0

            def _track_sync(**_kwargs: object) -> bool:
                nonlocal sync_calls
                sync_calls += 1
                return False

            agent._sync_runaway_harness_truth = _track_sync  # type: ignore[method-assign]
            with patch.object(agent, "_run_runaway_hard_verification", return_value=True):
                workflow_stage = getattr(session.meta, "project_workflow_stage", "")
                if workflow_stage in {"implementation", "verification"}:
                    agent._advance_runaway_checkpoint()
                if getattr(session.meta, "project_workflow_stage", "") == "verification":
                    agent._sync_runaway_harness_truth()
            self.assertEqual(session.meta.project_workflow_stage, "verification")
            self.assertEqual(sync_calls, 1)

    def test_next_open_task_skips_non_formal_plan_lines(self) -> None:
        text = (
            "## Phase 1\n"
            "- [ ] 自动采纳并落盘 plan 提案\n"
            "- [ ] T-001 real task\n"
        )
        line, _body, task_id = next_open_task(text)
        self.assertEqual(task_id, "T-001")
        self.assertEqual(line, 2)

        polluted = (
            "## Phase 1\n"
            "- [x] T-006 done task（V-015）\n"
            "- [ ] 自动采纳：勾选 T-003/T-004 并写 VERIFY\n"
            "- [ ] T-008 next real task\n"
        )
        line, _body, task_id = next_open_task(polluted)
        self.assertEqual(task_id, "T-008")
        self.assertEqual(line, 3)

    def test_advance_runaway_requires_evidence_before_next_task(self) -> None:
        from agent import Agent
        from project_mode import project_dir

        with temporary_agent_paths() as paths:
            pid = "runaway-advance-evidence"
            create_project(paths, pid)
            tasks_path = project_dir(paths, pid) / "TASKS.md"
            tasks_path.write_text(
                "## Phase 1\n"
                "- [x] T-001 first task\n"
                "- [x] T-002 second task\n"
                "- [ ] T-003 third task\n",
                encoding="utf-8",
            )
            session = create_new(paths, conversation_id="_runaway_advance_evidence_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "implementation"
            session.meta.project_plan_status = "confirmed"
            session.meta.project_active_task_id = "T-002"
            session.meta.project_runaway_checkpoint = "implementing"
            session.save()

            agent = Agent.create(session)
            agent.executor.begin_turn()
            self.assertFalse(agent._advance_runaway_checkpoint())
            self.assertEqual(session.meta.project_active_task_id, "T-002")

    def test_runaway_task_has_advance_evidence_accepts_verify_documentation(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "runaway-verify-evidence"
            create_project(paths, pid)
            from project_mode import project_dir, read_project_artifacts

            tasks_text = "- [x] T-002 second task verify: V-001\n"
            verify_text = read_project_artifacts(paths, pid).get("VERIFY.md", "")
            self.assertTrue(
                runaway_task_has_advance_evidence(
                    task_id="T-002",
                    tasks_text=tasks_text,
                    turn_evidence=[],
                    verify_text=verify_text,
                )
            )

    def test_runaway_task_has_advance_evidence_accepts_inline_verify_paren(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "runaway-inline-verify"
            create_project(paths, pid)
            from project_mode import read_project_artifacts

            tasks_text = "- [x] T-006 接入 Redis（V-015）\n"
            verify_text = read_project_artifacts(paths, pid).get("VERIFY.md", "")
            verify_text += "\n| V-015 | T-006 | redis check | exit 0 | partial | note |\n"
            self.assertTrue(
                runaway_task_has_advance_evidence(
                    task_id="T-006",
                    tasks_text=tasks_text,
                    turn_evidence=[],
                    verify_text=verify_text,
                )
            )

    def test_strip_nonformal_open_task_lines(self) -> None:
        text = (
            "## Phase 1\n"
            "- [ ] 自动采纳：勾选 T-003/T-004\n"
            "- [ ] T-008 next real task\n"
        )
        cleaned, stripped = strip_nonformal_open_task_lines(text)
        self.assertEqual(stripped, 1)
        self.assertIn("- [ ] T-008", cleaned)
        self.assertNotIn("自动采纳", cleaned)

    def test_revert_out_of_order_runaway_checkoffs(self) -> None:
        text = (
            "- [x] T-006 active task（V-015）\n"
            "- [x] T-007 jumped ahead\n"
            "- [ ] T-008 still open\n"
        )
        cleaned, reverted = revert_out_of_order_runaway_checkoffs(text, "T-006")
        self.assertEqual(reverted, ["T-007"])
        self.assertIn("- [x] T-006", cleaned)
        self.assertIn("- [ ] T-007", cleaned)

    def test_sanitize_runaway_tasks_artifact(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "runaway-sanitize"
            create_project(paths, pid)
            from project_mode import project_dir

            tasks_path = project_dir(paths, pid) / "TASKS.md"
            tasks_path.write_text(
                "- [x] T-006 done（V-015）\n"
                "- [x] T-007 jumped\n"
                "- [ ] plan noise mentions T-003\n"
                "- [ ] T-008 open\n",
                encoding="utf-8",
            )
            result = sanitize_runaway_tasks_artifact(paths, pid, "T-006")
            self.assertTrue(result["changed"])
            self.assertEqual(result["reverted"], ["T-007"])
            self.assertEqual(result["stripped"], 1)
            text = tasks_path.read_text(encoding="utf-8")
            self.assertIn("- [ ] T-007", text)
            self.assertNotIn("plan noise", text)

    def test_sync_runaway_task_checkoff_from_verify(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "runaway-verify-checkoff"
            create_project(paths, pid)
            from project_mode import project_dir

            tasks_path = project_dir(paths, pid) / "TASKS.md"
            verify_path = project_dir(paths, pid) / "VERIFY.md"
            tasks_path.write_text(
                "## Phase 2\n"
                "- [ ] T-011 singer apply flow（V-020）\n",
                encoding="utf-8",
            )
            verify_path.write_text(
                verify_path.read_text(encoding="utf-8")
                + "\n| V-020 | T-011 | apply flow | exit 0 | 通过 | mvn test ok |\n",
                encoding="utf-8",
            )
            self.assertTrue(
                sync_runaway_task_checkoff_from_verify(paths, pid, "T-011")
            )
            text = tasks_path.read_text(encoding="utf-8")
            self.assertRegex(text, r"-\s*\[x\]\s+T-011")

    def test_sync_all_runaway_task_checkoffs_from_verify(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "runaway-verify-batch"
            create_project(paths, pid)
            from project_mode import project_dir

            tasks_path = project_dir(paths, pid) / "TASKS.md"
            verify_path = project_dir(paths, pid) / "VERIFY.md"
            tasks_path.write_text(
                "## Phase 2\n"
                "- [ ] T-011 singer apply flow（V-020）\n"
                "- [ ] T-012 playback detail（V-021）\n",
                encoding="utf-8",
            )
            verify_path.write_text(
                verify_path.read_text(encoding="utf-8")
                + "\n| V-020 | T-011 | apply flow | exit 0 | 通过 | ok |\n"
                + "| V-021 | T-012 | playback | exit 0 | 通过 | ok |\n",
                encoding="utf-8",
            )
            synced = sync_all_runaway_task_checkoffs_from_verify(paths, pid)
            self.assertEqual(sorted(synced), ["T-011", "T-012"])
            text = tasks_path.read_text(encoding="utf-8")
            self.assertRegex(text, r"-\s*\[x\]\s+T-011")
            self.assertRegex(text, r"-\s*\[x\]\s+T-012")

    def test_advance_preserves_plan_partner_cap(self) -> None:
        from agent import Agent
        from project_mode import project_dir

        with temporary_agent_paths() as paths:
            pid = "runaway-plan-cap"
            create_project(paths, pid)
            tasks_path = project_dir(paths, pid) / "TASKS.md"
            verify_path = project_dir(paths, pid) / "VERIFY.md"
            tasks_path.write_text(
                "## Phase 1\n"
                "- [x] T-001 first task verify: V-001\n"
                "- [x] T-002 second task（V-002）\n"
                "- [ ] T-003 third task\n",
                encoding="utf-8",
            )
            verify_path.write_text(
                verify_path.read_text(encoding="utf-8")
                + "\n| V-002 | T-002 | second | exit 0 | 通过 | ok |\n",
                encoding="utf-8",
            )
            session = create_new(paths, conversation_id="_runaway_plan_cap_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "implementation"
            session.meta.project_plan_status = "confirmed"
            session.meta.project_active_task_id = "T-002"
            session.meta.project_runaway_checkpoint = "implementing"
            session.meta.project_runaway_task_done_baseline = 1
            session.save()

            agent = Agent.create(session)
            agent.executor.session.plan_partner_calls = 2
            agent.executor.begin_turn(reset_plan_cap=False)
            self.assertEqual(agent.executor.session.plan_partner_calls, 2)
            self.assertTrue(agent._advance_runaway_checkpoint())
            self.assertEqual(agent.executor.session.plan_partner_calls, 2)
            self.assertEqual(session.meta.project_active_task_id, "T-003")

    def test_ensure_project_acceptance_section(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "runaway-acceptance-append"
            create_project(paths, pid)
            from project_mode import parse_acceptance_spec, project_dir

            root = project_dir(paths, pid)
            script = root / ".acceptance" / "verify.py"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("print('ok')\n", encoding="utf-8")
            project_path = root / "PROJECT.md"
            project_path.write_text(
                "# Demo\n\n## AC-001 · baseline\n",
                encoding="utf-8",
            )
            self.assertIsNone(parse_acceptance_spec(project_path.read_text(encoding="utf-8")))
            self.assertTrue(ensure_project_acceptance_section(paths, pid))
            updated = project_path.read_text(encoding="utf-8")
            spec = parse_acceptance_spec(updated)
            self.assertIsNotNone(spec)
            assert spec is not None
            self.assertTrue(spec.script_rel.endswith(".acceptance/verify.py"))

    def _write_matrix_ready_project(self, paths, pid: str) -> None:
        from project_mode import project_dir

        root = create_project(paths, pid)
        (root / "PROJECT.md").write_text(
            "## 验收标准\n\n- 命令：`python verify.py` 期望退出码：0\n",
            encoding="utf-8",
        )
        (root / "TASKS.md").write_text("- [x] T-001 done\n", encoding="utf-8")
        (root / "VERIFY.md").write_text("- V-001 T-001 pass\n", encoding="utf-8")
        (root / "ENV.md").write_text(
            "quality:\n  commands:\n    - id: smoke\n      cmd: [\"echo\", \"ok\"]\n",
            encoding="utf-8",
        )

    def test_verifying_without_quality_commands_bootstraps_env(self) -> None:
        from agent import Agent

        with temporary_agent_paths() as paths:
            pid = "runaway-mx5-repair"
            self._write_matrix_ready_project(paths, pid)
            (paths.workspace / pid / "ENV.md").unlink()
            session = create_new(paths, conversation_id="_runaway_mx5_repair_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "verification"
            session.meta.project_runaway_checkpoint = "verifying"
            agent = Agent.create(session)
            with patch.object(agent, "_rerun_runaway_harness_verification", return_value=True):
                with patch.object(agent, "_run_runaway_repair_lane", return_value=True) as repair_lane:
                    self.assertTrue(agent._maybe_run_runaway_repair_lane_at_turn_start())
                    repair_lane.assert_not_called()
            self.assertEqual(session.meta.project_runaway_checkpoint, "release_wait")
            env_text = (paths.workspace / pid / "ENV.md").read_text(encoding="utf-8")
            self.assertIn("quality:", env_text)

    def test_stale_review_fail_promotes_to_verifying_when_harness_green(self) -> None:
        from agent import Agent

        with temporary_agent_paths() as paths:
            pid = "runaway-reconcile-review"
            self._write_matrix_ready_project(paths, pid)
            session = create_new(paths, conversation_id="_runaway_reconcile_review_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "verification"
            session.meta.project_runaway_checkpoint = "repairing"
            session.meta.project_runaway_repair_count = 2
            session.meta.project_runaway_last_error = "PROJECT.md 缺少 ## 验收标准"
            session.meta.project_runaway_last_verification = "fail"
            agent = Agent.create(session)
            failed = tool_ok(
                "deliverable_review",
                {
                    "verdict": "fail",
                    "blockers_count": 2,
                    "summary": "PROJECT.md 缺少 ## 验收标准",
                },
            )
            with patch.object(agent, "_rerun_runaway_harness_verification", return_value=True):
                self.assertFalse(agent._record_runaway_review_result(failed))
            self.assertEqual(session.meta.project_runaway_checkpoint, "release_wait")
            self.assertEqual(session.meta.project_runaway_repair_count, 2)
            self.assertEqual(session.meta.project_runaway_last_verification, "pass")
            self.assertEqual(session.meta.project_runaway_last_error, "")

    def test_turn_start_reconciles_repairing_without_bug_fix(self) -> None:
        from agent import Agent

        with temporary_agent_paths() as paths:
            pid = "runaway-reconcile-turn"
            self._write_matrix_ready_project(paths, pid)
            session = create_new(paths, conversation_id="_runaway_reconcile_turn_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "verification"
            session.meta.project_runaway_checkpoint = "repairing"
            session.meta.project_runaway_repair_count = 2
            session.meta.project_runaway_last_error = "陈旧阻塞"
            agent = Agent.create(session)
            with patch.object(agent, "_rerun_runaway_harness_verification", return_value=True):
                with patch.object(agent, "_run_runaway_repair_lane") as repair_lane:
                    self.assertTrue(agent._maybe_run_runaway_repair_lane_at_turn_start())
                    repair_lane.assert_not_called()
            self.assertEqual(session.meta.project_runaway_checkpoint, "release_wait")
            self.assertEqual(session.meta.project_runaway_repair_count, 2)

    def test_review_fail_advisory_does_not_increment_repair_count(self) -> None:
        from agent import Agent

        with temporary_agent_paths() as paths:
            pid = "runaway-review-advisory"
            self._write_matrix_ready_project(paths, pid)
            session = create_new(paths, conversation_id="_runaway_review_advisory_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "verification"
            session.meta.project_runaway_checkpoint = "verifying"
            agent = Agent.create(session)
            failed = tool_ok(
                "deliverable_review",
                {"verdict": "fail", "blockers_count": 2, "summary": "审查误报"},
            )
            with patch.object(agent, "_rerun_runaway_harness_verification", return_value=True):
                self.assertFalse(agent._record_runaway_review_result(failed))
            self.assertEqual(session.meta.project_runaway_checkpoint, "release_wait")
            self.assertEqual(session.meta.project_runaway_repair_count, 0)

    def test_should_chain_promotes_stale_verifying_to_release_wait(self) -> None:
        from agent import Agent

        with temporary_agent_paths() as paths:
            pid = "runaway-chain-verifying"
            self._write_matrix_ready_project(paths, pid)
            session = create_new(paths, conversation_id="_runaway_chain_verifying_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "verification"
            session.meta.project_runaway_checkpoint = "verifying"
            session.meta.project_runaway_acceptance_passed = True
            agent = Agent.create(session)
            with patch.object(agent, "_sync_runaway_harness_truth", return_value=True) as sync:
                self.assertFalse(agent.should_chain_runaway_after_turn("runaway_verification_harness"))
                sync.assert_called_once()
            with patch.object(agent, "_sync_runaway_harness_truth", return_value=False):
                self.assertTrue(agent.should_chain_runaway_after_turn("runaway_verification_harness"))

    def test_should_chain_runaway_after_turn_when_implementing(self) -> None:
        from agent import Agent

        with temporary_agent_paths() as paths:
            pid = "runaway-chain-turn"
            create_project(paths, pid)
            session = create_new(paths, conversation_id="_runaway_chain_turn_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "implementation"
            session.meta.project_runaway_checkpoint = "implementing"
            agent = Agent.create(session)
            self.assertTrue(agent.should_chain_runaway_after_turn("completed"))
            self.assertFalse(agent.should_chain_runaway_after_turn("cancelled"))
            session.meta.project_runaway_checkpoint = "release_wait"
            self.assertFalse(agent.should_chain_runaway_after_turn("completed"))

    def test_continue_runaway_stops_when_queue_done_and_harness_green(self) -> None:
        from agent import Agent

        with temporary_agent_paths() as paths:
            pid = "runaway-stop-segment"
            self._write_matrix_ready_project(paths, pid)
            session = create_new(paths, conversation_id="_runaway_stop_segment_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "verification"
            session.meta.project_runaway_checkpoint = "verifying"
            session.meta.project_runaway_acceptance_passed = True
            agent = Agent.create(session)
            self.assertFalse(
                agent._continue_runaway_after_natural_stop(
                    final_text="任务都完成了",
                    finish_reason="stop",
                )
            )


    def test_run_turn_skips_llm_at_release_wait(self) -> None:
        from unittest.mock import MagicMock

        from agent import Agent

        with temporary_agent_paths() as paths:
            pid = "runaway-exit-short"
            self._write_matrix_ready_project(paths, pid)
            session = create_new(paths, conversation_id="_runaway_exit_short_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "verification"
            session.meta.project_runaway_checkpoint = "release_wait"
            session.meta.project_runaway_acceptance_passed = True
            agent = Agent.create(session)
            agent.llm = MagicMock()
            result = agent.run_turn(
                "狂奔模式：正式任务已全部完成，Harness 硬验收已通过。",
                spawn_explore=False,
                force_skip_plan_spawn=True,
            )
            agent.llm.chat.assert_not_called()
            self.assertEqual(result.finish_reason, "runaway_verification_exit")
            self.assertEqual(result.tool_rounds, 0)
            self.assertIn("release_wait", result.assistant_text)

    def test_run_turn_allows_qa_at_release_wait(self) -> None:
        from unittest.mock import MagicMock

        from agent import Agent

        with temporary_agent_paths() as paths:
            pid = "runaway-exit-qa"
            self._write_matrix_ready_project(paths, pid)
            session = create_new(paths, conversation_id="_runaway_exit_qa_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "verification"
            session.meta.project_runaway_checkpoint = "release_wait"
            session.meta.project_runaway_acceptance_passed = True
            agent = Agent.create(session)
            agent.llm = MagicMock()
            agent.llm.chat.return_value = MagicMock(content="可以发布。", tool_calls=[])
            agent.run_turn("这个项目现在能发布了吗？", spawn_explore=False)
            agent.llm.chat.assert_called()


if __name__ == "__main__":
    unittest.main()
