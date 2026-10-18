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
    create_project,
    count_untouched_template_docs,
    delete_project_doc,
    format_project_overlay,
    list_project_docs,
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
