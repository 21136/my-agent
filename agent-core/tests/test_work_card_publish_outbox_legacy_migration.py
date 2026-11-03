from __future__ import annotations

import hashlib
import json
import sys
import subprocess
import uuid
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import pytest
from paths import AgentPaths
from session import Session, SessionMeta
from work_card_publish_outbox import WorkCardPublicationOutbox, WorkCardPublishOutboxError
from work_record_settlement import (
    BoundExecution, ExecutionReceipt, WorkRecordSettlementAdapter,
)
from work_records import LEGACY_OUTBOX_DIGEST_KEY, WorkRecordStore


def _project_with_started_run(tmp_path):
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace",
                       data=tmp_path / "data", evolve=tmp_path / "evolve")
    session_id = str(uuid.uuid4())
    session_dir = paths.data / "sessions" / session_id
    session_dir.mkdir(parents=True)
    session = Session(conversation_id=session_id, session_dir=session_dir, goal="",
                      meta=SessionMeta(project_id="project-a"), messages=[], paths=paths)
    session.save()
    store = WorkRecordStore(session)
    item = store.create_item(title="Legacy project task",
                             scope={"kind": "project", "project_id": "project-a"})
    req = store.add_requirement(item["item_id"], "Legacy requirement", event_id="legacy-source")
    run_id = uuid.uuid4().hex
    store.begin_requirement_run(item["item_id"], req["requirement_id"], run_id=run_id,
                                expected_current_run_id=None)
    return session, store, item["item_id"], req["requirement_id"], run_id


def _legacy_project(tmp_path):
    session, store, item_id, requirement_id, run_id = _project_with_started_run(tmp_path)
    binding = BoundExecution(session.conversation_id, item_id, requirement_id, run_id)
    receipt = ExecutionReceipt(
        session_id=session.conversation_id, item_id=item_id, requirement_id=requirement_id,
        run_id=run_id, sequence=2, outcome="cancelled", finish_reason="cancelled",
    )
    result = WorkRecordSettlementAdapter(store, binding).settle(receipt)
    assert result.status == "recorded" and store.list_publication_intents() == []
    return session, store, item_id, requirement_id, run_id


def _project_with_atomic_pending(tmp_path):
    session, store, item_id, requirement_id, run_id = _project_with_started_run(tmp_path)
    binding = BoundExecution(session.conversation_id, item_id, requirement_id, run_id)
    receipt = ExecutionReceipt(
        session_id=session.conversation_id, item_id=item_id, requirement_id=requirement_id,
        run_id=run_id, sequence=2, outcome="cancelled", finish_reason="cancelled",
    )
    result = WorkRecordSettlementAdapter(store, binding).settle(receipt, publish_project_update=True)
    assert result.status == "recorded"
    pending = store.list_publication_intents()
    assert len(pending) == 1 and pending[0]["state"] == "pending"
    return session, store, item_id, requirement_id, run_id


def _legacy_pending(session, store, item_id, requirement_id, run_id):
    intent_id = str(uuid.uuid4())
    generation = 1
    request_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"my-agent-work-card-publication:{session.conversation_id}:project-a:{intent_id}:{generation}",
    ))
    payload = {
        "schema_version": 1,
        "conversation_id": session.conversation_id,
        "project_id": "project-a",
        "revision": 1,
        "items": [{
            "intent_id": intent_id,
            "project_id": "project-a",
            "first_run_id": run_id,
            "latest_item_id": uuid.UUID(item_id).hex,
            "latest_requirement_id": uuid.UUID(requirement_id).hex,
            "latest_run_id": run_id,
            "generation": generation,
            "request_id": request_id,
            "card_id": None,
            "state": "pending",
            "window_owner_id": str(uuid.uuid4()),
            "window_started_monotonic": 10.0,
            "deadline_monotonic": 40.0,
            "window_seconds": 30.0,
        }],
    }
    path = session.session_dir / "work-card-publish-outbox.v1.json"
    raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return path, raw, payload["items"][0]


def test_valid_legacy_pending_intent_migrates_once_without_touching_legacy_file(tmp_path):
    session, store, item_id, requirement_id, run_id = _legacy_project(tmp_path)
    legacy_path, original_bytes, old_intent = _legacy_pending(
        session, store, item_id, requirement_id, run_id,
    )
    outbox = WorkCardPublicationOutbox(session, store)

    first = outbox.list_pending()
    second = outbox.list_pending()

    assert len(first) == len(second) == 1
    assert first[0]["intent_id"] == old_intent["intent_id"]
    assert first[0]["request_id"] == old_intent["request_id"]
    assert first[0]["latest_run_id"] == run_id
    assert first[0]["session_id"] == session.conversation_id
    assert first[0]["project_id"] == "project-a"
    assert first[0]["legacy_source_digest"]
    assert second == first
    assert legacy_path.read_bytes() == original_bytes
    assert len(store.list_publication_intents()) == 1



