from __future__ import annotations

import json
import sys
import threading
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_record_cards import (
    WorkRecordCardsError, build_independent_work_record_card, build_project_work_record_cards,
)


class Fixture:
    def __init__(self, root: Path, project_id: str = "project-alpha"):
        self.project_id = project_id
        self.session_id = "20261030-aaaaaaaa"
        self.paths = AgentPaths(
            agent_root=root,
            workspace=root / "workspace",
            data=root / "data",
            evolve=root / "evolve",
        )
        session_dir = self.paths.data / "sessions" / self.session_id
        session_dir.mkdir(parents=True)
        self.session = Session(
            conversation_id=self.session_id,
            session_dir=session_dir,
            goal="not read by cards",
            meta=SessionMeta(project_id=project_id),
            messages=[],
            paths=self.paths,
        )
        self.store = WorkRecordStore(self.session)

    def add_item(self, title="Project task", *, scope=None, source_refs=None):
        return self.store.create_item(
            title=title,
            scope=scope or {"kind": "project", "project_id": self.project_id},
            source_refs=source_refs,
        )

    def add_requirement(self, item_id, text, *, event_id, source_ref=None):
        return self.store.add_requirement(
            item_id, text, event_id=event_id, source_ref=source_ref,
        )


def test_project_cards_capture_every_requirement_and_latest_stored_result_with_sources(tmp_path):
    f = Fixture(tmp_path)
    item = f.add_item(source_refs=[{"kind": "project", "id": "project-alpha"}])
    first = f.add_requirement(
        item["item_id"], "requirement with history", event_id="req-1",
        source_ref={"kind": "requirement", "id": "requirement-source"},
    )
    second = f.add_requirement(item["item_id"], "unknown requirement", event_id="req-2")
    f.store.record_result(
        item["item_id"], first["requirement_id"], run_id="run-old", result_id="result-old",
        status="blocked", summary="older saved summary", verification={"status": "failed"},
        sources=[{"kind": "result", "id": "older-source"}],
    )
    f.store.record_result(
        item["item_id"], first["requirement_id"], run_id="run-latest", result_id="result-latest",
        status="completed", summary="latest saved summary",
        verification={"status": "not_verified", "tool_output": "DO_NOT_COPY"},
        sources=[{"kind": "result", "id": "latest-source"}],
    )
    f.store.record_result(
        item["item_id"], second["requirement_id"], run_id="run-unknown", result_id="result-unknown",
        status="unknown", summary="unknown remains unknown",
        verification={"status": "not_verified"},
    )

    sidecar_before = f.store._path.read_bytes()
    snapshot = build_project_work_record_cards(f.store, f.project_id)
    sidecar_after = f.store._path.read_bytes()

    assert sidecar_before == sidecar_after
    assert snapshot.persisted is False
    assert snapshot.project_id == f.project_id and snapshot.session_id == f.session_id
    assert snapshot.completeness == "complete"
    assert snapshot.history_complete is False
    assert snapshot.omitted_result_count == 1
    assert len(snapshot.cards) == 1
    card = snapshot.cards[0]
    assert card.project_id == f.project_id and card.session_id == f.session_id
    assert card.item_id == item["item_id"]
    assert card.source_refs[0].id == "project-alpha"
    req1 = next(row for row in card.requirements if row.requirement_id == first["requirement_id"])
    req2 = next(row for row in card.requirements if row.requirement_id == second["requirement_id"])
    assert req1.status == "completed"
    assert req1.current_run_id is None
    assert req1.latest_result_is_current is None
    assert req1.current_result_missing is False
    assert req1.source_ref.id == "requirement-source"
    assert req1.result_count == 2 and req1.omitted_result_count == 1
    assert req1.latest_result.project_id == f.project_id
    assert req1.latest_result.session_id == f.session_id
    assert req1.latest_result.item_id == item["item_id"]
    assert req1.latest_result.requirement_id == first["requirement_id"]
    assert req1.latest_result.result_id == "result-latest"
    assert req1.latest_result.run_id == "run-latest"
    assert req1.latest_result.status == "completed"
    assert req1.latest_result.summary == "latest saved summary"
    assert req1.latest_result.verification_status == "not_verified"
    assert req1.latest_result.sources[0].id == "latest-source"
    assert req2.status == "unknown"
    assert req2.current_run_id is None
    assert req2.latest_result_is_current is None
    assert req2.latest_result.status == "unknown"
    assert req2.latest_result.verification_status == "not_verified"
    assert "DO_NOT_COPY" not in repr(snapshot)
    assert "older saved summary" not in repr(snapshot)


