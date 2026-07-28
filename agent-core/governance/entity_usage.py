"""Record L2 memory entity_used on read_file (GOVERNANCE §3.1, TASKS T-602a)."""

from __future__ import annotations

import json
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from loader import copy_evolve_index_files
from paths import AgentPaths
from tools.logging import EvolveLog, utc_now_iso

_META_FILENAME = "meta.json"
_MEMORIES_PREFIX = "memories/"
_ENTITY_TYPE_MEMORY = "memory"
_LEVEL_L2 = "L2"


@dataclass(frozen=True, slots=True)
class MemoryEntityHit:
    entity_id: str
    evolve_rel_path: str
    absolute_path: Path


def normalize_evolve_relative_path(path_value: str) -> str | None:
    """Normalize read_file path to evolve-relative form (e.g. memories/foo.md)."""
    normalized = path_value.strip().replace("\\", "/")
    if not normalized:
        return None
    for prefix in ("evolve/", "./evolve/"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    return normalized or None


def is_memory_l2_evolve_path(evolve_rel: str) -> bool:
    return evolve_rel.startswith(_MEMORIES_PREFIX) and evolve_rel.endswith(".md")


def resolve_memory_entity(paths: AgentPaths, path_value: str) -> MemoryEntityHit | None:
    """Return memory entity when *path_value* points at evolve/memories/**/*.md."""
    evolve_rel = normalize_evolve_relative_path(path_value)
    if evolve_rel is None or not is_memory_l2_evolve_path(evolve_rel):
        return None

    absolute = paths.evolve / evolve_rel
    if not absolute.is_file():
        return None

    frontmatter = _parse_frontmatter(absolute.read_text(encoding="utf-8"))
    memory_id = frontmatter.get("id")
    if not isinstance(memory_id, str) or not memory_id.strip():
        return None

    return MemoryEntityHit(
        entity_id=memory_id.strip(),
        evolve_rel_path=evolve_rel,
        absolute_path=absolute,
    )


def record_memory_entity_used(
    *,
    paths: AgentPaths,
    path_value: str,
    evolve_log: EvolveLog | None,
    session_dir: Path | None,
    conversation_id: str | None = None,
    used_at: str | None = None,
) -> MemoryEntityHit | None:
    """Log entity_used (L2), bump memory frontmatter, update session pending_feedback."""
    hit = resolve_memory_entity(paths, path_value)
    if hit is None:
        return None

    ts = used_at or utc_now_iso()
    reason = f"read_file:{hit.evolve_rel_path}"

    if evolve_log is not None:
        evolve_log.log_entity_used(
            entity_id=hit.entity_id,
            entity_type=_ENTITY_TYPE_MEMORY,
            level=_LEVEL_L2,
            reason=reason,
            conversation_id=conversation_id,
        )

    bump_memory_usage_file(hit.absolute_path, used_at=ts)
    upsert_session_pending_feedback(
        session_dir,
        entity_id=hit.entity_id,
        entity_type=_ENTITY_TYPE_MEMORY,
        level=_LEVEL_L2,
        used_at=ts,
    )
    return hit


def bump_memory_usage_file(path: Path, *, used_at: str) -> bool:
    """Increment ``use_count`` and set ``last_used_at`` in memory frontmatter."""
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
    current = _parse_frontmatter(text).get("use_count", 0)
    use_count = _parse_int(current) + 1
    block = _set_frontmatter_scalar(block, "use_count", str(use_count))
    block = _set_frontmatter_scalar(block, "last_used_at", _quote_yaml(used_at))
    updated = f"{match.group(1)}{block}{match.group(3)}{text[match.end() :]}"

    try:
        path.write_text(updated, encoding="utf-8")
    except OSError:
        return False
    return True


def upsert_session_pending_feedback(
    session_dir: Path | None,
    *,
    entity_id: str,
    entity_type: str,
    level: str,
    used_at: str,
) -> None:
    """Maintain meta.json ``pending_feedback`` (RUNTIME §10.2); dedupe by entity_id."""
    if session_dir is None:
        return

    session_dir.mkdir(parents=True, exist_ok=True)
    meta_path = session_dir / _META_FILENAME
    payload: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, json.JSONDecodeError):
            payload = {}

    raw_pending = payload.get("pending_feedback", [])
    pending: list[dict[str, Any]] = (
        [item for item in raw_pending if isinstance(item, dict)] if isinstance(raw_pending, list) else []
    )
    pending = [item for item in pending if str(item.get("entity_id", "")).strip() != entity_id]
    pending.append(
        {
            "entity_id": entity_id,
            "type": entity_type,
            "level": level,
            "used_at": used_at,
        }
    )
    payload["pending_feedback"] = pending
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
        key = key.strip()
        value = raw_value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            result[key] = [item.strip() for item in inner.split(",") if item.strip()] if inner else []
        else:
            result[key] = value
    return result


