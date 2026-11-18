from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_evolution_verification import VerificationRunner, WorkspaceBinding


class SelfEvolutionVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "candidate"
        self.root.mkdir()
        self.source = self.root / "candidate.py"
        self.source.write_text("VALUE = 1\n", encoding="utf-8")
        self.binding = WorkspaceBinding(
            task_id="task-42", candidate_root=self.root, candidate_id="candidate-42",
            config_identity="config-rev-7", source_paths=("candidate.py",),
        )
        self.resolve = lambda task_id: self.binding if task_id == "task-42" else None
        self.runner = VerificationRunner(
            {"check": (sys.executable, "-c", "import os; print('verified'); print(os.getcwd())")},
            workspace_resolver=self.resolve, output_limit_chars=256,
        )

    def run_check(self, **kwargs):
        options = {
            "task_id": "task-42",
            "command_id": "check",
            "timeout_seconds": 2.0,
        }
        options.update(kwargs)
        return self.runner.run(**options)

    def test_executes_fixed_argv_in_candidate_and_binds_snapshot(self) -> None:
        result = self.run_check()
        self.assertEqual((result.outcome, result.error_code), ("passed", None))
        self.assertEqual(result.exit_code, 0)
        self.assertGreaterEqual(result.duration_seconds, 0)
        self.assertTrue(result.runtime)
        self.assertEqual(result.source_digest_before, result.source_digest_after)
        self.assertEqual(len(result.candidate_binding_digest), 64)
        self.assertEqual(result.stdout_excerpt, "[OUTPUT REDACTED]")
        self.assertTrue(result.cwd_is_candidate_root)
        self.assertNotIn(str(self.root.resolve()), result.stdout_excerpt)
        self.assertEqual(result.evidence_scope, "declared_source_paths_and_windows_job_tree")
        self.assertEqual(result.process_tree_state, "stopped")
        self.assertEqual(result.job_tree_state, "stopped")
        self.assertFalse(result.descendants_may_be_alive)
        self.assertFalse(result.requires_reconciliation)
        self.assertFalse(result.automatic_retry_allowed)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object behavior")
    def test_uncertain_job_start_cleanup_stays_unknown(self) -> None:
        from unittest.mock import patch
        from self_evolution_process import WindowsJobUnavailable
        with patch("self_evolution_verification.start_windows_job_process",
                   side_effect=WindowsJobUnavailable("private detail", process_started=True,
                                                     cleanup_confirmed=False)):
            result = self.run_check()
        self.assertEqual((result.outcome, result.error_code),
                         ("unknown", "process_start_cleanup_unconfirmed"))
        self.assertEqual(result.process_tree_state, "unknown")
        self.assertTrue(result.requires_reconciliation)
        self.assertTrue(result.descendants_may_be_alive)
        self.assertFalse(result.automatic_retry_allowed)
        self.assertNotIn("private detail", str(result))

    def test_job_completion_proves_only_the_registered_job_scope(self) -> None:
        result = self.run_check()
        if os.name != "nt":
            self.assertEqual(result.outcome, "failed")
            self.assertEqual(result.error_code, "candidate_guard_unavailable")
            return
        self.assertEqual((result.outcome, result.error_code), ("passed", None))
        self.assertEqual(result.process_tree_state, "stopped")
        self.assertEqual(result.job_tree_state, "stopped")
        self.assertEqual(result.evidence_scope, "declared_source_paths_and_windows_job_tree")
        self.assertFalse(result.requires_reconciliation)

    def test_different_tasks_same_candidate_cannot_run_concurrently(self) -> None:
        from dataclasses import replace
        ready = self.root / "candidate-concurrent-ready"
        command = (sys.executable, "-c", "from pathlib import Path; import time; Path('candidate-concurrent-ready').write_text('yes'); time.sleep(5)")
        resolver = lambda task: replace(self.binding, task_id=task)
        first = VerificationRunner({"check": command}, workspace_resolver=resolver)
        second = VerificationRunner({"check": command}, workspace_resolver=resolver)
        cancel = threading.Event()
        holder = {}
        thread = threading.Thread(target=lambda: holder.setdefault("result", first.run(task_id="one", command_id="check", timeout_seconds=8, cancel_event=cancel)))
        thread.start()
        try:
            self.assertTrue(self.wait_for(ready, 3))
            result = second.run(task_id="two", command_id="check", timeout_seconds=2)
            self.assertEqual(result.error_code, "candidate_in_progress")
            self.assertEqual(result.process_tree_state, "not_started")
        finally:
            cancel.set()
            thread.join(timeout=6)
        self.assertFalse(thread.is_alive())
        retry = second.run(task_id="two", command_id="check", timeout_seconds=1)
        self.assertEqual(retry.error_code, "candidate_not_reconciled")

    @unittest.skipUnless(os.name == "nt", "Windows cross-process candidate guard")
    def test_other_process_cannot_redispatch_same_unreconciled_candidate(self) -> None:
        import subprocess
        from dataclasses import replace
        ready = self.root / "cross-process-ready"
        command = (sys.executable, "-c", "from pathlib import Path; import time; Path('cross-process-ready').write_text('yes'); time.sleep(8)")
        resolver = lambda task: replace(self.binding, task_id=task)
        runner = VerificationRunner({"check": command}, workspace_resolver=resolver)
        cancel = threading.Event()
        holder = {}
        thread = threading.Thread(target=lambda: holder.setdefault("result", runner.run(task_id="one", command_id="check", timeout_seconds=10, cancel_event=cancel)))
        thread.start()
        script = (
            "import sys; from pathlib import Path; "
            f"sys.path.insert(0, {str(_AGENT_CORE)!r}); "
            "from self_evolution_verification import VerificationRunner, WorkspaceBinding; "
            f"root={str(self.root)!r}; "
            "r=VerificationRunner({'check':(sys.executable,'-c','pass')}, "
            "workspace_resolver=lambda task: WorkspaceBinding(task, root, 'same', 'cfg', ('candidate.py',))); "
            "print(r.run(task_id='two',command_id='check',timeout_seconds=2).error_code)"
        )
        try:
            self.assertTrue(self.wait_for(ready, 3))
            active = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=5, check=True)
            self.assertEqual(active.stdout.strip(), "candidate_in_progress")
            cancel.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertTrue(holder["result"].requires_reconciliation)
            after = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=5, check=True)
            self.assertEqual(after.stdout.strip(), "candidate_not_reconciled")
        finally:
            cancel.set()
            thread.join(timeout=5)

    def test_no_reader_threads_survive_external_pipe_writer(self) -> None:
        import subprocess
        from unittest.mock import patch
        import self_evolution_verification as verification
        writer = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        streams = (writer.stdout, writer.stderr)
        class SimulatedFinishedJob:
            returncode = 0
            tree_stopped = False
            stdout, stderr = streams
            def poll(self): return 0
            def active_processes(self): return 0
            def mark_tree_stopped(self): pass
            def wait(self, timeout=None): return 0
            def close(self): pass
        try:
            before = {thread.ident for thread in threading.enumerate()}
            with patch.object(verification, "WindowsJobProcess", SimulatedFinishedJob), patch.object(verification, "start_windows_job_process", return_value=SimulatedFinishedJob()):
                result = self.run_check()
            self.assertEqual(result.outcome, "unknown")
            self.assertIsNone(writer.poll(), "external writer unexpectedly exited during the test")
            self.assertTrue(all(stream.closed for stream in streams))
            extra = [thread for thread in threading.enumerate() if thread.ident not in before]
            self.assertFalse(extra, f"reader threads leaked: {extra}")
        finally:
            writer.terminate()
            writer.wait(timeout=3)
            for stream in streams:
                if not stream.closed: stream.close()

    def test_nonzero_exit_is_failed_not_success_from_output(self) -> None:
        runner = VerificationRunner({"check": (sys.executable, "-c", "print('looks good'); raise SystemExit(9)")}, workspace_resolver=self.resolve)
        result = self.run_with(runner)
        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.exit_code, 9)
        self.assertEqual(result.stdout_excerpt, "[OUTPUT REDACTED]")

    def test_model_cannot_choose_command_or_append_shell_text(self) -> None:
        result = self.run_check(command_id="check; echo injected")
        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.error_code, "command_not_allowed")
        self.assertIsNone(result.exit_code)

    def test_caller_cannot_supply_or_replace_trusted_fields(self) -> None:
        for key, value in {
            "candidate_root": self.root.parent, "candidate_id": "forged",
            "config_identity": "fake", "expected_config_identity": "fake",
            "source_paths": ("candidate.py",), "workspace_resolver": self.resolve,
        }.items():
            with self.subTest(key=key), self.assertRaises(TypeError):
                self.run_check(**{key: value})

    def test_unknown_task_and_resolver_failure_fail_closed(self) -> None:
        result = self.run_check(task_id="forged-task")
        self.assertEqual((result.outcome, result.error_code), ("failed", "workspace_unavailable"))
        self.assertFalse(result.cwd_is_candidate_root)
        def broken(_task_id):
            raise RuntimeError("secret resolver details")
        runner = VerificationRunner({"check": (sys.executable, "-c", "pass")}, workspace_resolver=broken)
        result = self.run_with(runner)
        self.assertEqual((result.outcome, result.error_code), ("failed", "workspace_unavailable"))
        self.assertNotIn("secret resolver details", str(result))

    def test_resolver_must_bind_requested_task(self) -> None:
        self.binding = WorkspaceBinding("other-task", self.root, "candidate-42", "config-rev-7", ("candidate.py",))
        result = self.run_check()
        self.assertEqual((result.outcome, result.error_code), ("failed", "workspace_unavailable"))

    def test_prelaunch_binding_replacement_is_rejected(self) -> None:
        from dataclasses import replace
        other = self.root.parent / "other"
        other.mkdir()
        (other / "candidate.py").write_text("VALUE = 1\n", encoding="utf-8")
        for field, value in {"candidate_root": other, "candidate_id": "fake",
                             "config_identity": "changed", "source_paths": ("other.py",)}.items():
            with self.subTest(field=field):
                responses = iter((self.binding, replace(self.binding, **{field: value})))
                runner = VerificationRunner({"check": (sys.executable, "-c", "pass")}, workspace_resolver=lambda _: next(responses))
                result = self.run_with(runner)
                self.assertEqual((result.outcome, result.error_code), ("failed", "workspace_changed"))
                self.assertFalse(result.cwd_is_candidate_root)

    def test_directory_replaced_at_same_path_before_launch_is_rejected(self) -> None:
        calls = 0
        def resolver(_task_id):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.root.rename(self.root.parent / "original")
                self.root.mkdir()
                (self.root / "candidate.py").write_text("VALUE = 1\n", encoding="utf-8")
            return self.binding
        runner = VerificationRunner({"check": (sys.executable, "-c", "pass")}, workspace_resolver=resolver)
        result = self.run_with(runner)
        self.assertEqual((result.outcome, result.error_code), ("failed", "workspace_changed"))
        self.assertFalse(result.cwd_is_candidate_root)

    def test_source_changed_at_second_resolution_never_runs(self) -> None:
        calls = 0
        def resolver(_task_id):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.source.write_text("VALUE = 99\n", encoding="utf-8")
            return self.binding
        runner = VerificationRunner({"check": (sys.executable, "-c", "pass")}, workspace_resolver=resolver)
        result = self.run_with(runner)
        self.assertEqual((result.outcome, result.error_code), ("failed", "source_changed_before_start"))
        self.assertFalse(result.cwd_is_candidate_root)

    def test_restored_source_is_only_point_in_time_evidence(self) -> None:
        program = "from pathlib import Path; p=Path('candidate.py'); x=p.read_bytes(); p.write_bytes(b'changed'); p.write_bytes(x)"
        runner = VerificationRunner({"check": (sys.executable, "-c", program)}, workspace_resolver=self.resolve)
        result = self.run_with(runner)
        self.assertEqual((result.outcome, result.error_code), ("unknown", "source_changed"))
        self.assertNotEqual(result.source_digest_before, result.source_digest_after)
        self.assertEqual(result.evidence_scope, "declared_source_paths_and_windows_job_tree")

    def test_binding_replacement_after_launch_is_unknown(self) -> None:
        for field in ("candidate_id", "config_identity", "candidate_root", "source_paths"):
            with self.subTest(field=field):
                from dataclasses import replace
                subroot = self.root / ("binding-" + field)
                subroot.mkdir()
                (subroot / "candidate.py").write_text("VALUE = 1\n", encoding="utf-8")
                self.binding = replace(self.binding, candidate_root=subroot)
                values = {"candidate_id": "other-id", "config_identity": "new-config",
                          "candidate_root": self.root.parent, "source_paths": ("other.py",)}
                calls = 0
                def resolver(_task_id):
                    nonlocal calls
                    calls += 1
                    return self.binding if calls < 3 else replace(self.binding, **{field: values[field]})
                runner = VerificationRunner({"check": (sys.executable, "-c", "pass")}, workspace_resolver=resolver)
                result = self.run_with(runner)
                self.assertEqual((result.outcome, result.error_code), ("unknown", "workspace_changed"))
                self.assertEqual(result.exit_code, 0)

    def test_directory_replaced_at_same_path_after_launch_is_unknown(self) -> None:
        calls = 0
        def resolver(_task_id):
            nonlocal calls
            calls += 1
            if calls == 3:
                self.root.rename(self.root.parent / "original")
                self.root.mkdir()
                (self.root / "candidate.py").write_text("VALUE = 1\n", encoding="utf-8")
            return self.binding
        runner = VerificationRunner({"check": (sys.executable, "-c", "pass")}, workspace_resolver=resolver)
        result = self.run_with(runner)
        self.assertEqual((result.outcome, result.error_code), ("unknown", "workspace_changed"))
        self.assertEqual(result.exit_code, 0)

    def test_binding_digest_changes_with_authoritative_config_or_argv(self) -> None:
        from dataclasses import replace
        first = self.run_check()
        self.binding = replace(self.binding, config_identity="revision-8")
        second = self.run_check()
        self.assertEqual((first.outcome, second.outcome), ("passed", "passed"))
        self.assertNotEqual(first.candidate_binding_digest, second.candidate_binding_digest)
        from self_evolution_verification import _digest_parts
        info = self.root.stat()
        root = os.path.normcase(str(self.root.resolve()))
        source = first.source_digest_before
        argv = (sys.executable, "-c", "import os; print('verified'); print(os.getcwd())")
        def expected(config, command):
            base = _digest_parts("self-evolution-verification-v2", "task-42", "candidate-42",
                                 config, root, str((info.st_dev, info.st_ino)), "check",
                                 _digest_parts(*command), source)
            return _digest_parts(base, source, source)
        self.assertEqual(first.candidate_binding_digest, expected("config-rev-7", argv))
        self.assertNotEqual(first.candidate_binding_digest, expected("revision-8", argv))
        self.assertNotEqual(first.candidate_binding_digest, expected("config-rev-7", (*argv, "changed")))

    def test_resolver_exception_after_launch_is_unknown(self) -> None:
        calls = 0
        def resolver(_task_id):
            nonlocal calls
            calls += 1
            if calls > 2:
                raise RuntimeError("private")
            return self.binding
        runner = VerificationRunner({"check": (sys.executable, "-c", "pass")}, workspace_resolver=resolver)
        result = self.run_with(runner)
        self.assertEqual((result.outcome, result.error_code), ("unknown", "workspace_changed"))

    def test_rejects_casefold_aliases_and_git_metadata_source_paths(self) -> None:
        from dataclasses import replace
        for paths in (("src/Module.py", "src/module.py"), (".GIT/config",), ("src/file.py:stream",), ("src/CON.txt",)):
            with self.subTest(paths=paths):
                self.binding = replace(self.binding, source_paths=paths)
                result = self.run_check()
                self.assertEqual((result.outcome, result.error_code), ("failed", "source_path_invalid"))

    def test_rejects_source_path_escape(self) -> None:
        outside = Path(self.temp.name) / "outside.py"
        outside.write_text("x=1", encoding="utf-8")
        from dataclasses import replace
        self.binding = replace(self.binding, source_paths=("../outside.py",))
        result = self.run_check()
        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.error_code, "source_path_invalid")

    @unittest.skipUnless(os.name == "nt", "Windows process-handle observation")
    def test_cancel_waits_for_observed_real_descendant_before_cleanup(self) -> None:
        import ctypes
        from ctypes import wintypes
        from dataclasses import replace
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        for index in range(16):
            with self.subTest(index=index):
                root = self.root / f"lifetime-{index}"
                root.mkdir()
                (root / "candidate.py").write_text("x=1", encoding="utf-8")
                ready = root / "pid"
                program = "import os,time; from pathlib import Path; Path('pid').write_text(str(os.getpid())); time.sleep(20)"
                binding = replace(self.binding, candidate_root=root)
                runner = VerificationRunner({"check": (sys.executable, "-c", program)}, workspace_resolver=lambda _: binding)
                cancel = threading.Event()
                holder = {}
                thread = threading.Thread(target=lambda: holder.setdefault("result", self.run_with(runner, timeout_seconds=6, cancel_event=cancel)))
                thread.start()
                handle = None
                try:
                    self.assertTrue(self.wait_for(ready, 3))
                    handle = kernel.OpenProcess(0x00100000, False, int(ready.read_text()))
                    self.assertTrue(handle)
                    cancel.set()
                    thread.join(timeout=5)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(holder["result"].job_tree_state, "stopped")
                    self.assertEqual(kernel.WaitForSingleObject(handle, 0), 0,
                                     "Job reported stopped before observed child exited")
                finally:
                    cancel.set()
                    thread.join(timeout=5)
                    if handle:
                        kernel.WaitForSingleObject(handle, 3000)
                        kernel.CloseHandle(handle)

    @unittest.skipUnless(os.name == "nt", "Windows process-handle observation")
    def test_timeout_waits_for_all_observed_job_process_handles(self) -> None:
        import ctypes
        from ctypes import wintypes
        from dataclasses import replace
        from unittest.mock import patch
        from self_evolution_process import WindowsJobProcess
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        kernel.QueryInformationJobObject.restype = wintypes.BOOL
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        kernel.IsProcessInJob.restype = wintypes.BOOL
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        class JobPids(ctypes.Structure):
            _fields_ = [("assigned", wintypes.DWORD), ("count", wintypes.DWORD), ("pids", ctypes.c_size_t * 64)]
        original = WindowsJobProcess.terminate_job
        handles = []
        def observe_then_terminate(job):
            info = JobPids()
            returned = wintypes.DWORD()
            self.assertTrue(kernel.QueryInformationJobObject(job._job, 3, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned)))
            for pid in info.pids[:info.count]:
                handle = kernel.OpenProcess(0x00101000, False, pid)
                if handle:
                    member = wintypes.BOOL()
                    if kernel.IsProcessInJob(handle, job._job, ctypes.byref(member)) and member.value:
                        handles.append((pid, handle))
                    else:
                        kernel.CloseHandle(handle)
            original(job)
        for index in range(30):
            root = self.root / f"timeout-observed-{index}"
            root.mkdir()
            (root / "candidate.py").write_text("x=1", encoding="utf-8")
            binding = replace(self.binding, candidate_root=root)
            runner = VerificationRunner({"check": (sys.executable, "-c", "import time; time.sleep(10)")}, workspace_resolver=lambda _: binding)
            try:
                with patch.object(WindowsJobProcess, "terminate_job", observe_then_terminate):
                    result = self.run_with(runner, timeout_seconds=0.15)
                self.assertEqual(result.error_code, "timeout")
                self.assertTrue(handles, "no actual Job process was observed")
                live = [pid for pid, handle in handles if kernel.WaitForSingleObject(handle, 0) != 0]
                self.assertFalse(live, f"runner returned while observed Job processes were still running: {live}")
            finally:
                for _, handle in handles:
                    self.assertEqual(kernel.WaitForSingleObject(handle, 3000), 0, "synthetic Job process did not exit")
                    kernel.CloseHandle(handle)
                handles.clear()

    def test_timeout_is_unknown_after_stop_request(self) -> None:
        runner = VerificationRunner({"check": (sys.executable, "-c", "import time; time.sleep(10)")}, workspace_resolver=self.resolve)
        result = self.run_with(runner, timeout_seconds=0.15)
        self.assertEqual(result.outcome, "unknown")
        self.assertEqual(result.error_code, "timeout")
        self.assertIsNone(result.exit_code)
        self.assertLess(result.duration_seconds, 5)

    def test_cancel_is_reported_unknown(self) -> None:
        event = threading.Event()
        event.set()
        result = self.run_check(cancel_event=event)
        self.assertEqual(result.outcome, "unknown")
        self.assertEqual(result.error_code, "cancelled")

    def test_cancel_during_process_is_unknown(self) -> None:
        event = threading.Event()
        runner = VerificationRunner({"check": (sys.executable, "-c", "import time; time.sleep(10)")}, workspace_resolver=self.resolve)
        finished = threading.Event()
        holder = {}

        def run() -> None:
            holder["result"] = self.run_with(runner, timeout_seconds=5, cancel_event=event)
            finished.set()

        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.15)
        event.set()
        thread.join(timeout=4)
        self.assertTrue(finished.is_set())
        self.assertEqual(holder["result"].outcome, "unknown")
        self.assertEqual(holder["result"].error_code, "cancelled")
        self.assertEqual(holder["result"].process_tree_state, "unknown")
        self.assertEqual(holder["result"].job_tree_state, "stopped")
        self.assertTrue(holder["result"].descendants_may_be_alive)
        self.assertTrue(holder["result"].requires_reconciliation)
        self.assertFalse(holder["result"].automatic_retry_allowed)

    @unittest.skipUnless(os.name == "nt", "Windows-specific process-tree behavior")
    def test_windows_cancel_and_timeout_terminate_real_job_descendants(self) -> None:
        # Real child processes exercise Job Object containment; no mocks or PID kills.
        for cause in ("cancelled", "timeout"):
            with self.subTest(cause=cause):
                from dataclasses import replace
                subroot = self.root / cause
                subroot.mkdir()
                (subroot / "candidate.py").write_text("VALUE = 1\n", encoding="utf-8")
                self.binding = replace(self.binding, candidate_root=subroot)
                ready = self.root / (cause + "-ready")
                probe = self.root / (cause + "-probe")
                alive = self.root / (cause + "-alive")
                stop = self.root / (cause + "-stop")
                finished = self.root / (cause + "-finished")
                child = (
                    "from pathlib import Path; import os, time\n"
                    f"ready=Path({str(ready)!r}); probe=Path({str(probe)!r}); "
                    f"alive=Path({str(alive)!r}); stop=Path({str(stop)!r}); finished=Path({str(finished)!r})\n"
                    "ready.write_text(str(os.getpid())); end=time.monotonic()+15\n"
                    "while not stop.exists() and time.monotonic()<end:\n"
                    " if probe.exists(): alive.write_text('alive')\n"
                    " time.sleep(.02)\n"
                    "finished.write_text('finished')\n"
                )
                parent = (
                    "import subprocess, sys, time\n"
                    f"subprocess.Popen([sys.executable, '-c', {child!r}], "
                    "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                    "time.sleep(15)\n"
                )
                runner = VerificationRunner({"check": (sys.executable, "-c", parent)}, workspace_resolver=self.resolve)
                cancel = threading.Event()
                holder = {}
                thread = threading.Thread(target=lambda: holder.setdefault("result", self.run_with(
                    runner, timeout_seconds=8, cancel_event=cancel)))
                thread.start()
                child_handle = None
                try:
                    self.assertTrue(self.wait_for(ready, 7), "real Job descendant did not complete the ready handshake")
                    import ctypes
                    from ctypes import wintypes
                    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                    kernel.OpenProcess.restype = wintypes.HANDLE
                    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                    kernel.WaitForSingleObject.restype = wintypes.DWORD
                    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                    child_handle = kernel.OpenProcess(0x00100000, False, int(ready.read_text()))
                    self.assertTrue(child_handle, "could not open synthetic child for exit observation")
                    if cause == "cancelled":
                        cancel.set()
                    thread.join(timeout=11)
                    self.assertFalse(thread.is_alive(), "runner did not return")
                    result = holder["result"]
                    self.assertEqual((result.outcome, result.error_code), ("unknown", cause))
                    probe.write_text("probe", encoding="utf8")
                    self.assertFalse(self.wait_for(alive, 0.5), "Job descendant survived termination")
                    self.assertEqual(result.process_tree_state, "unknown")
                    self.assertEqual(result.job_tree_state, "stopped")
                    self.assertTrue(result.descendants_may_be_alive)
                    self.assertTrue(result.requires_reconciliation)
                    self.assertFalse(result.automatic_retry_allowed)
                    self.assertEqual(runner._unreconciled_tasks, {"task-42"})
                finally:
                    cancel.set()
                    stop.write_text("stop", encoding="utf8")
                    thread.join(timeout=11)
                    if child_handle:
                        try:
                            self.assertEqual(kernel.WaitForSingleObject(child_handle, 3000), 0,
                                             "synthetic descendant did not exit")
                        finally:
                            kernel.CloseHandle(child_handle)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object behavior")
    def test_windows_root_exit_waits_for_real_descendant_before_passing(self) -> None:
        ready = self.root / "tree-ready"
        stop = self.root / "tree-stop"
        child = (
            "from pathlib import Path; import time\n"
            f"ready=Path({str(ready)!r}); stop=Path({str(stop)!r})\n"
            "ready.write_text('child alive')\n"
            "while not stop.exists(): time.sleep(.01)\n"
        )
        parent = (
            "import subprocess, sys\n"
            f"subprocess.Popen([sys.executable, '-c', {child!r}], "
            "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        )
        runner = VerificationRunner({"check": (sys.executable, "-c", parent)}, workspace_resolver=self.resolve)
        holder = {}
        cancel = threading.Event()
        thread = threading.Thread(target=lambda: holder.setdefault("result", self.run_with(runner, timeout_seconds=30, cancel_event=cancel)))
        thread.start()
        try:
            self.assertTrue(self.wait_for(ready, 15), "real Job descendant did not complete the ready handshake")
            time.sleep(0.1)  # Root has exited; descendant remains in the Job.
            self.assertTrue(thread.is_alive(), "direct-child exit was incorrectly treated as tree completion")
            stop.write_text("stop", encoding="utf-8")
            thread.join(timeout=4)
            self.assertFalse(thread.is_alive())
            result = holder["result"]
            self.assertEqual((result.outcome, result.error_code), ("passed", None))
            self.assertEqual(result.process_tree_state, "stopped")
            self.assertEqual(result.job_tree_state, "stopped")
            self.assertFalse(result.descendants_may_be_alive)
            self.assertFalse(result.requires_reconciliation)
            self.assertNotIn("task-42", runner._unreconciled_tasks)
        finally:
            cancel.set()
            stop.write_text("stop", encoding="utf-8")
            thread.join(timeout=4)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object behavior")
    def test_success_exit_stays_unknown_when_job_stop_proof_is_unavailable(self) -> None:
        from unittest.mock import patch
        import self_evolution_verification as verification
        from self_evolution_process import WindowsJobProcess

        runner = VerificationRunner({"check": (sys.executable, "-c", "pass")},
                                    workspace_resolver=self.resolve)
        # Isolate the deliberately retained uncertainty markers and close their
        # test-owned handles after the real temporary process exits.
        unresolved_keys: set[str] = set()
        active_keys: set[str] = set()
        retained_handles: list[tuple[object, int]] = []
        with patch.object(verification, "_UNRECONCILED_CANDIDATES", unresolved_keys), \
                patch.object(verification, "_ACTIVE_CANDIDATES", active_keys), \
                patch.object(verification, "_QUARANTINE_HANDLES", retained_handles):
            with patch.object(WindowsJobProcess, "mark_tree_stopped", lambda _self: None):
                result = self.run_with(runner)
            self.assertTrue(unresolved_keys)
            for kernel, handle in retained_handles:
                kernel.CloseHandle(handle)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.outcome, "unknown")
        self.assertEqual(result.error_code, "process_tree_unconfirmed")
        self.assertEqual(result.process_tree_state, "unknown")
        self.assertEqual(result.job_tree_state, "unknown")
        self.assertTrue(result.requires_reconciliation)
        self.assertTrue(result.descendants_may_be_alive)

    @staticmethod
    def wait_for(path: Path, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if path.exists():
                return True
            time.sleep(0.02)
        return path.exists()

    def test_concurrent_same_task_cannot_dispatch(self) -> None:
        ready = self.root / "running"
        program = "from pathlib import Path; import time; Path('running').write_text('yes'); time.sleep(1)"
        runner = VerificationRunner({"check": (sys.executable, "-c", program)}, workspace_resolver=self.resolve)
        event = threading.Event()
        holder = {}
        thread = threading.Thread(target=lambda: holder.setdefault("result", self.run_with(runner, cancel_event=event)))
        thread.start()
        try:
            self.assertTrue(self.wait_for(ready, 2))
            duplicate = self.run_with(runner)
            self.assertEqual((duplicate.outcome, duplicate.error_code), ("failed", "task_in_progress"))
            self.assertTrue(duplicate.requires_reconciliation)
        finally:
            event.set()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(holder["result"].error_code, "cancelled")
        self.assertEqual(self.run_with(runner).error_code, "task_not_reconciled")

    def test_unexpected_runner_error_fails_closed_and_blocks_redispatch(self) -> None:
        from unittest.mock import patch
        with patch.object(self.runner, "_run_once", side_effect=RuntimeError("secret internal details")):
            result = self.run_check()
        self.assertEqual((result.outcome, result.error_code), ("unknown", "runner_error"))
        self.assertTrue(result.requires_reconciliation)
        self.assertTrue(result.descendants_may_be_alive)
        self.assertNotIn("secret internal details", str(result))
        retry = self.run_check()
        self.assertEqual(retry.error_code, "task_not_reconciled")

    def test_prelaunch_cancellation_does_not_claim_descendants(self) -> None:
        event = threading.Event()
        event.set()
        result = self.run_check(cancel_event=event)
        self.assertEqual(result.process_tree_state, "not_started")
        self.assertFalse(result.descendants_may_be_alive)
        self.assertFalse(result.requires_reconciliation)
        self.assertFalse(result.automatic_retry_allowed)

    def test_source_mutation_during_execution_invalidates_result(self) -> None:
        runner = VerificationRunner({
            "check": (sys.executable, "-c", "from pathlib import Path; Path('candidate.py').write_text('VALUE = 2\\n'); print('done')")
        }, workspace_resolver=self.resolve)
        result = self.run_with(runner)
        self.assertEqual(result.outcome, "unknown")
        self.assertEqual(result.error_code, "source_changed")
        self.assertNotEqual(result.source_digest_before, result.source_digest_after)

    def test_output_is_bounded_and_common_secret_values_are_redacted(self) -> None:
        runner = VerificationRunner({
            "check": (sys.executable, "-c", "print('OPENAI_API_KEY=super-secret-value ' + 'x'*5000)")
        }, workspace_resolver=self.resolve, output_limit_chars=80)
        result = self.run_with(runner)
        self.assertLessEqual(len(result.stdout_excerpt), 80)
        self.assertNotIn("super-secret-value", result.stdout_excerpt)
        self.assertEqual(result.stdout_excerpt, "[OUTPUT REDACTED]")

    def test_structured_credentials_and_url_passwords_are_redacted_from_both_streams(self) -> None:
        program = (
            "import sys; "
            "print('{\"access_token\": \"structured-token-value with-spaces\"}') ; "
            "print('DATABASE_URL=postgres://alice:db-password-value@db.example/app', file=sys.stderr)"
        )
        runner = VerificationRunner({"check": (sys.executable, "-c", program)}, workspace_resolver=self.resolve)
        result = self.run_with(runner)
        combined = result.stdout_excerpt + result.stderr_excerpt
        self.assertNotIn("structured-token-value", combined)
        self.assertNotIn("db-password-value", combined)
        self.assertEqual(combined.count("[OUTPUT REDACTED]"), 2)

    def test_untrusted_output_is_withheld_even_for_unknown_secrets_and_path_variants(self) -> None:
        program = (
            "import os,sys,json; root=os.getcwd(); "
            "print('password=\"alpha beta gamma\"'); "
            "print('unlabeled-secret 123456789'); "
            "print(root); print(root.replace('\\\\','/')); "
            "print(json.dumps(root), file=sys.stderr); "
            "print(\"credential='delta epsilon zeta'\", file=sys.stderr)"
        )
        runner = VerificationRunner({"check": (sys.executable, "-c", program)}, workspace_resolver=self.resolve)
        result = self.run_with(runner)
        self.assertEqual(result.stdout_excerpt, "[OUTPUT REDACTED]")
        self.assertEqual(result.stderr_excerpt, "[OUTPUT REDACTED]")

    def test_candidate_source_digest_change_invalidates_success_evidence(self) -> None:
        runner = VerificationRunner({
            "check": (sys.executable, "-c", "from pathlib import Path; Path('candidate.py').write_text('VALUE = 2')")
        }, workspace_resolver=self.resolve)
        result = self.run_with(runner)
        self.assertNotEqual(result.candidate_digest_before, result.candidate_digest_after)
        self.assertEqual(result.outcome, "unknown")
        self.assertEqual(result.error_code, "source_changed")
        self.assertEqual(result.evidence_scope, "declared_source_paths_and_windows_job_tree")
        self.assertEqual(result.process_tree_state, "unknown")
        self.assertEqual(result.job_tree_state, "stopped")
        self.assertTrue(result.requires_reconciliation)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object behavior")
    def test_timeout_terminates_real_descendant_after_direct_child_exit(self) -> None:
        ready = self.root / "orphan-ready"
        probe = self.root / "orphan-probe"
        alive = self.root / "orphan-alive"
        child = (
            "from pathlib import Path; import time\n"
            f"ready=Path({str(ready)!r}); probe=Path({str(probe)!r}); alive=Path({str(alive)!r})\n"
            "ready.write_text('ready')\n"
            "while True:\n"
            " if probe.exists(): alive.write_text('alive')\n"
            " time.sleep(.01)\n"
        )
        parent = (
            "import subprocess, sys\n"
            f"subprocess.Popen([sys.executable, '-c', {child!r}], "
            "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        )
        runner = VerificationRunner({"check": (sys.executable, "-c", parent)}, workspace_resolver=self.resolve)
        try:
            result = self.run_with(runner, timeout_seconds=0.5)
            if result.error_code == "process_start_failed":
                self.assertEqual(result.process_tree_state, "not_started")
                return
            self.assertEqual((result.outcome, result.error_code), ("unknown", "timeout"))
            self.assertEqual(result.process_tree_state, "unknown")
            self.assertEqual(result.job_tree_state, "stopped")
            self.assertTrue(result.requires_reconciliation)
            probe.write_text("probe", encoding="utf-8")
            self.assertFalse(self.wait_for(alive, 0.5), "descendant survived Job termination")
        finally:
            probe.write_text("probe", encoding="utf-8")

    @staticmethod
    def wait_for(path: Path, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if path.exists():
                return True
            time.sleep(0.02)
        return path.exists()

    def run_with(self, runner, **kwargs):
        options = {
            "task_id": "task-42",
            "command_id": "check",
            "timeout_seconds": 2.0,
        }
        options.update(kwargs)
        return runner.run(**options)


if __name__ == "__main__":
    unittest.main()
