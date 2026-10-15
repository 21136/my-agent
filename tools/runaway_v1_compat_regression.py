#!/usr/bin/env python3
"""Run T-6106 v1 runaway compatibility regression (112 tests).

Forces MY_AGENT_RUNAWAY_V2=0 even when the parent shell has v2 enabled
(see start-desktop.bat). Exit code is pytest's.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_AGENT_CORE = ROOT / "agent-core"
_TESTS = _AGENT_CORE / "tests"

_V1_FILES = (
    _TESTS / "test_runaway_flow.py",
    _TESTS / "test_runaway_reliability.py",
    _TESTS / "test_runaway_startup.py",
    _TESTS / "test_runaway_verification_orchestrator.py",
    _TESTS / "test_runaway_verification_bootstrap.py",
    _TESTS / "test_runaway_verify_matrix.py",
    _TESTS / "test_project_artifacts.py",
)


def main() -> int:
    python = os.environ.get("MY_AGENT_PYTHON") or sys.executable
    env = {**os.environ, "MY_AGENT_RUNAWAY_V2": "0"}
    cmd = [python, "-m", "pytest", *[str(p) for p in _V1_FILES], "-q"]
    print("T-6106 v1 runaway compat:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(_AGENT_CORE), env=env)
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
