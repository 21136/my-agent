"""Isolated real WS/Agent/Host fixture for the browser batch-result E2E."""
from __future__ import annotations
import asyncio, json, logging, pathlib, sys, threading, uuid

core = pathlib.Path(sys.argv[1]).resolve()
sys.path[:0] = [str(core), str(core / "tests")]
import httpx, pytest, server
from websockets.asyncio.server import serve
from project_mode import create_project
from session import create_new
from tests.isolation_helpers import temporary_agent_paths
from tests.test_agent_project_worker_tool import _ParentModel
from llm_client import LLMResponse
from tests.test_project_worker_host import _host, _response

PROJECTS = ("browser-batch-success", "browser-batch-failure")
REQUEST = "Run project workers for both isolated projects; report each actual result."

class _OutcomeReportingParent(_ParentModel):
    def chat(self, messages, **kwargs):
        if self.calls == 0:
            return super().chat(messages, **kwargs)
        self.tools_seen.append(kwargs.get("tools"))
        self.calls += 1
        tool_result = next(message["content"] for message in reversed(messages)
                           if message.get("role") == "tool")
        payload = json.loads(tool_result)
        rows = payload["data"]["workers"]
        text = "; ".join(f'{row["project_id"]} {row["status"]}' for row in rows)
        return LLMResponse(model="parent-synthetic", content=f"Actual host outcomes: {text}",
                           tool_calls=[], finish_reason="stop", usage=None, raw={})

def model(request):
    payload = json.loads(request.content.decode())
    if PROJECTS[1] in json.dumps(payload):
        return httpx.Response(503, request=request, json={"error": "isolated synthetic failure"})
    return _response(request, {"role": "assistant", "content": "Synthetic worker completed.", "tool_calls": []}, "stop")

async def main():
    with temporary_agent_paths(copy_tool_dirs=("common/interactive_terminal", "common/run_service")) as paths:
        for pid in PROJECTS: create_project(paths, pid, project_template="light")
        session = create_new(paths, conversation_id="browser-batch-isolated")
        session.meta.turn_mode = "agent"; session.meta.harness = "desktop"; session.save()
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(server, "resume_desktop_or_create", lambda _paths: session)
        original = server._build_repl
        parent = _OutcomeReportingParent([
            {"project_id": PROJECTS[0], "task": "Report success", "read_targets": ["PROJECT.md"], "write_targets": ["NOTES.md"]},
            {"project_id": PROJECTS[1], "task": "Report failure", "read_targets": ["PROJECT.md"], "write_targets": ["NOTES.md"]},
        ])
        def build(bound_session, bound_paths, bridge, **kwargs):
            repl = original(bound_session, bound_paths, bridge, **kwargs); repl.agent.llm = parent
            repl.agent.project_worker_host = _host(bound_paths, monkeypatch, model)[0]
            return repl
        monkeypatch.setattr(server, "_build_repl", build)
        monkeypatch.setattr(server, "emit_corruption_notices", lambda *_a: None)
        monkeypatch.setattr(server, "_emit_pending_work_card_references", lambda *_a: None)
        async with serve(server.WsSessionHandler(paths).handle, "127.0.0.1", 0) as ws:
            print(json.dumps({"port": ws.sockets[0].getsockname()[1]}), flush=True)
            await asyncio.to_thread(sys.stdin.buffer.readline)
        monkeypatch.undo()
asyncio.run(main())
