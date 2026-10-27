from __future__ import annotations

import sys
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import BUILTIN_TOOL_NAMES, build_builtin_tool_definition
from loader import format_builtin_summary

_EVOLUTION_ID = "22222222-2222-4222-8222-222222222222"
_CONVERSATION_ID = "33333333-3333-4333-8333-333333333333"
_HOST_RUN_ID = "44444444-4444-4444-8444-444444444444"
_AUTH_ID = "55555555-5555-4555-8555-555555555555"


def _evolution_tool():
    try:
        from tools.builtin import evolution
    except ImportError:
        evolution = None
    assert evolution is not None and callable(getattr(evolution, "run", None)), "evolution builtin runner must be importable"
    return evolution


def test_evolution_builtin_is_registered_for_model_summary_and_index():
    try:
        definition = build_builtin_tool_definition("evolution")
    except KeyError:
        definition = None
    assert isinstance(definition, dict), "evolution must have a real model tool definition"
    assert "evolution" in BUILTIN_TOOL_NAMES
    schema = definition["function"]["parameters"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["action"]["enum"] == ["inspect", "patch", "snapshot", "build", "launch", "stop", "run_case"]
    assert "evolution" in format_builtin_summary()
    index = (_AGENT_CORE.parent / "evolve" / "tool-catalog" / "INDEX.md").read_text(encoding="utf-8")
    assert "evolution" in index.lower()


def test_builtin_request_uses_bound_session_identity_and_not_model_supplied_authority():
    evolution = _evolution_tool()
    from tools.executor import ExecutorSession
    from tools.schema import ToolResult
    from types import SimpleNamespace
    session = ExecutorSession(
        session_dir=Path("/sessions") / _CONVERSATION_ID,
        evolution_id=_EVOLUTION_ID,
        host_run_id=_HOST_RUN_ID,
        evolution_authorization_id=_AUTH_ID,
        turn_mode="agent",
        ordinary_work_intent="execute",
    )
    receipt_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    session.evolution_confirm_with_receipt = lambda _preview, _allow: ("yes", receipt_id)
    class Client:
        def __init__(self): self.requests = []; self.grants = []
        def inspect(self, evolution_id, conversation_id, host_run_id):
            return {"status": "succeeded", "result": {"evolution_id": evolution_id, "conversation_id": conversation_id, "state_revision": 9}}
        def authorize_grant(self, **grant): self.grants.append(grant); return "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        def request(self, request, timeout_s=10):
            self.requests.append(request)
            return {"operation_id": request["operation_id"], "evolution_id": request["evolution_id"], "state_revision": 10, "status": "succeeded", "result": {"workspace_revision": 1}, "error": None}
    client = Client()
    result = evolution.run({"action": "patch", "payload": {"path": "main.cjs", "expected_before_hash": "missing", "content_utf8": "module.exports=1"}}, client=client, session=session)
    assert isinstance(result, ToolResult) and result.ok is True
    request = client.requests[0]
    assert request["evolution_id"] == _EVOLUTION_ID
    assert request["conversation_id"] == _CONVERSATION_ID
    assert request["host_run_id"] == _HOST_RUN_ID
    assert request["authorization_id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    assert client.grants[0]["kind"] == "source.patch" and client.grants[0]["confirmation_id"] == receipt_id
    assert request["expected_state_revision"] == 9
    assert request["action"] == "patch"
    assert "host_token" not in request and "source_root" not in request


def test_missing_binding_or_authorization_never_dispatches_side_effects():
    evolution = _evolution_tool()
    from tools.executor import ExecutorSession
    class Client:
        def __init__(self): self.requests = []
        def inspect(self, *args): return {"status": "succeeded", "result": {"state_revision": 0}}
        def request(self, request, timeout_s=10): self.requests.append(request); return {"status": "succeeded"}
    client = Client()
    unbound = evolution.run({"action": "patch", "payload": {"path": "main.cjs", "expected_before_hash": "missing", "content_utf8": "x"}}, client=client, session=ExecutorSession())
    assert unbound.ok is False
    session = ExecutorSession(session_dir=Path("/sessions") / _CONVERSATION_ID, evolution_id=_EVOLUTION_ID, host_run_id=_HOST_RUN_ID, turn_mode="agent")
    denied = evolution.run({"action": "patch", "payload": {"path": "main.cjs", "expected_before_hash": "missing", "content_utf8": "x"}}, client=client, session=session)
    assert denied.ok is False
    assert client.requests == []


def test_model_cannot_supply_binding_or_authorization_fields():
    evolution = _evolution_tool()
    from tools.executor import ExecutorSession
    class Client:
        def inspect(self,*args): return {"status":"succeeded","result":{"state_revision":0}}
        def request(self,*args,**kwargs): raise AssertionError("must reject before client call")
    result=evolution.run({"action":"inspect","payload":{"query":"summary"},"conversation_id":_CONVERSATION_ID,"authorization_id":_AUTH_ID},client=Client(),session=ExecutorSession(evolution_id=_EVOLUTION_ID,host_run_id=_HOST_RUN_ID))
    assert result.ok is False

def test_ask_or_read_only_turn_cannot_dispatch_evolution_side_effects():
    evolution = _evolution_tool()
    from tools.executor import ExecutorSession
    class Client:
        def __init__(self): self.inspect_calls = 0; self.request_calls = 0
        def inspect(self, *args): self.inspect_calls += 1; return {"status": "succeeded", "result": {"state_revision": 1}}
        def request(self, *args, **kwargs): self.request_calls += 1; return {"status": "succeeded"}
    client = Client()
    session = ExecutorSession(session_dir=Path("/sessions") / _CONVERSATION_ID, evolution_id=_EVOLUTION_ID, host_run_id=_HOST_RUN_ID, evolution_authorization_id=_AUTH_ID, turn_mode="ask", ordinary_work_intent="read_only")
    result = evolution.run({"action": "patch", "payload": {"path": "main.cjs", "expected_before_hash": "missing", "content_utf8": "x"}}, client=client, session=session)
    assert result.ok is False
    assert client.inspect_calls == 0
    assert client.request_calls == 0


def test_unbound_inspect_returns_inactive_without_querying_supervisor():
    evolution = _evolution_tool()
    from tools.executor import ExecutorSession
    class Client:
        def inspect(self, *args): raise AssertionError("inactive inspect must not query another binding")
        def request(self, *args, **kwargs): raise AssertionError("inactive inspect must not send an action")
    result = evolution.run({"action": "inspect", "payload": {"query": "summary"}}, client=Client(), session=ExecutorSession())
    assert result.ok is True
    assert result.data["status"] == "inactive"

def test_tool_executor_dispatches_evolution_with_trusted_client_and_session():
    from paths import AgentPaths
    from tools.executor import ExecutorSession, ToolExecutor
    from tools.registry import ToolRegistry
    class Client:
        def __init__(self): self.requests = []
        def inspect(self, evolution_id, conversation_id, host_run_id):
            return {"status": "succeeded", "result": {"evolution_id": evolution_id, "conversation_id": conversation_id, "state_revision": 3}}
        def request(self, request, timeout_s=10):
            self.requests.append(request)
            return {"operation_id": request["operation_id"], "evolution_id": request["evolution_id"], "state_revision": 3, "status": "succeeded", "result": {"candidate_status": "ready"}, "error": None}
    registry=ToolRegistry.load(AgentPaths.discover())
    session=ExecutorSession(session_dir=Path("/sessions") / _CONVERSATION_ID, evolution_id=_EVOLUTION_ID, host_run_id=_HOST_RUN_ID, turn_mode="agent")
    client=Client()
    executor=ToolExecutor(registry=registry, session=session, evolution_client=client)
    result=executor.run("evolution", {"action":"inspect", "payload":{"query":"summary"}})
    assert result.ok is True
    assert result.data["result"]["candidate_status"] == "ready"
    assert len(client.requests) == 1



def test_evolution_tool_is_visible_in_real_ask_mode_tool_definition():
    from agent import build_llm_tools
    from paths import AgentPaths
    from session import Session, SessionMeta
    from tools.registry import ToolRegistry
    paths = AgentPaths.discover()
    session = Session(conversation_id=_CONVERSATION_ID, session_dir=Path(_CONVERSATION_ID), goal="", meta=SessionMeta(turn_mode="ask"), messages=[], paths=paths)
    names = {item["function"]["name"] for item in build_llm_tools(session, registry=ToolRegistry.load(paths))}
    assert "evolution" in names
    assert "run_evolved" not in names


def test_candidate_build_requires_a_fresh_trusted_grant_bound_to_the_visible_confirmation():
    evolution = _evolution_tool()
    from tools.executor import ExecutorSession
    from pathlib import Path

    source_snapshot_id = "sha256:" + "a" * 64
    build_input_digest = "sha256:" + "b" * 64
    grant_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    receipt_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    session = ExecutorSession(
        session_dir=Path("/sessions") / _CONVERSATION_ID,
        evolution_id=_EVOLUTION_ID,
        host_run_id=_HOST_RUN_ID,
        evolution_authorization_id=_AUTH_ID,
        turn_mode="agent",
        ordinary_work_intent="execute",
    )
    previews = []
    session.evolution_confirm_with_receipt = lambda preview, allow: (previews.append((preview, allow)) or ("yes", receipt_id))

    class Client:
        def __init__(self): self.requests = []; self.grants = []
        def inspect(self, evolution_id, conversation_id, host_run_id):
            return {"status": "succeeded", "result": {"evolution_id": evolution_id, "conversation_id": conversation_id, "state_revision": 9}}
        def authorize_grant(self, **grant):
            self.grants.append(grant)
            return grant_id
        def request(self, request, timeout_s=10):
            self.requests.append(request)
            return {"operation_id": request["operation_id"], "evolution_id": request["evolution_id"], "state_revision": 10, "status": "succeeded", "result": {"candidate_id": source_snapshot_id}, "error": None}

    client = Client()
    payload = {"source_snapshot_id": source_snapshot_id, "descriptor_artifact_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "build_input_digest": build_input_digest, "isolation_mode": "local_managed"}
    result = evolution.run({"action": "build", "payload": payload}, client=client, session=session)

    assert result.ok is True
    assert len(client.grants) == 1
    grant = client.grants[0]
    assert grant["evolution_id"] == _EVOLUTION_ID and grant["conversation_id"] == _CONVERSATION_ID
    assert grant["kind"] == "candidate.build" and grant["confirmation_id"] == receipt_id
    assert grant["constraints"] == {"source_snapshot_id": source_snapshot_id, "build_input_digest": build_input_digest, "isolation_mode": "local_managed"}
    assert grant["expires_at"].endswith("Z")
    assert client.requests[0]["authorization_id"] == grant_id
    assert client.requests[0]["authorization_id"] != _AUTH_ID
    assert previews and previews[0][1] is False and "candidate.build" in previews[0][0] and source_snapshot_id in previews[0][0]


def test_candidate_build_confirmation_denial_sends_no_grant_or_operation():
    evolution = _evolution_tool()
    from tools.executor import ExecutorSession
    from pathlib import Path
    session = ExecutorSession(session_dir=Path("/sessions") / _CONVERSATION_ID, evolution_id=_EVOLUTION_ID, host_run_id=_HOST_RUN_ID, turn_mode="agent", ordinary_work_intent="execute")
    session.evolution_confirm_with_receipt = lambda _preview, _allow: ("n", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    class Client:
        def __init__(self): self.grants = []; self.requests = []
        def inspect(self, evolution_id, conversation_id, host_run_id):
            return {"status": "succeeded", "result": {"evolution_id": evolution_id, "conversation_id": conversation_id, "state_revision": 9}}
        def authorize_grant(self, **grant): self.grants.append(grant); return "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        def request(self, request, timeout_s=10): self.requests.append(request); raise AssertionError("denied action must not execute")
    client = Client()
    payload = {"source_snapshot_id": "sha256:" + "a" * 64, "descriptor_artifact_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "build_input_digest": "sha256:" + "b" * 64, "isolation_mode": "local_managed"}
    result = evolution.run({"action": "build", "payload": payload}, client=client, session=session)
    assert result.ok is False
    assert client.grants == [] and client.requests == []



def test_candidate_launch_confirms_exact_candidate_and_keeps_stop_grant_in_session_only():
    evolution = _evolution_tool()
    from tools.executor import ExecutorSession
    from pathlib import Path
    candidate = "sha256:" + "a" * 64
    profile = "77777777-7777-4777-8777-777777777777"
    launch_grant = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    stop_grant = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    instance = "99999999-9999-4999-8999-999999999999"
    session = ExecutorSession(session_dir=Path("/sessions") / _CONVERSATION_ID, evolution_id=_EVOLUTION_ID, host_run_id=_HOST_RUN_ID, evolution_authorization_id=_AUTH_ID, turn_mode="agent", ordinary_work_intent="execute")
    receipts = []
    session.evolution_confirm_with_receipt = lambda preview, allow: (receipts.append((preview, allow)) or ("yes", "cccccccc-cccc-4ccc-8ccc-cccccccccccc"))
    class Client:
        def __init__(self): self.requests=[]; self.grants=[]
        def inspect(self, evolution_id, conversation_id, host_run_id):
            return {"status":"succeeded","result":{"evolution_id":evolution_id,"conversation_id":conversation_id,"state_revision":4,"current_candidate_id":candidate,"isolation_mode":"local_managed"}}
        def authorize_grant(self, **grant): self.grants.append(grant); return launch_grant
        def request(self, request, timeout_s=10):
            self.requests.append(request)
            return {"operation_id":request["operation_id"],"evolution_id":request["evolution_id"],"state_revision":5,"status":"succeeded","result":{"candidate_id":candidate,"instance_id":instance,"status":"ready"},"error":None}
        def authorization_for_instance(self, evolution_id, conversation_id, instance_id):
            assert (evolution_id,conversation_id,instance_id)==(_EVOLUTION_ID,_CONVERSATION_ID,instance)
            return stop_grant
    client=Client()
    result=evolution.run({"action":"launch","payload":{"candidate_id":candidate,"profile_fixture_id":profile}},client=client,session=session)
    assert result.ok is True
    assert client.requests[0]["authorization_id"]==launch_grant
    assert client.grants[0]["kind"]=="candidate.launch"
    assert client.grants[0]["constraints"]=={"candidate_id":candidate,"profile_fixture_id":profile,"isolation_mode":"local_managed"}
    assert session.evolution_stop_authorization_id==stop_grant
    assert session.evolution_stop_instance_id==instance
    assert session.evolution_authorization_id==_AUTH_ID
    assert receipts[0][1] is False and candidate in receipts[0][0] and profile in receipts[0][0]
    assert stop_grant not in repr(result.data)


def test_failed_launch_still_retains_stop_only_authority_for_partial_owned_instance():
    evolution = _evolution_tool()
    from tools.executor import ExecutorSession
    from pathlib import Path
    candidate = "sha256:" + "a" * 64
    profile = "77777777-7777-4777-8777-777777777777"
    instance = "99999999-9999-4999-8999-999999999999"
    stop_grant = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    session = ExecutorSession(session_dir=Path("/sessions") / _CONVERSATION_ID, evolution_id=_EVOLUTION_ID, host_run_id=_HOST_RUN_ID, turn_mode="agent", ordinary_work_intent="execute")
    session.evolution_confirm_with_receipt = lambda _preview, _allow: ("yes", "cccccccc-cccc-4ccc-8ccc-cccccccccccc")
    class Client:
        def inspect(self, evolution_id, conversation_id, host_run_id):
            return {"status":"succeeded","result":{"evolution_id":evolution_id,"conversation_id":conversation_id,"state_revision":4,"current_candidate_id":candidate,"isolation_mode":"local_managed"}}
        def authorize_grant(self, **_grant): return "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        def request(self, request):
            return {"operation_id":request["operation_id"],"evolution_id":request["evolution_id"],"state_revision":5,"status":"failed","result":{"candidate_id":candidate,"instance_id":instance,"status":"failed"},"error":{"code":"start_failed","message":"partial launch"}}
        def authorization_for_instance(self, evolution_id, conversation_id, instance_id): return stop_grant
    result=evolution.run({"action":"launch","payload":{"candidate_id":candidate,"profile_fixture_id":profile}},client=Client(),session=session)
    assert result.ok is False
    assert session.evolution_stop_authorization_id==stop_grant
    assert session.evolution_stop_instance_id==instance


def test_stop_tool_recovers_only_the_exact_current_instance_grant_after_session_reload():
    evolution = _evolution_tool()
    from tools.executor import ExecutorSession
    from pathlib import Path
    instance = "99999999-9999-4999-8999-999999999999"
    stop_grant = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    session = ExecutorSession(session_dir=Path("/sessions") / _CONVERSATION_ID, evolution_id=_EVOLUTION_ID, host_run_id=_HOST_RUN_ID, evolution_authorization_id=_AUTH_ID, turn_mode="agent", ordinary_work_intent="execute")
    class Client:
        def __init__(self): self.requests=[]; self.resolves=[]
        def inspect(self, evolution_id, conversation_id, host_run_id):
            return {"status":"succeeded","result":{"evolution_id":evolution_id,"conversation_id":conversation_id,"state_revision":4,"current_instance_id":instance,"candidate_status":"ready"}}
        def authorization_for_instance(self, evolution_id, conversation_id, instance_id):
            self.resolves.append((evolution_id,conversation_id,instance_id));return stop_grant
        def request(self, request):
            self.requests.append(request);return {"operation_id":request["operation_id"],"evolution_id":request["evolution_id"],"state_revision":5,"status":"succeeded","result":{"instance_id":instance,"status":"stopped"},"error":None}
    client=Client()
    result=evolution.run({"action":"stop","payload":{"instance_id":instance}},client=client,session=session)
    assert result.ok is True
    assert client.resolves==[(_EVOLUTION_ID,_CONVERSATION_ID,instance)]
    assert client.requests[0]["authorization_id"]==stop_grant
    assert session.evolution_stop_authorization_id==""
