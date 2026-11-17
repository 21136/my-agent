from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from loader import build_system_prompt
from session import create_new
from skill_registry import SkillRegistry, reset_skill_file_read_count, skill_file_read_count
from tools.builtin import load_workflow_skill
from tools.schema import tool_fail
from skill_runtime import (
    MAX_SKILL_BODIES_PER_TASK,
    begin_agent_skill_turn,
    clear_skill_index_cache_for_tests,
    get_skill_registry,
    invalidate_skill_index_cache,
    prepare_skill_turn,
)
from tests.isolation_helpers import make_temp_agent_paths


_AGENT_CORE = Path(__file__).resolve().parents[1]
_REPO_SKILLS = Path(__file__).resolve().parents[2] / "evolve" / "skills"


class SkillRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_skill_index_cache_for_tests()
        self.root = Path(tempfile.mkdtemp(prefix="skill-runtime-"))

    def tearDown(self) -> None:
        clear_skill_index_cache_for_tests()
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, name: str, body: str) -> None:
        folder = self.root / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(body, encoding="utf-8")

    def test_catalog_always_present_without_full_body_match(self) -> None:
        self._write(
            "alpha-skill",
            """---
name: alpha-skill
description: Alpha workflow.
triggers: [alpha-trigger]
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
---

ALPHA_BODY_SHOULD_NOT_AUTOLOAD
""",
        )
        paths = make_temp_agent_paths(self, prefix="skill-runtime-catalog-")
        shutil.copytree(self.root, paths.evolve / "skills")
        session = create_new(paths, conversation_id="skill-catalog")
        session.messages = [{"role": "user", "content": "今天天气不错"}]
        begin_agent_skill_turn(session, session.messages[0]["content"])
        loaded = build_system_prompt(session, paths=paths, agent_core_dir=_AGENT_CORE)
        self.assertIn("skill_catalog", loaded.section_names)
        self.assertIn("alpha-skill", loaded.prompt)
        self.assertIn("alpha-trigger", loaded.prompt)
        self.assertNotIn("ALPHA_BODY_SHOULD_NOT_AUTOLOAD", loaded.prompt)
        self.assertNotIn("skill_context", loaded.section_names)

    def test_continuation_reuses_loaded_skill(self) -> None:
        self._write(
            "project-verification",
            """---
name: project-verification
description: Verification workflow.
triggers: [验收]
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
---

VERIFY_BODY_MARKER
""",
        )
        paths = make_temp_agent_paths(self, prefix="skill-runtime-cont-")
        shutil.copytree(self.root, paths.evolve / "skills")
        session = create_new(paths, conversation_id="skill-cont")
        begin_agent_skill_turn(session, "请跑验收并给证据")
        self.assertIn("project-verification", session._skill_runtime.loaded)
        begin_agent_skill_turn(session, "可以")
        self.assertIn("VERIFY_BODY_MARKER", build_system_prompt(session, paths=paths).prompt)

    def test_task_reset_drops_loaded_skills(self) -> None:
        self._write(
            "project-verification",
            """---
name: project-verification
description: Verification workflow.
triggers: [验收]
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
---

VERIFY_BODY_MARKER
""",
        )
        paths = make_temp_agent_paths(self, prefix="skill-runtime-reset-")
        shutil.copytree(self.root, paths.evolve / "skills")
        session = create_new(paths, conversation_id="skill-reset")
        begin_agent_skill_turn(session, "请跑验收")
        self.assertTrue(session._skill_runtime.loaded)
        begin_agent_skill_turn(session, "新任务：帮我整理文档结构")
        self.assertFalse(session._skill_runtime.loaded)

    def test_explicit_skill_required_blocks_on_missing(self) -> None:
        paths = make_temp_agent_paths(self, prefix="skill-runtime-explicit-")
        shutil.copytree(_REPO_SKILLS, paths.evolve / "skills")
        session = create_new(paths, conversation_id="skill-explicit")
        block = begin_agent_skill_turn(session, "请使用 skill missing-skill 处理")
        self.assertIsNotNone(block)
        self.assertTrue("未找到" in block or "unknown skill" in block)

    def test_multi_skill_budget(self) -> None:
        for name, trigger in (("skill-a", "alpha"), ("skill-b", "beta")):
            self._write(
                name,
                f"""---
name: {name}
description: {name}
triggers: [{trigger}]
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
---

BODY_{name}
""",
            )
        state, block = prepare_skill_turn(
            state=None,
            user_text="alpha beta combined work",
            skills_root=self.root,
            scope="agent",
        )
        self.assertIsNone(block)
        self.assertLessEqual(len(state.loaded), MAX_SKILL_BODIES_PER_TASK)

    def test_disk_change_invalidates_loaded_body(self) -> None:
        self._write(
            "refresh-skill",
            """---
name: refresh-skill
description: Refresh.
triggers: [refresh-trigger]
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
---

OLD_BODY
""",
        )
        registry = get_skill_registry(self.root)
        state, _ = prepare_skill_turn(
            state=None,
            user_text="refresh-trigger please",
            skills_root=self.root,
            scope="agent",
        )
        self.assertIn("OLD_BODY", state.loaded["refresh-skill"].selection.package.body)
        path = self.root / "refresh-skill" / "SKILL.md"
        path.write_text(path.read_text(encoding="utf-8").replace("OLD_BODY", "NEW_BODY"), encoding="utf-8")
        invalidate_skill_index_cache(self.root)
        state2, _ = prepare_skill_turn(
            state=state,
            user_text="可以",
            skills_root=self.root,
            scope="agent",
        )
        for entry in state2.loaded.values():
            self.assertNotIn("OLD_BODY", entry.selection.package.body)

    def test_explicit_skill_revoked_after_file_change_blocks_continue(self) -> None:
        self._write(
            "explicit-refresh",
            """---
name: explicit-refresh
description: Explicit.
triggers: []
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
---

EXPLICIT_OLD
""",
        )
        paths = make_temp_agent_paths(self, prefix="skill-explicit-stale-")
        shutil.copytree(self.root, paths.evolve / "skills")
        session = create_new(paths, conversation_id="skill-explicit-stale")
        session.meta.project_active_task_id = "wi-001"
        block0 = begin_agent_skill_turn(session, "请使用 skill explicit-refresh")
        self.assertIsNone(block0)
        path = paths.evolve / "skills" / "explicit-refresh" / "SKILL.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace("EXPLICIT_OLD", "EXPLICIT_NEW"),
            encoding="utf-8",
        )
        invalidate_skill_index_cache(paths.evolve / "skills")
        block = begin_agent_skill_turn(session, "继续")
        self.assertIsNotNone(block)
        self.assertIn("失效", block)
        self.assertNotIn("EXPLICIT_OLD", block)

    def test_deleted_skill_file_revokes_loaded_body(self) -> None:
        self._write(
            "gone-skill",
            """---
name: gone-skill
description: Gone.
triggers: [gone-trigger]
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
---

GONE_BODY
""",
        )
        paths = make_temp_agent_paths(self, prefix="skill-gone-")
        shutil.copytree(self.root, paths.evolve / "skills")
        session = create_new(paths, conversation_id="skill-gone")
        block0 = begin_agent_skill_turn(session, "请使用 skill gone-skill")
        self.assertIsNone(block0)
        skill_path = paths.evolve / "skills" / "gone-skill" / "SKILL.md"
        skill_path.unlink()
        invalidate_skill_index_cache(paths.evolve / "skills")
        block = begin_agent_skill_turn(session, "好的")
        self.assertIsNotNone(block)
        self.assertIn("失效", block)

    def test_two_sessions_do_not_share_skill_runtime(self) -> None:
        self._write(
            "iso-skill",
            """---
name: iso-skill
description: Iso.
triggers: [iso]
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
---

ISO_BODY
""",
        )
        paths_a = make_temp_agent_paths(self, prefix="skill-iso-a-")
        paths_b = make_temp_agent_paths(self, prefix="skill-iso-b-")
        shutil.copytree(self.root, paths_a.evolve / "skills")
        shutil.copytree(self.root, paths_b.evolve / "skills")
        session_a = create_new(paths_a, conversation_id="iso-a")
        session_b = create_new(paths_b, conversation_id="iso-b")
        begin_agent_skill_turn(session_a, "iso 请加载")
        self.assertTrue(session_a._skill_runtime.loaded)
        block_b = begin_agent_skill_turn(session_b, "可以")
        self.assertIsNone(block_b)
        self.assertFalse(session_b._skill_runtime.loaded)

    def test_same_session_two_work_items_reset_loaded(self) -> None:
        self._write(
            "wi-skill",
            """---
name: wi-skill
description: WI.
triggers: [wi-trigger]
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
---

WI_BODY
""",
        )
        paths = make_temp_agent_paths(self, prefix="skill-wi-")
        shutil.copytree(self.root, paths.evolve / "skills")
        session = create_new(paths, conversation_id="skill-wi")
        session.meta.project_active_task_id = "wi-first"
        begin_agent_skill_turn(session, "wi-trigger 任务一")
        self.assertIn("wi-skill", session._skill_runtime.loaded)
        session.meta.project_active_task_id = "wi-second"
        begin_agent_skill_turn(session, "继续第二步")
        self.assertEqual(session._skill_runtime.task_binding, "work_item:wi-second")
        self.assertNotIn("wi-skill", session._skill_runtime.loaded)

    def test_worker_suffix_isolated_between_tasks(self) -> None:
        self._write(
            "task-a-skill",
            """---
name: task-a-skill
description: Task A.
triggers: [alpha-task]
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
audiences: [worker]
---

ALPHA_BODY
""",
        )
        self._write(
            "task-b-skill",
            """---
name: task-b-skill
description: Task B.
triggers: [beta-task]
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
audiences: [worker]
---

BETA_BODY
""",
        )
        from skill_runtime import build_worker_skill_prompt_suffix

        suffix_a = build_worker_skill_prompt_suffix(self.root, "alpha-task do work", "proj-x")
        suffix_b = build_worker_skill_prompt_suffix(self.root, "beta-task do work", "proj-x")
        self.assertIn("ALPHA_BODY", suffix_a)
        self.assertNotIn("BETA_BODY", suffix_a)
        self.assertIn("BETA_BODY", suffix_b)
        self.assertNotIn("ALPHA_BODY", suffix_b)
        self.assertNotIn("[Skill 目录]", suffix_a)

    def test_index_cache_reduces_skill_file_reads(self) -> None:
        self._write(
            "count-skill",
            """---
name: count-skill
description: Count reads.
triggers: [count]
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
---

BODY
""",
        )
        from skill_runtime import clear_skill_index_cache_for_tests, get_skill_registry

        clear_skill_index_cache_for_tests()
        reset_skill_file_read_count()
        root = self.root
        get_skill_registry(root)
        first_reads = skill_file_read_count()
        get_skill_registry(root)
        second_reads = skill_file_read_count()
        self.assertGreater(first_reads, 0)
        self.assertEqual(first_reads, second_reads)

    def test_continuation_with_extra_detail_keeps_task(self) -> None:
        self._write(
            "hold-skill",
            """---
name: hold-skill
description: Hold.
triggers: [hold-trigger]
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
---

HOLD_BODY
""",
        )
        paths = make_temp_agent_paths(self, prefix="skill-cont-detail-")
        shutil.copytree(self.root, paths.evolve / "skills")
        session = create_new(paths, conversation_id="skill-cont-detail")
        begin_agent_skill_turn(session, "请按 hold-trigger 做第一步")
        first_id = session._skill_runtime.task_id
        begin_agent_skill_turn(
            session,
            "可以，另外请把验收标准写进 README，但不要换话题",
        )
        self.assertEqual(session._skill_runtime.task_id, first_id)
        self.assertIn("hold-skill", session._skill_runtime.loaded)

    def test_short_unrelated_message_without_reset_keeps_task(self) -> None:
        self._write(
            "sticky-skill",
            """---
name: sticky-skill
description: Sticky.
triggers: [sticky]
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
---

STICKY_BODY
""",
        )
        paths = make_temp_agent_paths(self, prefix="skill-sticky-")
        shutil.copytree(self.root, paths.evolve / "skills")
        session = create_new(paths, conversation_id="skill-sticky")
        begin_agent_skill_turn(session, "sticky 工作开始")
        task_id = session._skill_runtime.task_id
        begin_agent_skill_turn(session, "修一下 typo")
        self.assertEqual(session._skill_runtime.task_id, task_id)

    def test_project_active_task_id_resets_binding(self) -> None:
        paths = make_temp_agent_paths(self, prefix="skill-meta-task-")
        shutil.copytree(self.root, paths.evolve / "skills")
        session = create_new(paths, conversation_id="skill-meta")
        begin_agent_skill_turn(session, "sticky 工作开始")
        first_binding = session._skill_runtime.task_binding
        session.meta.project_active_task_id = "task-001"
        begin_agent_skill_turn(session, "sticky 工作开始")
        first_binding = session._skill_runtime.task_binding
        session.meta.project_active_task_id = "task-002"
        begin_agent_skill_turn(session, "继续")
        self.assertEqual(session._skill_runtime.task_binding, "work_item:task-002")
        self.assertNotEqual(session._skill_runtime.task_binding, first_binding)

    def test_worker_catalog_excludes_agent_only_skills(self) -> None:
        self._write(
            "agent-only",
            """---
name: agent-only
description: Agent.
triggers: [agent-only]
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
audiences: [agent]
---

secret agent body
""",
        )
        self._write(
            "worker-visible",
            """---
name: worker-visible
description: Worker.
triggers: [worker-visible]
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
audiences: [worker]
---

worker body
""",
        )
        registry = get_skill_registry(self.root)
        from skill_runtime import build_worker_skill_prompt_suffix

        suffix = build_worker_skill_prompt_suffix(self.root, "worker-visible please", "proj-a")
        self.assertIn("worker-visible", suffix)
        self.assertNotIn("secret agent body", suffix)
        self.assertNotIn("agent-only", suffix)

    def test_load_workflow_skill_tool_loads_by_name(self) -> None:
        self._write(
            "tool-load",
            """---
name: tool-load
description: Tool load.
triggers: []
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
---

TOOL_LOAD_BODY
""",
        )
        paths = make_temp_agent_paths(self, prefix="skill-tool-")
        shutil.copytree(self.root, paths.evolve / "skills")
        session = create_new(paths, conversation_id="skill-tool")
        result = load_workflow_skill.run({"skill_id": "tool-load"}, paths=paths, agent_session=session)
        self.assertTrue(result.ok)
        self.assertIn("tool-load", session._skill_runtime.loaded)

    def test_explicit_missing_skill_blocks_turn(self) -> None:
        folder = self.root / "broken-skill"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text("no frontmatter\n", encoding="utf-8")
        clear_skill_index_cache_for_tests()
        paths = make_temp_agent_paths(self, prefix="skill-broken-")
        shutil.copytree(self.root, paths.evolve / "skills")
        session = create_new(paths, conversation_id="skill-broken")
        block = begin_agent_skill_turn(session, "请使用 skill broken-skill")
        self.assertIsNotNone(block)
        self.assertTrue("未找到" in block or "unknown skill" in block)

    def test_total_body_char_budget_blocks_explicit_load(self) -> None:
        huge = "x" * (32_000 + 100)
        self._write(
            "huge-skill",
            f"""---
name: huge-skill
description: Huge.
triggers: []
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
---

{huge}
""",
        )
        paths = make_temp_agent_paths(self, prefix="skill-huge-")
        shutil.copytree(self.root, paths.evolve / "skills")
        session = create_new(paths, conversation_id="skill-huge")
        block = begin_agent_skill_turn(session, "请使用 skill huge-skill")
        self.assertIsNotNone(block)
        self.assertIn("字符预算", block)

    def test_ambiguous_display_name_blocks_explicit_turn(self) -> None:
        for folder in ("dup-one", "dup-two"):
            self._write(
                folder,
                f"""---
name: colliding-name
description: {folder}.
triggers: []
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
---

body-{folder}
""",
            )
        clear_skill_index_cache_for_tests()
        paths = make_temp_agent_paths(self, prefix="skill-dup-")
        shutil.copytree(self.root, paths.evolve / "skills")
        session = create_new(paths, conversation_id="skill-dup")
        block = begin_agent_skill_turn(session, "请使用 skill colliding-name")
        self.assertIsNotNone(block)
        self.assertIn("歧义", block)

    def test_load_body_increments_read_count(self) -> None:
        self._write(
            "read-on-load",
            """---
name: read-on-load
description: Read on load.
triggers: []
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
---

BODY
""",
        )
        clear_skill_index_cache_for_tests()
        reset_skill_file_read_count()
        registry = get_skill_registry(self.root)
        index_reads = skill_file_read_count()
        registry.load("read-on-load")
        self.assertGreater(skill_file_read_count(), index_reads)


if __name__ == "__main__":
    unittest.main()