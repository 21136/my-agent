"""Builtin run_evolved — execute registry evolved tools (TOOLS.md §7.6, TASKS T-107)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[2]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from runtime_guards import SCAFFOLD_DEMO_PREVIEW_CHARS, SUBPROCESS_KILL_WAIT_SEC

from paths import AgentPaths
from tools.registry import EvolvedTool, ToolRegistry, parse_tool_manifest
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok, to_json

TOOL_NAME = "run_evolved"

# Top-level run_evolved fields merged into write_evolve inner arguments (LLM-visible shortcut).
WRITE_EVOLVE_TOP_KEYS: tuple[str, ...] = (
    "path",
    "content",
    "content_base64",
    "content_workspace_path",
    "on_conflict",
)


def coalesce_tool_arguments(outer: dict[str, Any]) -> dict[str, Any]:
    """Merge top-level write_evolve shortcut fields into the inner arguments object."""
    tool_name = outer.get("tool_name")
    inner = outer.get("arguments")
    if not isinstance(inner, dict):
        inner = {}
    if not isinstance(tool_name, str) or tool_name.strip() != "write_evolve":
        return inner
    merged = dict(inner)
    for key in WRITE_EVOLVE_TOP_KEYS:
        if key in outer and outer[key] is not None and key not in merged:
            merged[key] = outer[key]
    return merged


def run(
    arguments: dict[str, Any],
    *,
    registry: ToolRegistry | None = None,
    paths: AgentPaths | None = None,
    allowed_tools: set[str] | None = None,
    cancel_event: threading.Event | None = None,
) -> ToolResult:
    """Invoke an evolved tool script with JSON stdin; wrap stdout in ToolResult."""
    _ = paths
    started = time.perf_counter()
    registry = registry or ToolRegistry.load()

    tool_name = arguments.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        return _fail("tool_name is required", ToolErrorCode.VALIDATION_ERROR, started)

    tool_args = coalesce_tool_arguments(arguments)

    dry_run = bool(arguments.get("dry_run", False))
    if not dry_run and isinstance(tool_args.get("dry_run"), bool):
        dry_run = tool_args["dry_run"]

    tool = registry.get_evolved(tool_name.strip())
    if tool is None:
        allowed = sorted(allowed_tools or ())
        return _fail(
            f"未知 evolved 工具：{tool_name}",
            ToolErrorCode.TOOL_NOT_FOUND,
            started,
            tool_name=tool_name,
            details={
                "requested_tool": tool_name.strip(),
                "available_tools": allowed,
                "hint": "tool_name 须在本会话 evolved 清单内",
            },
        )

    if allowed_tools is not None and tool.name not in allowed_tools:
        allowed = sorted(allowed_tools)
        scope = tool.scope
        topics = list(tool.topics) if tool.topics else []
        if scope == "common" or "common" in topics:
            hint = "此工具 scope=common 但未出现在 allowed 清单，可能 status 非 active 或 registry 未刷新"
        else:
            hint = (
                f"工具 scope={scope}，当前会话缺少主题「{scope}」。"
                f"可执行「加主题 {scope}」激活，或修改 tool.toml 的 topics 添加「common」后重试。"
            )
        return _fail(
            f"工具「{tool.name}」不在本会话清单",
            ToolErrorCode.TOOL_NOT_FOUND,
            started,
            tool_name=tool.name,
            details={
                "requested_tool": tool.name,
                "available_tools": allowed,
                "hint": hint,
            },
        )

    if dry_run and not tool.policy.dry_run_supported:
        return _fail(
            f"tool {tool.name} does not support dry_run",
            ToolErrorCode.VALIDATION_ERROR,
            started,
            tool_name=tool.name,
        )

    try:
        inner = execute_evolved_tool(
            tool,
            tool_args,
            dry_run=dry_run,
            cancel_event=cancel_event,
        )
    except _EvolvedCancelledError:
        return _fail(
            f"tool {tool.name} cancelled",
            ToolErrorCode.VALIDATION_ERROR,
            started,
            tool_name=tool.name,
        )
    except subprocess.TimeoutExpired:
        return _fail(
            f"tool {tool.name} timed out after {tool.policy.timeout_sec}s",
            ToolErrorCode.TIMEOUT,
            started,
            tool_name=tool.name,
        )
    except _EvolvedExecutionError as exc:
        return _fail(str(exc), exc.code, started, tool_name=tool.name, details=exc.details)

    if inner.get("ok") is False:
        message = str(inner.get("error") or inner.get("message") or "evolved tool failed")
        return _fail(message, ToolErrorCode.VALIDATION_ERROR, started, tool_name=tool.name, details=inner)

    data = {"tool_name": tool.name}
    for key, value in inner.items():
        if key == "ok":
            continue
        data[key] = value

    return tool_ok(TOOL_NAME, data, duration_ms=_elapsed_ms(started))


def execute_evolved_tool(
    tool: EvolvedTool,
    arguments: dict[str, Any],
    *,
    dry_run: bool = False,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Run ``tool`` entry script; return parsed JSON from stdout."""
    payload = dict(arguments)
    payload["dry_run"] = dry_run
    stdin = json.dumps(payload, ensure_ascii=False)

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8:replace")

    proc = subprocess.Popen(
        [sys.executable, str(tool.entry.script_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(tool.directory),
        env=env,
    )
    assert proc.stdin is not None
    proc.stdin.write(stdin)
    proc.stdin.close()
    proc.stdin = None

    # Read stdout / stderr in background threads to prevent Windows pipe deadlock.
    # When evolved tool output exceeds ~4 KB, the OS pipe buffer fills, the child
    # blocks on write, and a bare poll() loop never sees it exit (BUG-014).
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def _read(stream, chunks):
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            chunks.append(chunk)

    stdout_thread = threading.Thread(target=_read, args=(proc.stdout, stdout_chunks), daemon=True)
    stderr_thread = threading.Thread(target=_read, args=(proc.stderr, stderr_chunks), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    deadline = time.monotonic() + tool.policy.timeout_sec
    while proc.poll() is None:
        if cancel_event is not None and cancel_event.is_set():
            _terminate_subprocess(proc)
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
            raise _EvolvedCancelledError()
        if time.monotonic() >= deadline:
            _terminate_subprocess(proc)
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
            raise subprocess.TimeoutExpired(cmd=proc.args, timeout=tool.policy.timeout_sec)
        time.sleep(0.05)

    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)
    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)

    try:
        inner = _parse_stdout_json(stdout)
    except json.JSONDecodeError as exc:
        if proc.returncode != 0:
            message = stderr.strip() or f"process exited with code {proc.returncode}"
            raise _EvolvedExecutionError(
                message,
                code=ToolErrorCode.VALIDATION_ERROR,
                details={
                    "exit_code": proc.returncode,
                    "stderr": stderr.strip(),
                    "stdout": stdout.strip(),
                },
            ) from exc
        raise _EvolvedExecutionError(
            f"invalid JSON on stdout: {exc}",
            code=ToolErrorCode.VALIDATION_ERROR,
            details={"stdout": stdout.strip()},
        ) from exc

    if not isinstance(inner, dict):
        raise _EvolvedExecutionError(
            "stdout JSON must be an object",
            code=ToolErrorCode.VALIDATION_ERROR,
            details={"stdout": stdout.strip()},
        )

    if proc.returncode != 0:
        message = str(inner.get("error") or inner.get("message") or stderr.strip() or "")
        if not message:
            message = f"process exited with code {proc.returncode}"
        raise _EvolvedExecutionError(
            message,
            code=ToolErrorCode.VALIDATION_ERROR,
            details={
                "exit_code": proc.returncode,
                "stderr": stderr.strip(),
                **inner,
            },
        )

    return inner


def run_scaffold_demo(
    tool: EvolvedTool,
    *,
    cancel_event: threading.Event | None = None,
    timeout_sec: float | None = None,
) -> dict[str, Any]:
    """Run ``python main.py demo`` for scaffold acceptance (T-1514; no confirm)."""
    main_py = tool.entry.script_path
    rel_dir = tool.relative_dir
    base: dict[str, Any] = {
        "tool_name": tool.name,
        "tool_dir": rel_dir,
        "attempted": False,
    }
    if not main_py.is_file():
        return {
            **base,
            "ok": False,
            "skipped_reason": "main.py missing",
        }

    limit = timeout_sec if timeout_sec is not None else float(tool.policy.timeout_sec)
    proc = subprocess.Popen(
        [sys.executable, str(main_py), "demo"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(tool.directory),
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8:replace"},
    )

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def _drain(stream, chunks):
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            chunks.append(chunk)

    st_reader = threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks), daemon=True)
    se_reader = threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True)
    st_reader.start()
    se_reader.start()

    deadline = time.monotonic() + limit
    while proc.poll() is None:
        if cancel_event is not None and cancel_event.is_set():
            _terminate_subprocess(proc)
            st_reader.join(timeout=2)
            se_reader.join(timeout=2)
            return {
                **base,
                "ok": False,
                "attempted": True,
                "cancelled": True,
                "exit_code": None,
                "skipped_reason": "cancelled",
            }
        if time.monotonic() >= deadline:
            _terminate_subprocess(proc)
            st_reader.join(timeout=2)
            se_reader.join(timeout=2)
            return {
                **base,
                "ok": False,
                "attempted": True,
                "exit_code": None,
                "skipped_reason": f"timed out after {int(limit)}s",
            }
        time.sleep(0.05)

    st_reader.join(timeout=5)
    se_reader.join(timeout=5)
    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)
    exit_code = proc.returncode if proc.returncode is not None else -1
    stdout_text = stdout or ""
    stderr_text = stderr or ""
    preview_limit = SCAFFOLD_DEMO_PREVIEW_CHARS
    return {
        **base,
        "ok": exit_code == 0,
        "attempted": True,
        "exit_code": exit_code,
        "stdout": stdout_text[:preview_limit],
        "stderr": stderr_text[:preview_limit],
        "stdout_truncated": len(stdout_text) > preview_limit,
        "stderr_truncated": len(stderr_text) > preview_limit,
    }


