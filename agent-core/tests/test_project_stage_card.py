"""IT-5819: stage card consumes artifact revisions and basis metadata."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ProjectStageCardContractTests(unittest.TestCase):
    def test_it5819_frontend_contract_keeps_artifact_and_basis_sections(self) -> None:
        panel = (ROOT / "desktop" / "src" / "shells" / "unified" / "project-panel.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn("executionStageArtifacts", panel)
        self.assertIn("renderStageArtifactSummary", panel)
        self.assertIn("open-artifact-doc", panel)
        self.assertIn("open-plan-review", panel)
        self.assertIn("DESIGN@", panel)
        self.assertIn("SCOPE@", panel)
        self.assertIn("映射完整度", panel)
        self.assertIn("证据新鲜度", panel)
        self.assertIn("人工验收", panel)

    def test_it5819_backend_exposes_artifact_ids_for_ac_coverage(self) -> None:
        project_api = (ROOT / "agent-core" / "project_api.py").read_text(encoding="utf-8")
        plan_agent = (ROOT / "agent-core" / "plan_agent.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(project_api.count('"ids": list(item.get("ids") or [])'), 1)
        self.assertGreaterEqual(plan_agent.count('"ids": list(item.get("ids") or [])'), 1)

    def test_it5820_review_button_has_visible_focus_transition(self) -> None:
        index = (ROOT / "desktop" / "src" / "shells" / "unified" / "index.ts").read_text(
            encoding="utf-8"
        )
        panel = (ROOT / "desktop" / "src" / "shells" / "unified" / "project-panel.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn('data-action="open-plan-review"', panel)
        self.assertIn('root.addEventListener("click", handleReviewSuggestionClick, true)', index)
        self.assertIn('ev.stopImmediatePropagation()', index)
        self.assertIn('planReviewEl.hidden = focus !== "plan_review"', index)

    def test_it5825_suggestion_card_uses_dedicated_review_action(self) -> None:
        index = (ROOT / "desktop" / "src" / "shells" / "unified" / "index.ts").read_text(
            encoding="utf-8"
        )
        panel = (ROOT / "desktop" / "src" / "shells" / "unified" / "project-panel.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn('data-action="open-suggestion-review"', panel)
        self.assertIn('[data-action="open-suggestion-review"]', index)
        self.assertIn('projectEls.taskFlow.addEventListener("click"', index)
        self.assertIn('openPlanReview(btn.dataset.suggestionId)', index)

    def test_it5826_suggestion_card_exposes_new_review_button(self) -> None:
        index = (ROOT / "desktop" / "src" / "shells" / "unified" / "index.ts").read_text(
            encoding="utf-8"
        )
        panel = (ROOT / "desktop" / "src" / "shells" / "unified" / "project-panel.ts").read_text(
            encoding="utf-8"
        )
        css = (ROOT / "desktop" / "src" / "shells" / "unified" / "unified.css").read_text(
            encoding="utf-8"
        )
        self.assertIn('data-action="open-suggestion-review-new"', panel)
        self.assertIn('[data-action="open-suggestion-review-new"]', index)
        self.assertIn('正在打开计划审阅', index)
        self.assertIn('计划审阅已打开', index)
        self.assertIn('正在读取待审阅提案', index)
        self.assertIn('正在切换主区', index)
        self.assertIn('正在渲染计划审阅', index)
        self.assertIn('planReviewEl.hidden = false', index)
        self.assertIn('let planReviewIndex = 0;', index)
        self.assertIn('[data-action="open-suggestion-review"]', css)

    def test_it5823_change_timeline_is_collapsed_until_requested(self) -> None:
        panel = (ROOT / "desktop" / "src" / "shells" / "unified" / "project-panel.ts").read_text(
            encoding="utf-8"
        )
        index = (ROOT / "desktop" / "src" / "shells" / "unified" / "index.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn("changeTimelineExpanded: boolean", panel)
        self.assertIn("state.changeTimelineExpanded ? ledgerRows : \"\"", panel)
        self.assertIn('data-action="toggle-change-timeline"', panel)
        self.assertIn('case "toggle-change-timeline":', index)


if __name__ == "__main__":
    unittest.main()
