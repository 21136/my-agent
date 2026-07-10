"""Assemble system prompt: base layer + session overlay (RUNTIME.md §4, TASKS T-204)."""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from session import Session, SessionMeta, utc_now_iso
from tools.logging import (
    EVENT_SESSION_START,
    EVENT_TOPICS_CONFIRMED,
    EvolveLog,
    read_events,
)
from tools.registry import BUILTIN_TOOLS, ToolRegistry

SECTION_SEPARATOR = "\n---\n"
CORE_REL = Path("prompts") / "core.txt"
INDEX_REL = Path("_index.toml")
SAFETY_REL = Path("prompts") / "safety.md"
MEMORIES_DIRNAME = "memories"
PROMPTS_DIRNAME = "prompts"

_ARCHIVED_STATUS = frozenset({"archived"})


@dataclass(frozen=True, slots=True)
class TopicIndexEntry:
    id: str
    name: str
    description: str
    prompt: str
    memory_dirs: tuple[str, ...]
    tool_dirs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MemoryIndexEntry:
    memory_id: str
    topics: tuple[str, ...]
    summary: str
    path: str


@dataclass(frozen=True, slots=True)
class LoadedSystem:
    """Assembled system prompt with traceable section order."""

    prompt: str
    section_names: tuple[str, ...]


def core_prompt_path(agent_core_dir: Path | None = None) -> Path:
    base = agent_core_dir or _AGENT_CORE
    return base / CORE_REL


def load_core_text(*, agent_core_dir: Path | None = None) -> str:
    path = core_prompt_path(agent_core_dir)
    if not path.is_file():
        return "[core.txt missing — implement T-209]"
    return path.read_text(encoding="utf-8").strip()


def load_topic_index(evolve_dir: Path) -> list[TopicIndexEntry]:
    index_path = evolve_dir / INDEX_REL
    if not index_path.is_file():
        return []
    try:
        payload = tomllib.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []

    topics_raw = payload.get("topic")
    if not isinstance(topics_raw, list):
        return []

    entries: list[TopicIndexEntry] = []
    for item in topics_raw:
        if not isinstance(item, dict):
            continue
        topic_id = item.get("id")
        name = item.get("name")
        description = item.get("description")
        prompt = item.get("prompt")
        if not all(isinstance(value, str) and value.strip() for value in (topic_id, name, description, prompt)):
            continue
        memory_dirs = _as_str_tuple(item.get("memory_dirs"))
        tool_dirs = _as_str_tuple(item.get("tool_dirs"))
        entries.append(
            TopicIndexEntry(
                id=topic_id.strip(),
                name=name.strip(),
                description=description.strip(),
                prompt=prompt.strip(),
                memory_dirs=memory_dirs,
                tool_dirs=tool_dirs,
            )
        )
    return entries


