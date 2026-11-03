from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from test_work_records_confirm_bridge import _make_case


def _record_result(store, item, *, run_id: str, summary: str) -> None:
    requirement = item["requirements"][0]
    store.record_result(
        item["item_id"], requirement["requirement_id"], run_id=run_id,
        result_id=f"result-{run_id}", status="unknown", summary=summary,
        verification={},
    )


def _blank_material(data, item_id: str):
    item = next(row for row in data["items"] if row["item_id"] == item_id)
    item["title"] = " \t\n"
    for requirement in item["requirements"]:
        requirement["text"] = " \t\n"
    return None, True


def _capture_outbound_attempts(agent):
    previews = []
    agent.executor.confirm_fn = lambda preview, _allow: previews.append(preview) or "y"
    return previews


def test_truly_empty_semantic_material_requires_clarification_without_disclosure_or_model_call(tmp_path, monkeypatch):
    agent, ordinary, provider, *_ = _make_case(tmp_path, monkeypatch)
    store = agent.work_record_recall_controller._store
    item = store.list_items()[0]
    secret = "PRIVATE_EMPTY_MATERIAL_RESULT"
    _record_result(store, item, run_id="empty-material", summary=secret)
    store._mutate(lambda data: _blank_material(data, item["item_id"]))
    agent.work_record_recall_controller._semantic_excerpt_policy = None
    previews = _capture_outbound_attempts(agent)

    result = agent.run_turn("项目A之前做了什么？", spawn_explore=False)

    assert "异常旧工作记录" in result.assistant_text
    assert "回查暂不可用" in result.assistant_text
    assert "原记录未删除" in result.assistant_text
    assert "请先澄清具体事项" not in result.assistant_text
    assert secret not in result.assistant_text
    assert "synthetic detail" not in result.assistant_text
    assert "synthetic work-record title" not in result.assistant_text
    assert provider.calls == []
    assert ordinary.calls == []
    assert previews == []


def test_mixed_valid_and_blank_title_candidates_fail_closed_before_model_guess(tmp_path, monkeypatch):
    agent, ordinary, provider, *_ = _make_case(tmp_path, monkeypatch)
    store = agent.work_record_recall_controller._store
    valid = store.list_items()[0]
    _record_result(store, valid, run_id="valid-mixed", summary="PRIVATE_VALID_RESULT")

    malformed = store.create_item(title="legacy title before corruption", scope={"kind": "independent"})
    store.add_requirement(malformed["item_id"], "legacy requirement must stay private", event_id="legacy-req")
    malformed = store.get_item(malformed["item_id"])
    _record_result(store, malformed, run_id="invalid-mixed", summary="PRIVATE_INVALID_RESULT")
    store._mutate(lambda data: _blank_material(data, malformed["item_id"]))
    agent.work_record_recall_controller._semantic_excerpt_policy = None
    previews = _capture_outbound_attempts(agent)

    result = agent.run_turn("项目A之前做了什么？", spawn_explore=False)

    assert "异常旧工作记录" in result.assistant_text
    assert "回查暂不可用" in result.assistant_text
    assert "原记录未删除" in result.assistant_text
    assert "请先澄清具体事项" not in result.assistant_text
    for private in (
        "PRIVATE_VALID_RESULT", "PRIVATE_INVALID_RESULT",
        "synthetic detail", "synthetic work-record title",
        "legacy requirement must stay private", "legacy title before corruption",
    ):
        assert private not in result.assistant_text
    assert provider.calls == []
    assert ordinary.calls == []
    assert previews == []
