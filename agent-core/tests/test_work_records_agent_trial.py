from __future__ import annotations
import os
import sys
from pathlib import Path
from unittest.mock import Mock
import pytest
_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path: sys.path.insert(0, str(_AGENT_CORE))
from agent import Agent
from llm_client import LLMClient, LLMResponse, load_config
from llm_models import get_registry
from llm_routing import resolve_model_id_for_role
from llm_secrets import get_llm_secret
from paths import AgentPaths
from session import create_new
from work_records import WorkRecordStore

class _FakeLLM:
    def __init__(self): self.calls = []
    def set_cancel_event(self, _event): pass
    def chat(self, messages, **kwargs):
        self.calls.append({"messages": list(messages), **kwargs})
        return LLMResponse(model="synthetic-fake", content="Received. I will organize this into checkable points.", tool_calls=[], finish_reason="stop", usage=None, raw={})

@pytest.fixture
def isolated_agent_paths(tmp_path):
    root = tmp_path / "agent-root"
    root.mkdir()
    return AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")

def _turn(agent, prompt):
    result = agent.run_turn(prompt, spawn_explore=False)
    assert result.assistant_text and "Received" in result.assistant_text
    assert result.tool_rounds == 0

def test_real_agent_turns_and_explicit_work_record_orchestration_share_session(isolated_agent_paths):
    """No automatic WorkRecordStore hook exists; writes are explicitly orchestrated here."""
    session = create_new(isolated_agent_paths, conversation_id="work-record-agent-trial")
    session.set_turn_mode("agent")
    session.meta.active_shell = "project"
    session.meta.project_id = "synthetic-project-001"
    llm = _FakeLLM()
    agent = Agent.create(session, llm=llm, confirm_fn=Mock())
    store = WorkRecordStore(session)

    project = store.create_item(title="Synthetic project: review login flow", scope={"kind": "project", "project_id": "synthetic-project-001"}, source_refs=[{"kind": "project", "id": "synthetic-login-flow"}])
    project_req = store.add_requirement(project["item_id"], "List login-flow review questions", event_id="synthetic-project-request", source_ref={"kind": "project", "id": "synthetic-login-flow"})
    _turn(agent, "[\u9644\u4ef6]\n- synthetic.pdf \u00b7 workspace/_drops/trial/synthetic.pdf \u00b7 12 KB \u00b7 application/pdf\n\n\u8bf7\u6839\u636e\u9644\u4ef6\uff0c\u6a21\u62df\u4e00\u573a\u9762\u8bd5\u5e76\u63d0\u95ee\u5408\u6210\u9879\u76ee\u7684\u767b\u5f55\u6d41\u7a0b\u3002")
    project_result = store.record_result(project["item_id"], project_req["requirement_id"], run_id="synthetic-project-run", result_id="synthetic-project-result", status="completed", summary="Fake Agent turn completed; no real login flow was executed or verified.", verification={"status": "not_verified", "summary": "Only Agent turn and explicit record orchestration were checked"}, sources=[{"kind": "project", "id": "synthetic-login-flow"}])

    independent = store.create_item(title="Independent item: organize a study list", scope={"kind": "independent"})
    independent_req = store.add_requirement(independent["item_id"], "List study-list organization steps", event_id="synthetic-independent-request")
    _turn(agent, "[\u9644\u4ef6]\n- synthetic.pdf \u00b7 workspace/_drops/trial/synthetic.pdf \u00b7 12 KB \u00b7 application/pdf\n\n\u8bf7\u6839\u636e\u9644\u4ef6\uff0c\u6a21\u62df\u4e00\u573a\u9762\u8bd5\uff0c\u63d0\u95ee\u5982\u4f55\u6574\u7406\u4e2a\u4eba\u5b66\u4e60\u6e05\u5355\u3002")
    independent_result = store.record_result(independent["item_id"], independent_req["requirement_id"], run_id="synthetic-independent-run", result_id="synthetic-independent-result", status="completed", summary="Fake Agent turn completed; no external operation was performed.", verification={"status": "not_verified", "summary": "Only Agent turn and explicit record orchestration were checked"})

    assert [item["item_id"] for item in store.search("login flow")] == [project["item_id"]]
    assert [item["item_id"] for item in store.search("study list")] == [independent["item_id"]]
    assert store.get_item(project["item_id"])["scope"] == {"kind": "project", "project_id": "synthetic-project-001"}
    assert store.get_item(independent["item_id"])["scope"] == {"kind": "independent", "object_ref": None}
    assert store.get_item(project["item_id"])["runs"] == [project_result]
    assert store.get_item(independent["item_id"])["runs"] == [independent_result]
    assert all(item["verification"]["status"] == "not_verified" for item in (project_result, independent_result))
    assert len(llm.calls) == 2
    assert all(not call.get("tools") for call in llm.calls)
    assert all(any(message.get("role") == "user" for message in call["messages"]) for call in llm.calls)



