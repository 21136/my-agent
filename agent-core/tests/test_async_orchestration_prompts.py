"""Pack 6 T-5601 — prompt / tool-catalog wait discipline (grep acceptance)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_ROOT = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

_RUN_MD = _ROOT / "evolve" / "tool-catalog" / "buckets" / "run.md"
_PROJECT_BOUNDARIES = _ROOT / "evolve" / "prompts" / "project-boundaries.md"
_CORE = _ROOT / "agent-core" / "prompts" / "core.txt"


class AsyncOrchestrationPromptTests(unittest.TestCase):
    """T-5601 · run.md + project-boundaries footnotes."""

    def test_run_md_wait_discipline(self) -> None:
        text = _RUN_MD.read_text(encoding="utf-8")
        self.assertIn("run_service", text)
        self.assertIn("`wait`", text)
        self.assertIn("同一回合", text)
        self.assertIn("ASYNC-ORCHESTRATION", text)

    def test_project_boundaries_orchestration_section(self) -> None:
        text = _PROJECT_BOUNDARIES.read_text(encoding="utf-8")
        self.assertIn("起服编排", text)
        self.assertIn("`wait`", text)
        self.assertIn("report_progress", text)

    def test_core_txt_no_long_orchestration_tutorial(self) -> None:
        core = _CORE.read_text(encoding="utf-8")
        self.assertNotIn("ASYNC-ORCHESTRATION", core)
        self.assertNotIn("多服务起服编排", core)

    def test_runaway_prompt_override_is_explicit_and_scoped(self) -> None:
        core = _CORE.read_text(encoding="utf-8")
        boundaries = _PROJECT_BOUNDARIES.read_text(encoding="utf-8")
        safety = (_ROOT / "evolve" / "prompts" / "safety.md").read_text(encoding="utf-8")
        coding = (_ROOT / "evolve" / "prompts" / "coding.md").read_text(encoding="utf-8")
        ritual = (_ROOT / "evolve" / "prompts" / "project-delivery-ritual.md").read_text(encoding="utf-8")
        self.assertIn("project runaway", core)
        self.assertIn("狂奔模式覆盖", boundaries)
        self.assertIn("仅当动态 overlay 明确", boundaries)
        self.assertIn("网络、宿主目录、敏感数据", safety)
        self.assertIn("狂奔模式已明确授权", coding)
        self.assertIn("本文件中的“用户采纳计划”", ritual)


if __name__ == "__main__":
    unittest.main()
