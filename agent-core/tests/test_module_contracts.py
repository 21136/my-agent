"""IT-06 / T-1803-06: desktop event helper import contracts (BUG-019)."""

from __future__ import annotations

import ast
import json
import re
import secrets
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from project_api import perform_project_switch
from project_cli import ParsedProjectCommand, run_project_command
from project_mode import create_project, normalize_project_id, project_dir
from project_switch import PROJECT_SESSIONS_KEY, read_project_sessions
from session import create_new

_FORBIDDEN_IMPORT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"from\s+session\s+import\s+[^\n#]*\bsession_memory_event\b"),
        "session_memory_event must be imported from context, not session",
    ),
    (
        re.compile(r"from\s+context\s+import\s+[^\n#]*\bsession_history_event\b"),
        "session_history_event must be imported from session, not context",
    ),
)

_SCAN_ROOTS = (
    _AGENT_CORE,
    _AGENT_CORE / "tests",
)


def _run_import_probe(statement: str) -> subprocess.CompletedProcess[str]:
    code = f"""
import sys
from pathlib import Path
sys.path.insert(0, {str(_AGENT_CORE)!r})
{statement}
"""
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(_AGENT_CORE),
    )


def _function_def(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path}")


def _import_from_pairs(func: ast.FunctionDef) -> set[tuple[str | None, str]]:
    pairs: set[tuple[str | None, str]] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            pairs.add((node.module, alias.name))
    return pairs


class ModuleContractTests(unittest.TestCase):
    def test_session_memory_event_not_importable_from_session(self) -> None:
        """T-1803-06: session_memory_event lives in context.py only."""
        result = _run_import_probe(
            "\n".join(
                [
                    "try:",
                    "    from session import session_memory_event",
                    "except ImportError:",
                    "    raise SystemExit(0)",
                    "raise SystemExit('import should fail')",
                ]
            )
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_session_history_event_not_importable_from_context(self) -> None:
        """session_history_event lives in session.py only."""
        result = _run_import_probe(
            "\n".join(
                [
                    "try:",
                    "    from context import session_history_event",
                    "except ImportError:",
                    "    raise SystemExit(0)",
                    "raise SystemExit('import should fail')",
                ]
            )
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_event_helpers_import_from_canonical_modules(self) -> None:
        result = _run_import_probe(
            "\n".join(
                [
                    "from context import session_memory_event",
                    "from session import session_history_event",
                    "assert callable(session_memory_event)",
                    "assert callable(session_history_event)",
                ]
            )
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_agent_core_sources_avoid_forbidden_event_imports(self) -> None:
        """Static scan: no production/test file reintroduces BUG-019 imports."""
        violations: list[str] = []
        for root in _SCAN_ROOTS:
            for path in root.rglob("*.py"):
                if path.name == "test_module_contracts.py":
                    continue
                text = path.read_text(encoding="utf-8")
                for pattern, message in _FORBIDDEN_IMPORT_PATTERNS:
                    for match in pattern.finditer(text):
                        line_no = text.count("\n", 0, match.start()) + 1
                        rel = path.relative_to(_AGENT_CORE)
                        violations.append(f"{rel}:{line_no}: {message}")
        self.assertEqual(violations, [])

    def test_project_api_switch_branch_lazy_import_sources(self) -> None:
        """T-1803-07: perform_project_switch lazy-imports event helpers from canonical modules."""
        pairs = _import_from_pairs(
            _function_def(_AGENT_CORE / "project_api.py", "perform_project_switch")
        )
        self.assertIn(("context", "session_memory_event"), pairs)
        self.assertIn(("session", "session_history_event"), pairs)
        self.assertNotIn(("session", "session_memory_event"), pairs)
        self.assertIn(("project_switch", "execute_project_switch"), pairs)
        self.assertIn(("project_switch", "plan_project_switch"), pairs)


class ProjectApiLazyImportTests(unittest.TestCase):
    def setUp(self) -> None:
        from tests.isolation_helpers import make_temp_agent_paths

        self.paths = make_temp_agent_paths(self)
        token = secrets.token_hex(4)
        self.project_a = f"test-api-a-{token}"
        self.project_b = f"test-api-b-{token}"
        self.session = create_new(
            self.paths,
            conversation_id=f"_test_api_lazy_{secrets.token_hex(4)}",
        )
        self._extra_session_ids: list[str] = []

    def test_perform_project_switch_session_replaced_branch_imports(self) -> None:
        """T-1803-07: session_replaced path executes lazy imports without ImportError."""
        create_project(self.paths, self.project_b)
        run_project_command(
            self.session,
            self.paths,
            ParsedProjectCommand(kind="new", project_id=self.project_a),
            output_fn=lambda _line: None,
        )

        updated, events = perform_project_switch(
            self.session,
            self.paths,
            {
                "project_id": self.project_b,
                "confirm": True,
                "request_id": "test-api-lazy-import",
            },
        )
        self._extra_session_ids.append(updated.conversation_id)

        done = next(event for event in events if event.get("type") == "project.switch.done")
        self.assertTrue(done.get("session_replaced"))
        event_types = [event.get("type") for event in events]
        self.assertIn("session.memory", event_types)
        self.assertIn("session.history", event_types)


if __name__ == "__main__":
    unittest.main()
