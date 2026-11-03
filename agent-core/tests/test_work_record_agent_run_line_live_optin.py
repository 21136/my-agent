from __future__ import annotations

"""Opt-in isolated Agent tool loop through server._run_line, not a Desktop test."""
import asyncio
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

import server
from agent import Agent
from evolution.context import normalize_host_run_id
from llm_client import LLMClient, load_config
from llm_models import get_registry
from paths import AgentPaths
from server import WsBridge, _begin_work_record_run_binding, _run_line
from session import Session, SessionMeta
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from work_records import WorkRecordStore

OPT_IN = "MY_AGENT_LIVE_WORK_RECORD_AGENT_LOOP_OPTIN"
MODEL = "0x567-flash"
RELATIVE = "workspace/synthetic-project/sample.txt"
CANARY = "SYNTHETIC_OWL_741"
PROMPT = "Please use read_file to read workspace/synthetic-project/sample.txt and briefly report. Only read this synthetic file."
TOOL = {"type": "function", "function": {"name": "read_file", "description": "Read the single synthetic fixture",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                   "required": ["path"], "additionalProperties": False}}}


class GuardedExecutor(ToolExecutor):
    """Enforce the allowlist before any builtin dispatch, including bad model calls."""
    allowed_calls = 0

    def run(self, tool_name, arguments=None):
        if (tool_name != "read_file" or type(arguments) is not dict
                or arguments != {"path": RELATIVE} or self.allowed_calls >= 1):
            raise RuntimeError("synthetic tool guard rejected request")
        root = self.registry.agent_paths.agent_root.resolve(strict=True)
        target = (root / RELATIVE).resolve(strict=True)
        if target != (root / "workspace" / "synthetic-project" / "sample.txt").resolve(strict=True):
            raise RuntimeError("synthetic target mismatch")
        if not target.is_relative_to(root):
            raise RuntimeError("synthetic target escaped root")
        self.allowed_calls += 1
        return super().run(tool_name, arguments)


class BoundedLiveClient:
    """Delegate actual model requests; tightly constrain outbound context and tool schema."""
    def __init__(self, delegate):
        self.delegate = delegate
        self.calls = 0
        self.tool_proposals = 0

    def set_cancel_event(self, event):
        self.delegate.set_cancel_event(event)

    def chat(self, messages, **kwargs):
        if self.calls >= 3 or not kwargs.get("tools"):
            raise RuntimeError("synthetic model call guard rejected request")
        clean = [{"role": "system", "content": "This is a synthetic test. Use only the provided read_file tool. After reading, reply briefly."}]
        user_count = 0
        for msg in messages:
            role = msg.get("role")
            if role in {"system", "developer"}:
                continue
            if role == "user":
                # Agent-generated anchor/reminders are not part of the outbound
                # synthetic request; never forward their contents.
                if msg.get("content") != PROMPT:
                    continue
                if user_count:
                    raise RuntimeError("duplicate synthetic prompt")
                user_count += 1
                clean.append({"role": "user", "content": PROMPT})
            elif role == "assistant":
                calls = msg.get("tool_calls")
                if not isinstance(calls, list) or len(calls) != 1:
                    raise RuntimeError("synthetic outbound assistant context rejected")
                clean.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
            elif role == "tool":
                content = msg.get("content")
                if not isinstance(content, str) or CANARY not in content or len(content) > 4000:
                    raise RuntimeError("synthetic outbound tool context rejected")
                clean.append({"role": "tool", "tool_call_id": msg.get("tool_call_id"), "content": content})
            else:
                raise RuntimeError("synthetic outbound role rejected")
        if user_count != 1:
            raise RuntimeError("synthetic prompt missing")
        self.calls += 1
        try:
            response = self.delegate.chat(clean, model=MODEL, tools=[TOOL], temperature=0, timeout_sec=90)
        except Exception:
            raise RuntimeError("synthetic provider request failed") from None
        calls = response.tool_calls
        if calls:
            if len(calls) != 1 or self.tool_proposals:
                raise RuntimeError("synthetic tool proposal count rejected")
            fn = calls[0].get("function") if isinstance(calls[0], dict) else None
            try:
                args = json.loads(fn.get("arguments", "")) if isinstance(fn, dict) else None
            except (TypeError, ValueError):
                args = None
            if not isinstance(fn, dict) or fn.get("name") != "read_file" or args != {"path": RELATIVE}:
                raise RuntimeError("synthetic tool proposal rejected")
            self.tool_proposals += 1
        return response


class ReplHarness:
    """Only the REPL shell is substituted; run_turn is the actual Agent method."""
    def __init__(self, session, agent):
        self.session, self.agent = session, agent
        self.last_turn_finish_reason = None
        self.ran = False

    def handle_line(self, line):
        self.ran = True
        turn = self.agent.run_turn(line, spawn_explore=False, force_skip_plan_spawn=True)
        self.last_turn_finish_reason = turn.finish_reason
        return "continue"


