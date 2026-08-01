"""Phase 23 M5 — doc cross-links + regression pack (S-80～82 · IT-80～81)."""

from __future__ import annotations

import secrets
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_ROOT = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from loader import session_evolved_allowlist
from session import create_new
from tools.registry import ToolRegistry

from tests.isolation_helpers import make_temp_agent_paths

_CATALOG_MODULES = (
    "tests.test_tool_catalog_m1",
    "tests.test_tool_catalog_m2",
    "tests.test_tool_catalog_m3",
    "tests.test_tool_catalog_m4",
    "tests.test_tool_catalog_mp",
    "tests.test_tool_catalog_mq",
    "tests.test_tool_catalog_mr",
)


class ToolCatalogM5DocTests(unittest.TestCase):
    """T-2360: TOOLS / MEMORY point at TOOL-CATALOG; hard-lock language superseded."""

    def test_tools_and_memory_cross_links(self) -> None:
        tools = (_ROOT / "docs" / "TOOLS.md").read_text(encoding="utf-8")
        memory = (_ROOT / "docs" / "MEMORY.md").read_text(encoding="utf-8")
        catalog = (_ROOT / "docs" / "TOOL-CATALOG.md").read_text(encoding="utf-8")
        self.assertIn("TOOL-CATALOG.md", tools)
        self.assertIn("superseded", tools.casefold())
        self.assertIn("TOOL-CATALOG.md", memory)
        self.assertIn("Phase 23", catalog)


class ToolCatalogM5It81Tests(unittest.TestCase):
    """IT-81: project-bound and default sessions share the same active allowlist."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(
            self,
            copy_tool_dirs=(
                "common/write_text",
                "coding/patch_file",
                "project/report_progress",
            ),
        )
        self.registry = ToolRegistry.load(self.paths)

    def test_it81_project_and_default_allowlist_equal(self) -> None:
        plain = create_new(
            self.paths,
            conversation_id=f"_m5_plain_{secrets.token_hex(3)}",
        )
        plain.meta.topics = []
        plain.meta.active_shell = "grow"
        plain.save()

        project = create_new(
            self.paths,
            conversation_id=f"_m5_proj_{secrets.token_hex(3)}",
        )
        project.meta.topics = ["coding"]
        project.meta.active_shell = "project"
        project.meta.project_root = "workspace/demo"
        project.meta.project_id = "demo"
        project.save()

        a = session_evolved_allowlist(plain, registry=self.registry)
        b = session_evolved_allowlist(project, registry=self.registry)
        self.assertEqual(a, b)
        self.assertIn("patch_file", a)
        self.assertIn("report_progress", a)


class ToolCatalogM5PackTests(unittest.TestCase):
    """T-2361: load prior Phase 23 slice tests (S-80～82 covered in m1/m2/m3)."""

    def test_load_prior_slices(self) -> None:
        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        for mod in _CATALOG_MODULES:
            suite.addTests(loader.loadTestsFromName(mod))
        result = unittest.TextTestRunner(verbosity=0).run(suite)
        self.assertTrue(
            result.wasSuccessful(),
            msg=f"Phase 23 pack failures={len(result.failures)} errors={len(result.errors)}",
        )


if __name__ == "__main__":
    unittest.main()
