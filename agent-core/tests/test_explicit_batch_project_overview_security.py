from __future__ import annotations

import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_records_overview import _trusted_worker_batch_projects, _overview_items


def _batch(tmp_path: Path):
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace",
                       data=tmp_path / "data", evolve=tmp_path / "evolve")
    session_dir = paths.data / "sessions" / "one-current-session"
    session_dir.mkdir(parents=True)
    session = Session("one-current-session", session_dir, "fixture", SessionMeta(project_id="alpha"), [], paths)
    store = WorkRecordStore(session)
    digest = "a" * 64
    created = store.create_item_with_requirement(
        title="多项目任务批次", scope={"kind": "independent"}, requirement="original request",
        event_id=f"worker-batch-original-{digest}",
        idempotency_key=f"worker-batch-original:{digest}",
    )
    item = created[0] if isinstance(created, tuple) else created
    alpha = store.add_requirement(
        item["item_id"], "alpha task", source_ref={"kind": "project", "id": "alpha"},
        event_id=f"worker-batch-task-{digest}-0", idempotency_key=f"worker-batch-task:{digest}:0",
    )
    beta = store.add_requirement(
        item["item_id"], "beta task", source_ref={"kind": "project", "id": "beta"},
        event_id=f"worker-batch-task-{digest}-1", idempotency_key=f"worker-batch-task:{digest}:1",
    )
    independent = store.add_requirement(item["item_id"], "unassigned task", event_id="ordinary-independent")
    return session, store, item["item_id"], {"alpha": alpha, "beta": beta, "independent": independent}


def test_trusted_batch_maps_bound_and_nonbound_projects_without_absorbing_independent_work(tmp_path):
    _session, store, item_id, requirements = _batch(tmp_path)
    item = next(row for row in store.list_items() if row["item_id"] == item_id)

    projects, original = _trusted_worker_batch_projects(item)
    projected = _overview_items([item])

    assert original == (item["requirements"][0]["requirement_id"], "original request")
    assert projects == {
        requirements["alpha"]["requirement_id"]: "alpha",
        requirements["beta"]["requirement_id"]: "beta",
    }
    by_project = {row["scope"]["project_id"]: row for row in projected
                  if row.get("scope", {}).get("kind") == "project"}
    assert set(by_project) == {"alpha", "beta"}
    assert [row["text"] for row in by_project["alpha"]["requirements"]] == ["alpha task"]
    assert [row["text"] for row in by_project["beta"]["requirements"]] == ["beta task"]
    independent = [row for row in projected if row.get("scope", {}).get("kind") == "independent"]
    assert len(independent) == 1
    assert [row["text"] for row in independent[0]["requirements"]] == ["unassigned task"]


def test_trusted_batch_attribution_rejects_tampered_project_identity_and_keeps_it_unassigned(tmp_path):
    _session, store, item_id, _requirements = _batch(tmp_path)
    item = next(row for row in store.list_items() if row["item_id"] == item_id)
    beta = next(row for row in item["requirements"] if row["text"] == "beta task")
    beta["source_ref"]["id"] = "gamma"

    projects, _original = _trusted_worker_batch_projects(item)

    assert beta["requirement_id"] not in projects

def test_named_current_batch_project_intent_is_exact_and_read_only_shape():
    import agent as agent_module

    recognize = getattr(agent_module, "_explicit_current_batch_project_overview", None)
    assert callable(recognize), "missing explicit current-batch project intent recognizer"
    assert recognize("项目 beta 现在情况如何") == "beta"
    assert recognize("项目B情况如何") is None
    assert recognize("查看项目 beta 的工作概况") == "beta"
    assert recognize("项目 beta 情况如何并修复代码") is None
    assert recognize("之前项目 beta 做了什么") is None
    assert recognize("他说‘项目 beta 现在情况如何’") is None


def test_nonbound_project_reader_is_gated_by_trusted_batch_not_session_registry():
    import work_records_overview as overview_module

    reader = getattr(overview_module, "render_current_batch_project_overview", None)
    assert callable(reader), "missing trusted current-batch project reader"

def test_nonbound_beta_query_renders_only_beta_from_same_trusted_batch(tmp_path):
    from work_records_overview import render_current_batch_project_overview

    session, _store, _item_id, _requirements = _batch(tmp_path)

    result = render_current_batch_project_overview(session, "beta")

    assert "beta task" in result
    assert "alpha task" not in result
    assert "unassigned task" not in result
    assert "original request" not in result
    assert "未读取其他会话" in result or "当前会话" in result


def test_nonbound_project_without_trusted_batch_identity_is_not_resolved_by_registry(tmp_path, monkeypatch):
    from work_records_overview import render_current_batch_project_overview
    import project_switch

    session, _store, _item_id, _requirements = _batch(tmp_path)
    monkeypatch.setattr(project_switch, "read_project_sessions", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("named batch scope must not consult project-session registry")))

    result = render_current_batch_project_overview(session, "gamma")

    assert "没有可核验" in result

def test_named_beta_query_routes_locally_without_model_and_keeps_other_scopes_out(tmp_path):
    import json
    from types import SimpleNamespace
    from agent import Agent

    session, store, _item_id, _requirements = _batch(tmp_path)
    (session.paths.data / "state.json").write_text(
        json.dumps({"project_sessions": {"alpha": session.conversation_id}}), encoding="utf-8")
    instance = object.__new__(Agent)
    instance.session = session
    instance.executor = SimpleNamespace(confirm_fn=None)
    instance.enable_work_record_recall = True
    instance.work_record_recall_controller = SimpleNamespace(_store=store)
    instance.enable_work_record_recording = False
    instance.work_record_recording_controller = None
    instance._run_turn_without_work_record_recording = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("explicit local overview must not fall through to a model"))

    result = instance.run_turn("项目 beta 现在情况如何")

    assert result.finish_reason == "current_batch_project_work_record_overview"
    assert "\n\n隐私提示：" in result.assistant_text
    assert "\\n\\n隐私提示：" not in result.assistant_text
    assert "beta task" in result.assistant_text
    assert "alpha task" not in result.assistant_text
    assert "unassigned task" not in result.assistant_text
