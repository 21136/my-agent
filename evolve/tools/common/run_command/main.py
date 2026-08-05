"""run_command — Cursor-like general shell under agent root (Phase 28 + D1 background)."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_DEFAULT_TIMEOUT = 120
_MAX_TIMEOUT = 600
_DEFAULT_LONG_TIMEOUT = 1800
_MAX_OUTPUT_CHARS = 64 * 1024  # Q5: 64 KiB each
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# Phase 35 M3a — long-running local installs / node_modules cleanup
_LONG_COMMAND_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:npm|pnpm|yarn)(?:\.cmd)?\s+(?:install|ci)\b"
    r"|\brmdir\b[\s\S]{0,80}node_modules"
    r"|Remove-Item[\s\S]{0,120}node_modules"
    r"|\brimraf\b"
    r"|cmd\s+/c\s+[\"']?rmdir[\s\S]{0,80}node_modules"
    r"|del\s+/s\s+/q\s+node_modules"
    r")"
)

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

_DANGEROUS_HINT = re.compile(
    r"(?i)\b(rm\s+-rf|Remove-Item\s+-Recurse|format\s+|mkfs\.|dd\s+if=)\b"
)


def _long_timeout_cap() -> int:
    raw = os.environ.get("MY_AGENT_RUN_COMMAND_LONG_TIMEOUT_SEC", str(_DEFAULT_LONG_TIMEOUT))
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_LONG_TIMEOUT
    return max(_MAX_TIMEOUT, value)


def _is_long_command(command: str) -> bool:
    return bool(_LONG_COMMAND_RE.search(command or ""))


def _resolve_timeout_sec(command: str, payload: dict[str, Any]) -> tuple[int, bool]:
    """Return (timeout_sec, used_long_tier)."""
    long_tier = _is_long_command(command)
    long_cap = _long_timeout_cap()
    cap = long_cap if long_tier else _MAX_TIMEOUT
    raw = payload.get("timeout_sec")
    if raw is None or raw == "":
        timeout_sec = long_cap if long_tier else _DEFAULT_TIMEOUT
    else:
        try:
            timeout_sec = int(raw)
        except (TypeError, ValueError):
            timeout_sec = long_cap if long_tier else _DEFAULT_TIMEOUT
    if timeout_sec < 1:
        timeout_sec = 1
    if timeout_sec > cap:
        timeout_sec = cap
    return timeout_sec, long_tier


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
    from paths import AgentPaths, PathOutOfBoundsError

    return AgentPaths, PathOutOfBoundsError


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n…(truncated)", True


def _coalesce_working_dir(payload: dict[str, Any]) -> str:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from project_npm_guard import coalesce_working_dir

    return coalesce_working_dir(payload)


def _resolve_cwd(paths, PathOutOfBoundsError, path_arg: str) -> Path:
    if not path_arg:
        return paths.agent_root
    text = path_arg.strip().replace("\\", "/").lstrip("/")
    try:
        return paths.resolve_under_agent(text, must_exist=True)
    except PathOutOfBoundsError:
        raise
    except (FileNotFoundError, TypeError, ValueError) as exc:
        raise ValueError(f"working_dir not found or invalid: {path_arg}") from exc


def _filter_env(raw: Any) -> tuple[dict[str, str] | None, str | None]:
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        return None, "env must be an object of string values"
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            return None, "env keys must be non-empty strings"
        if not isinstance(value, str):
            return None, f"env[{key!r}] must be a string"
        name = key.strip()
        upper = name.upper()
        if upper in _ENV_DENY_EXACT or _ENV_DENY_SUFFIX.match(name):
            return None, f"env key not allowed: {name}"
        out[name] = value
    return out, None


def _shell_argv(command: str) -> list[str]:
    if sys.platform == "win32":
        return [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ]
    return ["/bin/bash", "-lc", command]


def _auto_service_name(command: str, cwd_rel: str) -> str:
    digest = hashlib.sha256(f"{cwd_rel}\0{command}".encode("utf-8")).hexdigest()[:8]
    return f"cmd-{digest}"


def _load_run_service():
    service_main = Path(__file__).resolve().parent.parent / "run_service" / "main.py"
    if not service_main.is_file():
        raise FileNotFoundError(f"run_service not found: {service_main}")
    spec = importlib.util.spec_from_file_location("run_service_escalate", service_main)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load run_service")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _escalate_background(
    *,
    command: str,
    cwd_rel: str,
    working_dir_arg: str,
    payload: dict[str, Any],
    hint: str | None,
) -> dict[str, Any]:
    explicit = payload.get("name")
    if isinstance(explicit, str) and explicit.strip():
        name = explicit.strip()
        if not _NAME_RE.match(name):
            return {
                "ok": False,
                "error": "name must match /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/",
                "background": True,
            }
    else:
        name = _auto_service_name(command, cwd_rel)

    ready_timeout = payload.get("ready_timeout_sec", 0)
    try:
        ready_timeout = int(ready_timeout)
    except (TypeError, ValueError):
        ready_timeout = 0

    start_payload: dict[str, Any] = {
        "action": "start",
        "name": name,
        "command": command,
        "working_dir": working_dir_arg or cwd_rel,
        "ready_timeout_sec": ready_timeout,
    }
    ready_regex = payload.get("ready_regex")
    if isinstance(ready_regex, str) and ready_regex.strip():
        start_payload["ready_regex"] = ready_regex.strip()
    ready_port = payload.get("ready_port")
    if ready_port is not None and ready_port != "":
        start_payload["ready_port"] = ready_port

    try:
        mod = _load_run_service()
        out = mod.run_service(start_payload)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"background escalate failed: {exc}",
            "background": True,
            "name": name,
            "command": command,
            "cwd": cwd_rel,
        }

    if not isinstance(out, dict):
        return {
            "ok": False,
            "error": "run_service returned non-object",
            "background": True,
            "name": name,
        }

    result = dict(out)
    result["background"] = True
    result["escalated"] = True
    result["via"] = "run_command"
    result["name"] = name
    result["command"] = command
    result["cwd"] = cwd_rel
    if hint:
        result["hint"] = hint
    if "logs_tail" not in result:
        state = result.get("state") if isinstance(result.get("state"), dict) else {}
        log_rel = state.get("log_path")
        if isinstance(log_rel, str) and log_rel.strip():
            try:
                agent_root = _agent_root()
                log_file = (agent_root / log_rel.replace("\\", "/")).resolve()
                if log_file.is_file():
                    text = log_file.read_text(encoding="utf-8", errors="replace")
                    lines = text.splitlines()
                    result["logs_tail"] = "\n".join(lines[-40:])
            except OSError:
                pass
    return result


def run_command(payload: dict[str, Any]) -> dict[str, Any]:
    AgentPaths, PathOutOfBoundsError = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        return {"ok": False, "error": "command is required"}
    command = command.strip()

    timeout_sec, long_tier = _resolve_timeout_sec(command, payload)

    dry_run = bool(payload.get("dry_run", False))
    background = bool(payload.get("background", False))
    working_dir_arg = _coalesce_working_dir(payload)

    try:
        cwd = _resolve_cwd(paths, PathOutOfBoundsError, working_dir_arg)
    except PathOutOfBoundsError as exc:
        return {"ok": False, "error": f"working_dir out of bounds: {exc}"}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    force_install = bool(payload.get("force_install", False))
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from project_npm_guard import redundant_npm_install_error

    install_err = redundant_npm_install_error(cwd, command, force_install=force_install)
    if install_err:
        return {
            "ok": False,
            "error": install_err,
            "cwd": paths.to_agent_relative(cwd) if cwd != paths.agent_root else ".",
            "hint": "working_dir 示例: workspace/<id>/frontend",
        }

    env_extra, env_err = _filter_env(payload.get("env"))
    if env_err:
        return {"ok": False, "error": env_err}

    argv = _shell_argv(command)
    cwd_rel = paths.to_agent_relative(cwd) if cwd != paths.agent_root else "."
    if cwd_rel == "":
        cwd_rel = "."

    hint = None
    if _DANGEROUS_HINT.search(command):
        hint = "command looks destructive; confirm carefully"

    if background and env_extra:
        return {
            "ok": False,
            "error": "background escalate does not support env; omit env or use run_service",
            "background": True,
        }

    if background:
        explicit = payload.get("name")
        if isinstance(explicit, str) and explicit.strip():
            bg_name = explicit.strip()
        else:
            bg_name = _auto_service_name(command, cwd_rel)
        if dry_run:
            result: dict[str, Any] = {
                "ok": True,
                "dry_run": True,
                "background": True,
                "escalate_to": "run_service",
                "name": bg_name,
                "command": command,
                "cwd": cwd_rel,
                "argv": argv,
            }
            if hint:
                result["hint"] = hint
            return result
        return _escalate_background(
            command=command,
            cwd_rel=cwd_rel,
            working_dir_arg=working_dir_arg,
            payload=payload,
            hint=hint,
        )

    if dry_run:
        result = {
            "ok": True,
            "dry_run": True,
            "command": command,
            "argv": argv,
            "cwd": cwd_rel,
            "timeout_sec": timeout_sec,
            "long_timeout_tier": long_tier,
        }
        if env_extra:
            result["env_keys"] = sorted(env_extra)
        if hint:
            result["hint"] = hint
        return result

    run_env = None
    if env_extra:
        run_env = {**os.environ, **env_extra}

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
            env=run_env,
        )
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "error": f"command timed out after {timeout_sec}s",
            "command": command,
            "cwd": cwd_rel,
            "elapsed_ms": elapsed_ms,
            "timeout_sec": timeout_sec,
            "long_timeout_tier": long_tier,
            "hint": "long-lived processes need run_service, not run_command",
        }
    except OSError as exc:
        return {"ok": False, "error": str(exc), "command": command, "cwd": cwd_rel}

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    stdout, stdout_trunc = _truncate(completed.stdout or "")
    stderr, stderr_trunc = _truncate(completed.stderr or "")
    exit_code = int(completed.returncode)
    out: dict[str, Any] = {
        "ok": exit_code == 0,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "cwd": cwd_rel,
        "command": command,
        "elapsed_ms": elapsed_ms,
        "timeout_sec": timeout_sec,
        "long_timeout_tier": long_tier,
    }
    if stdout_trunc or stderr_trunc:
        out["truncated"] = True
    if hint:
        out["hint"] = hint
    if exit_code != 0:
        out["error"] = f"exit_code={exit_code}"
    return out


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main

    run_tool_main(run_command)


if __name__ == "__main__":
    main()