def test_legacy_corruption_fails_closed_without_overwriting_new_atomic_pending(tmp_path, caplog):
    session, store, item_id, requirement_id, run_id = _project_with_atomic_pending(tmp_path)
    atomic_before = store.list_publication_intents()
    assert len(atomic_before) == 1 and atomic_before[0]["state"] == "pending"
    legacy_path = session.session_dir / "work-card-publish-outbox.v1.json"
    corrupt = b'{"schema_version":99}\n'
    legacy_path.write_bytes(corrupt)

    with pytest.raises(WorkCardPublishOutboxError):
        WorkCardPublicationOutbox(session, store).list_pending()

    assert "legacy_outbox_corrupt_or_binding_mismatch" in caplog.text
    assert legacy_path.read_bytes() == corrupt
    assert store.list_publication_intents() == atomic_before


def test_legacy_pending_from_superseded_run_is_not_migrated(tmp_path):
    session, store, item_id, requirement_id, run_id = _legacy_project(tmp_path)
    legacy_path, original_bytes, _old_intent = _legacy_pending(
        session, store, item_id, requirement_id, run_id,
    )
    newer_run = uuid.uuid4().hex
    store.begin_requirement_run(item_id, requirement_id, run_id=newer_run,
                                expected_current_run_id=run_id)

    with pytest.raises(WorkCardPublishOutboxError, match="no longer current"):
        WorkCardPublicationOutbox(session, store).list_pending()

    assert legacy_path.read_bytes() == original_bytes
    assert store.list_publication_intents() == []


def test_migrated_legacy_source_change_is_detected_and_atomic_intent_is_preserved(tmp_path):
    session, store, item_id, requirement_id, run_id = _legacy_project(tmp_path)
    legacy_path, _original_bytes, _old_intent = _legacy_pending(
        session, store, item_id, requirement_id, run_id,
    )
    outbox = WorkCardPublicationOutbox(session, store)
    migrated = outbox.list_pending()
    assert len(migrated) == 1

    changed = json.loads(legacy_path.read_text(encoding="utf-8"))
    changed["revision"] += 1
    legacy_path.write_text(json.dumps(changed, sort_keys=True, separators=(",", ":")) + chr(10),
                           encoding="utf-8")
    changed_bytes = legacy_path.read_bytes()

    with pytest.raises(WorkCardPublishOutboxError, match="changed after migration"):
        outbox.list_pending()

    assert legacy_path.read_bytes() == changed_bytes
    assert store.list_publication_intents() == migrated


def test_legacy_published_entry_is_watermarked_but_never_requeued(tmp_path):
    session, store, item_id, requirement_id, run_id = _legacy_project(tmp_path)
    legacy_path, _original_bytes, old_intent = _legacy_pending(
        session, store, item_id, requirement_id, run_id,
    )
    payload = json.loads(legacy_path.read_text(encoding="utf-8"))
    payload["items"][0]["state"] = "published"
    payload["items"][0]["card_id"] = "wc-" + hashlib.sha256(
        f"{session.conversation_id}\0{old_intent['request_id']}".encode("utf-8"),
    ).hexdigest()
    legacy_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + chr(10),
                           encoding="utf-8")
    published_bytes = legacy_path.read_bytes()

    assert WorkCardPublicationOutbox(session, store).list_pending() == []

    assert store.list_publication_intents() == []
    assert legacy_path.read_bytes() == published_bytes


def test_legacy_migration_crash_after_atomic_replace_retries_once(tmp_path, monkeypatch):
    session, store, item_id, requirement_id, run_id = _legacy_project(tmp_path)
    legacy_path, original_bytes, old_intent = _legacy_pending(
        session, store, item_id, requirement_id, run_id,
    )
    original_publish = WorkRecordStore._publish

    class SimulatedProcessCrash(BaseException):
        pass

    def replace_then_crash(bound_store, data, *, previous=None):
        original_publish(bound_store, data, previous=previous)
        raise SimulatedProcessCrash()

    monkeypatch.setattr(WorkRecordStore, "_publish", replace_then_crash)
    with pytest.raises(SimulatedProcessCrash):
        WorkCardPublicationOutbox(session, store).list_pending()
    monkeypatch.setattr(WorkRecordStore, "_publish", original_publish)

    restored = Session.load(session.paths, session.conversation_id)
    restored_store = WorkRecordStore(restored)
    migrated = WorkCardPublicationOutbox(restored, restored_store).list_pending()
    retried = WorkCardPublicationOutbox(restored, restored_store).list_pending()

    assert len(migrated) == 1 and migrated[0]["intent_id"] == old_intent["intent_id"]
    assert retried == migrated
    assert legacy_path.read_bytes() == original_bytes
    assert len(restored_store.list_publication_intents()) == 1


