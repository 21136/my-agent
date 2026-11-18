from dataclasses import asdict
import json

from incident_repair import IncidentStore, RepairResult, RepairWorker
from self_evolution_auto_runner import Verification


def test_evidence_does_not_store_exception_or_host_path(tmp_path):
    try:
        raise RuntimeError("do not record raw credential path or transcript")
    except RuntimeError as error:
        store = IncidentStore(tmp_path)
        evidence = store.record(error, request_id="user text", version="a" * 64)
    saved = (tmp_path / (evidence.incident_id + ".json")).read_text(encoding="utf-8")
    assert "credential" not in saved and str(tmp_path) not in saved
    assert json.loads(saved) == asdict(evidence)
    assert evidence.request_id is None
    assert evidence.effect == "tool_result_not_saved"
    assert store.load(evidence.incident_id) == evidence
    assert store.load("../bad") is None


def test_wrong_baseline_failure_stops_without_model_or_writes(tmp_path):
    store = IncidentStore(tmp_path)
    evidence = store.record(RuntimeError(), request_id=None, version="a" * 64)
    calls = []
    worker = RepairWorker(
        store, lambda lane: {"agent-core/session.py": "x = 1\n"},
        lambda tree: Verification(False, "unrelated syntax or import failure", 1),
        lambda *args: calls.append("model"),
    )
    assert worker.run(evidence, primary="stable").status == "blocked"
    assert not calls
    assert not list(tmp_path.glob("*.candidate.json"))


def test_candidate_materialized_only_after_protected_red_and_green(tmp_path):
    store = IncidentStore(tmp_path)
    evidence = store.record(RuntimeError(), request_id=None, version="a" * 64)
    path = "agent-core/session.py"
    calls = []

    def verify(source):
        calls.append(source[path])
        if source[path] == "x = 1\n":
            return Verification(False, "E    Failed: INCIDENT_EXPLORE_PENDING_WRITE_CONFLICT\n"
                                "FAILED tests/test_incident_repair_oracle.py::"
                                "test_live_explore_read_does_not_reopen_and_repair_parent_pending_call\n"
                                "1 failed, 1 passed in 0.10s", 1)
        return Verification(True, "1 passed", 0)

    def complete(messages, model):
        if messages[0]["content"].startswith("Independently review"):
            return '{"decision":"accept"}'
        assert model == "0x567-flash"
        return json.dumps({"action": "edit", "message": "fix", "edits": [
            {"path": path, "before": "x = 1", "after": "x = 2"},
        ]})

    worker = RepairWorker(store, lambda lane: {path: "x = 1\n"}, verify, complete)
    result = worker.run(evidence, primary="stable")
    assert isinstance(result, RepairResult) and result.status == "candidate"
    assert calls == ["x = 1\n", "x = 2\n"]
    saved = json.loads((tmp_path / (evidence.incident_id + ".candidate.json")).read_text(encoding="utf-8"))
    assert saved["edits"] == [{"path": path, "before": "x = 1", "after": "x = 2"}]


def test_websocket_command_is_bound_to_offered_incident_and_session(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from threading import Event

    import incident_repair
    from server import WsSessionHandler

    handler = object.__new__(WsSessionHandler)
    handler.paths = SimpleNamespace(data=tmp_path)
    incidents = IncidentStore(tmp_path / "incidents")
    evidence = incidents.record(RuntimeError(), request_id=None, version="a" * 64)
    request_id = "4cf95668-67c7-44d8-9fc4-0aac540ad834"
    message = {"type": "incident.repair", "incident_id": evidence.incident_id,
               "request_id": request_id}
    events, runs = [], []
    bridge = SimpleNamespace(emit=events.append, _connection_closed=Event(),
                             incident_repair_offer=(evidence.incident_id, "session-a"))
    repl = SimpleNamespace(session=SimpleNamespace(conversation_id="session-b"))
    monkeypatch.setattr(incident_repair, "available", lambda paths: True)

    def repair(paths, store, actual, *, cancelled):
        assert not cancelled() and actual == evidence
        runs.append(actual.incident_id)
        return RepairResult("candidate", "only local candidate")

    monkeypatch.setattr(incident_repair, "repair_installation", repair)
    asyncio.run(handler._dispatch_incident_repair(message, repl, bridge))
    assert [event["status"] for event in events] == ["blocked"]
    assert not runs
    repl.session.conversation_id = "session-a"
    asyncio.run(handler._dispatch_incident_repair(message, repl, bridge))
    asyncio.run(handler._dispatch_incident_repair(message, repl, bridge))
    assert [event["status"] for event in events] == ["blocked", "running", "candidate", "blocked"]
    assert runs == [evidence.incident_id]
    assert all(event["request_id"] == request_id for event in events)


def test_verifier_requires_both_protected_oracles_to_pass():
    from self_evolution_auto_runner import DockerVerifier, _format_result_sentinel

    gate = DockerVerifier._has_success_sentinel
    assert not gate(_format_result_sentinel("token", 0, 1), "token", 0, minimum_passes=2)
    assert gate(_format_result_sentinel("token", 0, 2), "token", 0, minimum_passes=2)
