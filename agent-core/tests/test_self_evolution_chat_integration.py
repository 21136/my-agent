from __future__ import annotations

from server import SelfEvolutionChatIntegration
from self_evolution_types import EvolutionEvent, EvolutionIdentity, EvolutionRequest, EvolutionSnapshot


class RecordingHost:
    worker_bound = True
    production_ready = True

    def __init__(self) -> None:
        self.starts = []

    def start(self, request, **_kwargs):
        self.starts.append(request)
        return {"status": "started"}


def test_client_mode_and_boolean_authority_are_not_accepted() -> None:
    host = RecordingHost()
    integration = SelfEvolutionChatIntegration(host=host, session_provider=lambda: "session-a", emit=lambda _: None)

    try:
        integration.start(EvolutionRequest("change the project"), mode="agent",
                          explicit_intent=True, user_authorized=True)
    except TypeError:
        pass
    else:
        raise AssertionError("client-selected mode and booleans must not be an authorization API")

    assert host.starts == []


def test_untrusted_host_cannot_become_production_capability_by_flags() -> None:
    host = RecordingHost()
    integration = SelfEvolutionChatIntegration(host=host, session_provider=lambda: "session-a", emit=lambda _: None)

    result = integration.start(EvolutionRequest("change the project"))

    assert result["status"] == "unavailable"
    assert host.starts == []


def test_missing_host_fails_closed() -> None:
    integration = SelfEvolutionChatIntegration(host=None, session_provider=lambda: "session-a", emit=lambda _: None)

    result = integration.start(EvolutionRequest("change the project"))

    assert result["status"] == "unavailable"


def test_update_isolated_to_authenticated_current_session() -> None:
    emitted = []
    current = {"id": "session-a"}
    integration = SelfEvolutionChatIntegration(host=None, session_provider=lambda: current["id"], emit=emitted.append)
    integration._trusted_production_host = lambda: object()
    identity = EvolutionIdentity("session-a", "task-1", "attempt-1", "old-run", 1)
    prior = EvolutionSnapshot(identity, "observing", 1)
    event = EvolutionEvent(identity, 1, "editing", "phase_changed", ("edit_started",), 2,
                           ("change-1",))

    assert integration.deliver_update(event, prior) is True
    current["id"] = "session-b"
    assert integration.deliver_update(event, prior) is False
    assert [item["session_id"] for item in emitted] == ["session-a"]


def test_cross_turn_update_does_not_require_parent_run_to_remain_active() -> None:
    emitted = []
    integration = SelfEvolutionChatIntegration(host=None, session_provider=lambda: "session-a", emit=emitted.append)
    integration._trusted_production_host = lambda: object()
    identity = EvolutionIdentity("session-a", "task-1", "attempt-1", "finished-parent-run", 1)
    prior = EvolutionSnapshot(identity, "checking", 2)
    event = EvolutionEvent(identity, 2, "awaiting_review", "phase_changed", ("awaiting_review",), 3)

    assert integration.deliver_update(event, prior) is True
    assert emitted[0]["run_id"] == "finished-parent-run"
    assert "run_id" not in emitted[0].get("run_scope", {})


def test_raw_frontend_shaped_dict_is_not_a_trusted_host_event() -> None:
    integration = SelfEvolutionChatIntegration(host=None, session_provider=lambda: "session-a", emit=lambda _: None)
    event = {
        "type": "self_evolution.update", "session_id": "session-a", "task_id": "task-1",
        "attempt_id": "attempt-1", "run_id": "old-run", "turn_epoch": 1, "sequence": 1,
        "phase": "editing", "kind": "worker_phase_changed", "fact_codes": ["editing_started"],
        "snapshot_revision": 2, "evidence_refs": [],
    }

    assert integration.deliver_update(event, object()) is False

def test_event_projection_rejects_stale_sequence_and_revision() -> None:
    from self_evolution_types import EvolutionEvent, EvolutionIdentity

    emitted = []
    integration = SelfEvolutionChatIntegration(host=None, session_provider=lambda: "session-a", emit=emitted.append)
    integration._trusted_production_host = lambda: object()
    identity = EvolutionIdentity("session-a", "task-1", "attempt-1", "run-1", 1)
    prior = EvolutionSnapshot(identity, "observing", 4)
    first = EvolutionEvent(identity, 2, "editing", "phase_changed", ("edit_started",), 5,
                           ("change-1",))
    stale = EvolutionEvent(identity, 1, "checking", "phase_changed", ("check_started",), 4,
                           ("check-1",))

    assert integration.deliver_update(first, prior) is True
    assert integration.deliver_update(stale, prior) is False
    assert len(emitted) == 1


def test_snapshot_projection_requires_same_session_and_typed_snapshot() -> None:
    from self_evolution_types import EvolutionIdentity, EvolutionSnapshot

    emitted = []
    current = {"id": "session-a"}
    identity = EvolutionIdentity("session-a", "task-1", "attempt-1", "old-run", 1)
    snapshot = EvolutionSnapshot(identity, "awaiting_review", 3, discoveries=("public_fact",))

    class TrustedHostPlaceholder:
        worker_bound = True
        production_ready = True

    integration = SelfEvolutionChatIntegration(
        host=TrustedHostPlaceholder(), session_provider=lambda: current["id"], emit=emitted.append,
        snapshot_provider=lambda _task_id: snapshot,
    )
    assert integration.deliver_snapshot("task-1") is False
    assert emitted == []

def test_python_projection_is_accepted_by_real_desktop_contract_and_drops_private_fields() -> None:
    import json
    import subprocess
    from pathlib import Path

    from self_evolution_projection import project_evolution
    from self_evolution_types import EvolutionEvent, EvolutionIdentity, EvolutionSnapshot

    snapshot = EvolutionSnapshot(
        EvolutionIdentity("session-a", "task-1", "attempt-1", "run-1", 1),
        "awaiting_review", 4,
        candidate_digest="private_digest_123",
        discoveries=("worker-private discovery with secret: value",),
        change_summary="unreviewed worker prose",
    )
    event = EvolutionEvent(
        snapshot.identity, 5, "editing", "phase_changed", ("observation_recorded",), 5,
        ("observation-1",),
    )
    bundle = project_evolution(
        snapshot, authorized_session_id="session-a", last_sequence=4,
        events=(event,), preview_handle=None, public_findings=(),
    )
    projected = bundle["snapshot"]
    projected.pop("type", None)  # Match the renderer's flat restore snapshot contract.
    assert set(projected) == {
        "session_id", "task_id", "attempt_id", "run_id", "turn_epoch",
        "phase", "status", "snapshot_revision", "sequence", "findings", "verification", "preview_handle",
    }
    assert projected["findings"] == []
    assert "candidate_digest" not in projected
    assert "discoveries" not in projected
    assert "change_summary" not in projected

    script = r'''
import assert from "node:assert/strict";
import { SelfEvolutionProgress } from "./desktop/src/shells/unified/self-evolution-progress.ts";
const payload = JSON.parse(process.argv[1]);
const progress = new SelfEvolutionProgress();
assert.equal(progress.restore(payload, { session_id: "session-a" }), true);
assert.equal(progress.current()?.phase, "awaiting_review");
assert.equal(progress.current()?.status, "completed");
assert.deepEqual(progress.current()?.findings, []);
const accepted = progress.accept(JSON.parse(process.argv[2]));
assert.equal(accepted?.phase, "editing");
assert.deepEqual(accepted?.findings, []);
'''
    root = Path(__file__).parents[2]
    subprocess.run(
        ["node", "--input-type=module", "-e", script,
         json.dumps(projected), json.dumps(bundle["events"][0])],
        cwd=root, check=True, capture_output=True, text=True,
    )



def test_event_revision_watermark_rejects_replayed_older_state_after_newer_event() -> None:
    emitted = []
    integration = SelfEvolutionChatIntegration(host=None, session_provider=lambda: "session-a", emit=emitted.append)
    integration._trusted_production_host = lambda: object()
    identity = EvolutionIdentity("session-a", "task-1", "attempt-1", "run-1", 1)
    prior = EvolutionSnapshot(identity, "observing", 4)
    newer = EvolutionEvent(identity, 2, "editing", "phase_changed", ("edit_started",), 5, ("change-1",))
    replay = EvolutionEvent(identity, 3, "checking", "phase_changed", ("check_started",), 4, ("check-1",))

    assert integration.deliver_update(newer, prior) is True
    assert integration.deliver_update(replay, EvolutionSnapshot(identity, "editing", 3)) is False
    assert len(emitted) == 1


def test_event_sequence_watermarks_are_isolated_between_sessions() -> None:
    emitted = []
    current = {"id": "session-a"}
    integration = SelfEvolutionChatIntegration(host=None, session_provider=lambda: current["id"], emit=emitted.append)
    integration._trusted_production_host = lambda: object()
    first_identity = EvolutionIdentity("session-a", "shared-task", "shared-attempt", "run-a", 1)
    first_event = EvolutionEvent(first_identity, 1, "editing", "phase_changed", (), 2)
    assert integration.deliver_update(first_event, EvolutionSnapshot(first_identity, "observing", 1)) is True

    current["id"] = "session-b"
    second_identity = EvolutionIdentity("session-b", "shared-task", "shared-attempt", "run-b", 1)
    second_event = EvolutionEvent(second_identity, 1, "editing", "phase_changed", (), 2)
    assert integration.deliver_update(second_event, EvolutionSnapshot(second_identity, "observing", 1)) is True
    assert [event["session_id"] for event in emitted] == ["session-a", "session-b"]


def test_failed_transport_does_not_consume_event_watermarks() -> None:
    attempts = {"fail": True}
    delivered = []

    def emit(payload):
        if attempts["fail"]:
            raise RuntimeError("transport closed")
        delivered.append(payload)

    integration = SelfEvolutionChatIntegration(host=None, session_provider=lambda: "session-a", emit=emit)
    integration._trusted_production_host = lambda: object()
    identity = EvolutionIdentity("session-a", "task-a", "attempt-a", "run-a", 1)
    snapshot = EvolutionSnapshot(identity, "observing", 1)
    event = EvolutionEvent(identity, 1, "editing", "phase_changed", (), 2)
    assert integration.deliver_update(event, snapshot) is False
    attempts["fail"] = False
    assert integration.deliver_update(event, snapshot) is True
    assert delivered[0]["sequence"] == 1


def test_event_update_requires_exact_snapshot_identity() -> None:
    integration = SelfEvolutionChatIntegration(host=None, session_provider=lambda: "session-a", emit=lambda _: None)
    integration._trusted_production_host = lambda: object()
    identity = EvolutionIdentity("session-a", "task-a", "attempt-a", "run-a", 1)
    wrong_snapshot = EvolutionSnapshot(EvolutionIdentity("session-a", "task-a", "attempt-a", "other-run", 1), "observing", 1)
    event = EvolutionEvent(identity, 1, "editing", "phase_changed", (), 2)
    assert integration.deliver_update(event, wrong_snapshot) is False


def test_agent_snapshot_delivery_uses_backend_injected_chat_integration() -> None:
    from agent import Agent

    agent = Agent.__new__(Agent)
    calls = []
    agent.self_evolution_chat = type("Integration", (), {
        "deliver_snapshot": lambda _self, task_id: calls.append(task_id) or True,
    })()

    assert agent.deliver_self_evolution_snapshot("task-a") is True
    assert calls == ["task-a"]


def test_agent_snapshot_delivery_fails_closed_without_integration() -> None:
    from agent import Agent

    agent = Agent.__new__(Agent)
    agent.self_evolution_chat = None
    assert agent.deliver_self_evolution_snapshot("task-a") is False


def test_snapshot_event_reader_failure_fails_closed_without_emitting() -> None:
    from self_evolution_types import EvolutionIdentity, EvolutionSnapshot

    emitted = []
    snapshot = EvolutionSnapshot(EvolutionIdentity("session-a", "task-a", "attempt-a", "run-a", 1), "observing", 1)
    integration = SelfEvolutionChatIntegration(
        host=None, session_provider=lambda: "session-a", emit=emitted.append,
        snapshot_provider=lambda _task_id: snapshot,
        event_provider=lambda _snapshot: (_ for _ in ()).throw(RuntimeError("store unavailable")),
    )
    integration._trusted_production_host = lambda: object()

    assert integration.deliver_snapshot("task-a") is False
    assert emitted == []


def test_update_requires_a_production_ready_host_even_for_typed_events() -> None:
    emitted = []
    integration = SelfEvolutionChatIntegration(host=None, session_provider=lambda: "session-a", emit=emitted.append)
    identity = EvolutionIdentity("session-a", "task-a", "attempt-a", "run-a", 1)
    event = EvolutionEvent(identity, 1, "editing", "phase_changed", (), 2)
    snapshot = EvolutionSnapshot(identity, "observing", 1)

    assert integration.deliver_update(event, snapshot) is False
    assert emitted == []


def test_snapshot_delivery_restores_latest_persisted_watermark_and_subscribes_task() -> None:
    from self_evolution_types import EvolutionEvent, EvolutionIdentity, EvolutionSnapshot

    emitted = []
    identity = EvolutionIdentity("session-a", "task-a", "attempt-a", "run-a", 1)
    snapshot = EvolutionSnapshot(identity, "observing", 3)
    events = (
        EvolutionEvent(identity, 1, "preparing", "task_started", ("task_accepted",), 1),
        EvolutionEvent(identity, 2, "observing", "phase_changed", ("observation_recorded",), 2, ("observation-1",)),
        EvolutionEvent(identity, 3, "observing", "phase_changed", (), 3),
    )
    integration = SelfEvolutionChatIntegration(
        host=None, session_provider=lambda: "session-a", emit=emitted.append,
        snapshot_provider=lambda task_id: snapshot if task_id == "task-a" else None,
        event_provider=lambda _snapshot: events,
    )
    integration._trusted_production_host = lambda: object()

    assert integration.deliver_snapshot("task-a") is True
    assert emitted[0]["type"] == "self_evolution.snapshot"
    assert emitted[0]["sequence"] == 3
    assert integration.subscribed_task_ids() == ("task-a",)


