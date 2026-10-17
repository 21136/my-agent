# -*- coding: utf-8 -*-
"""Ordinary-mode M2: light project templates + gate downgrades."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from progress_gate import make_evidence_entry, report_progress_evidence_block_reason
from project_cli import parse_project_command, run_project_command
from project_manifest import (
    LIGHT_SCAFFOLD_ARTIFACTS,
    STANDARD_ARTIFACTS,
    load_manifest,
    manifest_blocks_on_l2_stale,
    manifest_has_l2_stale,
    propagate_stale,
)
from project_mode import (
    TaskStats,
    build_project_goal,
    compute_execution_stage,
    create_project,
    existing_plan_files,
    format_project_overlay,
    project_dir,
    project_mode_block_reason,
    read_project_template,
    upgrade_project_to_standard,
)
from session import create_new
from tests.isolation_helpers import temporary_agent_paths

_STANDARD_ONLY = (
    "SCOPE.md",
    "DESIGN.md",
    "TECH-DESIGN.md",
    "RELEASE.md",
    "MAP.md",
)


class LightTemplateParseTests(unittest.TestCase):
    def test_parse_light_flag_variants(self) -> None:
        for text in (
            "项目 新建 demo-light --light",
            "项目 新建 demo-light light",
            "项目 新建 demo-light --template light",
            "新项目 demo-light --light",
        ):
            with self.subTest(text=text):
                command = parse_project_command(text)
                self.assertIsNotNone(command)
                assert command is not None
                self.assertEqual(command.kind, "new")
                self.assertEqual(command.project_id, "demo-light")
                self.assertEqual(command.project_template, "light")

    def test_parse_standard_create_stays_default(self) -> None:
        command = parse_project_command("项目 新建 demo-std")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.kind, "new")
        self.assertEqual(command.project_template, "standard")

    def test_parse_upgrade_verbs(self) -> None:
        for text in ("项目 升档", "项目 标准模板", "project upgrade"):
            with self.subTest(text=text):
                command = parse_project_command(text)
                self.assertIsNotNone(command)
                assert command is not None
                self.assertEqual(command.kind, "upgrade")


class LightScaffoldTests(unittest.TestCase):
    def test_light_create_has_minimal_files(self) -> None:
        with temporary_agent_paths() as paths:
            root = create_project(paths, "lite-demo", project_template="light")
            for name in LIGHT_SCAFFOLD_ARTIFACTS:
                self.assertTrue((root / name).is_file(), name)
                self.assertTrue((root / name).read_text(encoding="utf-8").strip())
            for name in _STANDARD_ONLY:
                self.assertFalse((root / name).is_file(), name)
            manifest = load_manifest(root / ".plan-agent" / "manifest.json")
            self.assertIsNotNone(manifest)
            assert manifest is not None
            self.assertEqual(manifest["project"]["template"], "light")
            self.assertEqual(read_project_template(paths, "lite-demo"), "light")

    def test_standard_create_still_has_seven_files(self) -> None:
        with temporary_agent_paths() as paths:
            root = create_project(paths, "std-demo")
            for name in STANDARD_ARTIFACTS:
                self.assertTrue((root / name).is_file(), name)
            self.assertTrue((root / "MAP.md").is_file())
            manifest = load_manifest(root / ".plan-agent" / "manifest.json")
            self.assertIsNotNone(manifest)
            assert manifest is not None
            self.assertEqual(manifest["project"]["template"], "standard")

    def test_cli_light_create_persists_flag(self) -> None:
        with temporary_agent_paths() as paths:
            session = create_new(paths, conversation_id="_lite_cli_")
            outputs: list[str] = []
            command = parse_project_command("项目 新建 cli-lite --light")
            assert command is not None
            run_project_command(session, paths, command, output_fn=outputs.append)
            root = project_dir(paths, "cli-lite")
            self.assertTrue((root / "PROJECT.md").is_file())
            self.assertTrue((root / "TASKS.md").is_file())
            self.assertTrue((root / "VERIFY.md").is_file())
            self.assertFalse((root / "DESIGN.md").is_file())
            self.assertFalse((root / "MAP.md").is_file())
            self.assertEqual(read_project_template(paths, "cli-lite"), "light")
            self.assertTrue(any("轻量模板" in line for line in outputs))


class LightGateTests(unittest.TestCase):
    def test_light_write_not_blocked_by_missing_design_or_l2(self) -> None:
        with temporary_agent_paths() as paths:
            root = create_project(paths, "lite-write", project_template="light")
            manifest = load_manifest(root / ".plan-agent" / "manifest.json")
            assert manifest is not None
            propagate_stale(manifest, "SCOPE.md", level="L2")
            from project_manifest import manifest_path, save_manifest

            save_manifest(manifest_path(root), manifest)
            self.assertTrue(manifest_has_l2_stale(manifest))
            self.assertFalse(manifest_blocks_on_l2_stale(manifest))

            stage = compute_execution_stage(
                project_id="lite-write",
                plan_status="confirmed",
                task_stats=TaskStats(done=0, total=1),
                manifest=manifest,
                project_root=root,
                workflow_stage="implementation",
            )
            self.assertNotEqual(stage["reason"], "l2_stale")
            self.assertNotEqual(stage["reason"], "design_incomplete")
            self.assertNotEqual(stage["reason"], "scope_incomplete")
            self.assertNotIn("DESIGN.md", stage["blockers"])
            self.assertNotIn("MAP.md", stage["blockers"])

            reason = project_mode_block_reason(
                active_shell="project",
                project_root="workspace/lite-write",
                plan_status="confirmed",
                workflow_stage="implementation",
                tool_name="run_evolved",
                arguments={
                    "tool_name": "write_text",
                    "arguments": {"path": "workspace/lite-write/src/app.py"},
                },
                agent_paths=paths,
            )
            self.assertIsNone(reason)

    def test_standard_write_still_blocked_by_l2_stale(self) -> None:
        with temporary_agent_paths() as paths:
            root = create_project(paths, "std-write")
            manifest = load_manifest(root / ".plan-agent" / "manifest.json")
            assert manifest is not None
            propagate_stale(manifest, "SCOPE.md", level="L2")
            from project_manifest import manifest_path, save_manifest

            save_manifest(manifest_path(root), manifest)
            reason = project_mode_block_reason(
                active_shell="project",
                project_root="workspace/std-write",
                plan_status="confirmed",
                workflow_stage="implementation",
                tool_name="run_evolved",
                arguments={
                    "tool_name": "write_text",
                    "arguments": {"path": "workspace/std-write/src/app.py"},
                },
                agent_paths=paths,
            )
            self.assertIsNone(reason)

    def test_light_progress_gate_needs_verify_not_design(self) -> None:
        evidence = [
            make_evidence_entry(
                tool_name="run_evolved",
                evolved_name="run_project_tests",
                ok=True,
                task_id="T-001",
                verify_ids=["V-001"],
            )
        ]
        allowed = report_progress_evidence_block_reason(
            active_shell="project",
            armed_task_text="T-001 完成第一项可交付工作",
            turn_evidence=evidence,
            task_id="T-001",
            expected_ac_ids=[],
            expected_verify_ids=["V-001"],
            require_binding=True,
            require_ac_binding=False,
        )
        self.assertIsNone(allowed)

        blocked_missing_verify = report_progress_evidence_block_reason(
            active_shell="project",
            armed_task_text="T-001 完成第一项可交付工作",
            turn_evidence=evidence,
            task_id="T-001",
            expected_ac_ids=[],
            expected_verify_ids=[],
            require_binding=True,
            require_ac_binding=False,
        )
        self.assertIsNotNone(blocked_missing_verify)
        assert blocked_missing_verify is not None
        self.assertIn("VERIFY", blocked_missing_verify)

        still_needs_ac_on_standard = report_progress_evidence_block_reason(
            active_shell="project",
            armed_task_text="T-001 完成第一项可交付工作",
            turn_evidence=evidence,
            task_id="T-001",
            expected_ac_ids=[],
            expected_verify_ids=["V-001"],
            require_binding=True,
            require_ac_binding=True,
        )
        self.assertIsNotNone(still_needs_ac_on_standard)
        assert still_needs_ac_on_standard is not None
        self.assertIn("AC/V", still_needs_ac_on_standard)


class LightUpgradeTests(unittest.TestCase):
    def test_upgrade_backfills_and_flips_flag(self) -> None:
        with temporary_agent_paths() as paths:
            root = create_project(paths, "lite-up", project_template="light")
            (root / "PROJECT.md").write_text("# lite-up · kept progress\n", encoding="utf-8")
            result = upgrade_project_to_standard(paths, "lite-up")
            self.assertFalse(result["already_standard"])
            self.assertEqual(result["template"], "standard")
            self.assertIn("DESIGN.md", result["created"])
            self.assertIn("SCOPE.md", result["created"])
            self.assertEqual((root / "PROJECT.md").read_text(encoding="utf-8"), "# lite-up · kept progress\n")
            for name in STANDARD_ARTIFACTS:
                self.assertTrue((root / name).is_file(), name)
            self.assertEqual(read_project_template(paths, "lite-up"), "standard")

            again = upgrade_project_to_standard(paths, "lite-up")
            self.assertTrue(again["already_standard"])
            self.assertEqual(again["created"], [])
            self.assertEqual((root / "PROJECT.md").read_text(encoding="utf-8"), "# lite-up · kept progress\n")

    def test_cli_upgrade_is_idempotent(self) -> None:
        with temporary_agent_paths() as paths:
            session = create_new(paths, conversation_id="_lite_up_cli_")
            run_project_command(
                session,
                paths,
                parse_project_command("项目 新建 cli-up light"),
                output_fn=lambda _line: None,
            )
            outputs: list[str] = []
            run_project_command(
                session,
                paths,
                parse_project_command("项目 升档"),
                output_fn=outputs.append,
            )
            self.assertEqual(read_project_template(paths, "cli-up"), "standard")
            self.assertTrue(any("标准模板" in line for line in outputs))
            second: list[str] = []
            run_project_command(
                session,
                paths,
                parse_project_command("项目 标准模板"),
                output_fn=second.append,
            )
            self.assertTrue(any("无需升档" in line for line in second))


class LightPlanPartnerContextTests(unittest.TestCase):
    def test_plan_partner_prompt_omits_files_the_light_template_skips(self) -> None:
        from plan_agent import _build_plan_prompt, plan_files_constraint
        from project_mode import read_project_artifacts

        with temporary_agent_paths() as paths:
            root = create_project(paths, "lite-plan", project_template="light")
            artifacts = read_project_artifacts(paths, "lite-plan")
            present_constraint = plan_files_constraint(
                [name for name in ("PROJECT.md", "TASKS.md", "VERIFY.md", "ENV.md") if name in artifacts]
            )
            for name in ("PROJECT.md", "TASKS.md", "VERIFY.md", "ENV.md"):
                self.assertIn(name, present_constraint)
            for name in ("SCOPE.md", "DESIGN.md", "TECH-DESIGN.md", "RELEASE.md", "MAP.md", "TASKS.archive.md"):
                self.assertNotIn(name, present_constraint)
            prompt = _build_plan_prompt(
                artifacts.get("TASKS.md", ""),
                "这个项目有哪些计划文件？",
                map_text=artifacts.get("MAP.md", ""),
                project_text=artifacts.get("PROJECT.md", ""),
                env_text=artifacts.get("ENV.md", ""),
                artifact_texts=artifacts,
                archive_path=root / "TASKS.archive.md",
            )
            self.assertIn("磁盘上存在的计划文件只有：PROJECT.md、TASKS.md、VERIFY.md、ENV.md。", prompt)
            for name in (
                "SCOPE.md",
                "DESIGN.md",
                "TECH-DESIGN.md",
                "RELEASE.md",
                "MAP.md",
                "TASKS.archive.md",
            ):
                self.assertNotIn(f"### {name}", prompt)
                self.assertNotIn(f"## {name}", prompt)
                self.assertNotIn(f"`{name}`", prompt)
                self.assertNotIn(name, present_constraint)


class LightPlanFilePromptTests(unittest.TestCase):
    _SKIPPED = ("MAP.md", "SCOPE.md", "DESIGN.md", "TECH-DESIGN.md", "RELEASE.md")

    def test_light_overlay_and_goal_omit_files_the_template_skips(self) -> None:
        with temporary_agent_paths() as paths:
            create_project(paths, "lite-prompt", project_template="light")
            files = existing_plan_files(paths, "lite-prompt")
            for name in ("PROJECT.md", "TASKS.md", "VERIFY.md", "ENV.md"):
                self.assertIn(name, files)
            for name in self._SKIPPED:
                self.assertNotIn(name, files)
            overlay = format_project_overlay(
                project_root="workspace/lite-prompt",
                project_id="lite-prompt",
                plan_status="draft",
                plan_files=files,
            )
            goal = build_project_goal(
                project_root="workspace/lite-prompt",
                plan_status="draft",
                plan_files=files,
            )
            self.assertIn("plan_files: PROJECT.md, TASKS.md, VERIFY.md, ENV.md", overlay)
            for name in self._SKIPPED:
                self.assertNotIn(name, overlay)
                self.assertNotIn(name, goal)
            self.assertNotIn("地图：", goal)

    def test_standard_overlay_mentions_map_when_the_file_exists(self) -> None:
        with temporary_agent_paths() as paths:
            create_project(paths, "std-prompt")
            files = existing_plan_files(paths, "std-prompt")
            self.assertIn("MAP.md", files)
            for name in ("SCOPE.md", "DESIGN.md", "TECH-DESIGN.md", "RELEASE.md"):
                self.assertIn(name, files)
            overlay = format_project_overlay(
                project_root="workspace/std-prompt",
                project_id="std-prompt",
                plan_status="draft",
                plan_files=files,
            )
            goal = build_project_goal(
                project_root="workspace/std-prompt",
                plan_status="draft",
                plan_files=files,
            )
            self.assertIn("MAP.md", overlay)
            self.assertIn("地图：workspace/std-prompt/MAP.md", goal)

    def test_static_prompts_do_not_require_map_for_every_project(self) -> None:
        root = Path(__file__).resolve().parents[2]
        core = (root / "agent-core" / "prompts" / "core.txt").read_text(encoding="utf-8")
        project_section = core.split("## Project mode", 1)[1].split("## Memory", 1)[0]
        self.assertNotIn("MAP.md", project_section)
        self.assertIn("plan_files:", project_section)
        boundaries = (root / "evolve" / "prompts" / "project-boundaries.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("MAP.md", boundaries)
        self.assertIn("plan_files", boundaries)
        coding = (root / "evolve" / "prompts" / "coding.md").read_text(encoding="utf-8")
        user_project = coding.split("### A. 用户项目", 1)[1].split("### B.", 1)[0]
        self.assertNotIn("`PROJECT.md` / `MAP.md`", user_project)
        self.assertIn("docs/MAP.md", user_project)
        self.assertIn("plan_files", user_project)
        self.assertIn("未列出", user_project)

    def test_ordinary_scope_does_not_forbid_workspace_reads(self) -> None:
        root = Path(__file__).resolve().parents[2]
        core = (root / "agent-core" / "prompts" / "core.txt").read_text(encoding="utf-8")
        boundaries = (root / "evolve" / "prompts" / "project-boundaries.md").read_text(
            encoding="utf-8"
        )
        solo = (root / "evolve" / "prompts" / "project-delivery-solo.md").read_text(
            encoding="utf-8"
        )
        coding = (root / "evolve" / "prompts" / "coding.md").read_text(encoding="utf-8")
        project_section = core.split("## Project mode", 1)[1].split("## Memory", 1)[0]
        ordinary_text = "\n".join((project_section, boundaries, solo, coding))
        self.assertNotIn("不能读取", ordinary_text)
        self.assertNotIn("do not read or create it", project_section)
        self.assertNotIn("未列出的文件不存在", ordinary_text)
        self.assertNotIn("只读 `plan_files` 列出的计划文档；未列出的不要读", boundaries)
        self.assertIn("may be read or inspected", project_section)
        self.assertIn("do not refuse the read or the edit", project_section)
        self.assertIn("其它文件随时可以读", boundaries)
        self.assertIn("未列出的计划文档不要读、不要创建", boundaries)
        self.assertIn("狂奔模式覆盖", boundaries)
        self.assertIn("直接 `read_file`", solo)
        self.assertNotIn("看看/查一下", solo)

        with temporary_agent_paths() as paths:
            create_project(paths, "lite-read", project_template="light")
            files = existing_plan_files(paths, "lite-read")
            overlay = format_project_overlay(
                project_root="workspace/lite-read",
                project_id="lite-read",
                plan_status="draft",
                plan_files=files,
                runaway_enabled=False,
            )
            self.assertIn("随时可以读", overlay)
            self.assertIn("直接 read_file", overlay)
            self.assertIn("不要以计划范围为由拒绝", overlay)
            self.assertNotIn("不能读取", overlay)
            self.assertNotIn("未列出的文件不存在", overlay)
            for name in self._SKIPPED:
                self.assertNotIn(name, overlay)

            runaway = format_project_overlay(
                project_root="workspace/lite-read",
                project_id="lite-read",
                plan_status="confirmed",
                workflow_stage="requirements",
                plan_files=files,
                runaway_enabled=True,
            )
            self.assertIn("runaway_scope: 仅当前项目内安全写入", runaway)
            self.assertIn("禁止写业务代码", runaway)
            self.assertNotIn("ordinary_scope:", runaway)
            self.assertIn("随时可以读", runaway)


if __name__ == "__main__":
    unittest.main()
