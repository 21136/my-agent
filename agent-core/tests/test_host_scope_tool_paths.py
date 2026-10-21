from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from host_scope import add_host_root, empty_host_scope, save_host_scope
from tests.isolation_helpers import temporary_agent_paths
from tools.builtin import glob_file_search, grep, list_dir, read_file


class HostScopeToolPathTests(unittest.TestCase):
    def test_host_scope_paths_survive_builtin_tool_result_formatting(self) -> None:
        with tempfile.TemporaryDirectory(prefix="host-scope-tools-") as temp_dir:
            host_root = Path(temp_dir) / ".codex"
            mcp_dir = host_root / "mcp"
            mcp_dir.mkdir(parents=True)
            config_file = mcp_dir / "config.toml"
            config_file.write_text('MCP_MARKER = "local"\n', encoding="utf-8")

            with temporary_agent_paths() as paths:
                config = empty_host_scope()
                add_host_root(
                    paths,
                    config,
                    host_id="codex",
                    directory=host_root,
                    label="codex",
                    read=True,
                    write=True,
                )
                save_host_scope(paths, config)

                listed = list_dir.run({"path": "host:codex/"}, paths=paths)
                self.assertTrue(listed.ok, listed.error)
                self.assertEqual(listed.data["path"], "host:codex")
                self.assertIn("mcp", {entry["name"] for entry in listed.data["entries"]})

                read = read_file.run({"path": "host:codex/mcp/config.toml"}, paths=paths)
                self.assertTrue(read.ok, read.error)
                self.assertEqual(read.data["path"], "host:codex/mcp/config.toml")
                self.assertIn("MCP_MARKER", read.data["content"])

                with mock.patch("tools.builtin.glob_file_search.shutil.which", return_value=None):
                    found = glob_file_search.run(
                        {"path": "host:codex", "pattern": "*.toml"}, paths=paths
                    )
                self.assertTrue(found.ok, found.error)
                self.assertEqual(found.data["path"], "host:codex")
                self.assertIn("mcp/config.toml", found.data["paths"])

                with mock.patch("tools.builtin.grep.shutil.which", return_value=None):
                    matches = grep.run(
                        {"path": "host:codex", "pattern": "MCP_MARKER"}, paths=paths
                    )
                self.assertTrue(matches.ok, matches.error)
                self.assertEqual(matches.data["matches"][0]["path"], "host:codex/mcp/config.toml")


if __name__ == "__main__":
    unittest.main()
