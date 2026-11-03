from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import llm_client
from project_mode import create_project
from server import WsBridge, _build_repl
from session import create_new
from tests.isolation_helpers import temporary_agent_paths

_REAL_HTTPX_CLIENT = httpx.Client


def _tool_call(name: str, args: dict[str, Any], index: int):
    return {"id": f"call-{index}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def _response(request: httpx.Request, message: dict[str, Any], finish: str) -> httpx.Response:
    return httpx.Response(200, request=request, json={
        "model": "gpt-6-luna",
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
    })


def _mock_host(paths, monkeypatch, handler):
    monkeypatch.setenv("OX567_API_KEY", "integration-test-key")
    monkeypatch.setenv("OX567_BASE_URL", "https://api-cdn.0x567.com")
    monkeypatch.setenv("OX567_MODEL_FLASH", "gpt-6-luna")
    monkeypatch.setattr(
        llm_client.httpx,
        "Client",
        lambda **kwargs: _REAL_HTTPX_CLIENT(transport=httpx.MockTransport(handler), **kwargs),
    )
    from llm_client import LLMClient
    from project_worker_host import ProjectWorkerHost

    return ProjectWorkerHost(lambda: LLMClient(paths=paths))


def test_agent_host_dispatch_keeps_two_project_writes_and_independent_write_scoped(monkeypatch):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file")) as paths:
        for project_id in ("project-alpha", "project-beta"):
            create_project(paths, project_id, project_template="light")
        session = create_new(paths, conversation_id="20261030-livebound1")
        session.meta.active_shell = "project"
        session.meta.harness = "desktop"
        session.meta.project_id = "project-alpha"
        session.meta.project_root = "workspace/project-alpha"
        session.meta.turn_mode = "agent"
        session._ordinary_work_intent = "execute"
        session.messages.append({
            "role": "user",
            "content": "Modify project-alpha and project-beta, and write workspace/independent-note.txt independently.",
        })
        repl = _build_repl(
            session, paths, WsBridge(emit=lambda _event: None, paths=paths),
            work_record_controller_factory=lambda *_: None,
        )
        both_workers_posted = threading.Event()
        independent_finished = threading.Event()
        independent_outcomes: list[Any] = []
        first_projects_posted: set[str] = set()
        wire: list[dict[str, Any]] = []
        wire_lock = threading.Lock()

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content.decode("utf-8"))
            system = payload["messages"][0]["content"]
            project_id = next(value for value in ("project-alpha", "project-beta")
                              if f"project_id is '{value}'" in system)
            task = payload["messages"][1]["content"]
            with wire_lock:
                wire.append(payload)
                if not any(message.get("role") == "tool" for message in payload["messages"]):
                    first_projects_posted.add(project_id)
                    if first_projects_posted == {"project-alpha", "project-beta"}:
                        both_workers_posted.set()
            if not any(message.get("role") == "tool" for message in payload["messages"]):
                assert both_workers_posted.wait(4), "both projects were not active together"
                assert independent_finished.wait(6), (
                    "independent write did not return while both project requests were active"
                )
                assert independent_outcomes and independent_outcomes[0].ok, independent_outcomes
                assert (paths.workspace / "independent-note.txt").read_text(encoding="utf-8") == "independent verified\n"
                return _response(request, {"role": "assistant", "content": None,
                    "tool_calls": [_tool_call("write_project_file", {
                        "path": "result.txt", "content": f"verified:{project_id}:{task}\n"}, 1)]}, "tool_calls")
            return _response(request, {"role": "assistant", "content": f"finished {project_id}",
                                       "tool_calls": []}, "stop")

        # Use the real independent host writer, but start it only after both
        # project requests are inside the transport.  The transport waits until
        # the independent writer actually returns, so all three lanes overlap.
        import project_worker_independent
        real_independent_write = project_worker_independent.run_independent_write_from_host

        def observed_independent_write(*args, **kwargs):
            try:
                if not both_workers_posted.wait(6):
                    raise TimeoutError("project requests did not overlap")
                result = real_independent_write(*args, **kwargs)
                independent_outcomes.append(result)
                return result
            finally:
                independent_finished.set()

        monkeypatch.setattr(
            project_worker_independent, "run_independent_write_from_host", observed_independent_write,
        )
        repl.agent.project_worker_host = _mock_host(paths, monkeypatch, handler)
        prompts: list[str] = []
        prompt_lock = threading.Lock()
        repl.agent.executor.confirm_fn = lambda preview, _all: (
            prompt_lock.acquire(), prompts.append(preview), prompt_lock.release(), "y"
        )[-1]
        run_id, epoch = "integration-run", 1
        repl.agent.bind_project_worker_live_context(
            run_id, epoch,
            lambda bound, active_run, active_epoch: (
                bound is session and active_run == run_id and active_epoch == epoch
            ),
        )

        outcomes: list[Any] = []
        dispatch = threading.Thread(target=lambda: outcomes.append(repl.agent.executor.run(
            "run_project_workers", {
                "workers": [
                    {"project_id": "project-alpha", "task": "ALPHA", "read_targets": [], "write_targets": ["result.txt"]},
                    {"project_id": "project-beta", "task": "BETA", "read_targets": [], "write_targets": ["result.txt"]},
                ],
                "independent_write": {
                    "target_path": "workspace/independent-note.txt",
                    "content": "independent verified\n",
                },
            }
        )))
        dispatch.start()
        dispatch.join(15)
        assert not dispatch.is_alive(), "Agent host dispatch did not complete"
        assert len(outcomes) == 1
        result = outcomes[0]
        assert result.ok, result.error
        payload = result.to_dict()
        rows = payload["data"]["workers"]
        assert payload["data"]["independent_write"]["ok"] is True
        assert {row["project_id"] for row in rows} == {"project-alpha", "project-beta"}
        assert all(row["status"] == "model_turn_finished" for row in rows), rows
        assert all(row["writes"][0]["verification"] == "content_match" for row in rows)
        assert (paths.workspace / "project-alpha/result.txt").read_text(encoding="utf-8").startswith("verified:project-alpha:")
        assert (paths.workspace / "project-beta/result.txt").read_text(encoding="utf-8").startswith("verified:project-beta:")
        assert (paths.workspace / "independent-note.txt").read_text(encoding="utf-8") == "independent verified\n"
        assert len(wire) == 4
        assert session.meta.project_id == "project-alpha"
        assert sum("独立事项目标范围确认" in prompt for prompt in prompts) == 1
        assert sum("独立事项文件写入确认" in prompt for prompt in prompts) == 1

