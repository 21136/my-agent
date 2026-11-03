from __future__ import annotations

"""Opt-in real-provider component probe, not a Desktop/Agent end-to-end test.

The harness explicitly binds one synthetic project requirement, asks the real
provider for one tool call, allows only an exact read of one temporary file,
and uses the actual server begin/settle helpers. No tool output is sent back.
"""

import json
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from agent import Agent
from evolution.context import normalize_host_run_id
from llm_client import LLMClient, load_config
from llm_models import get_registry
from paths import AgentPaths
from server import WsBridge, _begin_work_record_run_binding, _settle_work_record_run
from session import Session, SessionMeta
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from work_records import WorkRecordStore

OPT_IN = "MY_AGENT_LIVE_WORK_RECORD_TOOL_RECEIPT_OPTIN"
MODEL = "0x567-flash"
RELATIVE = "workspace/synthetic-project/sample.txt"
TOOL = {"type": "function", "function": {"name": "read_file", "description": "Read a synthetic fixture",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                   "required": ["path"], "additionalProperties": False}}}
PROMPT = "Synthetic test only: call read_file exactly once with path workspace/synthetic-project/sample.txt. Do not use another tool."


def _requested_safe_read(response):
    """Reject every call except exactly one requested read of the fixture."""
    calls = getattr(response, "tool_calls", None)
    if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
        return False
    fn = calls[0].get("function")
    if not isinstance(fn, dict) or fn.get("name") != "read_file":
        return False
    try:
        args = json.loads(fn.get("arguments", ""))
    except (ValueError, TypeError):
        return False
    return type(args) is dict and args == {"path": RELATIVE}


def _fixture(tmp_path):
    root = tmp_path / "synthetic-root"
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    session_dir = paths.data / "sessions" / "synthetic-session"
    session_dir.mkdir(parents=True)
    paths.evolve.mkdir(parents=True)
    file = root / RELATIVE
    file.parent.mkdir(parents=True)
    file.write_text("SYNTHETIC FIXTURE CONTENT\n", encoding="utf-8")
    session = Session("synthetic-session", session_dir, "", SessionMeta(project_id="synthetic-project", project_root="workspace/synthetic-project"), [], paths)
    store = WorkRecordStore(session)
    item = store.create_item(title="Synthetic read", scope={"kind": "project", "project_id": "synthetic-project"})
    requirement = store.add_requirement(item["item_id"], "Read the synthetic fixture", event_id=str(uuid.uuid4()), idempotency_key="synthetic-requirement")
    bridge = WsBridge(emit=lambda _: None, paths=paths)
    bridge.lifecycle_conversation_id = session.conversation_id
    run = bridge.lifecycle.start_or_activate("ordinary")
    capture = SimpleNamespace(status="recorded", item_id=item["item_id"], requirement_id=requirement["requirement_id"])
    bound = _begin_work_record_run_binding(session=session, capture=capture, run_id=run.run_id,
                                           current_session=session, bridge=bridge, emit=lambda _: None)
    assert isinstance(bound, tuple), "synthetic run binding failed"
    return paths, session, bridge, bound, item, requirement, run


