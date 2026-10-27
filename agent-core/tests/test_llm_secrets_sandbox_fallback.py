"""Sandbox LLM secrets fall back to the stable product store."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from llm_secrets import (
    get_llm_secret,
    resolve_secret_source,
    set_llm_secret,
    stable_product_root_for_sandbox,
)
from paths import AgentPaths


def _make_agent_root(root: Path) -> None:
    (root / "evolve").mkdir(parents=True, exist_ok=True)
    (root / "evolve" / "_index.core.toml").write_text("[core]\n", encoding="utf-8")
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "workspace").mkdir(parents=True, exist_ok=True)


class SandboxSecretsFallbackTests(unittest.TestCase):
    def test_stable_product_root_for_registered_layout(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            stable = Path(raw) / "product"
            sandbox = stable / ".worktrees" / "sandbox"
            _make_agent_root(stable)
            _make_agent_root(sandbox)
            self.assertEqual(stable_product_root_for_sandbox(sandbox), stable.resolve())
            self.assertIsNone(stable_product_root_for_sandbox(stable))

    def test_get_llm_secret_falls_back_to_stable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            stable = Path(raw) / "product"
            sandbox = stable / ".worktrees" / "sandbox"
            _make_agent_root(stable)
            _make_agent_root(sandbox)
            stable_paths = AgentPaths.from_root(stable)
            sandbox_paths = AgentPaths.from_root(sandbox)
            set_llm_secret(stable_paths, "OX567_API_KEY", "ak-stable-test-key")
            self.assertIsNone(
                json.loads((sandbox_paths.data / "llm_secrets.json").read_text(encoding="utf-8") if False else "{}").get("keys")
                if not (sandbox_paths.data / "llm_secrets.json").is_file()
                else None
            )
            self.assertFalse((sandbox_paths.data / "llm_secrets.json").is_file())
            self.assertEqual(get_llm_secret("OX567_API_KEY", sandbox_paths), "ak-stable-test-key")
            self.assertEqual(resolve_secret_source("OX567_API_KEY", sandbox_paths), "file")

    def test_local_sandbox_secret_wins(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            stable = Path(raw) / "product"
            sandbox = stable / ".worktrees" / "sandbox"
            _make_agent_root(stable)
            _make_agent_root(sandbox)
            set_llm_secret(AgentPaths.from_root(stable), "OX567_API_KEY", "ak-stable")
            set_llm_secret(AgentPaths.from_root(sandbox), "OX567_API_KEY", "ak-sandbox")
            self.assertEqual(get_llm_secret("OX567_API_KEY", AgentPaths.from_root(sandbox)), "ak-sandbox")


if __name__ == "__main__":
    unittest.main()