@pytest.mark.parametrize("opt_in", [os.getenv("MY_AGENT_RUN_0X567_FLASH_TRIAL", "")])
def test_opt_in_real_0x567_flash_turn_uses_synthetic_input_only(isolated_agent_paths, opt_in):
    """Live transport smoke; Session/records are temp-only and recording is explicitly orchestrated."""
    if opt_in != "1":
        pytest.skip("set MY_AGENT_RUN_0X567_FLASH_TRIAL=1 to opt in to the live request")

    expected_model = "0x567-flash"
    sandbox_paths = AgentPaths.discover(start=Path(__file__).resolve())
    registry = get_registry(sandbox_paths)
    model_entry = registry.get(expected_model)
    assert model_entry is not None and model_entry.id == expected_model
    api_key = get_llm_secret(model_entry.api_key_env, sandbox_paths) if model_entry.api_key_env else None
    if not api_key:
        pytest.skip("0x567 credential is unavailable from sandbox config; no live request was made")

    session = create_new(isolated_agent_paths, conversation_id="work-record-live-trial")
    session.set_turn_mode("agent")
    session.meta.active_shell = "project"
    session.meta.project_id = "synthetic-live-project"
    session.meta.llm_model = expected_model
    assert resolve_model_id_for_role("main_turn", session.meta, registry=registry) == expected_model

    prompt = (
        "[SYNTHETIC TEST ONLY] Explain two generic review questions for a fictional "
        "login flow. Do not use tools, access files, or claim that real work was performed."
    )
    # Do not forward Agent-generated system/context messages: they may contain environment-specific material.
    network_messages = [
        {"role": "system", "content": "You are handling a synthetic test. Give a brief generic answer; no tools or real-world claims."},
        {"role": "user", "content": prompt},
    ]
    serialized_network_messages = repr(network_messages)
    assert api_key not in serialized_network_messages
    assert str(sandbox_paths.agent_root) not in serialized_network_messages
    assert str(isolated_agent_paths.agent_root) not in serialized_network_messages
    assert str(Path.home()) not in serialized_network_messages
    assert network_messages == [
        {"role": "system", "content": "You are handling a synthetic test. Give a brief generic answer; no tools or real-world claims."},
        {"role": "user", "content": prompt},
    ]

    # The model transport intentionally uses configured sandbox credentials/config; all mutable Agent state stays temporary.
    real_client = LLMClient(load_config(), paths=sandbox_paths)
    sent_network_messages = []

    class _SyntheticTransportAdapter:
        def set_cancel_event(self, event):
            real_client.set_cancel_event(event)

        def chat(self, _agent_messages, **_agent_kwargs):
            # Explicit boundary: discard the Agent's original prompt assembly and send only fixed synthetic text.
            outbound_messages = [dict(message) for message in network_messages]
            outbound_tools = None
            assert outbound_messages == network_messages
            assert outbound_tools is None
            assert api_key not in repr(outbound_messages)
            assert str(sandbox_paths.agent_root) not in repr(outbound_messages)
            sent_network_messages.append(outbound_messages)
            return real_client.chat(
                outbound_messages,
                model=expected_model,
                temperature=0.0,
                tools=outbound_tools,
            )

    agent = Agent.create(session, llm=_SyntheticTransportAdapter(), confirm_fn=lambda *_args: "n")
    # Test-only policy: remove schemas at the Agent loop and hard-stop any unexpected execution attempt.
    original_loop = agent._run_parent_tool_loop

    def _tools_disabled_loop(**kwargs):
        kwargs["tools"] = []
        return original_loop(**kwargs)

    def _deny_execution(*_args, **_kwargs):
        raise AssertionError("tool execution is disabled in the live-model trial")

    agent._run_parent_tool_loop = _tools_disabled_loop
    agent.executor.execute = _deny_execution

    result = agent.run_turn(prompt, spawn_explore=False)
    assert len(sent_network_messages) == 1
    assert sent_network_messages[0] == network_messages
    assert result.assistant_text.strip()
    assert result.tool_rounds == 0
    assert all(message.get("role") != "tool" for message in session.messages)

    # Explicit test orchestration after the Agent turn; no automatic record integration is implied.
    store = WorkRecordStore(session)
    item = store.create_item(
        title="Synthetic live trial: generic login review",
        scope={"kind": "project", "project_id": "synthetic-live-project"},
        source_refs=[{"kind": "project", "id": "synthetic-live-source"}],
    )
    requirement = store.add_requirement(
        item["item_id"], "List generic login-flow review questions",
        event_id="synthetic-live-requirement",
        source_ref={"kind": "project", "id": "synthetic-live-source"},
    )
    recorded = store.record_result(
        item["item_id"], requirement["requirement_id"],
        run_id="synthetic-live-run", result_id="synthetic-live-result",
        status="completed",
        summary="A live model responded to fixed synthetic text; no actual project work was performed.",
        verification={"status": "not_verified", "summary": "Live conversation only; no external operation or artifact verification"},
    )
    assert store.get_item(item["item_id"])["runs"] == [recorded]
    assert recorded["verification"]["status"] == "not_verified"