def _prepare(tmp_path, monkeypatch, llm):
    root = tmp_path / "synthetic-root"
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    session_dir = paths.data / "sessions" / "synthetic-session"
    session_dir.mkdir(parents=True)
    paths.evolve.mkdir(parents=True)
    target = root / RELATIVE
    target.parent.mkdir(parents=True)
    target.write_text(CANARY + "\n", encoding="utf-8")
    # All discovery by Agent/runtime components resolves to the temporary root.
    monkeypatch.setattr(AgentPaths, "discover", classmethod(lambda cls, *a, **kw: paths))
    # _run_line error logging otherwise includes raw line/exception; retain nothing.
    monkeypatch.setattr(server, "log_sidecar_exception", lambda *_a, **_kw: None)
    monkeypatch.setattr(server, "emit_error", lambda *_a, **_kw: None)
    session = Session("synthetic-session", session_dir, "", SessionMeta(
        project_id="synthetic-project", project_root="workspace/synthetic-project", llm_model=MODEL), [], paths)
    store = WorkRecordStore(session)
    item = store.create_item(title="Synthetic file reading", scope={"kind": "project", "project_id": "synthetic-project"})
    req = store.add_requirement(item["item_id"], "Read only the synthetic fixture", event_id=str(uuid.uuid4()), idempotency_key="synthetic-req")
    executor = GuardedExecutor(registry=ToolRegistry.load(paths), session=ExecutorSession(
        session_dir=session_dir, project_id="synthetic-project", project_root="workspace/synthetic-project"),
        confirm_fn=lambda *_a, **_kw: "n")
    agent = Agent(session=session, executor=executor, llm=llm)
    bridge = WsBridge(emit=lambda _event: None, paths=paths)
    assert bridge.begin_turn(mode="ordinary")
    run = bridge.lifecycle.snapshot()
    bridge.lifecycle_conversation_id = session.conversation_id
    bridge._turn_busy.set()
    capture = SimpleNamespace(status="recorded", item_id=item["item_id"], requirement_id=req["requirement_id"])
    binding = _begin_work_record_run_binding(session=session, capture=capture, run_id=run.run_id,
        current_session=session, bridge=bridge, emit=lambda _: None)
    assert isinstance(binding, tuple)
    return paths, session, executor, bridge, binding, item, req, run, ReplHarness(session, agent)


def test_tool_guard_rejects_out_of_scope_before_dispatch(tmp_path, monkeypatch):
    paths, session, executor, bridge, binding, item, req, run, repl = _prepare(tmp_path, monkeypatch, None)
    for name, arguments in (("read_file", {"path": "../private.txt"}),
                            ("list_dir", {"path": RELATIVE}),
                            ("read_file", {"path": RELATIVE, "extra": True})):
        with pytest.raises(RuntimeError, match="synthetic tool guard"):
            executor.run(name, arguments)
    assert executor.allowed_calls == 0
    assert executor.confirm_fn("synthetic request") == "n"
    bridge.end_turn()


@pytest.mark.skipif(os.environ.get(OPT_IN) != "1", reason=f"set {OPT_IN}=1 to run real synthetic model test")
def test_live_agent_loop_server_run_line_persists_host_receipt(tmp_path, monkeypatch):
    provider_paths = AgentPaths.discover(start=_CORE)
    entry = get_registry(provider_paths).get(MODEL)
    if entry is None or (entry.vendor, entry.provider_model) != ("0x567", "gpt-6-luna"):
        pytest.fail("expected model profile unavailable", pytrace=False)
    url = urlsplit(entry.chat_completions_url())
    if url.scheme != "https" or url.hostname != "api-cdn.0x567.com":
        pytest.fail("unexpected model endpoint", pytrace=False)
    if not entry.resolve_api_key(provider_paths):
        pytest.skip("credential unavailable; no request made")
    if not entry.supports_tool_call:
        pytest.fail("model lacks tool support", pytrace=False)
    llm = BoundedLiveClient(LLMClient(load_config(), paths=provider_paths))
    paths, session, executor, bridge, binding, item, req, run, repl = _prepare(tmp_path, monkeypatch, llm)
    try:
        asyncio.run(_run_line(repl, bridge, PROMPT, paths, prestarted_run_id=run.run_id, work_record_binding=binding))
    except Exception:
        pytest.fail("synthetic run failed; details withheld", pytrace=False)
    if not repl.ran or llm.tool_proposals != 1 or executor.allowed_calls != 1:
        pytest.fail(f"synthetic loop did not execute one real model-proposed read; provider calls={llm.calls}", pytrace=False)
    if bridge.lifecycle.snapshot().state != "settled":
        pytest.fail("synthetic lifecycle did not settle", pytrace=False)
    messages = session.messages
    tool_messages = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    reopened = WorkRecordStore(Session(session.conversation_id, session.session_dir, "", session.meta, [], paths))
    persisted = reopened.get_item(item["item_id"])
    assert persisted["requirements"][0]["status"] == "unknown"
    assert len(persisted["runs"]) == 1
    saved = persisted["runs"][0]
    assert saved["run_id"] == run.run_id and saved["requirement_id"] == req["requirement_id"]
    assert saved["status"] == "unknown"
    verification = saved["verification"]
    assert verification["status"] == "not_verified" and verification["evidence_refs"] == []
    receipts = verification["host_execution"].get("host_tool_receipts")
    assert isinstance(receipts, list) and len(receipts) == 1
    receipt = receipts[0]
    assert set(receipt) == {"host_run_id", "call_id", "tool", "ok"}
    assert normalize_host_run_id(receipt["host_run_id"]) == normalize_host_run_id(run.run_id)
    assert str(uuid.UUID(receipt["call_id"])) == receipt["call_id"]
    assert receipt["tool"] == "read_file" and receipt["ok"] is True
    assert verification["host_execution"]["omitted_count"] == 0
    assert RELATIVE not in repr(receipt) and CANARY not in repr(receipt)
