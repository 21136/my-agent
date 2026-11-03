from __future__ import annotations

import asyncio
import sys
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import server
from paths import AgentPaths
from session import Session, SessionMeta
from server import WsBridge, _settle_work_record_run
from work_card_ledger import WorkCardLedger
from work_card_publish_outbox import WorkCardPublicationOutbox
from work_records import WorkRecordStore


def _project_fixture(tmp_path):
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace",
                       data=tmp_path / "data", evolve=tmp_path / "evolve")
    session_dir = paths.data / "sessions" / "abc12345-1234-4abc-8def-1234567890ab"
    session_dir.mkdir(parents=True)
    session = Session(
        conversation_id="abc12345-1234-4abc-8def-1234567890ab", session_dir=session_dir,
        goal="", meta=SessionMeta(project_id="project-a"), messages=[], paths=paths,
    )
    store = WorkRecordStore(session)
    item = store.create_item(title="Persisted task", scope={"kind": "project", "project_id": "project-a"})
    req = store.add_requirement(item["item_id"], "Persisted requirement", event_id="source-requirement")
    return session, store, item["item_id"], req["requirement_id"]


def test_legacy_project_result_replay_reports_no_intent_without_claiming_incomplete(tmp_path):
    from work_record_settlement import BoundExecution, ExecutionReceipt, WorkRecordSettlementAdapter

    session, store, item_id, requirement_id = _project_fixture(tmp_path)
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    run = bridge.lifecycle.snapshot()
    bridge.lifecycle_conversation_id = session.conversation_id
    store.begin_requirement_run(item_id, requirement_id, run_id=run.run_id,
                                expected_current_run_id=None)
    assert bridge.finish_execution("completed", ok=True, run_id=run.run_id)
    terminal = bridge.lifecycle.snapshot()
    binding = BoundExecution(session.conversation_id, item_id, requirement_id, run.run_id)
    receipt = ExecutionReceipt(
        session_id=session.conversation_id, item_id=item_id, requirement_id=requirement_id,
        run_id=run.run_id, sequence=terminal.sequence, outcome="succeeded", finish_reason="completed",
    )
    first = WorkRecordSettlementAdapter(store, binding).settle(receipt)
    assert first.status == "recorded"
    assert store.list_publication_intents() == []

    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    settled = _settle_work_record_run(
        current_session=session, bridge=bridge, store=store, binding=binding,
        emit=events.append, repl=repl,
    )

    assert settled is True
    assert store.list_publication_intents() == []
    notices = [event["text"] for event in events if event.get("type") == "turn.notice"]
    assert any("未创建或改写发布 intent" in text and "未自动追溯补发" in text for text in notices)
    assert any("不表示工作未完成" in text and "未知、未经验证" in text for text in notices)
    assert not any(event.get("type") == "work-card.present" for event in events)


def test_recorded_host_settlement_publishes_persisted_project_card_over_real_bridge(tmp_path, monkeypatch):
    session, store, item_id, requirement_id = _project_fixture(tmp_path)
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    initial = bridge.lifecycle.snapshot()
    bridge.lifecycle_conversation_id = session.conversation_id
    store.begin_requirement_run(item_id, requirement_id, run_id=initial.run_id,
                               expected_current_run_id=None)
    assert bridge.finish_execution("completed", ok=True, run_id=initial.run_id)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    monkeypatch.setattr(server, "PROJECT_WORK_CARD_MERGE_SECONDS", 0.2, raising=False)

    async def settle_and_wait():
        assert _settle_work_record_run(
            current_session=session, bridge=bridge, store=store,
            binding=SimpleNamespace(session_id=session.conversation_id, item_id=item_id,
                                    requirement_id=requirement_id, run_id=initial.run_id),
            emit=events.append, repl=repl,
        )
        # Result persistence is synchronous and visible before the card timer fires.
        assert store.get_item(item_id)["runs"]
        assert WorkCardLedger(session, store).list_cards() == []
        await asyncio.sleep(0.005)
        second = store.add_requirement(item_id, "Second persisted requirement", event_id="second-source")
        assert bridge.begin_turn(mode="ordinary")
        second_run = bridge.lifecycle.snapshot()
        store.begin_requirement_run(item_id, second["requirement_id"], run_id=second_run.run_id,
                                   expected_current_run_id=None)
        assert bridge.finish_execution("completed", ok=True, run_id=second_run.run_id)
        assert _settle_work_record_run(
            current_session=session, bridge=bridge, store=store,
            binding=SimpleNamespace(session_id=session.conversation_id, item_id=item_id,
                                    requirement_id=second["requirement_id"], run_id=second_run.run_id),
            emit=events.append, repl=repl,
        )
        await asyncio.sleep(0.25)

    asyncio.run(settle_and_wait())

    present = [event for event in events if event.get("type") == "work-card.present"]
    assert len(present) == 1, events
    assert present[0]["session_id"] == session.conversation_id
    assert present[0]["card"]["snapshot"]["project_id"] == "project-a"
    requirements = present[0]["card"]["snapshot"]["cards"][0]["requirements"]
    assert len(requirements) == 2
    assert all(row["status"] == "unknown" for row in requirements)
    snapshot = present[0]["card"]["snapshot"]
    latest = store.get_item(item_id)["runs"][-1]
    assert snapshot["change_focus"] == {
        "item_id": item_id, "requirement_id": latest["requirement_id"],
        "result_id": latest["result_id"],
    }
    assert len(WorkCardLedger(session, store).list_cards()) == 1
    assert any(message.get("role") == "work_card_presentation" for message in session.messages)






