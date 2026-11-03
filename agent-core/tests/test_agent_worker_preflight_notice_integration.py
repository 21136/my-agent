"""Isolated scheduling coverage for immediate, project-bound worker notices."""
from __future__ import annotations

import concurrent.futures
import sys
import threading
import uuid

from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import project_worker_host as host_module
from project_mode import create_project
from project_switch import record_project_session
from server import WsBridge, _build_repl
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


def test_severe_prestart_notice_is_immediate_project_bound_and_isolated(monkeypatch):
    with temporary_agent_paths() as paths:
        project_ids = ("project-alpha", "project-beta", "project-gamma")
        for project_id in project_ids:
            create_project(paths, project_id, project_template="light")
            source = create_new(paths, conversation_id=f"20261031-{project_id[-5:]}")
            source.meta.project_id = project_id
            source.meta.project_root = f"workspace/{project_id}"
            source.save()
            record_project_session(paths, project_id, source.conversation_id)

        session = create_new(paths, conversation_id="20261031-preflight-notice")
        session.meta.turn_mode = "agent"
        session.meta.harness = "desktop"
        session.meta.active_shell = "project"
        session.meta.project_id = "project-alpha"
        session.meta.project_root = "workspace/project-alpha"
        session.meta.write_scope_root = ""
        session._ordinary_work_intent = "execute"
        session.messages.append({
            "role": "user",
            "content": "Update the requested README files in alpha, beta and gamma.",
        })
        session.save()

        bridge = WsBridge(emit=lambda _event: None, paths=paths, confirm_timeout=1)

        def confirm(prompt, _allow_all=False):
            if prompt.startswith("\u9879\u76ee worker \u8303\u56f4\u786e\u8ba4") and '"project_id":"project-beta"' in prompt:
                return "n"
            return "y"

        bridge.confirm_fn = confirm
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
        agent = repl.agent
        run_id, epoch = str(uuid.uuid4()), 1
        agent.bind_project_worker_live_context(
            run_id, epoch,
            lambda bound_session, actual_run, actual_epoch:
                bound_session is session and (actual_run, actual_epoch) == (run_id, epoch),
        )

        notice_seen = threading.Event()
        slow_worker_entered = threading.Event()
        release_slow_worker = threading.Event()
        turn_events = []

        def on_turn_event(event):
            turn_events.append(event)
            if (event.get("type") == "turn.notice"
                        and event.get("text", "").startswith("\u9879\u76ee\u300c")):
                notice_seen.set()
                # A broken desktop/event bridge must not abort sibling workers.
                raise RuntimeError("synthetic notice sink failure")

        agent.on_turn_event = on_turn_event

        real_executor = concurrent.futures.ThreadPoolExecutor

        class FailAlphaSubmitExecutor:
            def __init__(self, *args, **kwargs):
                self._delegate = real_executor(*args, **kwargs)

            def submit(self, fn, *args, **kwargs):
                request = args[1] if len(args) > 1 else None
                if getattr(request, "project_id", None) == "project-alpha":
                    raise RuntimeError("synthetic submit failure")
                return self._delegate.submit(fn, *args, **kwargs)

            def __enter__(self):
                self._delegate.__enter__()
                return self

            def __exit__(self, *args):
                return self._delegate.__exit__(*args)

        monkeypatch.setattr(host_module, "ThreadPoolExecutor", FailAlphaSubmitExecutor)

        def no_model_factory():
            slow_worker_entered.set()
            if not release_slow_worker.wait(timeout=3):
                raise TimeoutError("isolated slow-worker gate timed out")
            raise RuntimeError("synthetic: do not construct or call a model")

        monkeypatch.setattr(agent.project_worker_host, "_llm_client_factory", no_model_factory)
        actual_run_many = agent.project_worker_host.run_many
        run_many_calls = {}

        def count_run_many(bound_session, requests, **kwargs):
            project = requests[0].project_id
            run_many_calls[project] = run_many_calls.get(project, 0) + 1
            return actual_run_many(bound_session, requests, **kwargs)

        agent.project_worker_host.run_many = count_run_many

        def dispatch():
            return agent._dispatch_project_workers_tool({"workers": [
                {"project_id": project_id, "task": f"Update {project_id} README.",
                 "read_targets": ["README.md"], "write_targets": ["README.md"]}
                for project_id in project_ids
            ]})

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as caller:
            future = caller.submit(dispatch)
            try:
                notice_before_release = notice_seen.wait(timeout=1.0)
                sibling_started_before_release = slow_worker_entered.wait(timeout=1.0)
            finally:
                release_slow_worker.set()
            result = future.result(timeout=5)
            assert notice_before_release, (
                "severe prestart notice waited for the slow sibling; events="
                + repr([event.get("text", "").encode("unicode_escape") for event in turn_events])
            )
            assert sibling_started_before_release, "notification failure blocked the remaining project"

        assert result.ok is True
        assert run_many_calls == {project_id: 1 for project_id in project_ids}
        rows = result.data["workers"]
        by_project = {row["project_id"]: row for row in rows}
        assert by_project["project-alpha"]["error_code"] == "worker_dispatch_submit_failed"
        assert by_project["project-beta"]["error_code"] == "scope_denied"
        assert by_project["project-gamma"]["error_code"] == "llm_client_unavailable"
        notices = [event for event in turn_events if event.get("type") == "turn.notice"]
        assert len(notices) == 1
        assert notices[0]["text"] == "\u9879\u76ee\u300cproject-alpha\u300d\uff1a\u5f53\u524d worker \u4efb\u52a1\u672a\u80fd\u63d0\u4ea4\uff0c\u6682\u65f6\u65e0\u6cd5\u5f00\u59cb\u5904\u7406\u3002"
        assert all(value not in notices[0]["text"] for value in (
            str(paths.agent_root), "worker_dispatch_submit_failed", "worker_run_id",
            "reservation_id", "worker_run_id", "project-beta", "project-gamma",
        ))
        from work_records import WorkRecordStore
        records = WorkRecordStore(session).list_items()
        assert len(records) == 1
        assert len(records[0]["requirements"]) == 4
        candidates = agent._current_dialogue_project_card_candidates
        assert len(candidates) == 1
        assert set(candidates[0]["project_ids"]) == set(project_ids)



