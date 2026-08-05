"""Phase 41 P1 — flat proxy tools (AGENT-HARNESS · IT-410)."""

from __future__ import annotations

import secrets
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import build_llm_tools
from paths import AgentPaths
from session import create_new
from tool_proxies import (
    PROXY_EVOLVED_TOOL_NAMES,
    build_proxy_tool_definitions,
    rewrite_proxy_tool_call,
)
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry

from tests.isolation_helpers import temporary_agent_paths


class ToolProxyRewriteTests(unittest.TestCase):
    def test_rewrite_run_command(self) -> None:
        name, args = rewrite_proxy_tool_call(
            "run_command",
            {"command": "echo hi", "working_dir": "workspace"},
        )
        self.assertEqual(name, "run_evolved")
        self.assertEqual(args["tool_name"], "run_command")
        self.assertEqual(args["arguments"]["command"], "echo hi")
        self.assertEqual(args["arguments"]["working_dir"], "workspace")

    def test_rewrite_preserves_dry_run_top_level(self) -> None:
        name, args = rewrite_proxy_tool_call(
            "write_text",
            {"path": "a.txt", "content": "x", "dry_run": True},
        )
        self.assertEqual(name, "run_evolved")
        self.assertTrue(args["dry_run"])
        self.assertNotIn("dry_run", args["arguments"])

    def test_non_proxy_unchanged(self) -> None:
        name, args = rewrite_proxy_tool_call("grep", {"pattern": "x", "path": "."})
        self.assertEqual(name, "grep")
        self.assertEqual(args["pattern"], "x")


class ToolProxyLlmToolsTests(unittest.TestCase):
    def test_agent_tools_include_proxies(self) -> None:
        paths = AgentPaths.discover()
        session = create_new(paths, conversation_id=f"_it410_tools_{secrets.token_hex(3)}")
        session.meta.turn_mode = "agent"
        names = {t["function"]["name"] for t in build_llm_tools(session)}
        for proxy in PROXY_EVOLVED_TOOL_NAMES:
            self.assertIn(proxy, names)

    def test_ask_mode_hides_proxies_and_run_evolved(self) -> None:
        paths = AgentPaths.discover()
        session = create_new(paths, conversation_id=f"_it410_ask_{secrets.token_hex(3)}")
        session.meta.turn_mode = "ask"
        names = {t["function"]["name"] for t in build_llm_tools(session)}
        self.assertNotIn("run_evolved", names)
        for proxy in PROXY_EVOLVED_TOOL_NAMES:
            self.assertNotIn(proxy, names)

    def test_run_command_schema_flat(self) -> None:
        defs = {d["function"]["name"]: d for d in build_proxy_tool_definitions()}
        params = defs["run_command"]["function"]["parameters"]
        self.assertIn("command", params["required"])
        self.assertNotIn("tool_name", params.get("properties", {}))


class ToolProxyExecutorTests(unittest.TestCase):
    def test_executor_run_command_dry_run(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_command",)) as paths:
            registry = ToolRegistry.load(paths)
            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(allowed_evolved={"run_command"}),
            )
            result = executor.run(
                "run_command",
                {
                    "command": "echo proxy-dry-run",
                    "dry_run": True,
                },
            )
            self.assertTrue(result.ok, result.error)
            data = result.data if isinstance(result.data, dict) else {}
            self.assertTrue(data.get("dry_run") or data.get("command"))


if __name__ == "__main__":
    unittest.main()
