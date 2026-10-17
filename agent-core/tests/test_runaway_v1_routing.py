from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent import Agent


def _v2_agent() -> Agent:
    agent = Agent.__new__(Agent)
    session = MagicMock()
    session.meta.project_runaway_enabled = True
    session.meta.project_id = "demo"
    session.meta.project_workflow_stage = "verification"
    session.meta.project_runaway_checkpoint = "verifying"
    session.last_review_verdict = None
    session.last_review_blockers_count = 0
    agent.session = session
    return agent


def test_v2_review_result_records_advisory_without_v1_repair():
    agent = _v2_agent()
    agent._run_runaway_hard_verification = MagicMock(return_value=True)
    agent._transition_runaway_checkpoint = MagicMock()
    agent._runaway_repair_pending = MagicMock(return_value=True)
    agent._enter_runaway_repair_checkpoint = MagicMock()
    agent._sync_runaway_harness_truth = MagicMock(return_value=False)
    review = SimpleNamespace(
        data={"verdict": "pass", "blockers_count": 0, "summary": "review ok"}
    )

    with patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False):
        result = Agent._record_runaway_review_result(agent, review)

    assert result is False
    assert agent.session.meta.project_runaway_last_verification == "pass"
    assert agent.session.meta.project_runaway_review_blockers_count == 0
    agent.session.save.assert_called_once_with()
    agent._run_runaway_hard_verification.assert_not_called()
    agent._transition_runaway_checkpoint.assert_not_called()
    agent._runaway_repair_pending.assert_not_called()
    agent._enter_runaway_repair_checkpoint.assert_not_called()
    agent._sync_runaway_harness_truth.assert_not_called()


def test_v2_proposal_reconcile_skips_legacy_checkpoint_advance():
    agent = _v2_agent()
    agent.session.meta.project_workflow_stage = "implementation"
    agent.session.meta.project_active_task_id = "T-1"
    agent._advance_runaway_checkpoint = MagicMock(return_value=True)

    with patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False), patch(
        "runaway_flow.reconcile_runaway_tasks_artifact",
        return_value={"changed": False},
    ) as reconcile:
        changed = Agent._reconcile_runaway_tasks_after_plan(agent, "demo")

    reconcile.assert_called_once_with(agent.session.paths, "demo", "T-1")
    agent._advance_runaway_checkpoint.assert_not_called()
    assert changed is False


def test_v1_verification_wrapper_lazily_imports_implementation():
    import subprocess
    import sys
    from pathlib import Path

    agent_core = Path(__file__).resolve().parents[1]
    script = """
import sys
from agent import Agent
agent = Agent.__new__(Agent)
agent._matrix_repair_fingerprint('sample')
assert 'runaway_v1.verification' in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=agent_core,
        env={**os.environ, "MY_AGENT_RUNAWAY_V2": "0"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_v2_entry_review_and_proposal_paths_do_not_import_runaway_v1():
    import subprocess
    import sys
    from pathlib import Path

    agent_core = Path(__file__).resolve().parents[1]
    script = """
import os, sys
from types import SimpleNamespace
from unittest.mock import patch
from agent import Agent
from runaway_v2.kernel.runtime import RunawayRuntime
assert not any(name == 'runaway_v1' or name.startswith('runaway_v1.') for name in sys.modules)
meta = SimpleNamespace(
    project_runaway_enabled=True,
    project_id='demo',
    project_workflow_stage='implementation',
    project_active_task_id='T-1',
    project_runaway_checkpoint='implementing',
)
session = SimpleNamespace(meta=meta, paths=object(), save=lambda: None,
                          last_review_verdict=None, last_review_blockers_count=0)
agent = Agent.__new__(Agent)
agent.session = session
RunawayRuntime.run_turn = lambda self, user_text, **kwargs: 'v2-runtime'
with patch.dict(os.environ, {'MY_AGENT_RUNAWAY_V2': '1'}, clear=False):
    assert Agent.run_turn(agent, 'continue') == 'v2-runtime'
    review = SimpleNamespace(data={'verdict': 'pass', 'blockers_count': 0, 'summary': 'ok'})
    Agent._record_runaway_review_result(agent, review)
    with patch('runaway_flow.reconcile_runaway_tasks_artifact', return_value={'changed': False}):
        Agent._reconcile_runaway_tasks_after_plan(agent, 'demo')
