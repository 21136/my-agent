"""ToolExecutor — confirm, dry_run, spill, evolve_log (TOOLS.md §6, TASKS T-108–T-110)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[1]
_TOOLS_DIR = Path(__file__).resolve().parent
if sys.path and Path(sys.path[0]).resolve() == _TOOLS_DIR.resolve():
    sys.path.pop(0)
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from tools.builtin import fetch_url, grep, list_dir, read_file, run_evolved, web_search
from tools.logging import (
    EvolveLog,
    conversation_id_from_session,
    read_events,
)
from tools.registry import BuiltinTool, EvolvedTool, ToolRegistry
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok, to_json

ConfirmFn = Callable[[str, bool], str]
EventFn = Callable[[str, dict[str, Any]], None]

_BUILTIN_RUNNERS: dict[str, Callable[..., ToolResult]] = {
    "read_file": read_file.run,
    "list_dir": list_dir.run,
    "grep": grep.run,
    "web_search": web_search.run,
    "fetch_url": fetch_url.run,
    "run_evolved": run_evolved.run,
}

_META_FILENAME = "meta.json"
_WORKSPACE_APPROVED_KEY = "workspace_evolved_approved"
_EVENT_SESSION_WORKSPACE_APPROVED = "session_workspace_approved"
_DEFAULT_SPILL_CHARS = 8000
_DEFAULT_PREVIEW_CHARS = 2000
_TOOL_OUTPUTS_DIR = "tool_outputs"


@dataclass
class ExecutorSession:
    """Per-conversation executor state (TOOLS.md §6.3)."""

    session_dir: Path | None = None
    workspace_evolved_approved: bool = False
    allowed_evolved: set[str] | None = None

    @classmethod
    def load(cls, session_dir: Path | None, *, allowed_evolved: set[str] | None = None) -> ExecutorSession:
        approved = False
        if session_dir is not None:
            meta_path = session_dir / _META_FILENAME
            if meta_path.is_file():
                try:
                    payload = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    payload = {}
                if isinstance(payload, dict):
                    approved = bool(payload.get(_WORKSPACE_APPROVED_KEY, False))
        return cls(
            session_dir=session_dir,
            workspace_evolved_approved=approved,
            allowed_evolved=allowed_evolved,
        )


@dataclass
class ToolExecutor:
    """Dispatch builtins / run_evolved with confirm interaction."""

    registry: ToolRegistry
    session: ExecutorSession = field(default_factory=ExecutorSession)
    confirm_fn: ConfirmFn | None = None
    on_event: EventFn | None = None
    evolve_log: EvolveLog | None = None

    @classmethod
    def create(
        cls,
        *,
        paths: AgentPaths | None = None,
        session_dir: Path | None = None,
        allowed_evolved: set[str] | None = None,
        confirm_fn: ConfirmFn | None = None,
        on_event: EventFn | None = None,
        evolve_log: EvolveLog | None = None,
    ) -> ToolExecutor:
        agent_paths = paths or AgentPaths.discover()
        registry = ToolRegistry.load(agent_paths)
        session = ExecutorSession.load(session_dir, allowed_evolved=allowed_evolved)
        return cls(
            registry=registry,
            session=session,
            confirm_fn=confirm_fn,
            on_event=on_event,
            evolve_log=evolve_log if evolve_log is not None else EvolveLog.for_agent(agent_paths),
        )

    def run(self, tool_name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        """Validate, optionally confirm, then execute a tool call."""
        started = time.perf_counter()
        name = tool_name.strip()
        args = dict(arguments or {})
        confirm_decision = "skipped"

        error = self.validate(name, args)
        if error is not None:
            self._log_tool_call(name, args, error, confirm=confirm_decision, started=started)
            return error

        builtin = self.registry.get_builtin(name)
        assert builtin is not None

        evolved_target = self._resolve_evolved_target(name, args) if name == "run_evolved" else None
        if self._needs_confirm(builtin, evolved_target):
            confirm_decision = self._ask_confirm(name, args, evolved_target)
            if confirm_decision == "n":
                result = tool_fail(
                    name,
                    ToolErrorCode.CONFIRM_REJECTED,
                    "tool call rejected by user",
                    duration_ms=_elapsed_ms(started),
                )
                self._log_tool_call(name, args, result, confirm=confirm_decision, started=started)
                return result
            if confirm_decision == "a":
                self._approve_workspace_evolved(evolved_target)

        result = self._maybe_spill_output(self._execute_builtin(name, args, started=started))
        if name == "read_file" and result.ok:
            self._maybe_record_memory_entity_used(args, result)
        self._log_tool_call(name, args, result, confirm=confirm_decision, started=started)
        return result

    def validate(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult | None:
        """Return a failed ToolResult when the call is invalid; otherwise None."""
        name = tool_name.strip()
        if not name:
            return tool_fail(
                tool_name or "unknown",
                ToolErrorCode.VALIDATION_ERROR,
                "tool name is required",
            )

        builtin = self.registry.get_builtin(name)
        if builtin is None:
            return tool_fail(
                name,
                ToolErrorCode.TOOL_NOT_FOUND,
                f"unknown builtin tool: {name}",
            )

        if name != "run_evolved":
            return None

        evolved_name = arguments.get("tool_name")
        if not isinstance(evolved_name, str) or not evolved_name.strip():
            return tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                "tool_name is required for run_evolved",
            )

        evolved = self.registry.get_evolved(evolved_name.strip())
        if evolved is None:
            return tool_fail(
                name,
                ToolErrorCode.TOOL_NOT_FOUND,
                f"unknown evolved tool: {evolved_name}",
            )

        if self.session.allowed_evolved is not None and evolved.name not in self.session.allowed_evolved:
            return tool_fail(
                name,
                ToolErrorCode.TOOL_NOT_FOUND,
                f"tool not allowed in this session: {evolved.name}",
            )

        tool_args = arguments.get("arguments")
        if tool_args is not None and not isinstance(tool_args, dict):
            return tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                "arguments must be an object",
            )

        dry_run = arguments.get("dry_run", False)
        if dry_run is not None and not isinstance(dry_run, bool):
            return tool_fail(
                name,
                ToolErrorCode.VALIDATION_ERROR,
                "dry_run must be a boolean",
            )

        return None

    def _needs_confirm(self, builtin: BuiltinTool, evolved: EvolvedTool | None) -> bool:
        if not builtin.confirm:
            return False
        if evolved is not None and evolved.policy.workspace_only and self.session.workspace_evolved_approved:
            return False
        return True

    def _resolve_evolved_target(self, tool_name: str, arguments: dict[str, Any]) -> EvolvedTool | None:
        if tool_name != "run_evolved":
            return None
        evolved_name = arguments.get("tool_name")
        if not isinstance(evolved_name, str):
            return None
        return self.registry.get_evolved(evolved_name.strip())

    def _ask_confirm(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        evolved: EvolvedTool | None,
    ) -> str:
        preview = build_confirm_preview(tool_name, arguments, evolved=evolved)
        allow_approve_all = evolved is not None and evolved.policy.workspace_only
        if self.confirm_fn is not None:
            return _normalize_confirm_choice(self.confirm_fn(preview, allow_approve_all), allow_approve_all)
        return _prompt_confirm_interactive(preview, allow_approve_all)

    def _approve_workspace_evolved(self, evolved: EvolvedTool | None) -> None:
        self.session.workspace_evolved_approved = True
        self._persist_workspace_approval()
        event_payload: dict[str, Any] = {}
        if evolved is not None:
            event_payload["tool_name"] = evolved.name
        self._emit_event(_EVENT_SESSION_WORKSPACE_APPROVED, event_payload)
        if self.evolve_log is not None:
            self.evolve_log.log_session_workspace_approved(
                conversation_id=conversation_id_from_session(self.session.session_dir),
                tool_name=evolved.name if evolved is not None else None,
            )

    def _log_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
        *,
        confirm: str,
        started: float,
    ) -> None:
        if self.evolve_log is None:
            return
        evolved_tool: str | None = None
        dry_run: bool | None = None
        if tool_name == "run_evolved":
            raw_name = arguments.get("tool_name")
            if isinstance(raw_name, str) and raw_name.strip():
                evolved_tool = raw_name.strip()
            raw_dry = arguments.get("dry_run", False)
            if isinstance(raw_dry, bool):
                dry_run = raw_dry
        logged = result
        if result.duration_ms == 0:
            logged = ToolResult(
                ok=result.ok,
                tool=result.tool,
                data=result.data,
                truncated=result.truncated,
                error=result.error,
                duration_ms=_elapsed_ms(started),
                output_path=result.output_path,
            )
        self.evolve_log.log_tool_call(
            tool=tool_name,
            arguments=arguments,
            result=logged,
            conversation_id=conversation_id_from_session(self.session.session_dir),
            confirm=confirm,
            evolved_tool=evolved_tool,
            dry_run=dry_run,
        )

    def _persist_workspace_approval(self) -> None:
        if self.session.session_dir is None:
            return
        self.session.session_dir.mkdir(parents=True, exist_ok=True)
        meta_path = self.session.session_dir / _META_FILENAME
        payload: dict[str, Any] = {}
        if meta_path.is_file():
            try:
                loaded = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    payload = loaded
            except (OSError, json.JSONDecodeError):
                payload = {}
        payload[_WORKSPACE_APPROVED_KEY] = True
        meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.on_event is not None:
            self.on_event(event_type, payload)

    def _maybe_record_memory_entity_used(self, arguments: dict[str, Any], result: ToolResult) -> None:
        path_value = arguments.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            data = result.data if isinstance(result.data, dict) else {}
            alt = data.get("path")
            if isinstance(alt, str) and alt.strip():
                path_value = alt
            else:
                return

        from governance.entity_usage import record_memory_entity_used

        record_memory_entity_used(
            paths=self.registry.agent_paths,
            path_value=path_value,
            evolve_log=self.evolve_log,
            session_dir=self.session.session_dir,
            conversation_id=conversation_id_from_session(self.session.session_dir),
        )

    def _execute_builtin(self, tool_name: str, arguments: dict[str, Any], *, started: float) -> ToolResult:
        runner = _BUILTIN_RUNNERS[tool_name]
        if tool_name == "run_evolved":
            return runner(
                arguments,
                registry=self.registry,
                paths=self.registry.agent_paths,
                allowed_tools=self.session.allowed_evolved,
            )
        return runner(arguments, paths=self.registry.agent_paths)

    def _maybe_spill_output(self, result: ToolResult) -> ToolResult:
        """Spill oversized structured results to session tool_outputs (§6.4)."""
        return maybe_spill_result(
            result,
            session_dir=self.session.session_dir,
            agent_paths=self.registry.agent_paths,
        )


def spill_threshold_chars() -> int:
    raw = os.environ.get("TOOL_OUTPUT_SPILL_CHARS", str(_DEFAULT_SPILL_CHARS))
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_SPILL_CHARS
    return max(1, value)


def preview_chars() -> int:
    raw = os.environ.get("TOOL_OUTPUT_PREVIEW_CHARS", str(_DEFAULT_PREVIEW_CHARS))
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_PREVIEW_CHARS
    return max(1, value)


def serialize_tool_data(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def maybe_spill_result(
    result: ToolResult,
    *,
    session_dir: Path | None,
    agent_paths: AgentPaths,
) -> ToolResult:
    """When serialized ``data`` exceeds spill threshold, write full text and return preview."""
    if not result.ok or result.data is None:
        return result

    serialized = serialize_tool_data(result.data)
    if len(serialized) <= spill_threshold_chars():
        return result

    preview = serialized[: preview_chars()]
    output_path: str | None = None

    if session_dir is not None:
        output_dir = session_dir / _TOOL_OUTPUTS_DIR
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{uuid.uuid4().hex}.txt"
        output_file.write_text(serialized, encoding="utf-8")
        output_path = agent_paths.to_agent_relative(output_file)

    return tool_ok(
        result.tool,
        {"preview": preview},
        truncated=True,
        duration_ms=result.duration_ms,
        output_path=output_path,
    )


def build_confirm_preview(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    evolved: EvolvedTool | None = None,
) -> str:
    """Human-readable preview shown before confirm."""
    lines = [f"Tool: {tool_name}"]
    if tool_name == "run_evolved":
        evolved_name = arguments.get("tool_name")
        lines.append(f"Evolved: {evolved_name}")
        inner = arguments.get("arguments") or {}
        if isinstance(inner, dict) and inner:
            lines.append(f"Arguments: {json.dumps(inner, ensure_ascii=False, sort_keys=True)}")
        if arguments.get("dry_run"):
            lines.append("Mode: dry_run")
        if evolved is not None:
            lines.append(f"Policy: workspace_only={evolved.policy.workspace_only}")
    else:
        if arguments:
            lines.append(f"Arguments: {json.dumps(arguments, ensure_ascii=False, sort_keys=True)}")
    return "\n".join(lines)


def _normalize_confirm_choice(raw: str, allow_approve_all: bool) -> str:
    choice = raw.strip().lower()
    if choice in {"y", "yes"}:
        return "y"
    if choice in {"n", "no"}:
        return "n"
    if allow_approve_all and choice in {"a", "all"}:
        return "a"
    raise ValueError(f"invalid confirm choice: {raw!r}")


def _prompt_confirm_interactive(preview: str, allow_approve_all: bool) -> str:
    print(preview)
    if allow_approve_all:
        prompt = "Confirm [y]es / [n]o / [a]llow workspace evolved this session? "
        valid = {"y", "n", "a"}
    else:
        prompt = "Confirm [y]es / [n]o? "
        valid = {"y", "n"}
    while True:
        try:
            raw = input(prompt)
        except EOFError:
            raw = "n"
        choice = raw.strip().lower()
        if choice in valid:
            return choice
        print(f"Please enter one of: {', '.join(sorted(valid))}")


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _demo() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        evolve = Path(tmp) / "evolve"
        paths = AgentPaths.discover()
        session_dir = paths.data / "sessions" / "_executor_demo"
        session_dir.mkdir(parents=True, exist_ok=True)
        meta_path = session_dir / _META_FILENAME
        if meta_path.is_file():
            meta_path.unlink()
        for old in (session_dir / _TOOL_OUTPUTS_DIR).glob("*.txt") if (session_dir / _TOOL_OUTPUTS_DIR).is_dir() else []:
            old.unlink()
        tool_ws = evolve / "tools" / "common" / "echo_json"
        tool_remote = evolve / "tools" / "coding" / "remote_echo"
        tool_ws.mkdir(parents=True)
        tool_remote.mkdir(parents=True)

        (tool_ws / "main.py").write_text(
            """import json