def test_legacy_pending_migration_survives_real_process_restart(tmp_path):
    session, store, item_id, requirement_id, run_id = _legacy_project(tmp_path)
    legacy_path, legacy_bytes, old_intent = _legacy_pending(
        session, store, item_id, requirement_id, run_id,
    )
    core = Path(__file__).resolve().parents[1]
    child_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from paths import AgentPaths
from session import Session
from work_records import WorkRecordStore
from work_card_publish_outbox import WorkCardPublicationOutbox
root = Path(sys.argv[2])
paths = AgentPaths(agent_root=root, workspace=root/'workspace', data=root/'data', evolve=root/'evolve')
session = Session.load(paths, sys.argv[3])
store = WorkRecordStore(session)
pending = WorkCardPublicationOutbox(session, store).list_pending()
assert len(pending) == 1
print(pending[0]['intent_id'])
"""
    child = subprocess.run(
        [sys.executable, "-c", child_code, str(core), str(tmp_path), session.conversation_id],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip().splitlines()[-1] == old_intent["intent_id"]

    restored = Session.load(session.paths, session.conversation_id)
    restored_store = WorkRecordStore(restored)
    migrated = WorkCardPublicationOutbox(restored, restored_store).list_pending()
    assert len(migrated) == 1 and migrated[0]["intent_id"] == old_intent["intent_id"]
    assert legacy_path.read_bytes() == legacy_bytes
    assert len(restored_store.list_publication_intents()) == 1



@pytest.mark.parametrize(
    ("field", "value"),
    [("conversation_id", str(uuid.uuid4())), ("project_id", "project-b")],
)
def test_legacy_session_or_project_binding_mismatch_fails_closed(tmp_path, field, value):
    session, store, item_id, requirement_id, run_id = _legacy_project(tmp_path)
    legacy_path, _legacy_bytes, _old_intent = _legacy_pending(
        session, store, item_id, requirement_id, run_id,
    )
    payload = json.loads(legacy_path.read_text(encoding="utf-8"))
    payload[field] = value
    legacy_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + chr(10),
                           encoding="utf-8")
    mismatched_bytes = legacy_path.read_bytes()

    with pytest.raises(WorkCardPublishOutboxError):
        WorkCardPublicationOutbox(session, store).list_pending()

    assert legacy_path.read_bytes() == mismatched_bytes
    assert store.list_publication_intents() == []



def test_legacy_pending_conflict_does_not_hide_or_replace_new_atomic_pending(tmp_path):
    session, store, item_id, requirement_id, run_id = _project_with_atomic_pending(tmp_path)
    new_pending = store.list_publication_intents()
    assert len(new_pending) == 1 and new_pending[0]["state"] == "pending"
    legacy_path, legacy_bytes, _old = _legacy_pending(session, store, item_id, requirement_id, run_id)

    with pytest.raises(WorkCardPublishOutboxError, match="conflicts"):
        WorkCardPublicationOutbox(session, store).list_pending()

    assert legacy_path.read_bytes() == legacy_bytes
    assert store.list_publication_intents() == new_pending


def test_migrated_published_marker_survives_later_new_work_and_recovery(tmp_path):
    from work_record_cards import build_project_work_record_cards
    from work_card_ledger import WorkCardLedger
    from work_card_presentations import WorkCardPresentations

    session, store, item_id, requirement_id, run_id = _legacy_project(tmp_path)
    legacy_path, legacy_bytes, old_intent = _legacy_pending(
        session, store, item_id, requirement_id, run_id,
    )
    outbox = WorkCardPublicationOutbox(session, store)
    first = outbox.list_pending()[0]

    def enqueue_reference(intent):
        ledger = WorkCardLedger(session, store)
        snapshot = build_project_work_record_cards(store, "project-a")
        saved = ledger.save_snapshot(intent["request_id"], snapshot)
        assert saved.status in {"saved", "already_saved"} and saved.card_id
        assert outbox.bind_card(
            intent_id=intent["intent_id"], generation=intent["generation"], card_id=saved.card_id,
        ) in {"bound", "already_bound"}
        presentation_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"my-agent-work-card:{session.conversation_id}:{uuid.UUID(intent['latest_run_id']).hex}:{saved.card_id}",
        ))
        session.append_sent_work_card_reference(presentation_id, saved.card_id,
                                                run_id=str(uuid.UUID(intent["latest_run_id"])))
        WorkCardPresentations(session, store, ledger).create(presentation_id, saved.card_id)
        assert outbox.clear_published(
            intent_id=intent["intent_id"], generation=intent["generation"], card_id=saved.card_id,
        ) == "published"
        return saved.card_id

    first_card = enqueue_reference(first)
    later = store.add_requirement(item_id, "Later project requirement", event_id="later-source")
    later_run = uuid.uuid4().hex
    store.begin_requirement_run(item_id, later["requirement_id"], run_id=later_run,
                                expected_current_run_id=None)
    later_binding = BoundExecution(session.conversation_id, item_id, later["requirement_id"], later_run)
    later_receipt = ExecutionReceipt(
        session_id=session.conversation_id, item_id=item_id,
        requirement_id=later["requirement_id"], run_id=later_run,
        sequence=1, outcome="succeeded", finish_reason="completed",
    )
    assert WorkRecordSettlementAdapter(store, later_binding).settle(
        later_receipt, publish_project_update=True,
    ).status == "recorded"
    second = outbox.list_pending()[0]
    second_card = enqueue_reference(second)

    assert second_card != first_card
    assert outbox.list_pending() == []
    assert len(WorkCardLedger(session, store).list_cards()) == 2
    assert legacy_path.read_bytes() == legacy_bytes
    # Reading again after the single published slot has rotated must not resurrect old pending history.
    assert WorkCardPublicationOutbox(session, store).list_pending() == []


def test_legacy_pending_matching_already_published_atomic_settlement_is_not_requeued(tmp_path):
    from work_record_cards import build_project_work_record_cards
    from work_card_ledger import WorkCardLedger

    session, store, item_id, requirement_id, run_id = _project_with_atomic_pending(tmp_path)
    outbox = WorkCardPublicationOutbox(session, store)
    pending = outbox.list_pending()
    assert len(pending) == 1 and pending[0]["state"] == "pending"
    intent = pending[0]
    saved = WorkCardLedger(session, store).save_snapshot(
        intent["request_id"], build_project_work_record_cards(store, "project-a"),
    )
    assert saved.status == "saved"
    assert outbox.bind_card(intent_id=intent["intent_id"], generation=intent["generation"],
                            card_id=saved.card_id) == "bound"
    assert outbox.clear_published(intent_id=intent["intent_id"], generation=intent["generation"],
                                  card_id=saved.card_id) == "published"
    legacy_path, old_bytes, _old_intent = _legacy_pending(
        session, store, item_id, requirement_id, run_id,
    )

    assert WorkCardPublicationOutbox(session, store).list_pending() == []

    assert legacy_path.read_bytes() == old_bytes
    assert len(WorkCardLedger(session, store).list_cards()) == 1
    assert store.list_publication_intents() == []


def test_legacy_migration_failure_before_atomic_replace_retries_without_touching_source(tmp_path, monkeypatch):
    session, store, item_id, requirement_id, run_id = _legacy_project(tmp_path)
    legacy_path, original_bytes, old_intent = _legacy_pending(
        session, store, item_id, requirement_id, run_id,
    )
    original_publish = WorkRecordStore._publish

    class SimulatedProcessCrash(BaseException):
        pass

    def fail_before_replace(_bound_store, _data, *, previous=None):
        raise SimulatedProcessCrash()

    monkeypatch.setattr(WorkRecordStore, "_publish", fail_before_replace)
    with pytest.raises(SimulatedProcessCrash):
        WorkCardPublicationOutbox(session, store).list_pending()
    monkeypatch.setattr(WorkRecordStore, "_publish", original_publish)
    assert store.list_publication_intents() == []
    assert store._read().get(LEGACY_OUTBOX_DIGEST_KEY) is None
    assert legacy_path.read_bytes() == original_bytes

    recovered = WorkCardPublicationOutbox(session, store).list_pending()
    assert len(recovered) == 1 and recovered[0]["intent_id"] == old_intent["intent_id"]
    assert legacy_path.read_bytes() == original_bytes