def test_independent_and_other_project_items_are_not_in_cards(tmp_path):
    f = Fixture(tmp_path)
    item = f.add_item(title="selected project task")
    f.add_requirement(item["item_id"], "selected requirement", event_id="req")
    f.store.create_item(title="independent matter", scope={"kind": "independent"})

    snapshot = build_project_work_record_cards(f.store, f.project_id)

    assert [card.title for card in snapshot.cards] == ["selected project task"]
    assert all(card.project_id == f.project_id for card in snapshot.cards)


def test_project_id_must_match_the_authorized_store_session_binding(tmp_path):
    f = Fixture(tmp_path)

    with pytest.raises(WorkRecordCardsError):
        build_project_work_record_cards(f.store, "another-project")


def test_empty_project_sidecar_returns_an_empty_complete_snapshot(tmp_path):
    f = Fixture(tmp_path)

    snapshot = build_project_work_record_cards(f.store, f.project_id)

    assert snapshot.cards == ()
    assert snapshot.completeness == "complete"
    assert snapshot.item_count == snapshot.requirement_count == snapshot.result_count == 0


def test_snapshot_is_immutable_and_unchanged_after_store_update(tmp_path):
    f = Fixture(tmp_path)
    item = f.add_item(title="snapshot item")
    f.add_requirement(item["item_id"], "before update", event_id="req-before")
    snapshot = build_project_work_record_cards(f.store, f.project_id)
    original_card = snapshot.cards[0]

    with pytest.raises(FrozenInstanceError):
        snapshot.project_id = "changed"
    f.store.create_item(title="added later", scope={"kind": "project", "project_id": f.project_id})
    f.add_requirement(item["item_id"], "after update", event_id="req-after")

    assert snapshot.cards == (original_card,)
    assert [row.text for row in original_card.requirements] == ["before update"]
    refreshed = build_project_work_record_cards(f.store, f.project_id)
    assert len(refreshed.cards) == 2
    assert len(refreshed.cards[0].requirements) == 2


def test_oversized_project_card_set_returns_explicit_incomplete_snapshot(tmp_path, monkeypatch):
    import work_record_cards as cards_module

    f = Fixture(tmp_path)
    f.add_item(title="one")
    f.add_item(title="two")
    monkeypatch.setattr(cards_module, "_MAX_PROJECT_ITEMS", 1)

    snapshot = build_project_work_record_cards(f.store, f.project_id)

    assert snapshot.completeness == "incomplete"
    assert snapshot.incomplete_reason == "project_item_limit_exceeded"
    assert snapshot.cards == ()
    assert snapshot.item_count == 2 and snapshot.omitted_item_count == 2


def test_missing_or_corrupt_required_fields_fail_instead_of_silent_truncation(tmp_path):
    f = Fixture(tmp_path)
    item = f.add_item()
    requirement = f.add_requirement(item["item_id"], "required text", event_id="req")
    payload = json.loads((f.store._path).read_text(encoding="utf-8"))
    del payload["items"][0]["requirements"][0]["text"]
    f.store._path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(WorkRecordCardsError):
        build_project_work_record_cards(f.store, f.project_id)


