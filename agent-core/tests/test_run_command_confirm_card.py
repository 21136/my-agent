"""Dogfood fix: run_command confirm cards show the real command; temp cleanup is not danger."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from run_command_policy import classify_run_command, run_command_requires_confirm
from tools.executor import build_confirm_preview

_LONG_CMD = (
    "python3 -m unittest -v test_todo.py && rm -f .test-todos.json && "
    "python3 todo.py --file .test-todos.json add --due 2026-10-20 \"写周报\" && "
    "python3 todo.py --file .test-todos.json add \"买牛奶\" && "
    "python3 todo.py --file .test-todos.json list --sort due && "
    "python3 todo.py --file .test-todos.json list --overdue && "
    "rm -f .test-todos.json"
)


class RunCommandConfirmCardTests(unittest.TestCase):
    def _preview(self, command: str) -> str:
        return build_confirm_preview(
            "run_evolved",
            {"tool_name": "run_command", "arguments": {"command": command, "cwd": "workspace/todo"}},
        )

    def test_long_command_is_shown_in_full(self) -> None:
        self.assertGreater(len(_LONG_CMD), 300)
        preview = self._preview(_LONG_CMD)
        self.assertIn("命令全文：\n" + _LONG_CMD, preview)
        first = preview.splitlines()[0]
        self.assertTrue(first.startswith("运行命令：python3 -m unittest"), first)
        self.assertNotRegex(first, r"^运行命令：\d+ 字符")

    def test_multiline_command_keeps_lines(self) -> None:
        cmd = "cd sub && \\\n  python3 -m pytest -q\necho done"
        preview = self._preview(cmd)
        self.assertTrue(preview.endswith("命令全文：\n" + cmd), preview)

    def test_huge_command_is_capped_not_hidden(self) -> None:
        cmd = "echo " + "x" * 9000
        preview = self._preview(cmd)
        self.assertIn("echo xxxx", preview)
        self.assertIn("未显示", preview)
        self.assertLess(len(preview), 9000)

    def test_workspace_temp_cleanup_is_not_danger(self) -> None:
        self.assertEqual(classify_run_command("rm -f .test-todos.json"), "other")
        self.assertEqual(classify_run_command(_LONG_CMD), "other")
        self.assertEqual(classify_run_command("rm -f build/out.tmp *.log"), "other")
        self.assertEqual(classify_run_command("rm -f \\\n  a.json b.json"), "other")

    def test_temp_cleanup_still_confirms(self) -> None:
        needs, reason = run_command_requires_confirm(
            command="pytest -q && rm -f .cache.json",
            working_dir="workspace/todo",
            project_root="workspace/todo",
            quality_commands=[["pytest"]],
        )
        self.assertTrue(needs)
        self.assertEqual(reason, "other:delete")
        self.assertEqual(classify_run_command("echo hi; rm -f a.txt"), "other")

    def test_real_danger_stays_danger(self) -> None:
        for cmd in (
            "rm -rf tmp",
            "rm -fr .",
            "rm -f /etc/passwd",
            "rm -f ../other/x",
            "rm -f ~/notes.txt",
            "rm -f $HOME/x",
            "rm -f *",
            "rm -f C:/Windows/x",
            "rm -f a.txt; rm -rf /",
            "sudo rm a",
            "sudo apt-get update",
            "git clean -fdx",
            "git reset --hard HEAD~3",
            "chmod -R 777 .",
            "Remove-Item -Recurse src",
            "del /s *.py",
            "dd if=/dev/zero of=disk.img",
        ):
            self.assertEqual(classify_run_command(cmd), "danger", cmd)


if __name__ == "__main__":
    unittest.main()
