"""CLI for tool invocation without LLM (TASKS T-112)."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from tools.executor import ToolExecutor
from tools.logging import read_events
from tools.registry import ToolRegistry
from tools.schema import ToolErrorCode, to_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="my-agent", description="my-agent tool CLI (Phase 1)")
    sub = parser.add_subparsers(dest="command", required=True)

    tool = sub.add_parser("tool", help="Run or list tools")
    tool_sub = tool.add_subparsers(dest="tool_command", required=True)

    list_cmd = tool_sub.add_parser("list", help="List builtin and evolved tools")
    list_cmd.set_defaults(handler=_cmd_tool_list)

    run_cmd = tool_sub.add_parser("run", help="Run a builtin or evolved tool")
    run_cmd.add_argument("target", help="Builtin name, or 'evolved' for evolved tools")
    run_cmd.add_argument("evolved_name", nargs="?", help="Evolved tool name when target is 'evolved'")
    run_cmd.add_argument("--json", default="{}", help="Tool arguments as JSON object")
    run_cmd.add_argument("--dry-run", action="store_true", help="Pass dry_run=true to run_evolved")
    run_cmd.add_argument("-y", "--yes", action="store_true", help="Auto-confirm (answer y)")
    run_cmd.add_argument(
        "--session",
        default="_cli",
        help="Session id for confirm state / tool_outputs (default: _cli)",
    )
    run_cmd.set_defaults(handler=_cmd_tool_run)

    review_cmd = sub.add_parser("review", help="Deterministic evolve review (T-601)")
    review_cmd.add_argument(
        "--topic",
        action="append",
        dest="topics",
        default=[],
        help="Limit scope to topic id (repeatable)",
    )
    review_cmd.add_argument(
        "--log-window-days",
        type=int,
        default=90,
        help="Only count evolve_log usage within N days (default: 90)",
    )
    review_cmd.add_argument(
        "--no-observation",
        action="store_true",
        help="Omit observation-period block (<14d unused)",
    )
    review_cmd.add_argument(
        "--format",
        choices=["cli", "json", "markdown"],
        default="cli",
        help="Output format (default: cli)",
    )
    review_cmd.add_argument(
        "-o",
        "--output",
        help="Write report to file (default: stdout)",
    )
    review_cmd.set_defaults(handler=_cmd_review)

    audit_cmd = sub.add_parser("audit", help="LLM semantic evolve audit (T-603)")
    audit_cmd.add_argument(
        "scope",
        nargs="?",
        choices=["prompts"],
        default=None,
        help="Limit audit to prompt files only",
    )
    audit_cmd.add_argument(
        "--topic",
        action="append",
        dest="topics",
        default=[],
        help="Limit scope to topic id (repeatable)",
    )
    audit_cmd.add_argument(
        "--log-window-days",
        type=int,
        default=90,
        help="Deterministic review log window in days (default: 90)",
    )
    audit_cmd.add_argument(
        "--no-observation",
        action="store_true",
        help="Omit observation-period block from deterministic collect()",
    )
    audit_cmd.add_argument(
        "--only-llm",
        action="store_true",
        help="CLI/markdown: print llm_findings only",
    )
    audit_cmd.add_argument(
        "--format",
        choices=["cli", "json", "markdown"],
        default="cli",
        help="Output format (default: cli)",
    )
    audit_cmd.add_argument(
        "-o",
        "--output",
        help="Write report to file (default: stdout; use - for stdout)",
    )
    audit_cmd.set_defaults(handler=_cmd_audit)

    terminal_cmd = sub.add_parser("terminal", help="Terminal harness REPL (cwd-scoped)")
    terminal_cmd.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Working directory (default: shell cwd)",
    )
    terminal_cmd.set_defaults(handler=_cmd_terminal)

    return parser


def _cmd_tool_list(args: argparse.Namespace) -> int:
    paths = AgentPaths.discover()
    registry = ToolRegistry.load(paths)
    payload = {
        "builtins": [
            {"name": tool.name, "description": tool.description, "confirm": tool.confirm}
            for tool in registry.builtins()
        ],
        "evolved": [
            {
                "name": tool.name,
                "description": tool.description,
                "status": tool.status,
                "topics": list(tool.topics),
                "workspace_only": tool.policy.workspace_only,
            }
            for tool in registry.evolved()
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _cmd_tool_run(args: argparse.Namespace) -> int:
    paths = AgentPaths.discover()
    session_dir = paths.data / "sessions" / args.session
    arguments = _parse_json_arg(args.json)
    confirm_fn = (lambda _preview, _allow: "y") if args.yes else None

    executor = ToolExecutor.create(
        paths=paths,
        session_dir=session_dir,
        allowed_evolved=None,
        confirm_fn=confirm_fn,
    )

    if args.target == "evolved":
        if not args.evolved_name:
            print("error: evolved tool name required", file=sys.stderr)
            return 2
        tool_name = "run_evolved"
        inner = dict(arguments)
        run_args: dict[str, Any] = {
            "tool_name": args.evolved_name,
            "arguments": inner,
            "dry_run": bool(args.dry_run),
        }
    else:
        if args.evolved_name:
            print("error: extra evolved name only allowed with target 'evolved'", file=sys.stderr)
            return 2
        tool_name = args.target
        run_args = dict(arguments)
        if args.dry_run:
            print("error: --dry-run only applies to 'tool run evolved <name>'", file=sys.stderr)
            return 2

    result = executor.run(tool_name, run_args)
    print(to_json(result, indent=2))
    return 0 if result.ok else 1


def _cmd_review(args: argparse.Namespace) -> int:
    from governance.review import run_review_from_namespace

    return run_review_from_namespace(args)


def _cmd_audit(args: argparse.Namespace) -> int:
    from governance.audit import run_audit_from_namespace

    return run_audit_from_namespace(args)


def _cmd_terminal(args: argparse.Namespace) -> int:
    from cli_terminal import main as terminal_main

    argv: list[str] = []
    if args.path:
        argv.append(args.path)
    return terminal_main(argv)


def _parse_json_arg(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid --json: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("--json must be a JSON object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


def _demo() -> None:
    paths = AgentPaths.discover()
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "evolve_log.jsonl"
        from tools.logging import EvolveLog

        executor = ToolExecutor.create(
            paths=paths,
            session_dir=paths.data / "sessions" / "_cli_demo",
            allowed_evolved=None,
            confirm_fn=lambda _preview, _allow: "y",
            evolve_log=EvolveLog(log_path),
        )

        listed = _cmd_tool_list(argparse.Namespace())
        assert listed == 0
        registry = ToolRegistry.load(paths)
        assert registry.get_builtin("grep") is not None
        assert registry.get_evolved("write_text") is not None
        print("[PASS] tool list")

        grep = executor.run(
            "grep",
            {"pattern": "Phase 1", "path": "docs/MAP.md", "max_results": 2},
        )
        assert grep.ok
        print("[PASS] tool run grep")

        rel = "_cli_demo_write.txt"
        out = paths.workspace / rel
        out.unlink(missing_ok=True)

        dry = executor.run(
            "run_evolved",
            {
                "tool_name": "write_text",
                "arguments": {"path": rel, "content": "cli"},
                "dry_run": True,
            },
        )
        assert dry.ok and dry.data.get("dry_run") is True and not out.exists()
        print("[PASS] tool run evolved write_text dry_run")

        live = executor.run(
            "run_evolved",
            {
                "tool_name": "write_text",
                "arguments": {"path": rel, "content": "cli"},
                "dry_run": False,
            },
        )
        assert live.ok and out.read_text(encoding="utf-8") == "cli"
        print("[PASS] tool run evolved write_text live")

        events = read_events(log_path)
        assert any(event.get("event") == "tool_call" and event.get("tool") == "grep" for event in events)
        assert any(
            event.get("event") == "tool_call"
            and event.get("tool") == "run_evolved"
            and event.get("evolved_tool") == "write_text"
            for event in events
        )
        print("[PASS] evolve_log records CLI tool calls")

        cli_grep = _cmd_tool_run(
            argparse.Namespace(
                target="grep",
                evolved_name=None,
                json=json.dumps({"pattern": "T-112", "path": "docs/TASKS.md", "max_results": 1}),
                dry_run=False,
                yes=True,
                session="_cli_demo",
            )
        )
        assert cli_grep == 0
        print("[PASS] cli_tools.py tool run grep")

        cli_evolved = _cmd_tool_run(
            argparse.Namespace(
                target="evolved",
                evolved_name="write_text",
                json=json.dumps({"path": rel, "content": "via-cli", "on_conflict": "overwrite"}),
                dry_run=True,
                yes=True,
                session="_cli_demo",
            )
        )
        assert cli_evolved == 0
        print("[PASS] cli_tools.py tool run evolved write_text --dry-run")

        sort_dir = paths.workspace / "_cli_sort_demo"
        sort_dir.mkdir(parents=True, exist_ok=True)
        (sort_dir / "x.csv").write_text("1,2", encoding="utf-8")
        sort_rel = paths.to_workspace_relative(sort_dir)
        cli_sort = _cmd_tool_run(
            argparse.Namespace(
                target="evolved",
                evolved_name="sort_by_extension",
                json=json.dumps({"path": sort_rel}),
                dry_run=True,
                yes=True,
                session="_cli_demo",
            )
        )
        assert cli_sort == 0
        assert (sort_dir / "x.csv").is_file()
        print("[PASS] cli_tools.py tool run evolved sort_by_extension --dry-run (T-502)")

        (sort_dir / "x.csv").unlink(missing_ok=True)
        sort_dir.rmdir()
        out.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        raise SystemExit(main())
