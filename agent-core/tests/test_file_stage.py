"""Tests for desktop file drag-drop staging (T-1201 · IT-61 / T-1824-06)."""

from __future__ import annotations

import secrets
import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from file_stage import (
    compose_user_message,
    format_attachment_block,
    stage_absolute_path,
)
from host_scope import load_host_scope
from session import create_new

from tests.isolation_helpers import make_temp_agent_paths


class FileStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def test_project_incoming_copy(self) -> None:
        ext = self.tmp / "module.py"
        ext.write_text("x = 1\n", encoding="utf-8")

        session = create_new(
            self.paths, conversation_id=f"_test_file_stage_{secrets.token_hex(4)}"
        )
        session.meta.active_shell = "project"
        session.meta.project_root = "workspace/_test_file_stage_proj"
        proj = self.paths.resolve_under_agent(session.meta.project_root, must_exist=False)
        proj.mkdir(parents=True, exist_ok=True)

        item = stage_absolute_path(
            str(ext),
            paths=self.paths,
            session=session,
            shell="project",
            config=load_host_scope(self.paths),
        )
        self.assertTrue(item.copied)
        self.assertIn("/_incoming/", item.ref.replace("\\", "/"))
        self.assertTrue(item.readable_text)
        copied = self.paths.resolve_under_agent(item.ref, must_exist=True)
        self.assertEqual(copied.read_text(encoding="utf-8"), "x = 1\n")

    def test_session_history_keeps_attachment_block(self) -> None:
        from file_stage import StagedAttachment
        from session import build_session_chat_history

        session = create_new(
            self.paths, conversation_id=f"_test_file_history_{secrets.token_hex(4)}"
        )
        item = StagedAttachment(
            id="x",
            name="a.py",
            ref="workspace/p/_incoming/x/a.py",
            size=3,
            mime="text/x-python",
            readable_text=True,
            copied=True,
        )
        text = compose_user_message(text="看下这个", attachments=[item])
        session.messages.append({"role": "user", "content": text})
        history = build_session_chat_history(session)
        self.assertEqual(len(history), 1)
        self.assertIn("[附件]", history[0]["text"])
        self.assertIn("看下这个", history[0]["text"])

    def test_compose_user_message_with_attachments(self) -> None:
        from file_stage import StagedAttachment

        item = StagedAttachment(
            id="a",
            name="a.py",
            ref="workspace/p/_incoming/x/a.py",
            size=3,
            mime="text/x-python",
            readable_text=True,
            copied=True,
        )
        text = compose_user_message(text="", attachments=[item])
        self.assertIn("[附件]", text)
        self.assertIn("用户附带了", text)
        block = format_attachment_block([item])
        self.assertIn("a.py", block)


class ChineseFilenameDropTests(unittest.TestCase):
    """IT-61 / T-1824-06: Chinese (and space) filenames survive file.stage copy."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def test_grow_chinese_filename_preserved(self) -> None:
        chinese_name = "中文说明.txt"
        ext = self.tmp / chinese_name
        body = "中文内容-计划待确认\n"
        ext.write_text(body, encoding="utf-8")

        session = create_new(
            self.paths, conversation_id=f"_it61_grow_{secrets.token_hex(4)}"
        )
        session.meta.active_shell = "grow"
        session.save()

        item = stage_absolute_path(
            str(ext.resolve()),
            paths=self.paths,
            session=session,
            shell="grow",
            config=load_host_scope(self.paths),
        )
        self.assertTrue(item.copied)
        self.assertEqual(item.name, chinese_name)
        self.assertIn("/_drops/", item.ref.replace("\\", "/"))
        self.assertTrue(item.ref.endswith(chinese_name) or chinese_name in item.ref)
        on_disk = self.paths.resolve_under_agent(item.ref, must_exist=True)
        self.assertEqual(on_disk.name, chinese_name)
        self.assertEqual(on_disk.read_text(encoding="utf-8"), body)
        self.assertTrue(item.readable_text)

        block = format_attachment_block([item])
        self.assertIn(chinese_name, block)
        msg = compose_user_message(text="看看附件", attachments=[item])
        self.assertIn(chinese_name, msg)
        self.assertIn("[附件]", msg)

    def test_project_chinese_and_space_filename(self) -> None:
        chinese_space = "说明 文档.md"
        ext = self.tmp / chinese_space
        ext.write_text("# 标题\n内容\n", encoding="utf-8")

        session = create_new(
            self.paths, conversation_id=f"_it61_proj_{secrets.token_hex(4)}"
        )
        session.meta.active_shell = "project"
        session.meta.project_root = "workspace/it61-cn-proj"
        proj = self.paths.resolve_under_agent(session.meta.project_root, must_exist=False)
        proj.mkdir(parents=True, exist_ok=True)
        session.save()

        item = stage_absolute_path(
            str(ext.resolve()),
            paths=self.paths,
            session=session,
            shell="project",
            config=load_host_scope(self.paths),
        )
        self.assertTrue(item.copied)
        self.assertEqual(item.name, chinese_space)
        self.assertIn("/_incoming/", item.ref.replace("\\", "/"))
        on_disk = self.paths.resolve_under_agent(item.ref, must_exist=True)
        self.assertEqual(on_disk.name, chinese_space)
        self.assertIn("标题", on_disk.read_text(encoding="utf-8"))

    def test_chinese_parent_dir_external_path(self) -> None:
        """External path may include Chinese directory segments (Windows common)."""
        nested = self.tmp / "桌面资料" / "笔记"
        nested.mkdir(parents=True)
        name = "草稿.py"
        ext = nested / name
        ext.write_text("print('ok')\n", encoding="utf-8")

        session = create_new(
            self.paths, conversation_id=f"_it61_nested_{secrets.token_hex(4)}"
        )
        session.meta.active_shell = "daily"
        session.save()

        item = stage_absolute_path(
            str(ext.resolve()),
            paths=self.paths,
            session=session,
            shell="daily",
            config=load_host_scope(self.paths),
        )
        self.assertTrue(item.copied)
        self.assertEqual(item.name, name)
        on_disk = self.paths.resolve_under_agent(item.ref, must_exist=True)
        self.assertEqual(on_disk.name, name)
        self.assertEqual(on_disk.read_text(encoding="utf-8"), "print('ok')\n")


if __name__ == "__main__":
    unittest.main()
