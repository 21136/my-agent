from __future__ import annotations

import json
import threading
from pathlib import Path

from llm_client import LLMResponse
from self_evolution_host import _skill_worker_task_id
from self_evolution_worker import HostTool, SelfEvolutionWorkerContext, run_self_evolution_worker


BODY_MARKER = "TEMP_REAL_SKILL_BODY_92e7c1"


def _call(name: str, args: dict, call_id: str) -> dict:
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args),
    }}


class _FakeModel:
    """Deterministic confirmed-tools client; exercises the production worker loop."""

    def __init__(self, calls: list[dict | None]):
        self.responses = list(calls)
        self.requests: list[list[dict]] = []
        self.tool_schemas: list[list[dict]] = []
        self._cancel_event = None
        self._active_client = None

    def set_cancel_event(self, event):
        self._cancel_event = event

    def chat_confirmed_tools(self, messages, **kwargs):
        assert kwargs["confirm_fn"]("skill loop test", False) == "y"
        assert kwargs["after_each"]() is True
        kwargs["on_request_attempt"]()
        self.requests.append([dict(message) for message in messages])
        self.tool_schemas.append(kwargs["tools"])
        call = self.responses.pop(0)
        if call is None:
            return LLMResponse(content="Finished within test bounds", tool_calls=[], finish_reason="stop", usage=None,
                               model=kwargs["model_id"], raw={"choices": [{"message": {"tool_calls": []}}]})
        return LLMResponse(content="", tool_calls=[call], finish_reason="tool_calls", usage=None,
                           model=kwargs["model_id"], raw={"choices": [{"message": {"tool_calls": [call]}}]})


def _host_tools():
    return (
        HostTool("observe_candidate", "observe", lambda _ctx, _args: {"status": "observed"}),
        HostTool("read_candidate_file", "read", lambda _ctx, _args: {"status": "read", "content": ""}),
        HostTool("write_candidate_file", "write", lambda _ctx, _args: {"status": "written"}),
        HostTool("run_registered_check", "check", lambda _ctx, args: {
            "status": "passed", "check_id": args["check_id"], "candidate_digest": "digest-v1",
            "evidence_ref": "test-evidence", "process_tree_state": "stopped",
            "requires_reconciliation": False,
        }),
    )


def _context(skills_root: Path, grants: tuple[str, ...], *, attempt: str = "attempt-1"):
    return SelfEvolutionWorkerContext(
        session_id="session-1", task_id="task-1", attempt_id=attempt, run_id="run-1", turn_epoch=3,
        candidate_id="candidate-1", candidate_digest="digest-v1", cancel_event=threading.Event(),
        model_id="fake-model", outbound_request_confirm_fn=lambda _preview, allow_all: "y" if not allow_all else "n",
        after_each=lambda: True, current_candidate_digest=lambda: "digest-v1",
        skills_root=skills_root, authorized_skill_ids=grants,
        skill_worker_task_id=_skill_worker_task_id("session-1", "task-1", attempt, "run-1", 3),
    )


def _write_skill(root: Path, skill_id: str = "temporary-worker-skill") -> Path:
    skill = root / skill_id
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {skill_id}\ndescription: Test-only task workflow.\n"
        "triggers: [temporary]\nallowed_tools: []\nrisk_level: low\n"
        "verification: inspect\nversion: 1.0.0\naudiences: [worker]\n---\n\n"
        + BODY_MARKER,
        encoding="utf-8",
    )
    return skill


def _responses(*, load: str | None):
    result = []
    if load is not None:
        result.append(_call("load_worker_workflow_skill", {"skill_id": load}, "load"))
    result.extend([
        _call("observe_candidate", {}, "observe"),
        _call("write_candidate_file", {"path": "note.txt", "content": "updated"}, "write"),
        _call("run_registered_check", {"check_id": "syntax"}, "check"),
        None,
    ])
    return result


def test_worker_loop_exposes_only_authorized_skill_catalog_and_loads_body_on_demand(tmp_path):
    import skill_runtime

    skill_runtime.clear_skill_index_cache_for_tests()
    skill_runtime.clear_worker_skill_states_for_tests()
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _write_skill(skills_root)
    model = _FakeModel(_responses(load="temporary-worker-skill"))

    result = run_self_evolution_worker(
        "Improve safely", client=model, context=_context(skills_root, ("temporary-worker-skill",)),
        tools=_host_tools(),
    )

    assert result.status == "finished", result.error_code
    prompts = [next(m["content"] for m in req if m["role"] == "system") for req in model.requests]
    assert "temporary-worker-skill" in prompts[0]
    assert BODY_MARKER not in prompts[0]
    assert BODY_MARKER in prompts[1]
    assert "load_worker_workflow_skill" in {t["function"]["name"] for t in model.tool_schemas[0]}
    assert "read_candidate_file" in {t["function"]["name"] for t in model.tool_schemas[0]}
    assert skill_runtime.get_worker_skill_state(
        "session-1", _context(skills_root, ("temporary-worker-skill",)).skill_worker_task_id,
        "self-evolution", str(skills_root.resolve()), ("temporary-worker-skill",),
    ) is None


