"""Persistent PTY sessions for LLM-facing interactive terminal calls.

The evolved-tool process is short-lived, so each session has a small worker
process. Requests are exchanged through an inbox/outbox directory and output
is retained in a bounded append-only file. This keeps attach/resume possible
without keeping the tool invocation alive.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_MAX_SESSIONS = 8
_MAX_OUTPUT_BYTES = 256 * 1024
_DEFAULT_IDLE_TIMEOUT = 600
_MAX_IDLE_TIMEOUT = 1800
_DEFAULT_LIFETIME_TIMEOUT = 1800
_MAX_LIFETIME_TIMEOUT = 3600
# Past max idle/lifetime: a starting/running row with this heartbeat is a zombie.
_STALE_ACTIVE_SEC = 3600
_REQUEST_TIMEOUT_SEC = 5
_READ_MAX_CHARS = 64 * 1024
_MAX_INPUT_CHARS = 64 * 1024
_SESSION_RE = re.compile(r"^it-[0-9a-f]{16}$")
_ENV_DENY_EXACT = frozenset(
    {
        "LLM_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SESSION_TOKEN",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
    }
)
_ENV_DENY_SUFFIX = re.compile(r"(?i).*(?:_SECRET|_TOKEN|_PASSWORD|_PASSWD|_API_KEY)$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _agent_root() -> Path:
    current = Path(__file__).resolve()
    for directory in (current.parent, *current.parents):
        evolve = directory / "evolve"
        if (evolve / "_index.core.toml").is_file() or (evolve / "_index.toml").is_file():
            return directory
    raise RuntimeError("could not locate agent root")


def _load_paths():
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from paths import AgentPaths, PathOutOfBoundsError

    return AgentPaths, PathOutOfBoundsError


def _sessions_root(paths) -> Path:
    root = paths.data / "interactive-terminals"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _session_id(raw: Any) -> str | None:
    if not isinstance(raw, str) or not _SESSION_RE.fullmatch(raw.strip()):
        return None
    return raw.strip()


def _session_dir(paths, session_id: str) -> Path:
    candidate = (_sessions_root(paths) / session_id).resolve()
    if candidate.parent != _sessions_root(paths).resolve():
        raise ValueError("invalid session_id")
    return candidate


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        os.replace(temporary, path)
    except PermissionError:
        # Windows readers can briefly keep the destination open while another
        # process is replacing it.  Do not turn a normal status race into a
        # corrupt session; the lock below serializes writers and this retry
        # covers readers that were already in flight before they acquired it.
        if os.name != "nt":
            raise
        for _ in range(20):
            time.sleep(0.025)
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                continue
        else:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise


def _read_json(path: Path) -> dict[str, Any] | None:
    for attempt in range(4):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            break
        except (OSError, json.JSONDecodeError):
            if attempt == 3:
                return None
            time.sleep(0.01)
    return payload if isinstance(payload, dict) else None


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """Cross-process lock for request creation and state inspection."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _parse_iso_timestamp(value: Any) -> float | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError, OSError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _active_heartbeat_is_stale(state: dict[str, Any], *, now: float | None = None) -> bool:
    now_ts = time.time() if now is None else now
    stamp = _parse_iso_timestamp(state.get("last_activity_at") or state.get("created_at"))
    if stamp is None:
        return False
    return (now_ts - stamp) >= _STALE_ACTIVE_SEC


def _pid_alive(pid: Any) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    if os.name == "nt":
        # ``os.kill(pid, 0)`` is not a liveness probe on Windows: depending
        # on the process state it can return WinError 87 or succeed without
        # proving that the process is still running.  Query the native exit
        # code instead so cross-process status refreshes do not orphan live
        # PTY workers.
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL

            process_query_limited_information = 0x1000
            synchronize = 0x00100000
            handle = kernel32.OpenProcess(
                process_query_limited_information | synchronize,
                False,
                value,
            )
            if not handle:
                return False
            try:
                exit_code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except (AttributeError, OSError, TypeError, ValueError):
            return False
    try:
        os.kill(value, 0)
    except PermissionError:
        return True
    except (ProcessLookupError, OSError):
        return False
    return True


