"""Failure streak aggregation and suspect marking (GOVERNANCE §6, TASKS T-602c)."""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from governance.entities import scan_memory_records, scan_tool_records
from paths import AgentPaths
from tools.logging import (
    EVENT_FEEDBACK_NEGATIVE,
    EVENT_FEEDBACK_POSITIVE,
    EVENT_MARKED_SUSPECT,
    EvolveLog,
    read_events,
)

FAILURE_STREAK_THRESHOLD = 3
_ARCHIVED = frozenset({"archived"})


def compute_failure_streak(log_path: Path, entity_id: str) -> int:
    """Aggregate consecutive ``feedback_negative`` count from evolve_log (resets on positive)."""
    needle = entity_id.strip()
    if not needle:
        return 0

    streak = 0
    for event in read_events(log_path):
        if str(event.get("entity_id", "")).strip() != needle:
            continue
        event_type = event.get("event")
        if event_type == EVENT_FEEDBACK_POSITIVE:
            streak = 0
        elif event_type == EVENT_FEEDBACK_NEGATIVE:
            streak += 1
    return streak


def locate_entity(paths: AgentPaths, entity_id: str) -> tuple[str, Path] | None:
    """Return ``(entity_type, file_path)`` for a memory markdown or tool manifest."""
    needle = entity_id.strip()
    if not needle:
        return None

    evolve = paths.evolve
    for record in scan_memory_records(evolve):
        if record.memory_id == needle:
            return ("memory", evolve / record.path)

    for record in scan_tool_records(evolve):
        if record.name == needle:
            return ("tool", evolve / record.path / "tool.toml")

    return None


def read_entity_status(paths: AgentPaths, entity_id: str) -> str | None:
    located = locate_entity(paths, entity_id)
    if located is None:
        return None
    entity_type, path = located
    if entity_type == "memory":
        return _read_memory_status(path)
    return _read_tool_status(path)


