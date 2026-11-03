from __future__ import annotations
import json
import sys
import subprocess
import os
import uuid
import threading
from pathlib import Path
from session import Session, SessionMeta
from paths import AgentPaths
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_work_card_ledger import make_session, _reopen
from work_card_ledger import WorkCardLedger, _stable_card_id
from work_record_cards import build_project_work_record_cards
import work_card_ledger
import work_records
from work_records import WorkRecordWriteError


def _saturated_ledger(tmp_path, *, size_limit):
    session, store, snapshot = make_session(tmp_path)
    if size_limit:
        item = store.list_items()[0]
        for n in range(8):
            store.add_requirement(item['item_id'], f'synthetic-{n}-' + 'x'*1000, event_id=f'limit-{n}')
        snapshot = build_project_work_record_cards(store, 'synthetic-project')
    ledger = WorkCardLedger(session, store)
    first_request=str(uuid.uuid4())
    first=ledger.save_snapshot(first_request, snapshot)
    assert first.status=='saved'
    data=ledger._ledger_store._read()
    record=data['items'][0]
    envelope=len((json.dumps({**data,'items':[]},ensure_ascii=False,separators=(',',':'),sort_keys=True)+'\n').encode('utf8'))
    one=len(json.dumps(record,ensure_ascii=False,separators=(',',':'),sort_keys=True).encode('utf8'))
    count=min(work_card_ledger._MAX_CARDS, (work_records._MAX_BYTES-envelope)//(one+1)+1) if size_limit else work_card_ledger._MAX_CARDS
    assert 1 < count <= work_card_ledger._MAX_CARDS
    if size_limit: assert count < work_card_ledger._MAX_CARDS
    for n in range(1,count):
        request=str(uuid.uuid5(uuid.NAMESPACE_URL,f'synthetic-limit:{n}'))
        clone=dict(record,request_id=request,card_id=_stable_card_id(session.conversation_id,request))
        data['items'].append(clone)
    previous=ledger._ledger_store._read()
    data['revision']+=1
    ledger._ledger_store._publish(data,previous=previous)
    files = _ledger_files(ledger)
    assert files
    assert all(len(raw) <= work_records._MAX_BYTES for raw in files.values())
    total_bytes = sum(map(len, files.values()))
    if size_limit:
        assert total_bytes > work_records._MAX_BYTES
        assert count < work_card_ledger._MAX_CARDS
    return session, store, ledger, snapshot, first_request, first.card_id, files, count


def _ledger_files(ledger):
    session_dir = ledger._session_dir
    paths = [ledger._ledger_store._path, *session_dir.glob(f"{work_card_ledger.LEDGER_FILENAME}.entry-*.json")]
    return {path.name: path.read_bytes() for path in paths if path.exists()}


def test_count_limit_should_allow_new_card_without_losing_old_or_replay(tmp_path):
    session,store,ledger,snapshot,request,card,raw,count=_saturated_ledger(tmp_path,size_limit=False)
    assert count == 2000
    reopened,reopened_store=_reopen(session); ledger=WorkCardLedger(reopened,reopened_store)
    assert ledger.get_card(card) is not None
    assert ledger.save_snapshot(request,snapshot).status=='already_saved'
    assert _ledger_files(ledger) == raw
    new=ledger.save_snapshot(str(uuid.uuid4()),snapshot)
    after = _ledger_files(ledger)
    assert set(after) == set(raw) | {f"{work_card_ledger.LEDGER_FILENAME}.entry-{new.card_id}.json"}
    assert after[work_card_ledger.LEDGER_FILENAME] == raw[work_card_ledger.LEDGER_FILENAME]
    assert len(ledger.list_cards()) == count + 1
    assert ledger.get_card(card) is not None
    assert new.status=='saved', f'new card blocked at {count} cards/{sum(map(len, raw.values()))} bytes: {new.status}/{new.reason}'


def test_bytes_limit_should_allow_new_card_without_losing_old_or_replay(tmp_path):
    session,store,ledger,snapshot,request,card,raw,count=_saturated_ledger(tmp_path,size_limit=True)
    reopened,reopened_store=_reopen(session); ledger=WorkCardLedger(reopened,reopened_store)
    assert ledger.get_card(card) is not None
    assert ledger.save_snapshot(request,snapshot).status=='already_saved'
    assert _ledger_files(ledger) == raw
    new=ledger.save_snapshot(str(uuid.uuid4()),snapshot)
    after = _ledger_files(ledger)
    assert set(after) == set(raw) | {f"{work_card_ledger.LEDGER_FILENAME}.entry-{new.card_id}.json"}
    assert after[work_card_ledger.LEDGER_FILENAME] == raw[work_card_ledger.LEDGER_FILENAME]
    assert len(ledger.list_cards()) == count + 1
    assert ledger.get_card(card) is not None
    assert new.status=='saved', f'new card blocked at {sum(map(len, raw.values()))} bytes/{count} cards: {new.status}/{new.reason}'


def test_overflow_record_atomic_replace_crash_is_recoverable_and_idempotent(tmp_path, monkeypatch):
    session, store, snapshot = make_session(tmp_path)
    ledger = WorkCardLedger(session, store)
    first = ledger.save_snapshot(str(uuid.uuid4()), snapshot)
    assert first.status == "saved"
    before = _ledger_files(ledger)
    request = str(uuid.uuid4())
    entry_name = f"{work_card_ledger.LEDGER_FILENAME}.entry-{_stable_card_id(session.conversation_id, request)}.json"
    real_replace = work_card_ledger.os.replace

    class SimulatedProcessCrash(BaseException):
        pass

    def replace_then_crash(source, target):
        real_replace(source, target)
        if Path(target).name == entry_name:
            raise SimulatedProcessCrash()

    monkeypatch.setattr(work_card_ledger.os, "replace", replace_then_crash)
    try:
        ledger.save_snapshot(request, snapshot)
        raise AssertionError("injected crash did not occur")
    except SimulatedProcessCrash:
        pass
    monkeypatch.setattr(work_card_ledger.os, "replace", real_replace)

    restored, restored_store = _reopen(session)
    reopened = WorkCardLedger(restored, restored_store)
    assert len(reopened.list_cards()) == 2
    saved = reopened.get_by_request_id(request)
    assert saved is not None and saved["card_id"] == _stable_card_id(session.conversation_id, request)
    replay = reopened.save_snapshot(request, snapshot)
    assert replay.status == "already_saved" and replay.record == saved
    assert set(_ledger_files(reopened)) == set(before) | {entry_name}


def test_second_shard_ack_survives_restart_when_each_shard_has_one_card(tmp_path, monkeypatch):
    monkeypatch.setattr(work_card_ledger, "_MAX_CARDS", 1)
    session, store, snapshot = make_session(tmp_path)
    ledger = WorkCardLedger(session, store)
    first = ledger.save_snapshot(str(uuid.uuid4()), snapshot)
    second = ledger.save_snapshot(str(uuid.uuid4()), snapshot)
    assert first.status == second.status == "saved"

    ack = ledger.acknowledge_viewed(second.card_id)

    assert ack.status == "acknowledged"
    assert ledger.get_card(first.card_id)["viewed_at"] is None
    assert ledger.get_card(second.card_id)["viewed_at"] == ack.viewed_at
    reopened, reopened_store = _reopen(session)
    reopened_ledger = WorkCardLedger(reopened, reopened_store)
    assert reopened_ledger.get_card(second.card_id)["viewed_at"] == ack.viewed_at
    assert reopened_ledger.acknowledge_viewed(second.card_id).status == "already_acknowledged"


@pytest.mark.parametrize("remove_card_index", [0, 1], ids=["legacy-root", "entry-shard"])
def test_private_removed_mutation_fails_closed_without_changing_any_ledger_file(tmp_path, remove_card_index):
    session, store, snapshot = make_session(tmp_path)
    ledger = WorkCardLedger(session, store)
    first = ledger.save_snapshot(str(uuid.uuid4()), snapshot)
    second = ledger.save_snapshot(str(uuid.uuid4()), snapshot)
    assert first.status == second.status == "saved"
    target = (first, second)[remove_card_index]
    before = _ledger_files(ledger)

    def remove_card(data):
        data["items"] = [row for row in data["items"] if row["card_id"] != target.card_id]
        return None, True

    with pytest.raises(WorkRecordWriteError, match="removal.*not supported|history.*removal"):
        ledger._ledger_store._mutate(remove_card)

    assert _ledger_files(ledger) == before
    assert ledger.get_card(first.card_id) is not None
    assert ledger.get_card(second.card_id) is not None


def test_shard_read_does_not_mutate_shared_ledger_path_during_concurrent_save(tmp_path, monkeypatch):
    from work_records import _file_lock

    session, store, snapshot = make_session(tmp_path)
    ledger = WorkCardLedger(session, store)
    first = ledger.save_snapshot(str(uuid.uuid4()), snapshot)
    second = ledger.save_snapshot(str(uuid.uuid4()), snapshot)
    assert first.status == second.status == "saved"
    shard_path = session.session_dir / f"{work_card_ledger.LEDGER_FILENAME}.entry-{second.card_id}.json"
    assert shard_path.is_file()

    read_entered = threading.Event()
    continue_read = threading.Event()
    writer_started = threading.Event()
    writer_done = threading.Event()
    outcome = {}
    original_read = work_records.WorkRecordStore._read

    def pause_shard_read(bound_store):
        if Path(bound_store._path) == shard_path:
            read_entered.set()
            if not continue_read.wait(5):
                raise TimeoutError("test did not release controlled shard read")
        return original_read(bound_store)

    monkeypatch.setattr(work_records.WorkRecordStore, "_read", pause_shard_read)

    def read_shard_under_session_lock():
        with _file_lock(store._lock_path, store._lock_timeout):
            ledger._ledger_store._read_at(shard_path)

    def save_from_same_ledger():
        writer_started.set()
        try:
            outcome["result"] = ledger.save_snapshot(str(uuid.uuid4()), snapshot)
        except BaseException as exc:
            outcome["exception"] = exc
        finally:
            writer_done.set()

    reader = threading.Thread(target=read_shard_under_session_lock, daemon=True)
    writer = threading.Thread(target=save_from_same_ledger, daemon=True)
    reader.start()
    assert read_entered.wait(5)
    writer.start()
    assert writer_started.wait(5)
    # A legitimate writer must wait for the OS lock, not reject because a reader
    # temporarily changed the shared _LedgerStore._path attribute.
    rejected_before_unlock = writer_done.wait(0.2)
    continue_read.set()
    reader.join(5)
    writer.join(5)

    assert not reader.is_alive() and not writer.is_alive()
    assert not rejected_before_unlock
    assert "exception" not in outcome
    assert outcome["result"].status == "saved"
    assert len(WorkCardLedger(session, store).list_cards()) == 3


def test_real_child_crash_after_shard_ack_replace_recovers_and_request_replay_is_idempotent(tmp_path):
    session, store, snapshot = make_session(tmp_path)
    ledger = WorkCardLedger(session, store)
    original_request = str(uuid.uuid4())
    original = ledger.save_snapshot(original_request, snapshot)
    target_request = str(uuid.uuid4())
    target = ledger.save_snapshot(target_request, snapshot)
    assert original.status == target.status == "saved"
    entry_path = session.session_dir / f"{work_card_ledger.LEDGER_FILENAME}.entry-{target.card_id}.json"
    assert entry_path.is_file()

    child = r'''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_card_ledger import WorkCardLedger
core, root_text, conversation_id, project_id, card_id, entry_name = sys.argv[1:]
root = Path(root_text)
paths = AgentPaths(agent_root=root, workspace=root/"workspace", data=root/"data", evolve=root/"evolve")
session = Session(conversation_id, paths.data/"sessions"/conversation_id, "synthetic", SessionMeta(project_id=project_id), [], paths)
ledger = WorkCardLedger(session, WorkRecordStore(session))
real_replace = os.replace
def replace_then_hard_exit(source, target):
    real_replace(source, target)
    if Path(target).name == entry_name:
        os._exit(73)
os.replace = replace_then_hard_exit
ledger.acknowledge_viewed(card_id)
raise SystemExit("ACK did not publish the expected entry shard")
'''
    completed = subprocess.run(
        [sys.executable, "-c", child, str(Path(__file__).resolve().parents[1]), str(session.paths.agent_root),
         session.conversation_id, session.meta.project_id, target.card_id, entry_path.name],
        cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, timeout=20,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    assert completed.returncode == 73, (completed.returncode, completed.stdout, completed.stderr)

    reopened, reopened_store = _reopen(session)
    recovered = WorkCardLedger(reopened, reopened_store)
    card = recovered.get_card(target.card_id)
    assert card is not None and card["viewed_at"] is not None
    replay = recovered.save_snapshot(target_request, snapshot)
    assert replay.status == "already_saved" and replay.card_id == target.card_id
    ack_retry = recovered.acknowledge_viewed(target.card_id)
    assert ack_retry.status == "already_acknowledged"
    assert ack_retry.viewed_at == card["viewed_at"]
    assert recovered.get_card(original.card_id)["viewed_at"] is None