def test_worker_loop_denies_ungranted_skill_even_when_model_requests_its_id(tmp_path):
    import skill_runtime

    skill_runtime.clear_skill_index_cache_for_tests()
    skill_runtime.clear_worker_skill_states_for_tests()
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _write_skill(skills_root)
    _write_skill(skills_root, "other-authorized-skill")
    model = _FakeModel(_responses(load="temporary-worker-skill"))

    result = run_self_evolution_worker(
        "Improve safely", client=model,
        context=_context(skills_root, ("other-authorized-skill",)), tools=_host_tools(),
    )

    assert result.status == "finished", result.error_code
    system = model.requests[0][0]["content"]
    assert "temporary-worker-skill" not in system
    denial = json.loads(model.requests[1][-1]["content"])
    assert denial["error"] == "skill_not_authorized"
    assert BODY_MARKER not in "\n".join(m["content"] for req in model.requests for m in req if m["role"] == "system")


def test_run_or_attempt_change_gets_a_distinct_worker_skill_task_key():
    first = _skill_worker_task_id("session", "task", "attempt-a", "run-a", 4)
    retry = _skill_worker_task_id("session", "task", "attempt-b", "run-a", 4)
    next_run = _skill_worker_task_id("session", "task", "attempt-a", "run-b", 4)
    assert len({first, retry, next_run}) == 3


def test_worker_without_skill_configuration_preserves_legacy_four_tool_loop():
    context = SelfEvolutionWorkerContext(
        session_id="session-legacy", task_id="task-legacy", attempt_id="attempt-legacy", run_id="run-legacy",
        turn_epoch=0, candidate_id="candidate-legacy", candidate_digest="digest-v1",
        cancel_event=threading.Event(), model_id="fake-model",
        outbound_request_confirm_fn=lambda _preview, allow_all: "y" if not allow_all else "n",
        after_each=lambda: True, current_candidate_digest=lambda: "digest-v1",
    )
    model = _FakeModel(_responses(load=None))

    result = run_self_evolution_worker("Legacy task", client=model, context=context, tools=_host_tools())

    assert result.status == "finished", result.error_code
    assert {t["function"]["name"] for t in model.tool_schemas[0]} == {
        "observe_candidate", "read_candidate_file", "write_candidate_file", "run_registered_check",
    }
    assert "[Skill 授权目录]" not in model.requests[0][0]["content"]


def test_worker_loop_does_not_reuse_loaded_body_across_attempts(tmp_path):
    import skill_runtime

    skill_runtime.clear_skill_index_cache_for_tests()
    skill_runtime.clear_worker_skill_states_for_tests()
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _write_skill(skills_root)
    first_model = _FakeModel(_responses(load="temporary-worker-skill"))
    first_context = _context(skills_root, ("temporary-worker-skill",), attempt="attempt-a")
    first = run_self_evolution_worker(
        "Improve safely", client=first_model, context=first_context, tools=_host_tools(),
    )
    second_model = _FakeModel(_responses(load=None))
    second_context = _context(skills_root, ("temporary-worker-skill",), attempt="attempt-b")
    second = run_self_evolution_worker(
        "Improve safely", client=second_model, context=second_context, tools=_host_tools(),
    )

    assert first.status == second.status == "finished"
    assert first_context.skill_worker_task_id != second_context.skill_worker_task_id
    second_system = second_model.requests[0][0]["content"]
    assert "temporary-worker-skill" in second_system
    assert BODY_MARKER not in second_system