def test_poll_delivers_only_new_persisted_events_for_the_same_session_attempt() -> None:
    from self_evolution_types import EvolutionEvent, EvolutionIdentity, EvolutionSnapshot

    emitted = []
    current = {"session": "session-a"}
    identity = EvolutionIdentity("session-a", "task-a", "attempt-a", "run-a", 1)
    state = {"snapshot": EvolutionSnapshot(identity, "observing", 2)}
    rows = [EvolutionEvent(identity, 1, "observing", "phase_changed", (), 2)]
    integration = SelfEvolutionChatIntegration(
        host=None, session_provider=lambda: current["session"], emit=emitted.append,
        snapshot_provider=lambda _task: state["snapshot"],
        event_provider=lambda _snapshot: tuple(rows),
    )
    integration._trusted_production_host = lambda: object()
    assert integration.deliver_snapshot("task-a") is True
    emitted.clear()

    rows.append(EvolutionEvent(identity, 2, "editing", "phase_changed", ("edit_started",), 3, ("change-1",)))
    state["snapshot"] = EvolutionSnapshot(identity, "editing", 3)
    assert integration.poll_updates() == 1
    assert [item["sequence"] for item in emitted] == [2]
    assert emitted[0]["phase"] == "editing"

    current["session"] = "session-b"
    rows.append(EvolutionEvent(identity, 3, "checking", "phase_changed", (), 4))
    state["snapshot"] = EvolutionSnapshot(identity, "checking", 4)
    assert integration.poll_updates() == 0
    assert [item["session_id"] for item in emitted] == ["session-a"]


def test_snapshot_recovery_rejects_old_attempt_and_foreign_session_rows() -> None:
    from self_evolution_types import EvolutionEvent, EvolutionIdentity, EvolutionSnapshot

    emitted = []
    identity = EvolutionIdentity("session-a", "task-a", "attempt-new", "run-new", 2)
    snapshot = EvolutionSnapshot(identity, "editing", 8)
    rows = (
        EvolutionEvent(EvolutionIdentity("session-a", "task-a", "attempt-old", "run-old", 1),
                       99, "checking", "phase_changed", (), 99),
        EvolutionEvent(EvolutionIdentity("session-b", "task-a", "attempt-new", "run-new", 2),
                       100, "checking", "phase_changed", (), 100),
    )
    integration = SelfEvolutionChatIntegration(
        host=None, session_provider=lambda: "session-a", emit=emitted.append,
        snapshot_provider=lambda _task: snapshot,
        event_provider=lambda _snapshot: rows,
    )
    integration._trusted_production_host = lambda: object()

    assert integration.deliver_snapshot("task-a") is False
    assert emitted == []
    assert integration.poll_updates() == 0


def test_snapshot_task_lookup_cannot_return_another_task_from_the_same_session() -> None:
    from self_evolution_types import EvolutionIdentity, EvolutionSnapshot

    emitted = []
    foreign_task = EvolutionSnapshot(EvolutionIdentity("session-a", "different-task", "attempt-a", "run-a", 1), "observing", 1)
    integration = SelfEvolutionChatIntegration(
        host=None, session_provider=lambda: "session-a", emit=emitted.append,
        snapshot_provider=lambda _task: foreign_task,
    )
    integration._trusted_production_host = lambda: object()

    assert integration.deliver_snapshot("requested-task") is False
    assert emitted == []
    assert integration.subscribed_task_ids() == ()


def test_server_event_reader_uses_only_exact_host_store_and_snapshot_identity() -> None:
    from server import _self_evolution_host_events
    from self_evolution_host import SelfEvolutionHost
    from self_evolution_store import EventPage
    from self_evolution_types import EvolutionEvent, EvolutionIdentity, EvolutionSnapshot

    identity = EvolutionIdentity("session-a", "task-a", "attempt-a", "run-a", 1)
    snapshot = EvolutionSnapshot(identity, "observing", 2)
    row = {
        "session_id": "session-a", "task_id": "task-a", "attempt_id": "attempt-a",
        "run_id": "run-a", "turn_epoch": 1, "sequence": 1, "phase": "observing",
        "kind": "phase_changed", "fact_codes": [], "snapshot_revision": 2, "evidence_refs": [],
    }
    calls = []
    host = SelfEvolutionHost.__new__(SelfEvolutionHost)
    host._store = object()
    host._worker_capabilities = object()
    host.get_event_page = lambda task_id, attempt_id, *, after_sequence=0, session_id=None: (
        calls.append((task_id, attempt_id, after_sequence, session_id))
        or EventPage((row,), 1, 1, 1, False, False)
    )

    page = _self_evolution_host_events(host, snapshot)
    assert type(page) is EventPage and len(page.events) == 1
    assert type(page.events[0]) is EvolutionEvent and page.events[0].identity == identity
    assert calls == [("task-a", "attempt-a", 0, "session-a")]

    assert _self_evolution_host_events(object(), snapshot) is None


def test_snapshot_response_echoes_request_and_marks_history_not_replayed() -> None:
    emitted = []
    identity = EvolutionIdentity("session-a", "task-a", "attempt-a", "run-a", 1)
    snapshot = EvolutionSnapshot(identity, "editing", 3)
    rows = (EvolutionEvent(identity, 2, "editing", "phase_changed", ("edit_started",), 3, ("ref-a",)),)
    integration = SelfEvolutionChatIntegration(host=None, session_provider=lambda: "session-a", emit=emitted.append,
        snapshot_provider=lambda _: snapshot, event_provider=lambda _: rows)
    integration._trusted_production_host = lambda: object()
    assert integration.deliver_snapshot("task-a", request_id="c4053752-c169-4dd0-a03d-f27f706d02c1") is True
    assert emitted[0]["request_id"] == "c4053752-c169-4dd0-a03d-f27f706d02c1"
    assert emitted[0]["history_status"] == "not_restored"
    assert emitted[0]["sequence"] == 2
    assert len(emitted) == 1


def test_poll_gap_requests_resync_instead_of_emitting_uncorrelated_snapshot() -> None:
    emitted = []
    identity = EvolutionIdentity("session-a", "task-a", "attempt-a", "run-a", 1)
    state = {"snapshot": EvolutionSnapshot(identity, "observing", 1), "rows": ()}
    integration = SelfEvolutionChatIntegration(host=None, session_provider=lambda: "session-a", emit=emitted.append,
        snapshot_provider=lambda _: state["snapshot"], event_provider=lambda _: state["rows"])
    integration._trusted_production_host = lambda: object()
    assert integration.deliver_snapshot("task-a", request_id="c4053752-c169-4dd0-a03d-f27f706d02c1")
    emitted.clear()
    state.update(snapshot=EvolutionSnapshot(identity, "editing", 8), rows=(
        EvolutionEvent(identity, 8, "editing", "phase_changed", (), 8),))
    assert integration.poll_updates() == 0
    assert emitted == [{"type": "self_evolution.resync", "session_id": "session-a", "task_id": "task-a"}]


def test_ws_snapshot_request_propagates_request_id_from_real_message_handler() -> None:
    import asyncio
    from types import SimpleNamespace
    from server import WsSessionHandler

    calls, emitted = [], []
    handler = WsSessionHandler.__new__(WsSessionHandler)
    handler._dispatch_inline = lambda *_args: False
    agent = SimpleNamespace(deliver_self_evolution_snapshot=lambda task, *, request_id: calls.append((task, request_id)) or True)
    repl = SimpleNamespace(agent=agent, session=SimpleNamespace(conversation_id="session-a"))
    bridge = SimpleNamespace(emit=emitted.append)
    request_id = "c4053752-c169-4dd0-a03d-f27f706d02c1"
    asyncio.run(handler._handle_incoming({"type": "self_evolution.snapshot.get", "task_id": "task-a", "request_id": request_id}, repl, bridge))
    assert calls == [("task-a", request_id)]
    assert emitted == []



def test_snapshot_anchors_at_page_watermark_without_replaying_retained_history() -> None:
    from self_evolution_store import EventPage

    emitted = []
    identity = EvolutionIdentity("session-a", "task-a", "attempt-a", "run-a", 1)
    snapshot = EvolutionSnapshot(identity, "editing", 40)
    old = EvolutionEvent(identity, 38, "observing", "phase_changed", (), 38)
    middle = EvolutionEvent(identity, 39, "checking", "phase_changed", (), 39)
    recent = EvolutionEvent(identity, 40, "editing", "phase_changed", (), 40)
    page = EventPage((old, middle, recent), 38, 40, 40, True, True)
    integration = SelfEvolutionChatIntegration(host=None, session_provider=lambda: "session-a", emit=emitted.append,
        snapshot_provider=lambda _: snapshot, event_provider=lambda *_args, **_kwargs: page)
    integration._trusted_production_host = lambda: object()

    assert integration.deliver_snapshot("task-a") is True
    assert emitted[0]["type"] == "self_evolution.snapshot"
    assert emitted[0]["sequence"] == 40
    assert emitted[0]["history_status"] == "not_restored"
    assert len(emitted) == 1


def test_snapshot_rejects_internal_gap_or_watermark_mismatch_and_requests_resync() -> None:
    from self_evolution_store import EventPage

    emitted = []
    identity = EvolutionIdentity("session-a", "task-a", "attempt-a", "run-a", 1)
    snapshot = EvolutionSnapshot(identity, "editing", 8)
    page = EventPage((), 1, 8, 9, False, True)
    integration = SelfEvolutionChatIntegration(host=None, session_provider=lambda: "session-a", emit=emitted.append,
        snapshot_provider=lambda _: snapshot, event_provider=lambda *_args, **_kwargs: page)
    integration._trusted_production_host = lambda: object()

    assert integration.deliver_snapshot("task-a") is False
    assert emitted == [{"type": "self_evolution.resync", "session_id": "session-a", "task_id": "task-a"}]
    assert integration.subscribed_task_ids() == ()


def test_poll_uses_cursor_page_and_resyncs_on_page_gap_without_emitting_events() -> None:
    from self_evolution_store import EventPage

    emitted = []
    identity = EvolutionIdentity("session-a", "task-a", "attempt-a", "run-a", 1)
    snapshot = EvolutionSnapshot(identity, "editing", 8)
    seen = []
    first = EvolutionEvent(identity, 1, "observing", "phase_changed", (), 2)
    missing = EvolutionEvent(identity, 3, "editing", "phase_changed", (), 8)
    baseline = EventPage((first,), 1, 1, 1, False, False)
    page = EventPage((missing,), 1, 3, 3, False, True)
    integration = SelfEvolutionChatIntegration(host=None, session_provider=lambda: "session-a", emit=emitted.append,
        snapshot_provider=lambda _: snapshot,
        event_provider=lambda _snapshot, *, after_sequence=0: (
            seen.append(after_sequence) or (baseline if after_sequence == 0 and not seen[:-1] else page)))
    integration._trusted_production_host = lambda: object()
    assert integration.deliver_snapshot("task-a") is True
    emitted.clear()

    assert integration.poll_updates() == 0
    assert seen == [0, 1]
    assert emitted == [{"type": "self_evolution.resync", "session_id": "session-a", "task_id": "task-a"}]



def test_snapshot_rejects_pages_over_the_transport_bound_and_requests_resync() -> None:
    from self_evolution_store import EventPage

    emitted = []
    identity = EvolutionIdentity("session-a", "task-a", "attempt-a", "run-a", 1)
    events = tuple(EvolutionEvent(identity, seq, "observing", "phase_changed", (), seq)
                   for seq in range(1, 258))
    snapshot = EvolutionSnapshot(identity, "observing", 257)
    page = EventPage(events, 1, 257, 257, False, False)
    integration = SelfEvolutionChatIntegration(host=None, session_provider=lambda: "session-a", emit=emitted.append,
        snapshot_provider=lambda _: snapshot, event_provider=lambda *_args, **_kwargs: page)
    integration._trusted_production_host = lambda: object()

    assert integration.deliver_snapshot("task-a") is False
    assert emitted == [{"type": "self_evolution.resync", "session_id": "session-a", "task_id": "task-a"}]


def test_real_store_host_event_pages_deliver_incremental_batches_without_replay(tmp_path) -> None:
    import threading
    from server import _self_evolution_host_events
    from self_evolution_host import HostRunContext, SelfEvolutionHost
    from server import _self_evolution_host_events
    from tests.test_self_evolution_store import _store

    identity = EvolutionIdentity("session-a", "task-a", "attempt-a", "run-a", 1)
    store = _store(tmp_path / "events.sqlite3")
    store.create_task(identity, EvolutionSnapshot(identity, "preparing", 1))
    assert store.append_event(EvolutionEvent(identity, 1, "preparing", "task_started", (), 1))
    host = SelfEvolutionHost.__new__(SelfEvolutionHost)
    host._store = store
    host._worker_capabilities = object()
    host._startup_reconciliation_error = None
    host._context_provider = lambda: HostRunContext(
        "session-a", "run-a", 1, "agent", "work", threading.Event(), "req-a", "human")
    emitted = []
    cursors = []

    def read_page(snapshot, *, after_sequence=0):
        cursors.append(after_sequence)
        return _self_evolution_host_events(host, snapshot, after_sequence=after_sequence)

    integration = SelfEvolutionChatIntegration(
        host=host, session_provider=lambda: "session-a", emit=emitted.append,
        snapshot_provider=lambda task: store.read_task("session-a", task), event_provider=read_page)
    # Production readiness remains disabled; exercise only this isolated delivery boundary.
    integration._trusted_production_host = lambda: host
    assert integration.deliver_snapshot("task-a")
    assert emitted[0]["sequence"] == 1
    emitted.clear()

    for seq, phase in ((2, "observing"), (3, "editing"), (4, "checking")):
        store.compare_and_swap("task-a", seq - 1, EvolutionSnapshot(identity, phase, seq))
        assert store.append_event(EvolutionEvent(identity, seq, phase, "phase_changed", (), seq))
        assert integration.poll_updates() == 1
        assert integration.poll_updates() == 0

    assert [event["sequence"] for event in emitted] == [2, 3, 4]
    assert all(event["type"] != "self_evolution.resync" for event in emitted)
    assert cursors == [0, 1, 2, 2, 3, 3, 4]