import sys

payload = json.load(sys.stdin)
print(json.dumps({"ok": True, "echo": payload.get("message", "")}))
""",
            encoding="utf-8",
        )
        (tool_remote / "main.py").write_text(
            """import json
import sys

payload = json.load(sys.stdin)
print(json.dumps({"ok": True, "remote": payload.get("message", "")}))
""",
            encoding="utf-8",
        )

        _write_manifest(tool_ws / "tool.toml", name="echo_json", workspace_only=True)
        _write_manifest(tool_remote / "tool.toml", name="remote_echo", workspace_only=False, topics=["coding"])

        registry = ToolRegistry.load(paths)
        from tools.registry import parse_tool_manifest

        echo_tool = parse_tool_manifest(tool_ws / "tool.toml", evolve_dir=evolve)
        remote_tool = parse_tool_manifest(tool_remote / "tool.toml", evolve_dir=evolve)
        registry = ToolRegistry(agent_paths=paths, evolved=[echo_tool, remote_tool])

        prompts: list[str] = []
        events: list[tuple[str, dict[str, Any]]] = []

        def scripted_confirm(preview: str, allow_approve_all: bool) -> str:
            prompts.append(preview)
            if not prompts_queue:
                return "n"
            choice = prompts_queue.pop(0)
            return _normalize_confirm_choice(choice, allow_approve_all)

        prompts_queue: list[str] = []
        log_path = Path(tmp) / "evolve_log.jsonl"

        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession(session_dir=session_dir, allowed_evolved={"echo_json", "remote_echo"}),
            confirm_fn=scripted_confirm,
            on_event=lambda event_type, payload: events.append((event_type, payload)),
            evolve_log=EvolveLog(log_path),
        )

        read = executor.run("read_file", {"path": "docs/MAP.md"})
        assert read.ok, read
        assert not prompts
        print("[PASS] read_file runs without confirm")

        prompts_queue = ["n"]
        rejected = executor.run(
            "run_evolved",
            {"tool_name": "echo_json", "arguments": {"message": "blocked"}},
        )
        assert not rejected.ok and rejected.error.code == ToolErrorCode.CONFIRM_REJECTED
        print("[PASS] confirm n rejects run_evolved")

        prompts_queue = ["y"]
        approved = executor.run(
            "run_evolved",
            {"tool_name": "echo_json", "arguments": {"message": "hello"}},
        )
        assert approved.ok and approved.data["echo"] == "hello"
        assert not executor.session.workspace_evolved_approved
        print("[PASS] confirm y executes once")

        prompts_queue = ["a"]
        approved_all = executor.run(
            "run_evolved",
            {"tool_name": "echo_json", "arguments": {"message": "batch"}},
        )
        assert approved_all.ok
        assert executor.session.workspace_evolved_approved
        meta = json.loads((session_dir / _META_FILENAME).read_text(encoding="utf-8"))
        assert meta[_WORKSPACE_APPROVED_KEY] is True
        assert events[-1][0] == _EVENT_SESSION_WORKSPACE_APPROVED
        print("[PASS] confirm a sets workspace_evolved_approved + event")

        prompts.clear()
        skipped = executor.run(
            "run_evolved",
            {"tool_name": "echo_json", "arguments": {"message": "no prompt"}},
        )
        assert skipped.ok and not prompts
        print("[PASS] workspace_only evolved skips confirm after a")

        prompts_queue = ["y"]
        still_confirm = executor.run(
            "run_evolved",
            {"tool_name": "remote_echo", "arguments": {"message": "remote"}},
        )
        assert still_confirm.ok and prompts
        print("[PASS] non-workspace_only evolved still confirms")

        reloaded = ToolExecutor(
            registry=registry,
            session=ExecutorSession.load(session_dir, allowed_evolved={"echo_json", "remote_echo"}),
            confirm_fn=scripted_confirm,
        )
        assert reloaded.session.workspace_evolved_approved
        prompts.clear()
        resumed = reloaded.run(
            "run_evolved",
            {"tool_name": "echo_json", "arguments": {"message": "resumed"}},
        )
        assert resumed.ok and not prompts
        print("[PASS] workspace approval restored from meta.json")

        # --- T-109: dry_run + spill ---
        tool_write = evolve / "tools" / "common" / "write_probe"
        tool_write.mkdir(parents=True)
        (tool_write / "main.py").write_text(
            """import json
