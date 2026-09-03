"""T-5812 / IT-5812: seven-file templates and one-time legacy migration."""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_api import build_plan_request_payload, dispatch_project_message, project_state_payload
from project_manifest import STANDARD_ARTIFACTS, build_manifest, load_manifest, save_manifest
from project_mode import create_project, migrate_legacy_project, project_dir
from session import create_new
from tools.schema import tool_ok
from tests.isolation_helpers import temporary_agent_paths


class ProjectArtifactTests(unittest.TestCase):
    def test_scope_confirm_is_routed_and_persisted(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "scope-confirm-demo"
            create_project(paths, pid)
            session = create_new(paths, conversation_id="_scope_confirm_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"

            response = dispatch_project_message(
                session,
                paths,
                {"type": "project.scope.confirm"},
            )

            self.assertEqual(response["type"], "project.state")
            self.assertTrue(session.meta.project_scope_confirmed_at)
            self.assertEqual(
                response["scope_confirmed_at"],
                session.meta.project_scope_confirmed_at,
            )

    def test_runaway_toggle_is_project_scoped_and_persisted(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "runaway-demo"
            create_project(paths, pid)
            session = create_new(paths, conversation_id="_runaway_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"

            enabled = dispatch_project_message(
                session,
                paths,
                {"type": "project.runaway.set", "enabled": True},
            )

            self.assertTrue(session.meta.project_runaway_enabled)
            enabled_state = next(item for item in enabled["_events"] if item.get("type") == "project.state")
            self.assertTrue(enabled_state["runaway_enabled"])

            reloaded = type(session).load(paths, session.conversation_id)
            self.assertTrue(reloaded.meta.project_runaway_enabled)

            disabled = dispatch_project_message(
                reloaded,
                paths,
                {"type": "project.runaway.set", "enabled": False},
            )
            self.assertFalse(reloaded.meta.project_runaway_enabled)
            disabled_state = next(item for item in disabled["_events"] if item.get("type") == "project.state")
            self.assertFalse(disabled_state["runaway_enabled"])

    def test_runaway_checkpoint_round_trips_and_state_exposes_pause(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "runaway-checkpoint-demo"
            create_project(paths, pid)
            session = create_new(paths, conversation_id="_runaway_checkpoint_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_runaway_checkpoint = "paused"
            session.meta.project_runaway_task_done_baseline = 4
            session.meta.project_runaway_tool_rounds = 12
            session.meta.project_runaway_repair_count = 3
            session.meta.project_runaway_last_verification = "fail"
            session.meta.project_runaway_review_blockers_count = 2
            session.meta.project_runaway_paused_reason = "自动修复次数已用尽"
            session.save()

            reloaded = type(session).load(paths, session.conversation_id)
            self.assertEqual(reloaded.meta.project_runaway_checkpoint, "paused")
            self.assertEqual(reloaded.meta.project_runaway_task_done_baseline, 4)
            self.assertEqual(reloaded.meta.project_runaway_tool_rounds, 12)
            self.assertEqual(reloaded.meta.project_runaway_repair_count, 3)
            payload = project_state_payload(reloaded, paths)
            self.assertEqual(payload["runaway_status"], "已暂停：自动修复次数已用尽")
            self.assertEqual(payload["runaway_last_verification"], "fail")
            self.assertEqual(payload["runaway_repair_count"], 3)

    def test_review_fail_delegates_repair_to_hard_verify(self) -> None:
        from agent import Agent

        with temporary_agent_paths() as paths:
            pid = "runaway-review-hard-gate"
            create_project(paths, pid)
            session = create_new(paths, conversation_id="_runaway_review_hard_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "verification"
            session.meta.project_runaway_checkpoint = "verifying"
            agent = Agent.create(session)
            failed = tool_ok(
                "deliverable_review",
                {"verdict": "fail", "blockers_count": 1, "summary": "测试命令失败"},
            )
            with patch.object(agent, "_sync_runaway_harness_truth", return_value=False):
                with patch(
                    "runaway_verification.run_harness_verification",
                    return_value={
                        "project_id": pid,
                        "passed": False,
                        "evidence_persisted": True,
                        "evidence_fingerprint": "hard-failure",
                        "error": "验收命令失败",
                    },
                ):
                    self.assertTrue(agent._record_runaway_review_result(failed))
            self.assertEqual(session.meta.project_runaway_checkpoint, "repairing")
            self.assertEqual(session.meta.project_runaway_repair_count, 1)

    def test_runaway_does_not_emit_plan_confirmation_request(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "runaway-plan-gate-demo"
            create_project(paths, pid)
            session = create_new(paths, conversation_id="_runaway_plan_gate_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_plan_status = "draft"
            self.assertIsNone(build_plan_request_payload(session, paths))

    def test_runaway_allows_project_write_after_internal_plan_adoption(self) -> None:
        from project_mode import project_mode_block_reason

        reason = project_mode_block_reason(
            active_shell="project",
            project_root="workspace/runaway-plan-gate-demo",
            plan_status="draft",
            workflow_stage="implementation",
            runaway_enabled=True,
            tool_name="run_evolved",
            arguments={
                "tool_name": "write_text",
                "arguments": {"path": "workspace/runaway-plan-gate-demo/src/app.py"},
            },
        )
        self.assertIsNone(reason)

    def test_runaway_natural_stop_is_internal_continuation(self) -> None:
        from agent import Agent
        from project_mode import project_dir

        with temporary_agent_paths() as paths:
            pid = "runaway-continuation-demo"
            create_project(paths, pid)
            root = project_dir(paths, pid)
            (root / "VERIFY.md").write_text(
                "# runaway-continuation-demo · 验证矩阵\n\n（尚无证据）\n",
                encoding="utf-8",
            )
            session = create_new(paths, conversation_id="_runaway_continuation_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_plan_status = "confirmed"
            session.meta.project_workflow_stage = "implementation"
            session.meta.project_runaway_checkpoint = "implementing"
            session.meta.project_active_task_id = "T-001"
            agent = Agent.create(session)
            self.assertTrue(
                agent._continue_runaway_after_natural_stop(
                    final_text="我先总结一下。",
                    finish_reason="stop",
                )
            )
            self.assertIn("不要反复验证已完成任务", session.messages[-1]["content"])

    def test_runaway_duplicate_continue_key_does_not_loop_segments(self) -> None:
        from agent import Agent

        with temporary_agent_paths() as paths:
            pid = "runaway-continuation-dedupe"
            create_project(paths, pid)
            session = create_new(paths, conversation_id="_runaway_continue_dedupe_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_plan_status = "confirmed"
            session.meta.project_workflow_stage = "implementation"
            session.meta.project_runaway_checkpoint = "implementing"
            session.meta.project_active_task_id = "T-008"
            agent = Agent.create(session)
            self.assertTrue(
                agent._continue_runaway_after_natural_stop(
                    final_text="",
                    finish_reason="stop",
                )
            )
            self.assertFalse(
                agent._continue_runaway_after_natural_stop(
                    final_text="",
                    finish_reason="stop",
                )
            )
        from agent import Agent

        with temporary_agent_paths() as paths:
            pid = "runaway-confirm-demo"
            root = create_project(paths, pid)
            session = create_new(paths, conversation_id="_runaway_confirm_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_plan_status = "confirmed"
            session.meta.project_workflow_stage = "implementation"
            session.meta.project_runaway_enabled = True
            session.save()
            executor = Agent.create(session).executor
            builtin = executor.registry.get_builtin("run_evolved")
            run_command = SimpleNamespace(name="run_command", scope="project")
            write_text = SimpleNamespace(name="write_text", scope="project")
            self.assertIsNotNone(builtin)
            assert builtin is not None

            local_install = {"tool_name": "run_command", "arguments": {
                "command": "python -m pip install -r requirements.txt",
                "working_dir": f"workspace/{pid}",
            }}
            self.assertTrue(executor._needs_confirm(builtin, run_command, local_install, tool_name="run_evolved"))
            self.assertTrue(executor._runaway_confirm_is_covered(builtin, run_command, local_install, tool_name="run_evolved"))

            sensitive = {"tool_name": "write_text", "arguments": {
                "path": f"workspace/{pid}/.env",
                "content": "SECRET=x",
            }}
            self.assertFalse(executor._runaway_confirm_is_covered(builtin, write_text, sensitive, tool_name="run_evolved"))

            external = {"tool_name": "run_command", "arguments": {
                "command": "git push origin main",
                "working_dir": f"workspace/{pid}",
            }}
            self.assertFalse(executor._runaway_confirm_is_covered(builtin, run_command, external, tool_name="run_evolved"))

            find_no_cwd = {"tool_name": "run_command", "arguments": {
                "command": "find . -name PROJECT.md",
            }}
            self.assertTrue(
                executor._runaway_confirm_is_covered(
                    builtin, run_command, find_no_cwd, tool_name="run_evolved"
                )
            )

    def test_runaway_auto_adopts_plan_partner_proposals(self) -> None:
        from agent import Agent

        with temporary_agent_paths() as paths:
            pid = "runaway-proposal-demo"
            create_project(paths, pid)
            session = create_new(paths, conversation_id="_runaway_proposal_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            agent = Agent.create(session)
            fake_plan = Mock()
            fake_plan.accept_suggestion.return_value = {"ok": True}
            with patch("plan_agent.get_plan_agent", return_value=fake_plan):
                with patch("project_api._ack_human_plan_adopt") as ack:
                    self.assertTrue(agent._adopt_runaway_proposals(["proposal-1"]))
            fake_plan.accept_suggestion.assert_called_once_with("proposal-1", code_policy="plan_only")
            ack.assert_called_once()

    def test_it5812_new_project_has_non_empty_standard_artifacts(self) -> None:
        with temporary_agent_paths() as paths:
            root = create_project(paths, "template-demo")
            for name in STANDARD_ARTIFACTS:
                with self.subTest(name=name):
                    self.assertTrue((root / name).is_file())
                    self.assertTrue((root / name).read_text(encoding="utf-8").strip())

            manifest = load_manifest(root / ".plan-agent" / "manifest.json")
            self.assertIsNotNone(manifest)
            assert manifest is not None
            self.assertEqual(
                {item["path"] for item in manifest["artifacts"] if item["path"] in STANDARD_ARTIFACTS},
                set(STANDARD_ARTIFACTS),
            )

    def test_it5812_migrates_legacy_project_content_once(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "legacy-demo"
            root = project_dir(paths, pid)
            root.mkdir(parents=True)
            (root / "PROJECT.md").write_text(
                "# Legacy\n\n## 目标\n保留旧目标\n\n## 验收标准\n- AC-007 old acceptance\n",
                encoding="utf-8",
            )
            (root / "TASKS.md").write_text("- [ ] T-009 old task\n", encoding="utf-8")
            (root / "MAP.md").write_text("# Legacy map\n入口：old.py\n", encoding="utf-8")
            (root / "ENV.md").write_text("# ENV\nquality: old command\n", encoding="utf-8")

            session = create_new(paths, conversation_id="_it5812_legacy")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            payload = project_state_payload(session, paths)
            manifest = load_manifest(root / ".plan-agent" / "manifest.json")
            self.assertIsNotNone(payload["manifest"])
            for name in STANDARD_ARTIFACTS:
                with self.subTest(name=name):
                    self.assertTrue((root / name).is_file())
                    self.assertTrue((root / name).read_text(encoding="utf-8").strip())
            self.assertIn("保留旧目标", (root / "SCOPE.md").read_text(encoding="utf-8"))
            self.assertIn("old.py", (root / "DESIGN.md").read_text(encoding="utf-8"))
            self.assertIn("old command", (root / "VERIFY.md").read_text(encoding="utf-8"))
            self.assertIn("req:", (root / "TASKS.md").read_text(encoding="utf-8"))
            self.assertEqual(manifest["manifest_revision"], "r0")
            self.assertEqual(
                {item["status"] for item in manifest["artifacts"]},
                {"current"},
            )
            before = {name: (root / name).read_text(encoding="utf-8") for name in STANDARD_ARTIFACTS}
            self.assertFalse(migrate_legacy_project(paths, pid))
            after = {name: (root / name).read_text(encoding="utf-8") for name in STANDARD_ARTIFACTS}
            self.assertEqual(after, before)

    def test_it5912_project_state_reloads_manifest_after_external_scope_edit(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "compound-state-demo"
            root = create_project(paths, pid)
            (root / "SCOPE.md").write_text("# scope\n", encoding="utf-8")
            save_manifest(root / ".plan-agent" / "manifest.json", build_manifest(root, pid))
            session = create_new(paths, conversation_id="_it5912_state_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_plan_status = "confirmed"

            blocked = project_state_payload(session, paths)
            self.assertIn("REQ", blocked["execution_stage_blockers"])
            self.assertIn("AC", blocked["execution_stage_blockers"])

            (root / "SCOPE.md").write_text(
                "# scope\n\nREQ-MUSIC-001\nAC-MUSIC-001\n",
                encoding="utf-8",
            )
            refreshed = project_state_payload(session, paths)
            self.assertNotIn("REQ", refreshed["execution_stage_blockers"])
            self.assertNotIn("AC", refreshed["execution_stage_blockers"])
            self.assertIn("SCOPE.md", refreshed["execution_stage_blockers"])
            scope = next(
                item for item in refreshed["manifest"]["artifacts"] if item["path"] == "SCOPE.md"
            )
            self.assertEqual(scope["ids"], ["AC-MUSIC-001", "REQ-MUSIC-001"])


if __name__ == "__main__":
    unittest.main()
