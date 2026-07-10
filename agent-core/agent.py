"""Builtin LLM tool definitions + agent main loop (TOOLS.md, RUNTIME.md §7, TASKS T-202/T-206)."""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from loader import format_session_evolved_catalog, session_evolved_allowlist
from llm_client import LLMClient, LLMResponse, load_config, resolve_session_model
from paths import AgentPaths
from session import ANCHOR_HEADER, Session, SessionMeta, build_anchor_message, utc_now_iso
from tools.executor import ToolExecutor
from tools.logging import read_events
from tools.registry import BUILTIN_TOOLS, ToolRegistry
from tools.schema import to_json

DEFAULT_TOOL_LOOP_MAX = 10
MAIN_LOOP_TEMPERATURE = 0.3

# OpenAI function names exposed to the LLM (RUNTIME.md §7.2).
BUILTIN_TOOL_NAMES: tuple[str, ...] = tuple(tool.name for tool in BUILTIN_TOOLS)

_BUILTIN_DESCRIPTIONS: dict[str, str] = {
    "read_file": (
        "Read a text file under agent root or workspace (max 512KB, UTF-8). "
        "Use for docs, workspace files, evolve/memories, and spilled tool outputs."
    ),
    "list_dir": (
        "List directory entries under agent root. "
        "Set recursive=true to include one level of children."
    ),
    "grep": (
        "Search local file contents under agent root by regex pattern. "
        "Prefer ripgrep when available."
    ),
    "web_search": (
        "Search the web for links and snippets. "
        "Use fetch_url when full page text is needed."
    ),
    "fetch_url": (
        "Fetch an HTTP/HTTPS URL and return extracted plain text "
        "(HTML stripped). Pair with web_search for page bodies."
    ),
    "run_evolved": (
        "Run a registered evolved tool by name. "
        "tool_name must appear in the session evolved catalog in system prompt; "
        "pass that tool's arguments object. Supports dry_run for write tools."
    ),
}

_BUILTIN_PARAMETERS: dict[str, dict[str, Any]] = {
    "read_file": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path relative to agent root or workspace",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    "list_dir": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path relative to agent root",
            },
            "recursive": {
                "type": "boolean",
                "description": "When true, include immediate children (default false)",
                "default": False,
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    "grep": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for",
            },
            "path": {
                "type": "string",
                "description": "File or directory under agent root",
            },
            "glob": {
                "type": "string",
                "description": "Optional glob filter (e.g. '*.py')",
            },
            "ignore_case": {
                "type": "boolean",
                "description": "Case-insensitive search (default false)",
                "default": False,
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum matches to return (default 50)",
                "default": 50,
            },
        },
        "required": ["pattern", "path"],
        "additionalProperties": False,
    },
    "web_search": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Web search query",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results (default 5, hard cap 10)",
                "default": 5,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "fetch_url": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "HTTP or HTTPS URL to fetch",
            },
            "max_chars": {
                "type": "integer",
                "description": "Max characters to return (default 32000, hard cap 128000)",
                "default": 32000,
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    "run_evolved": {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": "string",
                "description": "Evolved tool name from the session catalog",
            },
            "arguments": {
                "type": "object",
                "description": "Arguments object for the evolved tool",
                "additionalProperties": True,
            },
            "dry_run": {
                "type": "boolean",
                "description": "Preview without side effects when supported (default false)",
                "default": False,
            },
        },
        "required": ["tool_name", "arguments"],
        "additionalProperties": False,
    },
}


def builtin_parameters(name: str) -> dict[str, Any]:
    """JSON Schema for a builtin's arguments (TOOLS.md §7)."""
    try:
        return _BUILTIN_PARAMETERS[name]
    except KeyError as exc:
        raise KeyError(f"unknown builtin tool: {name!r}") from exc


def build_builtin_tool_definition(name: str) -> dict[str, Any]:
    """Single OpenAI-compatible tool entry for *name*."""
    if name not in _BUILTIN_PARAMETERS:
        raise KeyError(f"unknown builtin tool: {name!r}")
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": _BUILTIN_DESCRIPTIONS[name],
            "parameters": _BUILTIN_PARAMETERS[name],
        },
    }


def build_builtin_tools(*, registry: ToolRegistry | None = None) -> list[dict[str, Any]]:
    """Return exactly the 6 builtin tools for LLM chat (no flat evolved)."""
    reg = registry or ToolRegistry.load()
    registry_names = tuple(tool.name for tool in reg.builtins())
    if registry_names != BUILTIN_TOOL_NAMES:
        raise RuntimeError(
            f"registry builtins {registry_names!r} != expected {BUILTIN_TOOL_NAMES!r}"
        )
    return [build_builtin_tool_definition(name) for name in BUILTIN_TOOL_NAMES]