def _terminate_pid_tree(pid: Any, *, force: bool = True) -> dict[str, Any]:
    """Best-effort cleanup for a PTY child after its worker is gone."""
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid pid"}
    if value <= 0:
        return {"ok": False, "error": "invalid pid"}
    if os.name == "nt":
        args = ["taskkill", "/PID", str(value), "/T"]
        if force:
            args.append("/F")
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            return {"ok": completed.returncode == 0 or not _pid_alive(value), "exit_code": completed.returncode}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "error": str(exc)}
    try:
        os.killpg(value, signal.SIGKILL if force else signal.SIGTERM)
        return {"ok": True}
    except (ProcessLookupError, OSError):
        try:
            os.kill(value, signal.SIGKILL if force else signal.SIGTERM)
            return {"ok": True}
        except ProcessLookupError:
            return {"ok": True, "note": "already gone"}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}


def _load_session(paths, raw_id: Any) -> tuple[str | None, Path | None, dict[str, Any] | None, dict[str, Any] | None]:
    session_id = _session_id(raw_id)
    if not session_id:
        return None, None, None, {"ok": False, "error": "valid session_id is required"}
    directory = _session_dir(paths, session_id)
    state_path = directory / "state.json"
    state = _read_json(state_path)
    if state is None:
        return session_id, directory, None, {"ok": False, "error": "session not found or state is corrupt"}
    return session_id, directory, state, None


def _validate_env(raw: Any) -> tuple[dict[str, str] | None, str | None]:
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        return None, "env must be an object of string values"
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(value, str):
            return None, "env keys and values must be strings"
        name = key.strip()
        if name.upper() in _ENV_DENY_EXACT or _ENV_DENY_SUFFIX.match(name):
            return None, f"env key not allowed: {name}"
        result[name] = value
    return result, None


def _resolve_cwd(paths, path_arg: Any) -> Path:
    raw = path_arg if isinstance(path_arg, str) and path_arg.strip() else "."
    text = raw.strip().replace("\\", "/").lstrip("/")
    AgentPaths, PathOutOfBoundsError = _load_paths()
    try:
        cwd = paths.resolve_under_agent(text, must_exist=True)
    except PathOutOfBoundsError:
        raise
    except (FileNotFoundError, TypeError, ValueError) as exc:
        raise ValueError(f"working_dir not found or invalid: {raw}") from exc
    if not cwd.is_dir():
        raise ValueError(f"working_dir is not a directory: {raw}")
    return cwd


def _write_state(directory: Path, state: dict[str, Any]) -> None:
    with _file_lock(directory / "session.lock"):
        _atomic_write_json(directory / "state.json", state)


def _touch_activity(directory: Path, state: dict[str, Any]) -> None:
    state["last_activity_at"] = _utc_now()
    _write_state(directory, state)


