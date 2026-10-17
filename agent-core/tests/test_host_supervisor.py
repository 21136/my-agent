from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from host_supervisor import HostProcessSpec, HostSupervisor


class HostSupervisorTests(unittest.TestCase):
    def test_dry_run_does_not_stop_or_start_processes(self) -> None:
        calls: list[str] = []
        supervisor = HostSupervisor(
            start=lambda spec: calls.append("start") or {"pid": 2},
            stop=lambda state: calls.append("stop") or {"ok": True},
            health=lambda state: calls.append("health") or {"ok": True},
        )
        result = supervisor.switch(
            old_spec=HostProcessSpec(("old",), Path("."), "old"),
            candidate_spec=HostProcessSpec(("new",), Path("."), "new"),
            old_state={"pid": 1},
            confirm=False,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.state, "dry_run")
        self.assertEqual(calls, [])

    def test_candidate_health_success_promotes(self) -> None:
        calls: list[str] = []
        supervisor = HostSupervisor(
            start=lambda spec: calls.append(f"start:{spec.name}") or {"pid": 2},
            stop=lambda state: calls.append(f"stop:{state['pid']}") or {"ok": True},
            health=lambda state: calls.append(f"health:{state['pid']}") or {"ok": True},
        )
        result = supervisor.switch(
            old_spec=HostProcessSpec(("old",), Path("."), "old"),
            candidate_spec=HostProcessSpec(("new",), Path("."), "new"),
            old_state={"pid": 1},
            confirm=True,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.state, "switched")
        self.assertEqual(calls, ["start:new", "health:2", "stop:1"])

    def test_candidate_failure_recovers_old_process(self) -> None:
        calls: list[str] = []
        supervisor = HostSupervisor(
            start=lambda spec: calls.append(f"start:{spec.name}") or {"ok": True, "pid": 2 if spec.name == "new" else 3},
            stop=lambda state: calls.append(f"stop:{state['pid']}") or {"ok": True},
            health=lambda state: calls.append(f"health:{state['pid']}") or {"ok": state["pid"] == 3},
        )
        result = supervisor.switch(
            old_spec=HostProcessSpec(("old",), Path("."), "old"),
            candidate_spec=HostProcessSpec(("new",), Path("."), "new"),
            old_state={"pid": 1},
            confirm=True,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.state, "rolled_back")
        self.assertTrue(result.recovered)
        self.assertEqual(calls, ["start:new", "health:2", "stop:2", "start:old", "health:3"])


if __name__ == "__main__":
    unittest.main()