def scan_memory_index(evolve_dir: Path) -> list[MemoryIndexEntry]:
    """Scan ``evolve/memories/**/*.md``; inject active/suspect id+summary (MEMORY §5)."""
    memories_root = evolve_dir / MEMORIES_DIRNAME
    if not memories_root.is_dir():
        return []

    entries: list[MemoryIndexEntry] = []
    for path in sorted(memories_root.rglob("*.md")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        frontmatter = _parse_frontmatter(text)
        if not frontmatter:
            continue
        status = str(frontmatter.get("status", "active")).strip().lower()
        if status in _ARCHIVED_STATUS:
            continue
        memory_id = frontmatter.get("id")
        summary = frontmatter.get("summary")
        if not isinstance(memory_id, str) or not memory_id.strip():
            continue
        if not isinstance(summary, str) or not summary.strip():
            continue
        topics = _frontmatter_topics(frontmatter.get("topics"))
        rel_path = path.relative_to(evolve_dir).as_posix()
        entries.append(
            MemoryIndexEntry(
                memory_id=memory_id.strip(),
                topics=topics,
                summary=summary.strip(),
                path=rel_path,
            )
        )
    entries.sort(key=lambda entry: (entry.memory_id, entry.path))
    return entries


def format_topic_index(entries: list[TopicIndexEntry]) -> str:
    """Render ``evolve/_index.toml`` for S0 injection (MEMORY §4.1, RUNTIME §4.1)."""
    lines = ["[主题索引]"]
    if not entries:
        lines.append("(none registered)")
        return "\n".join(lines)
    for entry in entries:
        lines.append(f"- {entry.id}: {entry.name} — {entry.description}")
        tool_dirs_label = ", ".join(entry.tool_dirs) if entry.tool_dirs else "(none)"
        lines.append(f"  tool_dirs: {tool_dirs_label}")
    return "\n".join(lines)


def format_memory_index(entries: list[MemoryIndexEntry]) -> str:
    """Render global memory index for S0 injection (MEMORY §5.2)."""
    lines = ["[久远记忆]"]
    if not entries:
        lines.append("(none active)")
        return "\n".join(lines)
    for entry in entries:
        topic_label = ", ".join(entry.topics) if entry.topics else "?"
        lines.append(f"- {entry.memory_id} ({topic_label}): {entry.summary}")
    return "\n".join(lines)


def format_builtin_summary() -> str:
    lines = [
        "[Builtin 工具]",
        "恒为 6 个 function；evolved 工具经 run_evolved 调用，见本会话清单：",
    ]
    for tool in BUILTIN_TOOLS:
        lines.append(f"- {tool.name}: {tool.description}")
    return "\n".join(lines)


def format_session_overlay(session: Session) -> str:
    topics = ", ".join(session.meta.topics) if session.meta.topics else "(none)"
    goal = session.goal.strip() or "(unset)"
    return "\n".join(
        [
            "[本次会议]",
            f"conversation_id: {session.conversation_id}",
            f"goal: {goal}",
            f"topics: {topics}",
            f"llm_model: {session.meta.llm_model}",
        ]
    )


def format_evolve_escalation_hint(session: Session) -> str | None:
    """EVOLVE §3.2: at most one proactive oral offer per session (T-403)."""
    if session.meta.evolve_offer_used or session.meta.phase != "S4":
        return None
    return "\n".join(
        [
            "[进化升格 · EVOLVE §3.2]",
            "本会话尚未主动提议固化。若发现值得长期保留的规则或事实，可口头问用户是否写入 evolve（每会话最多一次）；",
            "禁止直接声称已写入或生成 proposal 文件。用户确认后由其弱确认（好/要/写进去/对）或说「记住」触发检查点。",
        ]
    )


def load_safety_prompt(evolve_dir: Path) -> str:
    path = evolve_dir / SAFETY_REL
    if not path.is_file():
        return "[safety.md missing at evolve/prompts/safety.md]"
    return path.read_text(encoding="utf-8").strip()


def load_topic_prompt(
    evolve_dir: Path,
    topic_id: str,
    *,
    index: list[TopicIndexEntry] | None = None,
) -> str | None:
    """Load one topic prompt file; path from ``_index.toml`` ``prompt`` field."""
    path = resolve_topic_prompt_path(evolve_dir, topic_id, index=index)
    if path is None or not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip()


def resolve_topic_prompt_path(
    evolve_dir: Path,
    topic_id: str,
    *,
    index: list[TopicIndexEntry] | None = None,
) -> Path | None:
    """Map topic id → ``evolve/`` relative prompt path (MEMORY §3.2)."""
    entries = index if index is not None else load_topic_index(evolve_dir)
    for entry in entries:
        if entry.id == topic_id:
            return evolve_dir / Path(entry.prompt)
    return evolve_dir / PROMPTS_DIRNAME / f"{topic_id}.md"


def load_confirmed_topic_prompts(
    evolve_dir: Path,
    topic_ids: list[str] | tuple[str, ...],
    *,
    index: list[TopicIndexEntry] | None = None,
) -> list[tuple[str, str]]:
    """S3 overlay: full prompt text per confirmed topic (MEMORY §4.3, RUNTIME §4.2.7)."""
    entries = index if index is not None else load_topic_index(evolve_dir)
    by_id = {entry.id: entry for entry in entries}
    sections: list[tuple[str, str]] = []
    for topic_id in topic_ids:
        if topic_id == "safety":
            continue
        entry = by_id.get(topic_id)
        if entry is None:
            continue
        path = evolve_dir / Path(entry.prompt)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            sections.append((topic_id, text))
    return sections


def session_start_log_fields(session: Session) -> dict[str, Any]:
    """Fields for ``session_start`` evolve_log event (MEMORY §8)."""
    evolve_dir = session.paths.evolve
    return {
        "conversation_id": session.conversation_id,
        "memory_ids_loaded": [entry.memory_id for entry in scan_memory_index(evolve_dir)],
        "topics_available": [entry.id for entry in load_topic_index(evolve_dir)],
    }


def topics_confirmed_log_fields(
    session: Session,
    *,
    registry: ToolRegistry | None = None,
) -> dict[str, Any]:
    """Fields for ``topics_confirmed`` evolve_log event (MEMORY §8)."""
    evolve_dir = session.paths.evolve
    topic_index = load_topic_index(evolve_dir)
    by_id = {entry.id: entry for entry in topic_index}
    prompt_files: list[str] = []
    for topic_id, _ in load_confirmed_topic_prompts(
        evolve_dir, session.meta.topics, index=topic_index
    ):
        entry = by_id.get(topic_id)
        if entry is not None:
            prompt_files.append(entry.prompt)
    reg = registry or ToolRegistry.load(session.paths)
    evolved_tools = sorted(session_evolved_allowlist(session, registry=reg))
    return {
        "conversation_id": session.conversation_id,
        "topics_confirmed": list(session.meta.topics),
        "prompt_files_loaded": prompt_files,
        "evolved_tools_listed": evolved_tools,
    }


def log_session_start(
    session: Session,
    *,
    evolve_log: EvolveLog | None = None,
) -> None:
    """Append S0 session startup line to ``data/evolve_log.jsonl`` (T-306)."""
    log = evolve_log or EvolveLog.for_agent(session.paths)
    fields = session_start_log_fields(session)
    log.log_session_start(
        conversation_id=fields["conversation_id"],
        memory_ids_loaded=fields["memory_ids_loaded"],
        topics_available=fields["topics_available"],
    )


def log_topics_confirmed(
    session: Session,
    *,
    registry: ToolRegistry | None = None,
    evolve_log: EvolveLog | None = None,
) -> None:
    """Append S3 topic confirmation line to ``data/evolve_log.jsonl`` (T-306)."""
    log = evolve_log or EvolveLog.for_agent(session.paths)
    fields = topics_confirmed_log_fields(session, registry=registry)
    log.log_topics_confirmed(
        conversation_id=fields["conversation_id"],
        topics_confirmed=fields["topics_confirmed"],
        prompt_files_loaded=fields["prompt_files_loaded"],
        evolved_tools_listed=fields["evolved_tools_listed"],
    )


def format_session_evolved_catalog(
    topics: list[str],
    *,
    registry: ToolRegistry | None = None,
) -> str:
    """Human-readable evolved catalog for system overlay (TOOLS.md §4.3, T-308)."""
    reg = registry or ToolRegistry.load()
    session_tools = reg.session_evolved(topics)

    by_scope: dict[str, list[str]] = {}
    for tool in session_tools:
        label = f"- {tool.name}: {tool.description}"
        by_scope.setdefault(tool.scope, []).append(label)

    lines = [
        "[本会话可用 evolved 工具]（调用 run_evolved.tool_name）",
    ]
    for scope in ("common", *sorted(key for key in by_scope if key != "common")):
        if scope not in by_scope:
            continue
        heading = "始终" if scope == "common" else "本会话主题"
        lines.append(f"## {scope}（{heading}）")
        lines.extend(by_scope[scope])
    if len(session_tools) == 0:
        lines.append("(none active for current topics)")
    return "\n".join(lines)


def session_evolved_allowlist(
    session: Session,
    *,
    registry: ToolRegistry | None = None,
) -> frozenset[str]:
    """Allowed ``run_evolved.tool_name`` values for this session (common + topic scopes)."""
    reg = registry or ToolRegistry.load(session.paths)
    return frozenset(tool.name for tool in reg.session_evolved(session.meta.topics))


def format_evolved_catalog_overlay(
    session: Session,
    *,
    registry: ToolRegistry | None = None,
) -> str:
    """Evolved catalog section for §4.2 overlay after topics are confirmed."""
    reg = registry or ToolRegistry.load(session.paths)
    return format_session_evolved_catalog(session.meta.topics, registry=reg)


def load_digest(session: Session) -> str | None:
    if not session.digest_path.is_file():
        return None
    text = session.digest_path.read_text(encoding="utf-8").strip()
    return text or None


def build_system_prompt(
    session: Session,
    *,
    paths: AgentPaths | None = None,
    registry: ToolRegistry | None = None,
    include_overlay: bool = True,
    agent_core_dir: Path | None = None,
) -> LoadedSystem:
    """Build single system string (§4.1 base + optional §4.2 overlay)."""
    agent_paths = paths or session.paths
    reg = registry or ToolRegistry.load(agent_paths)
    evolve_dir = agent_paths.evolve
    topic_index = load_topic_index(evolve_dir)

    sections: list[tuple[str, str]] = [
        ("core", load_core_text(agent_core_dir=agent_core_dir)),
        ("topic_index", format_topic_index(topic_index)),
        ("memory_index", format_memory_index(scan_memory_index(evolve_dir))),
        ("builtin_summary", format_builtin_summary()),
    ]

    if include_overlay:
        sections.append(("session", format_session_overlay(session)))
        sections.append(("safety", load_safety_prompt(evolve_dir)))

        for topic_id, prompt_text in load_confirmed_topic_prompts(
            evolve_dir, session.meta.topics, index=topic_index
        ):
            sections.append((f"topic_prompt:{topic_id}", prompt_text))

        sections.append(
            (
                "evolved_catalog",
                format_evolved_catalog_overlay(session, registry=reg),
            )
        )

        escalation = format_evolve_escalation_hint(session)
        if escalation:
            sections.append(("evolve_escalation", escalation))

        digest = load_digest(session)
        if digest:
            sections.append(("digest", f"[对话摘要 digest]\n{digest}"))

    non_empty = [(name, text.strip()) for name, text in sections if text.strip()]
    prompt = SECTION_SEPARATOR.join(text for _, text in non_empty)
    return LoadedSystem(
        prompt=prompt,
        section_names=tuple(name for name, _ in non_empty),
    )


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, flags=re.DOTALL)
    if not match:
        return {}
    block = match.group(1)
    result: dict[str, Any] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
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