def test_sidecar_read_lock_keeps_snapshot_consistent_with_concurrent_writer(tmp_path):
    f = Fixture(tmp_path)
    item = f.add_item()
    f.add_requirement(item["item_id"], "before concurrent write", event_id="req-before")
    entered = threading.Event()
    release = threading.Event()
    writer_started = threading.Event()
    writer_done = threading.Event()
    snapshot_result = []
    writer_errors = []
    original_with_locked_items = f.store.with_locked_items

    def gated_with_locked_items(callback):
        def gated_callback(items):
            entered.set()
            if not release.wait(2):
                raise AssertionError("test did not release the snapshot callback")
            return callback(items)
        return original_with_locked_items(gated_callback)

    f.store.with_locked_items = gated_with_locked_items
    def take_snapshot():
        snapshot_result.append(build_project_work_record_cards(f.store, f.project_id))
    def add_concurrent_requirement():
        writer_started.set()
        try:
            f.add_requirement(item["item_id"], "after lock release", event_id="req-after")
        except BaseException as exc:
            writer_errors.append(exc)
        finally:
            writer_done.set()

    snapshot_thread = threading.Thread(target=take_snapshot)
    snapshot_thread.start()
    assert entered.wait(1)
    writer_thread = threading.Thread(target=add_concurrent_requirement)
    writer_thread.start()
    assert writer_started.wait(1)
    assert not writer_done.wait(0.1)
    release.set()
    snapshot_thread.join(2)
    writer_thread.join(2)

    assert not snapshot_thread.is_alive() and not writer_thread.is_alive()
    assert writer_errors == []
    assert len(snapshot_result) == 1
    assert [row.text for row in snapshot_result[0].cards[0].requirements] == ["before concurrent write"]
    refreshed = build_project_work_record_cards(f.store, f.project_id)
    assert [row.text for row in refreshed.cards[0].requirements] == [
        "before concurrent write", "after lock release",
    ]


def test_foreign_project_record_in_store_fails_closed(tmp_path):
    f = Fixture(tmp_path)
    item = f.add_item()
    f.add_requirement(item["item_id"], "authorized requirement", event_id="req")
    payload = json.loads(f.store._path.read_text(encoding="utf-8"))
    payload["items"].append({
        "item_id": "foreign-project-item",
        "title": "must not enter this card set",
        "scope": {"kind": "project", "project_id": "other-project"},
        "status": "active",
        "requirements": [],
        "runs": [],
        "history": [],
    })
    f.store._path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(WorkRecordCardsError):
        build_project_work_record_cards(f.store, f.project_id)


def test_two_real_store_updates_keep_old_snapshot_and_separate_current_from_history_completeness(tmp_path):
    f = Fixture(tmp_path)
    item = f.add_item(title="ongoing project task")
    requirement = f.add_requirement(
        item["item_id"], "current requirement", event_id="current-req",
        source_ref={"kind": "requirement", "id": "req-source"},
    )
    f.store.record_result(
        item["item_id"], requirement["requirement_id"], run_id="run-1", result_id="result-1",
        status="completed", summary="first work", verification={"status": "not_verified"},
        sources=[{"kind": "result", "id": "first-source"}],
    )
    old_snapshot = build_project_work_record_cards(f.store, f.project_id)

    f.store.record_result(
        item["item_id"], requirement["requirement_id"], run_id="run-2", result_id="result-2",
        status="completed", summary="second work", verification={"status": "not_verified"},
        sources=[{"kind": "result", "id": "second-source"}],
    )
    new_snapshot = build_project_work_record_cards(f.store, f.project_id)

    assert old_snapshot.completeness == "complete" and old_snapshot.history_complete is True
    assert old_snapshot.cards[0].requirements[0].latest_result.result_id == "result-1"
    assert old_snapshot.cards[0].requirements[0].latest_result.summary == "first work"
    assert new_snapshot.completeness == "complete"
    assert new_snapshot.history_complete is False
    assert new_snapshot.incomplete_reason is None
    assert new_snapshot.omitted_result_count == 1
    current = new_snapshot.cards[0].requirements[0]
    assert current.status == "completed"
    assert current.result_count == 2 and current.omitted_result_count == 1
    assert current.latest_result.result_id == "result-2"
    assert current.latest_result.summary == "second work"
    assert current.latest_result.verification_status == "not_verified"
    assert len(current.latest_result.sources) == 1
    assert current.latest_result.sources[0].kind == "result"
    assert current.latest_result.sources[0].id == "second-source"
    assert old_snapshot.cards[0].requirements[0].latest_result.result_id == "result-1"


