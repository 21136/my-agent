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
        # The stage/plan card that rendered these sections was removed with the
        # task subsystem (2026-10-18); the state still carries the artifacts.
        self.assertIn("executionStageArtifacts", panel)

    def test_it5819_backend_exposes_artifact_ids_for_ac_coverage(self) -> None:
        project_api = (ROOT / "agent-core" / "project_api.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(project_api.count('"ids": list(item.get("ids") or [])'), 1)

    def test_it5910a_frontend_contract_uses_shared_stage_status(self) -> None:
        panel = (ROOT / "desktop" / "src" / "shells" / "unified" / "project-panel.ts").read_text(
            encoding="utf-8"
        )
        ws = (ROOT / "desktop" / "src" / "api" / "ws.ts").read_text(encoding="utf-8")
        self.assertIn("executionStageStatus", panel)
        self.assertIn("executionStageWarnings", panel)
        self.assertIn("isDocumentationBatch", panel)
        self.assertIn("四个核心制品按一个批次处理", panel)
        self.assertIn("execution_stage_status", ws)
        self.assertIn("execution_stage_warnings", ws)
        self.assertIn("文档整理进行中；未生成文档属于进度，不是阻塞", panel)

    def test_it5913_mermaid_uses_shared_render_and_error_fallback_contract(self) -> None:
        markdown = (ROOT / "desktop" / "src" / "markdown.ts").read_text(encoding="utf-8")
        unified = (ROOT / "desktop" / "src" / "shells" / "unified" / "index.ts").read_text(
            encoding="utf-8"
        )
        pet = (ROOT / "desktop" / "src" / "shells" / "pet" / "index.ts").read_text(encoding="utf-8")
        self.assertIn("```mermaid", markdown)
        self.assertIn("mermaid-placeholder", markdown)
        self.assertIn("mermaid.render", markdown)
        self.assertIn("Mermaid 图表渲染失败，已保留源代码", markdown)
        self.assertIn("void hydrateMermaid(documentEl)", unified)
        self.assertIn("void hydrateMermaid(chatEl)", unified)
        self.assertIn("void hydrateMermaid(live)", unified)
        self.assertGreaterEqual(unified.count("void hydrateMermaid(live)"), 2)
        self.assertIn("void hydrateMermaid(chatEl)", pet)

    def test_it5914_documents_use_main_focus_instead_of_sidebar_body(self) -> None:
        index = (ROOT / "desktop" / "src" / "shells" / "unified" / "index.ts").read_text(
            encoding="utf-8"
        )
        panel = (ROOT / "desktop" / "src" / "shells" / "unified" / "project-panel.ts").read_text(
            encoding="utf-8"
        )
        css = (ROOT / "desktop" / "src" / "shells" / "unified" / "unified.css").read_text(
            encoding="utf-8"
        )
        rail = (ROOT / "desktop" / "src" / "shells" / "unified" / "rail.ts").read_text(
            encoding="utf-8"
        )
        reading = (ROOT / "desktop" / "src" / "shells" / "unified" / "doc-reading.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn('"document"', rail)
        self.assertIn('setMainFocus("document")', index)
        self.assertIn('data-action="document-back"', reading)
        self.assertIn("unified-document-content table", css)
        self.assertIn("unified-document-content .mermaid-placeholder", css)
        self.assertIn("unified-document-shell", css)
        self.assertIn("doc-outline-label", css)
        self.assertIn("min(72ch", css)
        self.assertIn('PROJECT.md": "项目说明"', reading)
        self.assertIn('TECH-DESIGN.md": "技术要点"', reading)
        self.assertIn("本页目录", reading)
        self.assertIn("文档标题，例如：需求分析", reading)
        self.assertIn("finalizeDocumentOutline", index)
        self.assertIn("normalizeNewDocPath", index)
        self.assertIn('chatEl.classList.toggle("hidden", !(focus === "chat" || splitDocs))', index)
        self.assertNotIn("data-main-focus=\"document\"] .unified-chat", css)
        self.assertIn("renderDocumentReaderHtml", index)
        self.assertIn("doc-outline-jump", index)
        self.assertNotIn("主区阅读", panel)
        self.assertIn("unified-document-kicker", reading)
        self.assertNotIn('textbook-artifact-completeness is-', panel)
        self.assertIn("ENV.md", reading)
        self.assertIn("环境与质量", reading)
        self.assertLess(reading.index("PROJECT.md"), reading.index("SCOPE.md"))
        self.assertLess(reading.index("TASKS.md"), reading.index("VERIFY.md"))
        self.assertLess(reading.index("VERIFY.md"), reading.index("ENV.md"))

    def test_it5823_change_timeline_is_not_rendered(self) -> None:
        """CHG ledger stays internal. The sidebar must not render it."""
        panel = (ROOT / "desktop" / "src" / "shells" / "unified" / "project-panel.ts").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("CHG 影响时间线", panel)
        self.assertNotIn("sidebar-change-timeline", panel)
        self.assertNotIn('data-action="toggle-change-timeline"', panel)
        self.assertNotIn("ledgerRows", panel)


if __name__ == "__main__":
    unittest.main()