def test_project_binding_failure_with_unavailable_name_emits_safe_fallback(monkeypatch):
    with temporary_agent_paths() as paths:
        project_id = "project-binding"
        create_project(paths, project_id, project_template="light")
        session = create_new(paths, conversation_id="20261031-binding-notice")
        session.meta.turn_mode = "agent"
        session.meta.harness = "desktop"
        session.meta.active_shell = "project"
        session.meta.project_id = project_id
        session.meta.project_root = f"workspace/{project_id}"
        session.meta.write_scope_root = ""
        session._ordinary_work_intent = "execute"
        session.messages.append({"role": "user", "content": "Update the project README."})
        session.save()

        bridge = WsBridge(emit=lambda _event: None, paths=paths, confirm_timeout=1)
        bridge.confirm_fn = lambda *_args: "y"
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
        agent = repl.agent
        run_id, epoch = str(uuid.uuid4()), 1
        agent.bind_project_worker_live_context(
            run_id, epoch,
            lambda bound_session, actual_run, actual_epoch:
                bound_session is session and (actual_run, actual_epoch) == (run_id, epoch),
        )

        events = []
        agent.on_turn_event = events.append
        resolver = host_module._resolve_bound_project
        calls = 0

        def unavailable_after_preflight(bound_paths, bound_project_id):
            nonlocal calls
            calls += 1
            # Agent display-name snapshot fails; host batch preflight succeeds;
            # the worker host then owns and reports the binding failure.
            if calls in (1, 3):
                raise OSError("synthetic binding unavailable")
            return resolver(bound_paths, bound_project_id)

        monkeypatch.setattr(host_module, "_resolve_bound_project", unavailable_after_preflight)
        monkeypatch.setattr(
            agent.project_worker_host, "_llm_client_factory",
            lambda: pytest.fail("a host binding preflight failure must not construct a model client"),
        )

        result = agent._dispatch_project_workers_tool({"workers": [{
            "project_id": project_id,
            "task": "Update the project README.",
            "read_targets": ["README.md"],
            "write_targets": ["README.md"],
        }]})

        assert result.ok is True
        assert result.data["workers"][0]["error_code"] == "project_binding_unavailable"
        notices = [event for event in events if event.get("type") == "turn.notice"]
        assert len(notices) == 1
        assert notices[0]["text"] == (
            "\u67d0\u4e2a\u9879\u76ee\uff08\u540d\u79f0\u6682\u4e0d\u53ef\u786e\u8ba4\uff09\uff1a"
            "\u5f53\u524d\u9879\u76ee\u8fde\u63a5\u6682\u4e0d\u53ef\u7528\uff0cworker \u672a\u80fd\u542f\u52a8\u3002"
        )
        assert str(paths.agent_root) not in notices[0]["text"]
        assert project_id not in notices[0]["text"]
