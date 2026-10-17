from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from loader import build_system_prompt
from skill_registry import SkillRegistry
from session import create_new
from tests.isolation_helpers import make_temp_agent_paths


class SkillPromptIntegrationTests(unittest.TestCase):
    def test_matching_skill_is_loaded_only_in_dynamic_overlay(self) -> None:
        paths = make_temp_agent_paths(self, prefix="skill-prompt-")
        shutil.copytree(Path("evolve/skills"), paths.evolve / "skills")
        session = create_new(paths, conversation_id="skill-prompt")
        session.messages = [{"role": "user", "content": "请跑验收并报告测试证据"}]
        loaded = build_system_prompt(session, paths=paths, agent_core_dir=_AGENT_CORE)
        self.assertIn("skill_index", loaded.section_names)
        self.assertIn("skill_context", loaded.section_names)
        self.assertIn("project-verification", loaded.prompt)

        static = build_system_prompt(
            session,
            paths=paths,
            agent_core_dir=_AGENT_CORE,
            include_overlay=False,
        )
        self.assertNotIn("skill_index", static.section_names)
        self.assertNotIn("skill_context", static.section_names)


    def test_pinned_skill_package_survives_source_change_until_turn_clears(self) -> None:
        paths = make_temp_agent_paths(self, prefix="skill-pin-")
        shutil.copytree(Path("evolve/skills"), paths.evolve / "skills")
        session = create_new(paths, conversation_id="skill-pin")
        session.messages = [{"role": "user", "content": "请跑验收"}]
        registry = SkillRegistry.discover(paths.evolve / "skills")
        selection = registry.select_packages("请跑验收", limit=1)[0]
        session._turn_skill_selection = selection

        loaded = build_system_prompt(session, paths=paths)
        self.assertIn(selection.identity.content_hash, loaded.prompt)
        self.assertIn("skill_context", loaded.dynamic_section_names)

        path = paths.evolve / "skills" / "project-verification" / "SKILL.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nPINNED_BODY_SENTINEL\n", encoding="utf-8")
        pinned = build_system_prompt(session, paths=paths)
        self.assertNotIn("PINNED_BODY_SENTINEL", pinned.prompt)
        self.assertIn(selection.identity.content_hash, pinned.prompt)

        session._turn_skill_selection = None
        refreshed = build_system_prompt(session, paths=paths)
        self.assertIn("PINNED_BODY_SENTINEL", refreshed.prompt)
        self.assertNotEqual(selection.identity.content_hash, registry.discover(paths.evolve / "skills").select_packages("请跑验收")[0].identity.content_hash)

    def test_skill_selection_is_request_scoped(self) -> None:
        paths = make_temp_agent_paths(self, prefix="skill-scope-")
        shutil.copytree(Path("evolve/skills"), paths.evolve / "skills")
        session = create_new(paths, conversation_id="skill-scope")
        session.messages = [{"role": "user", "content": "请查看项目状态"}]
        first = build_system_prompt(session, paths=paths, agent_core_dir=_AGENT_CORE)
        self.assertNotIn("skill_context", first.section_names)
        session.messages.append({"role": "user", "content": "修复这个报错并跑回归测试"})
        second = build_system_prompt(session, paths=paths, agent_core_dir=_AGENT_CORE)
        self.assertIn("skill_context", second.section_names)
        self.assertIn("systematic-debugging", second.prompt)


if __name__ == "__main__":
    unittest.main()
