from pathlib import Path
import sys
from types import SimpleNamespace

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_records_experiment import WorkRecordExperimentController


def context(tmp_path):
    root = tmp_path / "agent-root"
    paths = AgentPaths(root, root / "workspace", root / "data", root / "evolve")
    for directory in (paths.workspace, paths.data, paths.evolve):
        directory.mkdir(parents=True, exist_ok=True)
    session_dir = paths.data / "sessions" / "synthetic-experiment"
    session_dir.mkdir(parents=True)
    session = Session("synthetic-experiment", session_dir, "", SessionMeta(), [], paths)
    calls = []
    agent = SimpleNamespace(session=session, run_turn=lambda text: calls.append(text) or SimpleNamespace(assistant_text="synthetic answer", tool_rounds=0))
    return agent, WorkRecordStore(session), calls


def record_decision():
    return {
        "action": "record", "scope": {"kind": "independent"},
        "title": "synthetic experiment", "requirement": "synthetic requirement",
    }


def test_explicit_experiment_requires_caller_id_without_leaving_empty_item(tmp_path):
    agent, store, calls = context(tmp_path)
    controller = WorkRecordExperimentController(agent=agent, store=store, recognizer=lambda *_: record_decision())

    result = controller.run_turn("synthetic explicit record request")

    assert result.turn is None
    assert result.storage_error == "WorkRecordIdempotencyRequired"
    assert store.list_items() == []
    assert calls == []


def test_explicit_experiment_with_stable_synthetic_id_writes_atomically(tmp_path):
    agent, store, calls = context(tmp_path)
    controller = WorkRecordExperimentController(
        agent=agent,
        store=store,
        recognizer=lambda *_: record_decision(),
        idempotency_key_provider=lambda _text: "synthetic-experiment-request:one",
    )

    result = controller.run_turn("synthetic explicit record request")

    assert result.turn.assistant_text == "synthetic answer"
    assert result.item_id is not None
    assert store.get_item(result.item_id)["requirements"][0]["text"] == "synthetic requirement"
    assert calls == ["synthetic explicit record request"]
