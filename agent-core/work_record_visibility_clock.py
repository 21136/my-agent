"""Trusted elapsed-time samples for result-view ACKs and visibility projection.

Windows uses GetTickCount64 (includes sleep/hibernation) plus the kernel boot
identifier. Unsupported APIs/platforms return None so callers retain results.
"""
from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import time


@dataclass(frozen=True)
class ElapsedClockSample:
    boot_id: str
    monotonic_ns: int
    wall_utc: str


def sample_elapsed_clock() -> ElapsedClockSample | None:
    try:
        if os.name == "nt":
            class _BootInfo(ctypes.Structure):
                _fields_ = [("boot_identifier", ctypes.c_ubyte * 16),
                            ("firmware_type", ctypes.c_uint32),
                            ("boot_flags", ctypes.c_uint64)]
            info = _BootInfo()
            returned = ctypes.c_ulong()
            ntdll = ctypes.WinDLL("ntdll")
            query = ntdll.NtQuerySystemInformation
            query.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong,
                              ctypes.POINTER(ctypes.c_ulong))
            query.restype = ctypes.c_long
            if query(90, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned)) != 0:
                return None
            boot_id = bytes(info.boot_identifier).hex()
            kernel32 = ctypes.WinDLL("kernel32")
            get_tick_count = kernel32.GetTickCount64
            get_tick_count.argtypes = ()
            get_tick_count.restype = ctypes.c_ulonglong
            monotonic_ns = int(get_tick_count()) * 1_000_000
        elif os.name == "posix" and hasattr(os, "CLOCK_BOOTTIME"):
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
            monotonic_ns = time.clock_gettime_ns(os.CLOCK_BOOTTIME)
        else:
            return None
        if not boot_id or type(monotonic_ns) is not int or monotonic_ns < 0:
            return None
        wall_utc = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        return ElapsedClockSample(boot_id, monotonic_ns, wall_utc)
    except Exception:
        return None
