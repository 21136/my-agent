from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_cli import parse_project_command, run_project_command
from project_mode import project_dir
from project_switch import read_project_sessions
from session import create_new
from tests.isolation_helpers import make_temp_agent_paths


class ExternalProjectCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self, prefix="external-cli-")
        self.external = Path(tempfile.mkdtemp(prefix="external-cli-source-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.external, ignore_errors=True))
        (self.external / "README.md").write_text("# Existing\n", encoding="utf-8")
        (self.external / "app.py").write_text("print('partial')\n", encoding="utf-8")

    def test_parse_import_command_with_windows_path_and_id(self) -> None:
        command = parse_project_command(f'项目 导入 "{self.external}" imported-demo')
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.kind, "import")
        self.assertEqual(command.external_path, str(self.external))
        self.assertEqual(command.project_id, "imported-demo")

    def test_import_binds_original_directory_without_confirming_plan(self) -> None:
        session = create_new(self.paths, conversation_id="external-cli-session")
        command = parse_project_command(f'项目 导入 "{self.external}" imported-demo')
        assert command is not None
        output: list[str] = []
        result = run_project_command(session, self.paths, command, output_fn=output.append)
        self.assertTrue(result.meta_changed)
        self.assertEqual(session.meta.project_id, "imported-demo")
        self.assertEqual(session.meta.project_root, "workspace/imported-demo")
        self.assertEqual(session.meta.project_plan_status, "draft")
        self.assertEqual(session.next_work_intent, "execute")
        self.assertTrue((self.external / "app.py").is_file())
        self.assertTrue((self.external / "TASKS.md").is_file())
        self.assertEqual(
            (project_dir(self.paths, "imported-demo") / "app.py").resolve(),
            self.external.resolve() / "app.py",
        )
        self.assertIn("原地接入", "\n".join(output))
        self.assertEqual(read_project_sessions(self.paths).get("imported-demo"), session.conversation_id)

    def test_imported_project_can_be_reopened_from_a_new_session(self) -> None:
        session = create_new(self.paths, conversation_id="external-cli-open-source")
        command = parse_project_command(f'项目 导入 "{self.external}" imported-reopen')
        assert command is not None
        run_project_command(session, self.paths, command, output_fn=lambda _line: None)

        reopened = create_new(self.paths, conversation_id="external-cli-reopened")
        open_command = parse_project_command("项目 切换 imported-reopen")
        assert open_command is not None
        result = run_project_command(reopened, self.paths, open_command, output_fn=lambda _line: None)
        if result.session is not None:
            reopened = result.session
        self.assertEqual(reopened.meta.project_id, "imported-reopen")
        self.assertTrue(result.meta_changed)
        self.assertTrue((self.external / "TASKS.md").is_file())

    def test_import_does_not_overwrite_existing_external_tasks(self) -> None:
        existing = "# Existing tasks\n- [ ] T-777 continue existing work\n"
        (self.external / "TASKS.md").write_text(existing, encoding="utf-8")
        session = create_new(self.paths, conversation_id="external-cli-existing-tasks")
        command = parse_project_command(f'项目 导入 "{self.external}" imported-existing')
        assert command is not None
        run_project_command(session, self.paths, command, output_fn=lambda _line: None)
        self.assertEqual((self.external / "TASKS.md").read_text(encoding="utf-8"), existing)


if __name__ == "__main__":
    unittest.main()