def test_page_rejects_missing_first_requested_event_even_if_gap_flag_is_false() -> None:
    from self_evolution_store import EventPage

    identity = EvolutionIdentity("session-a", "task-a", "attempt-a", "run-a", 1)
    event = EvolutionEvent(identity, 3, "editing", "phase_changed", (), 3)
    # The metadata claims no gap, but sequence 2 after cursor 1 is absent.
    page = EventPage((event,), 1, 3, 3, False, False)
    assert not SelfEvolutionChatIntegration._valid_event_page(
        page, after_sequence=1, allow_retention_gap=False)

    emitted = []
    initial = EvolutionEvent(identity, 1, "observing", "phase_changed", (), 1)
    baseline = EventPage((initial,), 1, 1, 1, False, False)
    state = {"page": baseline, "snapshot": EvolutionSnapshot(identity, "observing", 1)}
    integration = SelfEvolutionChatIntegration(
        host=None, session_provider=lambda: "session-a", emit=emitted.append,
        snapshot_provider=lambda _task: state["snapshot"],
        event_provider=lambda _snapshot, *, after_sequence=0: state["page"])
    integration._trusted_production_host = lambda: object()
    assert integration.deliver_snapshot("task-a")
    emitted.clear()
    state.update(page=page, snapshot=EvolutionSnapshot(identity, "editing", 3))
    assert integration.poll_updates() == 0
    assert integration.poll_updates() == 0
    assert emitted == [{"type": "self_evolution.resync", "session_id": "session-a", "task_id": "task-a"}]


def test_snapshot_emit_can_reenter_subscription_lookup_with_real_store_and_host(tmp_path) -> None:
    import threading
    from self_evolution_host import HostRunContext, SelfEvolutionHost
    from server import _self_evolution_host_events
    from tests.test_self_evolution_store import _store

    identity = EvolutionIdentity("session-a", "task-a", "attempt-a", "run-a", 1)
    store = _store(tmp_path / "reentrant-events.sqlite3")
    store.create_task(identity, EvolutionSnapshot(identity, "preparing", 1))
    host = SelfEvolutionHost.__new__(SelfEvolutionHost)
    host._store = store
    host._worker_capabilities = object()
    host._startup_reconciliation_error = None
    host._context_provider = lambda: HostRunContext(
        "session-a", "run-a", 1, "agent", "work", threading.Event(), "req-a", "human")
    emitted = []
    completed = threading.Event()
    integration = None

    def emit(payload):
        emitted.append(payload)
        integration.subscribed_task_ids()

    integration = SelfEvolutionChatIntegration(
        host=host, session_provider=lambda: "session-a", emit=emit,
        snapshot_provider=lambda task: store.read_task("session-a", task),
        event_provider=lambda snapshot, *, after_sequence=0: _self_evolution_host_events(
            host, snapshot, after_sequence=after_sequence),
    )
    integration._trusted_production_host = lambda: host

    result = []
    def deliver():
        result.append(integration.deliver_snapshot("task-a"))
        completed.set()

    worker = threading.Thread(target=deliver, daemon=True)
    worker.start()
    assert completed.wait(1), "snapshot delivery deadlocked when emit re-entered subscription lookup"
    assert result == [True]
    assert len(emitted) == 1


def test_turn_context_provider_revokes_after_scope_and_is_thread_local():
    import threading
    from self_evolution_host import HostRunContext, HostRunContextProvider

    provider = HostRunContextProvider()
    alive = {"value": True}
    context = HostRunContext("session-a", "run-a", 1, "agent", "execute", threading.Event(),
                             "00000000-0000-4000-8000-000000000001", "human", self_evolution_intent=True)
    assert provider() is None
    from contextvars import copy_context
    with provider.bind(context, lambda: alive["value"]):
        assert provider() == context
        copied = copy_context()
        result = []
        worker = threading.Thread(target=lambda: result.append(provider()))
        worker.start(); worker.join()
        assert result == [None]
        alive["value"] = False
        assert provider() is None
    assert provider() is None
    alive["value"] = True
    assert copied.run(provider) is None  # copied async context cannot resurrect a closed lease


def test_real_user_message_path_uses_trusted_detector_confirmation_and_turn_scope(monkeypatch):
    import asyncio
    import uuid
    from server import WsBridge, WsSessionHandler, _build_repl, SelfEvolutionChatIntegration
    from session import create_new
    from tests.isolation_helpers import temporary_agent_paths
    from self_evolution_host import HostRunContextProvider

    with temporary_agent_paths() as paths:
        session = create_new(paths)
        session.set_turn_mode("agent")
        events = []
        bridge = WsBridge(emit=events.append, paths=paths)
        repl = _build_repl(session, paths, bridge)
        provider = HostRunContextProvider()
        seen = []
        requests = []
        from tests.test_self_evolution_store import _store as make_store
        persistent_store = make_store(paths.data / "confirmed-start.sqlite3")
        class Host:
            worker_bound = True
            _context_provider = provider
            _startup_reconciliation_error = None
            _store = persistent_store
            def start(self, request, *, start_request_claim, **_kwargs):
                assert start_request_claim.claim_token
                seen.append(provider())
                requests.append(request)
                return {"status": "started", "task_id": "fixture"}
        host = Host()
        chat = SelfEvolutionChatIntegration(host=host, session_provider=lambda: repl.session, emit=events.append)
        monkeypatch.setattr(chat, "_trusted_production_host", lambda: host)
        bridge.self_evolution_chat = chat
        repl.agent.self_evolution_chat = chat
        handler = WsSessionHandler(paths, self_evolution_context_provider=provider,
        self_evolution_intent_detector=lambda raw: EvolutionRequest(raw) if raw == "\u8bf7\u8c03\u6574\u81ea\u5df1\u7684\u8bbe\u7f6e\u754c\u9762" else None)
        request_id = str(uuid.uuid4())
        msg = {"type": "user.message", "text": "\u8bf7\u8c03\u6574\u81ea\u5df1\u7684\u8bbe\u7f6e\u754c\u9762", "session_id": session.conversation_id, "request_id": request_id}
        async def approved_turn():
            task = asyncio.create_task(handler._dispatch(msg, repl, bridge))
            for _ in range(500):
                pending = next((event for event in events if event.get("type") == "confirm.request"), None)
                if pending:
                    assert pending["session_id"] == session.conversation_id
                    assert msg["text"] in pending["preview"]
                    assert bridge.deliver_confirm(pending["request_id"], "y")
                    break
                await asyncio.sleep(.01)
            else:
                raise AssertionError("confirmation was not requested")
            await asyncio.wait_for(task, 8)
        asyncio.run(approved_turn())
        assert len(requests) == 1
        assert seen[0].conversation_id == session.conversation_id
        assert seen[0].request_origin == "human" and seen[0].self_evolution_intent is True
        assert seen[0].mode == "agent" and seen[0].work_intent == "execute"
        assert provider() is None
        assert session.messages[-2]["content"] == msg["text"]
        asyncio.run(handler._dispatch(msg, repl, bridge))
        assert len(requests) == 1


def test_real_confirmation_rejection_and_cancellation_never_start(monkeypatch):
    import asyncio
    import uuid
    from server import WsBridge, WsSessionHandler, _build_repl
    from session import create_new
    from tests.isolation_helpers import temporary_agent_paths
    from self_evolution_host import HostRunContextProvider

    for outcome in ("n", "cancel", "switch", "ask"):
        with temporary_agent_paths() as paths:
            session = create_new(paths)
            session.set_turn_mode("agent")
            events = []
            bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=2)
            repl = _build_repl(session, paths, bridge)
            provider = HostRunContextProvider()
            starts = []
            class Host:
                def start(self, request, **_kwargs):
                    starts.append((request, provider()))
                    return {"status": "started"}
            host = Host()
            chat = SelfEvolutionChatIntegration(host=host, session_provider=lambda: repl.session, emit=events.append)
            monkeypatch.setattr(chat, "_trusted_production_host", lambda: host)
            bridge.self_evolution_chat = chat
            repl.agent.self_evolution_chat = chat
            handler = WsSessionHandler(paths, self_evolution_context_provider=provider,
                self_evolution_intent_detector=lambda raw: EvolutionRequest(raw))
            msg = {"type": "user.message", "text": "\u8bf7\u8c03\u6574\u81ea\u5df1\u7684\u8bbe\u7f6e\u754c\u9762", "session_id": session.conversation_id,
                   "request_id": str(uuid.uuid4())}
            if outcome == "ask":
                # Change mode after recognition but before confirmation returns.
                def ask_during_confirmation(_preview, _allow):
                    session.set_turn_mode("ask")
                    return "y", str(uuid.uuid4())
                monkeypatch.setattr(bridge, "confirm_with_receipt", ask_during_confirmation)
            async def run():
                task = asyncio.create_task(handler._dispatch(msg, repl, bridge))
                if outcome != "ask":
                    for _ in range(500):
                        event = next((e for e in events if e.get("type") == "confirm.request"), None)
                        if event:
                            assert event["session_id"] == session.conversation_id
                            assert msg["text"] in event["preview"]
                            if outcome == "cancel":
                                assert bridge.request_cancel()
                            elif outcome == "switch":
                                repl.session = create_new(paths)
                                assert not bridge.deliver_confirm(event["request_id"], "y")
                            else:
                                assert bridge.deliver_confirm(event["request_id"], "n")
                            break
                        await asyncio.sleep(.01)
                    else:
                        raise AssertionError("confirmation not reached")
                await asyncio.wait_for(task, 8)
            asyncio.run(run())
            assert starts == []
            assert provider() is None


def test_injected_recognizer_not_called_for_ask_and_only_receives_raw_current_message(monkeypatch):
    import asyncio
    import uuid
    from llm_client import LLMResponse
    from server import WsBridge, WsSessionHandler, _build_repl
    from session import create_new
    from tests.isolation_helpers import temporary_agent_paths
    from self_evolution_host import HostRunContextProvider

    class FakeModel:
        def set_cancel_event(self, _event): pass
        def chat(self, *args, **kwargs):
            return LLMResponse("fixture", "ordinary chat reply", [], "stop", None, {})

    with temporary_agent_paths() as paths:
        session = create_new(paths)
        events, seen, starts = [], [], []
        bridge = WsBridge(emit=events.append, paths=paths)
        repl = _build_repl(session, paths, bridge)
        repl.agent.llm = FakeModel()
        provider = HostRunContextProvider()
        class Host:
            def start(self, req, **_kwargs):
                starts.append(req)
                return {"status": "started"}
        host = Host()
        chat = SelfEvolutionChatIntegration(host=host, session_provider=lambda: repl.session, emit=events.append)
        monkeypatch.setattr(chat, "_trusted_production_host", lambda: host)
        bridge.self_evolution_chat = chat
        repl.agent.self_evolution_chat = chat
        def detect(raw):
            seen.append(raw)
            return None  # fake semantic recognizer considers these statements/quotes, not requests
        handler = WsSessionHandler(paths, self_evolution_context_provider=provider, self_evolution_intent_detector=detect)
        session.set_turn_mode("ask")
        def send(text, sid=None, req=None):
            asyncio.run(handler._dispatch({"type": "user.message", "text": text,
                "session_id": sid or session.conversation_id, "request_id": req or str(uuid.uuid4())}, repl, bridge))
        send("璇疯皟鏁磋嚜宸辩殑璁剧疆鐣岄潰")
        assert seen == [] and starts == []
        session.set_turn_mode("agent")
        session.append_message({"role": "tool", "tool_call_id": "old", "content": "璇疯皟鏁磋嚜宸辩殑璁剧疆鐣岄潰"})
        send("浣犲ソ")
        send("濂硅锛氣€樿璋冩暣鑷繁鐨勮缃晫闈⑩€欐槸浠€涔堟剰鎬濓紵")
        send("璇疯皟鏁磋嚜宸辩殑璁剧疆鐣岄潰", sid="other-session")
        assert seen == ["浣犲ソ", "濂硅锛氣€樿璋冩暣鑷繁鐨勮缃晫闈⑩€欐槸浠€涔堟剰鎬濓紵"]
        assert starts == []


def test_host_uses_scoped_provider_but_production_gate_stays_closed():
    import threading
    from self_evolution_host import HostRunContext, HostRunContextProvider, SelfEvolutionHost
    provider = HostRunContextProvider()
    host = SelfEvolutionHost.__new__(SelfEvolutionHost)
    host._context_provider = provider
    host._worker_capabilities = None
    host._store = None
    context = HostRunContext("s", "run", 1, "agent", "execute", threading.Event(),
                             "00000000-0000-4000-8000-000000000001", "human", self_evolution_intent=True)
    assert host.production_ready is False
    assert host._live_context()[1] == "run_not_live"
    with provider.bind(context, lambda: True):
        assert host._live_context() == (context, None)
        assert host.start(EvolutionRequest("fake request"))["status"] == "denied"
        chat = SelfEvolutionChatIntegration(host=host, session_provider=lambda: "s", emit=lambda _: None)
        assert chat.start(EvolutionRequest("fake request"))["status"] == "unavailable"
    assert host._live_context()[1] == "run_not_live"


