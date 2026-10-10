"""Ordinary / light create must not leak plan chrome, T-001, or template docs."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from context_switch import create_project_with_session_isolation
from project_api import build_plan_request_payload, project_state_payload
from project_cli import parse_project_command, run_project_command
from project_mode import create_project, list_project_docs, project_dir, read_task_stats
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


class OrdinaryEmptyCreateTests(unittest.TestCase):
    def test_light_create_has_no_seeded_tasks(self) -> None:
        with temporary_agent_paths() as paths:
            root = create_project(paths, "lite-empty", project_template="light")
            tasks = (root / "TASKS.md").read_text(encoding="utf-8")
            self.assertNotIn("T-001", tasks)
            self.assertEqual(read_task_stats(root / "TASKS.md").total, 0)
            self.assertFalse((root / "DESIGN.md").is_file())
            self.assertFalse((root / "SCOPE.md").is_file())

    def test_light_create_hides_placeholder_docs(self) -> None:
        with temporary_agent_paths() as paths:
            create_project(paths, "lite-docs", project_template="light")
            docs = list_project_docs(paths, "lite-docs")
            names = {item["name"] for item in docs}
            self.assertNotIn("PROJECT.md", names)
            self.assertNotIn("TASKS.md", names)
            self.assertNotIn("VERIFY.md", names)
            # User-written docs still show.
            (project_dir(paths, "lite-docs") / "NOTES.md").write_text("# notes\n", encoding="utf-8")
            names2 = {item["name"] for item in list_project_docs(paths, "lite-docs")}
            self.assertIn("NOTES.md", names2)

    def test_light_cli_create_is_direct_and_skips_plan_request(self) -> None:
        with temporary_agent_paths() as paths:
            session = create_new(paths, conversation_id="_ord_empty_")
            outputs: list[str] = []
            command = parse_project_command("项目 新建 dogfood-empty --light")
            assert command is not None
            result = run_project_command(session, paths, command, output_fn=outputs.append)
            bound = result.session or session
            self.assertEqual(bound.meta.project_entry, "direct")
            self.assertEqual(bound.meta.project_plan_status, "confirmed")
            self.assertIsNone(build_plan_request_payload(bound, paths))
            state = project_state_payload(bound, paths)
            self.assertEqual(state.get("project_entry"), "direct")
            self.assertEqual(state.get("project_template"), "light")
            self.assertFalse(state.get("needs_plan_confirm"))
            self.assertEqual(state.get("tasks_total"), 0)
            self.assertTrue(any("可直接写代码" in line for line in outputs))
            self.assertFalse(any("计划待确认" in line for line in outputs))

    def test_standard_create_still_gets_plan_request(self) -> None:
        with temporary_agent_paths() as paths:
            session = create_new(paths, conversation_id="_ord_std_")
            updated, message = create_project_with_session_isolation(
                paths, session, "std-plan", project_template="standard"
            )
            self.assertIn("计划待确认", message)
            self.assertNotEqual(updated.meta.project_entry, "direct")
            payload = build_plan_request_payload(updated, paths)
            self.assertIsNotNone(payload)
            assert payload is not None
            self.assertEqual(payload["type"], "plan.request")
            self.assertGreater(payload["tasks_total"], 0)
            root = project_dir(paths, "std-plan")
            self.assertIn("T-001", (root / "TASKS.md").read_text(encoding="utf-8"))
            docs = list_project_docs(paths, "std-plan")
            names = {item["name"] for item in docs}
            self.assertIn("PROJECT.md", names)
            self.assertIn("DESIGN.md", names)


if __name__ == "__main__":
    unittest.main()
