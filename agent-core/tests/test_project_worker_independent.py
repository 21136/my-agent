from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_worker_execution import PROJECT_WORKER_COORDINATOR
from project_worker_independent import IndependentWriteSpec, run_independent_write
from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry


class IndependentWriteTests(unittest.TestCase):
    def _executor(self, paths, confirm):
        return ToolExecutor(
            registry=ToolRegistry.load(paths),
            session=ExecutorSession(allowed_evolved={"write_text"}),
            confirm_fn=confirm,
        )

    def _spec(self, paths, executor, *, target="workspace/resume.txt", scope=None,
              binding=None, conversation_id="chat-independent", operation_id="resume-1"):
        return IndependentWriteSpec(
            conversation_id=conversation_id,
            operation_id=operation_id,
            target_path=target,
            executor=executor,
            host_binding_check=binding or (lambda: True),
            scope_confirm_fn=scope or (lambda _preview: "y"),
        )

    def test_writes_one_agent_root_file_through_both_real_confirmation_gates(self):
        previews = []
        scope_previews = []
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            target = paths.agent_root / "workspace" / "resume.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            executor = self._executor(paths, lambda preview, _all: previews.append(preview) or "y")
            spec = self._spec(paths, executor, scope=lambda preview: scope_previews.append(preview) or "y")

            result = run_independent_write(spec, "Resume body\n")

            self.assertTrue(result.ok, result.error)
            self.assertEqual(target.read_text(encoding="utf-8"), "Resume body\n")
            self.assertEqual(len(scope_previews), 1)
            self.assertIn("workspace/resume.txt", scope_previews[0])
            self.assertEqual(len(previews), 1)
            self.assertIn("workspace/resume.txt", previews[0])

    def test_unconfirmed_scope_or_tool_write_never_changes_target(self):
        for gate in ("scope", "write"):
            with self.subTest(gate=gate), temporary_agent_paths(
                copy_tool_dirs=("common/write_text",)
            ) as paths:
                target = paths.agent_root / "workspace" / "resume.txt"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("original\n", encoding="utf-8")
                executor = self._executor(
                    paths, lambda _preview, _all: "n" if gate == "write" else "y"
                )
                spec = self._spec(
                    paths,
                    executor,
                    scope=lambda _preview: "n" if gate == "scope" else "y",
                    operation_id=f"resume-{gate}",
                )

                result = run_independent_write(spec, "changed\n")

                self.assertFalse(result.ok)
                self.assertEqual(target.read_text(encoding="utf-8"), "original\n")

    def test_stale_host_binding_during_write_confirmation_rejects_the_write(self):
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            target = paths.agent_root / "workspace" / "resume.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("original\n", encoding="utf-8")
            binding = {"current": True}

            def write_confirm(_preview, _all):
                binding["current"] = False
                return "y"

            spec = self._spec(
                paths,
                self._executor(paths, write_confirm),
                binding=lambda: binding["current"],
                operation_id="stale-confirm",
            )

            result = run_independent_write(spec, "changed\n")

            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, "confirm_rejected")
            self.assertEqual(target.read_text(encoding="utf-8"), "original\n")
    def test_target_replacement_during_scope_confirmation_fails_closed(self):
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            target = paths.agent_root / "workspace" / "resume.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("original\n", encoding="utf-8")
            replacement = target.with_name("replacement.txt")
            replacement.write_text("changed externally\n", encoding="utf-8")
            executor_confirmed = []

            def approve_scope(_preview):
                replacement.replace(target)
                return "y"

            spec = self._spec(
                paths,
                self._executor(paths, lambda *_args: executor_confirmed.append(True) or "y"),
                scope=approve_scope,
                operation_id="identity-race",
            )
            with self.assertRaises(PermissionError):
                run_independent_write(spec, "worker content\n")

            self.assertEqual(target.read_text(encoding="utf-8"), "changed externally\n")
            self.assertEqual(executor_confirmed, [])
    def test_rejects_external_absolute_host_and_traversal_paths_before_confirmation(self):
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            confirmed = []
            executor = self._executor(paths, lambda *_args: confirmed.append(True) or "y")
            outside = paths.agent_root.parent / "outside-resume.txt"
            for target in (str(outside), "host:docs/resume.txt", "../outside-resume.txt"):
                with self.subTest(target=target):
                    spec = self._spec(paths, executor, target=target)
                    with self.assertRaises(PermissionError):
                        run_independent_write(spec, "must not write")
            self.assertEqual(confirmed, [])
            self.assertFalse(outside.exists())

    def test_rejects_project_bound_executor_instead_of_inheriting_project_privilege(self):
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            executor = self._executor(paths, lambda *_args: "y")
            executor.session.active_shell = "project"
            executor.session.project_root = "workspace/project-a"
            executor.session.project_id = "project-a"
            spec = self._spec(paths, executor)
            with self.assertRaises(PermissionError):
                run_independent_write(spec, "must not inherit project scope")

    def test_shared_coordinator_serializes_independent_write_against_project_target(self):
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            project_root = paths.workspace / "project-a"
            project_root.mkdir(parents=True, exist_ok=True)
            target = project_root / "resume.txt"
            target.write_text("before\n", encoding="utf-8")
            entered_project = threading.Event()
            release_project = threading.Event()
            independent_done = threading.Event()
            errors = []
            executor = self._executor(paths, lambda _preview, _all: "y")
            spec = self._spec(
                paths, executor, target="workspace/project-a/resume.txt",
                operation_id="resume-conflict",
            )

            def hold_project_lease():
                try:
                    with PROJECT_WORKER_COORDINATOR.write_lease(
                        "chat-project", "project-a", project_root, ("resume.txt",), kind="project"
                    ):
                        entered_project.set()
                        release_project.wait(2)
                except BaseException as exc:
                    errors.append(exc)

            def write_independent():
                try:
                    run_independent_write(spec, "after\n")
                    independent_done.set()
                except BaseException as exc:
                    errors.append(exc)

            project_thread = threading.Thread(target=hold_project_lease)
            project_thread.start()
            self.assertTrue(entered_project.wait(1))
            independent_thread = threading.Thread(target=write_independent)
            independent_thread.start()
            time.sleep(0.12)
            self.assertFalse(independent_done.is_set(), "independent target bypassed project lease")
            release_project.set()
            project_thread.join(2)
            independent_thread.join(2)
            self.assertFalse(project_thread.is_alive())
            self.assertFalse(independent_thread.is_alive())
            self.assertEqual(errors, [])
            self.assertTrue(independent_done.is_set())
            self.assertEqual(target.read_text(encoding="utf-8"), "after\n")


    def test_existing_write_scope_and_protected_path_rules_remain_enforced(self):
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            executor = self._executor(paths, lambda _preview, _all: "y")
            executor.session.write_scope_root = "workspace/allowed"
            scope_calls = []
            for target in ("workspace/other/resume.txt", "workspace/allowed/.git/config"):
                with self.subTest(target=target):
                    spec = self._spec(
                        paths, executor, target=target,
                        scope=lambda preview: scope_calls.append(preview) or "y",
                    )
                    with self.assertRaises(PermissionError):
                        run_independent_write(spec, "denied")
            self.assertEqual(scope_calls, [])

    def test_shared_coordinator_allows_at_most_one_independent_write_per_conversation(self):
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            (paths.workspace / "independent-a").mkdir()
            (paths.workspace / "independent-b").mkdir()
            first_confirm_entered = threading.Event()
            release_first = threading.Event()
            second_write_confirmed = threading.Event()
            errors = []

            def first_confirm(_preview, _all):
                first_confirm_entered.set()
                if not release_first.wait(2):
                    return "n"
                return "y"

            first = self._spec(
                paths,
                self._executor(paths, first_confirm),
                target="workspace/independent-a/resume.txt",
                conversation_id="same-chat",
                operation_id="independent-a",
            )
            second = self._spec(
                paths,
                self._executor(paths, lambda _preview, _all: second_write_confirmed.set() or "y"),
                target="workspace/independent-b/resume.txt",
                conversation_id="same-chat",
                operation_id="independent-b",
            )

            def execute(spec, body):
                try:
                    result = run_independent_write(spec, body)
                    if not result.ok:
                        errors.append(result.error)
                except BaseException as exc:
                    errors.append(exc)

            first_thread = threading.Thread(target=execute, args=(first, "first\n"))
            first_thread.start()
            self.assertTrue(first_confirm_entered.wait(1))
            second_thread = threading.Thread(target=execute, args=(second, "second\n"))
            second_thread.start()
            time.sleep(0.12)
            self.assertFalse(second_write_confirmed.is_set(), "second independent write passed the shared lease")
            release_first.set()
            first_thread.join(2)
            second_thread.join(2)
            self.assertFalse(first_thread.is_alive())
            self.assertFalse(second_thread.is_alive())
            self.assertEqual(errors, [])
            self.assertTrue(second_write_confirmed.is_set())
if __name__ == "__main__":
    unittest.main()



