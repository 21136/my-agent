from __future__ import annotations

"""Opt-in model + real Agent semantic route, with synthetic-only local data."""
import builtins
import io
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))

from agent import Agent
import multi_project_luna_intent as intent
from paths import AgentPaths
from project_switch import record_project_session
from session import Session, SessionMeta, create_new
from work_records import WorkRecordStore
from work_records_experiment import WorkRecordExperimentController

OPT_IN = "MY_AGENT_MULTI_PROJECT_ROUTE_LIVE_OPTIN"
QUESTION = "请看看 synthetic-alpha 和 synthetic-beta 的工作进展"
SELECTED = {"synthetic-alpha", "synthetic-beta"}
OTHER = "synthetic-gamma"


class NeverMainModel:
    def chat(self, *_args, **_kwargs):
        raise AssertionError("main agent model must not run on semantic overview route")


class OutboundGuard:
    """Transparent provider delegate: do not accept any non-synthetic payload."""
    def __init__(self, delegate, paths, state):
        self.delegate, self.paths, self.state = delegate, paths, state
        self.calls = 0

    def chat(self, messages, **kwargs):
        if (not self.state["outbound_approved"] or self.calls or len(messages) != 2
            or messages[0] != {"role": "system", "content": intent._SYSTEM}
            or messages[1] != {"role": "user", "content": QUESTION}
            or kwargs.get("model") != "0x567-flash" or kwargs.get("tools") != []):
            raise RuntimeError("synthetic outbound envelope refused")
        self.calls += 1
        try:
            return self.delegate.chat(messages, **kwargs)
        except Exception:
            raise RuntimeError("provider failure withheld") from None


def _fixture(root):
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    for directory in (paths.workspace, paths.data, paths.evolve):
        directory.mkdir(parents=True)
    current = create_new(paths, conversation_id="20261030-00000001")
    current.save()
    sessions = {}
    for index, project_id in enumerate(("synthetic-alpha", "synthetic-beta", OTHER), 2):
        project_root = paths.workspace / project_id
        project_root.mkdir()
        (project_root / "PROJECT.md").write_text("# synthetic\n", encoding="utf-8")
        session = Session(conversation_id=f"20261030-{index:08x}",
                          session_dir=paths.data / "sessions" / f"20261030-{index:08x}",
                          goal="SYNTHETIC_GOAL_PRIVATE", meta=SessionMeta(active_shell="project", project_id=project_id,
                          project_root=f"workspace/{project_id}"), messages=[], paths=paths)
        session.session_dir.mkdir(parents=True)
        session.append_message({"role": "user", "content": "SYNTHETIC_PROJECT_MESSAGE_PRIVATE"})
        session.save()
        store = WorkRecordStore(session)
        item = store.create_item(title=f"{project_id} item", scope={"kind": "project", "project_id": project_id})
        req = store.add_requirement(item["item_id"], f"{project_id} requirement", event_id=f"{project_id}-req", idempotency_key=f"{project_id}-req")
        status = "blocked" if project_id == "synthetic-alpha" else "unknown"
        store.record_result(item["item_id"], req["requirement_id"], run_id=f"{project_id}-run",
                            result_id=f"{project_id}-result", status=status, summary="synthetic",
                            verification={"status": "not_verified", "evidence_refs": []})
        record_project_session(paths, project_id, session.conversation_id)
        sessions[project_id] = session
    agent = Agent.create(current, llm=NeverMainModel(), enable_work_record_recall=True)
    agent.work_record_recall_controller = WorkRecordExperimentController(
        agent=agent, store=WorkRecordStore(current), recognizer=lambda *_args: {"action": "none"})
    return paths, current, sessions, agent


def _install_guards(monkeypatch, current, sessions, state):
    selected_session_ids = {sessions[pid].conversation_id for pid in SELECTED}
    selected_dirs = {sessions[pid].session_dir.resolve() for pid in SELECTED}
    target_dirs = {session.session_dir.resolve() for session in sessions.values()}
    original_read = WorkRecordStore._read
    def guarded_read(store):
        sid = store._session.conversation_id
        if sid != current.conversation_id:
            if sid not in selected_session_ids or not state["read_approved"]:
                raise AssertionError("unapproved project sidecar read")
            state["sidecar_reads"].append(sid)
        return original_read(store)
    monkeypatch.setattr(WorkRecordStore, "_read", guarded_read)

    def check(path):
        if isinstance(path, (str, bytes, os.PathLike)):
            try:
                p = Path(os.fsdecode(path)).resolve(strict=False)
            except (OSError, ValueError):
                raise AssertionError("invalid path blocked") from None
            for directory in target_dirs:
                if p == directory or directory in p.parents:
                    if directory not in selected_dirs:
                        raise AssertionError("unselected project session access")
                    if p.name in {"messages.jsonl", "goal.md"}:
                        state["blocked_message_attempts"] += 1
                        raise AssertionError("project conversation access blocked")
                    if not state["read_approved"]:
                        raise AssertionError("project session read before approval")
    old_open, old_io_open, old_os_open = builtins.open, io.open, os.open
    def checked_open(path, *args, **kwargs):
        check(path)
        return old_open(path, *args, **kwargs)
    def checked_io_open(path, *args, **kwargs):
        check(path)
        return old_io_open(path, *args, **kwargs)
    def checked_os_open(path, *args, **kwargs):
        check(path)
        return old_os_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", checked_open)
    monkeypatch.setattr(io, "open", checked_io_open)
    monkeypatch.setattr(os, "open", checked_os_open)


