from __future__ import annotations

import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordConflict, WorkRecordIdempotencyRequired, WorkRecordStore
from work_records_service import WorkRecordCaptureService


def make_store(tmp_path: Path) -> WorkRecordStore:
    root = tmp_path / "agent-root"
    paths = AgentPaths(root, root / "workspace", root / "data", root / "evolve")
    for path in (paths.workspace, paths.data, paths.evolve):
        path.mkdir(parents=True, exist_ok=True)
    session_dir = paths.data / "sessions" / "synthetic"
    session_dir.mkdir(parents=True)
    session = Session("synthetic", session_dir, "", SessionMeta(), [], paths)
    return WorkRecordStore(session)


def create(store: WorkRecordStore, key: str, title: str = "synthetic item", requirement: str = "synthetic requirement"):
    return store.create_item_with_requirement(
        title=title,
        scope={"kind": "independent"},
        requirement=requirement,
        event_id=f"attempt:{uuid.uuid4()}",
        idempotency_key=key,
    )


def test_same_stable_request_replays_new_item_without_duplicate(tmp_path):
    store = make_store(tmp_path)
    first = create(store, "desktop-request:synthetic-1")
    replay = create(store, "desktop-request:synthetic-1")

    assert replay == first
    assert len(store.list_items()) == 1
    assert len(first["requirements"]) == 1


def test_same_request_key_with_conflicting_new_item_payload_is_rejected(tmp_path):
    store = make_store(tmp_path)
    create(store, "desktop-request:conflict")

    with pytest.raises(WorkRecordConflict):
        create(store, "desktop-request:conflict", requirement="different synthetic requirement")
    assert len(store.list_items()) == 1
    assert len(store.list_items()[0]["requirements"]) == 1


def test_same_stable_request_replays_append_without_duplicate(tmp_path):
    store = make_store(tmp_path)
    item = store.create_item(title="existing synthetic", scope={"kind": "independent"})
    first = store.add_requirement(item["item_id"], "append synthetic", event_id="event:append", idempotency_key="desktop-request:append")
    replay = store.add_requirement(item["item_id"], "append synthetic", event_id="event:append-retry", idempotency_key="desktop-request:append")

    assert replay == first
    assert len(store.get_item(item["item_id"])["requirements"]) == 1


def test_reused_append_key_with_different_target_or_content_conflicts(tmp_path):
    store = make_store(tmp_path)
    one = store.create_item(title="one", scope={"kind": "independent"})
    two = store.create_item(title="two", scope={"kind": "independent"})
    store.add_requirement(one["item_id"], "append synthetic", event_id="event:first", idempotency_key="desktop-request:append-conflict")

    with pytest.raises(WorkRecordConflict):
        store.add_requirement(two["item_id"], "append synthetic", event_id="event:second", idempotency_key="desktop-request:append-conflict")
    with pytest.raises(WorkRecordConflict):
        store.add_requirement(one["item_id"], "different synthetic", event_id="event:third", idempotency_key="desktop-request:append-conflict")
    assert len(store.get_item(one["item_id"])["requirements"]) == 1
    assert store.get_item(two["item_id"])["requirements"] == []