def _execute_and_settle(fixture):
    paths, session, bridge, (store, binding), item, requirement, run = fixture
    # The executor's trusted API canonicalizes lifecycle hex IDs to UUIDs.
    executor_session = ExecutorSession(session_dir=session.session_dir, host_run_id=run.run_id,
                                       project_id="synthetic-project", project_root="workspace/synthetic-project")
    executor = ToolExecutor(registry=ToolRegistry.load(paths), session=executor_session,
                            confirm_fn=lambda *_a, **_kw: "n")
    # This is a component harness: the Agent carries the real Session and
    # executor, but does not itself issue a model request or run the tool loop.
    agent = Agent(session=session, executor=executor, llm=None)
    executor.begin_turn()
    result = executor.run("read_file", {"path": RELATIVE})
    assert result.ok is True, "synthetic read_file did not succeed"
    # One harness-observed result message for the single real executor call.
    tool_messages = [{"role": "tool"}]
    generation, host_run_id, receipts, omitted = executor_session.snapshot_host_tool_receipts()
    assert generation > 0 and host_run_id == normalize_host_run_id(run.run_id)
    assert len(receipts) == 1 and omitted == 0
    assert set(receipts[0]) == {"host_run_id", "call_id", "tool", "ok"}
    assert receipts[0]["host_run_id"] == host_run_id
    assert receipts[0]["tool"] == "read_file" and receipts[0]["ok"] is True
    assert str(uuid.UUID(receipts[0]["call_id"])) == receipts[0]["call_id"]
    assert "sample.txt" not in repr(receipts) and "SYNTHETIC FIXTURE CONTENT" not in repr(receipts)
    _, finished = bridge.lifecycle.finish("completed", ok=True, run_id=run.run_id)
    assert finished
    assert _settle_work_record_run(current_session=session, bridge=bridge, store=store, binding=binding,
                                   agent=agent, tool_result_count=len(tool_messages), emit=lambda _: None), (
        "server settlement rejected the real executor receipt"
    )
    reopened = WorkRecordStore(Session(session.conversation_id, session.session_dir, "", session.meta, [], session.paths))
    persisted = reopened.get_item(item["item_id"])
    assert persisted["requirements"][0]["status"] == "unknown"
    assert persisted["runs"][0]["run_id"] == run.run_id
    assert persisted["runs"][0]["requirement_id"] == requirement["requirement_id"]
    verification = persisted["runs"][0]["verification"]
    assert verification["status"] == "not_verified" and verification["evidence_refs"] == []
    return run, receipts, verification


def test_offline_guard_accepts_only_exact_synthetic_read():
    def response(name, args):
        return SimpleNamespace(tool_calls=[{"function": {"name": name, "arguments": json.dumps(args)}}])
    assert _requested_safe_read(response("read_file", {"path": RELATIVE}))
    assert not _requested_safe_read(response("read_file", {"path": "../private.txt"}))
    assert not _requested_safe_read(response("list_dir", {"path": RELATIVE}))
    assert not _requested_safe_read(response("read_file", {"path": RELATIVE, "extra": 1}))
    assert not _requested_safe_read(SimpleNamespace(tool_calls=[]))


def test_offline_real_executor_and_server_settlement_component(tmp_path):
    run, receipts, verification = _execute_and_settle(_fixture(tmp_path))
    assert receipts[0]["host_run_id"] == normalize_host_run_id(run.run_id)
    assert verification["host_execution"].get("host_tool_receipts") == receipts
    assert verification["host_execution"].get("omitted_count") == 0


@pytest.mark.skipif(os.environ.get(OPT_IN) != "1", reason=f"set {OPT_IN}=1 to send one synthetic request to 0x567")
def test_live_luna_real_executor_receipt_persists_via_server(tmp_path):
    provider_paths = AgentPaths.discover(start=_CORE)
    entry = get_registry(provider_paths).get(MODEL)
    if entry is None or (entry.vendor, entry.provider_model) != ("0x567", "gpt-6-luna"):
        pytest.fail("expected 0x567/gpt-6-luna profile unavailable", pytrace=False)
    url = urlsplit(entry.chat_completions_url())
    if url.scheme != "https" or url.hostname != "api-cdn.0x567.com":
        pytest.fail("provider endpoint does not match the expected 0x567 host", pytrace=False)
    if not entry.resolve_api_key(provider_paths):
        pytest.skip("0x567 credential unavailable; no provider request made")
    if not entry.supports_tool_call:
        pytest.fail("configured model does not support tool calls", pytrace=False)
    fixture = _fixture(tmp_path)
    try:
        response = LLMClient(load_config(), paths=provider_paths).chat(
            [{"role": "user", "content": PROMPT}], model=MODEL, tools=[TOOL], temperature=0, timeout_sec=90)
    except Exception:
        pytest.fail("real provider request failed; details withheld", pytrace=False)
    if not _requested_safe_read(response):
        pytest.fail("real provider did not request exactly the allowed synthetic read; no tool executed", pytrace=False)
    run, receipts, verification = _execute_and_settle(fixture)
    stored = verification.get("host_execution", {}).get("host_tool_receipts")
    if stored != receipts:
        pytest.fail("real model and real read succeeded, but server settlement did not persist matching executor receipts", pytrace=False)


