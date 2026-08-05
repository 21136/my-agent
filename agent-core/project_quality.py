"""Phase 45 — db migration status (read-only) and quality commands via ENV.md."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths

_DEFAULT_TIMEOUT = 120
_VALID_BACKENDS = frozenset({"auto", "alembic", "prisma"})


def _resolve_working_dir(paths: AgentPaths, working_dir: str) -> Path:
    text = working_dir.strip().replace("\\", "/").lstrip("/")
    if not text:
        raise ValueError("working_dir is required")
    return paths.resolve_under_agent(text, must_exist=True)


def detect_migration_backend(cwd: Path) -> str | None:
    if (cwd / "alembic.ini").is_file() or (cwd / "backend" / "alembic.ini").is_file():
        return "alembic"
    if (cwd / "prisma" / "schema.prisma").is_file():
        return "prisma"
    if list(cwd.glob("**/schema.prisma")):
        return "prisma"
    return None


def _alembic_cwd(cwd: Path) -> Path:
    if (cwd / "alembic.ini").is_file():
        return cwd
    if (cwd / "backend" / "alembic.ini").is_file():
        return cwd / "backend"
    return cwd


def _run_cmd(argv: list[str], *, cwd: Path, timeout_sec: int) -> dict[str, Any]:
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
        )
        return {
            "ok": completed.returncode == 0,
            "exit_code": int(completed.returncode),
            "stdout": completed.stdout or "",
            "stderr": completed.stderr or "",
            "command": " ".join(argv),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timed out after {timeout_sec}s", "command": " ".join(argv)}
    except OSError as exc:
        return {"ok": False, "error": str(exc), "command": " ".join(argv)}


def db_migrate_status(
    paths: AgentPaths,
    *,
    working_dir: str,
    backend: str = "auto",
    dry_run: bool = False,
    timeout_sec: int = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    backend_key = (backend or "auto").strip().lower()
    if backend_key not in _VALID_BACKENDS:
        return {"ok": False, "error": f"backend must be one of {sorted(_VALID_BACKENDS)}"}

    try:
        cwd = _resolve_working_dir(paths, working_dir)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if not cwd.is_dir():
        return {"ok": False, "error": f"working_dir is not a directory: {working_dir}"}

    detected = detect_migration_backend(cwd) if backend_key == "auto" else backend_key
    if detected is None:
        return {
            "ok": False,
            "error": "no alembic.ini or prisma/schema.prisma found",
            "working_dir": paths.to_agent_relative(cwd),
        }

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "backend": detected,
            "working_dir": paths.to_agent_relative(cwd),
        }

    messages: list[str] = []
    current_revision: str | None = None
    heads: list[str] = []
    pending_count = 0
    dirty: bool | None = None

    if detected == "alembic":
        acwd = _alembic_cwd(cwd)
        current = _run_cmd(["alembic", "current"], cwd=acwd, timeout_sec=timeout_sec)
        heads_out = _run_cmd(["alembic", "heads"], cwd=acwd, timeout_sec=timeout_sec)
        history = _run_cmd(["alembic", "history", "-i"], cwd=acwd, timeout_sec=timeout_sec)
        if current.get("stdout"):
            messages.append(current["stdout"].strip())
            m = re.search(r"\(head\)|([0-9a-f]+)", current["stdout"], re.I)
            if m and m.group(1):
                current_revision = m.group(1)
        if heads_out.get("stdout"):
            for line in heads_out["stdout"].splitlines():
                line = line.strip()
                if line:
                    heads.append(line)
                    rev = re.match(r"^([0-9a-f]+)", line, re.I)
                    if rev:
                        heads.append(rev.group(1))
        if history.get("stdout"):
            messages.append(history["stdout"].strip()[:2000])
        # pending heuristic: heads count > 1 or current not in heads text
        pending_count = max(0, len(heads) - 1) if heads else 0
        ok = current.get("ok", False) or bool(current.get("stdout"))
        if current.get("error"):
            return {"ok": False, "error": current["error"], "backend": "alembic"}
    else:
        status = _run_cmd(["npx", "prisma", "migrate", "status"], cwd=cwd, timeout_sec=timeout_sec)
        if not status.get("ok") and not status.get("stdout"):
            status = _run_cmd(["prisma", "migrate", "status"], cwd=cwd, timeout_sec=timeout_sec)
        text = (status.get("stdout") or "") + (status.get("stderr") or "")
        messages.append(text.strip()[:4000])
        if "Database schema is up to date" in text:
            pending_count = 0
        elif "following migration have not yet been applied" in text.lower():
            pending_count = text.lower().count("migration")
            if pending_count == 0:
                pending_count = 1
        ok = status.get("ok", False) or "schema" in text.lower()
        if status.get("error") and not text:
            return {"ok": False, "error": status["error"], "backend": "prisma"}

    return {
        "ok": bool(ok),
        "backend": detected,
        "working_dir": paths.to_agent_relative(cwd),
        "current_revision": current_revision,
        "heads": list(dict.fromkeys(heads))[:20],
        "pending_count": pending_count,
        "dirty": dirty,
        "messages": messages,
    }


# ---- quality commands (ENV.md E11) ----

_QUALITY_ITEM_RE = re.compile(
    r"^\s*-\s*id:\s*(\S+)\s*$",
    re.MULTILINE,
)
_CMD_JSON_RE = re.compile(r"^\s*cmd:\s*(\[.*\])\s*$", re.MULTILINE)
_CWD_RE = re.compile(r"^\s*cwd:\s*(\S+)\s*$", re.MULTILINE)


def parse_quality_commands_from_env_text(text: str) -> list[dict[str, Any]]:
    """Parse optional quality.commands block from ENV.md (E11)."""
    if "quality:" not in text:
        return []
    start = text.find("quality:")
    block = text[start:]
    commands: list[dict[str, Any]] = []
    chunks = re.split(r"(?=^\s*-\s*id:)", block, flags=re.MULTILINE)
    for chunk in chunks[1:]:
        id_m = _QUALITY_ITEM_RE.search(chunk)
        if not id_m:
            continue
        cmd_m = _CMD_JSON_RE.search(chunk)
        if not cmd_m:
            continue
        try:
            cmd = json.loads(cmd_m.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(cmd, list) or not all(isinstance(x, str) for x in cmd):
            continue
        entry: dict[str, Any] = {"id": id_m.group(1), "cmd": cmd}
        cwd_m = _CWD_RE.search(chunk)
        if cwd_m:
            entry["cwd"] = cwd_m.group(1)
        commands.append(entry)
    return commands


def load_quality_commands_near(start: Path) -> list[dict[str, Any]]:
    from project_env import find_env_path

    env_path = find_env_path(start)
    if env_path is None:
        return []
    try:
        return parse_quality_commands_from_env_text(env_path.read_text(encoding="utf-8"))
    except OSError:
        return []


_RUFF_VIOLATION_RE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+):\s*(?P<code>\S+)\s+(?P<message>.+)$"
)
_ESLINT_VIOLATION_RE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+):\s+(?P<message>.+)$"
)


def parse_ruff_violations(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = _RUFF_VIOLATION_RE.match(line.strip())
        if m:
            out.append(
                {
                    "file": m.group("file"),
                    "line": int(m.group("line")),
                    "col": int(m.group("col")),
                    "message": f"{m.group('code')} {m.group('message')}".strip(),
                }
            )
    return out


def parse_eslint_violations(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = _ESLINT_VIOLATION_RE.match(line.strip())
        if m:
            out.append(
                {
                    "file": m.group("file"),
                    "line": int(m.group("line")),
                    "col": int(m.group("col")),
                    "message": m.group("message").strip(),
                }
            )
    return out


def run_quality(
    paths: AgentPaths,
    *,
    working_dir: str | None = None,
    only: list[str] | None = None,
    fail_fast: bool = True,
    dry_run: bool = False,
    timeout_sec: int = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    base: Path | None = None
    if working_dir:
        try:
            base = _resolve_working_dir(paths, working_dir)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
    else:
        base = paths.agent_root

    commands = load_quality_commands_near(base)
    if not commands:
        return {
            "ok": False,
            "error": "no quality.commands in ENV.md (see PROJECT-QUALITY E11)",
        }

    if only:
        wanted = {x.strip() for x in only if x.strip()}
        commands = [c for c in commands if c.get("id") in wanted]
        if not commands:
            return {"ok": False, "error": f"no matching quality command ids: {sorted(wanted)}"}

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "commands": commands,
        }

    results: list[dict[str, Any]] = []
    overall_ok = True
    for spec in commands:
        cmd_id = str(spec.get("id") or "unknown")
        cmd = spec.get("cmd") or []
        if not isinstance(cmd, list):
            return {"ok": False, "error": f"quality command {cmd_id}: cmd must be array"}
        cwd_rel = spec.get("cwd")
        if isinstance(cwd_rel, str) and cwd_rel.strip():
            cwd = (base / cwd_rel.strip()).resolve() if base else Path(cwd_rel)
        else:
            cwd = base or paths.agent_root
        started = time.perf_counter()
        run = _run_cmd([str(x) for x in cmd], cwd=cwd, timeout_sec=timeout_sec)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        excerpt = ((run.get("stdout") or "") + (run.get("stderr") or ""))[:4000]
        violations: list[dict[str, Any]] = []
        if "ruff" in cmd_id.lower() or "ruff" in " ".join(cmd):
            violations = parse_ruff_violations(excerpt)
        elif "eslint" in cmd_id.lower() or "eslint" in " ".join(cmd):
            violations = parse_eslint_violations(excerpt)
        step_ok = bool(run.get("ok"))
        if not step_ok:
            overall_ok = False
        entry = {
            "id": cmd_id,
            "ok": step_ok,
            "exit_code": run.get("exit_code"),
            "command": run.get("command"),
            "elapsed_ms": elapsed_ms,
            "violations": violations,
            "excerpt": excerpt if not violations else "",
        }
        if run.get("error"):
            entry["error"] = run["error"]
        results.append(entry)
        if not step_ok and fail_fast:
            break

    return {"ok": overall_ok, "results": results}
