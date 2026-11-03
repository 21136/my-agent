from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path
from types import SimpleNamespace

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from server import WsBridge, _begin_work_record_run_binding, _settle_work_record_run
from work_records import WorkRecordStore
from work_records_experiment import WorkRecordExperimentController

_PROJECT_ID = "synthetic-project-scope"
_SESSION_ID = "synthetic-stage1-session"


def _open_store(agent_root: Path) -> WorkRecordStore:
    paths = AgentPaths(
        agent_root=agent_root,
        workspace=agent_root / "workspace",
        data=agent_root / "data",
        evolve=agent_root / "evolve",
    )
    session_dir = paths.data / "sessions" / _SESSION_ID
    session_dir.mkdir(parents=True, exist_ok=True)
    session = Session(
        conversation_id=_SESSION_ID,
        session_dir=session_dir,
        goal="synthetic stage1 acceptance only",
        meta=SessionMeta(project_id=_PROJECT_ID),
        messages=[],
        paths=paths,
    )
    return WorkRecordStore(session)


def _reopen_and_project(agent_root: str, queue) -> None:
    """Simulate a fresh process reading only the persisted current-session sidecar."""
    store = _open_store(Path(agent_root))
    items = store.list_items()
    queue.put((items, WorkRecordExperimentController._format_recall(items)))


def _add_requirement(store: WorkRecordStore, item_id: str, requirement_id: str, text: str) -> dict:
    # Stable synthetic event IDs are just write identities, not message locators.
    return store.add_requirement(item_id, text, event_id=f"synthetic-event-{requirement_id}")


