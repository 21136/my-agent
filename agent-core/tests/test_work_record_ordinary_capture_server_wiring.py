from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import llm_client
import server
from paths import AgentPaths
from server import WsBridge, WsSessionHandler
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_records_service import is_explicit_record_request


REQUEST_ID = "bb8fb5bc-2892-4a0b-909d-14f32b0f48fd"
INDEPENDENT_TEXT = "这是一个独立事项：请完成合成报告，交付两条观测条目，并以报告包含两条条目作为验收标准。"
PROJECT_TEXT = "当前项目需要新增导出功能，请实现 CSV 导出，并以导出文件可重新读取作为验收标准。"


@pytest.mark.parametrize(
    ("text", "project_id"),
    [(INDEPENDENT_TEXT, None), (PROJECT_TEXT, "project-alpha")],
)
@pytest.mark.parametrize("request_id", [REQUEST_ID, None, "not-a-uuid"])
def test_ordinary_work_is_not_intercepted_by_optional_capture(
    tmp_path, monkeypatch, text, project_id, request_id,
):
    # The pre-existing explicit gate also matches the noun "记录"; keep this
    # regression independent of that separate, unchanged behavior.
    assert not is_explicit_record_request(text)
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    session_dir = paths.data / "sessions" / "capture-session"
    session_dir.mkdir(parents=True)
    session = Session("capture-session", session_dir, "", SessionMeta(project_id=project_id), [], paths)
    events = []
    bridge = WsBridge(emit=events.append, paths=paths)
    executed = []

    async def run_line(_repl, _bridge, line, *_args, **_kwargs):
        executed.append(line)

    def unexpected_capture_client(**_kwargs):
        pytest.fail("ordinary work must not construct a capture classifier")

    monkeypatch.setattr(server, "_run_line", run_line)
    monkeypatch.setattr(llm_client, "LLMClient", unexpected_capture_client)
    repl = SimpleNamespace(session=session, last_turn_finish_reason=None)
    handler = object.__new__(WsSessionHandler)
    handler.paths = paths
    handler.managed_readonly_context = None
    handler._file_stage = SimpleNamespace(take=lambda *_args: [])

    asyncio.run(handler._dispatch(
        {"type": "user.message", "text": text, "request_id": request_id}, repl, bridge,
    ))

    assert executed == [text]
    assert not any(event.get("type") == "confirm.request" for event in events)
    assert WorkRecordStore(session).list_items() == []