def test_atomic_settlement_write_failure_leaves_neither_result_nor_publication_intent(tmp_path, monkeypatch):
    session, store, item_id, requirement_id = _project_fixture(tmp_path)
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    run = bridge.lifecycle.snapshot()
    bridge.lifecycle_conversation_id = session.conversation_id
    store.begin_requirement_run(item_id, requirement_id, run_id=run.run_id,
                                expected_current_run_id=None)
    assert bridge.finish_execution("completed", ok=True, run_id=run.run_id)
    monkeypatch.setattr(store, "_publish",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("synthetic atomic write failure")))
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))

    assert not _settle_work_record_run(
        current_session=session, bridge=bridge, store=store,
        binding=SimpleNamespace(session_id=session.conversation_id, item_id=item_id,
                                requirement_id=requirement_id, run_id=run.run_id),
        emit=events.append, repl=repl,
    )

    assert store.get_item(item_id)["runs"] == []
    assert store.list_publication_intents() == []
    assert bridge._work_card_update_coalescer is None
    assert not any(event.get("type") == "work-card.present" for event in events)
    assert not any(message.get("role") == "work_card_presentation" for message in session.messages)



def test_disconnect_before_window_flush_recovers_only_pending_project_card(tmp_path, monkeypatch):
    from work_record_cards import build_project_work_record_cards

    session, store, item_id, requirement_id = _project_fixture(tmp_path)
    historical = WorkCardLedger(session, store).save_snapshot(
        str(uuid.uuid4()), build_project_work_record_cards(store, "project-a"),
    )
    assert historical.status == "saved"
    historical_card_id = historical.card_id
    assert "change_focus" not in historical.record["snapshot"]
    session.save()
    first_events = []
    first_bridge = WsBridge(emit=first_events.append, paths=session.paths)
    assert first_bridge.begin_turn(mode="ordinary")
    run = first_bridge.lifecycle.snapshot()
    first_bridge.lifecycle_conversation_id = session.conversation_id
    store.begin_requirement_run(item_id, requirement_id, run_id=run.run_id,
                                expected_current_run_id=None)
    assert first_bridge.finish_execution("completed", ok=True, run_id=run.run_id)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    monkeypatch.setattr(server, "PROJECT_WORK_CARD_MERGE_SECONDS", 30.0, raising=False)

    async def settle_before_disconnect():
        assert _settle_work_record_run(
            current_session=session, bridge=first_bridge, store=store,
            binding=SimpleNamespace(session_id=session.conversation_id, item_id=item_id,
                                    requirement_id=requirement_id, run_id=run.run_id),
            emit=first_events.append, repl=repl,
        )

    asyncio.run(settle_before_disconnect())
    assert not any(event.get("type") == "work-card.present" for event in first_events)
    first_bridge._work_card_update_coalescer.close()

    restored = Session.load(session.paths, session.conversation_id)
    restored_events = []
    restored_bridge = WsBridge(emit=restored_events.append, paths=restored.paths)
    restored_repl = SimpleNamespace(session=restored, agent=SimpleNamespace(session=restored))
    import work_records
    actual_now = work_records._monotonic
    monkeypatch.setattr(work_records, "_monotonic", lambda: actual_now() + 31.0)
    replayed = server._emit_pending_work_card_references(restored_repl, restored_bridge)

    assert replayed == 1
    card_events = [event for event in restored_events if event.get("type") == "work-card.present"]
    assert len(card_events) == 1
    assert card_events[0]["session_id"] == restored.conversation_id
    assert card_events[0]["card"]["snapshot"]["project_id"] == "project-a"
    assert card_events[0]["card"]["snapshot"]["change_focus"] == {
        "item_id": item_id, "requirement_id": requirement_id,
        "result_id": store.get_item(item_id)["runs"][-1]["result_id"],
    }
    restored_store = WorkRecordStore(restored)
    assert len(WorkCardLedger(restored, restored_store).list_cards()) == 2
    assert card_events[0]["card_id"] != historical_card_id
    assert len(restored_store.get_item(item_id)["runs"]) == 1
    assert server._emit_pending_work_card_references(restored_repl, restored_bridge) == 1
    retries = [event for event in restored_events if event.get("type") == "work-card.present"]
    assert [event["card_id"] for event in retries] == [card_events[0]["card_id"]], "same connection deduplicates the durable presentation id"
    assert len([row for row in restored.messages if row.get("role") == "work_card_presentation"]) == 1
    assert any(row["card_id"] == card_events[0]["card_id"]
               for row in WorkCardLedger(restored, restored_store).list_cards())
    assert WorkCardPublicationOutbox(restored, restored_store).list_pending() == []



