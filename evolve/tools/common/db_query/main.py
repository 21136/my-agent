"""db_query — SQLite file query (Phase 26 M2).

Readonly by default; write=true allows DML/DDL with confirm (executor).
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

_MAX_ROWS = 500
_DEFAULT_ROWS = 100
_MAX_CELL_CHARS = 2_000
_ALLOWED_SUFFIX = {".sqlite", ".db", ".sqlite3"}
_READ_HEAD = re.compile(
    r"^\s*(WITH\b[\s\S]+?\bSELECT\b|SELECT\b|PRAGMA\b|EXPLAIN\b)",
    re.IGNORECASE,
)
_FORBIDDEN = re.compile(
    r"\b(ATTACH|DETACH|VACUUM|REINDEX|LOAD_EXTENSION)\b",
    re.IGNORECASE,
)


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


def _is_read_sql(sql: str) -> bool:
    text = sql.strip()
    if not text:
        return False
    if ";" in text.rstrip(";"):
        return False  # multi-statement
    if _FORBIDDEN.search(text):
        return False
    return _READ_HEAD.match(text) is not None


def _truncate_cell(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _MAX_CELL_CHARS:
        return value[:_MAX_CELL_CHARS] + "…(truncated)"
    if isinstance(value, (bytes, bytearray)):
        return f"<blob {len(value)} bytes>"
    return value


def db_query(payload: dict[str, Any]) -> dict[str, Any]:
    db_path_raw = payload.get("db_path")
    sql = payload.get("sql")
    if not isinstance(db_path_raw, str) or not db_path_raw.strip():
        return {"ok": False, "error": "db_path is required"}
    if not isinstance(sql, str) or not sql.strip():
        return {"ok": False, "error": "sql is required"}
    sql = sql.strip()

    write = bool(payload.get("write", False))
    readonly = bool(payload.get("readonly", True))
    if write:
        readonly = False

    max_rows = payload.get("max_rows", _DEFAULT_ROWS)
    try:
        max_rows = int(max_rows)
    except (TypeError, ValueError):
        return {"ok": False, "error": "max_rows must be an integer"}
    max_rows = max(1, min(max_rows, _MAX_ROWS))

    if ";" in sql.rstrip(";"):
        return {"ok": False, "error": "multiple SQL statements are not allowed"}
    if _FORBIDDEN.search(sql):
        return {"ok": False, "error": "ATTACH/DETACH/VACUUM/extension SQL is forbidden"}

    if readonly and not _is_read_sql(sql):
        return {
            "ok": False,
            "error": "readonly mode only allows SELECT / WITH…SELECT / PRAGMA / EXPLAIN",
        }

    AgentPaths = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())
    text = db_path_raw.strip().replace("\\", "/").lstrip("/")
    try:
        db_file = paths.resolve_under_agent(text, must_exist=True)
    except Exception as exc:
        if not text.startswith("workspace/"):
            try:
                db_file = paths.resolve_under_agent(f"workspace/{text}", must_exist=True)
            except Exception:
                return {"ok": False, "error": f"db_path not found or out of bounds: {db_path_raw} ({exc})"}
        else:
            return {"ok": False, "error": f"db_path not found or out of bounds: {db_path_raw} ({exc})"}

    if db_file.suffix.lower() not in _ALLOWED_SUFFIX:
        return {
            "ok": False,
            "error": f"db_path suffix must be one of {sorted(_ALLOWED_SUFFIX)}",
        }
    if not db_file.is_file():
        return {"ok": False, "error": f"not a file: {db_path_raw}"}

    try:
        rel = str(db_file.resolve().relative_to(paths.agent_root.resolve())).replace("\\", "/")
    except ValueError:
        return {"ok": False, "error": "db_path escaped agent root"}

    uri = "file:" + quote(db_file.resolve().as_posix())
    if readonly:
        uri += "?mode=ro"

    try:
        conn = sqlite3.connect(uri, uri=True, timeout=10)
    except sqlite3.Error as exc:
        return {"ok": False, "error": f"open failed: {exc}", "db_path": rel}

    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql)
        if cur.description is None:
            # DML/DDL
            conn.commit()
            return {
                "ok": True,
                "db_path": rel,
                "readonly": readonly,
                "rowcount": cur.rowcount,
                "columns": [],
                "rows": [],
            }
        cols = [d[0] for d in cur.description]
        raw_rows = cur.fetchmany(max_rows + 1)
        truncated = len(raw_rows) > max_rows
        raw_rows = raw_rows[:max_rows]
        rows = [[_truncate_cell(r[c]) for c in cols] for r in raw_rows]
        if not readonly:
            conn.commit()
        return {
            "ok": True,
            "db_path": rel,
            "readonly": readonly,
            "columns": cols,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
        }
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc), "db_path": rel, "readonly": readonly}
    finally:
        conn.close()


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main

    run_tool_main(db_query)


if __name__ == "__main__":
    main()