def _new_session(paths, payload: dict[str, Any]) -> dict[str, Any]:
    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        return {"ok": False, "error": "start requires a non-empty command"}

    try:
        cwd = _resolve_cwd(paths, payload.get("working_dir", payload.get("cwd", ".")))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    env_extra, env_error = _validate_env(payload.get("env"))
    if env_error:
        return {"ok": False, "error": env_error}

    idle = _bounded_int(payload.get("idle_timeout_sec"), _DEFAULT_IDLE_TIMEOUT, 30, _MAX_IDLE_TIMEOUT)
    lifetime = _bounded_int(
        payload.get("lifetime_timeout_sec"),
        _DEFAULT_LIFETIME_TIMEOUT,
        60,
        _MAX_LIFETIME_TIMEOUT,
    )

    active = 0
    root = _sessions_root(paths)
    for state_path in root.glob("*/state.json"):
        state = _read_json(state_path)
        if state and state.get("state") in {"starting", "running"}:
            if _pid_alive(state.get("worker_pid")):
                active += 1
            else:
                _refresh_orphan(state_path.parent, state)
    if active >= _MAX_SESSIONS:
        return {"ok": False, "error": f"maximum active interactive sessions reached ({_MAX_SESSIONS})"}

    session_id = f"it-{uuid.uuid4().hex[:16]}"
    directory = root / session_id
    (directory / "requests").mkdir(parents=True)
    (directory / "responses").mkdir(parents=True)
    (directory / "output.log").write_bytes(b"")
    now = _utc_now()
    state: dict[str, Any] = {
        "session_id": session_id,
        "command": command.strip(),
        "cwd": paths.to_agent_relative(cwd) or ".",
        "cwd_absolute": str(cwd),
        "state": "starting",
        "alive": False,
        "exit_code": None,
        "signal": None,
        "reason": None,
        "created_at": now,
        "last_activity_at": now,
        "output_start": 0,
        "output_bytes": 0,
        "idle_timeout_sec": idle,
        "lifetime_timeout_sec": lifetime,
        "env_keys": sorted(env_extra or {}),
    }
    _write_state(directory, state)

    worker_env = None
    if env_extra:
        worker_env = {**os.environ, **env_extra}
    worker_cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", str(directory)]
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
        # The evolved-tool process may run inside a job object that is torn
        # down after the short-lived tool call returns. Let the worker survive
        # that boundary when the host permits breakaway children.
        creationflags |= getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    try:
        worker = subprocess.Popen(
            worker_cmd,
            cwd=str(_agent_root()),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env=worker_env,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
    except PermissionError as exc:
        # Some hosts disallow breakaway; retain the detached behavior there.
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        if os.name != "nt" or not breakaway or not (creationflags & breakaway):
            state["state"] = "failed"
            state["reason"] = f"worker start failed: {exc}"
            _write_state(directory, state)
            return {**_state_result(state), "session_id": session_id}
        creationflags &= ~breakaway
        try:
            worker = subprocess.Popen(
                worker_cmd,
                cwd=str(_agent_root()),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                env=worker_env,
                creationflags=creationflags,
                start_new_session=False,
            )
        except (OSError, ValueError) as exc:
            state["state"] = "failed"
            state["reason"] = f"worker start failed: {exc}"
            _write_state(directory, state)
            return {**_state_result(state), "session_id": session_id}
    except (OSError, ValueError) as exc:
        state["state"] = "failed"
        state["reason"] = f"worker start failed: {exc}"
        _write_state(directory, state)
        return {"ok": False, "state": state, "error": state["reason"]}

    # The worker can publish ``running`` before Popen returns. Merge the PID
    # into the latest state so the parent does not restore stale ``starting``.
    current_state = _read_json(directory / "state.json") or state
    current_state["worker_pid"] = int(worker.pid)
    _write_state(directory, current_state)
    state = current_state

    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        current = _read_json(directory / "state.json") or state
        if current.get("state") in {"running", "unsupported", "failed", "lost"}:
            state = current
            break
        time.sleep(0.05)
    result = _state_result(state)
    result["session_id"] = session_id
    if state.get("state") == "unsupported":
        result["ok"] = False
        result["error"] = state.get("reason") or "PTY backend is unavailable"
    return result


def _bounded_int(raw: Any, default: int, lower: int, upper: int) -> int:
    try:
        value = int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        value = default
    return max(lower, min(upper, value))


def _state_result(state: dict[str, Any]) -> dict[str, Any]:
    public = {
        key: state.get(key)
        for key in (
            "session_id",
            "state",
            "alive",
            "exit_code",
            "signal",
            "reason",
            "command",
            "cwd",
            "created_at",
            "last_activity_at",
            "output_start",
            "output_bytes",
            "idle_timeout_sec",
            "lifetime_timeout_sec",
        )
        if key in state
    }
    return {"ok": state.get("state") not in {"failed", "lost", "orphaned", "unsupported"}, "state": public}


def _submit_request(directory: Path, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    request_id = uuid.uuid4().hex
    request = {"request_id": request_id, "action": action, **payload}
    response_path = directory / "responses" / f"{request_id}.json"
    with _file_lock(directory / "session.lock"):
        # The lock gives concurrent callers a deterministic enqueue order;
        # keep the UUID in the payload so the response remains addressable.
        sequence_path = directory / "request-seq"
        try:
            sequence = int(sequence_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            sequence = 0
        sequence += 1
        sequence_path.write_text(str(sequence), encoding="ascii")
        request_name = f"{sequence:020d}-{request_id}.json"
        _atomic_write_json(directory / "requests" / request_name, request)
    deadline = time.monotonic() + _REQUEST_TIMEOUT_SEC
    while time.monotonic() < deadline:
        response = _read_json(response_path)
        if response is not None:
            try:
                response_path.unlink()
            except OSError:
                pass
            return response
        time.sleep(0.05)
    return {"ok": False, "error": "interactive session worker did not respond", "state": "orphaned"}


def _read_output(directory: Path, payload: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    cursor = _bounded_int(payload.get("cursor"), 0, 0, 2**63 - 1)
    max_chars = _bounded_int(payload.get("max_chars"), 16384, 1, _READ_MAX_CHARS)
    output_start = int(state.get("output_start") or 0)
    reset = cursor < output_start
    physical_cursor = max(0, cursor - output_start)
    try:
        raw = (directory / "output.log").read_bytes()
    except OSError as exc:
        return {"ok": False, "error": f"cannot read session output: {exc}"}
    data = raw[physical_cursor:]
    decoded = data.decode("utf-8", errors="replace")
    truncated = False
    if len(decoded) > max_chars:
        decoded = decoded[:max_chars]
        encoded = decoded.encode("utf-8", errors="replace")
        next_cursor = output_start + physical_cursor + len(encoded)
        truncated = True
    else:
        next_cursor = output_start + len(raw)
    result = _state_result(state)
    result.update(
        {
            "session_id": state.get("session_id"),
            "output": decoded,
            "cursor": cursor,
            "next_cursor": next_cursor,
            "cursor_reset": reset,
            "truncated": truncated,
        }
    )
    return result


def _refresh_orphan(directory: Path, state: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    if state.get("state") not in {"starting", "running"}:
        return state
    worker_pid = state.get("worker_pid")
    pid_alive = bool(worker_pid) and _pid_alive(worker_pid)
    stale = _active_heartbeat_is_stale(state, now=now)
    if pid_alive and not stale:
        return state

    if worker_pid and not pid_alive:
        cleanup = (
            _terminate_pid_tree(state.get("pid"), force=True)
            if state.get("pid")
            else {"ok": False, "error": "PTY pid missing"}
        )
        state["state"] = "lost"
        state["alive"] = False
        state["reason"] = "worker process is no longer alive"
        state["orphan_cleanup"] = cleanup
        _write_state(directory, state)
        return state

    if not stale:
        return state

    # Heartbeat is older than any legitimate idle/lifetime window. Do not
    # signal the recorded PID: after a restart it may have been reused.
    state["state"] = "lost" if worker_pid else "orphaned"
    state["alive"] = False
    state["reason"] = (
        "session heartbeat is stale; worker is no longer trusted"
        if worker_pid
        else "worker pid missing and session heartbeat is stale"
    )
    _write_state(directory, state)
    return state


def _existing_action(paths, payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip().lower()
    if action == "list":
        items: list[dict[str, Any]] = []
        for state_path in sorted(_sessions_root(paths).glob("*/state.json")):
            state = _read_json(state_path)
            if not state:
                continue
            state = _refresh_orphan(state_path.parent, state)
            item = _state_result(state)
            item["session_id"] = state.get("session_id")
            items.append(item)
        return {"ok": True, "action": "list", "sessions": items}

    session_id, directory, state, error = _load_session(paths, payload.get("session_id"))
    if error:
        return error
    assert session_id and directory and state
    state = _refresh_orphan(directory, state)
    if action == "status":
        result = _state_result(state)
        result["session_id"] = session_id
        return result
    if action == "read":
        return _read_output(directory, payload, state)
    if state.get("state") in {"lost", "orphaned", "failed", "unsupported", "closed", "exited"}:
        return {
            "ok": False,
            "session_id": session_id,
            "state": state.get("state"),
            "error": f"session is not active: {state.get('state')}",
        }
    if action == "input":
        text = payload.get("text")
        if not isinstance(text, str):
            return {"ok": False, "error": "input requires string text"}
        if not text:
            return {"ok": False, "error": "input text must be non-empty"}
        response = _submit_request(directory, "input", {"text": text})
    elif action == "signal":
        kind = str(payload.get("signal") or "ctrl_c").strip().lower()
        if kind not in {"ctrl_c", "eof", "terminate"}:
            return {"ok": False, "error": "signal must be ctrl_c, eof or terminate"}
        response = _submit_request(directory, "signal", {"signal": kind, "force": bool(payload.get("force", False))})
    elif action == "close":
        response = _submit_request(directory, "close", {"force": bool(payload.get("force", False))})
    else:
        return {"ok": False, "error": "unknown action (use start|input|read|signal|status|list|close)"}
    response.setdefault("session_id", session_id)
    return response


def interactive_terminal(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip().lower()
    if not action:
        return {"ok": False, "error": "action is required"}
    AgentPaths, _ = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())
    if action == "start":
        return _new_session(paths, payload)
    return _existing_action(paths, payload)


def _load_pty_process():
    try:
        from winpty import PtyProcess
    except ImportError as exc:
        raise RuntimeError(
            "unsupported: pywinpty is not installed; install the optional interactive-terminal dependency"
        ) from exc
    return PtyProcess


def _worker_response(directory: Path, request_id: str, payload: dict[str, Any]) -> None:
    _atomic_write_json(directory / "responses" / f"{request_id}.json", payload)


def _append_output(directory: Path, state: dict[str, Any], data: bytes) -> None:
    if not data:
        return
    output_path = directory / "output.log"
    with output_path.open("ab") as handle:
        handle.write(data)
    size = output_path.stat().st_size
    start = int(state.get("output_start") or 0)
    if size > _MAX_OUTPUT_BYTES:
        raw = output_path.read_bytes()[-_MAX_OUTPUT_BYTES:]
        output_path.write_bytes(raw)
        start += size - len(raw)
        size = len(raw)
    state["output_start"] = start
    state["output_bytes"] = size
    state["last_activity_at"] = _utc_now()
    _write_state(directory, state)


def _send_ctrl_c(pty: Any) -> None:
    sender = getattr(pty, "sendcontrol", None)
    if callable(sender):
        sender("c")
    else:
        pty.write("\x03")


def _spawn_pty(pty_cls: Any, command: str, cwd: str | None) -> Any:
    """Use the reliable Windows backend, while keeping older adapters compatible."""
    argv = _split_command(command)
    try:
        from winpty import Backend

        backend = getattr(Backend, "WinPTY", 1)
    except ImportError:
        backend = 1
    try:
        return pty_cls.spawn(argv, cwd=cwd, backend=backend)
    except TypeError:
        # Older pywinpty releases and the test adapter do not expose backend.
        return pty_cls.spawn(argv, cwd=cwd)


def _split_command(command: str) -> list[str]:
    """Parse a Windows command once so quoted arguments reach the PTY intact."""
    argv = shlex.split(command, posix=False)
    normalized: list[str] = []
    for item in argv:
        if len(item) >= 2 and item[0] == item[-1] and item[0] in {"'", '"'}:
            item = item[1:-1]
        normalized.append(item)
    if not normalized:
        raise ValueError("command must be non-empty")
    return normalized


def _terminate_pty(pty: Any, *, force: bool) -> None:
    terminator = getattr(pty, "terminate", None)
    if callable(terminator):
        try:
            terminator(force=force)
        except TypeError:
            terminator()
        return
    closer = getattr(pty, "close", None)
    if callable(closer):
        closer()


def _read_pty(pty: Any, size: int = 4096) -> str:
    """Read available PTY output without blocking the request loop."""
    reader = getattr(pty, "read")
    try:
        # Use a non-blocking timeout when a compatible backend exposes it. The
        # reader thread below still isolates older blocking implementations.
        return reader(size, timeout=0)
    except TypeError:
        # Small fakes and older compatible adapters may only accept ``size``.
        return reader(size)


def _pty_reader_loop(pty: Any, output_queue: queue.Queue[str], stop_event: threading.Event) -> None:
    """Move potentially blocking PTY reads off the request-processing loop."""
    while not stop_event.is_set():
        try:
            data = _read_pty(pty)
        except (EOFError, OSError):
            return
        if data:
            output_queue.put(str(data))
            continue
        try:
            alive = bool(getattr(pty, "isalive", lambda: False)())
        except Exception:
            alive = False
        if not alive:
            return
        stop_event.wait(0.05)


def _drain_output_queue(directory: Path, state: dict[str, Any], output_queue: queue.Queue[str]) -> None:
    while True:
        try:
            data = output_queue.get_nowait()
        except queue.Empty:
            return
        if data:
            _append_output(directory, state, data.encode("utf-8", errors="replace"))


def _worker_main(directory: Path, pty_cls: Any | None = None) -> int:
    state = _read_json(directory / "state.json")
    if state is None:
        return 2
    try:
        pty_cls = pty_cls or _load_pty_process()
        pty = _spawn_pty(pty_cls, state["command"], state.get("cwd_absolute"))
    except RuntimeError as exc:
        state.update({"state": "unsupported", "alive": False, "reason": str(exc)})
        _write_state(directory, state)
        return 0
    except Exception as exc:
        state.update({"state": "failed", "alive": False, "reason": f"PTY start failed: {exc}"})
        _write_state(directory, state)
        return 1

    state.update({"state": "running", "alive": True, "pid": getattr(pty, "pid", None)})
    _write_state(directory, state)
    deadline = time.monotonic() + int(state.get("lifetime_timeout_sec") or _DEFAULT_LIFETIME_TIMEOUT)
    close_requested = False
    output_queue: queue.Queue[str] = queue.Queue()
    reader_stop = threading.Event()
    reader = threading.Thread(
        target=_pty_reader_loop,
        args=(pty, output_queue, reader_stop),
        name="interactive-terminal-reader",
        daemon=True,
    )
    reader.start()

    while True:
        current = _read_json(directory / "state.json") or state
        request_files = sorted((directory / "requests").glob("*.json"))
        for request_path in request_files:
            request = _read_json(request_path)
            try:
                request_path.unlink()
            except OSError:
                pass
            if not request:
                continue
            request_id = str(request.get("request_id") or "")
            action = str(request.get("action") or "")
            try:
                if action == "input":
                    text = request.get("text")
                    if not isinstance(text, str) or not text:
                        raise ValueError("input text must be non-empty")
                    if len(text) > _MAX_INPUT_CHARS:
                        raise ValueError(f"input text exceeds {_MAX_INPUT_CHARS} characters")
                    if os.name == "nt":
                        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
                    pty.write(text)
                    current["last_activity_at"] = _utc_now()
                    current["last_action"] = "input"
                    _write_state(directory, current)
                    _worker_response(directory, request_id, {"ok": True, "action": "input", "state": _state_result(current)["state"]})
                elif action == "signal":
                    kind = request.get("signal", "ctrl_c")
                    if kind == "ctrl_c":
                        _send_ctrl_c(pty)
                        current["signal"] = "ctrl_c"
                    elif kind == "eof":
                        # PTY has no portable close-stdin operation. Ctrl-D is
                        # the stream EOF convention; the target shell decides
                        # when that completes its current input operation.
                        pty.write("\x04")
                        current["signal"] = "eof"
                    elif kind == "terminate":
                        _terminate_pty(pty, force=bool(request.get("force", False)))
                        current["signal"] = "terminate"
                        close_requested = True
                    else:
                        raise ValueError("unknown signal")
                    current["last_activity_at"] = _utc_now()
                    _write_state(directory, current)
                    _worker_response(directory, request_id, {"ok": True, "action": "signal", "signal": kind})
                elif action == "close":
                    _terminate_pty(pty, force=bool(request.get("force", False)))
                    close_requested = True
                    current["last_activity_at"] = _utc_now()
                    _write_state(directory, current)
                    _worker_response(directory, request_id, {"ok": True, "action": "close", "state": "closing"})
                else:
                    _worker_response(directory, request_id, {"ok": False, "error": f"unknown worker action: {action}"})
            except Exception as exc:
                _worker_response(directory, request_id, {"ok": False, "error": str(exc)})

        if time.monotonic() >= deadline:
            _terminate_pty(pty, force=True)
            current["reason"] = "lifetime timeout"
            close_requested = True

        last_activity_text = current.get("last_activity_at") or state.get("created_at")
        try:
            last_activity = datetime.fromisoformat(str(last_activity_text)).timestamp()
            idle_expired = time.time() - last_activity >= int(current.get("idle_timeout_sec") or _DEFAULT_IDLE_TIMEOUT)
        except (TypeError, ValueError, OSError):
            idle_expired = False
        if idle_expired:
            _terminate_pty(pty, force=True)
            current["reason"] = "idle timeout"
            close_requested = True

        alive = bool(getattr(pty, "isalive", lambda: False)())
        if not alive:
            # Allow a reader that observed process exit to publish its final
            # chunk before the state becomes terminal.
            reader.join(timeout=0.2)
            reader_stop.set()
            reader.join(timeout=0.05)
            _drain_output_queue(directory, current, output_queue)
            exit_code = getattr(pty, "exitstatus", None)
            current.update(
                {
                    "state": "closed" if close_requested else "exited",
                    "alive": False,
                    "exit_code": exit_code,
                    "pid": getattr(pty, "pid", None),
                    "last_activity_at": _utc_now(),
                }
            )
            _write_state(directory, current)
            break

        data_seen = not output_queue.empty()
        _drain_output_queue(directory, current, output_queue)
        if not data_seen:
            time.sleep(0.05)

    reader_stop.set()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", metavar="SESSION_DIR")
    args = parser.parse_args()
    if args.worker:
        raise SystemExit(_worker_main(Path(args.worker).resolve()))

    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main

    run_tool_main(interactive_terminal)


if __name__ == "__main__":
    main()
