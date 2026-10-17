from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_change import SelfChangeState, SelfChangeTransaction, save_transaction
from tests.isolation_helpers import temporary_agent_paths
from tools.registry import ToolRegistry

_TOOL_PATH = _AGENT_CORE.parent / "evolve" / "tools" / "coding" / "self_change" / "main.py"
_SPEC = importlib.util.spec_from_file_location("self_change_tool_under_test", _TOOL_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class SelfChangeToolTests(unittest.TestCase):
    def test_registry_exposes_self_change_as_active_coding_tool(self) -> None:
        registry = ToolRegistry.load()
        tool = registry.get_evolved("self_change")
        self.assertIsNotNone(tool)
        assert tool is not None
        self.assertEqual(tool.status, "active")
        self.assertEqual(tool.scope, "coding")


    def test_health_action_accepts_healthy_candidate(self) -> None:
        with temporary_agent_paths() as paths:
            tx = SelfChangeTransaction(
                transaction_id="tx-health",
                run_id="run-health",
                source_root=str(paths.agent_root),
                target_root=str(paths.agent_root),
                base_ref="HEAD",
                work_branch="codex/self-change/health",
                owned_paths=("seed.txt",),
                baseline_hashes={"seed.txt": "a" * 64},
                changed_paths=(),
                rollback_target="HEAD",
                state=SelfChangeState.CANDIDATE_READY,
            )
            save_transaction(paths, tx)
            result = _MODULE.run_self_change(
                {
                    "action": "health",
                    "transaction_id": "tx-health",
                    "command": [sys.executable, "-c", "print('healthy')"],
                    "working_dir": "",
                    "timeout_sec": 2,
                },
                paths=paths,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["health"]["ok"], True)
        self.assertEqual(result["state"], "accepted")

    def test_begin_and_status_are_persisted(self) -> None:
        with temporary_agent_paths() as paths:
            root = paths.agent_root
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            seed = root / "seed.txt"
            seed.write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "seed.txt"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "seed"], check=True)

            begun = _MODULE.run_self_change(
                {
                    "action": "begin",
                    "transaction_id": "tx-tool",
                    "run_id": "run-tool",
                    "owned_paths": ["seed.txt"],
                    "working_dir": "",
                },
                paths=paths,
            )
            status = _MODULE.run_self_change(
                {"action": "status", "transaction_id": "tx-tool"},
                paths=paths,
            )

        self.assertTrue(begun["ok"])
        self.assertEqual(begun["state"], "snapshotted")
        self.assertTrue(status["ok"])
        self.assertEqual(status["transaction_id"], "tx-tool")
        self.assertEqual(status["state"], "snapshotted")


if __name__ == "__main__":
    unittest.main()
