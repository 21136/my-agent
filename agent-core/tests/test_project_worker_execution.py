"""Real project-worker execution must stay inside the host-bound project scope."""
from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_mode import create_project
from project_worker_execution import ProjectWorkerCoordinator, ProjectWorkerSpec, run_project_worker
from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry


class ProjectWorkerExecutionTests(unittest.TestCase):
    def _executor(self, paths, project_id: str, confirm=None) -> ToolExecutor:
        create_project(paths, project_id, project_template="light")
        return ToolExecutor(
            registry=ToolRegistry.load(paths),
            session=ExecutorSession(
                allowed_evolved={"write_text", "patch_file"},
                project_root=f"workspace/{project_id}",
                project_id=project_id,
                active_shell="project",
                project_plan_status="confirmed",
                project_workflow_stage="implementation",
                project_entry="direct",
            ),
            confirm_fn=confirm or (lambda _tool, _all: "n"),
        )

    def test_worker_writes_through_real_executor_inside_bound_root(self):
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            executor = self._executor(paths, "worker-a")
            root = paths.workspace / "worker-a"
            spec = ProjectWorkerSpec(
                conversation_id="chat-1", project_id="worker-a", project_root=root,
                write_targets=("result.txt",), executor=executor,
                host_binding_check=lambda: True,
            )
            with self.assertLogs("my_agent.sidecar", level="INFO") as captured:
                result = run_project_worker(
                    spec, lambda worker: worker.run(
                        "write_text", {"path": "result.txt", "content": "real write\n"}
                    ),
                )
            self.assertTrue(result.ok, result.error)
            timing = [row for row in captured.output if "event=project_worker_timing" in row]
            self.assertTrue(any("phase=lease_wait" in row and "duration_ms=" in row for row in timing))
            self.assertTrue(all("result.txt" not in row and "chat-1" not in row for row in timing))
            self.assertEqual((root / "result.txt").read_text(encoding="utf-8"), "real write\n")

    def test_worker_cannot_write_undeclared_or_cross_root_target(self):
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            executor = self._executor(paths, "worker-a")
            root = paths.workspace / "worker-a"
            outside = paths.workspace / "worker-b" / "README.md"
            spec = ProjectWorkerSpec(
                conversation_id="chat-1", project_id="worker-a", project_root=root,
                write_targets=("result.txt",), executor=executor,
                host_binding_check=lambda: True,
            )
            with self.assertRaises(PermissionError):
                ProjectWorkerCoordinator().run_project_worker(
                    spec, lambda worker: worker.run(
                        "write_text", {"path": "../worker-b/README.md", "content": "escape"}
                    ),
                )
            self.assertFalse(outside.exists())

    def test_sensitive_write_still_requires_existing_host_confirmation(self):
        prompts: list[str] = []
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            executor = self._executor(paths, "worker-a", confirm=lambda tool, _all: prompts.append(tool) or "n")
            root = paths.workspace / "worker-a"
            original = (root / "PROJECT.md").read_text(encoding="utf-8")
            spec = ProjectWorkerSpec(
                conversation_id="chat-1", project_id="worker-a", project_root=root,
                write_targets=("PROJECT.md",), executor=executor,
                host_binding_check=lambda: True,
            )
            result = run_project_worker(
                spec, lambda worker: worker.run("write_text", {"path": "PROJECT.md", "content": "denied"})
            )
            self.assertFalse(result.ok)
            self.assertEqual(len(prompts), 1)
            self.assertIn("PROJECT.md", prompts[0])
            self.assertIn("confirm:plan_domain", prompts[0])
            self.assertEqual((root / "PROJECT.md").read_text(encoding="utf-8"), original)
    def test_changed_host_binding_fails_closed_before_worker_starts(self):
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            executor = self._executor(paths, "worker-a")
            called = []
            spec = ProjectWorkerSpec(
                conversation_id="chat-1", project_id="worker-a",
                project_root=paths.workspace / "worker-a", write_targets=("result.txt",),
                executor=executor, host_binding_check=lambda: False,
            )
            with self.assertRaises(PermissionError):
                ProjectWorkerCoordinator().run_project_worker(spec, lambda _worker: called.append(True))
            self.assertEqual(called, [])

    def test_same_project_workers_are_serialized_and_two_projects_can_overlap(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp)
            for name in ("a", "b"):
                (temp_root / name).mkdir()
            coordinator = ProjectWorkerCoordinator()
            active: set[str] = set()
            peak = 0
            guard = threading.Lock()
            failures: list[BaseException] = []

            def run(project_id: str):
                nonlocal peak
                try:
                    with coordinator.write_lease("chat-1", project_id, temp_root / project_id, ("same.txt",)):
                        with guard:
                            if project_id in active:
                                raise AssertionError("same project overlapped")
                            active.add(project_id)
                            peak = max(peak, len(active))
                        time.sleep(0.04)
                        with guard:
                            active.remove(project_id)
                except BaseException as exc:
                    failures.append(exc)

            threads = [threading.Thread(target=run, args=("a",)),
                       threading.Thread(target=run, args=("a",)),
                       threading.Thread(target=run, args=("b",))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)
            self.assertFalse(any(thread.is_alive() for thread in threads), "lease did not release")
            self.assertEqual(failures, [])
            self.assertEqual(peak, 2)

    def test_same_existing_file_identity_is_never_leased_twice(self):
        with temporary_agent_paths() as paths:
            root = paths.workspace / "worker-a"
            root.mkdir()
            original = root / "one.txt"
            original.write_text("x", encoding="utf-8")
            alias = root / "two.txt"
            try:
                alias.hardlink_to(original)
            except (OSError, NotImplementedError):
                self.skipTest("filesystem does not support hard links")
            coordinator = ProjectWorkerCoordinator()
            entered = threading.Event()
            release = threading.Event()
            second_entered = threading.Event()

            def first():
                with coordinator.write_lease("chat-1", "a", root, ("one.txt",)):
                    entered.set()
                    release.wait(2)

            def second():
                entered.wait(2)
                with coordinator.write_lease("chat-1", "b", root, ("two.txt",)):
                    second_entered.set()

            a = threading.Thread(target=first)
            b = threading.Thread(target=second)
            a.start(); b.start()
            self.assertTrue(entered.wait(1))
            time.sleep(0.05)
            self.assertFalse(second_entered.is_set())
            release.set(); a.join(2); b.join(2)
            self.assertTrue(second_entered.is_set())

    def test_three_distinct_projects_never_exceed_two_active_leases(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("a", "b", "c"):
                (root / name).mkdir()
            coordinator = ProjectWorkerCoordinator()
            gate = threading.Barrier(3, timeout=0.2)
            guard = threading.Lock()
            active: set[str] = set()
            entered: set[str] = set()
            peak = 0
            failures: list[BaseException] = []

            def run(project_id: str):
                nonlocal peak
                try:
                    with coordinator.write_lease("chat-1", project_id, root / project_id, ("file.txt",)):
                        with guard:
                            active.add(project_id)
                            entered.add(project_id)
                            peak = max(peak, len(active))
                        try:
                            gate.wait()
                        except threading.BrokenBarrierError:
                            pass
                        time.sleep(0.02)
                        with guard:
                            active.remove(project_id)
                except BaseException as exc:
                    failures.append(exc)

            threads = [threading.Thread(target=run, args=(name,)) for name in ("a", "b", "c")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)
            self.assertFalse(any(thread.is_alive() for thread in threads), "lease did not release")
            self.assertEqual(failures, [])
            self.assertEqual(entered, {"a", "b", "c"})
            self.assertLessEqual(peak, 2)

    def test_independent_lease_has_single_slot_but_does_not_use_project_slot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("a", "independent", "independent-2"):
                (root / name).mkdir()
            coordinator = ProjectWorkerCoordinator()
            independent_entered = threading.Event()
            release = threading.Event()
            second_independent_entered = threading.Event()
            project_entered = threading.Event()

            def independent(project_id: str, entered: threading.Event):
                with coordinator.write_lease("chat-1", project_id, root / project_id,
                                             ("file.txt",), kind="independent"):
                    entered.set()
                    release.wait(2)

            def project():
                with coordinator.write_lease("chat-1", "a", root / "a", ("file.txt",)):
                    project_entered.set()

            one = threading.Thread(target=independent, args=("independent", independent_entered))
            two = threading.Thread(target=independent, args=("independent-2", second_independent_entered))
            project_thread = threading.Thread(target=project)
            one.start()
            self.assertTrue(independent_entered.wait(1))
            two.start()
            project_thread.start()
            self.assertTrue(project_entered.wait(1), "independent item consumed a project slot")
            self.assertFalse(second_independent_entered.is_set(), "two independent writes overlapped")
            release.set()
            for thread in (one, two, project_thread):
                thread.join(timeout=2)
            self.assertTrue(second_independent_entered.is_set())


    def test_same_project_is_exclusive_across_conversations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            coordinator = ProjectWorkerCoordinator()
            first_entered = threading.Event()
            release_first = threading.Event()
            second_entered = threading.Event()

            def first():
                with coordinator.write_lease("chat-1", "project", root, ("one.txt",)):
                    first_entered.set()
                    release_first.wait(2)

            def second():
                first_entered.wait(2)
                with coordinator.write_lease("chat-2", "renamed-project", root, ("two.txt",)):
                    second_entered.set()

            a = threading.Thread(target=first)
            b = threading.Thread(target=second)
            a.start(); b.start()
            self.assertTrue(first_entered.wait(1))
            time.sleep(0.05)
            self.assertFalse(second_entered.is_set(), "same physical project overlapped across conversations")
            release_first.set()
            a.join(2); b.join(2)
            self.assertTrue(second_entered.is_set())

    def test_same_hardlinked_file_is_exclusive_across_conversations_and_kinds(self):
        with temporary_agent_paths() as paths:
            first_root = paths.workspace / "worker-a"
            second_root = paths.workspace / "worker-b"
            first_root.mkdir(); second_root.mkdir()
            first_file = first_root / "one.txt"
            first_file.write_text("x", encoding="utf-8")
            second_file = second_root / "two.txt"
            try:
                second_file.hardlink_to(first_file)
            except (OSError, NotImplementedError):
                self.skipTest("filesystem does not support hard links")
            coordinator = ProjectWorkerCoordinator()
            first_entered = threading.Event()
            release_first = threading.Event()
            second_entered = threading.Event()

            def first():
                with coordinator.write_lease("chat-1", "worker-a", first_root, ("one.txt",)):
                    first_entered.set()
                    release_first.wait(2)

            def second():
                first_entered.wait(2)
                with coordinator.write_lease("chat-2", "independent-item", second_root,
                                             ("two.txt",), kind="independent"):
                    second_entered.set()

            a = threading.Thread(target=first)
            b = threading.Thread(target=second)
            a.start(); b.start()
            self.assertTrue(first_entered.wait(1))
            time.sleep(0.05)
            self.assertFalse(second_entered.is_set(), "same file alias overlapped across conversations")
            release_first.set()
            a.join(2); b.join(2)
            self.assertTrue(second_entered.is_set())

    def test_declared_project_root_junction_is_rejected_before_worker_starts(self):
        import os
        import subprocess

        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            create_project(paths, "worker-b", project_template="light")
            root_a = paths.workspace / "worker-a"
            root_b = paths.workspace / "worker-b"
            try:
                os.symlink(root_b, root_a, target_is_directory=True)
            except OSError:
                completed = subprocess.run(
                    ["cmd.exe", "/c", "mklink", "/J", str(root_a), str(root_b)],
                    capture_output=True, text=True, check=False,
                )
                if completed.returncode != 0:
                    self.skipTest("neither directory symlinks nor junction creation is available")
            self.assertTrue(root_a.is_symlink() or root_a.is_junction())
            executor = ToolExecutor(
                registry=ToolRegistry.load(paths),
                session=ExecutorSession(
                    allowed_evolved={"write_text", "patch_file"},
                    project_root="workspace/worker-a", project_id="worker-a",
                    active_shell="project", project_plan_status="confirmed",
                    project_workflow_stage="implementation", project_entry="direct",
                ),
                confirm_fn=lambda _preview, _all: "n",
            )
            called = []
            spec = ProjectWorkerSpec(
                conversation_id="chat-1", project_id="worker-a", project_root=root_a,
                write_targets=("PROJECT.md",), executor=executor,
                host_binding_check=lambda: True,
            )
            with self.assertRaises(PermissionError):
                ProjectWorkerCoordinator().run_project_worker(spec, lambda _worker: called.append(True))
            self.assertEqual(called, [])

    def test_conflicting_cross_conversation_waiters_are_admitted_fifo_without_deadlock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            coordinator = ProjectWorkerCoordinator()
            holder = coordinator.write_lease("chat-holder", "project", root, ("holder.txt",))
            holder.__enter__()
            b_queued = threading.Event()
            c_queued = threading.Event()
            b_entered = threading.Event()
            c_entered = threading.Event()
            release_b = threading.Event()

            def queued(conversation_id: str, started: threading.Event, entered: threading.Event):
                started.set()
                with coordinator.write_lease(conversation_id, "same-project", root, (f"{conversation_id}.txt",)):
                    entered.set()
                    if conversation_id == "chat-b":
                        release_b.wait(2)

            b = threading.Thread(target=queued, args=("chat-b", b_queued, b_entered))
            c = threading.Thread(target=queued, args=("chat-c", c_queued, c_entered))
            b.start()
            self.assertTrue(b_queued.wait(1))
            with coordinator._condition:
                self.assertTrue(coordinator._condition.wait_for(lambda: len(coordinator._waiting) == 1, timeout=1))
            c.start()
            self.assertTrue(c_queued.wait(1))
            with coordinator._condition:
                self.assertTrue(coordinator._condition.wait_for(lambda: len(coordinator._waiting) == 2, timeout=1))
            holder.__exit__(None, None, None)
            self.assertTrue(b_entered.wait(1), "oldest conflicting waiter was not admitted")
            self.assertFalse(c_entered.wait(0.05), "later conflicting waiter overtook the oldest")
            release_b.set()
            self.assertTrue(c_entered.wait(1))
            b.join(2); c.join(2)
            self.assertFalse(b.is_alive() or c.is_alive(), "queued lease deadlocked")


if __name__ == "__main__":
    unittest.main()







class ProjectWorkerReadRetryTests(unittest.TestCase):
    def _read(self, paths, reader, cancel_event=None):
        import project_worker_execution as execution
        from project_worker_read import ProjectReadTransientError
        root = Path(tempfile.mkdtemp()) / "worker-a"
        root.mkdir()
        (root / "README.md").write_text("source", encoding="utf-8")
        executor = ProjectWorkerExecutionTests()._executor(paths, "worker-a")
        cancel_event = cancel_event or threading.Event()
        spec = ProjectWorkerSpec(
            conversation_id="retry-chat", project_id="worker-a", project_root=root,
            write_targets=(), read_targets=("README.md",), executor=executor,
            host_binding_check=lambda: True, cancel_event=cancel_event,
        )
        original_reader = execution.ProjectTextReader
        original_validate = execution.ProjectWorkerCoordinator._validate_spec
        original_host = execution.ProjectWorkerCoordinator._host_is_current
        execution.ProjectTextReader = reader
        execution.ProjectWorkerCoordinator._validate_spec = staticmethod(lambda _spec: (root, "worker-a"))
        execution.ProjectWorkerCoordinator._host_is_current = staticmethod(lambda _spec: True)
        scoped = execution.ScopedProjectTools(spec, root, "worker-a", {})
        try:
            try:
                result = scoped.read_snapshot("README.md")
            except Exception as exc:
                result = exc
        finally:
            execution.ProjectTextReader = original_reader
            execution.ProjectWorkerCoordinator._validate_spec = staticmethod(original_validate)
            execution.ProjectWorkerCoordinator._host_is_current = staticmethod(original_host)
        return result, scoped, ProjectReadTransientError

    def test_read_snapshot_retries_only_typed_transient_and_records_content_free_attempts(self):
        from project_worker_read import ProjectReadTransientError
        with temporary_agent_paths() as paths:
            calls = []
            class Reader:
                def __init__(self, _root): pass
                def read_texts(self, names):
                    calls.append(tuple(names))
                    if len(calls) == 1:
                        raise ProjectReadTransientError("sharing_violation")
                    return {"README.md": "private body"}
            result, scoped, _ = self._read(paths, Reader)
            self.assertEqual(result[0], "private body")
            self.assertEqual(len(calls), 2)
            self.assertEqual(scoped.read_attempts[0].result, "retrying")
            self.assertEqual(scoped.read_attempts[0].error_code, "sharing_violation")
            self.assertEqual(scoped.read_attempts[1].result, "success")
            self.assertNotIn("private body", repr(scoped.read_attempts))

    def test_read_snapshot_does_not_retry_untyped_or_nontransient_failures(self):
        with temporary_agent_paths() as paths:
            calls = []
            class Reader:
                def __init__(self, _root): pass
                def read_texts(self, names):
                    calls.append(tuple(names))
                    raise OSError("permission denied")
            result, _scoped, _ = self._read(paths, Reader)
            self.assertIsInstance(result, PermissionError)
            self.assertEqual(len(calls), 1)

    def test_read_snapshot_stops_at_six_attempts_and_cancel_before_retry(self):
        from project_worker_execution import ProjectWorkerCancelled
        from project_worker_read import ProjectReadTransientError
        with temporary_agent_paths() as paths:
            calls = []
            class AlwaysTransient:
                def __init__(self, _root): pass
                def read_texts(self, names):
                    calls.append(tuple(names))
                    raise ProjectReadTransientError("sharing_violation")
            result, scoped, _ = self._read(paths, AlwaysTransient)
            self.assertIsInstance(result, PermissionError)
            self.assertEqual(getattr(result, "error_code", ""), "read_retry_exhausted")
            self.assertEqual(len(calls), 6)
            self.assertEqual([entry.attempt for entry in scoped.read_attempts], [1, 2, 3, 4, 5, 6])
            self.assertEqual({entry.read_id for entry in scoped.read_attempts}, {1})
            self.assertEqual(scoped.read_attempts[-1].result, "failed")

            cancel = threading.Event()
            calls.clear()
            class CancelAfterTransient:
                def __init__(self, _root): pass
                def read_texts(self, names):
                    calls.append(tuple(names))
                    cancel.set()
                    raise ProjectReadTransientError("sharing_violation")
            with temporary_agent_paths() as cancel_paths:
                cancelled, cancelled_scope, _ = self._read(cancel_paths, CancelAfterTransient, cancel)
            self.assertIsInstance(cancelled, ProjectWorkerCancelled)
            self.assertEqual(len(calls), 1)
            self.assertTrue(any(entry.result == "cancelled" for entry in cancelled_scope.read_attempts))

    def test_retry_uses_short_cancellable_backoff_before_next_read(self):
        from project_worker_execution import ProjectWorkerCancelled
        from project_worker_read import ProjectReadTransientError
        with temporary_agent_paths() as paths:
            calls = []
            class CancelDuringWait(threading.Event):
                wait_count = 0
                def wait(self, timeout=None):
                    self.wait_count += 1
                    self.timeout = timeout
                    self.set()
                    return True
            cancel = CancelDuringWait()
            class Reader:
                def __init__(self, _root): pass
                def read_texts(self, names):
                    calls.append(tuple(names))
                    raise ProjectReadTransientError("sharing_violation")
            outcome, scoped, _ = self._read(paths, Reader, cancel)
            self.assertIsInstance(outcome, ProjectWorkerCancelled)
            self.assertEqual(len(calls), 1)
            self.assertEqual(cancel.wait_count, 1)
            self.assertGreater(cancel.timeout, 0)
            self.assertLessEqual(cancel.timeout, 0.1)
            self.assertTrue(any(entry.result == "cancelled" for entry in scoped.read_attempts))