def test_ask_request_id_does_not_reserve_later_self_evolution_candidate(monkeypatch):
    import asyncio
    import uuid
    from llm_client import LLMResponse
    from server import WsBridge, WsSessionHandler, _build_repl
    from session import create_new
    from tests.isolation_helpers import temporary_agent_paths
    from self_evolution_host import HostRunContextProvider
    class FakeModel:
        def set_cancel_event(self, _event): pass
        def chat(self, *args, **kwargs):
            return LLMResponse("fixture", "read only", [], "stop", None, {})
    with temporary_agent_paths() as paths:
        session = create_new(paths)
        bridge = WsBridge(emit=lambda _: None, paths=paths)
        repl = _build_repl(session, paths, bridge)
        repl.agent.llm = FakeModel()
        provider = HostRunContextProvider()
        starts = []
        from tests.test_self_evolution_store import _store as make_store
        persistent_store = make_store(paths.data / "ask-start.sqlite3")
        class Host:
            worker_bound = True
            _context_provider = provider
            _startup_reconciliation_error = None
            _store = persistent_store
            def start(self, request, *, start_request_claim, **_kwargs):
                assert start_request_claim.claim_token
                starts.append(request)
                return {"status": "started"}
        host = Host()
        chat = SelfEvolutionChatIntegration(host=host, session_provider=lambda: repl.session, emit=lambda _: None)
        monkeypatch.setattr(chat, "_trusted_production_host", lambda: host)
        bridge.self_evolution_chat = chat
        handler = WsSessionHandler(paths, self_evolution_context_provider=provider,
                                   self_evolution_intent_detector=lambda raw: EvolutionRequest(raw))
        monkeypatch.setattr(bridge, "confirm_with_receipt", lambda *_: ("y", str(uuid.uuid4())))
        msg = {"type": "user.message", "text": "adjust my settings", "session_id": session.conversation_id,
               "request_id": str(uuid.uuid4())}
        session.set_turn_mode("ask")
        asyncio.run(handler._dispatch(msg, repl, bridge))
        assert starts == []
        session.set_turn_mode("agent")
        asyncio.run(handler._dispatch(msg, repl, bridge))
        assert len(starts) == 1  # A new candidate still needs the exact confirmation.


def test_renderer_turn_mode_field_cannot_upgrade_ask_for_self_evolution(monkeypatch):
    import asyncio
    import uuid
    from llm_client import LLMResponse
    from server import WsBridge, WsSessionHandler, _build_repl
    from session import create_new
    from tests.isolation_helpers import temporary_agent_paths
    from self_evolution_host import HostRunContextProvider
    class FakeModel:
        def set_cancel_event(self, _event): pass
        def chat(self, *args, **kwargs):
            return LLMResponse("fixture", "ordinary", [], "stop", None, {})
    with temporary_agent_paths() as paths:
        session = create_new(paths)
        session.set_turn_mode("ask")
        bridge = WsBridge(emit=lambda _: None, paths=paths)
        repl = _build_repl(session, paths, bridge)
        repl.agent.llm = FakeModel()
        starts, detected = [], []
        class Host:
            def start(self, request, **_kwargs):
                starts.append(request)
                return {"status": "started"}
        host = Host()
        chat = SelfEvolutionChatIntegration(host=host, session_provider=lambda: repl.session, emit=lambda _: None)
        monkeypatch.setattr(chat, "_trusted_production_host", lambda: host)
        bridge.self_evolution_chat = chat
        def detect(raw):
            detected.append(raw)
            return EvolutionRequest(raw)
        handler = WsSessionHandler(paths, self_evolution_context_provider=HostRunContextProvider(),
                                   self_evolution_intent_detector=detect)
        monkeypatch.setattr(bridge, "confirm_with_receipt", lambda *_: ("y", str(uuid.uuid4())))
        asyncio.run(handler._dispatch({"type": "user.message", "text": "fix my settings", "turn_mode": "agent",
            "session_id": session.conversation_id, "request_id": str(uuid.uuid4())}, repl, bridge))
        assert not starts and not detected


def test_ordinary_same_request_id_is_not_reserved_by_self_evolution(monkeypatch):
    import asyncio
    import uuid
    from llm_client import LLMResponse
    from server import WsBridge, WsSessionHandler, _build_repl
    from session import create_new
    from tests.isolation_helpers import temporary_agent_paths
    from self_evolution_host import HostRunContextProvider
    class Model:
        calls = 0
        def set_cancel_event(self, _event): pass
        def chat(self, *args, **kwargs):
            self.calls += 1
            return LLMResponse("fixture", "hello", [], "stop", None, {})
    with temporary_agent_paths() as paths:
        session = create_new(paths)
        bridge = WsBridge(emit=lambda _: None, paths=paths)
        repl = _build_repl(session, paths, bridge)
        model = Model()
        repl.agent.llm = model
        bridge.self_evolution_chat = SelfEvolutionChatIntegration(host=None, session_provider=lambda: session,
                                                                  emit=lambda _: None)
        handler = WsSessionHandler(paths, self_evolution_context_provider=HostRunContextProvider(),
                                   self_evolution_intent_detector=lambda _: None)
        for mode in ("ask", "agent"):
            session.set_turn_mode(mode)
            msg = {"type": "user.message", "text": "hello", "session_id": session.conversation_id,
                   "request_id": str(uuid.uuid4())}
            before = model.calls
            asyncio.run(handler._dispatch(msg, repl, bridge))
            asyncio.run(handler._dispatch(msg, repl, bridge))
            assert model.calls == before + 2
        assert not handler._self_evolution_seen


def test_self_evolution_candidate_reservation_is_atomic_under_concurrency():
    import threading
    import uuid
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from server import WsSessionHandler
    from self_evolution_host import HostRunContextProvider
    from tests.isolation_helpers import temporary_agent_paths
    with temporary_agent_paths() as paths:
        barrier = threading.Barrier(2)
        raw = "Update the app settings"
        session = SimpleNamespace(conversation_id="session-a", meta=SimpleNamespace(turn_mode="agent"),
            messages=[{"role": "user", "content": raw}], _messages_fully_loaded=True,
            _messages_corrupt=False, _messages_unpersisted=False)
        agent = SimpleNamespace(session=session)
        repl = SimpleNamespace(session=session, agent=agent)
        run = SimpleNamespace(run_id="run-a", sequence=1, state="running", cancel_requested=False)
        provider = HostRunContextProvider()
        starts = []
        class Integration:
            def start(self, request, **_kwargs):
                starts.append((request, provider()))
                return {"status": "started"}
        busy, closed, cancelled = threading.Event(), threading.Event(), threading.Event()
        busy.set()
        bridge = SimpleNamespace(self_evolution_chat=Integration(), lifecycle=SimpleNamespace(snapshot=lambda: run),
            lifecycle_conversation_id="session-a", _turn_busy=busy, _connection_closed=closed,
            cancel_event=cancelled, confirm_with_receipt=lambda *_: ("y", str(uuid.uuid4())))
        def detect(text):
            barrier.wait(timeout=5)
            return EvolutionRequest(text)
        handler = WsSessionHandler(paths, self_evolution_context_provider=provider, self_evolution_intent_detector=detect)
        request_id = str(uuid.uuid4())
        one = handler._make_self_evolution_turn_dispatch(repl, bridge, session, agent, run, raw, request_id, "session-a", "agent")
        two = handler._make_self_evolution_turn_dispatch(repl, bridge, session, agent, run, raw, request_id, "session-a", "agent")
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(one, session, raw, 0)
            f2 = pool.submit(two, session, raw, 0)
            results = [f1.result(timeout=8), f2.result(timeout=8)]
        assert len(starts) == 1
        assert len(set(results)) == 2
        assert starts[0][1].conversation_id == "session-a"


def test_already_reserved_candidate_replay_is_blocked_even_if_detector_changes():
    import threading
    import uuid
    from types import SimpleNamespace
    from server import WsSessionHandler
    from self_evolution_host import HostRunContextProvider
    from tests.isolation_helpers import temporary_agent_paths
    with temporary_agent_paths() as paths:
        raw = "Update settings"
        session = SimpleNamespace(conversation_id="session-a", meta=SimpleNamespace(turn_mode="agent"),
            messages=[{"role": "user", "content": raw}], _messages_fully_loaded=True,
            _messages_corrupt=False, _messages_unpersisted=False)
        agent = SimpleNamespace(session=session)
        repl = SimpleNamespace(session=session, agent=agent)
        run = SimpleNamespace(run_id="run-a", sequence=1, state="running", cancel_requested=False)
        provider = HostRunContextProvider()
        starts = []
        class Integration:
            def start(self, request, **_kwargs):
                starts.append(request)
                return {"status": "started"}
        busy, closed, cancelled = threading.Event(), threading.Event(), threading.Event()
        busy.set()
        bridge = SimpleNamespace(self_evolution_chat=Integration(), lifecycle=SimpleNamespace(snapshot=lambda: run),
            lifecycle_conversation_id="session-a", _turn_busy=busy, _connection_closed=closed,
            cancel_event=cancelled, confirm_with_receipt=lambda *_: ("y", str(uuid.uuid4())))
        handler = WsSessionHandler(paths, self_evolution_context_provider=provider,
                                   self_evolution_intent_detector=lambda text: EvolutionRequest(text))
        request_id = str(uuid.uuid4())
        def make():
            return handler._make_self_evolution_turn_dispatch(repl, bridge, session, agent, run, raw,
                                                               request_id, "session-a", "agent")
        assert isinstance(make()(session, raw, 0), str)
        handler.self_evolution_intent_detector = lambda _: None
        assert isinstance(make()(session, raw, 0), str)
        assert len(starts) == 1

def test_read_only_host_context_recovers_real_store_snapshot_after_turn_end_and_reconnect(tmp_path):
    import threading
    from self_evolution_host import HostRunContextProvider, SelfEvolutionHost
    from server import SelfEvolutionChatIntegration, WsSessionHandler, _self_evolution_host_events
    from types import SimpleNamespace
    from tests.test_self_evolution_store import _store

    store = _store(tmp_path / "reconnect.sqlite3")
    identity = EvolutionIdentity("session-a", "task-real", "attempt-a", "run-finished", 4)
    snapshot = EvolutionSnapshot(identity, "preparing", 1)
    store.create_task_with_writer(identity, snapshot, "owner-a")
    snapshot = store.mark_task_unknown(identity.task_id, "fake_worker_finished")
    provider = HostRunContextProvider()
    host = SelfEvolutionHost.__new__(SelfEvolutionHost)
    host._store = store
    host._worker_capabilities = object()
    host._startup_reconciliation_error = None
    host._tasks = {}
    host._tasks_lock = threading.RLock()
    host._context_provider = provider

    live = {"session": "session-a", "connection": True}
    session = SimpleNamespace(conversation_id="session-a")
    repl = SimpleNamespace(session=session)
    bridge = SimpleNamespace(_connection_closed=threading.Event())
    handler = WsSessionHandler.__new__(WsSessionHandler)
    handler.self_evolution_context_provider = provider
    integration = SelfEvolutionChatIntegration(
        host=host, session_provider=lambda: repl.session, emit=lambda item: emitted.append(item),
        snapshot_provider=lambda task: host.get_snapshot(task, session_id=repl.session.conversation_id),
        event_provider=lambda snap, *, after_sequence=0: _self_evolution_host_events(
            host, snap, after_sequence=after_sequence),
    )
    emitted = []
    integration._trusted_production_host = lambda: host
    def bind_read():
        return handler._bind_self_evolution_read_context(repl, bridge)

    # The started run scope has been destroyed. A new websocket connection
    # restores only the verified conversation's read capability.
    assert provider() is None
    with bind_read():
        assert host.start(EvolutionRequest("must not start from read scope"))["error_code"] == "run_not_live"
    assert provider() is None

    # A fresh handler/integration on a reconnected socket has no old in-memory
    # subscription; it recovers solely through the trusted session-bound read scope.
    repl = SimpleNamespace(session=SimpleNamespace(conversation_id="session-a"))
    bridge = SimpleNamespace(_connection_closed=threading.Event())
    handler = WsSessionHandler.__new__(WsSessionHandler)
    handler.self_evolution_context_provider = provider
    integration = SelfEvolutionChatIntegration(
        host=host, session_provider=lambda: repl.session, emit=emitted.append,
        snapshot_provider=lambda task: host.get_snapshot(task, session_id=repl.session.conversation_id),
        event_provider=lambda snap, *, after_sequence=0: _self_evolution_host_events(
            host, snap, after_sequence=after_sequence),
    )
    integration._trusted_production_host = lambda: host

    page = None
    with bind_read():
        recovered = host.get_snapshot("task-real", session_id="session-a")
        page = host.get_event_page("task-real", "attempt-a", session_id="session-a")
        assert recovered == snapshot
        assert page is not None and page.watermark_sequence == 2 and len(page.events) == 2
        assert integration.deliver_snapshot("task-real", request_id="c4053752-c169-4dd0-a03d-f27f706d02c1")
        assert emitted[-1]["type"] == "self_evolution.snapshot"
        assert emitted[-1]["sequence"] == 2
        assert host.get_snapshot("forged-task-id", session_id="session-a") is None
        assert host.get_snapshot("task-real", session_id="session-b") is None
    assert provider() is None
    forged_identity = EvolutionIdentity("session-a", "forged-active", "attempt-f", "run-f", 5)
    store.create_task(forged_identity, EvolutionSnapshot(forged_identity, "preparing", 1))
    with bind_read():
        assert host.get_snapshot("forged-active", session_id="session-a") is None
        assert store.read_task("session-a", "forged-active").phase == "preparing"

    # Session switch and disconnect revoke the read lease; reconnecting the
    # original session permits only another freshly host-bound read scope.
    repl.session = SimpleNamespace(conversation_id="session-b")
    with bind_read():
        assert provider().conversation_id == "session-b"
        assert host.get_snapshot("task-real", session_id="session-a") is None
        assert host.get_snapshot("task-real", session_id="session-b") is None
    repl.session = session
    bridge._connection_closed.set()
    with bind_read():
        assert provider() is None
        assert host.get_event_page("task-real", "attempt-a", session_id="session-a") is None