def test_concurrent_retries_for_create_and_append_are_atomic(tmp_path):
    store = make_store(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        created = list(pool.map(lambda _i: create(store, "desktop-request:concurrent-create"), range(8)))
    assert len({item["item_id"] for item in created}) == 1
    assert len(store.list_items()) == 1

    item_id = created[0]["item_id"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        appended = list(pool.map(
            lambda _i: store.add_requirement(item_id, "one concurrent append", event_id="event:append", idempotency_key="desktop-request:concurrent-append"),
            range(8),
        ))
    assert len({row["requirement_id"] for row in appended}) == 1
    assert len(store.get_item(item_id)["requirements"]) == 2


def test_legacy_stable_event_id_remains_an_idempotency_key(tmp_path):
    store = make_store(tmp_path)
    item = store.create_item(title="legacy synthetic", scope={"kind": "independent"})

    first = store.add_requirement(item["item_id"], "legacy requirement", event_id="legacy-stable-event-1")
    replay = store.add_requirement(item["item_id"], "legacy requirement", event_id="legacy-stable-event-1")

    assert replay == first
    assert len(store.get_item(item["item_id"])["requirements"]) == 1


def test_missing_idempotency_key_fails_closed_without_writing(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(WorkRecordIdempotencyRequired):
        store.create_item_with_requirement(
            title="no id synthetic", scope={"kind": "independent"},
            requirement="must not persist", event_id=str(uuid.uuid4()),
        )
    item = store.create_item(title="existing synthetic", scope={"kind": "independent"})
    with pytest.raises(WorkRecordIdempotencyRequired):
        store.add_requirement(item["item_id"], "must not append", event_id=str(uuid.uuid4()))
    assert store.get_item(item["item_id"])["requirements"] == []


def test_concurrent_same_key_with_conflicting_create_payload_has_one_winner(tmp_path):
    store = make_store(tmp_path)
    barrier = __import__("threading").Barrier(2)

    def attempt(requirement):
        barrier.wait(timeout=5)
        try:
            return create(store, "desktop-request:concurrent-conflict", requirement=requirement)["item_id"]
        except WorkRecordConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ("first synthetic payload", "second synthetic payload")))

    assert results.count("conflict") == 1
    assert len({value for value in results if value != "conflict"}) == 1
    items = store.list_items()
    assert len(items) == 1
    assert len(items[0]["requirements"]) == 1


@pytest.mark.parametrize("append", [False, True])
def test_current_capture_caller_without_desktop_request_id_fails_closed(tmp_path, monkeypatch, append):
    import uuid

    store = make_store(tmp_path)
    target = store.create_item(title="existing synthetic", scope={"kind": "independent"}) if append else None

    class FakeProvider:
        def chat(self, _messages, **_kwargs):
            item_field = f',"item_id":"{target["item_id"]}"' if target else ',"item_id":null'
            return (
                '{"action":"record","kind":"requirement","title":"synthetic title",'
                '"requirement":"synthetic requirement","scope":"independent"' + item_field + '}'
            )

    monkeypatch.setattr(
        "work_records_service.get_registry",
        lambda *_a, **_k: type("Registry", (), {"get": lambda _self, _id: type("Model", (), {
            "id": "0x567-flash", "vendor": "0x567", "provider_model": "gpt-6-luna",
            "max_input_tokens": 96000,
        })()})(),
    )
    service = WorkRecordCaptureService(
        session=store._session,
        client=FakeProvider(),
        confirm=lambda _preview: ("yes", str(uuid.uuid4())),
    )

    outcome = service.capture("synthetic explicit record request")

    assert outcome.status == "invalid_request"
    assert "稳定请求编号" in outcome.message
    if append:
        assert store.get_item(target["item_id"])["requirements"] == []
    else:
        assert store.list_items() == []


def test_commit_status_is_atomic_and_old_api_unchanged(tmp_path):
    store = make_store(tmp_path)
    first, committed = store.create_item_with_requirement(
        title="synthetic title", scope={"kind": "independent"}, requirement="synthetic first",
        event_id="event:status", idempotency_key="request:status", return_commit_status=True,
    )
    replay, replay_committed = WorkRecordStore(store._session).create_item_with_requirement(
        title="synthetic title", scope={"kind": "independent"}, requirement="synthetic first",
        event_id="event:status", idempotency_key="request:status", return_commit_status=True,
    )
    assert committed is True and replay_committed is False and first == replay
    legacy = store.create_item_with_requirement(
        title="synthetic title", scope={"kind": "independent"}, requirement="synthetic first",
        event_id="event:status", idempotency_key="request:status",
    )
    assert isinstance(legacy, dict) and legacy["item_id"] == first["item_id"]
    with pytest.raises(WorkRecordConflict):
        store.create_item_with_requirement(
            title="changed", scope={"kind": "independent"}, requirement="synthetic first",
            event_id="event:status", idempotency_key="request:status", return_commit_status=True,
        )


def test_event_id_cannot_be_reused_on_another_append_target(tmp_path):
    store = make_store(tmp_path)
    first = store.create_item(title="first", scope={"kind": "independent"})
    second = store.create_item(title="second", scope={"kind": "independent"})
    store.add_requirement(first["item_id"], "synthetic first", event_id="event:shared", idempotency_key="key:first")
    with pytest.raises(WorkRecordConflict):
        store.add_requirement(second["item_id"], "synthetic second", event_id="event:shared", idempotency_key="key:second", return_commit_status=True)
    assert store.get_item(second["item_id"])["requirements"] == []