def set_memory_status(path: Path, status: str) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if not text.startswith("---"):
        return False
    match = re.match(r"^(---\s*\n)(.*?)(\n---\s*\n)", text, flags=re.DOTALL)
    if not match:
        return False
    block = match.group(2)
    if re.search(r"^status:\s*", block, flags=re.MULTILINE):
        block = re.sub(
            r"^status:\s*.+$",
            f"status: {status}",
            block,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        block = f"status: {status}\n{block}"
    updated = f"{match.group(1)}{block}{match.group(3)}{text[match.end() :]}"
    try:
        path.write_text(updated, encoding="utf-8")
    except OSError:
        return False
    return True


def set_tool_status(manifest_path: Path, status: str) -> bool:
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return False
    if not re.search(r'^status\s*=\s*"[^"]*"', text, flags=re.MULTILINE):
        return False
    updated = re.sub(
        r'^(status\s*=\s*)"[^"]*"',
        rf'\1"{status}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if updated == text:
        return False
    try:
        manifest_path.write_text(updated, encoding="utf-8")
    except OSError:
        return False
    return True


def mark_entity_suspect(paths: AgentPaths, entity_id: str) -> bool:
    """Write ``status: suspect`` to the entity file. Returns True when updated."""
    located = locate_entity(paths, entity_id)
    if located is None:
        return False

    entity_type, path = located
    current = _read_memory_status(path) if entity_type == "memory" else _read_tool_status(path)
    if current is None or current == "suspect" or current in _ARCHIVED:
        return False

    if entity_type == "memory":
        return set_memory_status(path, "suspect")
    return set_tool_status(path, "suspect")


def process_feedback_suspect(
    paths: AgentPaths,
    entity_id: str,
    *,
    evolve_log: EvolveLog,
) -> int:
    """After ``feedback_negative``: check streak; mark suspect + log when threshold reached."""
    streak = compute_failure_streak(evolve_log.path, entity_id)
    if streak < FAILURE_STREAK_THRESHOLD:
        return streak

    if mark_entity_suspect(paths, entity_id):
        evolve_log.log_marked_suspect(entity_id=entity_id, failure_streak=streak)
    return streak


def _read_memory_status(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    frontmatter = _parse_frontmatter(text)
    if not frontmatter:
        return None
    return str(frontmatter.get("status", "active")).strip().lower()


def _read_tool_status(manifest_path: Path) -> str | None:
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'^status\s*=\s*"([^"]*)"', text, flags=re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip().lower()


def _parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, flags=re.DOTALL)
    if not match:
        return {}
    result: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, raw_value = stripped.split(":", 1)
        result[key.strip()] = raw_value.strip()
    return result


def _demo() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        evolve = root / "evolve"
        evolve.mkdir()
        (evolve / "_index.core.toml").write_text('[[topic]]\nid = "workflow"\n', encoding="utf-8")
        mem_path = evolve / "memories" / "workflow" / "streak-demo.md"
        mem_path.parent.mkdir(parents=True)
        mem_path.write_text(
            "---\n"
            "id: streak-demo\n"
            "topics: [workflow]\n"
            "status: active\n"
            "summary: failure streak demo memory\n"
            "---\n\n"
            "## body\n",
            encoding="utf-8",
        )
        tool_dir = evolve / "tools" / "workflow" / "streak_tool"
        tool_dir.mkdir(parents=True)
        manifest = tool_dir / "tool.toml"
        manifest.write_text(
            '[tool]\n'
            'name = "streak_tool"\n'
            'description = "demo"\n'
            'version = "1.0.0"\n'
            'status = "active"\n'
            'topics = ["workflow"]\n\n'
            '[entry]\n'
            'type = "python"\n'
            'path = "main.py"\n\n'
            '[schema.input]\n'
            'type = "object"\n\n'
            '[schema.output]\n'
            'type = "object"\n\n'
            '[policy]\n'
            'confirm = true\n'
            'dry_run_supported = true\n'
            'workspace_only = true\n'
            'timeout_sec = 60\n',
            encoding="utf-8",
        )
        (tool_dir / "main.py").write_text("print('ok')\n", encoding="utf-8")
        (root / "workspace").mkdir()
        (root / "data").mkdir()
        paths = AgentPaths.from_root(root)
        log_path = paths.data / "evolve_log.jsonl"
        log = EvolveLog(log_path)

        log.log_feedback_negative(entity_id="streak-demo", conversation_id="_t602c")
        log.log_feedback_positive(entity_id="streak-demo", conversation_id="_t602c")
        log.log_feedback_negative(entity_id="streak-demo", conversation_id="_t602c")
        assert compute_failure_streak(log_path, "streak-demo") == 1
        print("[PASS] T-602c: positive feedback resets failure_streak")

        log.log_feedback_negative(entity_id="streak-demo", conversation_id="_t602c")
        assert compute_failure_streak(log_path, "streak-demo") == 2
        streak = process_feedback_suspect(paths, "streak-demo", evolve_log=log)
        assert streak == 2
        assert "status: suspect" not in mem_path.read_text(encoding="utf-8")
        assert not any(event.get("event") == EVENT_MARKED_SUSPECT for event in read_events(log_path))
        print("[PASS] T-602c: streak 2 does not mark suspect")

        log.log_feedback_negative(entity_id="streak-demo", conversation_id="_t602c")
        assert compute_failure_streak(log_path, "streak-demo") == 3
        streak = process_feedback_suspect(paths, "streak-demo", evolve_log=log)
        assert streak == 3
        assert "status: suspect" in mem_path.read_text(encoding="utf-8")
        marked = [event for event in read_events(log_path) if event.get("event") == EVENT_MARKED_SUSPECT]
        assert len(marked) == 1 and marked[0]["failure_streak"] == 3
        print("[PASS] T-602c: streak 3 marks memory suspect + marked_suspect log")

        streak_repeat = process_feedback_suspect(paths, "streak-demo", evolve_log=log)
        assert streak_repeat == 3
        assert sum(1 for event in read_events(log_path) if event.get("event") == EVENT_MARKED_SUSPECT) == 1
        print("[PASS] T-602c: idempotent when already suspect")

        for _ in range(3):
            log.log_feedback_negative(entity_id="streak_tool", conversation_id="_t602c_tool")
        process_feedback_suspect(paths, "streak_tool", evolve_log=log)
        assert 'status = "suspect"' in manifest.read_text(encoding="utf-8")
        print("[PASS] T-602c: streak 3 marks tool.toml suspect")


if __name__ == "__main__":
    _demo()
