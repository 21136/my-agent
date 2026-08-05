"""Tests for Phase 43 scaffold recipes (IT-431～435)."""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from project_mode import create_project
from scaffold_recipes import (
    ScaffoldRecipeError,
    list_recipes,
    load_recipe_manifest,
    render_template_text,
    run_scaffold_project,
)
from tests.isolation_helpers import temporary_agent_paths
from tools.registry import ToolRegistry


def _copy_spring_vue(paths: AgentPaths) -> None:
    live = AgentPaths.discover()
    src = live.evolve / "scaffolds" / "spring-vue"
    dst = paths.evolve / "scaffolds" / "spring-vue"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)


class ScaffoldRecipeTests(unittest.TestCase):
    def test_list_and_load_spring_vue(self) -> None:
        with temporary_agent_paths() as paths:
            _copy_spring_vue(paths)
            recipes = list_recipes(paths)
            self.assertIn("spring-vue", recipes)
            manifest = load_recipe_manifest(paths, "spring-vue")
            self.assertEqual(manifest.id, "spring-vue")
            steps = manifest.phase_steps("init")
            self.assertGreaterEqual(len(steps), 2)
            self.assertEqual(steps[0].get("kind"), "template_tree")

    def test_it431_dry_run_lists_steps(self) -> None:
        with temporary_agent_paths() as paths:
            _copy_spring_vue(paths)
            target = paths.workspace / "scaffold-demo"
            target.mkdir(parents=True, exist_ok=True)
            result = run_scaffold_project(
                paths,
                recipe="spring-vue",
                target_dir="workspace/scaffold-demo",
                dry_run=True,
            )
            self.assertTrue(result.get("ok"), result)
            self.assertTrue(result.get("dry_run"))
            steps = result.get("steps_run") or []
            self.assertGreaterEqual(len(steps), 1)
            self.assertTrue(all(s.get("ok") for s in steps))

    def test_it432_template_tree_writes_files(self) -> None:
        with temporary_agent_paths() as paths:
            _copy_spring_vue(paths)
            target = paths.workspace / "spring-demo"
            target.mkdir(parents=True, exist_ok=True)
            result = run_scaffold_project(
                paths,
                recipe="spring-vue",
                target_dir="workspace/spring-demo",
                variables={"project_name": "spring-demo"},
                stop_on_error=True,
            )
            # layout + env must succeed; optional mvn/npm may skip
            self.assertTrue(result.get("ok"), result)
            self.assertTrue((target / "backend" / "pom.xml").is_file())
            self.assertTrue((target / "frontend" / "package.json").is_file())
            pom = (target / "backend" / "pom.xml").read_text(encoding="utf-8")
            self.assertIn("spring-demo-backend", pom)
            layout_step = next(s for s in result["steps_run"] if s["id"] == "layout")
            self.assertTrue(layout_step.get("ok"))

    def test_it433_unknown_recipe(self) -> None:
        with temporary_agent_paths() as paths:
            result = run_scaffold_project(
                paths,
                recipe="no-such-recipe",
                target_dir="workspace/x",
                dry_run=True,
            )
            self.assertFalse(result.get("ok"))
            self.assertIn("unknown recipe", result.get("error", ""))

    def test_it434_invalid_variable_pattern(self) -> None:
        with temporary_agent_paths() as paths:
            _copy_spring_vue(paths)
            result = run_scaffold_project(
                paths,
                recipe="spring-vue",
                target_dir="workspace/bad-name",
                variables={"project_name": "Bad_Name"},
                dry_run=True,
            )
            self.assertFalse(result.get("ok"))
            self.assertIn("pattern", result.get("error", ""))

    def test_it435_create_project_with_template(self) -> None:
        with temporary_agent_paths() as paths:
            _copy_spring_vue(paths)
            dest = create_project(paths, "tpl-demo", template="spring-vue")
            self.assertTrue((dest / "backend" / "pom.xml").is_file())
            self.assertTrue((dest / "frontend" / "package.json").is_file())

    def test_registry_loads_scaffold_project(self) -> None:
        live = AgentPaths.discover()
        registry = ToolRegistry.load(live)
        tool = registry.get_evolved("scaffold_project")
        self.assertIsNotNone(tool)
        assert tool is not None
        self.assertEqual(tool.status, "active")
        self.assertEqual(tool.scope, "project")

    def test_fastapi_vue_template_tree(self) -> None:
        with temporary_agent_paths() as paths:
            live = AgentPaths.discover()
            for recipe in ("spring-vue", "fastapi-vue"):
                src = live.evolve / "scaffolds" / recipe
                dst = paths.evolve / "scaffolds" / recipe
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            result = run_scaffold_project(
                paths,
                recipe="fastapi-vue",
                target_dir="workspace/fastapi-demo",
                variables={"project_name": "fastapi-demo"},
            )
            self.assertTrue(result.get("ok"), result)
            target = paths.workspace / "fastapi-demo"
            self.assertTrue((target / "backend" / "main.py").is_file())
            main_py = (target / "backend" / "main.py").read_text(encoding="utf-8")
            self.assertIn("fastapi-demo", main_py)

    def test_spring_vue_deploy_phase_dry_run(self) -> None:
        with temporary_agent_paths() as paths:
            _copy_spring_vue(paths)
            result = run_scaffold_project(
                paths,
                recipe="spring-vue",
                target_dir="workspace/deploy-demo",
                phase="deploy",
                dry_run=True,
            )
            self.assertTrue(result.get("ok"), result)
            self.assertEqual(result.get("phase"), "deploy")

    def test_render_template_text(self) -> None:
        out = render_template_text("hello {{project_name}}", {"project_name": "demo"})
        self.assertEqual(out, "hello demo")
        with self.assertRaises(ScaffoldRecipeError):
            render_template_text("{{missing}}", {})

    def test_it436_manifests_use_run_command_not_archived_exec(self) -> None:
        """IT-436: recipe manifests must not reference archived npm_exec/mvn_exec kinds."""
        live = AgentPaths.discover()
        banned = frozenset({"npm_exec", "mvn_exec"})
        for recipe_id in ("spring-vue", "fastapi-vue"):
            manifest = load_recipe_manifest(live, recipe_id)
            for phase in ("init", "deploy"):
                for step in manifest.phase_steps(phase):
                    kind = str(step.get("kind") or "")
                    self.assertNotIn(
                        kind,
                        banned,
                        f"{recipe_id} phase {phase} step {step.get('id')!r} uses {kind!r}",
                    )
                    if kind == "run_command":
                        self.assertTrue(
                            str(step.get("command") or "").strip(),
                            f"{recipe_id} run_command step missing command",
                        )

    def test_it436_deprecated_step_kind_rejected(self) -> None:
        with temporary_agent_paths() as paths:
            _copy_spring_vue(paths)
            manifest_path = paths.evolve / "scaffolds" / "spring-vue" / "manifest.json"
            import json

            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            steps = raw["phases"]["init"]["steps"]
            steps.append(
                {
                    "id": "bad_npm",
                    "kind": "npm_exec",
                    "args": ["install"],
                    "working_dir": "frontend",
                }
            )
            manifest_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
            result = run_scaffold_project(
                paths,
                recipe="spring-vue",
                target_dir="workspace/bad-recipe",
                dry_run=True,
                stop_on_error=False,
            )
            bad = next(s for s in result["steps_run"] if s["id"] == "bad_npm")
            self.assertFalse(bad.get("ok"))
            self.assertIn("archived", str(bad.get("error") or "").casefold())


if __name__ == "__main__":
    unittest.main()