assert not any(name == 'runaway_v1' or name.startswith('runaway_v1.') for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=agent_core,
        env={**os.environ, "MY_AGENT_RUNAWAY_V2": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_v1_chain_routers_delegate_to_lazy_continuation_module():
    agent = _v2_agent()
    agent.session.meta.active_shell = "project"
    agent.session.meta.project_runaway_paused_reason = ""
    agent.session.meta.project_runaway_checkpoint = "implementing"
    agent.session.meta.project_active_task_id = ""
    agent.session.meta.project_id = ""
    agent._runaway_formal_queue_done = MagicMock(return_value=False)
    agent._sync_runaway_harness_truth = MagicMock(return_value=False)

    with patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "0"}, clear=False):
        assert Agent.should_chain_runaway_after_turn(agent, "completed") is True
        user_line = Agent.runaway_chain_user_line(agent)

    assert isinstance(user_line, str) and user_line


def test_v1_review_router_delegates_to_lazy_repair_implementation():
    agent = _v2_agent()
    agent._run_runaway_hard_verification = MagicMock(return_value=True)
    agent._sync_runaway_harness_truth = MagicMock(return_value=False)
    review = SimpleNamespace(
        data={"verdict": "pass", "blockers_count": 0, "summary": "review ok"}
    )

    with patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "0"}, clear=False):
        Agent._record_runaway_review_result(agent, review)

    agent._run_runaway_hard_verification.assert_called_once_with()
    agent._sync_runaway_harness_truth.assert_called_once_with(
        notice="交付审查通过，Harness 硬验收已通过"
    )


def test_v1_execute_segment_continuation_preserves_repair_then_advance_order():
    from runaway_v1.continuation import should_continue_v1_execute_segment

    agent = MagicMock()
    agent._runaway_repair_pending.return_value = True

    should_continue = should_continue_v1_execute_segment(
        agent,
        final_text="legacy final",
        finish_reason="stop",
        total_tool_rounds=3,
        total_max=5,
    )

    assert should_continue is True
    agent._runaway_repair_pending.assert_called_once_with()
    agent._advance_runaway_checkpoint.assert_not_called()
    agent._continue_runaway_after_natural_stop.assert_not_called()


def test_v1_execute_segment_natural_stop_runs_even_at_round_cap():
    from runaway_v1.continuation import should_continue_v1_execute_segment

    agent = MagicMock()
    agent._runaway_repair_pending.return_value = False
    agent._advance_runaway_checkpoint.return_value = False
    agent._continue_runaway_after_natural_stop.return_value = True

    should_continue = should_continue_v1_execute_segment(
        agent,
        final_text="legacy final",
        finish_reason="stop",
        total_tool_rounds=5,
        total_max=5,
    )

    assert should_continue is False
    agent._runaway_repair_pending.assert_called_once_with()
    agent._advance_runaway_checkpoint.assert_called_once_with()
    agent._continue_runaway_after_natural_stop.assert_called_once_with(
        final_text="legacy final",
        finish_reason="stop",
    )


def test_v1_hard_verification_uses_original_agent_context():
    agent = _v2_agent()
    agent.session.meta.active_shell = "project"
    agent._bootstrap_runaway_verification_artifacts = MagicMock(return_value=False)
    agent._rerun_runaway_harness_verification = MagicMock(return_value=True)

    with patch(
        "runaway_verify_matrix.lint_verify_matrix",
        return_value=SimpleNamespace(ok=True),
    ):
        result = Agent._run_runaway_hard_verification(agent)

    assert result is True
    agent._bootstrap_runaway_verification_artifacts.assert_called_once_with()
    agent._rerun_runaway_harness_verification.assert_called_once_with()


def test_v1_repair_pending_wrapper_delegates_to_verification_module():
    agent = Agent.__new__(Agent)
    with patch(
        "runaway_v1.verification._runaway_repair_pending",
        return_value=True,
    ) as implementation:
        assert Agent._runaway_repair_pending(agent) is True
    implementation.assert_called_once_with(agent)


def test_v1_documentation_repair_wrapper_delegates_to_verification_module():
    agent = Agent.__new__(Agent)
    error = RuntimeError("docs incomplete")
    with patch(
        "runaway_v1.verification._queue_runaway_documentation_repair",
        return_value=True,
    ) as implementation:
        assert Agent._queue_runaway_documentation_repair(agent, error) is True
    implementation.assert_called_once_with(agent, error)