def test_user_message_runs_isolated_fake_worker_and_recovers_events_on_new_connection(tmp_path, monkeypatch):
    import asyncio
    import json
    import subprocess
    import threading
    import uuid
    from dataclasses import replace
    from pathlib import Path
    from server import (SelfEvolutionChatIntegration, WsBridge, WsSessionHandler, _build_repl,
                        _self_evolution_host_events)
    from session import SessionMeta, create_new, resume_desktop_or_create
    from self_evolution_host import (HostRunContextProvider, SelfEvolutionHost,
                                     SelfEvolutionHostConfig)
    from self_evolution_model_adapter import build_trusted_worker_capabilities
    from self_evolution_worker import HostTool
    from tests.isolation_helpers import temporary_agent_paths
    from tests.test_self_evolution_host import _IsolatedTestWorkspace
    from tests.test_self_evolution_store import _store
    from llm_client import LLMClient, LLMResponse
    import self_evolution_model_adapter as model_adapter

    request_id = str(uuid.uuid4())
    entered = threading.Event()
    release = threading.Event()
    calls = []
    outbound_confirmations = []
    fake_clients = []
    raw_calls = [
        {"id": "observe-1", "type": "function", "function": {"name": "observe_candidate", "arguments": "{}"}},
        {"id": "write-1", "type": "function", "function": {"name": "write_candidate_file", "arguments": json.dumps({"path": "tracked.txt", "content": "candidate\n"})}},
        {"id": "check-1", "type": "function", "function": {"name": "run_registered_check", "arguments": json.dumps({"check_id": "syntax"})}},
    ]

    class FakeClient(LLMClient):
        def __init__(self, *, paths):
            super().__init__(paths=paths)
            self.index = 0
            fake_clients.append(self)

        def chat_confirmed_tools(self, messages, **kwargs):
            assert kwargs["after_each"]() is True
            assert kwargs["confirm_fn"]("offline fake preview", False) == "y"
            kwargs["on_request_attempt"]()
            calls.append({"model_id": kwargs["model_id"], "tools": kwargs["tools"]})
            index = self.index
            self.index += 1
            if index == 0:
                entered.set()
                assert release.wait(10), "test did not release fake worker"
            tool_calls = [raw_calls[index]] if index < len(raw_calls) else []
            content = None if tool_calls else "Fake worker completed its scripted turn."
            message = {"tool_calls": tool_calls}
            response = LLMResponse(
                model="offline-test-model", content=content, tool_calls=tool_calls,
                finish_reason="tool_calls" if tool_calls else "stop", usage={},
                raw={"choices": [{"message": message}]},
            )
            return response

    monkeypatch.setattr(model_adapter, "LLMClient", FakeClient)
    with temporary_agent_paths() as paths:
        paths.data.mkdir(parents=True, exist_ok=True)
        (paths.data / "llm_models.json").write_text(json.dumps({"models": [{
            "registryId": "0x567-flash", "name": "offline fixture", "vendor": "0x567",
            "baseUrl": "https://offline.invalid", "modelId": "offline-test-model",
            "apiKeyEnv": "NO_REAL_KEY_USED", "supportsToolCall": True, "tier": "flash",
        }]}), encoding="utf-8")
        (paths.data / "llm_role_models.json").write_text(json.dumps({
            "format": 1, "preferences": {"main": "0x567-pro", "subagent": "0x567-flash"},
            "explicit_roles": ["subagent"],
        }), encoding="utf-8")
        session = create_new(paths)
        session.set_turn_mode("agent")
        events = []
        bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=5)
        repl = _build_repl(session, paths, bridge)
        main_client = repl.agent.llm
        main_cancel_before = main_client._cancel_event
        provider = HostRunContextProvider()

        def observe(_context, _args):
            return {"status": "observed", "evidence_ref": "fixture-observation"}
        def write(_context, _args):
            return {"status": "written", "evidence_ref": "fixture-edit"}
        def check(context, args):
            return {"status": "passed", "check_id": args["check_id"],
                    "candidate_digest": context.candidate_digest, "evidence_ref": "fixture-check",
                    "exit_code": 0, "process_tree_state": "stopped",
                    "requires_reconciliation": False, "evidence_scope": "isolated-test"}

        trusted_tools = (
            HostTool("observe_candidate", "Observe the isolated candidate", observe),
            HostTool("write_candidate_file", "Write the isolated candidate", write),
            HostTool("run_registered_check", "Run the host test check", check),
        )
        caps = build_trusted_worker_capabilities(
            paths, session.meta, tools=trusted_tools,
            outbound_request_confirm_fn=lambda preview, allow: outbound_confirmations.append((preview, allow)) or "y",
            process_tree_stopped=lambda _identity: True, max_requests=4,
        )
        assert caps.enable_experimental_start is False
        # The only gate override is local to this test fixture; source production gates stay false.
        caps = replace(caps, enable_experimental_start=True)

        stable = Path(tmp_path / "stable")
        trust_root = Path(tmp_path / "candidates")
        stable.mkdir()
        trust_root.mkdir()
        subprocess.run(["git", "init", "--quiet", str(stable)], check=True)
        subprocess.run(["git", "-C", str(stable), "config", "user.name", "Fixture"], check=True)
        subprocess.run(["git", "-C", str(stable), "config", "user.email", "fixture@example.invalid"], check=True)
        (stable / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(stable), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(stable), "commit", "--quiet", "-m", "fixture"], check=True)
        config = SelfEvolutionHostConfig(
            stable_repository=stable, trust_root=trust_root, attribution_paths=("tracked.txt",),
            verification_source_paths=("tracked.txt",),
            verification_commands={"syntax": ("python", "-c", "print('offline')")},
            verification_timeout_seconds=3,
        )
        key = b"isolated-self-evolution-test-key-material-32-bytes"
        host = SelfEvolutionHost(
            config, integrity_key=key, context_provider=provider,
            trusted_verifier_resolver=lambda *_args: None,
            store=_store(tmp_path / "self-evolution.sqlite3"), worker_capabilities=caps,
        )
        host._workspace = _IsolatedTestWorkspace(stable, trust_root, key)
        monkeypatch.setattr(SelfEvolutionHost, "full_candidate_coverage_available", property(lambda _self: True))

        chat = SelfEvolutionChatIntegration(
            host=host, session_provider=lambda: repl.session, emit=events.append,
            snapshot_provider=lambda task: host.get_snapshot(task, session_id=repl.session.conversation_id),
            event_provider=lambda snapshot, *, after_sequence=0: _self_evolution_host_events(
                host, snapshot, after_sequence=after_sequence),
        )
        monkeypatch.setattr(chat, "_trusted_production_host", lambda: host)
        bridge.self_evolution_chat = chat
        repl.agent.self_evolution_chat = chat
        handler = WsSessionHandler(
            paths, self_evolution_host=host, self_evolution_context_provider=provider,
            self_evolution_intent_detector=lambda text: EvolutionRequest(text) if text == "self evolve request" else None,
        )
        message = {
            "type": "user.message", "text": "self evolve request",
            "session_id": session.conversation_id, "request_id": request_id,
            "model_id": "renderer-forged-model", "tools": ["renderer-forged-tool"],
            "confirm_fn": "renderer-forged-confirmation",
        }

        async def approved_turn():
            task = asyncio.create_task(handler._dispatch(message, repl, bridge))
            for _ in range(500):
                pending = next((event for event in events if event.get("type") == "confirm.request"), None)
                if pending:
                    assert bridge.deliver_confirm(pending["request_id"], "y")
                    break
                await asyncio.sleep(.01)
            else:
                raise AssertionError("user confirmation was not requested")
            await asyncio.wait_for(task, 10)
        asyncio.run(approved_turn())
        assert provider() is None  # the authorizing chat turn has ended
        assert entered.wait(5), "Host did not hand the request to its injected fake worker"
        assert len(fake_clients) == 1 and fake_clients[0] is caps.client
        assert caps.client is not main_client
        assert caps.client._cancel_event is not main_cancel_before
        assert main_client._cancel_event is main_cancel_before
        assert len(host._tasks) == 1
        task_id, task_state = next(iter(host._tasks.items()))
        assert task_state.identity is not None
        # Replay through the real user.message path while the original writer
        # is still blocked; it must not create another task or writer lease.
        asyncio.run(handler._dispatch(message, repl, bridge))
        assert list(host._tasks) == [task_id]
        slots = host._store.list_writer_slots()
        assert len(slots) == 1 and slots[0]["task_id"] == task_id
        assert slots[0]["attempt_id"] == task_state.identity.attempt_id

        with handler._bind_self_evolution_read_context(repl, bridge):
            assert chat.deliver_snapshot(task_id, request_id=str(uuid.uuid4()))
        initial_sequence = events[-1]["sequence"]
        release.set()
        assert host.wait_for_worker(task_id, task_state.identity.attempt_id, timeout=10)
        with handler._bind_self_evolution_read_context(repl, bridge):
            assert chat.poll_updates() >= 1
        incremental = [event for event in events if event.get("type") == "self_evolution.update"]
        assert incremental and all(event["sequence"] > initial_sequence for event in incremental)
        assert len(calls) == 4
        assert all(call["model_id"] == "0x567-flash" for call in calls)
        assert all({tool["function"]["name"] for tool in call["tools"]} ==
                   {tool.name for tool in trusted_tools} for call in calls)
        assert outbound_confirmations and all(allow is False for _, allow in outbound_confirmations)
        assert main_client._cancel_event is main_cancel_before

        # New socket: fresh integration state, same verified conversation and Host-owned store.
        restored = resume_desktop_or_create(paths)
        assert restored.conversation_id == session.conversation_id
        bridge2 = WsBridge(emit=events.append, paths=paths)
        repl2 = _build_repl(restored, paths, bridge2)
        handler2 = WsSessionHandler(paths, self_evolution_host=host, self_evolution_context_provider=provider)
        chat2 = SelfEvolutionChatIntegration(
            host=host, session_provider=lambda: repl2.session, emit=events.append,
            snapshot_provider=lambda task: host.get_snapshot(task, session_id=repl2.session.conversation_id),
            event_provider=lambda snapshot, *, after_sequence=0: _self_evolution_host_events(
                host, snapshot, after_sequence=after_sequence),
        )
        monkeypatch.setattr(chat2, "_trusted_production_host", lambda: host)
        with handler2._bind_self_evolution_read_context(repl2, bridge2):
            assert chat2.deliver_snapshot(task_id, request_id=str(uuid.uuid4()))
        assert events[-1]["type"] == "self_evolution.snapshot"
        assert events[-1]["task_id"] == task_id
        assert events[-1]["phase"] == "unknown"  # verification reconciliation is not claimed as success
        assert events[-1]["history_status"] == "not_restored"
        assert provider() is None


def test_websocket_snapshot_message_recovers_on_new_connection_and_rejects_other_session(tmp_path, monkeypatch):
    import asyncio
    import threading
    from types import SimpleNamespace

    from server import SelfEvolutionChatIntegration, WsBridge, WsSessionHandler, _self_evolution_host_events
    from self_evolution_host import HostRunContextProvider, SelfEvolutionHost
    from self_evolution_types import EvolutionIdentity, EvolutionSnapshot
    from tests.isolation_helpers import temporary_agent_paths
    from tests.test_self_evolution_store import _store

    store = _store(tmp_path / "ws-recovery.sqlite3")
    identity = EvolutionIdentity("session-a", "task-ws", "attempt-ws", "finished-run", 9)
    initial = EvolutionSnapshot(identity, "preparing", 1, allowed_next_actions=("cancel", "get_snapshot"))
    store.create_task_with_writer(identity, initial, "owner-ws")
    persisted = store.mark_task_unknown(identity.task_id, "worker_owner_unconfirmed")

    provider = HostRunContextProvider()
    host = SelfEvolutionHost.__new__(SelfEvolutionHost)
    host._store = store
    host._worker_capabilities = object()
    host._startup_reconciliation_error = None
    host._tasks = {}
    host._tasks_lock = threading.RLock()
    host._context_provider = provider
    monkeypatch.setattr(SelfEvolutionHost, "production_ready", property(lambda _self: True))

    emitted = []
    with temporary_agent_paths() as paths:
        def connected(session_id):
            session = SimpleNamespace(conversation_id=session_id)
            repl = SimpleNamespace(session=session)
            bridge = WsBridge(emit=emitted.append, paths=paths)
            integration = SelfEvolutionChatIntegration(
                host=host, session_provider=lambda: repl.session, emit=emitted.append,
                snapshot_provider=lambda task_id: host.get_snapshot(task_id, session_id=repl.session.conversation_id),
                event_provider=lambda snapshot, *, after_sequence=0: _self_evolution_host_events(
                    host, snapshot, after_sequence=after_sequence),
            )
            integration._trusted_production_host = lambda: host
            repl.agent = SimpleNamespace(deliver_self_evolution_snapshot=integration.deliver_snapshot)
            handler = WsSessionHandler.__new__(WsSessionHandler)
            handler.self_evolution_context_provider = provider
            return handler, repl, bridge

        # The first connection is gone; a fresh handler has no subscription state.
        handler, repl, bridge = connected("session-a")
        asyncio.run(handler._handle_incoming({
            "type": "self_evolution.snapshot.get", "task_id": identity.task_id,
            "request_id": "c4053752-c169-4dd0-a03d-f27f706d02c1",
        }, repl, bridge))
        assert emitted[-1]["type"] == "self_evolution.snapshot"
        assert emitted[-1]["task_id"] == identity.task_id
        assert emitted[-1]["phase"] == persisted.phase
        assert provider() is None

        # A different session uses the same real message handler but cannot read
        # or receive the original session's durable snapshot.
        handler_b, repl_b, bridge_b = connected("session-b")
        asyncio.run(handler_b._handle_incoming({
            "type": "self_evolution.snapshot.get", "task_id": identity.task_id,
            "request_id": "d4053752-c169-4dd0-a03d-f27f706d02c2",
        }, repl_b, bridge_b))
        assert emitted[-1]["type"] == "self_evolution.snapshot.unavailable"
        assert emitted[-1]["session_id"] == "session-b"
        assert store.read_task("session-a", identity.task_id) == persisted
        assert provider() is None