import sys
from pathlib import Path

payload = json.load(sys.stdin)
target = Path(payload["path"])
if payload.get("dry_run"):
    print(json.dumps({"ok": True, "dry_run": True, "would_write": str(target)}))
else:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload.get("content", ""), encoding="utf-8")
    print(json.dumps({"ok": True, "written": str(target)}))
""",
            encoding="utf-8",
        )
        _write_manifest(tool_write / "tool.toml", name="write_probe", workspace_only=True)
        write_tool = parse_tool_manifest(tool_write / "tool.toml", evolve_dir=evolve)
        registry = ToolRegistry(agent_paths=paths, evolved=[echo_tool, remote_tool, write_tool])
        executor.registry = registry
        executor.session.allowed_evolved = {"echo_json", "remote_echo", "write_probe"}
        executor.session.workspace_evolved_approved = True

        out_file = paths.workspace / "_executor_t109_probe.txt"
        if out_file.exists():
            out_file.unlink()

        dry = executor.run(
            "run_evolved",
            {
                "tool_name": "write_probe",
                "arguments": {"path": str(out_file), "content": "live"},
                "dry_run": True,
            },
        )
        assert dry.ok and dry.data.get("dry_run") is True
        assert not out_file.exists()
        print("[PASS] dry_run does not write file")

        live = executor.run(
            "run_evolved",
            {
                "tool_name": "write_probe",
                "arguments": {"path": str(out_file), "content": "live"},
                "dry_run": False,
            },
        )
        assert live.ok and out_file.read_text(encoding="utf-8") == "live"
        print("[PASS] live run_evolved writes file")

        big_name = "_executor_t109_big.txt"
        big_path = paths.workspace / big_name
        big_path.write_text("line\n" * 3000, encoding="utf-8")
        try:
            big = executor.run("read_file", {"path": big_name})
            assert big.ok and big.truncated is True
            assert big.output_path is not None
            assert isinstance(big.data, dict) and "preview" in big.data
            assert len(big.data["preview"]) == preview_chars()
            spilled = session_dir / _TOOL_OUTPUTS_DIR / Path(big.output_path).name
            assert spilled.is_file()
            assert len(spilled.read_text(encoding="utf-8")) > spill_threshold_chars()
            print("[PASS] oversized builtin result spills to tool_outputs")
        finally:
            if big_path.exists():
                big_path.unlink()
            if out_file.exists():
                out_file.unlink()

        log_events = read_events(log_path)
        tool_calls = [event for event in log_events if event.get("event") == "tool_call"]
        assert len(tool_calls) >= 8
        assert any(event.get("confirm") == "n" and event.get("error_code") == ToolErrorCode.CONFIRM_REJECTED for event in tool_calls)
        assert any(event.get("event") == "session_workspace_approved" for event in log_events)
        assert all(event.get("conversation_id") == session_dir.name for event in log_events if "conversation_id" in event)
        print(f"[PASS] evolve_log records {len(tool_calls)} tool_call line(s)")

        # --- T-602a: entity_used L2 on read_file evolve/memories/** ---
        with tempfile.TemporaryDirectory() as t602_tmp:
            root = Path(t602_tmp)
            evolve_root = root / "evolve"
            evolve_root.mkdir()
            (evolve_root / "_index.toml").write_text('[[topic]]\nid = "workflow"\n', encoding="utf-8")
            mem_path = evolve_root / "memories" / "workflow" / "t602-demo.md"
            mem_path.parent.mkdir(parents=True)
            mem_path.write_text(
                "---\n"
                "id: t602-demo\n"
                "topics: [workflow]\n"
                "status: active\n"
                "summary: executor entity_used demo\n"
                "use_count: 0\n"
                "---\n\n"
                "## body\n",
                encoding="utf-8",
            )
            (root / "workspace").mkdir()
            (root / "data").mkdir()
            t602_session = root / "data" / "sessions" / "_t602"
            t602_session.mkdir(parents=True)
            t602_paths = AgentPaths.from_root(root)
            t602_log = Path(t602_tmp) / "t602_evolve_log.jsonl"
            t602_executor = ToolExecutor(
                registry=ToolRegistry.load(t602_paths),
                session=ExecutorSession(session_dir=t602_session),
                evolve_log=EvolveLog(t602_log),
            )
            read_mem = t602_executor.run(
                "read_file",
                {"path": "evolve/memories/workflow/t602-demo.md"},
            )
            assert read_mem.ok
            entity_events = [event for event in read_events(t602_log) if event.get("event") == "entity_used"]
            assert len(entity_events) == 1
            assert entity_events[0]["entity_id"] == "t602-demo"
            assert entity_events[0]["level"] == "L2"
            assert entity_events[0]["type"] == "memory"
            assert entity_events[0]["conversation_id"] == "_t602"
            updated = mem_path.read_text(encoding="utf-8")
            assert "use_count: 1" in updated
            assert "last_used_at:" in updated
            meta = json.loads((t602_session / "meta.json").read_text(encoding="utf-8"))
            assert meta["pending_feedback"][-1]["entity_id"] == "t602-demo"
            assert meta["pending_feedback"][-1]["level"] == "L2"
            print("[PASS] T-602a: read_file evolve/memories/** → entity_used + frontmatter + pending_feedback")

            before_entity_count = len(entity_events)
            index_read = t602_executor.run("read_file", {"path": "evolve/_index.toml"})
            assert index_read.ok
            after_entity_count = len(
                [event for event in read_events(t602_log) if event.get("event") == "entity_used"]
            )
            assert after_entity_count == before_entity_count
            print("[PASS] T-602a: non-memory read_file does not emit entity_used")

        print(to_json(approved, indent=2))


def _write_manifest(path: Path, *, name: str, workspace_only: bool, topics: list[str] | None = None) -> None:
    topic_list = topics or ["common"]
    topics_literal = ", ".join(f'"{topic}"' for topic in topic_list)
    path.write_text(
        f"""[tool]
name = "{name}"
description = "demo tool"
version = "1.0.0"
status = "active"
topics = [{topics_literal}]

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
workspace_only = {"true" if workspace_only else "false"}
timeout_sec = 30
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    _demo()
