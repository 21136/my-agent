"""Project document catalog: list / read / create / write / rename / delete."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_api import ProjectApiError, dispatch_doc_message
from project_mode import (
    ProjectModeError,
    create_project_doc,
    delete_project_doc,
    list_project_docs,
    project_dir,
    read_project_doc,
    rename_project_doc,
    write_project_doc,
)
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


def _bind_project(session, pid: str, *, stage: str = "documentation") -> None:
    session.meta.active_shell = "project"
    session.meta.project_id = pid
    session.meta.project_workflow_stage = stage  # type: ignore[assignment]
    session.save()


class ProjectDocOpsTests(unittest.TestCase):
    def test_list_skips_node_modules_and_supports_write_rename_delete(self) -> None:
        with temporary_agent_paths(prefix="project-docs-") as paths:
            pid = "doc-ops"
            root = project_dir(paths, pid)
            root.mkdir(parents=True)
            (root / "PROJECT.md").write_text("# 项目\n", encoding="utf-8")
            (root / "notes.md").write_text("# notes\n", encoding="utf-8")
            nested = root / "node_modules" / "pkg"
            nested.mkdir(parents=True)
            (nested / "README.md").write_text("vendor\n", encoding="utf-8")

            listed = {item["path"] for item in list_project_docs(paths, pid)}
            self.assertIn("PROJECT.md", listed)
            self.assertIn("notes.md", listed)
            self.assertNotIn("node_modules/pkg/README.md", listed)

            written = write_project_doc(paths, pid, "notes.md", "# 笔记\r\n第二行\r")
            self.assertEqual(written["type"], "project.doc.write.done")
            self.assertEqual((root / "notes.md").read_text(encoding="utf-8"), "# 笔记\n第二行\n")

            renamed = rename_project_doc(paths, pid, "notes.md", title="需求分析")
            self.assertEqual(renamed["path"], "需求分析.md")
            self.assertEqual(renamed["old_path"], "notes.md")
            self.assertFalse((root / "notes.md").exists())
            self.assertTrue((root / "需求分析.md").is_file())

            same = rename_project_doc(paths, pid, "需求分析.md", title="需求分析.md")
            self.assertEqual(same["path"], "需求分析.md")

            deleted = delete_project_doc(paths, pid, "需求分析.md")
            self.assertEqual(deleted["type"], "project.doc.delete.done")
            self.assertFalse((root / "需求分析.md").exists())
            self.assertTrue((root / "PROJECT.md").is_file())

    def test_rename_keeps_directory_and_rejects_collision_and_escape(self) -> None:
        with temporary_agent_paths(prefix="project-docs-rename-") as paths:
            pid = "doc-rename"
            root = project_dir(paths, pid)
            (root / "guides").mkdir(parents=True)
            (root / "guides" / "alpha.md").write_text("a\n", encoding="utf-8")
            (root / "guides" / "beta.md").write_text("b\n", encoding="utf-8")

            moved = rename_project_doc(paths, pid, "guides/alpha.md", title="概述")
            self.assertEqual(moved["path"], "guides/概述.md")
            self.assertTrue((root / "guides" / "概述.md").is_file())

            with self.assertRaisesRegex(ProjectModeError, "同名"):
                rename_project_doc(paths, pid, "guides/概述.md", title="beta")

            with self.assertRaisesRegex(ProjectModeError, "超出项目目录"):
                rename_project_doc(paths, pid, "guides/概述.md", new_path="../escape.md")

            with self.assertRaisesRegex(ProjectModeError, "找不到文档"):
                read_project_doc(paths, pid, "missing.md")

            with self.assertRaisesRegex(ProjectModeError, "不在项目文档目录"):
                delete_project_doc(paths, pid, "node_modules/pkg/README.md")

    def test_create_adds_md_suffix(self) -> None:
        with temporary_agent_paths(prefix="project-docs-create-") as paths:
            pid = "doc-create"
            project_dir(paths, pid).mkdir(parents=True)
            created = create_project_doc(paths, pid, "草稿")
            self.assertEqual(created["path"], "草稿.md")
            self.assertIn("# 草稿", (project_dir(paths, pid) / "草稿.md").read_text(encoding="utf-8"))

    def test_dispatch_write_rename_delete_and_requirements_create_gate(self) -> None:
        with temporary_agent_paths(prefix="project-docs-api-") as paths:
            pid = "doc-api"
            root = project_dir(paths, pid)
            root.mkdir(parents=True)
            (root / "TASKS.md").write_text("# tasks\n", encoding="utf-8")
            (root / "scratch.md").write_text("old\n", encoding="utf-8")

            session = create_new(paths, conversation_id="doc-api-session")
            _bind_project(session, pid, stage="requirements")

            with self.assertRaisesRegex(ProjectApiError, "整理文档"):
                dispatch_doc_message(session, paths, {"type": "project.doc.create", "path": "x.md"})

            written = dispatch_doc_message(
                session,
                paths,
                {"type": "project.doc.write", "path": "scratch.md", "content": "new body\n"},
            )
            events = written["_events"]
            self.assertEqual(events[0]["type"], "project.doc.write.done")
            self.assertEqual(events[1]["type"], "project.doc.list.done")
            self.assertEqual((root / "scratch.md").read_text(encoding="utf-8"), "new body\n")

            renamed = dispatch_doc_message(
                session,
                paths,
                {"type": "project.doc.rename", "path": "scratch.md", "title": "备忘"},
            )
            self.assertEqual(renamed["_events"][0]["path"], "备忘.md")

            deleted = dispatch_doc_message(
                session,
                paths,
                {"type": "project.doc.delete", "path": "备忘.md"},
            )
            self.assertEqual(deleted["_events"][0]["type"], "project.doc.delete.done")
            self.assertFalse((root / "备忘.md").exists())
            listed_paths = {item["path"] for item in deleted["_events"][1]["docs"]}
            self.assertIn("TASKS.md", listed_paths)
            self.assertNotIn("备忘.md", listed_paths)

            with self.assertRaisesRegex(ProjectApiError, "unknown doc message type"):
                dispatch_doc_message(session, paths, {"type": "project.doc.nope"})


if __name__ == "__main__":
    unittest.main()
