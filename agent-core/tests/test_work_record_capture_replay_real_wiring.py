from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import llm_client
import project_api
import server
import work_records_luna_intent
import work_records_service
from paths import AgentPaths
from server import WsBridge, WsSessionHandler
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_records_service import WorkRecordCaptureService
from work_records_luna_intent import EXPECTED_PROVIDER_MODEL, EXPECTED_VENDOR, MODEL_ID


REQUEST_ID = "bb8fb5bc-2892-4a0b-909d-14f32b0f48fd"
ORIGINAL_TEXT = "\u8bf7\u8bb0\u5f55\u4e00\u4e0b\u72ec\u7acb\u4e8b\u9879\uff1a\u5408\u6210\u56de\u653e\u6d4b\u8bd5\u9700\u4ea4\u4ed8\u4e00\u4efd\u62a5\u544a\uff0c\u9a8c\u6536\u6807\u51c6\u662f\u5305\u542b\u4e09\u4e2a\u89c2\u5bdf\u6761\u76ee\u3002"
CHANGED_TEXT = "\u8bf7\u8bb0\u5f55\u4e00\u4e0b\u72ec\u7acb\u4e8b\u9879\uff1a\u5408\u6210\u56de\u653e\u6d4b\u8bd5\u9700\u4ea4\u4ed8\u4e00\u4efd\u6e05\u5355\uff0c\u9a8c\u6536\u6807\u51c6\u662f\u5305\u542b\u4e09\u4e2a\u89c2\u5bdf\u6761\u76ee\u3002"


class FakeLunaClient:
    def __init__(self, paths):
        self.paths = paths
        self.calls = []
        self.cancel_event = None

    def set_cancel_event(self, event):
        self.cancel_event = event

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        decision = {
            "action": "record",
            "kind": "requirement",
            "title": "Synthetic replay report",
            "requirement": "Deliver a synthetic report containing three observation rows",
            "scope": "independent",
            "item_id": None,
        }
        return SimpleNamespace(content=json.dumps(decision), tool_calls=[])


def make_session(tmp_path: Path) -> Session:
    root = tmp_path / "agent-root"
    root.mkdir()
    paths = AgentPaths(
        agent_root=root,
        workspace=root / "workspace",
        data=root / "data",
        evolve=root / "evolve",
    )
    session_dir = paths.data / "sessions" / "synthetic-replay-session"
    session_dir.mkdir(parents=True)
    session = Session(
        conversation_id="synthetic-replay-session",
        session_dir=session_dir,
        goal="synthetic replay integration",
        meta=SessionMeta(),
        messages=[],
        paths=paths,
    )
    session.save()
    return session


async def _wait_for_confirm_request(events, index: int, task):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while loop.time() < deadline:
        requests = [event for event in events if event.get("type") == "confirm.request"]
        if len(requests) > index:
            return requests[index]
        if task.done():
            await task
            raise AssertionError(f"expected confirmation request {index + 1}")
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for confirmation request {index + 1}")


async def _dispatch_and_approve(handler, message, repl, bridge, events, expected_new_confirms):
    baseline = sum(event.get("type") == "confirm.request" for event in events)
    task = asyncio.create_task(handler._dispatch(message, repl, bridge))
    for offset in range(expected_new_confirms):
        event = await _wait_for_confirm_request(events, baseline + offset, task)
        assert bridge.deliver_confirm(event["request_id"], "y") is True
    await asyncio.wait_for(task, timeout=5)


def _backup_snapshot(session: Session, store: WorkRecordStore):
    sidecar = session.session_dir / "work-records.v1.json"
    key = hashlib.sha256(sidecar.resolve(strict=True).as_posix().encode("utf-8")).hexdigest()[:16]
    backup_dir = session.paths.agent_root / "data" / ".file-guard" / key
    assert backup_dir.is_dir()
    from guard_snapshots import STAMP, safe_container

    assert safe_container(backup_dir)
    files = sorted(path for path in backup_dir.iterdir() if STAMP.fullmatch(path.name))
    assert files
    snapshots = {}
    for path in files:
        info = path.lstat()
        assert stat.S_ISREG(info.st_mode)
        assert not (getattr(info, "st_file_attributes", 0) & 0x400)
        assert getattr(info, "st_nlink", 1) == 1
        payload = json.loads(path.read_text(encoding="utf-8"))
        store._validate_payload(payload)
        snapshots[path.name] = path.read_bytes()
    return backup_dir, snapshots


