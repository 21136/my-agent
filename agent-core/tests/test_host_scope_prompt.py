from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from host_scope import add_host_root, empty_host_scope, load_host_scope, resolve_host_path, save_host_scope
from loader import _format_host_scope_overlay
from tests.isolation_helpers import temporary_agent_paths


class HostScopePromptTests(unittest.TestCase):
    def test_registered_host_root_is_resolvable_and_exposed_to_model_prompt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="host-scope-prompt-") as temp_dir:
            host_directory = Path(temp_dir) / ".codex"
            mcp_directory = host_directory / "mcp"
            mcp_directory.mkdir(parents=True)
            with temporary_agent_paths() as paths:
                config = empty_host_scope()
                add_host_root(
                    paths,
                    config,
                    host_id="codex",
                    directory=host_directory,
                    label="codex",
                    read=True,
                    write=True,
                )
                save_host_scope(paths, config)

                reloaded = load_host_scope(paths)
                resolved = resolve_host_path("host:codex/mcp", config=reloaded, must_exist=True)
                self.assertEqual(resolved.absolute, mcp_directory.resolve())

                prompt_overlay = _format_host_scope_overlay(paths)
                self.assertIn("host:codex (读写)", prompt_overlay)
                self.assertIn(host_directory.resolve().as_posix(), prompt_overlay)


if __name__ == "__main__":
    unittest.main()
