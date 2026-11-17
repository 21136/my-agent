"""Mocked worker loop: granted catalog -> load_worker_workflow_skill -> continue."""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_worker_model_loop import run_project_worker_model_loop
from skill_runtime import (
    build_worker_skill_prompt_suffix,
    clear_skill_index_cache_for_tests,
    clear_worker_skill_states_for_tests,
    get_worker_skill_state,
    invalidate_skill_index_cache,
    worker_tool_load_workflow_skill,
)
from tests.isolation_helpers import temporary_agent_paths
from tests.test_project_worker_model_loop import (
    _ApproveExternalRequests,
    _harness,
    _tool_call,
)


def _skill_md(name: str, body: str) -> str:
    return f"""---
name: {name}
description: Worker skill {name}.
triggers: [{name}-trigger]
allowed_tools: []
risk_level: low
verification: inspect
version: 1.0.0
audiences: [worker]
---

{body}
"""


def _install_skills(paths, *, include_secret: bool = True) -> None:
    skills_root = paths.evolve / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    (skills_root / "wf-a").mkdir(parents=True, exist_ok=True)
    (skills_root / "wf-a" / "SKILL.md").write_text(
        _skill_md("wf-a", "WORKFLOW_BODY_A"), encoding="utf-8",
    )
    if include_secret:
        (skills_root / "secret-b").mkdir(parents=True, exist_ok=True)
        (skills_root / "secret-b" / "SKILL.md").write_text(
            _skill_md("secret-b", "SECRET_BODY_B"), encoding="utf-8",
        )


def _worker_spec(paths, monkeypatch, responses, *, authorized: tuple[str, ...]):
    client, spec, cancel, requests, _urls = _harness(
        paths,
        monkeypatch,
        responses,
        confirm=lambda _preview, _all: "y",
    )
    (paths.workspace / "worker-a" / "README.md").write_text("src\n", encoding="utf-8")
    _install_skills(paths)
    spec = replace(
        spec,
        worker_task_id="task-stable-hash",
        authorized_skill_ids=authorized,
    )
    return client, spec, cancel, requests


@pytest.fixture(autouse=True)
def _clear_skill_caches() -> None:
    clear_skill_index_cache_for_tests()
    clear_worker_skill_states_for_tests()
    yield
    clear_skill_index_cache_for_tests()
    clear_worker_skill_states_for_tests()


def test_worker_catalog_load_and_finish(monkeypatch) -> None:
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        model_responses = [
            (
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [_tool_call("load_worker_workflow_skill", {"skill_id": "wf-a"})],
                },
                "tool_calls",
            ),
            (
                {
                    "role": "assistant",
                    "content": "Workflow loaded; continuing worker task.",
                    "tool_calls": [],
                },
                "stop",
            ),
        ]
        client, spec, _cancel, requests = _worker_spec(
            paths, monkeypatch, model_responses, authorized=("wf-a",),
        )
        result = run_project_worker_model_loop(
            spec, "Apply wf-a workflow to result.txt", client=client, max_requests=6,
        )
        assert result.status == "model_turn_finished", (
            result.status, result.error_code, result.error_message, result.error_type,
        )
        system = requests[0]["messages"][0]["content"]
        assert "WORKFLOW_BODY_A" not in system
        assert "wf-a" in system
        assert "load_worker_workflow_skill" in json.dumps(requests[0]["tools"])
        tool_payload = json.loads(requests[1]["messages"][-1]["content"])
        assert tool_payload.get("ok") is True
        assert tool_payload.get("skill_id") == "wf-a"
        assert "WORKFLOW_BODY_A" in tool_payload.get("skill_context", "")
        assert result.requests == 2


def test_worker_rejects_unauthorized_skill_id(monkeypatch) -> None:
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        model_responses = [
            (
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [_tool_call("load_worker_workflow_skill", {"skill_id": "secret-b"})],
                },
                "tool_calls",
            ),
            ({"role": "assistant", "content": "Stopped.", "tool_calls": []}, "stop"),
        ]
        client, spec, _cancel, requests = _worker_spec(
            paths, monkeypatch, model_responses, authorized=("wf-a",),
        )
        result = run_project_worker_model_loop(spec, "Try secret skill", client=client, max_requests=4)
        assert result.status == "model_turn_finished"
        payload = json.loads(requests[1]["messages"][-1]["content"])
        assert payload.get("ok") is False
        assert payload.get("error") == "skill_not_authorized"
        assert "SECRET_BODY_B" not in requests[0]["messages"][0]["content"]


def test_worker_rejects_path_traversal_skill_ref(monkeypatch) -> None:
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        model_responses = [
            (
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [_tool_call("load_worker_workflow_skill", {"skill_id": "../wf-a"})],
                },
                "tool_calls",
            ),
            ({"role": "assistant", "content": "Nope.", "tool_calls": []}, "stop"),
        ]
        client, spec, _cancel, requests = _worker_spec(
            paths, monkeypatch, model_responses, authorized=("wf-a",),
        )
        run_project_worker_model_loop(spec, "Traversal", client=client, max_requests=3)
        payload = json.loads(requests[1]["messages"][-1]["content"])
        assert payload.get("ok") is False
        assert payload.get("error") == "path_denied"