def test_explicit_self_evolution_cancel_command_uses_fresh_human_turn_and_exact_task_binding():
    import threading
    import uuid
    from types import SimpleNamespace
    from server import WsSessionHandler
    from self_evolution_host import HostRunContextProvider
    from self_evolution_types import CancelReceipt
    from tests.isolation_helpers import temporary_agent_paths

    with temporary_agent_paths() as paths:
        task_id, attempt_id = str(uuid.uuid4()), str(uuid.uuid4())
        raw = f"/self-evolution cancel {task_id} {attempt_id}"
        session = SimpleNamespace(conversation_id="session-a", meta=SimpleNamespace(turn_mode="agent"),
            messages=[{"role": "user", "content": raw}], _messages_fully_loaded=True,
            _messages_corrupt=False, _messages_unpersisted=False)
        agent = SimpleNamespace(session=session)
        repl = SimpleNamespace(session=session, agent=agent)
        run = SimpleNamespace(run_id="new-human-run", sequence=12, state="running", cancel_requested=False)
        provider = HostRunContextProvider()
        calls = []
        class Integration:
            def request_cancel(self, task, attempt):
                calls.append((task, attempt, provider()))
                return CancelReceipt(task, attempt, "accepted")
        busy, closed, cancelled = threading.Event(), threading.Event(), threading.Event()
        busy.set()
        bridge = SimpleNamespace(self_evolution_chat=Integration(), lifecycle=SimpleNamespace(snapshot=lambda: run),
            lifecycle_conversation_id="session-a", _turn_busy=busy, _connection_closed=closed,
            cancel_event=cancelled)
        handler = WsSessionHandler(paths, self_evolution_context_provider=provider,
                                   self_evolution_intent_detector=None)
        dispatch = handler._make_self_evolution_turn_dispatch(
            repl, bridge, session, agent, run, raw, str(uuid.uuid4()), "session-a", "agent")

        reply = dispatch(session, raw, 0)

        assert isinstance(reply, str)
        assert len(calls) == 1
        called_task, called_attempt, context = calls[0]
        assert (called_task, called_attempt) == (task_id, attempt_id)
        assert context.conversation_id == "session-a"
        assert context.run_id == "new-human-run" and context.turn_epoch == 12
        assert context.request_origin == "human" and context.self_evolution_intent is False
        assert provider() is None


def test_ordinary_chat_cannot_cancel_self_evolution_task_by_mentioning_it():
    import threading
    import uuid
    from types import SimpleNamespace
    from server import WsSessionHandler
    from self_evolution_host import HostRunContextProvider
    from tests.isolation_helpers import temporary_agent_paths

    with temporary_agent_paths() as paths:
        task_id, attempt_id = str(uuid.uuid4()), str(uuid.uuid4())
        raw = f"Explain the command /self-evolution cancel {task_id} {attempt_id}"
        session = SimpleNamespace(conversation_id="session-a", meta=SimpleNamespace(turn_mode="agent"),
            messages=[{"role": "user", "content": raw}], _messages_fully_loaded=True,
            _messages_corrupt=False, _messages_unpersisted=False)
        agent = SimpleNamespace(session=session)
        repl = SimpleNamespace(session=session, agent=agent)
        run = SimpleNamespace(run_id="new-human-run", sequence=12, state="running", cancel_requested=False)
        provider = HostRunContextProvider()
        calls = []
        class Integration:
            def request_cancel(self, task, attempt):
                calls.append((task, attempt))
                return {"disposition": "accepted"}
        busy, closed, cancelled = threading.Event(), threading.Event(), threading.Event()
        busy.set()
        bridge = SimpleNamespace(self_evolution_chat=Integration(), lifecycle=SimpleNamespace(snapshot=lambda: run),
            lifecycle_conversation_id="session-a", _turn_busy=busy, _connection_closed=closed,
            cancel_event=cancelled)
        handler = WsSessionHandler(paths, self_evolution_context_provider=provider,
                                   self_evolution_intent_detector=lambda _text: None)
        dispatch = handler._make_self_evolution_turn_dispatch(
            repl, bridge, session, agent, run, raw, str(uuid.uuid4()), "session-a", "agent")

        assert dispatch(session, raw, 0) is None
        assert calls == []


def test_read_only_and_foreign_session_cannot_cancel_through_host_integration():
    from server import SelfEvolutionChatIntegration
    from self_evolution_host import HostReadContext, HostRunContext, HostRunContextProvider
    from self_evolution_types import CancelReceipt
    import threading

    provider = HostRunContextProvider()
    calls = []
    class Host:
        worker_bound = True
        def request_cancel(self, task_id, attempt_id):
            context = provider()
            calls.append(context)
            return CancelReceipt(task_id, attempt_id, "accepted")
    host = Host()
    host._context_provider = provider
    integration = SelfEvolutionChatIntegration(host=host, session_provider=lambda: "session-a", emit=lambda _: None)
    integration._trusted_worker_host = lambda: host

    with provider.bind_read("session-a", lambda: True):
        result = integration.request_cancel("task-a", "attempt-a")
        assert result is None
    assert calls == []

    context = HostRunContext("session-b", "run-b", 2, "agent", "execute", threading.Event(),
                             "00000000-0000-4000-8000-000000000002", "human")
    with provider.bind(context, lambda: True):
        result = integration.request_cancel("task-a", "attempt-a")
    assert result is None
    assert calls == []


def test_cancel_remains_available_when_start_replay_capacity_is_full():
    import threading
    import uuid
    from types import SimpleNamespace
    from server import SelfEvolutionChatIntegration, WsSessionHandler
    from self_evolution_host import HostRunContextProvider, SelfEvolutionHost, _TaskState
    from tests.isolation_helpers import temporary_agent_paths
    from tests.test_self_evolution_store import _store

    with temporary_agent_paths() as paths:
        task_id, attempt_id = str(uuid.uuid4()), str(uuid.uuid4())
        raw = f"/self-evolution cancel {task_id} {attempt_id}"
        session = SimpleNamespace(conversation_id="session-a", meta=SimpleNamespace(turn_mode="agent"),
            messages=[{"role": "user", "content": raw}], _messages_fully_loaded=True,
            _messages_corrupt=False, _messages_unpersisted=False)
        agent = SimpleNamespace(session=session)
        repl = SimpleNamespace(session=session, agent=agent)
        run = SimpleNamespace(run_id="new-run", sequence=12, state="running", cancel_requested=False)
        provider = HostRunContextProvider()
        store = _store(paths.data / "cancel-capacity.sqlite3")
        identity = EvolutionIdentity("session-a", task_id, attempt_id, "old-run", 1)
        store.create_task(identity, EvolutionSnapshot(identity, "preparing", 1))
        store.claim_writer("owner", task_id, attempt_id)
        worker_stop = threading.Event()
        state = _TaskState(task_id, ("session-a", "old-run", 1, "old-request"), {}, None, {},
                           identity=identity, cancel_event=worker_stop, owner_id="owner", writer_held=True)
        host = SelfEvolutionHost.__new__(SelfEvolutionHost)
        host._store, host._worker_capabilities = store, object()
        host._context_provider, host._startup_reconciliation_error = provider, None
        host._tasks, host._tasks_lock = {task_id: state}, threading.RLock()
        integration = SelfEvolutionChatIntegration(host=host, session_provider=lambda: repl.session, emit=lambda _: None)
        busy = threading.Event()
        busy.set()
        bridge = SimpleNamespace(self_evolution_chat=integration, lifecycle=SimpleNamespace(snapshot=lambda: run),
            lifecycle_conversation_id="session-a", _turn_busy=busy, _connection_closed=threading.Event(),
            cancel_event=threading.Event())
        handler = WsSessionHandler(paths, self_evolution_host=host, self_evolution_context_provider=provider)
        original = {("prior-session", str(uuid.uuid4())) for _ in range(4096)}
        assert len(original) == 4096
        handler._self_evolution_seen.update(original)
        request_id = str(uuid.uuid4())
        dispatch = handler._make_self_evolution_turn_dispatch(
            repl, bridge, session, agent, run, raw, request_id, "session-a", "agent")

        assert "\u5df2\u63a5\u6536" in dispatch(session, raw, 0)
        assert worker_stop.is_set()
        assert store.read_task("session-a", task_id).phase == "cancelling"
        assert handler._self_evolution_seen == original
        # Replaying this exact cancellation is safe and cannot release the writer.
        assert "\u5df2\u63a5\u6536" in dispatch(session, raw, 0)
        assert len(store.read_events("session-a", task_id, attempt_id)) == 1
        assert store.list_writer_slots()[0]["owner_id"] == "owner"


def test_missing_cancellation_flood_does_not_consume_start_replay_capacity():
    import threading
    import uuid
    from types import SimpleNamespace
    from server import WsSessionHandler
    from self_evolution_host import HostRunContextProvider
    from self_evolution_types import CancelReceipt
    from tests.isolation_helpers import temporary_agent_paths

    with temporary_agent_paths() as paths:
        session = SimpleNamespace(conversation_id="session-a", meta=SimpleNamespace(turn_mode="agent"),
            messages=[], _messages_fully_loaded=True, _messages_corrupt=False, _messages_unpersisted=False)
        agent = SimpleNamespace(session=session)
        repl = SimpleNamespace(session=session, agent=agent)
        run = SimpleNamespace(run_id="run-a", sequence=1, state="running", cancel_requested=False)
        provider = HostRunContextProvider()
        started = []
        class Integration:
            def request_cancel(self, task, attempt):
                return CancelReceipt(task, attempt, "not_found")
            def start(self, request, **_kwargs):
                started.append(request)
                return {"status": "started"}
        busy = threading.Event()
        busy.set()
        bridge = SimpleNamespace(self_evolution_chat=Integration(), lifecycle=SimpleNamespace(snapshot=lambda: run),
            lifecycle_conversation_id="session-a", _turn_busy=busy, _connection_closed=threading.Event(),
            cancel_event=threading.Event(), confirm_with_receipt=lambda *_: ("y", str(uuid.uuid4())))
        handler = WsSessionHandler(paths, self_evolution_context_provider=provider,
                                   self_evolution_intent_detector=lambda text: EvolutionRequest(text))
        task_id, attempt_id = str(uuid.uuid4()), str(uuid.uuid4())
        raw = f"/self-evolution cancel {task_id} {attempt_id}"
        session.messages = [{"role": "user", "content": raw}]
        for _ in range(4100):
            dispatch = handler._make_self_evolution_turn_dispatch(
                repl, bridge, session, agent, run, raw, str(uuid.uuid4()), "session-a", "agent")
            assert isinstance(dispatch(session, raw, 0), str)
        assert not handler._self_evolution_seen
        raw = "Start a self evolution task"
        session.messages = [{"role": "user", "content": raw}]
        request_id = str(uuid.uuid4())
        dispatch = handler._make_self_evolution_turn_dispatch(
            repl, bridge, session, agent, run, raw, request_id, "session-a", "agent")
        assert isinstance(dispatch(session, raw, 0), str)
        assert len(started) == 1
        # The start reservation remains authoritative even on replay.
        assert isinstance(dispatch(session, raw, 0), str)
        assert len(started) == 1
        assert ("session-a", request_id) in handler._self_evolution_seen