def test_trusted_capability_builder_binds_project_skill_root_and_only_trusted_grants(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from paths import AgentPaths
    from self_evolution_model_adapter import build_trusted_worker_capabilities
    from session import SessionMeta
    import self_evolution_model_adapter

    root = tmp_path / "agent-root"
    paths = AgentPaths(root, root / "workspace", root / "data", root / "evolve")
    paths.workspace.mkdir(parents=True)
    skills_root = paths.evolve / "skills"
    skills_root.mkdir(parents=True)
    _write_skill(skills_root)
    class _Client:
        _cancel_event = None
        _active_client = None
        def chat_confirmed_tools(self, *args, **kwargs):
            raise AssertionError("must not make a model call while building capabilities")
        def set_cancel_event(self, event):
            self._cancel_event = event

    monkeypatch.setattr(self_evolution_model_adapter, "create_self_evolution_model_adapter",
                        lambda _paths, _meta: SimpleNamespace(client=_Client(), model_id="fake-model"))
    caps = build_trusted_worker_capabilities(
        paths, SessionMeta(), tools=_host_tools(), outbound_request_confirm_fn=lambda *_: "y",
        process_tree_stopped=lambda _identity: True,
        authorized_skill_ids=("temporary-worker-skill", "not-present", "temporary-worker-skill"),
    )

    assert isinstance(paths, AgentPaths)
    assert caps.skills_root == paths.evolve / "skills"
    assert caps.authorized_skill_ids == ("temporary-worker-skill",)
    empty_caps = build_trusted_worker_capabilities(
        paths, SessionMeta(), tools=_host_tools(), outbound_request_confirm_fn=lambda *_: "y",
        process_tree_stopped=lambda _identity: True,
    )
    assert empty_caps.skills_root == caps.skills_root
    assert empty_caps.authorized_skill_ids == ()



def test_skill_loader_accepts_only_id_and_never_model_supplied_root(tmp_path):
    import skill_runtime

    skill_runtime.clear_skill_index_cache_for_tests()
    skill_runtime.clear_worker_skill_states_for_tests()
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _write_skill(skills_root)
    forged = _call("load_worker_workflow_skill", {
        "skill_id": "temporary-worker-skill", "skills_root": str(skills_root.parent),
    }, "forged-root")
    model = _FakeModel([forged])

    result = run_self_evolution_worker(
        "Improve safely", client=model,
        context=_context(skills_root, ("temporary-worker-skill",)), tools=_host_tools(),
    )

    assert result.status == "failed"
    assert result.error_code == "invalid_tool_arguments"
    assert BODY_MARKER not in str(model.requests)


def test_host_injects_trusted_grants_and_exact_attempt_binding_into_worker(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import self_evolution_host
    from self_evolution_host import EvolutionIdentity, SelfEvolutionHost, TrustedWorkerCapabilities

    skills_root = tmp_path / "host-skills"
    skills_root.mkdir()
    _write_skill(skills_root)
    identity = EvolutionIdentity("session-host", "task-host", "attempt-host", "run-host", 19)
    capabilities = TrustedWorkerCapabilities(
        client=_FakeModel([]), model_id="fake-model",
        outbound_request_confirm_fn=lambda _preview, allow_all: "y" if not allow_all else "n",
        tools=_host_tools(), process_tree_stopped=lambda _identity: True,
        skills_root=skills_root,
        authorized_skill_ids=("temporary-worker-skill",),
        enable_experimental_start=True,
    )
    host = object.__new__(SelfEvolutionHost)
    host._store = object()
    host._worker_capabilities = capabilities
    host._verification = None
    host._config = SimpleNamespace(verification_commands={}, verification_timeout_seconds=1)
    host._current_candidate_digest = lambda _state: "digest-v1"
    host._candidate_identity = lambda _record: "candidate-host"
    host._worker_binding_valid = lambda _state: True
    host._invoke_bound_tool = lambda _state, _ctx, tool, args: tool.invoke(_ctx, args)
    host._persist_worker_phase = lambda *_args: None
    host._settle_worker_result = lambda *_args, **_kwargs: None
    received = []
    def capture_worker(_task, **kwargs):
        received.append(kwargs["context"])
        return SimpleNamespace(status="finished")
    monkeypatch.setattr(self_evolution_host, "run_self_evolution_worker", capture_worker)

    state = SimpleNamespace(
        identity=identity, cancel_event=threading.Event(), owner_id="owner",
        workspace_record={}, guidance_inbox=None,
    )
    SelfEvolutionHost._run_worker(host, state, "host task")

    assert len(received) == 1
    context = received[0]
    assert context.skills_root == skills_root
    assert context.authorized_skill_ids == ("temporary-worker-skill",)
    assert context.skill_worker_task_id == _skill_worker_task_id(
        identity.session_id, identity.task_id, identity.attempt_id, identity.run_id, identity.turn_epoch,
    )
    assert context.skill_worker_task_id != _skill_worker_task_id(
        identity.session_id, identity.task_id, "replacement-attempt", identity.run_id, identity.turn_epoch,
    )


def test_host_skill_root_with_empty_grants_keeps_legacy_prompt_and_four_tools(tmp_path):
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _write_skill(skills_root)
    model = _FakeModel(_responses(load=None))
    context = _context(skills_root, ())

    result = run_self_evolution_worker(
        "Legacy no-grant task", client=model, context=context, tools=_host_tools(),
    )

    assert result.status == "finished", result.error_code
    assert {t["function"]["name"] for t in model.tool_schemas[0]} == {
        "observe_candidate", "read_candidate_file", "write_candidate_file", "run_registered_check",
    }
    assert "[Skill ????]" not in model.requests[0][0]["content"]
