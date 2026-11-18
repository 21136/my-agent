from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_evolution_process import WindowsJobUnavailable, start_windows_job_process


@unittest.skipUnless(os.name == "nt", "Windows Job Object behavior")
class WindowsJobProcessTests(unittest.TestCase):
    def test_wmi_brokered_process_escapes_private_job_and_requires_explicit_revoke(self) -> None:
        """Real WMI process creation is outside this Job's containment boundary."""
        import base64
        import ctypes
        from ctypes import wintypes

        import self_evolution_process as job_module

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        job_module._declare_apis(kernel)
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateProcess.restype = wintypes.BOOL
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ready = root / "brokered-pid.txt"
            payload = root / "brokered.ps1"
            ready_literal = "'" + str(ready).replace("'", "''") + "'"
            payload.write_text(
                f"[IO.File]::WriteAllText({ready_literal}, [string]$PID)\n"
                "Start-Sleep -Seconds 180\n",
                encoding="utf-8",
            )
            powershell = str(Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
            brokered_command = subprocess.list2cmdline([
                powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(payload),
            ])
            ps_literal = "'" + brokered_command.replace("'", "''") + "'"
            result_path = str(root / "wmi-result.txt")
            ps_result_literal = "'" + result_path.replace("'", "''") + "'"
            create = (
                f"$r=Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
                f"-Arguments @{{CommandLine={ps_literal}}}; "
                "if ($r.ReturnValue -ne 0) { exit 41 }; "
                f"[IO.File]::WriteAllText({ps_result_literal}, [string]$r.ProcessId)"
            )
            encoded = base64.b64encode(create.encode("utf-16le")).decode("ascii")
            proc = start_windows_job_process(
                [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                cwd=temp, env=os.environ.copy(),
            )
            escaped_handle = None
            escaped_pid = None
            try:
                proc.wait(timeout=15)
                self.assertEqual(proc.returncode, 0, "WMI broker invocation failed")
                deadline = time.monotonic() + 10
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.025)
                self.assertTrue(ready.exists(), "WMI-created adversarial process did not start")
                escaped_pid = int(ready.read_text(encoding="utf-8"))
                escaped_handle = kernel.OpenProcess(0x00101001, False, escaped_pid)
                self.assertTrue(escaped_handle, "could not open broker-created process")
                member = wintypes.BOOL()
                self.assertTrue(kernel.IsProcessInJob(escaped_handle, proc._job, ctypes.byref(member)))
                self.assertFalse(member.value, "WMI-created process unexpectedly joined the private Job")

                proc.terminate_job()
                self.assertTrue(proc.wait_tree_stopped(5), "private Job members did not stop")
                self.assertEqual(kernel.WaitForSingleObject(escaped_handle, 250), 0x00000102,
                                 "broker-created process was unexpectedly terminated with the Job")
            finally:
                if not escaped_handle and ready.exists():
                    try:
                        escaped_pid = int(ready.read_text(encoding="utf-8"))
                        escaped_handle = kernel.OpenProcess(0x00101001, False, escaped_pid)
                    except (OSError, ValueError):
                        escaped_handle = None
                if escaped_handle:
                    # This explicit handle-based revoke belongs to the test's
                    # cleanup only; production verification has no such handle.
                    if kernel.WaitForSingleObject(escaped_handle, 0) == 0x00000102:
                        self.assertTrue(kernel.TerminateProcess(escaped_handle, 0xC000013A))
                        self.assertEqual(kernel.WaitForSingleObject(escaped_handle, 5000), 0)
                    kernel.CloseHandle(escaped_handle)
                try:
                    if proc.active_processes():
                        proc.terminate_job()
                        proc.wait_tree_stopped(5)
                finally:
                    proc.close()

    def test_unavailable_process_snapshot_never_claims_stopped(self) -> None:
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            proc = start_windows_job_process(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                cwd=temp, env=os.environ.copy(),
            )
            try:
                with patch.object(proc, "_capture_termination_handles", return_value=None):
                    proc.terminate_job()
                self.assertFalse(proc.wait_tree_stopped(2))
                self.assertFalse(proc.tree_stopped)
                proc.wait(timeout=3)
            finally:
                proc.close()
                proc.stdout.close()
                proc.stderr.close()

    def test_private_job_counts_and_terminates_descendants_without_touching_other_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ready = root / "ready"
            probe = root / "probe"
            alive = root / "alive"
            child = (
                "from pathlib import Path; import time\n"
                f"ready=Path({str(ready)!r}); probe=Path({str(probe)!r}); alive=Path({str(alive)!r})\n"
                "ready.write_text('ready')\n"
                "while True:\n"
                " if probe.exists(): alive.write_text('alive')\n"
                " time.sleep(.01)\n"
            )
            parent = (
                "import subprocess, sys, time\n"
                f"subprocess.Popen([sys.executable, '-c', {child!r}], "
                "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                "time.sleep(30)\n"
            )
            try:
                proc = start_windows_job_process(
                    [sys.executable, "-c", parent], cwd=temp, env=os.environ.copy(),
                )
            except WindowsJobUnavailable as exc:
                self.assertFalse(exc.started_uncertain)
                return  # Creation-time containment is unavailable; fail closed, never run uncontained.
            unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            try:
                deadline = time.monotonic() + 4
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(ready.exists(), "contained child failed to start")
                self.assertGreaterEqual(proc.active_processes(), 2)
                proc.terminate_job()
                self.assertTrue(proc.wait_tree_stopped(4), "Job active-process count did not reach zero")
                proc.mark_tree_stopped()
                self.assertIsNotNone(proc.wait(2))
                self.assertIsNone(unrelated.poll(), "terminating the private Job affected an unrelated process")
                probe.write_text("probe", encoding="utf-8")
                time.sleep(.2)
                self.assertFalse(alive.exists(), "descendant survived private Job termination")
            finally:
                try:
                    if proc.active_processes():
                        proc.terminate_job()
                        proc.wait_tree_stopped(3)
                except OSError:
                    pass
                proc.close()
                if unrelated.poll() is None:
                    unrelated.terminate()
                    unrelated.wait(timeout=3)
                self.assertTrue(proc.stdout.closed, "close() left the owned stdout pipe open")
                self.assertTrue(proc.stderr.closed, "close() left the owned stderr pipe open")


if __name__ == "__main__":
    unittest.main()