def test_cancel_replay_cannot_signal_new_attempt_after_old_attempt_ended():
    import threading
    import uuid
    from server import SelfEvolutionChatIntegration
    from self_evolution_host import HostRunContext, HostRunContextProvider, SelfEvolutionHost, _TaskState
    from tests.isolation_helpers import temporary_agent_paths
    from tests.test_self_evolution_store import _store

    with temporary_agent_paths() as paths:
        task_id, old_id, new_id = (str(uuid.uuid4()) for _ in range(3))
        store = _store(paths.data / "cancel-old-attempt.sqlite3")
        old = EvolutionIdentity("session-a", task_id, old_id, "old-run", 1)
        store.create_task(old, EvolutionSnapshot(old, "preparing", 1))
        store.compare_and_swap(task_id, 1, EvolutionSnapshot(old, "blocked", 2))
        new = EvolutionIdentity("session-a", task_id, new_id, "new-run", 2)
        store.begin_attempt(task_id, 2, new, EvolutionSnapshot(new, "preparing", 3))
        store.claim_writer("new-owner", task_id, new_id)
        stop = threading.Event()
        state = _TaskState(task_id, ("session-a", "new-run", 2, "request"), {}, None, {},
                           identity=new, cancel_event=stop, owner_id="new-owner", writer_held=True)
        provider = HostRunContextProvider()
        host = SelfEvolutionHost.__new__(SelfEvolutionHost)
        host._store, host._worker_capabilities = store, object()
        host._context_provider, host._startup_reconciliation_error = provider, None
        host._tasks, host._tasks_lock = {task_id: state}, threading.RLock()
        integration = SelfEvolutionChatIntegration(host=host, session_provider=lambda: "session-a", emit=lambda _: None)
        context = HostRunContext("session-a", "user-run", 3, "agent", "execute", threading.Event(),
                                 str(uuid.uuid4()), "human")
        with provider.bind(context, lambda: True):
            assert integration.request_cancel(task_id, old_id).disposition == "not_found"
            assert integration.request_cancel(task_id, old_id).disposition == "not_found"
        assert not stop.is_set()
        assert store.read_task("session-a", task_id).identity == new
        assert store.read_task("session-a", task_id).phase == "preparing"
        assert store.list_writer_slots()[0]["owner_id"] == "new-owner"


def test_full_start_replay_set_preserves_existing_reservations_and_blocks_new_start():
    import threading
    import uuid
    from types import SimpleNamespace
    from server import WsSessionHandler
    from self_evolution_host import HostRunContextProvider
    from tests.isolation_helpers import temporary_agent_paths

    with temporary_agent_paths() as paths:
        raw = "Start the task"
        session = SimpleNamespace(conversation_id="session-a", meta=SimpleNamespace(turn_mode="agent"),
            messages=[{"role": "user", "content": raw}], _messages_fully_loaded=True,
            _messages_corrupt=False, _messages_unpersisted=False)
        agent = SimpleNamespace(session=session)
        repl = SimpleNamespace(session=session, agent=agent)
        run = SimpleNamespace(run_id="run", sequence=1, state="running", cancel_requested=False)
        provider = HostRunContextProvider()
        starts = []
        class Integration:
            def start(self, request, **_kwargs):
                starts.append(request)
                return {"status": "started"}
        busy = threading.Event()
        busy.set()
        bridge = SimpleNamespace(self_evolution_chat=Integration(), lifecycle=SimpleNamespace(snapshot=lambda: run),
            lifecycle_conversation_id="session-a", _turn_busy=busy, _connection_closed=threading.Event(),
            cancel_event=threading.Event(), confirm_with_receipt=lambda *_: ("y", str(uuid.uuid4())))
        handler = WsSessionHandler(paths, self_evolution_context_provider=provider,
                                   self_evolution_intent_detector=lambda text: EvolutionRequest(text))
        request_id = str(uuid.uuid4())
        def dispatch_for(key):
            return handler._make_self_evolution_turn_dispatch(
                repl, bridge, session, agent, run, raw, key, "session-a", "agent")(session, raw, 0)
        dispatch_for(request_id)
        assert len(starts) == 1
        handler._self_evolution_seen.update(("old-session", str(uuid.uuid4())) for _ in range(4095))
        before = set(handler._self_evolution_seen)
        assert len(before) == 4096
        dispatch_for(request_id)
        dispatch_for(str(uuid.uuid4()))
        assert len(starts) == 1
        assert handler._self_evolution_seen == before

def _natural_cancel_dispatch(raw, *, detector=None, active_disposition="accepted"):
    import threading
    import uuid
    from types import SimpleNamespace
    from server import WsSessionHandler
    from self_evolution_host import HostRunContextProvider
    from tests.isolation_helpers import temporary_agent_paths

    manager = temporary_agent_paths()
    paths = manager.__enter__()
    session = SimpleNamespace(conversation_id="session-a", meta=SimpleNamespace(turn_mode="agent"),
        messages=[{"role": "user", "content": raw}], _messages_fully_loaded=True,
        _messages_corrupt=False, _messages_unpersisted=False)
    agent = SimpleNamespace(session=session)
    repl = SimpleNamespace(session=session, agent=agent)
    run = SimpleNamespace(run_id="human-run", sequence=21, state="running", cancel_requested=False)
    provider = HostRunContextProvider()
    calls = []
    class Integration:
        def request_cancel_active(self):
            calls.append(provider())
            return SimpleNamespace(disposition=active_disposition)
    busy = threading.Event(); busy.set()
    bridge = SimpleNamespace(self_evolution_chat=Integration(), lifecycle=SimpleNamespace(snapshot=lambda: run),
        lifecycle_conversation_id="session-a", _turn_busy=busy, _connection_closed=threading.Event(),
        cancel_event=threading.Event())
    handler = WsSessionHandler(paths, self_evolution_context_provider=provider,
                               self_evolution_intent_detector=detector)
    dispatch = handler._make_self_evolution_turn_dispatch(
        repl, bridge, session, agent, run, raw, str(uuid.uuid4()), "session-a", "agent")
    return manager, dispatch, session, raw, calls


def test_natural_language_cancel_requires_explicit_unambiguous_self_evolution_reference():
    cases = [
        "\u8bf7\u53d6\u6d88\u5f53\u524d\u81ea\u6211\u6539\u9020\u4efb\u52a1",
        "\u505c\u6b62\u8fd9\u4e2a\u81ea\u6211\u8fdb\u5316\u6d3b\u52a8",
        "\u522b\u7ee7\u7eed\u81ea\u6211\u6539\u9020\u4e86",
        "\u6211\u5728\u60f3\u662f\u5426\u8981\u53d6\u6d88\u81ea\u6211\u6539\u9020\uff1f",
        "\u5047\u8bbe\u53d6\u6d88\u81ea\u6211\u6539\u9020\u4f1a\u600e\u6837\uff1f",
        "\u8bf7\u89e3\u91ca\u2018\u53d6\u6d88\u81ea\u6211\u6539\u9020\u2019\u8fd9\u53e5\u8bdd",
        "\u666e\u901a\u804a\u5929\u91cc\u63d0\u5230\u81ea\u6211\u6539\u9020",
    ]
    for index, raw in enumerate(cases):
        detector = (lambda _text: (_ for _ in ()).throw(AssertionError("cancel must not consult the model"))) if index == 0 else None
        manager, dispatch, session, text, calls = _natural_cancel_dispatch(raw, detector=detector)
        try:
            reply = dispatch(session, text, 0)
            should_cancel = index < 3
            assert bool(calls) is should_cancel, raw
            if should_cancel:
                assert "\u5df2\u505c\u6b62" not in reply and "\u5df2\u53d6\u6d88" not in reply
            else:
                assert reply is None
        finally:
            manager.__exit__(None, None, None)


def test_explicit_natural_cancel_with_no_unique_activity_returns_no_action_receipt():
    raw = "\u8bf7\u505c\u6b62\u5f53\u524d\u81ea\u6211\u6539\u9020\u4efb\u52a1"
    manager, dispatch, session, text, calls = _natural_cancel_dispatch(raw, active_disposition="not_found")
    try:
        reply = dispatch(session, text, 0)
        assert len(calls) == 1
        assert isinstance(reply, str)
        assert "\u552f\u4e00" in reply and ("\u672a\u6267\u884c" in reply or "\u672a\u53d1\u51fa" in reply)
    finally:
        manager.__exit__(None, None, None)



def test_natural_cancel_adapter_requires_authenticated_current_session_and_unique_host_target():
    import threading
    from server import SelfEvolutionChatIntegration
    from self_evolution_host import HostRunContext, HostRunContextProvider
    from self_evolution_types import CancelReceipt

    provider = HostRunContextProvider()
    calls = []
    class Host:
        worker_bound = True
        _context_provider = provider
        def resolve_unique_active_task(self):
            calls.append(("resolve", provider()))
            return ("task-current", "attempt-current")
        def request_cancel(self, task_id, attempt_id):
            calls.append((task_id, attempt_id, provider()))
            return CancelReceipt(task_id, attempt_id, "accepted")
    host = Host()
    integration = SelfEvolutionChatIntegration(host=host, session_provider=lambda: "session-a", emit=lambda _: None)
    integration._trusted_worker_host = lambda: host

    read_result = None
    with provider.bind_read("session-a", lambda: True):
        read_result = integration.request_cancel_active()
    assert read_result is None and calls == []

    foreign = HostRunContext("session-b", "run-b", 2, "agent", "execute", threading.Event(),
                             "00000000-0000-4000-8000-000000000002", "human")
    with provider.bind(foreign, lambda: True):
        assert integration.request_cancel_active() is None
    assert calls == []

    current = HostRunContext("session-a", "run-current", 3, "agent", "execute", threading.Event(),
                             "00000000-0000-4000-8000-000000000003", "human")
    with provider.bind(current, lambda: True):
        receipt = integration.request_cancel_active()
    assert receipt.disposition == "accepted"
    assert calls[0][0] == "resolve"
    assert calls[1][:2] == ("task-current", "attempt-current")



