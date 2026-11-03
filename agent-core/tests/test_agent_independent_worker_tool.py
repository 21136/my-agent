from __future__ import annotations

import sys
import threading

import pytest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import build_builtin_tool_definition
from project_mode import create_project
from server import WsBridge, _build_repl
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


def test_main_agent_tool_can_schedule_one_explicit_independent_file_write():
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "bound-project", project_template="light")
        session = create_new(paths, conversation_id="20261030-independent01")
        session.meta.active_shell = "project"
        session.meta.project_id = "bound-project"
        session.meta.project_root = "workspace/bound-project"
        session.meta.turn_mode = "agent"
        session._ordinary_work_intent = "execute"
        session.messages.append({
            "role": "user",
            "content": "Write workspace/independent-note.txt with the independent note content.",
        })
        bridge = WsBridge(emit=lambda _event: None, paths=paths)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_: None)
        repl.agent.executor.confirm_fn = lambda _preview, _allow_all: "y"
        repl.agent.bind_project_worker_live_context("live-run-1", 1, lambda *_args: True)

        definition = build_builtin_tool_definition("run_project_workers")["function"]
        assert "independent_write" in definition["parameters"]["properties"]
        assert definition["parameters"]["anyOf"] == [{"required": ["workers"]}, {"required": ["independent_write"]}]
        result = repl.agent.executor.run("run_project_workers", {
            "independent_write": {
                "target_path": "workspace/independent-note.txt",
                "content": "Independent note from main conversation.",
            },
        })

        target = paths.workspace / "independent-note.txt"
        assert result.ok, result.error
        assert target.read_text(encoding="utf-8") == "Independent note from main conversation."
        assert session.meta.project_id == "bound-project"


def test_main_agent_rejects_model_invented_independent_target():
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "bound-project", project_template="light")
        session = create_new(paths, conversation_id="20261030-independent02")
        session.meta.active_shell = "project"
        session.meta.project_id = "bound-project"
        session.meta.project_root = "workspace/bound-project"
        session.meta.turn_mode = "agent"
        session._ordinary_work_intent = "execute"
        session.messages.append({"role": "user", "content": "Write the requested note."})
        repl = _build_repl(
            session, paths, WsBridge(emit=lambda _event: None, paths=paths),
            work_record_controller_factory=lambda *_: None,
        )
        repl.agent.executor.confirm_fn = lambda _preview, _allow_all: "y"
        repl.agent.bind_project_worker_live_context("live-run-2", 1, lambda *_args: True)

        result = repl.agent.executor.run("run_project_workers", {
            "independent_write": {
                "target_path": "workspace/model-invented.txt",
                "content": "must not be written",
            },
        })

        assert not result.ok
        assert result.error.code == "permission_denied"
        assert "explicitly named" in result.error.message
        assert not (paths.workspace / "model-invented.txt").exists()


def test_independent_write_runs_concurrently_with_worker_host_and_partial_result_is_explicit():
    import time

    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "bound-project", project_template="light")
        session = create_new(paths, conversation_id="20261030-independent03")
        session.meta.active_shell = "project"
        session.meta.project_id = "bound-project"
        session.meta.project_root = "workspace/bound-project"
        session.meta.turn_mode = "agent"
        session._ordinary_work_intent = "execute"
        session.messages.append({
            "role": "user",
            "content": "Write workspace/parallel-note.txt and update the bound-project PROJECT.md.",
        })
        prompts = []
        repl = _build_repl(
            session, paths, WsBridge(emit=lambda _event: None, paths=paths),
            work_record_controller_factory=lambda *_: None,
        )
        repl.agent.executor.confirm_fn = lambda preview, _allow_all: prompts.append(preview) or "y"
        repl.agent.bind_project_worker_live_context("live-run-3", 1, lambda *_args: True)
        target = paths.workspace / "parallel-note.txt"
        host_started = threading.Event()

        def fail_after_concurrent_lane_finishes(*_args, **_kwargs):
            host_started.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not target.exists():
                time.sleep(0.01)
            assert target.exists(), "independent lane did not overlap the active worker-host call"
            raise RuntimeError("injected worker-host failure before dispatch")

        repl.agent.project_worker_host.run_many = fail_after_concurrent_lane_finishes
        result = repl.agent.executor.run("run_project_workers", {
            "workers": [{
                "project_id": "bound-project",
                "task": "Update PROJECT.md with the requested project note.",
                "read_targets": [],
                "write_targets": ["PROJECT.md"],
            }],
            "independent_write": {
                "target_path": "workspace/parallel-note.txt",
                "content": "Independent lane completed.",
            },
        })

        assert host_started.is_set()
        assert not result.ok
        assert result.error.code == "worker_dispatch_failed"
        assert result.error.details["project_worker_dispatch_status"] == "failed"
        assert result.error.details["independent_write"]["ok"] is True
        assert target.read_text(encoding="utf-8") == "Independent lane completed."
        assert sum("独立事项目标范围确认" in prompt for prompt in prompts) == 1
        assert sum("独立事项文件写入确认" in prompt for prompt in prompts) == 1