def test_terminal_current_requirement_without_its_result_is_incomplete(tmp_path):
    f = Fixture(tmp_path)
    item = f.add_item()
    requirement = f.add_requirement(item["item_id"], "terminal but result missing", event_id="req")
    payload = json.loads(f.store._path.read_text(encoding="utf-8"))
    payload["items"][0]["requirements"][0]["status"] = "completed"
    f.store._path.write_text(json.dumps(payload), encoding="utf-8")

    snapshot = build_project_work_record_cards(f.store, f.project_id)

    card_requirement = snapshot.cards[0].requirements[0]
    assert snapshot.completeness == "incomplete"
    assert snapshot.missing_current_result_count == 1
    assert card_requirement.latest_result is None
    assert card_requirement.current_result_missing is True


def test_started_new_run_exposes_old_result_as_not_current_without_incomplete_card(tmp_path):
    f = Fixture(tmp_path)
    item = f.add_item()
    requirement = f.add_requirement(item["item_id"], "multi-run requirement", event_id="req-multi-run")
    requirement_id = requirement["requirement_id"]
    f.store.begin_requirement_run(
        item["item_id"], requirement_id, run_id="old-run", expected_current_run_id=None,
    )
    f.store.record_result_if_current_run(
        item["item_id"], requirement_id, run_id="old-run", result_id="old-result",
        status="completed", summary="old run result", verification={"status": "not_verified"},
    )
    completed_old_run = build_project_work_record_cards(f.store, f.project_id)
    f.store.begin_requirement_run(
        item["item_id"], requirement_id, run_id="new-run", expected_current_run_id="old-run",
    )

    snapshot = build_project_work_record_cards(f.store, f.project_id)
    card_requirement = snapshot.cards[0].requirements[0]

    assert snapshot.completeness == "complete"
    assert card_requirement.status == "in_progress"
    assert card_requirement.current_run_id == "new-run"
    assert card_requirement.latest_result.run_id == "old-run"
    assert card_requirement.latest_result_is_current is False
    assert card_requirement.current_result_missing is False
    assert card_requirement.latest_result.verification_status == "not_verified"
    assert completed_old_run.cards[0].requirements[0].current_run_id == "old-run"
    assert completed_old_run.cards[0].requirements[0].latest_result_is_current is True


def test_terminal_status_with_old_result_not_matching_current_run_is_incomplete(tmp_path):
    f = Fixture(tmp_path)
    item = f.add_item()
    requirement = f.add_requirement(item["item_id"], "terminal mismatch requirement", event_id="req-terminal")
    requirement_id = requirement["requirement_id"]
    f.store.begin_requirement_run(
        item["item_id"], requirement_id, run_id="old-run", expected_current_run_id=None,
    )
    f.store.record_result_if_current_run(
        item["item_id"], requirement_id, run_id="old-run", result_id="old-result",
        status="completed", summary="old run only", verification={"status": "not_verified"},
    )
    f.store.begin_requirement_run(
        item["item_id"], requirement_id, run_id="new-run", expected_current_run_id="old-run",
    )
    payload = json.loads(f.store._path.read_text(encoding="utf-8"))
    payload["items"][0]["requirements"][0]["status"] = "completed"
    f.store._path.write_text(json.dumps(payload), encoding="utf-8")

    snapshot = build_project_work_record_cards(f.store, f.project_id)
    card_requirement = snapshot.cards[0].requirements[0]

    assert card_requirement.current_run_id == "new-run"
    assert card_requirement.latest_result.run_id == "old-run"
    assert card_requirement.latest_result_is_current is False
    assert card_requirement.current_result_missing is True
    assert snapshot.completeness == "incomplete"
    assert snapshot.incomplete_reason == "current_result_missing"
    assert snapshot.missing_current_result_count == 1