def _parse_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def _quote_yaml(value: str) -> str:
    if not value:
        return '""'
    if any(ch in value for ch in ':"\'\n#'):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _set_frontmatter_scalar(block: str, key: str, value: str) -> str:
    line = f"{key}: {value}"
    if re.search(rf"^{re.escape(key)}:\s*", block, flags=re.MULTILINE):
        return re.sub(
            rf"^{re.escape(key)}:\s*.+$",
            line,
            block,
            count=1,
            flags=re.MULTILINE,
        )
    return block.rstrip() + "\n" + line


def _demo() -> None:
    import shutil

    from tools.executor import ExecutorSession, ToolExecutor
    from tools.logging import read_events
    from tools.registry import ToolRegistry

    paths = AgentPaths.discover()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        evolve = root / "evolve"
        evolve.mkdir()
        copy_evolve_index_files(paths.evolve, evolve)
        mem_path = evolve / "memories" / "workflow" / "entity-usage-demo.md"
        mem_path.parent.mkdir(parents=True)
        mem_path.write_text(
            "---\n"
            "id: entity-usage-demo\n"
            "topics: [workflow]\n"
            "status: active\n"
            "summary: governance entity_usage demo\n"
            "use_count: 2\n"
            "---\n\n"
            "## body\n",
            encoding="utf-8",
        )
        (root / "workspace").mkdir()
        (root / "data").mkdir()
        session_dir = root / "data" / "sessions" / "_entity_usage_demo"
        session_dir.mkdir(parents=True)
        agent_paths = AgentPaths.from_root(root)
        log_path = root / "data" / "evolve_log.jsonl"

        hit = resolve_memory_entity(agent_paths, "evolve/memories/workflow/entity-usage-demo.md")
        assert hit is not None and hit.entity_id == "entity-usage-demo"
        assert resolve_memory_entity(agent_paths, "evolve/prompts/workflow.md") is None
        print("[PASS] T-602a: resolve_memory_entity L2 path only")

        recorded = record_memory_entity_used(
            paths=agent_paths,
            path_value="evolve/memories/workflow/entity-usage-demo.md",
            evolve_log=EvolveLog(log_path),
            session_dir=session_dir,
            conversation_id="_entity_usage_demo",
        )
        assert recorded is not None
        events = read_events(log_path)
        assert len(events) == 1 and events[0]["event"] == "entity_used"
        assert events[0]["entity_id"] == "entity-usage-demo"
        text = mem_path.read_text(encoding="utf-8")
        assert "use_count: 3" in text
        meta = json.loads((session_dir / "meta.json").read_text(encoding="utf-8"))
        assert meta["pending_feedback"][0]["entity_id"] == "entity-usage-demo"
        print("[PASS] T-602a: record_memory_entity_used log + frontmatter + pending_feedback")

        executor = ToolExecutor(
            registry=ToolRegistry.load(agent_paths),
            session=ExecutorSession(session_dir=session_dir),
            evolve_log=EvolveLog(log_path),
        )
        result = executor.run("read_file", {"path": "evolve/memories/workflow/entity-usage-demo.md"})
        assert result.ok
        entity_events = [event for event in read_events(log_path) if event.get("event") == "entity_used"]
        assert len(entity_events) == 2
        assert "use_count: 4" in mem_path.read_text(encoding="utf-8")
        print("[PASS] T-602a: ToolExecutor read_file triggers entity_used hook")


if __name__ == "__main__":
    _demo()
