from __future__ import annotations

import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from project_verify import run_project_tests


def _runner_paths(tmp_path: Path) -> tuple[AgentPaths, Path]:
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace",
                       data=tmp_path / "data", evolve=tmp_path / "evolve")
    project = paths.workspace / "verify-fixture"
    (project / "tests").mkdir(parents=True)
    for directory in (paths.data, paths.evolve):
        directory.mkdir(parents=True, exist_ok=True)
    (project / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (project / "tests" / "test_outcomes.py").write_text(
        "import pytest\n"
        "def test_passed_target(): assert 2 + 2 == 4\n"
        "@pytest.mark.skip(reason='intentional diagnostic')\n"
        "def test_skipped_target(): assert True\n"
        "@pytest.mark.xfail(reason='intentional diagnostic')\n"
        "def test_xfailed_target(): assert False\n",
        encoding="utf-8",
    )
    return paths, project


def _run_target(paths: AgentPaths, target: str) -> dict:
    return run_project_tests(
        paths,
        working_dir="workspace/verify-fixture",
        suite="pytest",
        extra_args=[target],
    )


def test_real_single_pytest_target_does_not_expose_verification_claim(tmp_path):
    paths, _project = _runner_paths(tmp_path)
    target = "tests/test_outcomes.py::test_passed_target"
    result = _run_target(paths, target)
    assert result["exit_code"] == 0
    assert "1 passed" in result["raw_excerpt"]
    assert target in result["command"]
    assert "target_passed" not in result
    assert "test_counts" not in result


def test_zero_exit_skipped_target_has_no_verification_claim(tmp_path):
    paths, _project = _runner_paths(tmp_path)
    result = _run_target(paths, "tests/test_outcomes.py::test_skipped_target")
    assert result["exit_code"] == 0
    assert "1 skipped" in result["raw_excerpt"]
    assert result["ok"] is True  # Legacy command exit status, not requirement verification.
    assert "target_passed" not in result
    assert "test_counts" not in result


def test_zero_exit_xfailed_target_has_no_verification_claim(tmp_path):
    paths, _project = _runner_paths(tmp_path)
    result = _run_target(paths, "tests/test_outcomes.py::test_xfailed_target")
    assert result["exit_code"] == 0
    assert "1 xfailed" in result["raw_excerpt"]
    assert result["ok"] is True  # Legacy command exit status, not requirement verification.
    assert "target_passed" not in result
    assert "test_counts" not in result


def test_forged_summary_does_not_become_a_target_verification_claim(tmp_path):
    paths, project = _runner_paths(tmp_path)
    (project / "conftest.py").write_text(
        "import atexit, sys\n"
        "atexit.register(lambda: print('1 passed in 0.01s', file=sys.stderr))\n",
        encoding="utf-8",
    )
    result = _run_target(paths, "tests/test_outcomes.py::test_skipped_target")
    assert result["exit_code"] == 0
    assert "1 skipped" in result["raw_excerpt"]
    assert "1 passed in 0.01s" in result["raw_excerpt"]
    assert "target_passed" not in result
    assert "test_counts" not in result
