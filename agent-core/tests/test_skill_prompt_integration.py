from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_REPO_SKILLS = Path(__file__).resolve().parents[2] / "evolve" / "skills"
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from loader import build_system_prompt
from skill_runtime import begin_agent_skill_turn, clear_skill_index_cache_for_tests
from session import create_new
from tests.isolation_helpers import make_temp_agent_paths


class SkillPromptIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_skill_index_cache_for_tests()

    def test_matching_skill_loads_body_in_dynamic_overlay(self) -> None:
        paths = make_temp_agent_paths(self, prefix="skill-prompt-")
        shutil.copytree(_REPO_SKILLS, paths.evolve / "skills")
        session = create_new(paths, conversation_id="skill-prompt")
        user = "请跑验收并报告测试证据"
        session.messages = [{"role": "user", "content": user}]
        begin_agent_skill_turn(session, user)
        loaded = build_system_prompt(session, paths=paths, agent_core_dir=_AGENT_CORE)
        self.assertIn("skill_catalog", loaded.section_names)
        self.assertIn("skill_context", loaded.section_names)
        self.assertIn("project-verification", loaded.prompt)

        static = build_system_prompt(
            session,
            paths=paths,
            agent_core_dir=_AGENT_CORE,
            include_overlay=False,
        )
        self.assertNotIn("skill_catalog", static.section_names)
        self.assertNotIn("skill_context", static.section_names)

    def test_catalog_present_even_without_match(self) -> None:
        paths = make_temp_agent_paths(self, prefix="skill-scope-")
        shutil.copytree(_REPO_SKILLS, paths.evolve / "skills")
        session = create_new(paths, conversation_id="skill-scope")
        begin_agent_skill_turn(session, "请查看项目状态")
        loaded = build_system_prompt(session, paths=paths, agent_core_dir=_AGENT_CORE)
        self.assertIn("skill_catalog", loaded.section_names)
        self.assertNotIn("skill_context", loaded.section_names)

        begin_agent_skill_turn(session, "这个程序发生崩溃了")
        second = build_system_prompt(session, paths=paths, agent_core_dir=_AGENT_CORE)
        self.assertIn("skill_context", second.section_names)
        self.assertIn("systematic-debugging", second.prompt)


if __name__ == "__main__":
    unittest.main()