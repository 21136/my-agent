"""jshell_exec — interactive Java REPL via jshell, maintains state across calls via session_id."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_DEFAULT_SESSION = "default"
_DEFAULT_TIMEOUT = 30
_MAX_OUTPUT = 32000


def _agent_root() -> Path:
    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        evolve_marker = directory / "evolve"
        if (evolve_marker / "_index.core.toml").is_file() or (evolve_marker / "_index.toml").is_file():
            return directory
    raise RuntimeError("could not locate agent root")


def _session_dir() -> Path:
    d = _agent_root() / "data" / "jshell_sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _session_path(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
    return _session_dir() / f"{safe}.jsh"


def _find_jshell() -> str:
    jshell = shutil.which("jshell")
    if jshell:
        return jshell
    # Windows JDK paths
    for base in (r"C:\Program Files\Java", r"C:\Program Files (x86)\Java"):
        base_p = Path(base)
        if base_p.is_dir():
            for jdk in sorted(base_p.iterdir(), reverse=True):
                candidate = jdk / "bin" / "jshell.exe"
                if candidate.is_file():
                    return str(candidate)
    return "jshell"


def _truncate(text: str, limit: int = _MAX_OUTPUT) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n…(truncated)", True


def run_jshell(payload: dict[str, Any]) -> dict[str, Any]:
    code_str = payload.get("code")
    if not isinstance(code_str, str) or not code_str.strip():
        return {"ok": False, "error": "code is required (Java snippet)"}

    session_id = payload.get("session_id", _DEFAULT_SESSION)
    if not isinstance(session_id, str) or not session_id.strip():
        session_id = _DEFAULT_SESSION

    reset = bool(payload.get("reset", False))
    timeout_sec = int(payload.get("timeout_sec", _DEFAULT_TIMEOUT))
    if timeout_sec < 1:
        timeout_sec = 1
    dry_run = bool(payload.get("dry_run", False))

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "session_id": session_id,
            "code_preview": code_str[:200],
        }

    jshell_bin = _find_jshell()
    session_file = _session_path(session_id)

    if reset and session_file.exists():
        session_file.unlink()

    # Build startup: replay all previous snippets
    startup_lines: list[str] = []
    if session_file.exists():
        startup_lines = session_file.read_text(encoding="utf-8").splitlines()

    # Write temp startup file (jshell --startup needs a file, can't use /dev/stdin)
    startup_path = session_file.with_suffix(".startup.tmp")
    startup_path.write_text("\n".join(startup_lines), encoding="utf-8")

    try:
        command = [
            jshell_bin,
            "--feedback", "concise",
            "--startup", str(startup_path),
            "-",  # read new snippet from stdin
        ]
        env = {**os.environ}
        completed = subprocess.run(
            command,
            input=code_str,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        startup_path.unlink(missing_ok=True)
        return {"ok": False, "error": f"jshell timed out after {timeout_sec}s", "session_id": session_id}
    except OSError as exc:
        startup_path.unlink(missing_ok=True)
        return {"ok": False, "error": f"jshell not available: {exc}", "session_id": session_id}
    finally:
        startup_path.unlink(missing_ok=True)

    stdout, _ = _truncate(completed.stdout or "")
    stderr, _ = _truncate(completed.stderr or "")

    ok = completed.returncode == 0 and not _has_error(stderr)

    result: dict[str, Any] = {
        "ok": ok,
        "session_id": session_id,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }

    # On success, persist the snippet
    if ok:
        with open(session_file, "a", encoding="utf-8") as f:
            if session_file.stat().st_size > 0:
                f.write("\n")
            f.write(code_str.strip())

    return result


def _has_error(stderr: str) -> bool:
    """jshell prints errors to stderr even with exit_code 0."""
    return "error:" in stderr.lower()


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_jshell)


def _demo() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from paths import AgentPaths
    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    paths = AgentPaths.discover()
    registry = ToolRegistry.load(paths)
    tool = registry.get_evolved("jshell_exec")
    assert tool is not None and tool.status in {"active", "draft"}
    print("[PASS] registry loads jshell_exec")

    # dry_run
    dry = run(
        {"tool_name": "jshell_exec", "arguments": {"code": "int x = 1;", "dry_run": True}, "dry_run": True},
        registry=registry,
    )
    assert dry.ok and dry.data.get("dry_run") is True
    print("[PASS] dry_run")

    # session reset
    reset = run(
        {"tool_name": "jshell_exec", "arguments": {"code": "int x = 42;", "session_id": "demo_reset", "reset": True}},
        registry=registry,
    )
    assert reset.ok, f"reset failed: {reset.data}"
    print(f"[PASS] reset session: {reset.data.get('stdout', '').strip()}")

    # state carry-over
    s1 = run(
        {"tool_name": "jshell_exec", "arguments": {"code": "int a = 10;", "session_id": "demo_state"}},
        registry=registry,
    )
    assert s1.ok, f"s1 failed: {s1.data}"
    s2 = run(
        {"tool_name": "jshell_exec", "arguments": {"code": "System.out.println(a * 2);", "session_id": "demo_state"}},
        registry=registry,
    )
    assert s2.ok, f"s2 failed: {s2.data}"
    assert "20" in s2.data.get("stdout", ""), f"expected 20, got: {s2.data.get('stdout')}"
    print("[PASS] state carry-over: a=10 → a*2=20")

    # /exit to clean up
    run(
        {"tool_name": "jshell_exec", "arguments": {"code": "/exit", "session_id": "demo_state"}},
        registry=registry,
    )
    # cleanup session files
    for sid in ("demo_reset", "demo_state"):
        sp = _session_path(sid)
        sp.unlink(missing_ok=True)

    print("[PASS] all jshell_exec demo checks")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