def _terminate_subprocess(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=SUBPROCESS_KILL_WAIT_SEC)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


class _EvolvedCancelledError(RuntimeError):
    """Raised when evolved subprocess is stopped via cancel_event."""


class _EvolvedExecutionError(RuntimeError):
    def __init__(self, message: str, *, code: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def _parse_stdout_json(stdout: str) -> dict[str, Any]:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise json.JSONDecodeError("empty stdout", stdout, 0)
    parsed = json.loads(lines[-1])
    if not isinstance(parsed, dict):
        raise json.JSONDecodeError("expected JSON object", lines[-1], 0)
    return parsed


def _fail(
    message: str,
    code: str,
    started: float,
    *,
    tool_name: str | None = None,
    details: dict[str, Any] | None = None,
) -> ToolResult:
    merged = dict(details or {})
    if tool_name is not None:
        merged.setdefault("tool_name", tool_name)
    return tool_fail(
        TOOL_NAME,
        code,
        message,
        duration_ms=_elapsed_ms(started),
        details=merged or None,
    )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _demo() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        evolve = Path(tmp)
        tool_dir = evolve / "tools" / "common" / "echo_json"
        tool_dir.mkdir(parents=True)
        (tool_dir / "main.py").write_text(
            """import json
import sys

payload = json.load(sys.stdin)
if payload.get("dry_run"):
    print(json.dumps({"ok": True, "dry_run": True, "received": payload}))
else:
    print(json.dumps({"ok": True, "echo": payload.get("message", "")}))
""",
            encoding="utf-8",
        )
        manifest = tool_dir / "tool.toml"
        manifest.write_text(
            """[tool]
name = "echo_json"
description = "Echo stdin payload"
version = "1.0.0"
status = "active"
topics = ["common"]

[entry]
type = "python"
path = "main.py"

[schema.input]
type = "object"
required = ["message"]
[schema.input.properties.message]
type = "string"

[schema.output]
type = "object"

[policy]
confirm = true
dry_run_supported = true
workspace_only = true
timeout_sec = 30
""",
            encoding="utf-8",
        )

        paths = AgentPaths.discover()
        tool = parse_tool_manifest(manifest, evolve_dir=evolve)
        registry = ToolRegistry(agent_paths=paths, evolved=[tool])

        live = run(
            {"tool_name": "echo_json", "arguments": {"message": "hello"}, "dry_run": False},
            registry=registry,
        )
        assert live.ok and live.data["echo"] == "hello"
        print(f"[PASS] execute: {live.data}")

        dry = run(
            {"tool_name": "echo_json", "arguments": {"message": "hello"}, "dry_run": True},
            registry=registry,
        )
        assert dry.ok and dry.data.get("dry_run") is True
        print("[PASS] dry_run")

        missing = run({"tool_name": "missing", "arguments": {}}, registry=registry)
        assert not missing.ok and missing.error.code == ToolErrorCode.TOOL_NOT_FOUND
        print("[PASS] tool_not_found")

        blocked = run(
            {"tool_name": "echo_json", "arguments": {"message": "x"}},
            registry=registry,
            allowed_tools=set(),
        )
        assert not blocked.ok and blocked.error.code == ToolErrorCode.TOOL_NOT_FOUND
        print("[PASS] session allowlist")

        sleepy_dir = evolve / "tools" / "common" / "sleepy"
        sleepy_dir.mkdir(parents=True)
        (sleepy_dir / "main.py").write_text(
            """import json, sys, time
json.load(sys.stdin)
time.sleep(30)
print(json.dumps({"ok": True}))
""",
            encoding="utf-8",
        )
        sleepy_manifest = sleepy_dir / "tool.toml"
        sleepy_manifest.write_text(
            """[tool]
name = "sleepy"
description = "sleep for cancel demo"
version = "1.0.0"
status = "active"
topics = ["common"]

[entry]
type = "python"
path = "main.py"

[schema.input]
type = "object"

[schema.output]
type = "object"

[policy]
confirm = false
dry_run_supported = false
workspace_only = true
timeout_sec = 60
""",
            encoding="utf-8",
        )
        sleepy_tool = parse_tool_manifest(sleepy_manifest, evolve_dir=evolve)
        sleepy_registry = ToolRegistry(agent_paths=paths, evolved=[sleepy_tool])
        cancel = threading.Event()

        def _cancel_soon() -> None:
            time.sleep(0.15)
            cancel.set()

        threading.Thread(target=_cancel_soon, daemon=True).start()
        cancelled = run(
            {"tool_name": "sleepy", "arguments": {}},
            registry=sleepy_registry,
            cancel_event=cancel,
        )
        assert not cancelled.ok
        print("[PASS] T-1512: cancel_event terminates evolved subprocess")

        demo_dir = evolve / "tools" / "common" / "demo_pass"
        demo_dir.mkdir(parents=True)
        (demo_dir / "main.py").write_text(
            'if __name__ == "__main__":\n    print("[PASS] scaffold demo")\n',
            encoding="utf-8",
        )
        demo_manifest = demo_dir / "tool.toml"
        demo_manifest.write_text(
            """[tool]
name = "demo_pass"
description = "scaffold demo pass"
version = "1.0.0"
status = "active"
topics = ["common"]

[entry]
type = "python"
path = "main.py"

[schema.input]
type = "object"

[schema.output]
type = "object"

[policy]
confirm = false
dry_run_supported = false
workspace_only = true
timeout_sec = 30
""",
            encoding="utf-8",
        )
        demo_tool = parse_tool_manifest(demo_manifest, evolve_dir=evolve)
        demo_out = run_scaffold_demo(demo_tool)
        assert demo_out.get("attempted") and demo_out.get("exit_code") == 0
        print("[PASS] T-1514: run_scaffold_demo executes main.py demo")

        import base64

        we = registry.get_evolved("write_evolve")
        if we is not None:
            flat_body = coalesce_tool_arguments(
                {
                    "tool_name": "write_evolve",
                    "path": "evolve/tools/common/flat_demo/main.py",
                    "content_base64": base64.b64encode(b"print('flat')\n").decode("ascii"),
                    "on_conflict": "overwrite",
                }
            )
            assert flat_body.get("path", "").endswith("main.py")
            assert "content_base64" in flat_body
            print("[PASS] coalesce_tool_arguments merges top-level write_evolve fields")

            bad_we = run(
                {
                    "tool_name": "write_evolve",
                    "path": "not/a/valid/path",
                    "content_base64": base64.b64encode(b"x").decode("ascii"),
                    "on_conflict": "overwrite",
                },
                registry=registry,
            )
            assert not bad_we.ok
            assert bad_we.error.message != "process exited with code 1"
            assert "path must match" in bad_we.error.message
            print("[PASS] evolved stdout JSON error surfaced (not empty stderr)")

        print(to_json(live, indent=2))


if __name__ == "__main__":
    _demo()