def test_independent_card_is_unbound_to_project_and_excludes_every_other_item(tmp_path):
    f = Fixture(tmp_path)
    independent = f.store.create_item(title="Résumé review", scope={"kind": "independent"})
    requirement = f.add_requirement(
        independent["item_id"], "update résumé summary", event_id="resume-req",
        source_ref={"kind": "requirement", "id": "resume-source"},
    )
    f.store.record_result(
        independent["item_id"], requirement["requirement_id"], run_id="resume-run-old",
        result_id="resume-result-old", status="blocked", summary="old review",
        verification={"status": "failed"},
    )
    f.store.record_result(
        independent["item_id"], requirement["requirement_id"], run_id="resume-run-new",
        result_id="resume-result-new", status="completed", summary="stored résumé update",
        verification={"status": "not_verified"},
        sources=[{"kind": "result", "id": "resume-evidence"}],
    )
    project_item = f.add_item(title="must not appear")
    f.add_requirement(project_item["item_id"], "project requirement", event_id="project-req")
    other_independent = f.store.create_item(title="other independent matter", scope={"kind": "independent"})

    snapshot = build_independent_work_record_card(f.store, independent["item_id"])

    assert snapshot.kind == "independent"
    assert snapshot.project_id is None
    assert snapshot.session_id == f.session_id and snapshot.item_id == independent["item_id"]
    assert snapshot.completeness == "complete"
    assert snapshot.history_complete is False and snapshot.omitted_result_count == 1
    card = snapshot.card
    assert card.kind == "independent" and card.project_id is None
    assert card.session_id == f.session_id and card.item_id == independent["item_id"]
    assert card.title == "Résumé review"
    assert [row.requirement_id for row in card.requirements] == [requirement["requirement_id"]]
    req = card.requirements[0]
    assert req.source_ref.id == "resume-source"
    assert req.latest_result.result_id == "resume-result-new"
    assert req.latest_result.verification_status == "not_verified"
    assert req.latest_result.sources[0].id == "resume-evidence"
    assert req.omitted_result_count == 1
    assert req.current_run_id is None
    assert req.latest_result_is_current is None
    assert "must not appear" not in repr(snapshot)
    assert "other independent matter" not in repr(snapshot)


def test_independent_card_works_for_unbound_session(tmp_path):
    f = Fixture(tmp_path, project_id="")
    independent = f.store.create_item(title="unbound independent", scope={"kind": "independent"})
    f.add_requirement(independent["item_id"], "unbound requirement", event_id="unbound-req")

    snapshot = build_independent_work_record_card(f.store, independent["item_id"])

    assert snapshot.kind == "independent"
    assert snapshot.project_id is None
    assert snapshot.session_id == f.session_id
    assert snapshot.card.item_id == independent["item_id"]
    assert snapshot.completeness == "complete"


def test_independent_card_rejects_missing_project_scoped_and_ambiguous_ids(tmp_path):
    f = Fixture(tmp_path)
    project_item = f.add_item(title="not independent")
    independent = f.store.create_item(title="one independent", scope={"kind": "independent"})
    for item_id in ("missing-item", project_item["item_id"]):
        with pytest.raises(WorkRecordCardsError):
            build_independent_work_record_card(f.store, item_id)

    payload = json.loads(f.store._path.read_text(encoding="utf-8"))
    duplicate = next(item for item in payload["items"] if item["item_id"] == independent["item_id"])
    payload["items"].append(dict(duplicate))
    f.store._path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(WorkRecordCardsError):
        build_independent_work_record_card(f.store, independent["item_id"])


def test_independent_card_snapshot_is_immutable_and_does_not_follow_store_changes(tmp_path):
    f = Fixture(tmp_path)
    independent = f.store.create_item(title="immutable independent", scope={"kind": "independent"})
    f.add_requirement(independent["item_id"], "before change", event_id="before")
    snapshot = build_independent_work_record_card(f.store, independent["item_id"])
    f.add_requirement(independent["item_id"], "after change", event_id="after")

    with pytest.raises(FrozenInstanceError):
        snapshot.card.title = "mutated"
    with pytest.raises(FrozenInstanceError):
        snapshot.card.requirements[0].text = "mutated"
    assert [row.text for row in snapshot.card.requirements] == ["before change"]
    refreshed = build_independent_work_record_card(f.store, independent["item_id"])
    assert [row.text for row in refreshed.card.requirements] == ["before change", "after change"]