def _frontmatter_topics(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    return ()


def _install_demo_evolved_tool(
    tool_dir: Path,
    *,
    name: str,
    topics: list[str],
    description: str,
) -> None:
    tool_dir.mkdir(parents=True, exist_ok=True)
    (tool_dir / "main.py").write_text(
        "import json\nprint(json.dumps({'ok': True}))\n",
        encoding="utf-8",
    )
    topics_literal = ", ".join(f'"{topic}"' for topic in topics)
    (tool_dir / "tool.toml").write_text(
        f"""[tool]
name = "{name}"
description = "{description}"
version = "1.0.0"
status = "active"
topics = [{topics_literal}]

[entry]
type = "python"
path = "main.py"

[schema.input]
type = "object"
required = []

[schema.output]
type = "object"

[policy]
confirm = true
dry_run_supported = true
workspace_only = true
timeout_sec = 60
""",
        encoding="utf-8",
    )


def _demo() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        agent_core = root / "agent-core"
        (agent_core / "prompts").mkdir(parents=True)
        (agent_core / "prompts" / "core.txt").write_text(
            "CORE-STUB: identity and boundaries.",
            encoding="utf-8",
        )

        evolve = root / "evolve"
        (evolve / "prompts").mkdir(parents=True)
        (evolve / "prompts" / "safety.md").write_text(
            "SAFETY-STUB: always-on rules.",
            encoding="utf-8",
        )
        (evolve / "prompts" / "workflow.md").write_text(
            "# Workflow topic\nPrefer small steps.",
            encoding="utf-8",
        )
        (evolve / "prompts" / "coding.md").write_text(
            "# Coding topic\nUse Python 3.12+; read docs before coding.",
            encoding="utf-8",
        )
        (evolve / "memories" / "workflow").mkdir(parents=True)
        (evolve / "memories" / "workflow" / "demo.md").write_text(
            "---\nid: demo-memory\ntopics: [workflow]\nstatus: active\n"
            "summary: Demo memory entry\n---\n\nBody.",
            encoding="utf-8",
        )
        (evolve / "memories" / "coding").mkdir(parents=True)
        (evolve / "memories" / "coding" / "archived.md").write_text(
            "---\nid: old-fact\ntopics: [coding]\nstatus: archived\n"
            "summary: Should not appear\n---\n\nArchived body.",
            encoding="utf-8",
        )
        (evolve / "memories" / "coding" / "suspect.md").write_text(
            "---\nid: suspect-fact\ntopics: [coding]\nstatus: suspect\n"
            "summary: Suspect but still indexed\n---\n\nSuspect body.",
            encoding="utf-8",
        )
        (evolve / "memories" / "workflow" / "multi.md").write_text(
            "---\nid: multi-topic\ntopics: [coding, workflow]\nstatus: active\n"
            "summary: Spans two topics\n---\n\nBody.",
            encoding="utf-8",
        )
        (evolve / "memories" / "workflow" / "no-frontmatter.md").write_text(
            "# Not a memory file\nNo YAML header.",
            encoding="utf-8",
        )
        (evolve / "memories" / "workflow" / "missing-id.md").write_text(
            "---\ntopics: [workflow]\nstatus: active\nsummary: no id field\n---\n",
            encoding="utf-8",
        )
        (evolve / INDEX_REL).write_text(
            '[[topic]]\nid = "workflow"\nname = "工作流"\n'
            'description = "整理与重复任务"\n'
            'prompt = "prompts/workflow.md"\n'
            'memory_dirs = ["memories/workflow"]\n'
            'tool_dirs = ["tools/workflow"]\n\n'
            '[[topic]]\nid = "coding"\nname = "开发"\n'
            'description = "Python 开发"\n'
            'prompt = "prompts/coding.md"\n'
            'memory_dirs = ["memories/coding"]\n'
            'tool_dirs = ["tools/coding"]\n',
            encoding="utf-8",
        )
        shutil.copytree(
            AgentPaths.discover().evolve / "tools" / "common" / "write_text",
            evolve / "tools" / "common" / "write_text",
        )
        _install_demo_evolved_tool(
            evolve / "tools" / "coding" / "coding_probe",
            name="coding_probe",
            description="Coding-only evolved probe",
            topics=["coding"],
        )
        _install_demo_evolved_tool(
            evolve / "tools" / "workflow" / "workflow_probe",
            name="workflow_probe",
            description="Workflow-only evolved probe",
            topics=["workflow"],
        )
        (root / "workspace").mkdir()
        (root / "data" / "sessions" / "loader-demo").mkdir(parents=True)

        paths = AgentPaths.from_root(root)
        session = Session(
            conversation_id="loader-demo",
            session_dir=paths.data / "sessions" / "loader-demo",
            goal="Test system assembly",
            meta=SessionMeta(
                topics=["workflow"],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[],
            paths=paths,
        )
        session.digest_path.write_text("# 压缩 1\nEarlier chat summary.", encoding="utf-8")

        loaded = build_system_prompt(session, paths=paths, agent_core_dir=agent_core)
        names = loaded.section_names

        assert names[0] == "core"
        assert "CORE-STUB" in loaded.prompt
        print("[PASS] section 1: core.txt")

        assert "topic_index" in names
        assert "workflow" in loaded.prompt
        assert "tool_dirs: tools/workflow" in loaded.prompt
        print("[PASS] section 2: topic index from _index.toml (incl. tool_dirs)")

        assert "memory_index" in names
        assert "demo-memory" in loaded.prompt
        assert "demo-memory (workflow): Demo memory entry" in loaded.prompt
        print("[PASS] section 3: memory id+summary index")

        memory_entries = scan_memory_index(evolve)
        memory_ids = {entry.memory_id for entry in memory_entries}
        assert "demo-memory" in memory_ids
        assert "multi-topic" in memory_ids
        assert "suspect-fact" in memory_ids
        assert "old-fact" not in memory_ids
        memory_block = format_memory_index(memory_entries)
        assert "multi-topic (coding, workflow): Spans two topics" in memory_block
        assert "suspect-fact (coding): Suspect but still indexed" in memory_block
        assert "old-fact" not in memory_block
        assert "Should not appear" not in memory_block
        assert "no id field" not in memory_block
        print("[PASS] T-302: scan memories; archived skipped; MEMORY §5.2 format")

        assert "builtin_summary" in names
        assert "read_file" in loaded.prompt
        assert names.index("builtin_summary") < names.index("session")
        print("[PASS] section 4: builtin summary before overlay")

        assert "session" in names
        assert "loader-demo" in loaded.prompt
        print("[PASS] overlay: session block")

        assert "safety" in names
        assert "SAFETY-STUB" in loaded.prompt
        print("[PASS] overlay: safety always loaded")

        assert "topic_prompt:workflow" in names
        assert "Prefer small steps" in loaded.prompt
        print("[PASS] overlay: topic prompt for workflow")

        coding_session = Session(
            conversation_id="loader-coding",
            session_dir=paths.data / "sessions" / "loader-coding",
            goal="Implement loader",
            meta=SessionMeta(
                topics=["coding", "workflow"],
                llm_model="deepseek-v4-pro",
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[],
            paths=paths,
        )
        coding_loaded = build_system_prompt(coding_session, paths=paths, agent_core_dir=agent_core)
        assert "topic_prompt:coding" in coding_loaded.section_names
        assert "topic_prompt:workflow" in coding_loaded.section_names
        assert "Use Python 3.12+" in coding_loaded.prompt
        assert coding_loaded.section_names.index("safety") < coding_loaded.section_names.index(
            "topic_prompt:coding"
        )
        assert coding_loaded.section_names.index("topic_prompt:coding") < coding_loaded.section_names.index(
            "evolved_catalog"
        )
        prompts = load_confirmed_topic_prompts(evolve, ["coding", "workflow", "safety"])
        assert [tid for tid, _ in prompts] == ["coding", "workflow"]
        assert resolve_topic_prompt_path(evolve, "coding", index=load_topic_index(evolve)) == (
            evolve / "prompts" / "coding.md"
        )
        print("[PASS] T-305: confirmed topics inject full prompt via _index prompt path")

        demo_reg = ToolRegistry.load(paths)
        workflow_only = Session(
            conversation_id="loader-workflow-tools",
            session_dir=paths.data / "sessions" / "loader-workflow-tools",
            goal="tool filter",
            meta=SessionMeta(
                topics=["workflow"],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[],
            paths=paths,
        )
        workflow_loaded = build_system_prompt(
            workflow_only, paths=paths, agent_core_dir=agent_core, registry=demo_reg
        )
        assert "workflow_probe" in workflow_loaded.prompt
        assert "coding_probe" not in workflow_loaded.prompt
        assert "write_text" in workflow_loaded.prompt
        coding_allow = session_evolved_allowlist(coding_session, registry=demo_reg)
        workflow_allow = session_evolved_allowlist(workflow_only, registry=demo_reg)
        assert coding_allow == frozenset({"write_text", "coding_probe", "workflow_probe"})
        assert workflow_allow == frozenset({"write_text", "workflow_probe"})
        coding_only = Session(
            conversation_id="loader-coding-tools",
            session_dir=paths.data / "sessions" / "loader-coding-tools",
            goal="tool filter",
            meta=SessionMeta(
                topics=["coding"],
                llm_model="deepseek-v4-pro",
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[],
            paths=paths,
        )
        coding_only_allow = session_evolved_allowlist(coding_only, registry=demo_reg)
        assert coding_only_allow == frozenset({"write_text", "coding_probe"})
        catalog_names = {
            line.split(":", 1)[0].removeprefix("- ").strip()
            for line in workflow_loaded.prompt.splitlines()
            if line.startswith("- ") and ": " in line
        }
        assert workflow_allow.issubset(catalog_names)

        from tools.executor import ToolExecutor

        executor = ToolExecutor.create(
            paths=paths,
            session_dir=session.session_dir,
            allowed_evolved=set(coding_only_allow),
            confirm_fn=lambda _preview, _allow_all: "y",
        )
        blocked = executor.run(
            "run_evolved",
            {"tool_name": "workflow_probe", "arguments": {}},
        )
        assert blocked.ok is False
        assert blocked.error is not None
        assert "not allowed" in blocked.error.message
        allowed_call = executor.run(
            "run_evolved",
            {"tool_name": "coding_probe", "arguments": {}},
        )
        assert allowed_call.ok is True
        print("[PASS] T-308: evolved catalog (common+topic) + run_evolved allowlist")

        log_path = paths.data / "evolve_log.jsonl"
        log = EvolveLog(log_path)
        start_fields = session_start_log_fields(session)
        assert "demo-memory" in start_fields["memory_ids_loaded"]
        assert "workflow" in start_fields["topics_available"]
        log_session_start(session, evolve_log=log)
        log_topics_confirmed(coding_session, evolve_log=log, registry=ToolRegistry.load(paths))
        events = read_events(log_path)
        start_evt = next(event for event in events if event.get("event") == EVENT_SESSION_START)
        confirm_evt = next(event for event in events if event.get("event") == EVENT_TOPICS_CONFIRMED)
        assert start_evt["conversation_id"] == "loader-demo"
        assert isinstance(start_evt["memory_ids_loaded"], list)
        assert isinstance(start_evt["topics_available"], list)
        assert confirm_evt["topics_confirmed"] == ["coding", "workflow"]
        assert "prompts/coding.md" in confirm_evt["prompt_files_loaded"]
        assert "prompts/workflow.md" in confirm_evt["prompt_files_loaded"]
        assert "write_text" in confirm_evt["evolved_tools_listed"]
        print("[PASS] T-306: evolve_log session_start + topics_confirmed")

        assert "evolved_catalog" in names
        assert "write_text" in loaded.prompt
        print("[PASS] overlay: evolved catalog (common)")

        assert "digest" in names
        assert "Earlier chat summary" in loaded.prompt
        print("[PASS] overlay: digest when digest.md exists")

        assert loaded.prompt.count(SECTION_SEPARATOR) == len(names) - 1
        print("[PASS] sections joined with --- separator")

        empty_topics = Session(
            conversation_id="loader-empty",
            session_dir=paths.data / "sessions" / "loader-empty",
            goal="",
            meta=SessionMeta(topics=[], llm_model="deepseek-v4-flash", updated_at=utc_now_iso()),
            messages=[],
            paths=paths,
        )
        empty_loaded = build_system_prompt(empty_topics, paths=paths, agent_core_dir=agent_core)
        assert "safety" in empty_loaded.section_names
        assert "SAFETY-STUB" in empty_loaded.prompt
        assert not any(name.startswith("topic_prompt:") for name in empty_loaded.section_names)
        print("[PASS] safety loads even when topics=[]")

        base_only = build_system_prompt(
            session, paths=paths, include_overlay=False, agent_core_dir=agent_core
        )
        assert base_only.section_names == ("core", "topic_index", "memory_index", "builtin_summary")
        assert "SAFETY-STUB" not in base_only.prompt
        print("[PASS] include_overlay=False yields S0 base only")

    # Real repo smoke (uses checked-in core + safety).
    paths = AgentPaths.discover()
    repo_session = Session(
        conversation_id="_loader_smoke",
        session_dir=paths.data / "sessions" / "_loader_smoke",
        goal="smoke",
        meta=SessionMeta(topics=["workflow"], llm_model="deepseek-v4-flash", updated_at=utc_now_iso()),
        messages=[],
        paths=paths,
    )
    repo_loaded = build_system_prompt(repo_session)
    assert "safety" in repo_loaded.section_names
    assert "write_text" in repo_loaded.prompt
    print("[PASS] real repo: safety + write_text in system prompt")

    repo_topics = load_topic_index(paths.evolve)
    assert len(repo_topics) >= 4
    assert any(e.id == "coding" and e.tool_dirs == ("tools/coding",) for e in repo_topics)
    assert any(e.id == "safety" and e.tool_dirs == () for e in repo_topics)
    topic_block = format_topic_index(repo_topics)
    assert "coding:" in topic_block and "开发与代码" in topic_block
    assert "tool_dirs: tools/coding" in topic_block
    assert "tool_dirs: (none)" in topic_block
    base_repo = build_system_prompt(repo_session, include_overlay=False)
    assert "tool_dirs: tools/coding" in base_repo.prompt
    assert "tool_dirs: tools/workflow" in base_repo.prompt
    print("[PASS] T-301: _index.toml → id/name/description/tool_dirs in system")

    repo_memories = scan_memory_index(paths.evolve)
    repo_memory_block = format_memory_index(repo_memories)
    assert repo_memory_block.startswith("[久远记忆]")
    base_repo_mem = build_system_prompt(repo_session, include_overlay=False)
    assert "[久远记忆]" in base_repo_mem.prompt
    if repo_memories:
        entry = repo_memories[0]
        assert f"- {entry.memory_id} (" in base_repo_mem.prompt
    else:
        assert "(none active)" in base_repo_mem.prompt
    print("[PASS] T-302: memory index in S0 (repo has %d active)" % len(repo_memories))

    coding_prompt_path = paths.evolve / "prompts" / "coding.md"
    if coding_prompt_path.is_file():
        coding_session = Session(
            conversation_id="_loader_coding",
            session_dir=paths.data / "sessions" / "_loader_coding",
            goal="smoke",
            meta=SessionMeta(topics=["coding"], llm_model="deepseek-v4-pro", updated_at=utc_now_iso()),
            messages=[],
            paths=paths,
        )
        coding_loaded = build_system_prompt(coding_session)
        assert "topic_prompt:coding" in coding_loaded.section_names
        assert coding_prompt_path.read_text(encoding="utf-8").strip() in coding_loaded.prompt
        print("[PASS] T-305: real repo coding.md full text in system overlay")

        log_path = paths.data / "evolve_log.jsonl"
        log = EvolveLog(log_path)
        log_session_start(coding_session, evolve_log=log)
        log_topics_confirmed(coding_session, evolve_log=log, registry=ToolRegistry.load(paths))
        tail = read_events(log_path, limit=2)
        assert tail[-2]["event"] == EVENT_SESSION_START
        assert tail[-2]["topics_available"]
        assert tail[-1]["event"] == EVENT_TOPICS_CONFIRMED
        assert tail[-1]["topics_confirmed"] == ["coding"]
        assert "prompts/coding.md" in tail[-1]["prompt_files_loaded"]
        assert "write_text" in tail[-1]["evolved_tools_listed"]
        print("[PASS] T-306: real repo evolve_log fields")

        assert any(entry.memory_id == "project-my-agent" for entry in repo_memories)
        assert "project-my-agent (coding):" in base_repo_mem.prompt
        assert "先 tool 后 skill" in base_repo_mem.prompt
        assert "import agent_core" in coding_loaded.prompt
        assert "topic_prompt:coding" in coding_loaded.section_names
        print("[PASS] T-307: coding prompt + memory index in system (repo evolve samples)")

        repo_reg = ToolRegistry.load(paths)
        repo_allow = session_evolved_allowlist(coding_session, registry=repo_reg)
        repo_catalog = format_evolved_catalog_overlay(coding_session, registry=repo_reg)
        assert repo_allow == frozenset({"write_text"})
        assert "write_text" in repo_catalog
        assert "sort_by_extension" not in repo_catalog
        assert "[本会话可用 evolved 工具]" in repo_catalog
        print("[PASS] T-308: real repo catalog matches allowlist (write_text)")

        workflow_m3 = Session(
            conversation_id="loader-workflow-m3",
            session_dir=paths.data / "sessions" / "loader-workflow-m3",
            goal="整理下载目录",
            meta=SessionMeta(
                topics=["workflow"],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[],
            paths=paths,
        )
        wf_allow = session_evolved_allowlist(workflow_m3, registry=repo_reg)
        wf_catalog = format_evolved_catalog_overlay(workflow_m3, registry=repo_reg)
        assert wf_allow == frozenset({"write_text", "sort_by_extension"})
        assert "sort_by_extension" in wf_catalog
        print("[PASS] T-502: workflow sort_by_extension in catalog + allowlist")
    else:
        print("[SKIP] T-305 real repo: evolve/prompts/coding.md not present (see T-307)")

    fresh_s4 = Session(
        "_esc",
        paths.data / "sessions" / "_esc",
        "g",
        SessionMeta(topics=["workflow"], llm_model="flash", updated_at=utc_now_iso(), phase="S4"),
        [],
        paths,
    )
    with_hint = build_system_prompt(fresh_s4)
    assert "evolve_escalation" in with_hint.section_names
    assert "每会话最多一次" in with_hint.prompt
    used_s4 = Session(
        "_esc2",
        paths.data / "sessions" / "_esc2",
        "g",
        SessionMeta(
            topics=["workflow"],
            llm_model="flash",
            updated_at=utc_now_iso(),
            phase="S4",
            evolve_offer_used=True,
        ),
        [],
        paths,
    )
    without_hint = build_system_prompt(used_s4)
    assert "evolve_escalation" not in without_hint.section_names
    print("[PASS] T-403: evolve_escalation hint in system when offer slot unused")

    core_text = load_core_text()
    assert "my-agent" in core_text
    assert "run_evolved" in core_text
    assert "Do not pretend" in core_text or "pretend" in core_text.lower()
    assert "read_file" in core_text and "grep" in core_text
    assert "T-209" not in core_text
    print("[PASS] core.txt: identity, boundaries, no pretend (T-209)")


if __name__ == "__main__":
    _demo()