def test_worker_skill_load_unit_authorized_and_isolation() -> None:
    with temporary_agent_paths() as paths:
        skills_root = paths.evolve / "skills"
        _install_skills(paths)
        suffix = build_worker_skill_prompt_suffix(
            skills_root, "wf-a-trigger apply",
            conversation_id="conv-1", project_id="proj-1", worker_task_id="task-1",
            authorized_skill_ids=("wf-a",),
            skills_root_key=str(skills_root.resolve()),
        )
        assert "wf-a" in suffix
        assert "SECRET" not in suffix
        ok = worker_tool_load_workflow_skill(
            skills_root=skills_root,
            conversation_id="conv-1",
            worker_task_id="task-1",
            project_id="proj-1",
            authorized_skill_ids=("wf-a",),
            skill_ref="wf-a",
            task_text="apply",
            skills_root_key=str(skills_root.resolve()),
        )
        assert ok["ok"] is True
        assert "WORKFLOW_BODY_A" in ok["skill_context"]
        denied = worker_tool_load_workflow_skill(
            skills_root=skills_root,
            conversation_id="conv-1",
            worker_task_id="task-1",
            project_id="proj-1",
            authorized_skill_ids=("wf-a",),
            skill_ref="secret-b",
            skills_root_key=str(skills_root.resolve()),
        )
        assert denied["ok"] is False
        assert denied["error"] == "skill_not_authorized"
        state = get_worker_skill_state(
            "conv-1", "task-1", "proj-1", str(skills_root.resolve()), ("wf-a",),
        )
        assert state is not None
        assert "wf-a" in state.loaded
        build_worker_skill_prompt_suffix(
            skills_root, "unrelated step",
            conversation_id="conv-1", project_id="proj-2", worker_task_id="task-1",
            authorized_skill_ids=("wf-a",),
            skills_root_key=str(skills_root.resolve()),
        )
        old = get_worker_skill_state(
            "conv-1", "task-1", "proj-1", str(skills_root.resolve()), ("wf-a",),
        )
        other = get_worker_skill_state(
            "conv-1", "task-1", "proj-2", str(skills_root.resolve()), ("wf-a",),
        )
        assert old is not None and "wf-a" in old.loaded
        assert other is not None and not other.loaded


def test_normalize_host_skill_ids_ignores_unknown_and_model_forgery() -> None:
    with temporary_agent_paths() as paths:
        from skill_runtime import normalize_host_authorized_skill_ids

        skills_root = paths.evolve / "skills"
        (skills_root / "wf-a").mkdir(parents=True)
        (skills_root / "wf-a" / "SKILL.md").write_text(_skill_md("wf-a", "x"), encoding="utf-8")
        result = normalize_host_authorized_skill_ids(
            skills_root,
            ["wf-a", "fake-id", "../../../etc/passwd", "wf-a"],
        )
        assert result == ("wf-a",)


def test_worker_skill_revoke_on_grant_shrink() -> None:
    with temporary_agent_paths() as paths:
        skills_root = paths.evolve / "skills"
        _install_skills(paths)
        worker_tool_load_workflow_skill(
            skills_root=skills_root,
            conversation_id="c", worker_task_id="t", project_id="p",
            authorized_skill_ids=("wf-a",), skill_ref="wf-a",
            skills_root_key=str(skills_root.resolve()),
        )
        suffix = build_worker_skill_prompt_suffix(
            skills_root, "continue",
            conversation_id="c", project_id="p", worker_task_id="t",
            authorized_skill_ids=(),
            skills_root_key=str(skills_root.resolve()),
        )
        assert "WORKFLOW_BODY_A" not in suffix
        state = get_worker_skill_state("c", "t", "p", str(skills_root.resolve()), ())
        assert state is None or not state.loaded


def test_worker_skill_revoke_on_file_change() -> None:
    with temporary_agent_paths() as paths:
        skills_root = paths.evolve / "skills"
        (skills_root / "wf-a").mkdir(parents=True)
        path = skills_root / "wf-a" / "SKILL.md"
        path.write_text(_skill_md("wf-a", "OLD_BODY"), encoding="utf-8")
        worker_tool_load_workflow_skill(
            skills_root=skills_root,
            conversation_id="c", worker_task_id="t", project_id="p",
            authorized_skill_ids=("wf-a",), skill_ref="wf-a",
            skills_root_key=str(skills_root.resolve()),
        )
        path.write_text(_skill_md("wf-a", "NEW_BODY"), encoding="utf-8")
        invalidate_skill_index_cache(skills_root)
        suffix = build_worker_skill_prompt_suffix(
            skills_root, "continue",
            conversation_id="c", project_id="p", worker_task_id="t",
            authorized_skill_ids=("wf-a",),
            skills_root_key=str(skills_root.resolve()),
        )
        assert "OLD_BODY" not in suffix