class ToolLoopExceededError(RuntimeError):
    """Tool inner loop exceeded ``DEFAULT_TOOL_LOOP_MAX`` rounds."""


class ChatClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse: ...


@dataclass
class TurnResult:
    assistant_text: str
    tool_rounds: int
    finish_reason: str | None


@dataclass
class Agent:
    """One conversation turn: user input → LLM tool loop → persisted messages."""

    session: Session
    executor: ToolExecutor
    llm: ChatClient
    tool_loop_max: int = DEFAULT_TOOL_LOOP_MAX

    @classmethod
    def create(
        cls,
        session: Session,
        *,
        llm: ChatClient | None = None,
        confirm_fn: Any | None = None,
        tool_loop_max: int = DEFAULT_TOOL_LOOP_MAX,
    ) -> Agent:
        registry = ToolRegistry.load(session.paths)
        allowed = session_evolved_allowlist(session, registry=registry)
        executor = ToolExecutor.create(
            paths=session.paths,
            session_dir=session.session_dir,
            allowed_evolved=allowed,
            confirm_fn=confirm_fn,
        )
        return cls(
            session=session,
            executor=executor,
            llm=llm or LLMClient(),
            tool_loop_max=tool_loop_max,
        )

    def _sync_allowed_evolved(self) -> None:
        self.executor.session.allowed_evolved = session_evolved_allowlist(
            self.session,
            registry=self.executor.registry,
        )

    def run_turn(self, user_text: str) -> TurnResult:
        """Append user message, run ≤10 tool rounds, persist messages.jsonl."""
        prepare_session_for_s4(self.session)
        self._sync_allowed_evolved()

        self.session.append_message({"role": "user", "content": user_text})

        from context import build_llm_messages, maybe_auto_compact
        from loader import build_system_prompt

        system = build_system_prompt(self.session).prompt
        maybe_auto_compact(self.session, system, self.llm)

        tools = build_builtin_tools(registry=self.executor.registry)
        model = self.session.meta.llm_model or resolve_session_model(self.session.meta.topics)

        working = build_llm_messages(self.session)
        tool_rounds = 0
        final_text = ""
        finish_reason: str | None = None

        for _ in range(self.tool_loop_max):
            system = build_system_prompt(self.session).prompt
            maybe_auto_compact(self.session, system, self.llm)
            working = build_llm_messages(self.session)

            response = self.llm.chat(
                [{"role": "system", "content": system}, *working],
                model=model,
                tools=tools,
                temperature=MAIN_LOOP_TEMPERATURE,
            )
            finish_reason = response.finish_reason

            if not response.tool_calls:
                final_text = (response.content or "").strip()
                if final_text:
                    assistant_msg = {"role": "assistant", "content": final_text}
                    working.append(assistant_msg)
                    self.session.append_message(assistant_msg)
                break

            tool_rounds += 1
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": response.content,
                "tool_calls": response.tool_calls,
            }
            working.append(assistant_msg)
            self.session.append_message(assistant_msg)

            for tool_call in response.tool_calls:
                tool_name, arguments = _parse_tool_call(tool_call)
                result = self.executor.run(tool_name, arguments)
                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", ""),
                    "content": to_json(result),
                }
                working.append(tool_message)
                self.session.append_message(tool_message)
        else:
            raise ToolLoopExceededError(
                f"tool loop exceeded {self.tool_loop_max} rounds without a final reply"
            )

        self.session.save()
        return TurnResult(
            assistant_text=final_text,
            tool_rounds=tool_rounds,
            finish_reason=finish_reason,
        )


def has_anchor_message(session: Session) -> bool:
    if not session.messages:
        return False
    first = session.messages[0]
    if first.get("role") != "user":
        return False
    content = first.get("content")
    return isinstance(content, str) and content.startswith(ANCHOR_HEADER)


def prepare_session_for_s4(session: Session) -> None:
    """Insert §5 anchor block once before main-loop history."""
    if has_anchor_message(session):
        return
    session.messages.insert(0, build_anchor_message(session))
    session.meta.compact_before_index = max(session.meta.compact_before_index, 1)
    session.save()


