"""Real Agent batch-to-Goal finalization regression for multi-project acceptance."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

import llm_client
from project_mode import create_project
from server import WsBridge, _build_repl, _run_line
from session import create_new
from tests.isolation_helpers import temporary_agent_paths
from tests.test_agent_project_worker_tool import _ParentModel, _REAL_HTTPX_CLIENT


@pytest.mark.parametrize(
    "scenario", ["returned", "failed-batch", "rejected", "no-goal", "goal-persist-failure"],
    ids=["returned", "failed-batch", "scope-rejected", "ordinary-without-goal", "active-goal-persist-failure"],
)
def test_goal_multi_project_gate_is_written_only_at_first_worker_start(monkeypatch, scenario):
    reject_worker = scenario == "failed-batch"
    reject_confirmation = scenario == "rejected"
    disable_goal = scenario == "no-goal"
    fail_goal_persist = scenario == "goal-persist-failure"
    with temporary_agent_paths(copy_tool_dirs=("common/write_text", "common/run_command")) as paths:
        for project_id in ("project-alpha", "project-beta"):
            create_project(paths, project_id, project_template="light")
            project_root = paths.workspace / project_id
            (project_root / "README.md").write_text(f"{project_id} source\n", encoding="utf-8")
        alpha_root = paths.workspace / "project-alpha"
        (alpha_root / "PROJECT.md").write_text(
            "# alpha\n\n## \u9a8c\u6536\u6807\u51c6\n- \u547d\u4ee4\uff1a`python accept.py`\n", encoding="utf-8",
        )
        acceptance_marker = alpha_root / "acceptance-ran.txt"
        (alpha_root / "accept.py").write_text(
            "from pathlib import Path\nPath('acceptance-ran.txt').write_text('ran')\n", encoding="utf-8",
        )
        # No per-project command is authorized by the multi-project batch path.

        session = create_new(paths, conversation_id="20261101-aaaa1111")
        session.meta.turn_mode = "agent"
        session.meta.harness = "desktop"
        session.meta.active_shell = "project"
        session.meta.project_id = "project-alpha"
        session.meta.project_root = "workspace/project-alpha"
        session.goal = "Update project-alpha and project-beta independently."
        session._ordinary_work_intent = "execute"
        session.save()

        parent = _ParentModel([
            {"project_id": "project-alpha", "task": "Update alpha.",
             "read_targets": ["README.md"], "write_targets": ["PROJECT.md"]},
            {"project_id": "project-beta", "task": "Update beta.",
             "read_targets": ["README.md"], "write_targets": ["PROJECT.md"]},
        ])
        events = []
        start_marker_snapshots = []

        def emit(event):
            events.append(event)
            if event.get("type") == "activity.update" and event.get("phase") == "started":
                state_path = Path(session.session_dir) / "goal-state.json"
                payload = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
                start_marker_snapshots.append(payload.get("multi_project_unverified"))

        bridge = WsBridge(emit=emit, paths=paths, confirm_timeout=5)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
        repl.agent.llm = parent
        parent.set_cancel_event(repl.agent.cancel_event)
        if disable_goal:
            import goal_runtime
            monkeypatch.setattr(goal_runtime, "goal_runtime_for_session", lambda *_args, **_kwargs: None)
        if fail_goal_persist:
            from goal_runtime import GoalRuntime
            monkeypatch.setattr(
                GoalRuntime, "mark_multi_project_unverified",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("synthetic goal-state persistence failure")),
            )

        def provider(request: httpx.Request) -> httpx.Response:
            request_body = json.loads(request.content.decode("utf-8"))
            if reject_worker and "project_id is 'project-beta'" in request_body["messages"][0]["content"]:
                raise httpx.ConnectError("synthetic beta worker failure", request=request)
            message = {"role": "assistant", "content": "Worker turn returned; acceptance not run.", "tool_calls": []}
            return httpx.Response(200, request=request, json={
                "model": "gpt-6-luna", "choices": [{"finish_reason": "stop", "message": message}],
            })

        monkeypatch.setenv("OX567_API_KEY", "goal-batch-test-secret")
        monkeypatch.setenv("OX567_BASE_URL", "https://api-cdn.0x567.com")
        monkeypatch.setenv("OX567_MODEL_FLASH", "gpt-6-luna")
        monkeypatch.setattr(llm_client.httpx, "Client", lambda **kwargs: _REAL_HTTPX_CLIENT(
            transport=httpx.MockTransport(provider), **kwargs))

        queued = bridge.lifecycle.enqueue("ordinary")
        bridge.lifecycle_conversation_id = session.conversation_id
        bridge._turn_busy.set()

        async def run_and_confirm_every_gate():
            turn = asyncio.create_task(_run_line(
                repl, bridge,
                "Update project-alpha and project-beta in one independent worker batch.",
                paths, lock_held=True, prestarted_run_id=queued.run_id,
            ))
            deadline = asyncio.get_running_loop().time() + 15
            while not turn.done():
                pending_id = bridge._pending_confirm_id
                if pending_id:
                    prompt = next(event.get("preview", "") for event in events if event.get("request_id") == pending_id)
                    is_worker_scope = prompt.startswith("\u9879\u76ee worker \u8303\u56f4\u786e\u8ba4\uff1a")
                    choice = "n" if reject_confirmation and is_worker_scope else "y"
                    assert bridge.deliver_confirm(pending_id, choice)
                if asyncio.get_running_loop().time() >= deadline:
                    bridge.request_cancel()
                    raise AssertionError("Goal-enabled two-project worker batch timed out")
                await asyncio.sleep(0.005)
            await turn

        asyncio.run(asyncio.wait_for(run_and_confirm_every_gate(), timeout=20))
        assert parent.calls == 2
        batch_rows = [
            json.loads(message["content"])
            for message in session.messages
            if message.get("role") == "tool"
            and json.loads(message.get("content", "{}")).get("tool") == "run_project_workers"
        ]
        assert batch_rows
        batch_result = batch_rows[-1]
        assert not acceptance_marker.exists(), "no path may auto-run the PROJECT.md shell command"
        state_path = Path(session.session_dir) / "goal-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else None
        if disable_goal:
            rows = batch_result["data"]["workers"]
            assert {row["project_id"] for row in rows} == {"project-alpha", "project-beta"}
            assert all(row["status"] == "model_turn_finished" for row in rows)
            assert start_marker_snapshots == [None, None]
            assert not (Path(session.session_dir) / "goal-state.json").exists()
            assert not any("\u591a\u9879\u76ee worker \u6279\u6b21\u672a\u9010\u9879\u9a8c\u6536" in (message.get("content") or "")
                           for message in session.messages if message.get("role") == "assistant")
            return
        if fail_goal_persist:
            rows = batch_result["data"]["workers"]
            assert rows and all(row["status"] == "blocked" for row in rows)
            assert state["multi_project_unverified"] is False
            assert not start_marker_snapshots
            assert state["status"] == "active"
            return
        if reject_confirmation:
            rejected_rows = batch_result["data"]["workers"]
            assert rejected_rows and all(row["status"] == "blocked" for row in rejected_rows)
            assert state["multi_project_unverified"] is False
            assert state["multi_project_ids"] == []
            assert state["status"] == "active"
            assert any("\u5c1a\u672a\u542f\u52a8" in (message.get("content") or "")
                       for message in session.messages if message.get("role") == "assistant")
            assert not start_marker_snapshots
            assert not any(event.get("type") == "activity.update" and event.get("phase") == "started"
                           for event in events)
            return

        rows = batch_result["data"]["workers"]
        assert {row["project_id"] for row in rows} == {"project-alpha", "project-beta"}
        assert all(row["acceptance_status"] == "not_run" for row in rows)
        failed_rows = [row for row in rows if row["status"] in {"blocked", "failed"}]
        assert bool(failed_rows) is reject_worker
        assert any(
            message.get("role") == "assistant" and "\u591a\u9879\u76ee worker \u6279\u6b21\u672a\u9010\u9879\u9a8c\u6536" in (message.get("content") or "")
            for message in session.messages
        )
        assert state["status"] == "active"
        assert state["multi_project_unverified"] is True
        assert state["multi_project_ids"] == ["project-alpha", "project-beta"]
        assert start_marker_snapshots and all(value is True for value in start_marker_snapshots)
        assert any(event.get("type") == "activity.update" and event.get("phase") == "started"
                   for event in events)
        assert "required_project_ids" not in state
        assert "project_verifications" not in state
        reloaded = __import__("goal_runtime").GoalRuntime(Path(session.session_dir))
        loaded = reloaded.load()
        assert loaded is not None
        assert loaded.status == "paused"
        assert loaded.multi_project_unverified is True