def test_uncertain_ws_enqueue_recovers_same_durable_card_reference(tmp_path, monkeypatch):
    session, store, item_id, requirement_id = _project_fixture(tmp_path)
    session.save()
    initial_events = []
    bridge = WsBridge(emit=initial_events.append, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    run = bridge.lifecycle.snapshot()
    bridge.lifecycle_conversation_id = session.conversation_id
    store.begin_requirement_run(item_id, requirement_id, run_id=run.run_id,
                                expected_current_run_id=None)
    assert bridge.finish_execution("completed", ok=True, run_id=run.run_id)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    monkeypatch.setattr(server, "PROJECT_WORK_CARD_MERGE_SECONDS", 30.0, raising=False)

    async def settle():
        assert _settle_work_record_run(
            current_session=session, bridge=bridge, store=store,
            binding=SimpleNamespace(session_id=session.conversation_id, item_id=item_id,
                                    requirement_id=requirement_id, run_id=run.run_id),
            emit=initial_events.append, repl=repl,
        )

    asyncio.run(settle())
    bridge._work_card_update_coalescer.close()
    failed_events = []
    def fail_card_once(event):
        if event.get("type") == "work-card.present":
            raise OSError("synthetic socket enqueue failure")
        failed_events.append(event)
    restored = Session.load(session.paths, session.conversation_id)
    failed_bridge = WsBridge(emit=fail_card_once, paths=restored.paths)
    failed_repl = SimpleNamespace(session=restored, agent=SimpleNamespace(session=restored))
    import work_records
    actual_now = work_records._monotonic
    monkeypatch.setattr(work_records, "_monotonic", lambda: actual_now() + 31.0)
    assert server._emit_pending_work_card_references(failed_repl, failed_bridge) == 0
    pending = WorkCardPublicationOutbox(restored, WorkRecordStore(restored)).list_pending()
    assert len(pending) == 1 and pending[0]["card_id"]
    assert not any(event.get("type") == "work-card.present" for event in failed_events)

    delivered_events = []
    retry_session = Session.load(session.paths, session.conversation_id)
    retry_bridge = WsBridge(emit=delivered_events.append, paths=retry_session.paths)
    retry_repl = SimpleNamespace(session=retry_session, agent=SimpleNamespace(session=retry_session))
    assert server._emit_pending_work_card_references(retry_repl, retry_bridge) == 1
    card_event = next(event for event in delivered_events if event.get("type") == "work-card.present")
    assert card_event["card_id"] == pending[0]["card_id"]
    assert card_event["presentation_id"] == server._work_card_presentation_id(
        retry_session.conversation_id, run.run_id, pending[0]["card_id"],
    )
    retry_store = WorkRecordStore(retry_session)
    assert WorkCardPublicationOutbox(retry_session, retry_store).list_pending() == []
    assert len(retry_store.get_item(item_id)["runs"]) == 1


def test_outbox_persistence_uncertainty_warns_without_scheduling_or_reexecuting(tmp_path, monkeypatch):
    session, store, item_id, requirement_id = _project_fixture(tmp_path)
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    run = bridge.lifecycle.snapshot()
    bridge.lifecycle_conversation_id = session.conversation_id
    store.begin_requirement_run(item_id, requirement_id, run_id=run.run_id,
                                expected_current_run_id=None)
    assert bridge.finish_execution("completed", ok=True, run_id=run.run_id)
    monkeypatch.setattr(store, "_publish",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("synthetic atomic write failure")))
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))

    assert not _settle_work_record_run(
        current_session=session, bridge=bridge, store=store,
        binding=SimpleNamespace(session_id=session.conversation_id, item_id=item_id,
                                requirement_id=requirement_id, run_id=run.run_id),
        emit=events.append, repl=repl,
    )

    assert store.get_item(item_id)["runs"] == []
    assert store.list_publication_intents() == []
    assert bridge._work_card_update_coalescer is None
    assert not any(event.get("type") == "work-card.present" for event in events)
    warnings = [event.get("text", "") for event in events
                if event.get("type") == "turn.notice" and event.get("level") == "warn"]
    assert any("工作记录结果未能确认保存" in text and "不会重做" in text for text in warnings)