def test_independent_card_limit_is_explicitly_incomplete(tmp_path, monkeypatch):
    import work_record_cards as cards_module

    f = Fixture(tmp_path)
    independent = f.store.create_item(title="large independent", scope={"kind": "independent"})
    f.add_requirement(independent["item_id"], "requirement A", event_id="a")
    f.add_requirement(independent["item_id"], "requirement B", event_id="b")
    monkeypatch.setattr(cards_module, "_MAX_REQUIREMENTS", 1)

    snapshot = build_independent_work_record_card(f.store, independent["item_id"])

    assert snapshot.completeness == "incomplete"
    assert snapshot.incomplete_reason == "requirement_limit_exceeded"
    assert snapshot.card is None
    assert snapshot.omitted_requirement_count == 2


def test_independent_card_rejects_corrupt_required_fields(tmp_path):
    f = Fixture(tmp_path)
    independent = f.store.create_item(title="corrupt independent", scope={"kind": "independent"})
    f.add_requirement(independent["item_id"], "required requirement", event_id="req")
    payload = json.loads(f.store._path.read_text(encoding="utf-8"))
    del payload["items"][0]["requirements"][0]["text"]
    f.store._path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(WorkRecordCardsError):
        build_independent_work_record_card(f.store, independent["item_id"])


def test_independent_card_marks_last_result_as_old_after_new_run_starts(tmp_path):
    f = Fixture(tmp_path)
    independent = f.store.create_item(title="multi-run independent", scope={"kind": "independent"})
    requirement = f.add_requirement(independent["item_id"], "independent run requirement", event_id="req")
    requirement_id = requirement["requirement_id"]
    f.store.begin_requirement_run(independent["item_id"], requirement_id,
                                  run_id="old-independent-run", expected_current_run_id=None)
    f.store.record_result_if_current_run(
        independent["item_id"], requirement_id,
        run_id="old-independent-run", result_id="old-independent-result",
        status="completed", summary="old independent run",
        verification={"status": "not_verified"},
    )
    f.store.begin_requirement_run(independent["item_id"], requirement_id,
                                  run_id="new-independent-run", expected_current_run_id="old-independent-run")

    snapshot = build_independent_work_record_card(f.store, independent["item_id"])
    requirement_card = snapshot.card.requirements[0]

    assert snapshot.completeness == "complete"
    assert requirement_card.status == "in_progress"
    assert requirement_card.current_run_id == "new-independent-run"
    assert requirement_card.latest_result.run_id == "old-independent-run"
    assert requirement_card.latest_result_is_current is False
    assert requirement_card.current_result_missing is False
    assert requirement_card.latest_result.verification_status == "not_verified"


def test_independent_terminal_old_result_cannot_satisfy_new_current_run(tmp_path):
    f = Fixture(tmp_path)
    independent = f.store.create_item(title="terminal independent", scope={"kind": "independent"})
    requirement = f.add_requirement(independent["item_id"], "terminal requirement", event_id="req")
    requirement_id = requirement["requirement_id"]
    f.store.begin_requirement_run(independent["item_id"], requirement_id,
                                  run_id="old-independent-run", expected_current_run_id=None)
    f.store.record_result_if_current_run(
        independent["item_id"], requirement_id,
        run_id="old-independent-run", result_id="old-independent-result",
        status="completed", summary="old independent run",
        verification={"status": "not_verified"},
    )
    f.store.begin_requirement_run(independent["item_id"], requirement_id,
                                  run_id="new-independent-run", expected_current_run_id="old-independent-run")
    payload = json.loads(f.store._path.read_text(encoding="utf-8"))
    target = next(row for row in payload["items"] if row["item_id"] == independent["item_id"])
    target["requirements"][0]["status"] = "completed"
    f.store._path.write_text(json.dumps(payload), encoding="utf-8")

    snapshot = build_independent_work_record_card(f.store, independent["item_id"])
    requirement_card = snapshot.card.requirements[0]

    assert requirement_card.current_run_id == "new-independent-run"
    assert requirement_card.latest_result.run_id == "old-independent-run"
    assert requirement_card.latest_result_is_current is False
    assert requirement_card.current_result_missing is True
    assert snapshot.completeness == "incomplete"
