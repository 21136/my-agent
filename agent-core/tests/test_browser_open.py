"""IT-150 · Phase 33 F1 — browser_open."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tests.isolation_helpers import temporary_agent_paths
from tools.executor import (
    ExecutorSession,
    ToolExecutor,
    _browser_open_needs_confirm,
)
from tools.registry import ToolRegistry


def _load_mod(main_py: Path):
    spec = importlib.util.spec_from_file_location("browser_open_it150", main_py)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class BrowserOpenIT150Tests(unittest.TestCase):
    def test_dry_run_and_scheme_validation(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/browser_open",)) as paths:
            main_py = paths.evolve / "tools" / "common" / "browser_open" / "main.py"
            mod = _load_mod(main_py)

            dry = mod.browser_open(
                {"url": "http://127.0.0.1:8080/", "dry_run": True}
            )
            self.assertTrue(dry.get("ok"), dry)
            self.assertTrue(dry.get("dry_run"))
            self.assertTrue(dry.get("loopback"))
            self.assertFalse(dry.get("needs_confirm"))

            bad = mod.browser_open({"url": "file:///etc/passwd"})
            self.assertFalse(bad.get("ok"))
            js = mod.browser_open({"url": "javascript:alert(1)"})
            self.assertFalse(js.get("ok"))
            creds = mod.browser_open({"url": "https://user:pass@example.com/"})
            self.assertFalse(creds.get("ok"))

    def test_open_mocked_and_confirm_gate(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/browser_open",)) as paths:
            main_py = paths.evolve / "tools" / "common" / "browser_open" / "main.py"
            mod = _load_mod(main_py)

            with mock.patch.object(mod.webbrowser, "open", return_value=True) as opened:
                out = mod.browser_open({"url": "https://example.com/docs"})
                self.assertTrue(out.get("ok"), out)
                self.assertTrue(out.get("opened"))
                self.assertFalse(out.get("loopback"))
                opened.assert_called_once()

            self.assertFalse(
                _browser_open_needs_confirm({"url": "http://localhost:3000/"})
            )
            self.assertTrue(
                _browser_open_needs_confirm({"url": "https://example.com/"})
            )

            registry = ToolRegistry.load(paths)
            confirms: list[str] = []

            def confirm_fn(preview: str, allow_approve_all: bool = False) -> str:
                confirms.append(preview)
                return "n"

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(allowed_evolved={"browser_open"}),
                confirm_fn=confirm_fn,
            )

            loop = executor.run(
                "run_evolved",
                {
                    "tool_name": "browser_open",
                    "arguments": {
                        "url": "http://127.0.0.1:5173/",
                        "dry_run": True,
                    },
                },
            )
            self.assertTrue(loop.ok, loop)
            self.assertEqual(confirms, [])

            external = executor.run(
                "run_evolved",
                {
                    "tool_name": "browser_open",
                    "arguments": {"url": "https://example.com/"},
                },
            )
            self.assertFalse(external.ok)
            self.assertEqual(len(confirms), 1)

            # Loopback without dry_run: _needs_confirm False
            evolved = registry.get_evolved("browser_open")
            builtin = registry.get_builtin("run_evolved")
            assert evolved is not None and builtin is not None
            needs = executor._needs_confirm(
                builtin,
                evolved,
                {
                    "tool_name": "browser_open",
                    "arguments": {"url": "http://localhost:8080/app"},
                },
                tool_name="run_evolved",
            )
            self.assertFalse(needs)


if __name__ == "__main__":
    unittest.main()