def test_real_user_message_replay_and_request_id_conflict_do_not_repeat_any_side_effect(
    tmp_path, monkeypatch,
):
    session = make_session(tmp_path)
    paths = session.paths
    events = []
    bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=5)
    client = FakeLunaClient(paths)
    monkeypatch.setattr(llm_client, "LLMClient", lambda **_kwargs: client)
    monkeypatch.setattr(work_records_luna_intent, "get_registry", lambda *_args: SimpleNamespace(
        get=lambda model_id: SimpleNamespace(
            id=MODEL_ID,
            vendor=EXPECTED_VENDOR,
            provider_model=EXPECTED_PROVIDER_MODEL,
            max_input_tokens=100_000,
        ) if model_id == MODEL_ID else None,
    ))
    monkeypatch.setattr(server, "session_memory_event", lambda *_args: {"type": "session.memory"})
    monkeypatch.setattr(server, "_proposals_payload", lambda *_args: {"type": "evolve.proposals", "items": []})
    monkeypatch.setattr(server, "_memories_payload", lambda *_args: {"type": "evolve.memories", "items": []})
    monkeypatch.setattr(project_api, "after_turn_project_hooks", lambda *_args: None)

    capture_calls = []
    original_capture = WorkRecordCaptureService.capture
    def observe_capture(self, text, *, request_id=None, track_run=False):
        result = original_capture(self, text, request_id=request_id, track_run=track_run)
        capture_calls.append((text, request_id, track_run, result.status))
        return result
    monkeypatch.setattr(WorkRecordCaptureService, "capture", observe_capture)

    mutation_calls = []
    original_mutate = WorkRecordStore._mutate
    def observe_mutation(self, *args, **kwargs):
        mutation_calls.append((self._session.conversation_id, kwargs.get("return_commit_status", False)))
        return original_mutate(self, *args, **kwargs)
    monkeypatch.setattr(WorkRecordStore, "_mutate", observe_mutation)

    executions = []
    repl = SimpleNamespace(
        session=session,
        last_turn_finish_reason=None,
        agent=SimpleNamespace(
            bind_host_run_context=None,
            executor=SimpleNamespace(session=SimpleNamespace(evolution_authorization_id="")),
        ),
        handle_line=lambda line: executions.append(line) or "continue",
    )
    handler = object.__new__(WsSessionHandler)
    handler.paths = paths
    handler.managed_readonly_context = None
    handler._file_stage = SimpleNamespace(take=lambda *_args: [])
    message = {"type": "user.message", "text": ORIGINAL_TEXT, "request_id": REQUEST_ID}

    asyncio.run(_dispatch_and_approve(handler, message, repl, bridge, events, 2))

    requests = [event for event in events if event.get("type") == "confirm.request"]
    assert len(requests) == 2
    assert requests[0]["preview"].startswith("\u5de5\u4f5c\u8bb0\u5f55\u5355\u6b21\u5916\u53d1\u786e\u8ba4")
    assert requests[1]["preview"].startswith("\u5de5\u4f5c\u8bb0\u5f55\u672c\u5730\u843d\u76d8\u786e\u8ba4")
    assert capture_calls == [(ORIGINAL_TEXT, REQUEST_ID, True, "recorded")]
    assert len(client.calls) == 1
    assert executions == [ORIGINAL_TEXT]
    terminal = bridge.lifecycle.snapshot()
    assert terminal is not None and terminal.state == "settled"
    assert terminal.finish_reason == "completed"
    first_run_id = terminal.run_id

    store = WorkRecordStore(session)
    items = store.list_items()
    assert len(items) == 1
    item = items[0]
    requirement = item["requirements"][0]
    assert requirement["current_run_id"] == first_run_id
    assert requirement["status"] == "unknown"
    assert len(item["runs"]) == 1
    run = item["runs"][0]
    assert run["run_id"] == first_run_id
    assert run["status"] == "unknown"
    assert run["verification"]["status"] == "not_verified"
    assert run["verification"]["evidence_refs"] == []

    sidecar_path = session.session_dir / "work-records.v1.json"
    sidecar_after_first = sidecar_path.read_bytes()
    backup_dir, backups_after_first = _backup_snapshot(session, store)
    mutation_count_after_first = len(mutation_calls)
    assert mutation_count_after_first == 3  # capture commit, run bind, unknown settlement
    confirm_count_after_first = len(requests)

    asyncio.run(_dispatch_and_approve(handler, message, repl, bridge, events, 0))
    replay_terminal = bridge.lifecycle.snapshot()
    assert replay_terminal is not None and replay_terminal.run_id != first_run_id
    assert replay_terminal.state == "failed"
    assert capture_calls[-1] == (ORIGINAL_TEXT, REQUEST_ID, True, "already_recorded")
    assert len(capture_calls) == 2
    assert len(client.calls) == 1
    assert len(executions) == 1
    assert sum(event.get("type") == "confirm.request" for event in events) == confirm_count_after_first
    assert len(mutation_calls) == mutation_count_after_first
    assert sidecar_path.read_bytes() == sidecar_after_first
    assert _backup_snapshot(session, store)[1] == backups_after_first
    replay_notices = [
        event for event in events
        if event.get("type") == "turn.notice" and "\u6b64\u524d\u5df2\u6709\u8bb0\u5f55" in event.get("text", "")
    ]
    assert replay_notices

    conflict_message = {**message, "text": CHANGED_TEXT}
    asyncio.run(_dispatch_and_approve(handler, conflict_message, repl, bridge, events, 0))
    conflict_terminal = bridge.lifecycle.snapshot()
    assert conflict_terminal is not None and conflict_terminal.state == "failed"
    assert capture_calls[-1] == (CHANGED_TEXT, REQUEST_ID, True, "request_conflict")
    assert len(capture_calls) == 3
    assert len(client.calls) == 1
    assert executions == [ORIGINAL_TEXT]
    assert sum(event.get("type") == "confirm.request" for event in events) == confirm_count_after_first
    assert len(mutation_calls) == mutation_count_after_first
    assert sidecar_path.read_bytes() == sidecar_after_first
    assert _backup_snapshot(session, store)[1] == backups_after_first
    assert len(store.list_items()) == 1
    assert len(store.get_item(item["item_id"])["runs"]) == 1
    assert backup_dir.is_dir()