def test_synthetic_single_session_project_and_independent_component_flow_survives_restart(tmp_path):
    """Synthetic component flow only: no Agent, tool, model, network, or real user path."""
    agent_root = tmp_path / "synthetic-agent-root"
    agent_root.mkdir()
    store = _open_store(agent_root)
    session = store._session

    project_item = store.create_item(
        title="Synthetic project item",
        scope={"kind": "project", "project_id": _PROJECT_ID},
    )
    project_unknown_req = _add_requirement(
        store, project_item["item_id"], "project-unknown", "Synthetic project work awaiting proof",
    )
    project_fixture_req = _add_requirement(
        store, project_item["item_id"], "project-fixture", "Synthetic fixture-verified project check",
    )

    independent_item = store.create_item(
        title="Synthetic independent item",
        scope={"kind": "independent"},
    )
    independent_fixture_req = _add_requirement(
        store, independent_item["item_id"], "independent-fixture", "Synthetic fixture-verified independent check",
    )
    independent_open_req = _add_requirement(
        store, independent_item["item_id"], "independent-open", "Synthetic independent work still unresolved",
    )

    # Exercise the existing server binding/settlement component for one requirement.
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    snapshot = bridge.lifecycle.snapshot()
    assert snapshot is not None
    bridge.lifecycle_conversation_id = session.conversation_id
    bridge._turn_busy.set()
    capture = SimpleNamespace(
        status="recorded",
        item_id=project_item["item_id"],
        requirement_id=project_unknown_req["requirement_id"],
    )
    binding = _begin_work_record_run_binding(
        session=session,
        capture=capture,
        run_id=snapshot.run_id,
        current_session=session,
        bridge=bridge,
        emit=bridge.emit_turn_event,
        request_id=None,
    )
    assert isinstance(binding, tuple)
    run_store, bound = binding
    assert bridge.finish_execution("completed", ok=True, run_id=snapshot.run_id)
    assert _settle_work_record_run(
        current_session=session,
        bridge=bridge,
        store=run_store,
        binding=bound,
        emit=bridge.emit_turn_event,
    )

    # These two terminal records are explicit synthetic fixtures, not real evidence.
    for item_id, requirement, run_id in (
        (project_item["item_id"], project_fixture_req, "fixture-project-run"),
        (independent_item["item_id"], independent_fixture_req, "fixture-independent-run"),
    ):
        store.begin_requirement_run(
            item_id, requirement["requirement_id"], run_id=run_id,
            expected_current_run_id=None,
        )
        store.record_result_if_current_run(
            item_id, requirement["requirement_id"], run_id=run_id,
            result_id=f"fixture-result-{run_id}", status="completed",
            summary="Synthetic trusted fixture result; not a live execution claim.",
            verification={"status": "synthetic_fixture_verified", "fixture_only": True},
            sources=[],
        )

    # A second persisted begin remains uncertain; this test never starts execution.
    store.begin_requirement_run(
        independent_item["item_id"], independent_open_req["requirement_id"],
        run_id="fixture-independent-open-run", expected_current_run_id=None,
    )
    before_read = store._path.read_bytes()

    # A separate OS process reopens the same temporary sidecar, then uses the existing
    # current-session recall projection. This is not a full user-message/Agent E2E test.
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    process = ctx.Process(target=_reopen_and_project, args=(str(agent_root), queue))
    process.start()
    process.join(20)
    assert process.exitcode == 0
    items, rendered = queue.get(timeout=3)
    assert store._path.read_bytes() == before_read  # read projection did not mutate storage

    by_title = {item["title"]: item for item in items}
    project = by_title["Synthetic project item"]
    independent = by_title["Synthetic independent item"]
    assert project["scope"] == {"kind": "project", "project_id": _PROJECT_ID}
    assert independent["scope"]["kind"] == "independent"
    assert project["item_id"] != independent["item_id"]
    assert {req["text"] for req in project["requirements"]} == {
        "Synthetic project work awaiting proof", "Synthetic fixture-verified project check",
    }
    assert {req["text"] for req in independent["requirements"]} == {
        "Synthetic fixture-verified independent check", "Synthetic independent work still unresolved",
    }

    project_requirements = {req["text"]: req for req in project["requirements"]}
    independent_requirements = {req["text"]: req for req in independent["requirements"]}
    assert project_requirements["Synthetic project work awaiting proof"]["status"] == "unknown"
    assert project_requirements["Synthetic fixture-verified project check"]["status"] == "completed"
    assert independent_requirements["Synthetic fixture-verified independent check"]["status"] == "completed"
    assert independent_requirements["Synthetic independent work still unresolved"]["status"] == "in_progress"
    assert project["status"] == "unknown"
    assert independent["status"] == "in_progress"
    assert all(item["status"] != "completed" for item in items)

    project_unknown_run = next(
        run for run in project["runs"] if run["requirement_id"] == project_unknown_req["requirement_id"]
    )
    assert project_unknown_run["status"] == "unknown"
    verification = project_unknown_run["verification"]
    assert verification["status"] == "not_verified"
    assert verification["evidence_refs"] == []
    assert set(verification) == {"status", "evidence_refs", "host_execution"}
    host_execution = verification["host_execution"]
    assert set(host_execution) == {"sequence", "finish_reason"}
    assert type(host_execution["sequence"]) is int and host_execution["sequence"] >= 1
    assert host_execution["finish_reason"] == "completed"
    assert project_requirements["Synthetic project work awaiting proof"]["status"] == "unknown"
    assert project_unknown_run["status"] == "unknown"
    assert all(not req.get("source_ref") for item in items for req in item["requirements"])
    assert all(not item.get("source_refs") for item in items)
    assert all(not run.get("sources") for item in items for run in item["runs"])

    # No request IDs, synthetic event IDs, or fabricated message locations are exposed
    # as source citations when this component flow has no stable message locator.
    assert "synthetic-event-" not in rendered
    assert "message://" not in rendered
    assert "source://" not in rendered
    assert "Synthetic project item" in rendered
    assert "Synthetic independent item" in rendered
    assert "\u5df2\u8bb0\u5f55\u5f00\u59cb\uff0c\u5f53\u524d\u6267\u884c\u72b6\u6001\u5f85\u786e\u8ba4" in rendered
