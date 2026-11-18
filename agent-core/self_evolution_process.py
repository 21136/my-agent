"""Windows Job Object process-tree control for trusted host checks.

This is containment for ordinary Win32 process creation, not a security sandbox:
OS brokers such as WMI can create processes outside a caller's Job Object.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import threading
import time
from ctypes import wintypes
from typing import Mapping, Sequence


class WindowsJobUnavailable(OSError):
    """The process could not be created/contained; uncertain cleanup is explicit."""

    def __init__(self, message: str, *, process_started: bool = False,
                 cleanup_confirmed: bool = False) -> None:
        super().__init__(message)
        self.process_started = process_started
        self.cleanup_confirmed = cleanup_confirmed
        self.started_uncertain = process_started and not cleanup_confirmed


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _BasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", wintypes.LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", wintypes.LPVOID)]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class WindowsJobProcess:
    """Popen-shaped owner of one dedicated, non-breakaway Windows Job Object."""

    def __init__(self, kernel, job, process, stdout, stderr) -> None:
        self._kernel = kernel
        self._job = job
        self._process = process
        self.stdout = stdout
        self.stderr = stderr
        self.returncode: int | None = None
        self.tree_stopped = False
        self._closed = False
        self._lock = threading.Lock()
        self._termination_handles: list[int] = []
        self._termination_snapshot_complete = False

    def poll(self) -> int | None:
        with self._lock:
            if self.returncode is not None:
                return self.returncode
            code = wintypes.DWORD()
            if not self._kernel.GetExitCodeProcess(self._process, ctypes.byref(code)):
                raise _last_error("GetExitCodeProcess")
            if code.value != 259:  # STILL_ACTIVE
                self.returncode = int(code.value)
            return self.returncode

    def active_processes(self) -> int:
        info = _BasicAccountingInformation()
        returned = wintypes.DWORD()
        if not self._kernel.QueryInformationJobObject(
            self._job, 1, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned)
        ):
            raise _last_error("QueryInformationJobObject")
        if returned.value < ctypes.sizeof(info) or info.ActiveProcesses < 0:
            raise OSError("incomplete Job Object accounting information")
        return int(info.ActiveProcesses)

    def terminate_job(self) -> None:
        # The job accounting count can reach zero after an individual process
        # handle is signaled. Capture existing members after initiating stop;
        # verify membership on the opened handle to avoid PID-reuse mistakes.
        self._capture_termination_handles()
        if not self._kernel.TerminateJobObject(self._job, 0xC000013A):
            raise _last_error("TerminateJobObject")

    def _capture_termination_handles(self) -> None:
        self._termination_snapshot_complete = False
        for handle in self._termination_handles:
            self._kernel.CloseHandle(handle)
        self._termination_handles.clear()
        capacity = 32
        while capacity <= 4096:
            class _PidList(ctypes.Structure):
                _fields_ = [("assigned", wintypes.DWORD), ("count", wintypes.DWORD),
                            ("pids", ctypes.c_size_t * capacity)]
            info = _PidList()
            returned = wintypes.DWORD()
            ok = self._kernel.QueryInformationJobObject(
                self._job, 3, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned))
            if not ok:
                # ERROR_MORE_DATA is expected if the list grew while querying.
                if ctypes.get_last_error() == 234:
                    capacity *= 2
                    continue
                return
            if info.assigned > capacity or info.count > capacity or info.count != info.assigned:
                capacity *= 2
                continue
            complete = True
            for pid in info.pids[:info.count]:
                handle = self._kernel.OpenProcess(0x00101000, False, pid)
                if not handle:
                    complete = False
                    continue
                member = wintypes.BOOL()
                if not self._kernel.IsProcessInJob(handle, self._job, ctypes.byref(member)) or not member.value:
                    self._kernel.CloseHandle(handle)
                    complete = False
                    continue
                self._termination_handles.append(handle)
            self._termination_snapshot_complete = complete
            return

    def wait_tree_stopped(self, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        handles_confirmed = self._termination_snapshot_complete
        for handle in self._termination_handles:
            millis = max(0, min(0xFFFFFFFE, int((deadline - time.monotonic()) * 1000)))
            if self._kernel.WaitForSingleObject(handle, millis) != 0:
                handles_confirmed = False
        for handle in self._termination_handles:
            self._kernel.CloseHandle(handle)
        self._termination_handles.clear()
        if not handles_confirmed:
            return False
        while True:
            if self.active_processes() == 0:
                self.mark_tree_stopped()
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)

    def mark_tree_stopped(self) -> None:
        self.tree_stopped = self.active_processes() == 0

    def wait(self, timeout: float | None = None) -> int:
        millis = 0xFFFFFFFF if timeout is None else max(0, min(0xFFFFFFFE, int(timeout * 1000)))
        result = self._kernel.WaitForSingleObject(self._process, millis)
        if result == 0x00000102:
            raise subprocess.TimeoutExpired("<windows-job-process>", timeout)
        if result != 0:
            raise _last_error("WaitForSingleObject(process)")
        code = self.poll()
        if code is None:
            raise OSError("process signaled without an exit code")
        return code

    def close(self) -> None:
        if self._closed:
            return
        # Closing the last private Job handle is a final safety net. The caller
        # only reports stopped after observing ActiveProcesses == 0.
        for handle in self._termination_handles:
            self._kernel.CloseHandle(handle)
        self._termination_handles.clear()
        if self._job:
            self._kernel.CloseHandle(self._job)
            self._job = None
        if self._process:
            self._kernel.CloseHandle(self._process)
            self._process = None
        for stream in (self.stdout, self.stderr):
            try:
                stream.close()
            except OSError:
                pass
        self._closed = True

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def start_windows_job_process(
    argv: Sequence[str], *, cwd: str, env: Mapping[str, str],
) -> WindowsJobProcess:
    """Atomically assign at CreateProcess time via PROC_THREAD_ATTRIBUTE_JOB_LIST."""
    if os.name != "nt":
        raise WindowsJobUnavailable("Windows Job Objects are unavailable")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _declare_apis(kernel)
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise _last_error("CreateJobObjectW")
    process_info = _ProcessInformation()
    handles: list[int] = []
    attribute_buffer = None
    initialized_attributes = False
    assigned = False
    try:
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE only; no breakaway flags.
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise _last_error("SetInformationJobObject")

        security = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), None, True)
        stdout_read, stdout_write = _create_pipe(kernel, security)
        handles.extend([stdout_read, stdout_write])
        stderr_read, stderr_write = _create_pipe(kernel, security)
        handles.extend([stderr_read, stderr_write])
        null_input = kernel.CreateFileW("NUL", 0x80000000, 3, ctypes.byref(security), 3, 0x00000080, None)
        if null_input == wintypes.HANDLE(-1).value:
            raise _last_error("CreateFileW(NUL)")
        handles.append(null_input)
        for read_handle in (stdout_read, stderr_read):
            if not kernel.SetHandleInformation(read_handle, 1, 0):
                raise _last_error("SetHandleInformation")

        inherit = (wintypes.HANDLE * 3)(null_input, stdout_write, stderr_write)
        job_list = (wintypes.HANDLE * 1)(job)
        size = ctypes.c_size_t()
        kernel.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
        if size.value == 0:
            raise _last_error("InitializeProcThreadAttributeList(size)")
        attribute_buffer = ctypes.create_string_buffer(size.value)
        attr_ptr = ctypes.cast(attribute_buffer, wintypes.LPVOID)
        if not kernel.InitializeProcThreadAttributeList(attr_ptr, 2, 0, ctypes.byref(size)):
            raise _last_error("InitializeProcThreadAttributeList")
        initialized_attributes = True
        if not kernel.UpdateProcThreadAttribute(
            attr_ptr, 0, 0x00020002, ctypes.cast(inherit, wintypes.LPVOID),
            ctypes.sizeof(inherit), None, None,
        ):
            raise _last_error("UpdateProcThreadAttribute(handle list)")

        # JOB_LIST is applied by CreateProcessW after the new
        # process can execute. If nested membership is unsupported/invalid,
        # CreateProcess fails and no uncontained child is started.
        if not kernel.UpdateProcThreadAttribute(
            attr_ptr, 0, 0x0002000D, ctypes.cast(job_list, wintypes.LPVOID),
            ctypes.sizeof(job_list), None, None,
        ):
            raise _last_error("UpdateProcThreadAttribute(job list)")

        startup = _StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(_StartupInfoEx)
        startup.StartupInfo.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput = null_input
        startup.StartupInfo.hStdOutput = stdout_write
        startup.StartupInfo.hStdError = stderr_write
        startup.lpAttributeList = attr_ptr
        mutable_command = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(argv)))
        env_items = [f"{key}={value}" for key, value in sorted(env.items(), key=lambda kv: kv[0].casefold())]
        environment = ctypes.create_unicode_buffer("\0".join(env_items) + "\0\0")
        flags = 0x00000400 | 0x00080000 | 0x08000000  # unicode env, startup EX, no window
        if not kernel.CreateProcessW(
            argv[0], mutable_command, None, None, True, flags, environment, cwd,
            ctypes.byref(startup), ctypes.byref(process_info),
        ):
            raise _last_error("CreateProcessW")
        assigned = True
        kernel.CloseHandle(stdout_write)
        kernel.CloseHandle(stderr_write)
        kernel.CloseHandle(null_input)
        handles.remove(stdout_write)
        handles.remove(stderr_write)
        handles.remove(null_input)

        # Successful CreateProcess proves membership was established after
        # any child instruction ran. A private job handle is never inherited.
        kernel.CloseHandle(process_info.hThread)
        process_info.hThread = None

        import msvcrt
        stdout_fd = msvcrt.open_osfhandle(int(stdout_read), os.O_RDONLY | os.O_BINARY)
        handles.remove(stdout_read)
        try:
            stderr_fd = msvcrt.open_osfhandle(int(stderr_read), os.O_RDONLY | os.O_BINARY)
        except BaseException:
            os.close(stdout_fd)
            raise
        handles.remove(stderr_read)
        try:
            stdout = os.fdopen(stdout_fd, "rb", buffering=0)
        except BaseException:
            os.close(stdout_fd)
            os.close(stderr_fd)
            raise
        try:
            stderr = os.fdopen(stderr_fd, "rb", buffering=0)
        except BaseException:
            stdout.close()
            os.close(stderr_fd)
            raise
        owner = WindowsJobProcess(kernel, job, process_info.hProcess, stdout, stderr)
        job = None
        process_info.hProcess = None
        return owner
    except BaseException as original_error:
        # Only this private Job and its just-created process are touched.
        cleanup_confirmed = not bool(process_info.hProcess)
        if process_info.hProcess:
            if assigned:
                terminated = bool(kernel.TerminateJobObject(job, 0xC000013A))
                if terminated:
                    deadline = time.monotonic() + 2.0
                    try:
                        while time.monotonic() < deadline:
                            info = _BasicAccountingInformation()
                            returned = wintypes.DWORD()
                            if not kernel.QueryInformationJobObject(job, 1, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned)):
                                break
                            if returned.value >= ctypes.sizeof(info) and info.ActiveProcesses == 0:
                                cleanup_confirmed = True
                                break
                            time.sleep(0.01)
                    except Exception:
                        cleanup_confirmed = False
            else:
                terminated = bool(kernel.TerminateProcess(process_info.hProcess, 0xC000013A))
                cleanup_confirmed = terminated and kernel.WaitForSingleObject(process_info.hProcess, 2000) == 0
            kernel.CloseHandle(process_info.hProcess)
        if process_info.hThread:
            kernel.CloseHandle(process_info.hThread)
        if job:
            kernel.CloseHandle(job)  # KILL_ON_JOB_CLOSE applies only to this private job.
        for handle in handles:
            if handle:
                kernel.CloseHandle(handle)
        if process_info.dwProcessId:
            raise WindowsJobUnavailable(
                "process setup failed; termination "
                + ("confirmed" if cleanup_confirmed else "unconfirmed"),
                process_started=True, cleanup_confirmed=cleanup_confirmed,
            ) from original_error
        raise
    finally:
        if initialized_attributes:
            kernel.DeleteProcThreadAttributeList(attr_ptr)


def _create_pipe(kernel, security):
    read_handle = wintypes.HANDLE()
    write_handle = wintypes.HANDLE()
    if not kernel.CreatePipe(ctypes.byref(read_handle), ctypes.byref(write_handle), ctypes.byref(security), 0):
        raise _last_error("CreatePipe")
    return read_handle.value, write_handle.value


def _declare_apis(kernel) -> None:
    kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    kernel.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.LPVOID, wintypes.LPVOID, wintypes.BOOL, wintypes.DWORD, wintypes.LPVOID, wintypes.LPCWSTR, wintypes.LPVOID, ctypes.POINTER(_ProcessInformation)]
    kernel.CreateProcessW.restype = wintypes.BOOL
    kernel.CreatePipe.argtypes = [ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(_SecurityAttributes), wintypes.DWORD]
    kernel.CreatePipe.restype = wintypes.BOOL
    kernel.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    kernel.SetHandleInformation.restype = wintypes.BOOL
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_SecurityAttributes), wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.InitializeProcThreadAttributeList.argtypes = [wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
    kernel.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel.UpdateProcThreadAttribute.argtypes = [wintypes.LPVOID, wintypes.DWORD, ctypes.c_size_t, wintypes.LPVOID, ctypes.c_size_t, wintypes.LPVOID, ctypes.POINTER(ctypes.c_size_t)]
    kernel.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel.DeleteProcThreadAttributeList.argtypes = [wintypes.LPVOID]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL


def _last_error(api: str) -> WindowsJobUnavailable:
    error = ctypes.get_last_error()
    return WindowsJobUnavailable(f"{api} failed (winerror={error})")
