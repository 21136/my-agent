"""Phase 46 M1 — tool workshop prompt injection (IT-461, IT-463)."""

from __future__ import annotations

import secrets
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_ROOT = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from loader import (
    build_system_prompt,
    is_workshop_eligible,
    load_tool_workshop_prompt,
)
from session import Session, SessionMeta, create_new, utc_now_iso
from tools.registry import ToolRegistry

from tests.isolation_helpers import make_temp_agent_paths


def _make_session(
    paths,
    *,
    project_id: str = "",
    project_root: str = "",
    active_shell: str = "grow",
    scaffold_tool_turn: bool = False,
    topics: list[str] | None = None,
) -> Session:
    session = create_new(
        paths,
        conversation_id=f"_tw_{secrets.token_hex(4)}",
    )
    session.meta.project_id = project_id
    session.meta.project_root = project_root
    session.meta.active_shell = active_shell
    session.meta.topics = topics or []
    session.scaffold_tool_turn = scaffold_tool_turn
    session.save()
    return session


class WorkshopEligibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        workshop_src = _ROOT / "evolve" / "prompts" / "tool_workshop.md"
        dest = self.paths.evolve / "prompts"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "tool_workshop.md").write_text(
            workshop_src.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    def test_project_bound_has_no_tool_workshop_section(self) -> None:
        session = _make_session(
            self.paths,
            project_id="demo",
            project_root="workspace/demo",
            active_shell="project",
        )
        loaded = build_system_prompt(
            session,
            paths=self.paths,
            agent_core_dir=_AGENT_CORE,
        )
        self.assertNotIn("tool_workshop", loaded.section_names)
        self.assertFalse(is_workshop_eligible(session))

    def test_grow_bound_to_project_still_no_workshop(self) -> None:
        session = _make_session(
            self.paths,
            project_id="demo",
            project_root="workspace/demo",
            active_shell="grow",
        )
        loaded = build_system_prompt(
            session,
            paths=self.paths,
            agent_core_dir=_AGENT_CORE,
        )
        self.assertNotIn("tool_workshop", loaded.section_names)

    def test_grow_unbound_has_tool_workshop_section(self) -> None:
        session = _make_session(
            self.paths,
            project_id="",
            project_root="",
            active_shell="grow",
        )
        loaded = build_system_prompt(
            session,
            paths=self.paths,
            agent_core_dir=_AGENT_CORE,
        )
        self.assertIn("tool_workshop", loaded.section_names)
        self.assertTrue(is_workshop_eligible(session))
        self.assertTrue(
            "工具工坊" in loaded.prompt or "Tool Workshop" in loaded.prompt
        )


class ToolWorkshopContentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(
            self,
            copy_tool_dirs=("common/write_evolve",),
        )
        workshop_src = _ROOT / "evolve" / "prompts" / "tool_workshop.md"
        dest = self.paths.evolve / "prompts"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "tool_workshop.md").write_text(
            workshop_src.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        catalog_src = _ROOT / "evolve" / "tool-catalog"
        catalog_dest = self.paths.agent_root / "evolve" / "tool-catalog"
        catalog_dest.mkdir(parents=True, exist_ok=True)
        (catalog_dest / "INDEX.md").write_text(
            (catalog_src / "INDEX.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    def test_tool_workshop_under_55_lines(self) -> None:
        text = load_tool_workshop_prompt(self.paths.evolve)
        self.assertLessEqual(len(text.splitlines()), 55)

    def test_scaffold_turn_still_has_overlay_and_cookbook(self) -> None:
        registry = ToolRegistry.load(self.paths)
        session = _make_session(
            self.paths,
            scaffold_tool_turn=True,
            topics=["coding"],
        )
        loaded = build_system_prompt(
            session,
            paths=self.paths,
            agent_core_dir=_AGENT_CORE,
            registry=registry,
        )
        self.assertIn("scaffold_tool", loaded.section_names)
        self.assertIn("write_evolve 调用规范", loaded.prompt)

    def test_no_triplicate_base64_manual(self) -> None:
        workshop = load_tool_workshop_prompt(self.paths.evolve)
        self.assertNotIn("content_base64 = UTF-8", workshop)

        session = _make_session(self.paths, scaffold_tool_turn=True)
        loaded = build_system_prompt(
            session,
            paths=self.paths,
            agent_core_dir=_AGENT_CORE,
        )
        parts = loaded.prompt.split("\n---\n")
        by_name = dict(zip(loaded.section_names, parts, strict=False))
        self.assertNotIn("content_base64 = UTF-8", by_name.get("tool_workshop", ""))
        catalog = by_name.get("evolved_catalog", "")
        self.assertIn("write_evolve 调用规范", catalog)
        self.assertIn("content_base64", catalog)
        self.assertNotIn("write_evolve 调用规范", by_name.get("tool_workshop", ""))


if __name__ == "__main__":
    unittest.main()
