import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import asyncio
import uuid
from test_linked_project_session_delivery import setup_copy
import server
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations
from linked_snapshot_view_evidence import LinkedSnapshotViewEvidence, _SnapshotEvidenceStore
import work_record_visibility_clock as clock
from test_linked_snapshot_view_evidence import sample


def test_linked_view_handler_identity_receipt_and_retry(tmp_path, monkeypatch):
    async def run():
        parent, ns, rows, store, events, bridge, repl = setup_copy(tmp_path)
        assert server._resume_linked_project_session_copies(repl, bridge, parent, store, emit_event=True) == 1
        event = next(e for e in events if e.get('type') == 'work-card.present')
        pid, card = event['presentation_id'], event['card_id']
        handler = server.WsSessionHandler(parent.paths)
        request = {'type':'work-card.snapshot.view', 'session_id':parent.conversation_id, 'presentation_id':pid, 'card_id':card}
        ledger = WorkCardLedger(parent, store)
        presentations = WorkCardPresentations(parent, store, ledger)
        evidence = LinkedSnapshotViewEvidence(parent, store, ledger)
        monkeypatch.setattr(clock, 'sample_elapsed_clock', lambda: sample(100))
        assert presentations.mark_transport_offer(pid, card).status == 'offered'
        await handler._handle_incoming({**request, 'type':'work-card.received'}, repl, bridge)
        assert events[-1]['status'] == 'received'
        assert evidence.elapsed(pid, card).status == 'not_viewed'
        for bad in ({**request, 'session_id':'foreign'}, {**request, 'presentation_id':str(uuid.uuid4())}, {**request, 'card_id':'wc-'+'0'*64}, {**request, 'extra':True}):
            await handler._handle_incoming(bad, repl, bridge)
            assert events[-1]['status'] not in {'acknowledged','already_acknowledged'}
        assert evidence.elapsed(pid, card).status == 'not_viewed'
        original = _SnapshotEvidenceStore._publish
        monkeypatch.setattr(_SnapshotEvidenceStore, '_publish', lambda *a, **k: (_ for _ in ()).throw(OSError('disk')))
        await handler._handle_incoming(request, repl, bridge)
        assert events[-1]['status'] == 'unavailable'
        assert presentations.get(pid).fact.acknowledged_at is not None
        assert evidence.elapsed(pid, card).status == 'not_viewed'
        monkeypatch.setattr(_SnapshotEvidenceStore, '_publish', original)
        await handler._handle_incoming(request, repl, bridge)
        assert events[-1]['status'] == 'acknowledged'
        await handler._handle_incoming(request, repl, bridge)
        assert events[-1]['status'] == 'already_acknowledged'
        assert evidence.elapsed(pid, card).status == 'waiting'
        monkeypatch.setattr(clock, 'sample_elapsed_clock', lambda: sample(1900))
        assert evidence.elapsed(pid, card).status == 'elapsed'
    asyncio.run(run())



