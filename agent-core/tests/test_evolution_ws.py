from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

import project_api
import server
from execution_lifecycle import ExecutionLifecycle
from paths import AgentPaths
from server import WsBridge, _run_line
from session import Session, SessionMeta

CONVERSATION_ID = "33333333-3333-4333-8333-333333333333"
HOST_RUN_ID = "44444444-4444-4444-8444-444444444444"
SCOPE_ID = "55555555-5555-4555-8555-555555555555"
EVOLUTION_ID = "66666666-6666-4666-8666-666666666666"
AUTHORIZATION_ID = "77777777-7777-4777-8777-777777777777"


def _temporary_session(tmp_path: Path) -> Session:
    root = tmp_path / "host"
    index = root / "evolve" / "_index.core.toml"
    index.parent.mkdir(parents=True)
    index.write_text('version = 1\n', encoding="utf-8")
    paths = AgentPaths.from_root(root)
    return Session(
        conversation_id=CONVERSATION_ID,
        session_dir=paths.data / "sessions" / CONVERSATION_ID,
        goal="T8 lifecycle binding fixture",
        meta=SessionMeta(),
        messages=[],
        paths=paths,
    )


def test_each_real_agent_turn_binds_the_existing_lifecycle_id_without_persisting_it(
    tmp_path: Path, monkeypatch
):
    session = _temporary_session(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    bridge.lifecycle = ExecutionLifecycle(run_id_factory=lambda: HOST_RUN_ID)
    bound: list[tuple[str, str]] = []
    repl = SimpleNamespace(
        session=session,
        agent=SimpleNamespace(
            executor=SimpleNamespace(
                session=SimpleNamespace(evolution_authorization_id=AUTHORIZATION_ID)
            ),
            bind_host_run_context=lambda run_id, authorization_id="": bound.append(
                (run_id, authorization_id)
            ),
        ),
        last_turn_finish_reason=None,
        handle_line=lambda _line: "continue",
    )
    monkeypatch.setattr(project_api, "after_turn_project_hooks", lambda *_args: None)
    monkeypatch.setattr(
        server,
        "session_memory_event",
        lambda *_args, **_kwargs: {"type": "session.memory"},
    )

    asyncio.run(_run_line(repl, bridge, "continue the same task", session.paths))

    assert bound == [(HOST_RUN_ID, AUTHORIZATION_ID)]
    assert bridge.lifecycle_conversation_id == CONVERSATION_ID
    assert session.meta.evolution_id == ""
    assert HOST_RUN_ID not in repr(session.meta.to_dict())
    assert any(event.get("type") == "execution.state" and event.get("run_id") == HOST_RUN_ID for event in events)

class _SupervisorClient:
    def __init__(self):
        self.begin_calls: list[dict] = []
        self.inspect_calls: list[tuple[str, str, str]] = []
        self.projections = [
            {
                "evolution_id": EVOLUTION_ID,
                "conversation_id": CONVERSATION_ID,
                "state_revision": 0,
                "candidate_status": "not_run",
            },
            {
                "evolution_id": EVOLUTION_ID,
                "conversation_id": CONVERSATION_ID,
                "state_revision": 2,
                "candidate_status": "failed",
                "current_candidate_id": "sha256:" + "a" * 64,
                "current_instance_id": "88888888-8888-4888-8888-888888888888",
                "pending_operation_ids": [],
                "unknown_operation_ids": [],
                "prototype_control_verified": False,
                "scripted_recovery_verified": False,
                "agent_recovery_verified": False,
                "independent_review": "incomplete",
            },
            {
                "evolution_id": EVOLUTION_ID,
                "conversation_id": CONVERSATION_ID,
                "state_revision": 4,
                "candidate_status": "ready",
                "current_candidate_id": "sha256:" + "b" * 64,
                "current_instance_id": "99999999-9999-4999-8999-999999999999",
                "pending_operation_ids": [],
                "unknown_operation_ids": [],
                "prototype_control_verified": False,
                "scripted_recovery_verified": False,
                "agent_recovery_verified": False,
                "independent_review": "incomplete",
            },
        ]

    def begin_binding(
        self,
        conversation_id: str,
        scope_id: str,
        confirmation_id: str,
        expires_at: str,
    ) -> dict:
        self.begin_calls.append(
            {
                "conversation_id": conversation_id,
                "scope_id": scope_id,
                "confirmation_id": confirmation_id,
                "expires_at": expires_at,
            }
        )
        return {
            "evolution_id": EVOLUTION_ID,
            "authorization_id": AUTHORIZATION_ID,
        }

    def inspect(self, evolution_id: str, conversation_id: str, host_run_id: str) -> dict:
        self.inspect_calls.append((evolution_id, conversation_id, host_run_id))
        projection = {
            "evolution_id": evolution_id,
            "conversation_id": conversation_id,
            "state_revision": 0,
            "candidate_status": "not_run",
        }
        if self.projections:
            projection = self.projections.pop(0)
        return {"status": "succeeded", "result": projection}


async def _confirm_pending_bridge_request(bridge: WsBridge, task) -> None:
    for _ in range(100):
        request_id = bridge._pending_confirm_id
        if request_id:
            assert bridge.deliver_confirm(request_id, "y")
            await task
            return request_id
        await asyncio.sleep(0.005)
    raise AssertionError("evolution.begin did not use the existing confirmation chain")


def test_begin_and_reinspect_keep_failure_and_repair_on_the_same_trusted_session(
    tmp_path: Path, monkeypatch
):
    async def _exercise():
        session = _temporary_session(tmp_path)
        bridge = WsBridge(emit=lambda _event: None, paths=session.paths)
        bridge.lifecycle = ExecutionLifecycle(run_id_factory=lambda: HOST_RUN_ID)
        host_run = bridge.lifecycle.start_or_activate("ordinary")
        bridge.lifecycle.finish("completed", ok=True, run_id=host_run.run_id)
        bridge.lifecycle_conversation_id = CONVERSATION_ID
        events: list[dict] = []
        bridge.emit = events.append
        client = _SupervisorClient()
        handler = server.WsSessionHandler(
            session.paths,
            evolution_client=client,
            evolution_scope_id=SCOPE_ID,
            evolution_owned_paths=("main.cjs",),
        )
        bound_context: list[tuple[str, str]] = []
        repl = SimpleNamespace(
            session=session,
            agent=SimpleNamespace(
                executor=SimpleNamespace(
                    evolution_client=None,
                    session=SimpleNamespace(
                        evolution_id="", host_run_id="", evolution_authorization_id=""
                    ),
                ),
                bind_host_run_context=lambda run_id, authorization_id="": bound_context.append(
                    (run_id, authorization_id)
                ),
            ),
        )

        begin = asyncio.create_task(
            handler._dispatch(
                {
                    "type": "evolution.begin",
                    "conversation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                },
                repl,
                bridge,
            )
        )
        confirmation_id = await _confirm_pending_bridge_request(bridge, begin)

        assert session.meta.evolution_id == EVOLUTION_ID
        assert client.begin_calls[0]["conversation_id"] == CONVERSATION_ID
        assert client.begin_calls[0]["scope_id"] == SCOPE_ID
        assert client.begin_calls[0]["confirmation_id"] == confirmation_id
        from datetime import datetime, timedelta, timezone
        expires_at = datetime.fromisoformat(client.begin_calls[0]["expires_at"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        assert now < expires_at <= now + timedelta(minutes=11)
        confirm_preview = next(event["preview"] for event in events if event.get("type") == "confirm.request")
        assert "main.cjs" in confirm_preview
        assert "local_managed" in confirm_preview
        assert "10 分钟" in confirm_preview
        assert bound_context == [(HOST_RUN_ID, AUTHORIZATION_ID)]
        assert repl.agent.executor.evolution_client is client

        await handler._dispatch({"type": "evolution.inspect"}, repl, bridge)
        await handler._dispatch({"type": "evolution.inspect"}, repl, bridge)
        states = [event for event in events if event.get("type") == "evolution.state"]
        assert [
            event["projection"]["candidate_status"] for event in states
        ] == ["failed", "ready"]
        assert all(event["conversation_id"] == CONVERSATION_ID for event in states)
        assert all(event["host_run_id"] == HOST_RUN_ID for event in states)
        assert session.meta.evolution_id == EVOLUTION_ID


    asyncio.run(_exercise())

def test_begin_fails_closed_without_a_real_lifecycle_identity(tmp_path: Path):
    session = _temporary_session(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    client = _SupervisorClient()
    handler = server.WsSessionHandler(
        session.paths, evolution_client=client, evolution_scope_id=SCOPE_ID
    )
    repl = SimpleNamespace(
        session=session,
        agent=SimpleNamespace(
            executor=SimpleNamespace(
                session=SimpleNamespace(
                    evolution_id="", host_run_id="", evolution_authorization_id=""
                )
            )
        ),
    )

    asyncio.run(handler._dispatch({"type": "evolution.begin"}, repl, bridge))

    assert client.begin_calls == []
    assert session.meta.evolution_id == ""
    assert any(event.get("type") == "error" for event in events)



OTHER_CONVERSATION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def test_begin_rejects_a_lifecycle_snapshot_from_a_different_open_session(tmp_path: Path):
    async def _exercise():
        session = _temporary_session(tmp_path)
        events: list[dict] = []
        bridge = WsBridge(emit=events.append, paths=session.paths)
        bridge.lifecycle = ExecutionLifecycle(run_id_factory=lambda: HOST_RUN_ID)
        host_run = bridge.lifecycle.start_or_activate("ordinary")
        bridge.lifecycle.finish("completed", ok=True, run_id=host_run.run_id)
        bridge.lifecycle_conversation_id = OTHER_CONVERSATION_ID
        client = _SupervisorClient()
        handler = server.WsSessionHandler(
            session.paths, evolution_client=client, evolution_scope_id=SCOPE_ID
        )
        repl = SimpleNamespace(
            session=session,
            agent=SimpleNamespace(
                executor=SimpleNamespace(
                    session=SimpleNamespace(
                        evolution_id="", host_run_id="", evolution_authorization_id=""
                    )
                ),
                bind_host_run_context=lambda *_args: None,
            ),
        )

        await handler._dispatch({"type": "evolution.begin"}, repl, bridge)

        assert client.begin_calls == []
        assert session.meta.evolution_id == ""
        assert any(event.get("type") == "error" for event in events)

    asyncio.run(_exercise())

def test_confirmation_receipt_returns_the_exact_request_id_that_was_approved():
    import threading
    import time

    bridge = WsBridge(emit=lambda _event: None, paths=AgentPaths.discover())
    assert callable(getattr(bridge, "confirm_with_receipt", None))
    results: list[tuple[str, str]] = []
    worker = threading.Thread(
        target=lambda: results.append(bridge.confirm_with_receipt("bind scope", False)),
        daemon=True,
    )
    worker.start()
    for _ in range(100):
        request_id = bridge._pending_confirm_id
        if request_id:
            break
        time.sleep(0.005)
    assert bridge._pending_confirm_id
    approved_id = bridge._pending_confirm_id
    assert bridge.deliver_confirm(approved_id, "y")
    worker.join(timeout=1)

    assert results == [("y", approved_id)]


def test_private_sidecar_bootstrap_parser_accepts_only_bounded_exact_json():
    import json
    import sys
    import pytest
    from evolution.contracts import canonical_json

    parser = getattr(server, "parse_evolution_sidecar_bootstrap", None)
    assert callable(parser), "server must parse the parent-owned private bootstrap from stdin"
    bootstrap = {
        "protocol": "my-agent-evolution-sidecar/1.0",
        "control_release": {
            "release_id": "sha256:" + "a" * 64,
            "root": str(Path(sys.executable).resolve().parent),
            "node_executable": str(Path(sys.executable).resolve()),
            "python_executable": str(Path(sys.executable).resolve()),
            "manifest_path": str(Path(sys.executable).resolve()),
            "manifest_digest": "sha256:" + "a" * 64,
        },
        "pipe_name": r"\\.\pipe\my-agent-evolution-12345678-1234-4234-8234-123456789012",
        "host_instance_id": "44444444-4444-4444-8444-444444444444",
        "host_token": "c" * 64,
        "scope_id": SCOPE_ID,
        "owned_paths": ["main.cjs", "src/index.ts"],
    }
    encoded = canonical_json(bootstrap) + b"\n"
    parsed = parser(encoded)
    assert parsed == bootstrap
    assert parser(encoded)["host_token"] == "c" * 64

    with pytest.raises(ValueError):
        parser(canonical_json({**bootstrap, "unexpected": "reject"}) + b"\n")
    with pytest.raises(ValueError):
        parser(canonical_json({**bootstrap, "owned_paths": ["../escape.cjs"]}) + b"\n")
    with pytest.raises(ValueError):
        parser(encoded + b"extra")


def test_ui_stop_uses_the_current_instance_grant_and_refreshes_projection(tmp_path, monkeypatch):
    async def _exercise():
        session = _temporary_session(tmp_path)
        session.meta.evolution_id = EVOLUTION_ID
        events: list[dict] = []
        bridge = WsBridge(emit=events.append, paths=session.paths)
        bridge.lifecycle = ExecutionLifecycle(run_id_factory=lambda: HOST_RUN_ID)
        bridge.lifecycle.start_or_activate("ordinary")
        bridge.lifecycle_conversation_id = CONVERSATION_ID
        stop_grant = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        instance_id = "99999999-9999-4999-8999-999999999999"
        executor_session = SimpleNamespace(evolution_authorization_id=AUTHORIZATION_ID, evolution_stop_authorization_id="", evolution_stop_instance_id="")
        repl = SimpleNamespace(session=session, agent=SimpleNamespace(executor=SimpleNamespace(session=executor_session)))
        requests = []
        projections = [
            {"evolution_id": EVOLUTION_ID, "conversation_id": CONVERSATION_ID, "state_revision": 3, "current_instance_id": instance_id, "candidate_status": "ready"},
            {"evolution_id": EVOLUTION_ID, "conversation_id": CONVERSATION_ID, "state_revision": 5, "current_instance_id": instance_id, "candidate_status": "stopped"},
        ]
        class Client:
            def inspect(self, *_args): return {"status": "succeeded", "result": projections.pop(0)}
            def resolve_stop_authorization(self, conversation_id, evolution_id, target_instance):
                assert (conversation_id, evolution_id, target_instance) == (CONVERSATION_ID, EVOLUTION_ID, instance_id)
                return stop_grant
            def request(self, request): requests.append(request); return {"status": "succeeded", "result": {"status": "stopped"}}
        handler = server.WsSessionHandler(session.paths, evolution_client=Client())
        await handler._dispatch({"type": "evolution.stop"}, repl, bridge)
        assert requests[0]["authorization_id"] == stop_grant
        assert requests[0]["payload"] == {"instance_id": instance_id}
        assert executor_session.evolution_authorization_id == AUTHORIZATION_ID
        assert executor_session.evolution_stop_authorization_id == ""
        assert executor_session.evolution_stop_instance_id == ""
        state = next(event for event in events if event.get("type") == "evolution.state")
        assert state["projection"]["candidate_status"] == "stopped"
        assert state["projection"]["state_revision"] == 5

    monkeypatch.setattr(project_api, "after_turn_project_hooks", lambda *_args: None)
    asyncio.run(_exercise())


def test_t9_scripted_driver_starts_a_model_free_lifecycle_only_for_fixed_t4_profile(tmp_path: Path):
    session = _temporary_session(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    bridge.lifecycle = ExecutionLifecycle(run_id_factory=lambda: HOST_RUN_ID)
    handler = server.WsSessionHandler(
        session.paths,
        evolution_client=SimpleNamespace(),
        evolution_scope_id=SCOPE_ID,
        evolution_owned_paths=server.T9_T4_OWNED_PATHS,
    )
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(executor=SimpleNamespace(session=SimpleNamespace())))
    request = {
        "type": "evolution.scripted", "request_id": "99999999-9999-4999-8999-999999999999",
        "conversation_id": CONVERSATION_ID, "operation": "lifecycle.begin",
    }
    asyncio.run(handler._dispatch_scripted_evolution(request, repl, bridge))
    result = next(event for event in events if event.get("type") == "evolution.scripted.result")
    assert result["status"] == "succeeded"
    assert result["result"] == {"conversation_id": CONVERSATION_ID, "host_run_id": HOST_RUN_ID}
    assert bridge.lifecycle_conversation_id == CONVERSATION_ID
    assert not any(event.get("type") in {"llm.pending", "assistant.delta"} for event in events)


def test_t9_scripted_inspect_uses_the_fixed_host_client_for_the_active_session(tmp_path: Path):
    session = _temporary_session(tmp_path)
    session.meta.evolution_id = EVOLUTION_ID
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    bridge.lifecycle = ExecutionLifecycle(run_id_factory=lambda: HOST_RUN_ID)
    bridge.lifecycle.start_or_activate("scripted_recovery")
    bridge.lifecycle_conversation_id = CONVERSATION_ID

    class Client:
        def __init__(self):
            self.requests: list[dict] = []

        def request(self, request):
            self.requests.append(request)
            return {
                "operation_id": request["operation_id"],
                "evolution_id": EVOLUTION_ID,
                "state_revision": 1,
                "status": "succeeded",
                "result": {"conversation_id": CONVERSATION_ID, "evolution_id": EVOLUTION_ID},
                "error": None,
            }

    client = Client()
    handler = server.WsSessionHandler(
        session.paths, evolution_client=client, evolution_scope_id=SCOPE_ID,
        evolution_owned_paths=server.T9_T4_OWNED_PATHS,
    )
    repl = SimpleNamespace(
        session=session,
        agent=SimpleNamespace(executor=SimpleNamespace(session=SimpleNamespace(evolution_id=EVOLUTION_ID, host_run_id=HOST_RUN_ID))),
    )
    request = {
        "type": "evolution.scripted", "request_id": "99999999-9999-4999-8999-999999999999",
        "conversation_id": CONVERSATION_ID, "operation": "action", "action": "inspect",
        "payload": {"query": "summary"},
    }
    asyncio.run(handler._dispatch_scripted_evolution(request, repl, bridge))
    result = next(event for event in events if event.get("type") == "evolution.scripted.result")
    assert result["status"] == "succeeded", result.get("error")
    assert len(client.requests) == 1
    assert client.requests[0]["conversation_id"] == CONVERSATION_ID
    assert client.requests[0]["host_run_id"] == HOST_RUN_ID
    assert client.requests[0]["action"] == "inspect"
def test_t9_scripted_driver_rejects_any_non_fixed_owned_scope(tmp_path: Path):
    session = _temporary_session(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    handler = server.WsSessionHandler(
        session.paths,
        evolution_client=SimpleNamespace(),
        evolution_scope_id=SCOPE_ID,
        evolution_owned_paths=("*",),
    )
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(executor=SimpleNamespace(session=SimpleNamespace())))
    request = {
        "type": "evolution.scripted", "request_id": "99999999-9999-4999-8999-999999999999",
        "conversation_id": CONVERSATION_ID, "operation": "lifecycle.begin",
    }
    asyncio.run(handler._dispatch_scripted_evolution(request, repl, bridge))
    result = next(event for event in events if event.get("type") == "evolution.scripted.result")
    assert result["status"] == "failed" and result["error"]["code"] == "permission_denied"
    assert bridge.lifecycle.snapshot() is None