def _parse_tool_call(tool_call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    fn = tool_call.get("function")
    if not isinstance(fn, dict):
        raise ValueError("tool_call missing function object")
    name = fn.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("tool_call missing function name")
    raw_args = fn.get("arguments", "{}")
    if isinstance(raw_args, dict):
        return name.strip(), raw_args
    if not isinstance(raw_args, str):
        raw_args = str(raw_args)
    try:
        parsed = json.loads(raw_args or "{}")
    except json.JSONDecodeError:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    return name.strip(), parsed


@dataclass
class _MockLLM:
    """Scripted chat responses for demos and tests."""

    responses: list[LLMResponse] = field(default_factory=list)
    config: Any = field(default_factory=load_config)

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        if not self.responses:
            raise RuntimeError("mock LLM has no scripted responses left")
        return self.responses.pop(0)


def _demo_tools() -> None:
    registry = ToolRegistry.load()
    tools = build_builtin_tools(registry=registry)

    assert len(tools) == 6, len(tools)
    print(f"[PASS] build_builtin_tools returns {len(tools)} tools")

    names = [item["function"]["name"] for item in tools]
    assert names == list(BUILTIN_TOOL_NAMES), names
    print(f"[PASS] tool names: {', '.join(names)}")

    for item in tools:
        assert item["type"] == "function"
        fn = item["function"]
        assert isinstance(fn["description"], str) and fn["description"]
        params = fn["parameters"]
        assert params["type"] == "object"
        assert isinstance(params.get("properties"), dict)
        for key in params.get("required", []):
            assert key in params["properties"], f"{fn['name']}: missing required {key!r}"
    print("[PASS] each tool has type=function and valid parameters schema")

    # Evolved tools must not appear as flat LLM functions (TOOLS.md §4.3).
    evolved_names = {tool.name for tool in registry.evolved()}
    flat_names = set(names)
    leaked = evolved_names & flat_names
    assert not leaked, leaked
    assert "write_text" not in flat_names
    print("[PASS] no evolved tools flattened as LLM functions")

    # JSON round-trip (what llm_client.chat will send).
    payload = json.dumps(tools, ensure_ascii=False)
    roundtrip = json.loads(payload)
    assert roundtrip == tools
    print(f"[PASS] JSON-serializable ({len(payload)} chars)")

    catalog = format_session_evolved_catalog([], registry=registry)
    assert "write_text" in catalog
    assert not any(line.startswith("- run_evolved:") for line in catalog.splitlines())
    print("[PASS] format_session_evolved_catalog lists write_text under common")

    empty_topics = format_session_evolved_catalog(["nonexistent-topic"], registry=registry)
    assert "write_text" in empty_topics
    print("[PASS] common tools always in catalog regardless of topics")

    print()
    print("Sample tool definition (read_file):")
    print(json.dumps(build_builtin_tool_definition("read_file"), indent=2, ensure_ascii=False))


def _demo_loop() -> None:
    paths = AgentPaths.discover()
    session_dir = paths.data / "sessions" / "_agent_loop_demo"
    session_dir.mkdir(parents=True, exist_ok=True)

    session = Session(
        conversation_id="_agent_loop_demo",
        session_dir=session_dir,
        goal="Verify agent tool loop",
        meta=SessionMeta(
            topics=[],
            llm_model=resolve_session_model([]),
            updated_at=utc_now_iso(),
            phase="S4",
        ),
        messages=[],
        paths=paths,
    )
    session.save()

    grep_args = json.dumps(
        {"pattern": "T-206", "path": "docs/TASKS.md", "max_results": 1},
        ensure_ascii=False,
    )
    mock = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": "call_grep_1",
                        "type": "function",
                        "function": {"name": "grep", "arguments": grep_args},
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            ),
            LLMResponse(
                model="mock",
                content="Found T-206 in TASKS.md.",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
        ]
    )

    agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")
    result = agent.run_turn("Where is T-206 documented?")

    assert result.tool_rounds == 1
    assert "T-206" in result.assistant_text
    assert has_anchor_message(session)
    assert session.messages[0]["role"] == "user"
    assert ANCHOR_HEADER in session.messages[0]["content"]
    print("[PASS] anchor block inserted before history")

    roles = [msg["role"] for msg in session.messages]
    assert roles.count("tool") == 1
    assert roles[-1] == "assistant"
    print("[PASS] tool loop: user → assistant(tool_calls) → tool → assistant")

    reloaded = Session.load(paths, "_agent_loop_demo")
    assert len(reloaded.messages) == len(session.messages)
    assert reloaded.messages[-1]["content"] == result.assistant_text
    print("[PASS] messages.jsonl persisted across reload")

    stuck = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": "list_dir",
                            "arguments": json.dumps({"path": "docs"}),
                        },
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            )
            for i in range(DEFAULT_TOOL_LOOP_MAX + 1)
        ]
    )
    stuck_session = Session(
        conversation_id="_agent_loop_stuck",
        session_dir=paths.data / "sessions" / "_agent_loop_stuck",
        goal="loop cap",
        meta=SessionMeta(
            topics=[],
            llm_model=resolve_session_model([]),
            updated_at=utc_now_iso(),
            phase="S4",
        ),
        messages=[],
        paths=paths,
    )
    stuck_session.save()
    stuck_agent = Agent.create(stuck_session, llm=stuck, confirm_fn=lambda _p, _a: "y")
    try:
        stuck_agent.run_turn("list forever")
        print("[FAIL] expected ToolLoopExceededError")
        raise SystemExit(1)
    except ToolLoopExceededError:
        print(f"[PASS] tool loop capped at {DEFAULT_TOOL_LOOP_MAX} rounds")

    if load_config().api_key:
        live_session = Session(
            conversation_id="_agent_live",
            session_dir=paths.data / "sessions" / "_agent_live",
            goal="List docs folder",
            meta=SessionMeta(
                topics=[],
                llm_model=resolve_session_model([]),
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[],
            paths=paths,
        )
        live_session.save()
        live_agent = Agent.create(live_session, confirm_fn=lambda _p, _a: "y")
        live = live_agent.run_turn("用 list_dir 列出 docs 目录（path=docs，不要 recursive），一句话总结条目数。")
        assert live.assistant_text
        print(f"[PASS] live turn ({live.tool_rounds} tool round(s)): {live.assistant_text[:100]!r}")
    else:
        print("[SKIP] live agent turn: LLM_API_KEY not set")

    _demo_m3_workflow_sort(paths)


def _demo_m3_workflow_sort(paths: AgentPaths) -> None:
    """T-503: mock LLM schedules workflow evolved tool; evolve_log records the call."""
    registry = ToolRegistry.load(paths)
    demo_dir = paths.workspace / "_agent_m3_sort"
    if demo_dir.exists():
        for child in sorted(demo_dir.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
    demo_dir.mkdir(parents=True)
    (demo_dir / "photo.jpg").write_text("jpg", encoding="utf-8")
    (demo_dir / "readme").write_text("no ext", encoding="utf-8")

    rel = paths.to_workspace_relative(demo_dir)
    sort_payload = json.dumps(
        {"tool_name": "sort_by_extension", "arguments": {"path": rel}, "dry_run": False},
        ensure_ascii=False,
    )

    session_dir = paths.data / "sessions" / "_agent_m3_sort"
    session_dir.mkdir(parents=True, exist_ok=True)
    session = Session(
        conversation_id="_agent_m3_sort",
        session_dir=session_dir,
        goal="整理 workspace 下载夹",
        meta=SessionMeta(
            topics=["workflow"],
            llm_model=resolve_session_model(["workflow"]),
            updated_at=utc_now_iso(),
            phase="S4",
        ),
        messages=[],
        paths=paths,
    )
    session.save()

    allow = session_evolved_allowlist(session, registry=registry)
    assert "sort_by_extension" in allow
    assert "write_text" in allow

    log_path = paths.data / "evolve_log.jsonl"
    before = len(read_events(log_path))

    mock = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": "call_sort_1",
                        "type": "function",
                        "function": {"name": "run_evolved", "arguments": sort_payload},
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            ),
            LLMResponse(
                model="mock",
                content="已按扩展名整理 _agent_m3_sort：jpg 与无扩展名文件各 1 个。",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
        ]
    )
    agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")
    result = agent.run_turn(f"用 sort_by_extension 整理 {rel} 目录")

    assert result.tool_rounds == 1
    assert (demo_dir / "jpg" / "photo.jpg").is_file()
    assert (demo_dir / "_no_ext" / "readme").is_file()
    events = read_events(log_path)
    new_calls = [
        event
        for event in events[before:]
        if event.get("event") == "tool_call"
        and event.get("tool") == "run_evolved"
        and event.get("evolved_tool") == "sort_by_extension"
    ]
    assert new_calls
    print("[PASS] T-503: workflow session schedules sort_by_extension; evolve_log recorded")

    for child in sorted(demo_dir.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    demo_dir.rmdir()


def _demo() -> None:
    _demo_tools()
    print()
    _demo_loop()


if __name__ == "__main__":
    _demo()