def test_view_lease_blocks_session_switch_until_disk_operation_exits(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace
    async def run():
        parent, ns, rows, store, events, bridge, repl = setup_copy(tmp_path)
        assert server._resume_linked_project_session_copies(repl, bridge, parent, store, emit_event=True) == 1
        event = next(e for e in events if e.get('type') == 'work-card.present')
        request = {'type':'work-card.snapshot.view', 'session_id':parent.conversation_id,
                   'presentation_id':event['presentation_id'], 'card_id':event['card_id']}
        monkeypatch.setattr(clock, 'sample_elapsed_clock', lambda: sample(100))
        entered, release = threading.Event(), threading.Event()
        original = WorkCardPresentations.acknowledge
        def blocked(self, *args, **kwargs):
            entered.set()
            if not release.wait(5): raise TimeoutError('test release timed out')
            return original(self, *args, **kwargs)
        monkeypatch.setattr(WorkCardPresentations, 'acknowledge', blocked)
        handler = server.WsSessionHandler(parent.paths)
        other = SimpleNamespace()
        switched = asyncio.Event()
        original_dispatch = handler._dispatch
        async def dispatch(message, repl, bridge, **kwargs):
            if message.get('type') == 'session.open':
                repl.session = other
                repl.agent.session = other
                switched.set()
                return
            return await original_dispatch(message, repl, bridge, **kwargs)
        monkeypatch.setattr(handler, '_dispatch', dispatch)
        view = asyncio.create_task(handler._handle_incoming(request, repl, bridge))
        switch = None
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            switch = asyncio.create_task(handler._handle_incoming({'type':'session.open'}, repl, bridge))
            for _ in range(100):
                if bridge._session_binding_transitions: break
                await asyncio.sleep(.01)
            assert bridge._session_binding_transitions
            await handler._handle_incoming(request, repl, bridge)
            assert events[-1]['status'] == 'session_unavailable'
            assert not switched.is_set()
            release.set()
            await asyncio.wait_for(view, 3)
            await asyncio.wait_for(switch, 3)
            assert switched.is_set()
            await handler._handle_incoming(request, repl, bridge)
            assert events[-1]['status'] == 'session_unavailable'
        finally:
            release.set()
            await asyncio.gather(view, return_exceptions=True)
            if switch: await asyncio.gather(switch, return_exceptions=True)
    asyncio.run(run())


def test_conflicting_history_presentation_fails_closed(tmp_path, monkeypatch):
    async def run():
        parent, ns, rows, store, events, bridge, repl = setup_copy(tmp_path)
        server._resume_linked_project_session_copies(repl, bridge, parent, store, emit_event=True)
        event = next(e for e in events if e.get('type') == 'work-card.present')
        reference = next(m for m in parent.messages if m.get('presentation_id') == event['presentation_id'])
        parent.messages.append({**reference, 'card_id':'wc-'+'0'*64})
        parent.save()
        monkeypatch.setattr(clock, 'sample_elapsed_clock', lambda: sample(100))
        request = {'type':'work-card.snapshot.view', 'session_id':parent.conversation_id,
                   'presentation_id':event['presentation_id'], 'card_id':event['card_id']}
        await server.WsSessionHandler(parent.paths)._handle_incoming(request, repl, bridge)
        assert events[-1]['status'] == 'rejected'
        assert LinkedSnapshotViewEvidence(parent, store, WorkCardLedger(parent, store)).elapsed(event['presentation_id'], event['card_id']).status == 'not_viewed'
    asyncio.run(run())



def test_snapshot_view_write_crash_reopens_real_handler_retry_without_confusing_received(tmp_path):
    import json
    import os
    import subprocess
    from test_linked_source_snapshot_overview import _confirmed_project_copies
    from work_card_presentations import WorkCardPresentations

    parent, store, _bindings, _rows, _sources, events, _bridge, _repl = _confirmed_project_copies(
        tmp_path, terminal_source=True,
    )
    parent.save()
    cards = [row for row in WorkCardLedger(parent, store).list_cards()
             if row["snapshot"].get("kind") == "linked_project_session_copy"]
    card = next(row for row in cards if row["snapshot"]["source"]["project_id"] == "project-a")
    reference = next(message for message in parent.messages
                     if message.get("role") == "work_card_presentation" and message.get("card_id") == card["card_id"])
    presentation_id, card_id = reference["presentation_id"], card["card_id"]
    assert WorkCardPresentations(parent, store, WorkCardLedger(parent, store)).mark_transport_offer(
        presentation_id, card_id,
    ).status == "offered"

    child = r'''
import asyncio, json, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
root, mode, presentation_id, card_id = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
sys.path.insert(0, str(root / "agent-core"))
from paths import AgentPaths
from session import Session
from work_records import WorkRecordStore
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations
from linked_snapshot_view_evidence import LinkedSnapshotViewEvidence, _SnapshotEvidenceStore
from work_record_visibility_clock import ElapsedClockSample
import work_record_visibility_clock as clock
from server import WsBridge, WsSessionHandler
from work_records_overview_api import query_work_records_overview
paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
session = Session.load(paths, "parent-dialog", message_cap=None)
store = WorkRecordStore(session); ledger = WorkCardLedger(session, store)
events=[]; bridge = WsBridge(emit=events.append, paths=paths)
repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
handler = WsSessionHandler(paths)
request = {"type":"work-card.snapshot.view", "session_id":session.conversation_id,
           "presentation_id":presentation_id, "card_id":card_id}
received = {"type":"work-card.received", "session_id":session.conversation_id,
           "presentation_id":presentation_id, "card_id":card_id}

def tick(seconds):
    wall = datetime(2026,10,31,tzinfo=timezone.utc) + timedelta(seconds=seconds)
    return ElapsedClockSample("restart-test-boot", seconds*1_000_000_000, wall.isoformat().replace("+00:00","Z"))

async def main():
    if mode == "crash":
        _SnapshotEvidenceStore._publish = lambda *a,**k: (_ for _ in ()).throw(OSError("synthetic evidence disk failure"))
        await handler._handle_incoming(request, repl, bridge)
        assert events[-1]["type"] == "work-card.snapshot.view.done"
        assert events[-1]["status"] == "unavailable"
        assert WorkCardPresentations(session,store,ledger).get(presentation_id).fact.acknowledged_at is not None
        assert LinkedSnapshotViewEvidence(session,store,ledger).elapsed(presentation_id,card_id).status == "not_viewed"
        os._exit(73)

    now = {"seconds":100}
    clock.sample_elapsed_clock = lambda: tick(now["seconds"])
    await handler._handle_incoming(received, repl, bridge)
    assert events[-1]["type"] == "work-card.received.done"
    assert events[-1]["status"] in {"received", "already_received"}
    evidence = LinkedSnapshotViewEvidence(session,store,ledger)
    assert evidence.elapsed(presentation_id,card_id).status == "not_viewed"
    before = query_work_records_overview(session,page_size=10)
    group = next(g for g in before["groups"] if g["kind"] == "linked_source_snapshot"
                 and g["source_snapshot"]["project_id"] == "project-a")
    assert group["visibility"] == "visible" and group["visibility_timer_status"] == "not_viewed"

    # Explicit retry through the same host message handler fills the missing evidence.
    await handler._handle_incoming(request, repl, bridge)
    assert events[-1]["type"] == "work-card.snapshot.view.done"
    assert events[-1]["status"] in {"acknowledged", "already_acknowledged"}
    assert evidence.elapsed(presentation_id,card_id).status == "waiting"
    after = query_work_records_overview(session,page_size=10)
    group = next(g for g in after["groups"] if g["kind"] == "linked_source_snapshot"
                 and g["source_snapshot"]["project_id"] == "project-a")
    assert group["visibility"] == "visible" and group["visibility_timer_status"] == "waiting"
    assert group["attention_status"] == "unknown"

    now["seconds"] += 31*60
    matured = query_work_records_overview(session,page_size=10)
    assert not any(g["kind"] == "linked_source_snapshot" and g["source_snapshot"]["project_id"] == "project-a"
                   for g in matured["groups"])
    assert matured["visibility_counts"]["collapsed_groups"] >= 1
    print(json.dumps({"state":matured["state"]}))

asyncio.run(main())
'''
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH"}}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    args = [sys.executable, "-c", child, str(tmp_path)]
    crash = subprocess.run(args + ["crash", presentation_id, card_id],
        cwd=Path(__file__).resolve().parents[1], env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False)
    assert crash.returncode == 73, f"stdout={crash.stdout[-1000:]!r}; stderr={crash.stderr[-1500:]!r}"
    recovered = subprocess.run(args + ["recover", presentation_id, card_id],
        cwd=Path(__file__).resolve().parents[1], env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45, check=False)
    assert recovered.returncode == 0, f"stdout={recovered.stdout[-1500:]!r}; stderr={recovered.stderr[-2000:]!r}"
    assert json.loads(recovered.stdout)["state"] in {"visible", "collapsed"}
