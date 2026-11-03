from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from project_cli import bind_project_session
from project_mode import create_project
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations
from work_record_cards import build_independent_work_record_card

BOOT_ID = "synthetic-same-startup-20261030"
ACK_MONO = 8_000_000_000
THIRTY_MINUTES_NS = 30 * 60 * 1_000_000_000


def _run_child(code: str, *args: object) -> dict:
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code), *(str(arg) for arg in args)],
        cwd=_CORE,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
        env={**os.environ, "PYTHONPATH": str(_CORE)},
    )
    assert completed.returncode == 0, (
        f"child exit={completed.returncode}\nstdout={completed.stdout}\nstderr={completed.stderr}"
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _new_item(store: WorkRecordStore, title: str, *, status: str = "completed") -> dict:
    item = store.create_item(title=title, scope={"kind": "independent"})
    req = store.add_requirement(item["item_id"], f"requirement for {title}", event_id=f"event-{title}")
    store.begin_requirement_run(item["item_id"], req["requirement_id"], run_id=f"run-{title}",
                               expected_current_run_id=None)
    store.record_result_if_current_run(
        item["item_id"], req["requirement_id"], run_id=f"run-{title}", result_id=f"result-{title}",
        status=status, summary=f"summary for {title}", verification={"status": "not_verified"},
    )
    return {"item": item, "requirement": req, "result_id": f"result-{title}"}


def _publish_card(session: Session, store: WorkRecordStore, row: dict) -> tuple[str, str]:
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(str(uuid.uuid4()), build_independent_work_record_card(store, row["item"]["item_id"]))
    assert saved.status == "saved"
    presentation_id = str(uuid.uuid4())
    assert WorkCardPresentations(session, store, ledger).create(presentation_id, saved.card_id).status == "created"
    session.append_sent_work_card_reference(presentation_id, saved.card_id, run_id=str(uuid.uuid4()))
    return saved.card_id, presentation_id


def test_server_result_detail_ack_survives_process_restart_and_projects_by_independent_elapsed_time(tmp_path):
    """Synthetic 30-minute clock: real sidecar writes are reopened in fresh Python processes."""
    agent_root = tmp_path / "synthetic-agent"
    paths = AgentPaths(agent_root=agent_root, workspace=agent_root / "workspace",
                       data=agent_root / "data", evolve=agent_root / "evolve")
    paths.workspace.mkdir(parents=True)
    create_project(paths, "synthetic-project")
    session_id = "20261030-1234abcd"
    session_dir = paths.data / "sessions" / session_id
    session_dir.mkdir(parents=True)
    session = Session(session_id, session_dir, "synthetic elapsed projection", SessionMeta(), [], paths)
    bind_project_session(session, "synthetic-project", plan_status="draft")
    session.save()
    store = WorkRecordStore(session)

    mature = _new_item(store, "mature acknowledged result")
    late = _new_item(store, "independently timed result with only 25 minutes")
    stale = _new_item(store, "superseded acknowledged result")
    unread = _new_item(store, "unread completed result")
    blocked = _new_item(store, "blocked result", status="blocked")
    published = {row["item"]["item_id"]: _publish_card(session, store, row)
                 for row in (mature, late, stale, unread, blocked)}

    # Process 1: use the real server result-detail handler and persist ACKs at
    # synthetic monotonic samples. No real 30-minute sleep is performed.
    ack_code = r'''
import asyncio, json, sys
from datetime import datetime, timedelta, timezone
from paths import AgentPaths
from session import load_session_for_harness
from server import WsBridge, WsSessionHandler
import work_record_visibility_clock as clock
from work_record_visibility_clock import ElapsedClockSample
import server
root, session_id, boot_id, mono, requests_json = sys.argv[1:]
root = __import__('pathlib').Path(root)
paths = AgentPaths(agent_root=root, workspace=root/'workspace', data=root/'data', evolve=root/'evolve')
session = load_session_for_harness(paths, session_id, expected='desktop', full_messages=True)
events=[]
bridge=WsBridge(emit=events.append, paths=paths)
repl=server._build_repl(session, paths, bridge)
handler=WsSessionHandler(paths)
requests=json.loads(requests_json)
async def main():
    statuses=[]
    for request, offset in requests:
        wall=(datetime(2026, 10, 30, tzinfo=timezone.utc)+timedelta(microseconds=int(offset)//1000)).isoformat().replace('+00:00','Z')
        clock.sample_elapsed_clock=lambda: ElapsedClockSample(boot_id, int(mono)+int(offset), wall)
        await handler._handle_incoming(request, repl, bridge)
        statuses.append(events[-1].get('status'))
    print(json.dumps({'statuses':statuses, 'pid':__import__('os').getpid()}))
asyncio.run(main())
'''
    reqs = []
    for row, offset in ((mature, 0), (late, 5 * 60 * 1_000_000_000), (stale, 0)):
        card_id, presentation_id = published[row["item"]["item_id"]]
        reqs.append(({
            "type": "work-card.result.view", "session_id": session_id,
            "presentation_id": presentation_id, "card_id": card_id,
            "result_id": row["result_id"], "requirement_id": row["requirement"]["requirement_id"],
        }, offset))
    first_process = _run_child(ack_code, agent_root, session_id, BOOT_ID, ACK_MONO, json.dumps(reqs))
    assert first_process["statuses"] == ["acknowledged", "acknowledged", "acknowledged"]

    # Change the stale item's current result after its old result ACK. Its old
    # card/result proof must not transfer to the new current result.
    store.begin_requirement_run(stale["item"]["item_id"], stale["requirement"]["requirement_id"],
                                run_id="new-current-run", expected_current_run_id="run-superseded acknowledged result")
    store.record_result_if_current_run(
        stale["item"]["item_id"], stale["requirement"]["requirement_id"], run_id="new-current-run",
        result_id="new-current-result", status="completed", summary="replacement result",
        verification={"status": "not_verified"},
    )

    # Process 2: independent interpreter and newly loaded Session/sidecars.
    # This advances a SYNTHETIC sample exactly 30m after ACK; it is not wall wait.
    projection_code = r'''
import json, sys
from pathlib import Path
from paths import AgentPaths
from session import load_session_for_harness
from work_records import WorkRecordStore
from work_record_visibility_clock import ElapsedClockSample
import work_record_visibility_clock as clock
from work_record_visibility_projection import project_visible_items
from work_card_ledger import WorkCardLedger
root, session_id, boot_id, mono = sys.argv[1:]
root=Path(root)
paths=AgentPaths(agent_root=root, workspace=root/'workspace', data=root/'data', evolve=root/'evolve')
session=load_session_for_harness(paths, session_id, expected='desktop', full_messages=True)
clock.sample_elapsed_clock=lambda: ElapsedClockSample(boot_id, int(mono), '2026-10-30T00:30:00Z')
store=WorkRecordStore(session)
items=store.list_items()
visible=project_visible_items(session, store, items)
ledger=WorkCardLedger(session, store)
print(json.dumps({'visible_titles':[item['title'] for item in visible],
                  'all_titles':[item['title'] for item in items],
                  'history_cards':len(ledger.list_cards()), 'pid':__import__('os').getpid()}))
'''
    second_process = _run_child(projection_code, agent_root, session_id, BOOT_ID, ACK_MONO + THIRTY_MINUTES_NS)
    assert first_process["pid"] != second_process["pid"]
    visible = second_process["visible_titles"]
    assert "mature acknowledged result" not in visible
    assert "independently timed result with only 25 minutes" in visible
    assert "superseded acknowledged result" in visible
    assert "unread completed result" in visible
    assert "blocked result" in visible
    assert second_process["history_cards"] == 5
    assert len(second_process["all_titles"]) == 5  # projection does not delete source records


def test_windows_elapsed_clock_is_read_only_and_stable_across_fresh_processes():
    if os.name != "nt":
        pytest.skip("Windows-only OS clock probe")
    probe = r'''
import json, time
import work_record_visibility_clock as clock
first=clock.sample_elapsed_clock()
time.sleep(0.05)
second=clock.sample_elapsed_clock()
print(json.dumps({'first':None if first is None else first.__dict__,
                  'second':None if second is None else second.__dict__, 'pid':__import__('os').getpid()}))
'''
    readings = [_run_child(probe), _run_child(probe)]
    for result in readings:
        first, second = result["first"], result["second"]
        assert first is not None and second is not None
        assert first["boot_id"] == second["boot_id"]
        assert second["monotonic_ns"] >= first["monotonic_ns"]
        assert second["wall_utc"] >= first["wall_utc"]
    assert readings[0]["first"]["boot_id"] == readings[1]["first"]["boot_id"]






