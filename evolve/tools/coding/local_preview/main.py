"""local_preview — start optional process, wait port, probe, open browser."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


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


def _resolve_cwd(paths, path_arg: str) -> Path:
    if not path_arg:
        return paths.agent_root
    text = path_arg.strip().replace("\\", "/").lstrip("/")
    try:
        return paths.resolve_under_agent(text, must_exist=True)
    except Exception:
        if not text.startswith("workspace/"):
            try:
                return paths.resolve_under_agent(f"workspace/{text}", must_exist=True)
            except Exception:
                pass
        raise ValueError(f"working_dir 不存在或越界: {path_arg}")


def _is_loopback(host: str) -> bool:
    h = (host or "").lower()
    return h in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _wait_port(host: str, port: int, timeout_sec: float) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.4)
    return False


def _probe_url(url: str) -> dict[str, Any]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return {"ok": True, "status": getattr(resp, "status", None)}
    except urllib.error.HTTPError as exc:
        return {"ok": True, "status": exc.code, "note": "HTTP error but server responded"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def run_local_preview(payload: dict[str, Any]) -> dict[str, Any]:
    url = payload.get("url")
    if not isinstance(url, str) or not url.strip():
        return {"ok": False, "error": "url is required"}
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return {"ok": False, "error": "url must be http or https"}
    host = parsed.hostname or ""
    if not host:
        return {"ok": False, "error": "url missing host"}

    dry_run = bool(payload.get("dry_run", False))
    start_command = payload.get("start_command") if isinstance(payload.get("start_command"), str) else ""
    start_command = start_command.strip()
    open_browser = bool(payload.get("open_browser", True))
    do_probe = bool(payload.get("probe", True))
    wait_sec = payload.get("wait_sec", 30)
    if not isinstance(wait_sec, int) or wait_sec < 0 or wait_sec > 600:
        return {"ok": False, "error": "wait_sec must be int in [0,600]"}
    port = payload.get("port")
    if port is not None and (not isinstance(port, int) or port < 1 or port > 65535):
        return {"ok": False, "error": "port must be int in [1,65535]"}
    if port is None and parsed.port:
        port = parsed.port

    paths = _load_paths().discover(start=_agent_root())
    try:
        cwd = _resolve_cwd(paths, payload.get("working_dir") if isinstance(payload.get("working_dir"), str) else "")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    plan = {
        "url": url,
        "host": host,
        "loopback": _is_loopback(host),
        "start_command": start_command or None,
        "working_dir": str(cwd),
        "port": port,
        "wait_sec": wait_sec,
        "probe": do_probe,
        "open_browser": open_browser,
    }
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": plan}

    started_pid = None
    if start_command:
        # Detached background process
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
                subprocess, "DETACHED_PROCESS", 0
            )
        proc = subprocess.Popen(
            start_command,
            cwd=str(cwd),
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            start_new_session=(sys.platform != "win32"),
        )
        started_pid = proc.pid

    port_ready = None
    if port is not None and wait_sec > 0:
        wait_host = "127.0.0.1" if _is_loopback(host) or host == "0.0.0.0" else host
        port_ready = _wait_port(wait_host, port, float(wait_sec))
        if not port_ready:
            return {
                "ok": False,
                "error": f"port {port} not ready within {wait_sec}s",
                "started_pid": started_pid,
                "plan": plan,
            }

    probe_result = None
    if do_probe:
        probe_result = _probe_url(url)

    opened = False
    if open_browser:
        opened = bool(webbrowser.open(url))

    return {
        "ok": True,
        "dry_run": False,
        "started_pid": started_pid,
        "port_ready": port_ready,
        "probe": probe_result,
        "browser_opened": opened,
        "plan": plan,
    }


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main

    run_tool_main(run_local_preview)


if __name__ == "__main__":
    main()