def test_reconnect_resumes_original_deadline_and_merges_new_same_project_settlement(tmp_path, monkeypatch):
    import work_records
    from work_card_update_coalescer import ProjectWorkCardUpdateCoalescer

    class _TimerHandle:
        def __init__(self, when, callback):
            self.when = when
            self.callback = callback
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    class _ControlledLoop:
        def __init__(self):
            self.now = 0.0
            self.handles = []

        def call_later(self, delay, callback):
            handle = _TimerHandle(self.now + float(delay), callback)
            self.handles.append(handle)
            return handle

        def next_deadline(self):
            active = [handle.when for handle in self.handles if not handle.cancelled]
            return min(active) if active else None

        def advance(self, seconds):
            target = self.now + seconds
            while True:
                ready = [handle for handle in self.handles
                         if not handle.cancelled and handle.when <= target]
                if not ready:
                    break
                handle = min(ready, key=lambda entry: entry.when)
                self.handles.remove(handle)
                self.now = handle.when
                handle.callback()
            self.now = target

    session, store, item_id, first_requirement = _project_fixture(tmp_path)
    session.save()
    publication_clock = {"now": 1000.0}
    monkeypatch.setattr(work_records, "_monotonic", lambda: publication_clock["now"])
    monkeypatch.setattr(server, "PROJECT_WORK_CARD_MERGE_SECONDS", 30.0, raising=False)
    controlled_loop = _ControlledLoop()

    class _ControlledCoalescer(ProjectWorkCardUpdateCoalescer):
        def __init__(self, *, delay_seconds=30.0, loop=None):
            super().__init__(delay_seconds=delay_seconds, loop=controlled_loop)

    import work_card_update_coalescer
    monkeypatch.setattr(work_card_update_coalescer, "ProjectWorkCardUpdateCoalescer", _ControlledCoalescer)

    first_events = []
    first_bridge = WsBridge(emit=first_events.append, paths=session.paths)
    assert first_bridge.begin_turn(mode="ordinary")
    first_run = first_bridge.lifecycle.snapshot()
    first_bridge.lifecycle_conversation_id = session.conversation_id
    store.begin_requirement_run(item_id, first_requirement, run_id=first_run.run_id,
                                expected_current_run_id=None)
    assert first_bridge.finish_execution("completed", ok=True, run_id=first_run.run_id)
    first_repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))

    async def first_settlement():
        assert _settle_work_record_run(
            current_session=session, bridge=first_bridge, store=store,
            binding=SimpleNamespace(session_id=session.conversation_id, item_id=item_id,
                                    requirement_id=first_requirement, run_id=first_run.run_id),
            emit=first_events.append, repl=first_repl,
        )

    asyncio.run(first_settlement())
    first_intent = WorkCardPublicationOutbox(session, store).list_pending()[0]
    first_deadline = first_intent["deadline_monotonic"]
    assert first_deadline == 1030.0
    assert WorkCardPublicationOutbox(session, store).remaining_seconds(first_intent) == 30.0
    assert not any(event.get("type") == "work-card.present" for event in first_events)
    first_bridge._work_card_update_coalescer.close()

    restored = Session.load(session.paths, session.conversation_id)
    restored_store = WorkRecordStore(restored)
    restored_events = []
    restored_bridge = WsBridge(emit=restored_events.append, paths=restored.paths)
    restored_repl = SimpleNamespace(session=restored, agent=SimpleNamespace(session=restored))
    publication_clock["now"] = 1005.0

    async def reconnect_then_settle_and_advance_deadline():
        assert server._emit_pending_work_card_references(restored_repl, restored_bridge) == 0
        assert not any(event.get("type") == "work-card.present" for event in restored_events)
        resumed_intent = WorkCardPublicationOutbox(restored, restored_store).list_pending()[0]
        assert resumed_intent["deadline_monotonic"] == first_deadline
        assert WorkCardPublicationOutbox(restored, restored_store).remaining_seconds(resumed_intent) == 25.0
        assert controlled_loop.next_deadline() == 25.0

        second = restored_store.add_requirement(
            item_id, "Second requirement during recovered window", event_id="second-during-window",
        )
        assert restored_bridge.begin_turn(mode="ordinary")
        second_run = restored_bridge.lifecycle.snapshot()
        restored_bridge.lifecycle_conversation_id = restored.conversation_id
        restored_store.begin_requirement_run(item_id, second["requirement_id"],
                                             run_id=second_run.run_id,
                                             expected_current_run_id=None)
        assert restored_bridge.finish_execution("completed", ok=True, run_id=second_run.run_id)
        publication_clock["now"] = 1007.0
        assert _settle_work_record_run(
            current_session=restored, bridge=restored_bridge, store=restored_store,
            binding=SimpleNamespace(session_id=restored.conversation_id, item_id=item_id,
                                    requirement_id=second["requirement_id"], run_id=second_run.run_id),
            emit=restored_events.append, repl=restored_repl,
        )
        merged_intent = WorkCardPublicationOutbox(restored, restored_store).list_pending()[0]
        assert merged_intent["deadline_monotonic"] == first_deadline
        assert merged_intent["latest_run_id"] == second_run.run_id
        assert WorkCardPublicationOutbox(restored, restored_store).remaining_seconds(merged_intent) == 23.0
        assert controlled_loop.next_deadline() == 25.0, "later settlement must not extend the first deadline"

        controlled_loop.advance(24.999)
        assert not any(event.get("type") == "work-card.present" for event in restored_events)
        publication_clock["now"] = first_deadline
        controlled_loop.advance(0.001)

    asyncio.run(reconnect_then_settle_and_advance_deadline())

    cards = [event for event in restored_events if event.get("type") == "work-card.present"]
    assert len(cards) == 1
    requirements = cards[0]["card"]["snapshot"]["cards"][0]["requirements"]
    assert len(requirements) == 2
    assert all(row["status"] == "unknown" for row in requirements)
    assert len(WorkCardLedger(restored, restored_store).list_cards()) == 1
    assert len(restored_store.get_item(item_id)["runs"]) == 2
    assert WorkCardPublicationOutbox(restored, restored_store).list_pending() == []


