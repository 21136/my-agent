""" repl -  interactive Python REPL maintains state across calls via session_id."""

from __future__ import annotations

import io
import json
import os
import pickle
import sys
import textwrap
from pathlib import Path
from typing import Any

_DEFAULT_SESSION = "default"
_MAX_OUTPUT = 32000


def _agent_root() -> Path:
    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        evolve_marker = directory / "evolve"
        if (evolve_marker / "_index.core.toml").is_file() or (evolve_marker / "_index.toml").is_file():
            return directory
    raise RuntimeError("could not locate agent root")


def _session_dir() -> Path:
    d = _agent_root() / "data" / "repl_sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _session_path(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
    return _session_dir() / f"{safe}.pkl"


def _default_ns() -> dict[str, Any]:
    return {"__builtins__": __builtins__}


def _load_ns(session_id: str) -> dict[str, Any]:
    p = _session_path(session_id)
    if p.exists():
        try:
            with open(p, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass
    return _default_ns()


def _save_ns(session_id: str, ns: dict[str, Any]) -> None:
    save = {}
    for k, v in ns.items():
        if k.startswith("__") and k != "__builtins__":
            continue
        try:
            pickle.dumps(v)
            save[k] = v
        except (pickle.PickleError, TypeError, RecursionError):
            pass
    p = _session_path(session_id)
    with open(p, "wb") as f:
        pickle.dump(save, f)


def _truncate(text: str, limit: int = _MAX_OUTPUT) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n... (truncated)", True


def run_repl(payload: dict[str, Any]) -> dict[str, Any]:
    code_str = payload.get("code")
    if not isinstance(code_str, str) or not code_str.strip():
        return {"ok": False, "error": "code is required"}

    session_id = payload.get("session_id", _DEFAULT_SESSION)
    if not isinstance(session_id, str) or not session_id.strip():
        session_id = _DEFAULT_SESSION

    reset = bool(payload.get("reset", False))
    dry_run = bool(payload.get("dry_run", False))

    if dry_run:
        return {"ok": True, "dry_run": True, "session_id": session_id, "code_preview": code_str[:200]}

    os.chdir(str(_agent_root()))

    ns = _load_ns(session_id)
    if reset:
        ns = _default_ns()

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    result = {"ok": True, "session_id": session_id}

    try:
        dedented = textwrap.dedent(code_str)
        compiled = compile(dedented, "<repl>", "exec", flags=0)
        exec(compiled, ns)
        result["stdout"] = _truncate(sys.stdout.getvalue())[0]
        result["stderr"] = _truncate(sys.stderr.getvalue())[0]
    except SyntaxError as e:
        result["stdout"] = _truncate(sys.stdout.getvalue())[0]
        result["stderr"] = _truncate(sys.stderr.getvalue())[0]
        result["error"] = f"SyntaxError: {e}"
        result["ok"] = False
    except Exception as e:
        result["stdout"] = _truncate(sys.stdout.getvalue())[0]
        result["stderr"] = _truncate(sys.stderr.getvalue())[0]
        result["error"] = f"{type(e).__name__}: {e}"
        result["ok"] = False
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    _save_ns(session_id, ns)
    return result


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_repl)


if __name__ == "__main__":
    main()