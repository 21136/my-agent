"""Durable user-selected approval modes with conservative executor-side scope checks."""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Literal

from paths import AgentPaths

ApprovalMode = Literal["manual", "auto", "full"]
APPROVAL_MODES: tuple[str, ...] = ("manual", "auto", "full")
DEFAULT_APPROVAL_MODE: ApprovalMode = "manual"
_LOCK = threading.RLock()
# Persisted mode is a preference, not a durable grant. Every process starts fail-closed.
_ACTIVE_MODE: ApprovalMode = DEFAULT_APPROVAL_MODE


class ApprovalPreferenceError(ValueError):
    """Invalid or unavailable approval preferences."""


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate setting key")
        value[key] = item
    return value


def _preference_path(paths: AgentPaths) -> Path:
    return paths.data / "approval_preferences.json"


def load_saved_approval_mode(paths: AgentPaths | None = None) -> ApprovalMode:
    agent_paths = paths or AgentPaths.discover()
    target = _preference_path(agent_paths)
    with _LOCK:
        try:
            payload = json.loads(
                target.read_text(encoding="utf-8"),
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except FileNotFoundError:
            return DEFAULT_APPROVAL_MODE
        except (OSError, UnicodeError, ValueError):
            # An unreadable preference must never elevate the effective permission.
            return DEFAULT_APPROVAL_MODE
        if (type(payload) is not dict or type(payload.get("format")) is not int
                or payload.get("format") != 1
                or set(payload) != {"format", "approval_mode"}
                or type(payload.get("approval_mode")) is not str
                or payload["approval_mode"] not in APPROVAL_MODES):
            return DEFAULT_APPROVAL_MODE
        return payload["approval_mode"]  # type: ignore[return-value]


def load_approval_preferences(paths: AgentPaths | None = None) -> ApprovalMode:
    """Return the active process mode, failing closed if persisted state is damaged."""
    global _ACTIVE_MODE
    with _LOCK:
        saved = load_saved_approval_mode(paths)
        if saved != _ACTIVE_MODE:
            # This includes process restart, malformed storage, or an out-of-band edit.
            # A saved preference never reactivates or changes permissions on its own.
            _ACTIVE_MODE = DEFAULT_APPROVAL_MODE
        return _ACTIVE_MODE


def _save_approval_preferences(paths: AgentPaths, mode: ApprovalMode) -> None:
    target = _preference_path(paths)
    payload = json.dumps(
        {"format": 1, "approval_mode": mode}, ensure_ascii=False, indent=2,
    ) + "\n"
    temporary: str | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=target.parent,
            prefix=f".{target.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
    except OSError as exc:
        raise ApprovalPreferenceError("无法保存审批偏好") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _payload(paths: AgentPaths, *, event_type: str = "state", status: str = "ok",
             message: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": f"approval_preferences.{event_type}",
        "status": status,
        "approval_mode": load_approval_preferences(paths),
        "saved_approval_mode": load_saved_approval_mode(paths),
        "available_modes": list(APPROVAL_MODES),
    }
    if message:
        result["message"] = message
    return result


def dispatch_approval_preferences_message(paths: AgentPaths, message: dict[str, Any]) -> list[dict[str, Any]]:
    if type(message) is not dict or type(message.get("type")) is not str:
        raise ApprovalPreferenceError("审批偏好请求无效")
    msg_type = message["type"]
    if msg_type == "approval_preferences.get":
        if set(message) != {"type"}:
            return [_payload(paths, event_type="done", status="rejected", message="读取请求包含未知字段")]
        return [_payload(paths)]
    if msg_type != "approval_preferences.set":
        raise ApprovalPreferenceError("未知的审批偏好操作")
    if set(message) != {"type", "approval_mode"}:
        return [_payload(paths, event_type="done", status="rejected", message="设置请求字段无效")]
    raw_mode = message.get("approval_mode")
    if type(raw_mode) is not str or raw_mode not in APPROVAL_MODES:
        return [_payload(paths, event_type="done", status="rejected", message="审批档位无效")]
    mode: ApprovalMode = raw_mode  # type: ignore[assignment]
    global _ACTIVE_MODE
    with _LOCK:
        # A failed request to turn approvals back on must revoke in-memory authority
        # even when the preference file cannot be updated.
        if mode == "manual":
            _ACTIVE_MODE = DEFAULT_APPROVAL_MODE
        try:
            _save_approval_preferences(paths, mode)
        except ApprovalPreferenceError as exc:
            return [_payload(paths, event_type="done", status="rejected", message=str(exc))]
        _ACTIVE_MODE = mode
    return [_payload(paths, event_type="done")]


def approval_mode_allows(
    *, mode: str, project_id: str, project_root: str, active_shell: str,
    work_intent: str, task_scope: str, tool_name: str,
    path: str = "", command: str = "", cwd: str = "", background: bool = False,
) -> bool:
    """Decide which ordinary confirms a user-selected mode may skip.

    Both modes require an executable, project-bound turn. ``auto`` additionally
    requires an explicit task scope and keeps a narrow command allowlist. ``full``
    skips confirmation for any run_command whose working directory stays within the
    tool's existing agent-root execution scope. This is not a sandbox: arbitrary shell
    commands can access secrets, use the network, delete data, or bypass dedicated
    tools' confirmation paths. The UI must make that risk prominent.
    """
    if mode not in {"auto", "full"}:
        return False
    if (not project_id.strip() or not project_root.strip()
            or active_shell not in {"grow", "unified", "project"}
            or work_intent != "execute"):
        return False
    # Task scope is a product-level guard for auto mode, not a path capability.
    # The actual target is still independently confined to the bound project root.
    if mode == "auto" and not task_scope.strip():
        return False

    if tool_name in {"write_text", "patch_file", "search_replace"}:
        from write_policy import is_sensitive_write_path, path_under_project

        return bool(path.strip() and path_under_project(path, project_root)
                    and not is_sensitive_write_path(path))

    if tool_name == "run_command":
        if not command.strip():
            return False

        # Full intentionally permits arbitrary shell execution, including commands
        # which classify as build/test, network, install, or destructive. Preserve
        # run_command's own cwd boundary, but do not imply it constrains subprocess
        # effects. Empty cwd and '.' are the tool's existing agent-root default.
        if mode == "full":
            from run_command_policy import working_dir_under_agent_root

            return working_dir_under_agent_root(cwd)

        if background or not cwd.strip():
            return False
        # Shell composition, redirection and inline evaluation can hide side effects.
        if any(token in command for token in (";", "&&", "||", "|", "<", ">", "&", "`", "$(", "\n", "\r")):
            return False
        if re.search(r"(?i)\b(?:pythonw?|python3?|py|node|pwsh|powershell|bash|sh|ruby|perl)\b\s+(?:-[ce]|-command|-encodedcommand|--eval|--execute)\b", command):
            return False
        from run_command_policy import working_dir_under_project

        if not working_dir_under_project(cwd, project_root):
            return False
        # Do not infer safety from a blacklist or substring classifier. These tokens
        # enable composition, expansion, redirection, command substitution, or script
        # blocks in common shells (including PowerShell).
        if re.search(r"(?i)[`$%<>|;&(){}\[\]\\\r\n'\"]", command):
            return False
        # No executable/script runner is eligible for automatic approval. Build/test
        # tools can execute arbitrary repository-controlled code despite benign names.
        # This exact allowlist is deliberately limited to inert workspace status and
        # version queries; arguments are not accepted except the listed fixed forms.
        safe_commands = {
            "pwd", "get-location", "git status", "git status --short", "git diff",
            "git diff --stat", "git branch", "git branch --show-current",
            "python --version", "python3 --version", "node --version", "npm --version",
        }
        return command.strip().casefold() in safe_commands
    return False
import asyncio
import hmac
import json
import secrets
from typing import Any


def _approval_control_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate request field")
        result[key] = value
    return result


async def start_approval_control_server(paths: AgentPaths, token: str | None = None):
    """Start a loopback-only, no-CORS private control endpoint for Electron main."""
    credential = token or secrets.token_hex(32)
    if type(credential) is not str or len(credential) != 64 or any(ch not in "0123456789abcdef" for ch in credential):
        raise ValueError("approval control credential is malformed")

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        status = 400
        payload: dict[str, Any] = {"status": "rejected", "message": "审批控制请求无效"}
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=3)
            if len(request_line) > 2048 or not request_line.endswith(b"\r\n"):
                raise ValueError("bad request line")
            parts = request_line[:-2].decode("ascii").split(" ")
            if len(parts) != 3 or parts[2] != "HTTP/1.1":
                raise ValueError("bad request line")
            method, target, _version = parts
            headers: dict[str, str] = {}
            total = 0
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=3)
                total += len(line)
                if total > 8192 or not line.endswith(b"\r\n"):
                    raise ValueError("bad headers")
                if line == b"\r\n":
                    break
                name, separator, value = line[:-2].partition(b":")
                if not separator:
                    raise ValueError("bad header")
                key = name.decode("ascii").strip().lower()
                if not key or key in headers:
                    raise ValueError("duplicate header")
                headers[key] = value.decode("latin-1").strip()
            if target != "/approval-preferences" or "?" in target or "#" in target:
                status, payload = 404, {"status": "rejected", "message": "控制接口不存在"}
            elif "origin" in headers or "access-control-request-method" in headers:
                status, payload = 403, {"status": "rejected", "message": "浏览器来源不能访问审批控制接口"}
            elif not hmac.compare_digest(headers.get("authorization", ""), f"Bearer {credential}"):
                status, payload = 401, {"status": "rejected", "message": "审批控制认证失败"}
            elif method == "GET":
                if "content-length" in headers or "transfer-encoding" in headers:
                    raise ValueError("GET body is not accepted")
                payload = dispatch_approval_preferences_message(paths, {"type": "approval_preferences.get"})[0]
                status = 200
            elif method == "POST":
                if headers.get("content-type", "").lower() != "application/json":
                    status, payload = 415, {"status": "rejected", "message": "请求格式无效"}
                elif "transfer-encoding" in headers:
                    raise ValueError("transfer encoding is not accepted")
                else:
                    length = headers.get("content-length", "")
                    if not length.isascii() or not length.isdigit() or int(length) > 4096:
                        raise ValueError("request body length is invalid")
                    raw = await asyncio.wait_for(reader.readexactly(int(length)), timeout=3)
                    message = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_approval_control_json_pairs)
                    if type(message) is not dict or message.get("type") != "approval_preferences.set":
                        raise ValueError("unsupported approval control request")
                    payload = dispatch_approval_preferences_message(paths, message)[0]
                    status = 200
            else:
                status, payload = 405, {"status": "rejected", "message": "请求方法不支持"}
        except Exception:
            status, payload = 400, {"status": "rejected", "message": "审批控制请求无效"}
        try:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            reasons = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 415: "Unsupported Media Type"}
            writer.write((f"HTTP/1.1 {status} {reasons.get(status, 'Error')}\r\n"
                          f"Content-Type: application/json; charset=utf-8\r\n"
                          f"Content-Length: {len(body)}\r\n"
                          "Cache-Control: no-store\r\nConnection: close\r\n\r\n").encode("ascii") + body)
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(handle, "127.0.0.1", 0, limit=16 * 1024)
    return server, credential, server.sockets[0].getsockname()[1]