def test_crash_after_atomic_result_replace_recovers_server_bound_intent(tmp_path, monkeypatch):
    session, store, item_id, requirement_id = _project_fixture(tmp_path)
    session.save()
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    run = bridge.lifecycle.snapshot()
    bridge.lifecycle_conversation_id = session.conversation_id
    store.begin_requirement_run(item_id, requirement_id, run_id=run.run_id,
                                expected_current_run_id=None)
    assert bridge.finish_execution("completed", ok=True, run_id=run.run_id)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    original_publish = WorkRecordStore._publish

    class SimulatedProcessCrash(BaseException):
        pass

    def replace_then_crash(bound_store, data, *, previous=None):
        original_publish(bound_store, data, previous=previous)
        raise SimulatedProcessCrash()

    monkeypatch.setattr(WorkRecordStore, "_publish", replace_then_crash)
    try:
        _settle_work_record_run(
            current_session=session, bridge=bridge, store=store,
            binding=SimpleNamespace(session_id=session.conversation_id, item_id=item_id,
                                    requirement_id=requirement_id, run_id=run.run_id),
            emit=events.append, repl=repl,
        )
        raise AssertionError("injected process crash was not raised")
    except SimulatedProcessCrash:
        pass
    monkeypatch.setattr(WorkRecordStore, "_publish", original_publish)

    # A new Session/store models a fresh server process reading only committed disk state.
    restored = Session.load(session.paths, session.conversation_id)
    restored_events = []
    restored_bridge = WsBridge(emit=restored_events.append, paths=restored.paths)
    restored_repl = SimpleNamespace(session=restored, agent=SimpleNamespace(session=restored))
    restored_store = WorkRecordStore(restored)
    outbox = WorkCardPublicationOutbox(restored, restored_store)
    before_replay = outbox.list_pending()
    assert len(before_replay) == 1
    committed_run = restored_store.get_item(item_id)["runs"][0]
    restored_store.record_server_settlement_if_current_run(
        item_id, requirement_id, run_id=committed_run["run_id"],
        result_id=committed_run["result_id"], status=committed_run["status"],
        summary=committed_run["result_summary"], verification=committed_run["verification"],
        sources=committed_run["sources"],
    )
    assert outbox.list_pending() == before_replay
    import work_records
    monkeypatch.setattr(work_records, "_PUBLICATION_PROCESS_INSTANCE_ID", str(uuid.uuid4()))
    recovered = server._emit_pending_work_card_references(restored_repl, restored_bridge)

    assert recovered == 1
    card = next(event for event in restored_events if event.get("type") == "work-card.present")
    assert card["session_id"] == restored.conversation_id
    assert card["card"]["snapshot"]["project_id"] == "project-a"
    assert len(WorkRecordStore(restored).get_item(item_id)["runs"]) == 1
    assert (restored.session_dir / "work-card-publish-outbox.v1.json").exists() is False
    assert len(WorkRecordStore(restored).list_publication_intents()) == 0
    assert len(WorkCardLedger(restored, WorkRecordStore(restored)).list_cards()) == 1



