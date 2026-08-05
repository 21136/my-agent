"""Phase 26 M2 — db_query + pip_install active (IT-85 / IT-86)."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from pip_install_policy import validate_pip_install_payload
from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry


def _load_mod(main_py: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, main_py)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DbQueryTests(unittest.TestCase):
    def test_it85_readonly_and_write_gate(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/db_query",)) as paths:
            db = paths.workspace / "demo.sqlite"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute("INSERT INTO t(name) VALUES ('alice')")
            conn.commit()
            conn.close()

            main_py = paths.evolve / "tools" / "common" / "db_query" / "main.py"
            mod = _load_mod(main_py, "db_query_m2")
            mod._agent_root = lambda: paths.agent_root  # type: ignore[method-assign]

            ok = mod.db_query(
                {
                    "db_path": "workspace/demo.sqlite",
                    "sql": "SELECT id, name FROM t",
                    "readonly": True,
                }
            )
            self.assertTrue(ok.get("ok"), ok)
            self.assertEqual(ok.get("rows"), [[1, "alice"]])

            denied = mod.db_query(
                {
                    "db_path": "workspace/demo.sqlite",
                    "sql": "DELETE FROM t",
                    "readonly": True,
                }
            )
            self.assertFalse(denied.get("ok"))

            multi = mod.db_query(
                {
                    "db_path": "workspace/demo.sqlite",
                    "sql": "SELECT 1; SELECT 2",
                }
            )
            self.assertFalse(multi.get("ok"))

            registry = ToolRegistry.load(paths)
            self.assertEqual(registry.get_evolved("db_query").status, "active")
            confirms: list[str] = []

            def confirm_fn(preview: str, allow_approve_all: bool = False) -> str:
                confirms.append(preview)
                return "y"

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(allowed_evolved={"db_query"}),
                confirm_fn=confirm_fn,
            )
            # readonly path — no confirm
            r1 = executor.run(
                "run_evolved",
                {
                    "tool_name": "db_query",
                    "arguments": {
                        "db_path": "workspace/demo.sqlite",
                        "sql": "SELECT COUNT(*) AS c FROM t",
                    },
                },
            )
            self.assertTrue(r1.ok, r1)
            self.assertEqual(confirms, [])

            # write — confirm
            r2 = executor.run(
                "run_evolved",
                {
                    "tool_name": "db_query",
                    "arguments": {
                        "db_path": "workspace/demo.sqlite",
                        "sql": "INSERT INTO t(name) VALUES ('bob')",
                        "write": True,
                    },
                },
            )
            self.assertTrue(r2.ok, r2)
            self.assertTrue(confirms)


class PipInstallTests(unittest.TestCase):
    def test_it86_archived_module_still_validates(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/pip_install",)) as paths:
            registry = ToolRegistry.load(paths)
            tool = registry.get_evolved("pip_install")
            self.assertIsNotNone(tool)
            self.assertEqual(tool.status, "archived")

            def resolve_req(text: str) -> Path:
                norm = text.strip().replace("\\", "/").lstrip("/")
                return paths.resolve_under_agent(norm, must_exist=True)

            dry_cmd, dry_err = validate_pip_install_payload(
                {"packages": ["httpx"], "dry_run": True},
                resolve_requirements=resolve_req,
            )
            self.assertIsNone(dry_err)
            assert dry_cmd is not None
            self.assertIn("pip", " ".join(dry_cmd))

            _, bad_err = validate_pip_install_payload(
                {"packages": ["httpx; rm -rf /"]},
                resolve_requirements=resolve_req,
            )
            self.assertIsNotNone(bad_err)

            _, flag_err = validate_pip_install_payload(
                {"packages": ["--user"]},
                resolve_requirements=resolve_req,
            )
            self.assertIsNotNone(flag_err)

            _, missing_err = validate_pip_install_payload(
                {"requirements": "workspace/no-such-req.txt"},
                resolve_requirements=resolve_req,
            )
            self.assertIsNotNone(missing_err)

            req = paths.workspace / "requirements.txt"
            req.write_text("httpx==0.0.0\n", encoding="utf-8")
            req_cmd, req_err = validate_pip_install_payload(
                {"requirements": "workspace/requirements.txt", "dry_run": True},
                resolve_requirements=resolve_req,
            )
            self.assertIsNone(req_err)
            assert req_cmd is not None

            # Archived: executor rejects even with allowlist + confirm.
            confirms: list[str] = []

            def confirm_fn(preview: str, allow_approve_all: bool = False) -> str:
                confirms.append(preview)
                return "y"

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(allowed_evolved={"pip_install"}),
                confirm_fn=confirm_fn,
            )
            denied = executor.run(
                "run_evolved",
                {"tool_name": "pip_install", "arguments": {"packages": ["httpx"], "dry_run": True}},
            )
            self.assertFalse(denied.ok)
            self.assertIn("不可执行", (denied.error.message if denied.error else ""))
            self.assertEqual(confirms, [])


if __name__ == "__main__":
    unittest.main()
