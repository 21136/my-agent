"""Pytest hooks for agent-core tests."""

from __future__ import annotations

import os

import pytest

# T-6106 · RUNAWAY-V2.md §10.3: frozen v1 runaway contract; must not read MY_AGENT_RUNAWAY_V2=1
# from the parent process (e.g. start-desktop.bat default or dev shell).
_V1_RUNAWAY_COMPAT_MODULES = frozenset(
    {
        "test_runaway_flow",
        "test_runaway_reliability",
        "test_runaway_startup",
        "test_runaway_verification_orchestrator",
        "test_runaway_verification_bootstrap",
        "test_runaway_verify_matrix",
        "test_project_artifacts",
    }
)


@pytest.fixture(autouse=True)
def _force_runaway_v1_compat_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    module_name = (request.module.__name__ or "").rsplit(".", 1)[-1]
    if module_name in _V1_RUNAWAY_COMPAT_MODULES:
        monkeypatch.setenv("MY_AGENT_RUNAWAY_V2", "0")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "runaway_v1_compat: T-6106 frozen v1 runaway regression (112 tests)",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        module_name = item.module.__name__.rsplit(".", 1)[-1]
        if module_name in _V1_RUNAWAY_COMPAT_MODULES:
            item.add_marker(pytest.mark.runaway_v1_compat)
