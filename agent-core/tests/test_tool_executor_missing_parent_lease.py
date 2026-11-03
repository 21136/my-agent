from __future__ import annotations

import os
import subprocess
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
from project_worker_execution import PROJECT_WORKER_COORDINATOR
from project_worker_independent import IndependentWriteSpec, run_independent_write
from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry


_CROSS_PROCESS_CHILD = r"""
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from project_worker_execution import ProjectWorkerCoordinator
root, attempting, prepared, entered, release = map(Path, sys.argv[2:7])
coordinator = ProjectWorkerCoordinator()
attempting.write_text("attempting", encoding="ascii")
coordinator._prepare_lease("child", "independent", root, ("resume.txt",), "independent")
prepared.write_text("prepared", encoding="ascii")
with coordinator.write_lease("child", "independent", root, ("resume.txt",), kind="independent"):
    entered.write_text("entered", encoding="ascii")
    while not release.exists():
        time.sleep(0.01)
"""


def _wait_for(path: Path, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.01)
    return path.exists()


class MissingParentWriteLeaseTests(unittest.TestCase):
    def _executor(self, paths, name, *, confirm, on_event=None):
        return ToolExecutor(
            registry=ToolRegistry.load(paths),
            session=ExecutorSession(
                session_dir=paths.data / "sessions" / name,
                allowed_evolved={"write_text", "patch_file"},
            ),
            confirm_fn=confirm,
            on_event=on_event,
            project_worker_host_dispatch=lambda _arguments: None,
        )

    def test_ancestor_lease_conflicts_after_missing_directories_are_created(self):
        with temporary_agent_paths() as paths:
            anchor = paths.workspace
            relative = "future/deep/resume.txt"
            coordinator = PROJECT_WORKER_COORDINATOR
            entered = threading.Event()
            materialized = threading.Event()
            release = threading.Event()
            second_entered = threading.Event()
            errors = []

            def hold_ancestor_lease():
                try:
                    with coordinator.write_lease(
                        "ancestor-holder", "independent", anchor, (relative,),
                        kind="independent", allow_missing_parents=True,
                    ):
                        entered.set()
                        (anchor / "future" / "deep").mkdir(parents=True)
                        (anchor / "future" / "deep" / "resume.txt").write_text("one")
                        materialized.set()
                        release.wait(2)
                except BaseException as exc:
                    errors.append(("ancestor", exc))

            def acquire_after_materialization():
                try:
                    with coordinator.write_lease(
                        "materialized-target", "independent", anchor / "future" / "deep",
                        ("resume.txt",), kind="independent",
                    ):
                        second_entered.set()
                except BaseException as exc:
                    errors.append(("materialized", exc))

            first = threading.Thread(target=hold_ancestor_lease)
            first.start()
            self.assertTrue(entered.wait(1))
            self.assertTrue(materialized.wait(1))
            second = threading.Thread(target=acquire_after_materialization)
            second.start()
            time.sleep(0.12)
            self.assertFalse(second_entered.is_set(), "created target did not match its ancestor lease")
            release.set()
            first.join(2)
            second.join(2)
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(errors, [])
            self.assertTrue(second_entered.is_set())

    def test_materialized_path_remains_cross_process_exclusive(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            anchor = base / "trusted"
            anchor.mkdir()
            target = anchor / "nested" / "deep" / "resume.txt"
            attempting = base / "attempting"
            prepared = base / "prepared"
            entered = base / "entered"
            release = base / "release"
            holder = PROJECT_WORKER_COORDINATOR.write_lease(
                "parent", "independent", anchor, ("nested/deep/resume.txt",),
                kind="independent", allow_missing_parents=True,
            )
            holder.__enter__()
            child_process = None
            holder_open = True
            try:
                target.parent.mkdir(parents=True)
                target.write_text("parent", encoding="utf-8")
                child_process = subprocess.Popen(
                    [sys.executable, "-c", _CROSS_PROCESS_CHILD, str(_AGENT_CORE),
                     str(target.parent), str(attempting), str(prepared), str(entered), str(release)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self.assertTrue(_wait_for(prepared), "child did not prepare its target lease")
                time.sleep(0.15)
                self.assertFalse(entered.exists(), "child bypassed the ancestor path lock")
                release.touch()
                holder.__exit__(None, None, None)
                holder_open = False
                self.assertTrue(_wait_for(entered), "child did not enter after the parent released")
            finally:
                release.touch()
                if holder_open:
                    holder.__exit__(None, None, None)
                if child_process is not None:
                    child_process.wait(timeout=5)
            stderr = child_process.stderr.read() if child_process.stderr is not None else ""
            if child_process.stderr is not None:
                child_process.stderr.close()
            self.assertEqual(child_process.returncode, 0, stderr)

    def test_real_write_text_creates_parent_chain_under_lease_and_serializes_sibling_call(self):
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            target = paths.workspace / "nested" / "deep" / "resume.txt"
            entered_first_tool = threading.Event()
            release_first_tool = threading.Event()
            first_confirmed = threading.Event()
            second_confirmed = threading.Event()
            results = []

            def pause_first_tool(event, _payload):
                if event == "tool.start":
                    entered_first_tool.set()
                    release_first_tool.wait(2)

            first = self._executor(
                paths, "chat-first",
                confirm=lambda _preview, _all: first_confirmed.set() or "y",
                on_event=pause_first_tool,
            )
            second = self._executor(
                paths, "chat-second",
                confirm=lambda _preview, _all: second_confirmed.set() or "y",
            )

            def write(executor, body):
                results.append(executor.run("write_text", {
                    "path": "workspace/nested/deep/resume.txt",
                    "content": body,
                    "on_conflict": "overwrite",
                }))

            first_thread = threading.Thread(target=write, args=(first, "first\n"))
            first_thread.start()
            self.assertTrue(entered_first_tool.wait(1))
            self.assertTrue(first_confirmed.is_set())
            self.assertFalse(target.parent.exists(), "directories were created before the held execution point")

            second_thread = threading.Thread(target=write, args=(second, "second\n"))
            second_thread.start()
            time.sleep(0.12)
            self.assertFalse(second_confirmed.is_set(), "second writer passed the shared ancestor lease")
            self.assertFalse(target.parent.exists())

            release_first_tool.set()
            first_thread.join(3)
            second_thread.join(3)
            self.assertFalse(first_thread.is_alive(), "first writer deadlocked")
            self.assertFalse(second_thread.is_alive(), "second writer deadlocked")
            self.assertTrue(second_confirmed.is_set())
            self.assertEqual(len(results), 2)
            self.assertTrue(all(item.ok for item in results), [item.error for item in results])
            self.assertEqual(target.read_text(encoding="utf-8"), "second\n")

    def test_write_text_rename_conflicts_serialize_on_the_target_parent(self):
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            parent = paths.workspace / "rename"
            parent.mkdir(parents=True)
            target = parent / "resume.txt"
            first_started = threading.Event()
            release_first = threading.Event()
            second_confirmed = threading.Event()
            results = []

            def pause_first(event, _payload):
                if event == "tool.start":
                    first_started.set()
                    release_first.wait(2)

            first = self._executor(
                paths, "rename-first", confirm=lambda _preview, _all: "y",
                on_event=pause_first,
            )
            second = self._executor(
                paths, "rename-second",
                confirm=lambda _preview, _all: second_confirmed.set() or "y",
            )

            def write(executor, content):
                results.append(executor.run("write_text", {
                    "path": "workspace/rename/resume.txt",
                    "content": content,
                    "on_conflict": "rename",
                }))

            first_thread = threading.Thread(target=write, args=(first, "first\n"))
            first_thread.start()
            self.assertTrue(first_started.wait(1))
            second_thread = threading.Thread(target=write, args=(second, "second\n"))
            second_thread.start()
            time.sleep(0.12)
            self.assertFalse(second_confirmed.is_set(), "rename writer passed its parent lease")
            release_first.set()
            first_thread.join(3)
            second_thread.join(3)
            self.assertFalse(first_thread.is_alive())
            self.assertFalse(second_thread.is_alive())
            self.assertEqual(len(results), 2)
            self.assertTrue(all(item.ok for item in results), [item.error for item in results])
            self.assertEqual(target.read_text(encoding="utf-8"), "first\n")
            self.assertEqual((parent / "resume-1.txt").read_text(encoding="utf-8"), "second\n")

    def test_internal_alias_and_canonical_path_share_one_lease(self):
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            real_dir = paths.workspace / "real-dir"
            alias = paths.workspace / "linked-dir"
            real_dir.mkdir()
            try:
                alias.symlink_to(real_dir, target_is_directory=True)
            except OSError:
                completed = subprocess.run(
                    ["cmd.exe", "/c", "mklink", "/J", str(alias), str(real_dir)],
                    capture_output=True, text=True, check=False,
                )
                if completed.returncode != 0:
                    self.skipTest("directory symlink/junction creation is unavailable")
            target = real_dir / "deep" / "resume.txt"
            first_started = threading.Event()
            release_first = threading.Event()
            second_confirmed = threading.Event()
            results = []

            def pause_first(event, _payload):
                if event == "tool.start":
                    first_started.set()
                    release_first.wait(2)

            first = self._executor(
                paths, "alias-first", confirm=lambda _preview, _all: "y",
                on_event=pause_first,
            )
            second = self._executor(
                paths, "alias-second",
                confirm=lambda _preview, _all: second_confirmed.set() or "y",
            )

            def write(executor, path, body):
                results.append(executor.run("write_text", {
                    "path": path, "content": body, "on_conflict": "overwrite",
                }))

            first_thread = threading.Thread(
                target=write, args=(first, "workspace/linked-dir/deep/resume.txt", "alias\n")
            )
            first_thread.start()
            self.assertTrue(first_started.wait(1))
            second_thread = threading.Thread(
                target=write, args=(second, "workspace/real-dir/deep/resume.txt", "canonical\n")
            )
            second_thread.start()
            time.sleep(0.12)
            self.assertFalse(second_confirmed.is_set(), "canonical path bypassed the alias lease")
            release_first.set()
            first_thread.join(3)
            second_thread.join(3)
            self.assertFalse(first_thread.is_alive())
            self.assertFalse(second_thread.is_alive())
            self.assertEqual(len(results), 2)
            self.assertTrue(all(item.ok for item in results), [item.error for item in results])
            self.assertEqual(target.read_text(encoding="utf-8"), "canonical\n")

    def test_normal_patch_file_waits_for_the_same_shared_file_lease(self):
        with temporary_agent_paths(copy_tool_dirs=("coding/patch_file",)) as paths:
            target = paths.workspace / "patch" / "resume.txt"
            target.parent.mkdir(parents=True)
            target.write_text("old\n", encoding="utf-8")
            confirmed = threading.Event()
            executor = self._executor(
                paths, "patch-main",
                confirm=lambda _preview, _all: confirmed.set() or "y",
            )
            results = []
            holder = PROJECT_WORKER_COORDINATOR.write_lease(
                "patch-holder", "independent", target.parent, (target.name,),
                kind="independent",
            )
            holder.__enter__()
            thread = threading.Thread(target=lambda: results.append(executor.run("patch_file", {
                "path": "workspace/patch/resume.txt",
                "find": "old",
                "replacement": "new",
            })))
            thread.start()
            try:
                time.sleep(0.12)
                self.assertFalse(confirmed.is_set(), "patch_file bypassed the shared lease")
                self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
            finally:
                holder.__exit__(None, None, None)
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertTrue(confirmed.is_set())
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0].ok, results[0].error)
            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")

    def test_project_bound_write_creates_nested_parent_after_confirmation(self):
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            project_id = "nested-project-write"
            create_project(paths, project_id, project_template="light")
            project_root = paths.workspace / project_id
            target = project_root / "new" / "deep" / "resume.txt"
            confirmed = threading.Event()
            executor = ToolExecutor(
                registry=ToolRegistry.load(paths),
                session=ExecutorSession(
                    session_dir=paths.data / "sessions" / "project-chat",
                    allowed_evolved={"write_text"},
                    project_root=f"workspace/{project_id}",
                    project_id=project_id,
                    active_shell="project",
                    project_plan_status="confirmed",
                    project_workflow_stage="implementation",
                    project_entry="direct",
                ),
                confirm_fn=lambda _preview, _all: confirmed.set() or "y",
                project_worker_host_dispatch=lambda _arguments: None,
            )

            result = executor.run("write_text", {
                "path": f"workspace/{project_id}/new/deep/resume.txt",
                "content": "project-body\n",
                "on_conflict": "overwrite",
            })

            self.assertTrue(result.ok, result.error)
            # Existing in-project confirmation policy remains unchanged for a safe new file.
            self.assertFalse(confirmed.is_set())
            self.assertEqual(target.read_text(encoding="utf-8"), "project-body\n")

    def test_independent_entry_reenters_host_executor_without_double_acquiring(self):
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            target = paths.workspace / "independent" / "deep" / "resume.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            executor = self._executor(
                paths, "independent-reentrant",
                confirm=lambda _preview, _all: "y",
            )
            spec = IndependentWriteSpec(
                conversation_id="independent-reentrant-chat",
                operation_id="independent-reentrant-op",
                target_path="workspace/independent/deep/resume.txt",
                executor=executor,
                host_binding_check=lambda: True,
                scope_confirm_fn=lambda _preview: "y",
            )

            result = run_independent_write(spec, "independent-body\n")

            self.assertTrue(result.ok, result.error)
            self.assertEqual(target.read_text(encoding="utf-8"), "independent-body\n")

    def test_model_supplied_lease_flag_does_not_skip_shared_admission(self):
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            parent = paths.workspace / "forgery"
            parent.mkdir()
            target = parent / "resume.txt"
            confirmed = threading.Event()
            executor = self._executor(
                paths, "forged-token",
                confirm=lambda _preview, _all: confirmed.set() or "y",
            )
            results = []
            holder = PROJECT_WORKER_COORDINATOR.write_lease(
                "real-holder", "independent", parent, ("resume.txt",), kind="independent",
            )
            holder.__enter__()
            thread = threading.Thread(target=lambda: results.append(executor.run("write_text", {
                "path": "workspace/forgery/resume.txt",
                "content": "forged\n",
                "on_conflict": "overwrite",
                "_host_write_lease": True,
            })))
            thread.start()
            try:
                time.sleep(0.12)
                self.assertFalse(confirmed.is_set(), "model argument forged a lease bypass")
                self.assertFalse(target.exists())
            finally:
                holder.__exit__(None, None, None)
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0].ok, results[0].error)
            self.assertEqual(target.read_text(encoding="utf-8"), "forged\n")

    def test_rejected_write_does_not_create_missing_parent_chain(self):
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            executor = self._executor(
                paths, "rejected-parent",
                confirm=lambda _preview, _all: "n",
            )

            result = executor.run("write_text", {
                "path": "workspace/rejected/deep/resume.txt",
                "content": "must-not-create\n",
                "on_conflict": "overwrite",
            })

            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, "confirm_rejected")
            self.assertFalse((paths.workspace / "rejected").exists())

    def test_cancel_while_waiting_does_not_create_missing_parent_chain(self):
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            relative = "workspace/cancelled/deep/resume.txt"
            target = paths.agent_root / relative
            holder = PROJECT_WORKER_COORDINATOR.write_lease(
                "cancel-holder", "independent", paths.workspace, ("cancelled/deep/resume.txt",),
                kind="independent", allow_missing_parents=True,
            )
            holder.__enter__()
            confirmed = threading.Event()
            cancel = threading.Event()
            executor = self._executor(
                paths, "cancelled-chat",
                confirm=lambda _preview, _all: confirmed.set() or "y",
            )
            executor.cancel_event = cancel
            results = []
            thread = threading.Thread(target=lambda: results.append(executor.run("write_text", {
                "path": relative,
                "content": "never-written",
                "on_conflict": "overwrite",
            })))
            thread.start()
            try:
                time.sleep(0.12)
                self.assertFalse(confirmed.is_set())
                cancel.set()
            finally:
                holder.__exit__(None, None, None)
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(results), 1)
            self.assertFalse(results[0].ok)
            self.assertEqual(results[0].error.code, "cancelled")
            self.assertFalse(target.parent.exists())


if __name__ == "__main__":
    unittest.main()
