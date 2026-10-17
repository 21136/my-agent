from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from skill_registry import (
    DirectorySkillSource,
    SkillRegistry,
    SkillRegistryError,
)


class SkillRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="skills-registry-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def _write(self, name: str, text: str) -> Path:
        folder = self.root / name
        folder.mkdir(parents=True)
        path = folder / "SKILL.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_discover_parses_metadata_and_loads_body_on_demand(self) -> None:
        self._write(
            "project-verification",
            """---
name: project-verification
description: Run project tests and collect evidence.
triggers:
  - 验收
  - verify
allowed_tools:
  - run_command
risk_level: low
verification: exit code and saved evidence
version: 1.0.0
---

# Verification workflow

Run the configured checks and report actual output.
""",
        )
        registry = SkillRegistry.discover(self.root)
        self.assertEqual([item.name for item in registry.skills], ["project-verification"])
        self.assertEqual(registry.select("请跑验收", limit=1)[0].name, "project-verification")
        package = registry.load("project-verification")
        self.assertIn("actual output", package.body)
        self.assertIn("run_command", package.descriptor.allowed_tools)

    def test_invalid_skill_isolated_in_diagnostics(self) -> None:
        self._write("broken", "not frontmatter")
        registry = SkillRegistry.discover(self.root)
        self.assertEqual(registry.skills, ())
        self.assertEqual(len(registry.diagnostics), 1)

    def test_index_is_compact_and_context_is_bounded(self) -> None:
        self._write(
            "debugging",
            """---
name: debugging
description: Debug failures systematically.
triggers: [bug, 报错]
allowed_tools: [read_file, run_command]
risk_level: medium
verification: regression test
version: 1.0.0
---

""" + ("x" * 1000),
        )
        registry = SkillRegistry.discover(self.root, max_body_bytes=200)
        index = registry.format_index()
        self.assertIn("debugging", index)
        self.assertIn("按需加载", index)
        with self.assertRaises(SkillRegistryError):
            registry.load("debugging")

    def test_first_party_project_workflow_skills_are_discoverable(self) -> None:
        registry = SkillRegistry.discover(Path("evolve/skills"))
        expected = {
            "project-intake",
            "requirements-to-spec",
            "implementation-plan",
            "code-review",
            "resume-incomplete-work",
            "release-readiness",
        }
        self.assertTrue(expected.issubset({item.name for item in registry.skills}))
        verification = next(item for item in registry.skills if item.name == "project-verification")
        self.assertEqual(verification.status, "stable")
        self.assertEqual(verification.compatibility_models, ("0x567-flash",))
        self.assertEqual(verification.capabilities, ("inspect_files", "run_tests"))
        self.assertEqual(registry.select("项目写了一半，继续接着做", limit=1)[0].name, "resume-incomplete-work")
        self.assertEqual(registry.select("审查代码", limit=1)[0].name, "code-review")
        self.assertEqual(registry.select("准备发布前检查", limit=1)[0].name, "release-readiness")

    def test_load_rejects_path_escape_and_unknown_skill(self) -> None:
        registry = SkillRegistry.discover(self.root)
        with self.assertRaises(SkillRegistryError):
            registry.load("../outside")
        with self.assertRaises(SkillRegistryError):
            registry.load("missing")



    def test_registry_from_sources_is_deterministic_first_source_wins(self) -> None:
        first_root = self.root / "first"
        second_root = self.root / "second"
        first_root.mkdir()
        second_root.mkdir()
        for root, version in ((first_root, "1.0.0"), (second_root, "2.0.0")):
            folder = root / "project-verification"
            folder.mkdir()
            (folder / "SKILL.md").write_text(
                f"""---
name: project-verification
description: Source {version}.
triggers: [验收]
allowed_tools: [run_command]
risk_level: low
verification: exit code
version: {version}
---

Source {version}.
""",
                encoding="utf-8",
            )

        registry = SkillRegistry.from_sources(
            (
                DirectorySkillSource(first_root, source_id="first"),
                DirectorySkillSource(second_root, source_id="second"),
            )
        )

        selected = registry.select_packages("请跑验收", limit=1)[0]
        self.assertEqual(selected.identity.source_id, "first")
        self.assertEqual(selected.identity.version, "1.0.0")

    def test_optional_metadata_is_parsed_for_audit(self) -> None:
        self._write(
            "metadata-skill",
            """---
name: metadata-skill
description: Skill with compatibility metadata.
triggers: [metadata]
allowed_tools: [read_file]
risk_level: low
verification: inspect output
version: 2.0.0
api_version: 1
status: stable
compatibility:
  runtime: ">=0.2.0"
  models:
    - 0x567-flash
capabilities:
  - inspect_files
  - run_tests
---

Metadata body.
""",
        )

        descriptor = SkillRegistry.discover(self.root).skills[0]

        self.assertEqual(descriptor.api_version, "1")
        self.assertEqual(descriptor.status, "stable")
        self.assertEqual(descriptor.runtime_min_version, ">=0.2.0")
        self.assertEqual(descriptor.compatibility_models, ("0x567-flash",))
        self.assertEqual(descriptor.capabilities, ("inspect_files", "run_tests"))

    def test_directory_source_exposes_stable_identity(self) -> None:
        self._write(
            "project-verification",
            """---
name: project-verification
description: Run project tests and collect evidence.
triggers: [验收]
allowed_tools: [run_command]
risk_level: low
verification: exit code
version: 1.0.0
---

Run the configured checks.
""",
        )

        source = DirectorySkillSource(self.root)
        packages, diagnostics = source.discover()

        self.assertEqual(diagnostics, ())
        self.assertEqual(len(packages), 1)
        descriptor = packages[0].descriptor
        self.assertEqual(descriptor.source_id, "builtin-directory")
        self.assertEqual(len(descriptor.content_hash), 64)
        self.assertEqual(descriptor.content_hash, packages[0].identity.content_hash)

    def test_package_selection_contains_identity_and_hash_changes(self) -> None:
        path = self._write(
            "project-verification",
            """---
name: project-verification
description: Run project tests and collect evidence.
triggers: [验收]
allowed_tools: [run_command]
risk_level: low
verification: exit code
version: 1.0.0
---

Run the configured checks.
""",
        )
        registry = SkillRegistry.discover(self.root)
        selected = registry.select_packages("请跑验收", limit=1)

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].descriptor.name, "project-verification")
        self.assertEqual(selected[0].identity.version, "1.0.0")
        original_hash = selected[0].identity.content_hash

        path.write_text(path.read_text(encoding="utf-8") + "\nupdated\n", encoding="utf-8")
        updated = DirectorySkillSource(self.root).load("project-verification")
        self.assertNotEqual(updated.identity.content_hash, original_hash)


if __name__ == "__main__":
    unittest.main()

