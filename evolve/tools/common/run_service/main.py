"""run_service — manage long-lived background processes (dev servers, spring-boot:run).

State: data/services/<name>.json · logs: data/services/<name>.log
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MAX_LOG_CHARS = 48000
# Used by agent-core executor for action-gated confirm (status/logs/wait/list skip).
READ_ACTIONS = frozenset({"status", "logs", "wait", "list"})
MUTATING_ACTIONS = frozenset({"start", "stop", "restart"})


def _agent_root() -> Path:
    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        evolve_marker = directory / "evolve"
        if (evolve_marker / "_index.core.toml").is_file() or (evolve_marker / "_index.toml").is_file():
            return directory
    raise RuntimeError("could not locate agent root")


def _load_paths():
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from paths import AgentPaths

    return AgentPaths


def _services_dir(paths) -> Path:
    d = paths.data / "services"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_path(paths, name: str) -> Path:
    return _services_dir(paths) / f"{name}.json"


def _log_path(paths, name: str) -> Path:
    return _services_dir(paths) / f"{name}.log"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_name(name: str | None) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return "name is required"
    if not _NAME_RE.match(name.strip()):
        return "name must match /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/"
    return None


def _resolve_working_dir(paths, path_arg: str | None) -> Path:
    if not path_arg:
        return paths.agent_root
    text = path_arg.strip().replace("\\", "/").lstrip("/")
    try:
        return paths.resolve_under_agent(text, must_exist=True)
    except Exception:
        pass
    if not text.startswith("workspace/"):
        try:
            return paths.resolve_under_agent(f"workspace/{text}", must_exist=True)
        except Exception:
            pass
    return paths.agent_root / text


def _coalesce_working_dir(payload: dict[str, Any]) -> str:
    working = payload.get("working_dir", "")
    if isinstance(working, str) and working.strip():
        return working.strip()
    cwd = payload.get("cwd", "")
    if isinstance(cwd, str) and cwd.strip():
        return cwd.strip()
    return ""


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            # signal 0 is not supported; OpenProcess / Wait
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
            )
            if handle:
                exit_code = ctypes.c_ulong()
                ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                ctypes.windll.kernel32.CloseHandle(handle)
                if not ok:
                    return True
                # STILL_ACTIVE = 259
                return int(exit_code.value) == 259
            err = ctypes.windll.kernel32.GetLastError()
            # Access denied (5) often means process exists
            return err == 5
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def _kill_tree(pid: int, *, force: bool = True) -> dict[str, Any]:
    if pid <= 0:
        return {"ok": False, "error": "invalid pid"}
    if sys.platform == "win32":
        args = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            args.insert(1, "/F")
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            return {
                "ok": completed.returncode == 0 or not _pid_alive(pid),
                "exit_code": completed.returncode,
                "stdout": (completed.stdout or "")[:2000],
                "stderr": (completed.stderr or "")[:2000],
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    try:
        os.killpg(pid, signal.SIGTERM if not force else signal.SIGKILL)
        return {"ok": True}
    except ProcessLookupError:
        try:
            os.kill(pid, signal.SIGTERM if not force else signal.SIGKILL)
            return {"ok": True}
        except ProcessLookupError:
            return {"ok": True, "note": "already gone"}
    except Exception as exc:
        try:
            os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
            return {"ok": True, "fallback": "kill"}
        except Exception as exc2:
            return {"ok": False, "error": f"{exc}; {exc2}"}


def _load_state(paths, name: str) -> dict[str, Any] | None:
    path = _state_path(paths, name)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _save_state(paths, name: str, state: dict[str, Any]) -> None:
    path = _state_path(paths, name)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _refresh_alive(state: dict[str, Any]) -> dict[str, Any]:
    pid = int(state.get("pid") or 0)
    alive = _pid_alive(pid)
    state = dict(state)
    state["alive"] = alive
    if not alive and state.get("status") in {"running", "starting"}:
        state["status"] = "stopped"
    return state


def _resolve_log_file(paths, state: dict[str, Any] | None, name: str) -> Path:
    if state and state.get("log_path"):
        candidate = Path(str(state["log_path"]))
        if not candidate.is_absolute():
            candidate = paths.agent_root / candidate
        return candidate
    return _log_path(paths, name)


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _log_matches(log_file: Path, pattern: str) -> bool:
    if not pattern or not log_file.is_file():
        return False
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    try:
        return re.search(pattern, text, re.MULTILINE) is not None
    except re.error:
        return False


def _ready(state: dict[str, Any], paths, *, regex: str | None = None, port: int | None = None) -> bool:
    name = str(state.get("name") or "")
    log_file = _resolve_log_file(paths, state, name)
    use_regex = regex if regex is not None else str(state.get("ready_regex") or "")
    use_port = port if port is not None else state.get("ready_port")
    checks: list[bool] = []
    if use_regex:
        checks.append(_log_matches(log_file, use_regex))
    if use_port is not None and str(use_port).strip() != "":
        try:
            checks.append(_port_open(int(use_port)))
        except (TypeError, ValueError):
            checks.append(False)
    if not checks:
        # No ready criteria: process alive counts as ready enough for wait/start return
        return bool(state.get("alive"))
    return all(checks)


def _tail_log(log_file: Path, lines: int) -> str:
    if not log_file.is_file():
        return ""
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"(read error: {exc})"
    parts = text.splitlines()
    n = max(1, min(int(lines), 2000))
    out = "\n".join(parts[-n:])
    if len(out) > _MAX_LOG_CHARS:
        return out[-_MAX_LOG_CHARS:]
    return out


def _popen_detached(command: str | list[str], *, cwd: Path, log_file: Path) -> subprocess.Popen:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(log_file, "a", encoding="utf-8", errors="replace", buffering=1)
    log_handle.write(f"\n--- run_service start {_utc_now()} ---\n")
    log_handle.flush()

    kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW — keep stdout pipe to file
        create_new_process_group = 0x00000200
        create_no_window = 0x08000000
        kwargs["creationflags"] = create_new_process_group | create_no_window
        if isinstance(command, str):
            kwargs["shell"] = True
    else:
        kwargs["start_new_session"] = True
        if isinstance(command, str):
            kwargs["shell"] = True

    try:
        proc = subprocess.Popen(command, **kwargs)
    finally:
        # Child inherits the fd; parent should close its copy
        try:
            log_handle.close()
        except OSError:
            pass
    # Fire-and-forget: avoid ResourceWarning when abandoning a long-lived child.
    if proc.returncode is None:
        proc.returncode = 0  # type: ignore[misc]
    return proc


def _action_start(paths, payload: dict[str, Any]) -> dict[str, Any]:
    err = _validate_name(payload.get("name"))
    if err:
        return {"ok": False, "error": err}

    name = str(payload["name"]).strip()
    existing = _load_state(paths, name)
    if existing:
        refreshed = _refresh_alive(existing)
        if refreshed.get("alive"):
            return {
                "ok": False,
                "error": f"service '{name}' already running (pid={refreshed.get('pid')})",
                "state": refreshed,
            }

    command = payload.get("command")
    args = payload.get("args")
    if isinstance(command, str) and command.strip():
        cmd: str | list[str] = command.strip()
        cmd_display = command.strip()
    elif isinstance(args, list) and args and all(isinstance(a, str) for a in args):
        cmd = list(args)
        cmd_display = " ".join(args)
    else:
        return {"ok": False, "error": "start requires command (string) or args (string[])"}

    cwd = _resolve_working_dir(paths, _coalesce_working_dir(payload))
    if not cwd.is_dir():
        return {"ok": False, "error": f"working_dir does not exist: {cwd}"}

    ready_regex = str(payload.get("ready_regex") or "").strip()
    ready_port = payload.get("ready_port")
    if ready_port is not None and ready_port != "":
        try:
            ready_port = int(ready_port)
        except (TypeError, ValueError):
            return {"ok": False, "error": "ready_port must be an integer"}
    else:
        ready_port = None

    ready_timeout = payload.get("ready_timeout_sec", 90)
    try:
        ready_timeout = int(ready_timeout)
    except (TypeError, ValueError):
        ready_timeout = 90

    log_file = _log_path(paths, name)
    try:
        proc = _popen_detached(cmd, cwd=cwd, log_file=log_file)
    except OSError as exc:
        return {"ok": False, "error": f"failed to start: {exc}"}

    state: dict[str, Any] = {
        "name": name,
        "command": cmd_display,
        "cwd": str(cwd),
        "pid": int(proc.pid),
        "pgid": int(proc.pid),
        "started_at": _utc_now(),
        "log_path": str(log_file.relative_to(paths.agent_root)).replace("\\", "/"),
        "ready_regex": ready_regex,
        "ready_port": ready_port,
        "status": "starting",
        "alive": True,
    }
    _save_state(paths, name, state)

    if ready_timeout <= 0 and not ready_regex and ready_port is None:
        state["status"] = "running"
        _save_state(paths, name, state)
        return {"ok": True, "action": "start", "ready": False, "state": state}

    deadline = time.monotonic() + max(0, ready_timeout)
    ready = False
    while time.monotonic() < deadline:
        state = _refresh_alive(state)
        if not state.get("alive"):
            state["status"] = "failed"
            _save_state(paths, name, state)
            return {
                "ok": False,
                "error": "process exited before ready",
                "action": "start",
                "ready": False,
                "state": state,
                "logs_tail": _tail_log(log_file, 40),
            }
        if _ready(state, paths):
            ready = True
            break
        time.sleep(0.4)

    state = _refresh_alive(state)
    state["status"] = "running" if state.get("alive") else "failed"
    state["ready"] = ready
    _save_state(paths, name, state)
    out: dict[str, Any] = {
        "ok": bool(state.get("alive")),
        "action": "start",
        "ready": ready,
        "state": state,
    }
    if not ready:
        out["warning"] = "started but ready criteria not met within timeout"
        out["logs_tail"] = _tail_log(log_file, 40)
    return out


def _action_stop(paths, payload: dict[str, Any]) -> dict[str, Any]:
    err = _validate_name(payload.get("name"))
    if err:
        return {"ok": False, "error": err}
    name = str(payload["name"]).strip()
    state = _load_state(paths, name)
    if not state:
        return {"ok": False, "error": f"unknown service '{name}'"}
    state = _refresh_alive(state)
    force = payload.get("force", True)
    if isinstance(force, str):
        force = force.strip().lower() not in {"0", "false", "no"}
    pid = int(state.get("pid") or 0)
    kill_info: dict[str, Any] = {"ok": True, "note": "already stopped"}
    if state.get("alive"):
        kill_info = _kill_tree(pid, force=bool(force))
        # brief settle
        for _ in range(10):
            if not _pid_alive(pid):
                break
            time.sleep(0.2)
    state = _refresh_alive(state)
    state["status"] = "stopped"
    state["stopped_at"] = _utc_now()
    _save_state(paths, name, state)
    return {
        "ok": kill_info.get("ok", True) and not state.get("alive"),
        "action": "stop",
        "kill": kill_info,
        "state": state,
    }


def _action_status(paths, payload: dict[str, Any]) -> dict[str, Any]:
    name = payload.get("name")
    if not name:
        return _action_list(paths, payload)
    err = _validate_name(name if isinstance(name, str) else None)
    if err:
        return {"ok": False, "error": err}
    name = str(name).strip()
    state = _load_state(paths, name)
    if not state:
        return {"ok": True, "action": "status", "found": False, "name": name}
    state = _refresh_alive(state)
    state["ready"] = _ready(state, paths) if state.get("alive") else False
    _save_state(paths, name, state)
    return {"ok": True, "action": "status", "found": True, "state": state}


def _action_logs(paths, payload: dict[str, Any]) -> dict[str, Any]:
    err = _validate_name(payload.get("name"))
    if err:
        return {"ok": False, "error": err}
    name = str(payload["name"]).strip()
    state = _load_state(paths, name)
    log_file = _resolve_log_file(paths, state, name)
    tail_lines = payload.get("tail_lines", 80)
    try:
        tail_lines = int(tail_lines)
    except (TypeError, ValueError):
        tail_lines = 80
    try:
        rel = str(log_file.resolve().relative_to(paths.agent_root.resolve())).replace("\\", "/")
    except ValueError:
        rel = str(log_file)
    return {
        "ok": True,
        "action": "logs",
        "name": name,
        "log_path": rel,
        "text": _tail_log(log_file, tail_lines),
    }


def _action_wait(paths, payload: dict[str, Any]) -> dict[str, Any]:
    err = _validate_name(payload.get("name"))
    if err:
        return {"ok": False, "error": err}
    name = str(payload["name"]).strip()
    state = _load_state(paths, name)
    if not state:
        return {"ok": False, "error": f"unknown service '{name}'"}

    regex = str(payload.get("ready_regex") or state.get("ready_regex") or "").strip()
    port = payload.get("ready_port", state.get("ready_port"))
    if port is not None and port != "":
        try:
            port = int(port)
        except (TypeError, ValueError):
            return {"ok": False, "error": "ready_port must be an integer"}
    else:
        port = None

    timeout = payload.get("timeout_sec", payload.get("ready_timeout_sec", 90))
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = 90

    deadline = time.monotonic() + max(1, timeout)
    while time.monotonic() < deadline:
        state = _refresh_alive(state)
        if not state.get("alive"):
            _save_state(paths, name, state)
            return {
                "ok": False,
                "action": "wait",
                "ready": False,
                "error": "process not alive",
                "state": state,
            }
        if _ready(state, paths, regex=regex or None, port=port):
            state["ready"] = True
            state["status"] = "running"
            _save_state(paths, name, state)
            return {"ok": True, "action": "wait", "ready": True, "state": state}
        time.sleep(0.4)

    state = _refresh_alive(state)
    state["ready"] = False
    _save_state(paths, name, state)
    return {
        "ok": False,
        "action": "wait",
        "ready": False,
        "error": "timeout waiting for ready",
        "state": state,
        "logs_tail": _tail_log(_resolve_log_file(paths, state, name), 40),
    }


def _action_list(paths, _payload: dict[str, Any]) -> dict[str, Any]:
    services_dir = _services_dir(paths)
    items: list[dict[str, Any]] = []
    for path in sorted(services_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        data = _refresh_alive(data)
        items.append(
            {
                "name": data.get("name") or path.stem,
                "alive": bool(data.get("alive")),
                "status": data.get("status"),
                "pid": data.get("pid"),
                "cwd": data.get("cwd"),
                "command": data.get("command"),
            }
        )
        if data.get("name"):
            _save_state(paths, str(data["name"]), data)
    return {"ok": True, "action": "list", "services": items}


def run_service(payload: dict[str, Any]) -> dict[str, Any]:
    """Evolved entry — returns a JSON-serializable dict (ok + fields)."""
    AgentPaths = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    action = str(payload.get("action") or "").strip().lower()
    if not action:
        return {"ok": False, "error": "action is required"}

    dispatch = {
        "start": _action_start,
        "stop": _action_stop,
        "status": _action_status,
        "logs": _action_logs,
        "wait": _action_wait,
        "list": _action_list,
        "restart": None,  # filled below
    }

    if action == "restart":
        stop_out = _action_stop(paths, payload)
        # Allow restart even if unknown/stopped
        start_payload = dict(payload)
        # Prefer stored command/cwd when not provided
        name = str(payload.get("name") or "").strip()
        prev = _load_state(paths, name) if name else None
        if prev:
            if not start_payload.get("command") and not start_payload.get("args"):
                start_payload["command"] = prev.get("command")
            if not _coalesce_working_dir(start_payload) and prev.get("cwd"):
                try:
                    rel = Path(str(prev["cwd"])).resolve().relative_to(paths.agent_root.resolve())
                    start_payload["working_dir"] = str(rel).replace("\\", "/")
                except Exception:
                    start_payload["working_dir"] = str(prev.get("cwd"))
            if not start_payload.get("ready_regex") and prev.get("ready_regex"):
                start_payload["ready_regex"] = prev["ready_regex"]
            if start_payload.get("ready_port") in (None, "") and prev.get("ready_port") is not None:
                start_payload["ready_port"] = prev["ready_port"]
        start_out = _action_start(paths, start_payload)
        return {
            "ok": bool(start_out.get("ok")),
            "action": "restart",
            "stop": stop_out,
            "start": start_out,
            "state": start_out.get("state"),
            "ready": start_out.get("ready"),
            "error": start_out.get("error"),
        }

    handler = dispatch.get(action)
    if handler is None:
        return {
            "ok": False,
            "error": f"unknown action '{action}' (use start|stop|status|logs|wait|list|restart)",
        }
    return handler(paths, payload)


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main

    run_tool_main(run_service)


if __name__ == "__main__":
    main()