def test_real_subprocess_server_settlement_is_recovered_by_fresh_process(tmp_path):
    session, store, item_id, requirement_id = _project_fixture(tmp_path)
    session.save()
    core = Path(__file__).resolve().parents[1]
    child_code = r"""
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from paths import AgentPaths
from session import Session
from work_records import WorkRecordStore
from server import WsBridge, _settle_work_record_run
root = Path(sys.argv[2])
paths = AgentPaths(agent_root=root, workspace=root/'workspace', data=root/'data', evolve=root/'evolve')
session = Session.load(paths, sys.argv[3])
store = WorkRecordStore(session)
item_id, requirement_id = sys.argv[4], sys.argv[5]
events = []
bridge = WsBridge(emit=events.append, paths=paths)
assert bridge.begin_turn(mode='ordinary')
run = bridge.lifecycle.snapshot()
bridge.lifecycle_conversation_id = session.conversation_id
store.begin_requirement_run(item_id, requirement_id, run_id=run.run_id, expected_current_run_id=None)
assert bridge.finish_execution('completed', ok=True, run_id=run.run_id)
bridge.end_turn()
binding = SimpleNamespace(session_id=session.conversation_id, item_id=item_id,
                          requirement_id=requirement_id, run_id=run.run_id)
assert _settle_work_record_run(current_session=session, bridge=bridge, store=store,
                               binding=binding, emit=events.append)
print(run.run_id)
"""
    child = subprocess.run(
        [sys.executable, "-c", child_code, str(core), str(tmp_path),
         session.conversation_id, item_id, requirement_id],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert child.returncode == 0, child.stderr
    child_run_id = child.stdout.strip().splitlines()[-1]

    restored = Session.load(session.paths, session.conversation_id)
    restored_store = WorkRecordStore(restored)
    pending = WorkCardPublicationOutbox(restored, restored_store).list_pending()
    assert len(pending) == 1 and pending[0]["latest_run_id"] == child_run_id
    restored_events = []
    restored_bridge = WsBridge(emit=restored_events.append, paths=restored.paths)
    restored_repl = SimpleNamespace(session=restored, agent=SimpleNamespace(session=restored))
    assert server._emit_pending_work_card_references(restored_repl, restored_bridge) == 1
    card = next(event for event in restored_events if event.get("type") == "work-card.present")
    assert card["session_id"] == restored.conversation_id
    assert card["card"]["snapshot"]["project_id"] == "project-a"
    assert len(restored_store.get_item(item_id)["runs"]) == 1



def test_concurrent_process_settlements_merge_into_one_recoverable_project_intent(tmp_path):
    session, _store, item_id, first_requirement = _project_fixture(tmp_path)
    second = WorkRecordStore(session).add_requirement(
        item_id, "Concurrent second requirement", event_id="concurrent-second-source",
    )
    session.save()
    core = Path(__file__).resolve().parents[1]
    child_code = r"""
import asyncio, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from paths import AgentPaths
from session import Session
from work_records import WorkRecordStore
from server import WsBridge, _settle_work_record_run
root = Path(sys.argv[2])
paths = AgentPaths(agent_root=root, workspace=root/'workspace', data=root/'data', evolve=root/'evolve')
session = Session.load(paths, sys.argv[3])
store = WorkRecordStore(session)
item_id, requirement_id = sys.argv[4], sys.argv[5]
async def main():
    bridge = WsBridge(emit=lambda _event: None, paths=paths)
    assert bridge.begin_turn(mode='ordinary')
    run = bridge.lifecycle.snapshot()
    bridge.lifecycle_conversation_id = session.conversation_id
    store.begin_requirement_run(item_id, requirement_id, run_id=run.run_id, expected_current_run_id=None)
    assert bridge.finish_execution('completed', ok=True, run_id=run.run_id)
    bridge.end_turn()
    binding = SimpleNamespace(session_id=session.conversation_id, item_id=item_id,
                              requirement_id=requirement_id, run_id=run.run_id)
    assert _settle_work_record_run(current_session=session, bridge=bridge, store=store,
                                   binding=binding, emit=lambda _event: None)
    print(run.run_id)
asyncio.run(main())
"""
    args = [sys.executable, "-c", child_code, str(core), str(tmp_path),
            session.conversation_id, item_id]
    procs = [
        subprocess.Popen(args + [requirement_id], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for requirement_id in (first_requirement, second["requirement_id"])
    ]
    results = [proc.communicate(timeout=25) + (proc.returncode,) for proc in procs]
    assert all(code == 0 for _stdout, stderr, code in results), results

    restored = Session.load(session.paths, session.conversation_id)
    restored_store = WorkRecordStore(restored)
    item = restored_store.get_item(item_id)
    assert len(item["runs"]) == 2
    pending = WorkCardPublicationOutbox(restored, restored_store).list_pending()
    assert len(pending) == 1 and pending[0]["generation"] == 2
    assert pending[0]["latest_run_id"] in {
        stdout.strip().splitlines()[-1] for stdout, _stderr, _code in results
    }

    events = []
    bridge = WsBridge(emit=events.append, paths=restored.paths)
    repl = SimpleNamespace(session=restored, agent=SimpleNamespace(session=restored))
    assert server._emit_pending_work_card_references(repl, bridge) == 1
    cards = [event for event in events if event.get("type") == "work-card.present"]
    assert len(cards) == 1
    requirements = cards[0]["card"]["snapshot"]["cards"][0]["requirements"]
    assert len(requirements) == 2
    assert all(row["status"] == "unknown" for row in requirements)
    assert len(WorkRecordStore(restored).get_item(item_id)["runs"]) == 2



def test_server_cancelled_project_run_commits_only_recoverable_card_intent(tmp_path):
    session, store, item_id, requirement_id = _project_fixture(tmp_path)
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    run = bridge.lifecycle.snapshot()
    bridge.lifecycle_conversation_id = session.conversation_id
    store.begin_requirement_run(item_id, requirement_id, run_id=run.run_id,
                                expected_current_run_id=None)
    assert bridge.finish_execution("cancelled", ok=False, run_id=run.run_id)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))

    assert _settle_work_record_run(
        current_session=session, bridge=bridge, store=store,
        binding=SimpleNamespace(session_id=session.conversation_id, item_id=item_id,
                                requirement_id=requirement_id, run_id=run.run_id),
        emit=events.append, repl=repl,
    )

    item = store.get_item(item_id)
    assert len(item["runs"]) == 1
    assert item["runs"][0]["status"] == "unknown"
    assert item["runs"][0]["verification"]["host_execution"]["finish_reason"] == "cancelled"
    intents = WorkCardPublicationOutbox(session, store).list_pending()
    assert len(intents) == 1 and intents[0]["latest_run_id"] == run.run_id
