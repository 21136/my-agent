"""Unit tests for llm_routing (Phase 42 Track J · IT-440/441)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_client import DEFAULT_MODEL, DEFAULT_MODEL_CODING
from llm_routing import resolve_model_id_for_role
from plan_agent import PlanAgent, drop_plan_agent
from session import SessionMeta
from tests.isolation_helpers import make_temp_agent_paths


class LlmRoutingTests(unittest.TestCase):
    def test_it440_main_turn_defaults_flash(self) -> None:
        self.assertEqual(
            resolve_model_id_for_role("main_turn", SessionMeta()),
            DEFAULT_MODEL,
        )

    def test_it440_main_turn_respects_llm_model(self) -> None:
        meta = SessionMeta(llm_model="deepseek-v4-pro")
        self.assertEqual(resolve_model_id_for_role("main_turn", meta), DEFAULT_MODEL_CODING)

    def test_it440_execution_model_overrides_llm_model(self) -> None:
        meta = SessionMeta(
            llm_model="deepseek-v4-pro",
            execution_model="deepseek-v4-flash",
        )
        self.assertEqual(resolve_model_id_for_role("main_turn", meta), DEFAULT_MODEL)

    def test_it441_plan_partner_defaults_pro(self) -> None:
        self.assertEqual(
            resolve_model_id_for_role("plan_partner", SessionMeta()),
            DEFAULT_MODEL_CODING,
        )

    def test_it441_planning_model_override(self) -> None:
        meta = SessionMeta(planning_model="deepseek-v4-flash")
        self.assertEqual(resolve_model_id_for_role("plan_partner", meta), DEFAULT_MODEL)

    def test_plan_partner_env_override(self) -> None:
        with patch.dict(os.environ, {"PLAN_PARTNER_MODEL": "deepseek-v4-flash"}, clear=False):
            self.assertEqual(
                resolve_model_id_for_role("plan_partner", SessionMeta()),
                DEFAULT_MODEL,
            )

    def test_checker_env_override(self) -> None:
        with patch.dict(os.environ, {"CHECKER_MODEL": "deepseek-v4-pro"}, clear=False):
            self.assertEqual(
                resolve_model_id_for_role("checker", SessionMeta()),
                DEFAULT_MODEL_CODING,
            )


class PlanAgentRoutingTests(unittest.TestCase):
    def test_plan_agent_uses_configured_pro_model(self) -> None:
        paths = make_temp_agent_paths(self)
        pid = "route-demo"
        drop_plan_agent(pid)
        agent = PlanAgent(paths=paths, project_id=pid)
        agent.configure_planning_model(DEFAULT_MODEL_CODING)
        llm = agent._ensure_llm()
        self.assertEqual(getattr(llm, "_plan_model", ""), DEFAULT_MODEL_CODING)


if __name__ == "__main__":
    unittest.main()
