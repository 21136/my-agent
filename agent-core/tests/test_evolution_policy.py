from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from tools.executor import ExecutorSession, ToolExecutor, _BUILTIN_RUNNERS
from tools.registry import ToolRegistry
from tools.schema import tool_ok


_INVALID_ACTIONS = [
    pytest.param([], id="empty-list"),
    pytest.param(["inspect"], id="list"),
    pytest.param({}, id="empty-dict"),
    pytest.param({"name": "inspect"}, id="dict"),
    pytest.param(None, id="null"),
    pytest.param(0, id="integer"),
    pytest.param(1.5, id="float"),
    pytest.param(True, id="true"),
    pytest.param(False, id="false"),
]


def _memory_registry():
    # Construct the real builtin registry without discovery or reading manifests.
    root = _AGENT_CORE.parent / "artifacts/managed-closeout-fixes-20261026/synthetic-agent"
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    return ToolRegistry(agent_paths=paths, evolved=[])


def _decision_fn():
    try:
        from evolution.policy import evolution_tool_decision
    except ImportError:
        evolution_tool_decision = None
    assert callable(evolution_tool_decision), "evolution.policy.evolution_tool_decision must be importable"
    return evolution_tool_decision


def test_active_evolution_denies_host_execution_and_unknown_tools_but_keeps_normal_sessions():
    decide = _decision_fn()
    for name in ("run_evolved", "run_command", "run_service", "write_text", "patch_file", "git_commit", "git_push", "generate_image", "propose_context_switch", "unknown_new_tool"):
        result = decide("22222222-2222-4222-8222-222222222222", name, {})
        assert result["decision"] == "deny", name
    for name in ("read_file", "list_dir", "grep", "glob_file_search", "codebase_search", "show_images"):
        assert decide("22222222-2222-4222-8222-222222222222", name, {})["decision"] == "allow"
    assert decide("22222222-2222-4222-8222-222222222222", "evolution", {"action": "inspect"})["decision"] == "allow"
    assert decide("", "run_evolved", {"tool_name": "run_command", "arguments": {}})["decision"] == "allow"


def test_proxy_rewrite_cannot_reach_run_evolved_runner_in_bound_session():
    decide = _decision_fn()
    del decide
    registry = _memory_registry()
    session = ExecutorSession(evolution_id="22222222-2222-4222-8222-222222222222", allowed_evolved={"run_command"})
    executor = ToolExecutor(registry=registry, session=session, confirm_fn=lambda *_a, **_k: "y")
    calls = []
    def fake_runner(*args, **kwargs):
        calls.append((args, kwargs))
        return tool_ok("run_evolved", {"executed": True})
    executor.validate = lambda _name, _arguments: None
    with patch.dict(_BUILTIN_RUNNERS, {"run_evolved": fake_runner}):
        result = executor.run("run_command", {"command": "node candidate/main.cjs"})
    assert calls == []
    assert result.ok is False
    assert result.error.code in {"permission_denied", "validation_error"}
    direct = executor.run("run_evolved", {"tool_name": "run_command", "arguments": {"command": "node candidate/main.cjs"}})
    assert direct.ok is False
    assert direct.error.code in {"permission_denied", "validation_error"}
    assert calls == []

def test_bound_evolution_session_cannot_use_read_only_tools_for_registered_host_roots():
    decide = _decision_fn()
    for name, arguments in (("read_file", {"path": "host:trusted/C:/secret.txt"}), ("list_dir", {"path": "host:trusted/C:/"}), ("grep", {"path": "host:trusted/C:/"})):
        result = decide("22222222-2222-4222-8222-222222222222", name, arguments)
        assert result["decision"] == "deny"


@pytest.mark.parametrize("action", _INVALID_ACTIONS)
def test_bound_policy_rejects_non_string_actions_without_raising(action):
    result = _decision_fn()("22222222-2222-4222-8222-222222222222", "evolution", {"action": action, "payload": {}})
    assert result["decision"] == "deny"
    assert result["code"] == "unsupported_capability"


@pytest.mark.parametrize("action", _INVALID_ACTIONS)
def test_bound_executor_returns_tool_error_for_non_string_actions(action):
    executor = ToolExecutor(
        registry=_memory_registry(),
        session=ExecutorSession(evolution_id="22222222-2222-4222-8222-222222222222"),
    )
    result = executor.run("evolution", {"action": action, "payload": {}})
    assert result.ok is False
    assert result.tool == "evolution"
    assert result.error.code == "unsupported_capability"
    assert result.data is None


@pytest.mark.parametrize("action", ["inspect", "patch", "snapshot", "build", "launch", "stop", "run_case"])
def test_bound_policy_preserves_supported_actions(action):
    assert _decision_fn()("22222222-2222-4222-8222-222222222222", "evolution", {"action": action})["decision"] == "allow"


@pytest.mark.parametrize("action", _INVALID_ACTIONS)
def test_ordinary_policy_keeps_deferring_action_validation(action):
    assert _decision_fn()("", "evolution", {"action": action, "payload": {}})["decision"] == "allow"
