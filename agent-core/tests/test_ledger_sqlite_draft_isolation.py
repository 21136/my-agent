from __future__ import annotations
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_work_card_ledger import make_session
from work_card_ledger import WorkCardLedger
from ledger_sqlite_draft_fixture import IsolatedLedgerDraft, MigrationUnavailable


def _setup(tmp_path):
    session, store, snapshot = make_session(tmp_path)
    ledger = WorkCardLedger(session, store)
    first=ledger.save_snapshot(str(uuid.uuid4()),snapshot)
    assert first.status == 'saved'
    return session, ledger, snapshot


def test_legacy_writer_race_rolls_back_draft_and_activation_remains_unavailable(tmp_path):
    session, ledger, snapshot = _setup(tmp_path)
    path=ledger._ledger_store._path
    before=path.read_bytes()
    draft=IsolatedLedgerDraft(session,ledger,tmp_path)
    def legacy_write():
        assert ledger.save_snapshot(str(uuid.uuid4()),snapshot).status == 'saved'
    with pytest.raises(MigrationUnavailable,match='changed'):
        draft.prepare(after_snapshot=legacy_write)
    assert path.read_bytes()!=before  # Only the simulated old writer wrote v1.
    assert len(ledger.list_cards())==2
    with sqlite3.connect(draft.db) as db:
        assert db.execute('SELECT count(*) FROM cards').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM metadata').fetchone()[0]==0
    with pytest.raises(MigrationUnavailable): draft.activate()
    with pytest.raises(MigrationUnavailable): draft.save()


def test_valid_draft_never_authorizes_second_writer_or_modifies_v1(tmp_path):
    session, ledger, snapshot = _setup(tmp_path)
    before=ledger._ledger_store._path.read_bytes()
    draft=IsolatedLedgerDraft(session,ledger,tmp_path)
    assert draft.prepare()=='unpublished'
    assert ledger._ledger_store._path.read_bytes()==before
    with sqlite3.connect(draft.db) as db:
        assert db.execute('SELECT state FROM metadata').fetchone()==('unpublished',)
        assert db.execute('SELECT count(*) FROM cards').fetchone()[0]==1
    with pytest.raises(MigrationUnavailable): draft.activate()
    with pytest.raises(MigrationUnavailable): draft.save()
    # Legacy can still write: a scratch SQLite draft is not an exclusive gate.
    assert ledger.save_snapshot(str(uuid.uuid4()),snapshot).status=='saved'
    with pytest.raises(MigrationUnavailable): draft.activate()
    assert len(ledger.list_cards())==2


@pytest.mark.parametrize('phase,code,count',[('before_commit',73,0),('after_commit',74,1)])
def test_process_crash_draft_never_becomes_authoritative(tmp_path, phase, code, count):
    session, ledger, snapshot = _setup(tmp_path)
    before=ledger._ledger_store._path.read_bytes()
    core=Path(__file__).resolve().parents[1]
    script='''
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]); sys.path.insert(0,str(Path(sys.argv[1])/'tests'))
from paths import AgentPaths
from session import Session
from work_records import WorkRecordStore
from work_card_ledger import WorkCardLedger
from ledger_sqlite_draft_fixture import IsolatedLedgerDraft
root=Path(sys.argv[2]); agent=root/'agent-root'
paths=AgentPaths(agent_root=agent,workspace=agent/'workspace',data=agent/'data',evolve=agent/'evolve')
session=Session.load(paths,sys.argv[3],message_cap=None)
ledger=WorkCardLedger(session,WorkRecordStore(session))
IsolatedLedgerDraft(session,ledger,root).prepare(crash_phase=sys.argv[4])
'''
    env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','PATH','PATHEXT'}}
    env.update({'TEMP':str(tmp_path.parent),'TMP':str(tmp_path.parent),'HOME':str(tmp_path),'USERPROFILE':str(tmp_path),
                'APPDATA':str(tmp_path),'LOCALAPPDATA':str(tmp_path),'PYTHONNOUSERSITE':'1'})
    child=subprocess.run([sys.executable,'-I','-B','-c',script,str(core),str(tmp_path),session.conversation_id,phase],
                         cwd=str(core),env=env,capture_output=True,text=True,timeout=15)
    assert child.returncode==code, child.stderr
    assert ledger._ledger_store._path.read_bytes()==before
    draft=IsolatedLedgerDraft(session,ledger,tmp_path)
    with sqlite3.connect(draft.db) as db:
        assert db.execute('SELECT count(*) FROM cards').fetchone()[0]==count
        assert db.execute('SELECT count(*) FROM metadata').fetchone()[0]==count
    with pytest.raises(MigrationUnavailable): draft.activate()
    with pytest.raises(MigrationUnavailable): draft.save()
    assert len(ledger.list_cards())==1


def test_actual_legacy_subprocess_write_cannot_activate_draft(tmp_path):
    session, ledger, snapshot = _setup(tmp_path)
    draft=IsolatedLedgerDraft(session,ledger,tmp_path)
    core=Path(__file__).resolve().parents[1]
    script='''
import sys,uuid
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from paths import AgentPaths
from session import Session
from work_records import WorkRecordStore
from work_card_ledger import WorkCardLedger
from work_record_cards import build_project_work_record_cards
root=Path(sys.argv[2])/'agent-root'
paths=AgentPaths(agent_root=root,workspace=root/'workspace',data=root/'data',evolve=root/'evolve')
s=Session.load(paths,sys.argv[3],message_cap=None)
store=WorkRecordStore(s)
snapshot=build_project_work_record_cards(store,'synthetic-project')
assert WorkCardLedger(s,store).save_snapshot(str(uuid.uuid4()),snapshot).status=='saved'
'''
    env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','PATH','PATHEXT'}}
    env.update({'TEMP':str(tmp_path.parent),'TMP':str(tmp_path.parent),'HOME':str(tmp_path),'USERPROFILE':str(tmp_path),
                'APPDATA':str(tmp_path),'LOCALAPPDATA':str(tmp_path),'PYTHONNOUSERSITE':'1'})
    def old_writer():
        child=subprocess.run([sys.executable,'-I','-B','-c',script,str(core),str(tmp_path),session.conversation_id],
                             cwd=str(core),env=env,capture_output=True,text=True,timeout=15)
        assert child.returncode==0,child.stderr
    with pytest.raises(MigrationUnavailable,match='changed'):
        draft.prepare(after_snapshot=old_writer)
    assert len(ledger.list_cards())==2
    with sqlite3.connect(draft.db) as db:
        assert db.execute('SELECT count(*) FROM cards').fetchone()[0]==0
    with pytest.raises(MigrationUnavailable): draft.activate()