def test_main_agent_does_not_treat_a_negated_write_as_authorization():
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "bound-project", project_template="light")
        session = create_new(paths, conversation_id="20261030-independent04")
        session.meta.active_shell = "project"
        session.meta.project_id = "bound-project"
        session.meta.project_root = "workspace/bound-project"
        session.meta.turn_mode = "agent"
        session._ordinary_work_intent = "execute"
        session.messages.append({
            "role": "user",
            "content": "Do not write workspace/forbidden-note.txt.",
        })
        prompts = []
        repl = _build_repl(
            session, paths, WsBridge(emit=lambda _event: None, paths=paths),
            work_record_controller_factory=lambda *_: None,
        )
        repl.agent.executor.confirm_fn = lambda preview, _allow_all: prompts.append(preview) or "y"
        repl.agent.bind_project_worker_live_context("live-run-4", 1, lambda *_args: True)

        result = repl.agent.executor.run("run_project_workers", {
            "independent_write": {
                "target_path": "workspace/forbidden-note.txt",
                "content": "must not be written",
            },
        })

        assert not result.ok
        assert result.error.code == "permission_denied"
        assert prompts == []
        assert not (paths.workspace / "forbidden-note.txt").exists()


def test_main_agent_rejects_present_but_invalid_workers_field():
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "bound-project", project_template="light")
        session = create_new(paths, conversation_id="20261030-independent05")
        session.meta.active_shell = "project"
        session.meta.project_id = "bound-project"
        session.meta.project_root = "workspace/bound-project"
        session.meta.turn_mode = "agent"
        session._ordinary_work_intent = "execute"
        session.messages.append({"role": "user", "content": "Write workspace/strict-note.txt."})
        repl = _build_repl(
            session, paths, WsBridge(emit=lambda _event: None, paths=paths),
            work_record_controller_factory=lambda *_: None,
        )
        repl.agent.executor.confirm_fn = lambda _preview, _allow_all: "y"
        repl.agent.bind_project_worker_live_context("live-run-5", 1, lambda *_args: True)

        result = repl.agent._dispatch_project_workers_tool({
            "workers": None,
            "independent_write": {
                "target_path": "workspace/strict-note.txt",
                "content": "must not be written",
            },
        })

        assert not result.ok
        assert not (paths.workspace / "strict-note.txt").exists()


@pytest.mark.parametrize(
    ("user_request", "should_write"),
    [
        ("\u5206\u522b\u4fee\u6539\u9879\u76eeA\u548c\u7b80\u5386 workspace/resume.md\u3002", True),
        ("\u7279\u522b\u662f\u4fee\u6539\u9879\u76eeA\u548c\u7b80\u5386 workspace/resume.md\u3002", True),
        ("\u4fee\u6539\u7b80\u5386 workspace/resume.md\uff0c\u4e0d\u8981\u4fee\u6539\u5176\u4ed6\u6587\u4ef6\u3002", True),
        ("\u4fee\u6539\u9879\u76eeA\uff0c\u4e0d\u8981\u4fee\u6539\u5176\u4ed6\u6587\u4ef6 workspace/resume.md\u3002", False),
        ("\u4e0d\u8981\u4fee\u6539 workspace/resume.md\u3002", False),
    ],
)
def test_chinese_target_negation_is_scoped_in_real_agent_tool_chain(user_request, should_write):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "bound-project", project_template="light")
        session = create_new(paths, conversation_id="20261030-cn-scope01")
        session.meta.active_shell = "project"
        session.meta.project_id = "bound-project"
        session.meta.project_root = "workspace/bound-project"
        session.meta.turn_mode = "agent"
        session._ordinary_work_intent = "execute"
        session.messages.append({"role": "user", "content": user_request})
        prompts = []
        repl = _build_repl(
            session, paths, WsBridge(emit=lambda _event: None, paths=paths),
            work_record_controller_factory=lambda *_: None,
        )
        repl.agent.executor.confirm_fn = lambda preview, _allow_all: prompts.append(preview) or "y"
        repl.agent.bind_project_worker_live_context("live-cn-run", 1, lambda *_args: True)

        result = repl.agent.executor.run("run_project_workers", {
            "independent_write": {
                "target_path": "workspace/resume.md",
                "content": "explicitly requested resume update",
            },
        })

        target = paths.workspace / "resume.md"
        assert result.ok is should_write, result.error
        if should_write:
            assert target.read_text(encoding="utf-8") == "explicitly requested resume update"
            assert sum("独立事项目标范围确认" in preview for preview in prompts) == 1
            assert sum("独立事项文件写入确认" in preview for preview in prompts) == 1
        else:
            assert not target.exists()
            assert prompts == []
