"""Docs overhaul batch 1: ordinary plan writes, versioned save, trash delete."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_api import dispatch_doc_message
from project_mode import (
    ORDINARY_PROTECTED_WRITE_BLOCK_MSG,
    PLAN_DOMAIN_WRITE_BLOCK_MSG,
    create_project,
    count_untouched_template_docs,
    delete_project_doc,
    format_project_overlay,
    list_project_docs,
    main_agent_plan_domain_write_block,
    project_dir,
    project_mode_block_reason,
    project_root_rel,
    read_project_doc,
    restore_project_doc_from_trash,
    write_project_doc,
)
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


def _write_args(root: str, name: str) -> dict:
    return {
        "tool_name": "write_text",
        "arguments": {"path": f"{root}/{name}", "content": "# x\n"},
    }


class OrdinaryPlanWriteTests(unittest.TestCase):
    def test_ordinary_allows_standard_docs_blocks_map(self) -> None:
        root = "workspace/demo"
        for name in (
            "PROJECT.md",
            "SCOPE.md",
            "DESIGN.md",
            "TECH-DESIGN.md",
            "TASKS.md",
            "VERIFY.md",
            "RELEASE.md",
            "ENV.md",
        ):
            self.assertIsNone(
                main_agent_plan_domain_write_block(
                    project_root=root,
                    tool_name="run_evolved",
                    arguments=_write_args(root, name),
                    allow_ordinary_plan_writes=True,
                )
            )
        blocked = main_agent_plan_domain_write_block(
            project_root=root,
            tool_name="run_evolved",
            arguments=_write_args(root, "MAP.md"),
            allow_ordinary_plan_writes=True,
        )
        self.assertEqual(blocked, ORDINARY_PROTECTED_WRITE_BLOCK_MSG)

    def test_non_ordinary_still_blocks_project(self) -> None:
        root = "workspace/demo"
        blocked = main_agent_plan_domain_write_block(
            project_root=root,
            tool_name="run_evolved",
            arguments=_write_args(root, "PROJECT.md"),
            allow_ordinary_plan_writes=False,
        )
        self.assertEqual(blocked, PLAN_DOMAIN_WRITE_BLOCK_MSG)

    def test_skip_stage_walls_passes_allow_flag(self) -> None:
        reason = project_mode_block_reason(
            active_shell="project",
            project_root="workspace/demo",
            plan_status="confirmed",
            tool_name="run_evolved",
            arguments=_write_args("workspace/demo", "PROJECT.md"),
            skip_stage_walls=True,
            runaway_enabled=False,
        )
        self.assertIsNone(reason)
        reason_map = project_mode_block_reason(
            active_shell="project",
            project_root="workspace/demo",
            plan_status="confirmed",
            tool_name="run_evolved",
            arguments=_write_args("workspace/demo", "MAP.md"),
            skip_stage_walls=True,
            runaway_enabled=False,
        )
        self.assertIsNotNone(reason_map)
        self.assertIn("MAP", reason_map or "")

    def test_overlay_mentions_ordinary_plan_write_and_active_doc(self) -> None:
        overlay = format_project_overlay(
            project_root="workspace/demo",
            project_id="demo",
            plan_status="confirmed",
            skip_stage_walls=True,
            project_entry="direct",
            active_doc="PROJECT.md",
            active_doc_selection="目标一行",
        )
        self.assertIn("ordinary_plan_write", overlay)
        self.assertIn("active_doc: PROJECT.md", overlay)
        self.assertIn("目标一行", overlay)


class DocVersionAndTrashTests(unittest.TestCase):
    def test_write_conflict_and_force(self) -> None:
        with temporary_agent_paths(prefix="doc-ver-") as paths:
            pid = "ver-demo"
            root = project_dir(paths, pid)
            root.mkdir(parents=True)
            (root / "notes.md").write_text("# a\n", encoding="utf-8")
            read = read_project_doc(paths, pid, "notes.md")
            write_project_doc(paths, pid, "notes.md", "# b\n")
            with self.assertRaisesRegex(Exception, "已被修改") as ctx:
                write_project_doc(
                    paths,
                    pid,
                    "notes.md",
                    "# c\n",
                    base_hash=read["content_hash"],
                )
            exc = ctx.exception
            self.assertEqual(getattr(exc, "code", ""), "doc_conflict")
            forced = write_project_doc(
                paths,
                pid,
                "notes.md",
                "# c\n",
                base_hash=read["content_hash"],
                force=True,
            )
            self.assertEqual(forced["type"], "project.doc.write.done")
            self.assertEqual((root / "notes.md").read_text(encoding="utf-8"), "# c\n")

    def test_delete_moves_to_trash_and_restore(self) -> None:
        with temporary_agent_paths(prefix="doc-trash-") as paths:
            pid = "trash-demo"
            root = project_dir(paths, pid)
            root.mkdir(parents=True)
            (root / "gone.md").write_text("# bye\n", encoding="utf-8")
            deleted = delete_project_doc(paths, pid, "gone.md")
            self.assertFalse((root / "gone.md").exists())
            self.assertTrue((root / "_trash" / "gone.md").is_file())
            self.assertIn("undo", deleted)
            restored = restore_project_doc_from_trash(
                paths,
                pid,
                path=deleted["undo"]["path"],
                trash_path=deleted["undo"]["trash_path"],
                content=deleted["undo"]["content"],
            )
            self.assertEqual(restored["path"], "gone.md")
            self.assertEqual((root / "gone.md").read_text(encoding="utf-8"), "# bye\n")

    def test_list_hides_untouched_standard_templates(self) -> None:
        with temporary_agent_paths(prefix="doc-hide-") as paths:
            pid = "hide-demo"
            create_project(paths, pid, project_template="standard")
            listed = {d["path"] for d in list_project_docs(paths, pid)}
            # Untouched templates should be collapsed away
            self.assertNotIn("PROJECT.md", listed)
            self.assertNotIn("SCOPE.md", listed)
            count = count_untouched_template_docs(paths, pid)
            self.assertGreaterEqual(count, 5)
            # After editing, it appears
            root = project_dir(paths, pid)
            (root / "PROJECT.md").write_text("# hide-demo · 项目章程\n\n真内容\n", encoding="utf-8")
            listed2 = {d["path"] for d in list_project_docs(paths, pid)}
            self.assertIn("PROJECT.md", listed2)

    def test_template_title_uses_project_id(self) -> None:
        with temporary_agent_paths(prefix="doc-title-") as paths:
            pid = "title-demo"
            create_project(paths, pid, project_template="standard")
            body = (project_dir(paths, pid) / "PROJECT.md").read_text(encoding="utf-8")
            self.assertIn("# title-demo ·", body)
            self.assertNotIn("# template ·", body)

    def test_api_write_conflict_event(self) -> None:
        with temporary_agent_paths(prefix="doc-api-") as paths:
            pid = "api-demo"
            root = project_dir(paths, pid)
            root.mkdir(parents=True)
            (root / "x.md").write_text("# 1\n", encoding="utf-8")
            session = create_new(paths, conversation_id="doc-api")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_workflow_stage = "documentation"  # type: ignore[assignment]
            session.save()
            first = read_project_doc(paths, pid, "x.md")
            write_project_doc(paths, pid, "x.md", "# 2\n")
            result = dispatch_doc_message(
                session,
                paths,
                {
                    "type": "project.doc.write",
                    "path": "x.md",
                    "content": "# 3\n",
                    "base_hash": first["content_hash"],
                },
            )
            self.assertEqual(result["type"], "project.doc.write.conflict")


if __name__ == "__main__":
    unittest.main()
