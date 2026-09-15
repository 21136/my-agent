# -*- coding: utf-8 -*-
"""Desktop project/session switch chrome must stay above the rail."""

from __future__ import annotations

import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PANEL = _ROOT / "desktop" / "src" / "shells" / "unified" / "project-panel.ts"
_INDEX = _ROOT / "desktop" / "src" / "shells" / "unified" / "index.ts"
_CSS = _ROOT / "desktop" / "src" / "shells" / "unified" / "unified.css"


class DesktopProjectSwitchChromeTests(unittest.TestCase):
    def test_compat_switch_card_stays_hidden(self) -> None:
        panel = _PANEL.read_text(encoding="utf-8")
        self.assertIn('els.switchCard.classList.add("hidden")', panel)
        self.assertNotIn("switchCard.classList.remove", panel)
        css = _CSS.read_text(encoding="utf-8")
        self.assertIn("#project-switch-card", css)
        self.assertIn("display: none !important", css)

    def test_switch_confirm_renders_in_sidebar_body(self) -> None:
        panel = _PANEL.read_text(encoding="utf-8")
        self.assertIn("function renderRailSidebarBody", panel)
        body_start = panel.index("function renderRailSidebarBody")
        body = panel[body_start:body_start + 900]
        self.assertIn("state.switchOverlay", body)
        self.assertIn("renderSwitchConfirmHtml", body)
        self.assertIn("renderSwitchLoadingHtml", body)

    def test_atomic_switch_entry_exists(self) -> None:
        panel = _PANEL.read_text(encoding="utf-8")
        self.assertIn("export function beginAtomicProjectSwitch", panel)
        index = _INDEX.read_text(encoding="utf-8")
        self.assertIn("beginBoundSessionSwitch", index)
        self.assertIn("beginAtomicProjectSwitch(projectState, pid)", index)
        self.assertIn('client.switchProject(target, { confirm: true })', index)
        self.assertIn("unified-switch-mask", index)

    def test_rail_footer_stays_pinned(self) -> None:
        css = _CSS.read_text(encoding="utf-8")
        self.assertIn(".sidebar-footer", css)
        footer = css[css.index(".sidebar-footer") : css.index(".sidebar-footer") + 280]
        self.assertIn("margin-top: auto", footer)
        self.assertIn("flex-shrink: 0", footer)
        self.assertIn("z-index: 3", footer)


if __name__ == "__main__":
    unittest.main()