def _run(root, monkeypatch, delegate, provider_paths, *, outbound=True, read=True):
    paths, current, sessions, agent = _fixture(root)
    state = {"outbound_approved": False, "read_approved": False, "sidecar_reads": [],
             "blocked_message_attempts": 0, "stages": []}
    _install_guards(monkeypatch, current, sessions, state)
    guarded = OutboundGuard(delegate, provider_paths, state)
    def approve_outbound(request):
        assert request.user_text == QUESTION
        assert (request.model, request.vendor, request.provider_model) == ("0x567-flash", "0x567", "gpt-6-luna")
        assert not state["sidecar_reads"] and not state["read_approved"]
        state["stages"].append("outbound")
        state["outbound_approved"] = outbound
        return outbound
    def approve_read(preview, allow_approve_all=False):
        assert state["outbound_approved"] and guarded.calls == 1
        assert not state["sidecar_reads"] and allow_approve_all is False
        assert all(pid in preview for pid in SELECTED) and OTHER not in preview
        assert all(sessions[pid].conversation_id in preview for pid in SELECTED)
        state["stages"].append("read")
        state["read_approved"] = read
        return "y" if read else "n"
    agent.executor.confirm_fn = approve_read
    agent.multi_project_overview_recognizer = intent.MultiProjectLunaIntent(guarded, approve_outbound=approve_outbound)
    result = agent.run_turn(QUESTION, spawn_explore=False)
    return result, guarded, state, sessions


class SyntheticClassifier:
    def chat(self, messages, **kwargs):
        return SimpleNamespace(content='{"intent":"selected","project_names":["synthetic-alpha","synthetic-beta"]}', tool_calls=[])


@pytest.mark.parametrize("outbound,read,expected_stages", [(False, True, ["outbound"]), (True, False, ["outbound", "read"])])
def test_offline_separate_confirmations_fail_closed(tmp_path, monkeypatch, outbound, read, expected_stages):
    entry = SimpleNamespace(id="0x567-flash", vendor="0x567", provider_model="gpt-6-luna", max_input_tokens=96000,
                            chat_completions_url=lambda: "https://api-cdn.0x567.com/v1/chat/completions")
    monkeypatch.setattr(intent, "get_registry", lambda *_args: SimpleNamespace(get=lambda _: entry))
    result, client, state, _ = _run(tmp_path, monkeypatch, SyntheticClassifier(), None, outbound=outbound, read=read)
    assert state["stages"] == expected_stages and state["sidecar_reads"] == []
    assert client.calls == int(outbound)
    assert "synthetic-alpha item" not in result.assistant_text
    assert state["blocked_message_attempts"] == 0


def test_offline_real_agent_reading_uses_confirmed_project_scopes(tmp_path, monkeypatch):
    entry = SimpleNamespace(id="0x567-flash", vendor="0x567", provider_model="gpt-6-luna", max_input_tokens=96000,
                            chat_completions_url=lambda: "https://api-cdn.0x567.com/v1/chat/completions")
    monkeypatch.setattr(intent, "get_registry", lambda *_args: SimpleNamespace(get=lambda _: entry))
    result, client, state, sessions = _run(tmp_path, monkeypatch, SyntheticClassifier(), None)
    assert state["stages"] == ["outbound", "read"] and client.calls == 1
    assert set(state["sidecar_reads"]) == {sessions[pid].conversation_id for pid in SELECTED}
    assert state["blocked_message_attempts"] == 0
    assert "要求：synthetic-alpha requirement；状态：受阻" in result.assistant_text
    assert "要求：synthetic-beta requirement；状态：结果待确认" in result.assistant_text
    assert "synthetic-gamma item" not in result.assistant_text


@pytest.mark.skipif(os.getenv(OPT_IN) != "1", reason="synthetic-only live opt-in")
def test_live_model_agent_route_two_consents_and_selected_project_records(tmp_path, monkeypatch):
    from llm_client import LLMClient, load_config
    from llm_models import get_registry
    provider_paths = AgentPaths.discover(start=CORE)
    entry = get_registry(provider_paths).get("0x567-flash")
    if entry is None or (entry.vendor, entry.provider_model) != ("0x567", "gpt-6-luna"):
        pytest.fail("provider profile unavailable", pytrace=False)
    url = urlsplit(entry.chat_completions_url())
    if url.scheme != "https" or url.hostname != "api-cdn.0x567.com":
        pytest.fail("provider endpoint not permitted", pytrace=False)
    if not entry.resolve_api_key(provider_paths):
        pytest.skip("provider credential unavailable")
    client = LLMClient(load_config(), paths=provider_paths)
    result, guarded, state, sessions = _run(tmp_path, monkeypatch, client, provider_paths)
    if state["stages"] != ["outbound", "read"] or guarded.calls != 1:
        pytest.fail("live route did not reach both separately authorized steps; response withheld", pytrace=False)
    assert set(state["sidecar_reads"]) == {sessions[pid].conversation_id for pid in SELECTED}
    assert state["blocked_message_attempts"] == 0
    assert result.finish_reason == "multi_project_work_record_overview"
    assert result.tool_rounds == 0
    text = result.assistant_text
    assert "synthetic-alpha item" in text and "synthetic-beta item" in text
    assert "synthetic-gamma item" not in text
    assert "synthetic-alpha requirement" in text and "synthetic-beta requirement" in text
    assert "要求：synthetic-alpha requirement；状态：受阻" in text
    assert "要求：synthetic-beta requirement；状态：结果待确认" in text
    assert "SYNTHETIC_PROJECT_MESSAGE_PRIVATE" not in text and "SYNTHETIC_GOAL_PRIVATE" not in text
