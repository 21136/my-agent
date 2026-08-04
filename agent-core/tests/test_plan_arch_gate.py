"""IT-181 · PLAN-ARCH M2: add/move gated until accept_suggestion."""

from __future__ import annotations

import json
import secrets
import unittest
from unittest.mock import MagicMock

from plan_agent import drop_plan_agent, get_plan_agent
from project_mode import create_project, normalize_project_id, project_dir

from tests.isolation_helpers import make_temp_agent_paths


class PlanArchGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.pid = normalize_project_id(f"pg-{secrets.token_hex(3)}")
        create_project(self.paths, self.pid)
        drop_plan_agent(self.pid)
        self.agent = get_plan_agent(self.paths, self.pid)
        self.tasks = project_dir(self.paths, self.pid) / "TASKS.md"

    def tearDown(self) -> None:
        drop_plan_agent(self.pid)

    def _write_tasks(self, body: str) -> None:
        self.tasks.write_text(body.rstrip() + "\n", encoding="utf-8")

    def test_it181_llm_add_does_not_write_until_accept(self) -> None:
        self._write_tasks(
            "## Phase 1\n"
            "- [ ] Existing open task with enough text\n"
        )
        before = self.tasks.read_text(encoding="utf-8")
        fake_resp = MagicMock()
        fake_resp.content = json.dumps(
            {
                "operations": [
                    {
                        "kind": "add",
                        "phase": "Phase 1",
                        "description": "Brand new gated task item here",
                        "reason": "user asked",
                    }
                ]
            },
            ensure_ascii=False,
        )
        fake_llm = MagicMock()
        fake_llm.chat.return_value = fake_resp
        fake_llm._plan_model = "test"
        self.agent._llm = fake_llm

        summary = self.agent.reason_about_intent("加个任务")
        self.assertIn("提案", summary)
        self.assertEqual(self.tasks.read_text(encoding="utf-8"), before)
        self.assertGreaterEqual(len(self.agent._pending_gated), 1)

        state = self.agent.build_state()
        adds = [
            s
            for s in (state.get("suggestions") or [])
            if isinstance(s, dict) and s.get("action") == "add_task"
        ]
        self.assertTrue(adds)
        self.agent.accept_suggestion(adds[0]["id"])
        self.assertIn("Brand new gated task item here", self.tasks.read_text(encoding="utf-8"))
        self.assertNotIn(adds[0]["id"], self.agent._pending_gated)
        self.assertEqual(self.agent._last_partner_notices, [])
        state_after = self.agent.build_state()
        self.assertEqual(state_after.get("partner_notices") or [], [])

    def test_pending_gated_survives_build_state(self) -> None:
        sug = self.agent._suggestion(
            kind="add_task",
            title="t",
            body="b",
            key="persist-1",
            risk="gate",
            action="add_task",
            payload={"phase": "Phase 1", "description": "Persist me please item"},
        )
        self.agent.park_gated_suggestion(sug)
        state = self.agent.build_state()
        ids = {s["id"] for s in (state.get("suggestions") or []) if isinstance(s, dict)}
        self.assertIn(sug["id"], ids)


if __name__ == "__main__":
    unittest.main()