def test_persistent_start_adapter_replays_results_without_second_host_dispatch(tmp_path, monkeypatch):
    import hashlib
    import json
    import threading
    import uuid
    from server import SelfEvolutionChatIntegration
    from self_evolution_host import HostRunContext, HostRunContextProvider
    from tests.test_self_evolution_store import _store

    store = _store(tmp_path / "persistent-start.sqlite3")
    provider = HostRunContextProvider()
    request_id = str(uuid.uuid4())
    context = HostRunContext("session-a", "run-a", 7, "agent", "execute", threading.Event(),
                             request_id, "human", self_evolution_intent=True)
    class Host:
        worker_bound = True
        _store = store
        _context_provider = provider
        def __init__(self): self.starts = []
        def start(self, request, *, start_request_claim, **_kwargs):
            self.starts.append((request, start_request_claim))
            return {"status": "started", "task_id": "task-a", "attempt_id": "attempt-a",
                    "run_id": "run-a", "worker_bound": True, "os_sandbox": False,
                    "merge_authorized": False}
    host = Host()
    integration = SelfEvolutionChatIntegration(host=host, session_provider=lambda: "session-a", emit=lambda _: None)
    monkeypatch.setattr(integration, "_trusted_production_host", lambda: host)
    request = EvolutionRequest("improve parser", ("preserve behavior",))
    canonical = json.dumps({"user_goal": request.user_goal,
                            "acceptance_points": list(request.acceptance_points),
                            "context_refs": list(request.context_refs)},
                           ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    with provider.bind(context, lambda: True):
        first = integration.start(request, request_digest=digest)
        replay = integration.start(request, request_digest=digest)
    assert first["status"] == "started"
    assert replay == first
    assert len(host.starts) == 1
    assert host.starts[0][1].claim_token

    mismatch = "f" * 64 if digest != "f" * 64 else "e" * 64
    with provider.bind(context, lambda: True):
        conflict = integration.start(request, request_digest=mismatch)
    assert conflict == {"status": "blocked", "error_code": "request_id_payload_conflict"}
    assert len(host.starts) == 1


def test_persistent_start_adapter_never_dispatches_unclaimed_or_unconfirmed_request(tmp_path, monkeypatch):
    import threading
    import uuid
    from server import SelfEvolutionChatIntegration
    from self_evolution_host import HostRunContext, HostRunContextProvider
    from tests.test_self_evolution_store import _store

    store = _store(tmp_path / "pending-start.sqlite3")
    provider = HostRunContextProvider()
    request_id = str(uuid.uuid4())
    claim = store.reserve_start_request("session-a", request_id, "a" * 64)
    assert claim.status == "claimed"
    class Host:
        worker_bound = True
        _store = store
        _context_provider = provider
        def __init__(self): self.starts = 0
        def start(self, request, *, start_request_claim, **_kwargs):
            self.starts += 1
            return {"status": "started"}
    host = Host()
    integration = SelfEvolutionChatIntegration(host=host, session_provider=lambda: "session-a", emit=lambda _: None)
    monkeypatch.setattr(integration, "_trusted_production_host", lambda: host)
    request = EvolutionRequest("change")
    context = HostRunContext("session-a", "run-a", 1, "agent", "execute", threading.Event(),
                             request_id, "human", self_evolution_intent=True)
    with provider.bind(context, lambda: True):
        pending = integration.start(request, request_digest="a" * 64)
    assert pending["status"] == "unknown"
    assert host.starts == 0

    unconfirmed = HostRunContext("session-a", "run-a", 1, "agent", "execute", threading.Event(),
                                 str(uuid.uuid4()), "human", self_evolution_intent=False)
    with provider.bind(unconfirmed, lambda: True):
        denied = integration.start(request)
    assert denied["status"] == "denied"
    assert host.starts == 0



def test_run_server_backend_seam_is_typed_backend_only_and_stays_disabled_by_default():
    from server import _self_evolution_backend_for_server
    from self_evolution_bootstrap import SelfEvolutionBootstrapDependencies, SelfEvolutionBootstrapRefused
    from tests.isolation_helpers import temporary_agent_paths

    with temporary_agent_paths() as paths:
        default = _self_evolution_backend_for_server(paths, None)
        assert default[:3] == (None, None, None)
        assert default[3][0] == "backend_dependencies_unavailable"
        assert "host_integrity_key_unavailable" in default[3]
        assert "persistent_store_unavailable" in default[3]
        assert "host_production_gate_closed" in default[3]
        assert "trusted_review_disclosure_callback_unavailable" in default[3]

        incomplete = SelfEvolutionBootstrapDependencies(paths=paths)
        partial = _self_evolution_backend_for_server(paths, incomplete)
        assert partial[:3] == (None, None, None)
        assert "backend_intent_detector_unavailable" in partial[3]
        assert "process_tree_safety_callback_unavailable" in partial[3]
        assert "candidate_creation_safety_gate_closed" in partial[3]
        try:
            _self_evolution_backend_for_server(paths, {"enable_experimental_start": True})
        except TypeError as exc:
            assert "trusted SelfEvolutionBootstrapDependencies" in str(exc)
        else:
            raise AssertionError("renderer-shaped mappings cannot inject Host capabilities")

        foreign = SelfEvolutionBootstrapDependencies(paths=object())
        try:
            _self_evolution_backend_for_server(paths, foreign)
        except SelfEvolutionBootstrapRefused as exc:
            assert exc.reason_codes == ("backend_agent_paths_mismatch",)
        else:
            raise AssertionError("backend dependencies must be bound to the active AgentPaths")


def test_ordinary_run_server_reports_self_evolution_gate_reasons(capsys, monkeypatch):
    import asyncio
    import json
    from types import SimpleNamespace
    import server
    import interface_lock
    from tests.isolation_helpers import temporary_agent_paths

    with temporary_agent_paths() as paths:
        monkeypatch.setattr(server.AgentPaths, "discover", lambda: paths)
        monkeypatch.setattr(server, "configure_sidecar_logging", lambda _paths: None)
        monkeypatch.setattr(server.atexit, "register", lambda *_args, **_kwargs: None)

        class LockGuard:
            def __init__(self, *_args):
                self._held = False
            def release(self):
                self._held = False

        monkeypatch.setattr(interface_lock, "InterfaceLockGuard", LockGuard)
        monkeypatch.setattr(interface_lock, "acquire_lock", lambda *_args, **_kwargs: SimpleNamespace(
            status=interface_lock.AcquireStatus.ACQUIRED, holder=None,
        ))

        class FakeServer:
            sockets = [SimpleNamespace(getsockname=lambda: ("127.0.0.1", 43210))]
            async def serve_forever(self):
                raise asyncio.CancelledError

        class FakeServe:
            async def __aenter__(self):
                return FakeServer()
            async def __aexit__(self, *_args):
                return False

        monkeypatch.setattr(server, "serve", lambda *_args, **_kwargs: FakeServe())
        handler_kwargs = []
        def make_handler(*_args, **kwargs):
            handler_kwargs.append(kwargs)
            return SimpleNamespace(handle=lambda _ws: None)
        monkeypatch.setattr(server, "WsSessionHandler", make_handler)

        try:
            asyncio.run(server.run_server("127.0.0.1", 0))
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("the test server must stop at the ready boundary")

        ready = json.loads(capsys.readouterr().out.splitlines()[0])
        status = ready["self_evolution"]
        assert status["status"] == "disabled"
        assert "backend_dependencies_unavailable" in status["reason_codes"]
        assert "host_integrity_key_unavailable" in status["reason_codes"]
        assert "persistent_store_unavailable" in status["reason_codes"]
        assert "backend_intent_detector_unavailable" in status["reason_codes"]
        assert "host_production_gate_closed" in status["reason_codes"]
        assert "trusted_review_disclosure_callback_unavailable" in status["reason_codes"]
        assert status["worker_start_enabled"] is False
        assert handler_kwargs[0]["self_evolution_tool_dispatch_enabled"] is True
        assert handler_kwargs[0]["self_evolution_blockers"] == tuple(status["reason_codes"])
        assert "private_store_root_anchor_enrollment_unavailable" in status["reason_codes"]




def test_outbound_approval_is_attempt_scoped_and_independent_of_parent_turn(tmp_path):
    import threading
    import time
    from pathlib import Path
    from paths import AgentPaths
    from server import WsBridge

    root = tmp_path / "project"
    (root / "evolve").mkdir(parents=True)
    (root / "evolve" / "_index.toml").write_text("format = 1\n", encoding="utf-8")
    paths = AgentPaths.from_root(root)
    paths.data.mkdir(parents=True)
    session = type("SessionRef", (), {"conversation_id": "session-a"})()
    events = []
    bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=2)
    bridge._session_provider = lambda: session
    identity = EvolutionIdentity("session-a", "task-a", "attempt-a", "run-a", 4)
    assert bridge.register_self_evolution_attempt(identity) is True

    answers = []
    waiter = threading.Thread(target=lambda: answers.append(
        bridge.confirm_self_evolution_outbound(identity, "Send request to configured model?")))
    waiter.start()
    deadline = time.monotonic() + 1
    while not events and time.monotonic() < deadline:
        time.sleep(0.005)
    event = events[0]
    assert event["confirm_kind"] == "self_evolution_outbound"
    assert event["session_id"] == identity.session_id
    assert event["task_id"] == identity.task_id
    assert event["attempt_id"] == identity.attempt_id
    assert "run_id" not in event  # not a main-turn confirmation
    assert bridge.deliver_self_evolution_outbound_confirm(
        event["request_id"], "y", task_id="another-task", attempt_id=identity.attempt_id) is False
    # The established Desktop response carries request_id + choice only; the
    # server-side one-use mapping supplies the exact attempt binding.
    assert bridge.deliver_self_evolution_outbound_confirm(event["request_id"], "y") is True
    waiter.join(timeout=1)
    assert not waiter.is_alive()
    assert answers == ["y"]


def test_outbound_approval_fails_closed_after_connection_close(tmp_path):
    from paths import AgentPaths
    from server import WsBridge

    root = tmp_path / "project"
    (root / "evolve").mkdir(parents=True)
    (root / "evolve" / "_index.toml").write_text("format = 1\n", encoding="utf-8")
    paths = AgentPaths.from_root(root)
    paths.data.mkdir(parents=True)
    bridge = WsBridge(emit=lambda _event: None, paths=paths, confirm_timeout=0.05)
    bridge._session_provider = lambda: type("SessionRef", (), {"conversation_id": "session-a"})()
    identity = EvolutionIdentity("session-a", "task-a", "attempt-a", "run-a", 4)
    assert bridge.register_self_evolution_attempt(identity) is True
    bridge.mark_connection_closed()
    assert bridge.confirm_self_evolution_outbound(identity, "Send?") == "n"


def test_experimental_static_ast_cli_is_off_by_default_and_explicitly_selectable():
    from server import build_parser

    default = build_parser().parse_args([])
    assert default.experimental_self_evolution_static_ast is False
    assert default.self_evolution_disposable_target is None
    assert default.self_evolution_enroll_trust_root is False

    selected = build_parser().parse_args([
        "--experimental-self-evolution-static-ast",
        "--self-evolution-disposable-target", "C:/isolated/disposable-clone",
        "--self-evolution-enroll-trust-root",
    ])
    assert selected.experimental_self_evolution_static_ast is True
    assert selected.self_evolution_disposable_target == "C:/isolated/disposable-clone"
    assert selected.self_evolution_enroll_trust_root is True


def test_trusted_target_approval_binds_exact_clean_standalone_clone(tmp_path):
    import subprocess
    from paths import AgentPaths
    from server import _approve_disposable_target_path

    app = tmp_path / "application"
    (app / "evolve").mkdir(parents=True)
    (app / "evolve" / "_index.toml").write_bytes(b"format = 1\n")
    (app / "data").mkdir()
    paths = AgentPaths.from_root(app)
    target = tmp_path / "purpose-owned-clone"
    target.mkdir()
    subprocess.run(["git", "init", str(target)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "isolated@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.name", "isolated fixture"], check=True)
    (target / "agent.py").write_bytes(b"value = 1\n")
    subprocess.run(["git", "-C", str(target), "add", "agent.py"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "-m", "isolated baseline"], check=True, capture_output=True)

    approved = _approve_disposable_target_path(paths, target)

    assert approved == target.resolve()
    assert (target / ".self-evolution-experimental-target.json").is_file()
    assert _approve_disposable_target_path(paths, target) == target.resolve()


def test_apply_result_echoes_review_identity_and_preserves_partial_status():
    from contextlib import nullcontext
    from types import SimpleNamespace
    import threading
    from server import WsSessionHandler

    identity = EvolutionIdentity("session-a", "task-a", "attempt-a", "run-a", 7)
    snapshot = EvolutionSnapshot(identity, "awaiting_review", 9)
    review_binding = {"session_id": "session-a", "task_id": "task-a", "attempt_id": "attempt-a",
                      "run_id": "run-a", "turn_epoch": 7, "snapshot_revision": 9,
                      "apply_eligible_paths": ("agent.py",), "apply_available": True}
    class NativeBoundHost:
        experimental_apply_target_ready = True
        def get_snapshot(self, task_id, *, session_id):
            return snapshot if task_id == identity.task_id and session_id == identity.session_id else None
        def apply_reviewed_candidate_for_session(self, task_id, attempt_id, *, proposed_paths):
            assert proposed_paths == ("agent.py",)
            return {"task_id": task_id, "status": "partial_failure", "written_paths": ("agent.py",),
                    "pending_paths": ("next.py",)}
    host = NativeBoundHost()
    integration = SimpleNamespace(_trusted_experimental_host=lambda: host, native_apply_available=True)
    bridge = SimpleNamespace(self_evolution_chat=integration, _connection_closed=threading.Event(),
                             get_self_evolution_review_binding=lambda *_args: review_binding)
    repl = SimpleNamespace(session=SimpleNamespace(conversation_id="session-a"))
    handler = object.__new__(WsSessionHandler)
    handler._bind_self_evolution_read_context = lambda _repl, _bridge: nullcontext()

    result = handler._deliver_self_evolution_apply(
        repl, bridge, "task-a", "attempt-a", "00000000-0000-4000-8000-000000000099", ("agent.py",),
    )

    assert result == {
        "type": "self_evolution.apply.result", "session_id": "session-a", "task_id": "task-a",
        "attempt_id": "attempt-a", "run_id": "run-a", "turn_epoch": 7,
        "snapshot_revision": 9, "request_id": "00000000-0000-4000-8000-000000000099",
        "status": "partial",
    }


def test_apply_unavailable_echoes_exact_review_identity(tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace
    import threading
    from paths import AgentPaths
    from server import WsSessionHandler

    root = tmp_path / "project"
    (root / "evolve").mkdir(parents=True)
    (root / "evolve" / "_index.toml").write_text("format = 1\n", encoding="utf-8")
    paths = AgentPaths.from_root(root)
    paths.data.mkdir(parents=True)
    identity = EvolutionIdentity("session-a", "task-a", "attempt-a", "run-a", 7)
    snapshot = EvolutionSnapshot(identity, "awaiting_review", 9)
    review_binding = {"session_id": "session-a", "task_id": "task-a", "attempt_id": "attempt-a",
                      "run_id": "run-a", "turn_epoch": 7, "snapshot_revision": 9,
                      "apply_eligible_paths": ("agent.py",), "apply_available": True}
    host = SimpleNamespace(
        experimental_apply_target_ready=False,
        get_snapshot=lambda *_args, **_kwargs: snapshot,
    )
    integration = SimpleNamespace(_trusted_experimental_host=lambda: host, native_apply_available=False)
    bridge = SimpleNamespace(self_evolution_chat=integration, _connection_closed=threading.Event(),
                             get_self_evolution_review_binding=lambda *_args: review_binding)
    repl = SimpleNamespace(session=SimpleNamespace(conversation_id="session-a"))
    handler = object.__new__(WsSessionHandler)
    handler._bind_self_evolution_read_context = lambda _repl, _bridge: nullcontext()

    result = handler._deliver_self_evolution_apply(
        repl, bridge, "task-a", "attempt-a", "00000000-0000-4000-8000-000000000099", ("agent.py",),
    )

    assert result == {
        "type": "self_evolution.apply.unavailable", "session_id": "session-a",
        "task_id": "task-a", "attempt_id": "attempt-a", "run_id": "run-a",
        "turn_epoch": 7, "snapshot_revision": 9,
        "request_id": "00000000-0000-4000-8000-000000000099", "reason": "unsupported",
    }


def test_static_ast_allowlist_matches_merge_executor_source_roots(tmp_path):
    from server import _experimental_static_ast_paths

    root = tmp_path / "repo"
    for relative in ("agent-core/server.py", "desktop/main.py", "docs/entry.py",
                     "protocol/api.py", "scripts/check.py", "evolve/tools/tool.py",
                     "evolve/skills/skill.py", "evolve/tool-catalog/catalog.py",
                     "src.py", "tests/test_sample.py", "workspace/app.py"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"value = 1\n")
    import subprocess
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "isolated@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "isolated fixture"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "tracked fixtures"], check=True, capture_output=True)

    assert _experimental_static_ast_paths(root) == (
        "agent-core/server.py", "desktop/main.py", "docs/entry.py",
        "evolve/skills/skill.py", "evolve/tool-catalog/catalog.py",
        "evolve/tools/tool.py", "protocol/api.py", "scripts/check.py",
    )


def test_experimental_sidecar_rejects_nonloopback_and_mixed_bootstrap_before_startup():
    import asyncio
    from server import run_server

    base = {"experimental_self_evolution_static_ast": True,
            "self_evolution_disposable_target": "C:/isolated/clone"}
    assert asyncio.run(run_server("0.0.0.0", 8765, **base)) == 4
    assert asyncio.run(run_server("127.0.0.1", 8765, evolution_bootstrap={}, **base)) == 4
    assert asyncio.run(run_server("127.0.0.1", 8765, fixed_host_layout={}, **base)) == 4
    assert asyncio.run(run_server("127.0.0.1", 8765, managed_readonly_context=object(), **base)) == 4
