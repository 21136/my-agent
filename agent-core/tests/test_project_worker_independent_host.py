from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_mode import create_project
from project_worker_execution import PROJECT_WORKER_COORDINATOR
from project_worker_host import ProjectWorkerHost
from project_worker_independent import run_independent_write_from_host
from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry


class IndependentWriteHostTests(unittest.TestCase):
    def _parent_executor(self, paths, *, confirm, on_event=None) -> ToolExecutor:
        project_id = "parent-project"
        create_project(paths, project_id, project_template="light")
        return ToolExecutor(
            registry=ToolRegistry.load(paths),
            session=ExecutorSession(
                session_dir=paths.data / "sessions" / "main-chat",
                allowed_evolved={"write_text"},
                active_shell="project",
                project_id=project_id,
                project_root=f"workspace/{project_id}",
                turn_mode="agent",
                ordinary_work_intent="execute",
            ),
            confirm_fn=confirm,
            on_event=on_event,
            project_worker_host_dispatch=lambda _arguments: None,
        )

    def test_host_entry_writes_one_unbound_target_with_two_real_confirms(self):
        prompts = []
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            executor = self._parent_executor(
                paths, confirm=lambda preview, _all: prompts.append(preview) or "y"
            )
            original_binding = (executor.session.project_id, executor.session.project_root)
            host = ProjectWorkerHost(lambda: None)
            cancelled = threading.Event()

            result = run_independent_write_from_host(
                executor,
                worker_host=host,
                target_path="workspace/independent-resume.txt",
                content="new resume body\n",
                host_binding_check=lambda: True,
                cancel_event=cancelled,
            )

            target = paths.workspace / "independent-resume.txt"
            self.assertTrue(result.ok, result.error)
            self.assertEqual(target.read_text(encoding="utf-8"), "new resume body\n")
            self.assertEqual(len(prompts), 2)
            self.assertIn("独立事项目标范围确认", prompts[0])
            self.assertIn("独立事项文件写入确认", prompts[1])
            self.assertIn("workspace/independent-resume.txt", prompts[0])
            self.assertEqual((executor.session.project_id, executor.session.project_root), original_binding)

    def test_host_entry_waits_for_project_worker_lease_on_the_same_file(self):
        scope_confirmed = threading.Event()
        write_confirmed = threading.Event()
        results = []
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            executor = self._parent_executor(
                paths,
                confirm=lambda preview, _all: (
                    scope_confirmed.set()
                    if "独立事项目标范围确认" in preview
                    else write_confirmed.set()
                ) or "y",
            )
            project_root = paths.workspace / "parent-project"
            target = project_root / "resume.txt"
            target.write_text("before\n", encoding="utf-8")
            worker_lease = PROJECT_WORKER_COORDINATOR.write_lease(
                "project-worker-chat", "parent-project", project_root,
                ("resume.txt",), kind="project",
            )
            worker_lease.__enter__()

            def invoke():
                results.append(run_independent_write_from_host(
                    executor,
                    worker_host=ProjectWorkerHost(lambda: None),
                    target_path="workspace/parent-project/resume.txt",
                    content="after\n",
                    host_binding_check=lambda: True,
                    cancel_event=threading.Event(),
                ))

            thread = threading.Thread(target=invoke)
            thread.start()
            try:
                self.assertTrue(scope_confirmed.wait(1))
                time.sleep(0.12)
                self.assertFalse(write_confirmed.is_set(), "independent host bypassed project-worker lease")
                self.assertEqual(target.read_text(encoding="utf-8"), "before\n")
            finally:
                worker_lease.__exit__(None, None, None)
            thread.join(3)

            self.assertFalse(thread.is_alive())
            self.assertTrue(write_confirmed.is_set())
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0].ok, results[0].error)
            self.assertEqual(target.read_text(encoding="utf-8"), "after\n")

    def test_host_entry_fails_closed_when_live_turn_is_stale(self):
        prompts = []
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            executor = self._parent_executor(
                paths, confirm=lambda preview, _all: prompts.append(preview) or "y"
            )
            result = run_independent_write_from_host(
                executor,
                worker_host=ProjectWorkerHost(lambda: None),
                target_path="workspace/stale-resume.txt",
                content="must not write",
                host_binding_check=lambda: False,
                cancel_event=threading.Event(),
            )
            self.assertFalse(result.ok)
            self.assertEqual(prompts, [])
            self.assertFalse((paths.workspace / "stale-resume.txt").exists())

    def test_host_turn_change_during_scope_confirm_rejects_before_write_confirm(self):
        prompts = []
        binding = {"current": True}
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            def confirm(preview, _all):
                prompts.append(preview)
                binding["current"] = False
                return "y"

            executor = self._parent_executor(paths, confirm=confirm)
            result = run_independent_write_from_host(
                executor,
                worker_host=ProjectWorkerHost(lambda: None),
                target_path="workspace/stale-after-confirm.txt",
                content="must not write",
                host_binding_check=lambda: binding["current"],
                cancel_event=threading.Event(),
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, "confirm_rejected")
            self.assertEqual(len(prompts), 1)
            self.assertIn("独立事项目标范围确认", prompts[0])
            self.assertFalse((paths.workspace / "stale-after-confirm.txt").exists())

    def test_host_scope_confirmation_rejection_prevents_write(self):
        prompts = []
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            executor = self._parent_executor(
                paths,
                confirm=lambda preview, _all: prompts.append(preview) or "n",
            )
            result = run_independent_write_from_host(
                executor,
                worker_host=ProjectWorkerHost(lambda: None),
                target_path="workspace/rejected-resume.txt",
                content="no write",
                host_binding_check=lambda: True,
                cancel_event=threading.Event(),
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, "confirm_rejected")
            self.assertEqual(len(prompts), 1)
            self.assertIn("独立事项目标范围确认", prompts[0])
            self.assertFalse((paths.workspace / "rejected-resume.txt").exists())

    def test_host_entry_preserves_the_parent_write_scope_fence(self):
        prompts = []
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            executor = self._parent_executor(
                paths, confirm=lambda preview, _all: prompts.append(preview) or "y"
            )
            executor.session.write_scope_root = "workspace/allowed"
            result = run_independent_write_from_host(
                executor,
                worker_host=ProjectWorkerHost(lambda: None),
                target_path="workspace/outside/resume.txt",
                content="must stay fenced",
                host_binding_check=lambda: True,
                cancel_event=threading.Event(),
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, "permission_denied")
            self.assertEqual(prompts, [])
            self.assertFalse((paths.workspace / "outside").exists())

    def test_host_entry_allows_only_one_independent_write_active_per_conversation(self):
        first_started = threading.Event()
        release_first = threading.Event()
        start_count = 0
        start_lock = threading.Lock()
        write_prompts = []
        results = []

        def on_event(event, _payload):
            nonlocal start_count
            if event == "tool.start":
                with start_lock:
                    start_count += 1
                    ordinal = start_count
                if ordinal == 1:
                    first_started.set()
                    release_first.wait(3)

        def confirm(preview, _all):
            if "独立事项文件写入确认" in preview:
                write_prompts.append(preview)
            return "y"

        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            executor = self._parent_executor(paths, confirm=confirm, on_event=on_event)
            host = ProjectWorkerHost(lambda: None)
            cancel = threading.Event()

            def invoke(target: str, body: str) -> None:
                results.append(run_independent_write_from_host(
                    executor,
                    worker_host=host,
                    target_path=target,
                    content=body,
                    host_binding_check=lambda: True,
                    cancel_event=cancel,
                ))

            first = threading.Thread(
                target=invoke, args=("workspace/one/resume.txt", "one\n"),
            )
            second = threading.Thread(
                target=invoke, args=("workspace/two/resume.txt", "two\n"),
            )
            first.start()
            self.assertTrue(first_started.wait(1))
            second.start()
            time.sleep(0.15)
            self.assertEqual(start_count, 1, "a second independent write entered concurrently")
            release_first.set()
            first.join(3)
            second.join(3)
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(len(results), 2)
            self.assertTrue(all(result.ok for result in results), [r.error for r in results])
            self.assertEqual(start_count, 2)
            self.assertEqual(len(write_prompts), 2)
            self.assertEqual((paths.workspace / "one/resume.txt").read_text(encoding="utf-8"), "one\n")
            self.assertEqual((paths.workspace / "two/resume.txt").read_text(encoding="utf-8"), "two\n")


if __name__ == "__main__":
    unittest.main()
