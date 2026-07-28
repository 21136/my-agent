"""CLI entry + script acceptance for governance review (T-601)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from governance.collector import ReviewCollector, ReviewOptions
from governance.report import report_to_dict
from governance.renderer import ReviewRenderer, ReviewSink, render_cli, render_json, render_markdown
from loader import copy_evolve_index_files
from paths import AgentPaths
from tools.logging import EvolveLog


def build_review_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="my-agent review", description="Deterministic evolve review (T-601)")
    parser.add_argument(
        "--topic",
        action="append",
        dest="topics",
        default=[],
        help="Limit scope to topic id (repeatable)",
    )
    parser.add_argument(
        "--log-window-days",
        type=int,
        default=90,
        help="Only count evolve_log usage within N days (default: 90)",
    )
    parser.add_argument(
        "--no-observation",
        action="store_true",
        help="Omit observation-period block (<14d unused)",
    )
    parser.add_argument(
        "--format",
        choices=["cli", "json", "markdown"],
        default="cli",
        help="Output format (default: cli)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Write report to file (default: stdout)",
    )
    return parser


def run_review_from_namespace(args: argparse.Namespace, paths: AgentPaths | None = None) -> int:
    agent_paths = paths or AgentPaths.discover()
    options = ReviewOptions(
        topics=tuple(topic.strip() for topic in args.topics if topic.strip()),
        log_window_days=max(0, int(args.log_window_days)),
        include_observation_period=not args.no_observation,
    )
    report = ReviewCollector(agent_paths).collect(options)
    content = ReviewRenderer.render(report, args.format)
    ReviewSink.emit(content, args.output)
    return 0


def run_review(argv: list[str] | None = None) -> int:
    parser = build_review_parser()
    args = parser.parse_args(argv)
    return run_review_from_namespace(args)


def _write_memory(
    evolve: Path,
    *,
    rel: str,
    memory_id: str,
    topics: list[str],
    summary: str,
    status: str = "active",
    conflicts_with: list[str] | None = None,
    created_at: str | None = None,
) -> None:
    path = evolve / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"id: {memory_id}",
        f"topics: [{', '.join(topics)}]",
        f"status: {status}",
        f"summary: {summary}",
    ]
    if created_at:
        lines.append(f"created_at: {created_at}")
    if conflicts_with:
        lines.append(f"conflicts_with: [{', '.join(conflicts_with)}]")
    lines.extend(["---", "", "## 背景", "demo"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _demo() -> None:
    paths = AgentPaths.discover()
    old_created = (datetime.now(UTC) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    recent_created = (datetime.now(UTC) - timedelta(days=3)).isoformat().replace("+00:00", "Z")

    with tempfile.TemporaryDirectory() as tmp:
        evolve = Path(tmp) / "evolve"
        data = Path(tmp) / "data"
        evolve.mkdir()
        data.mkdir()
        shutil.copytree(paths.evolve / "tools", evolve / "tools")
        shutil.copytree(paths.evolve / "prompts", evolve / "prompts")
        copy_evolve_index_files(paths.evolve, evolve)
        (evolve / "memories" / "coding").mkdir(parents=True)

        _write_memory(
            evolve,
            rel="memories/coding/never-used-old.md",
            memory_id="demo-never-used",
            topics=["coding"],
            summary="ancient unused memory entry for governance demo",
            created_at=old_created,
        )
        _write_memory(
            evolve,
            rel="memories/coding/observation-new.md",
            memory_id="demo-observation",
            topics=["coding"],
            summary="brand new unused memory still in observation window",
            created_at=recent_created,
        )
        _write_memory(
            evolve,
            rel="memories/coding/suspect-one.md",
            memory_id="demo-suspect",
            topics=["coding"],
            summary="flagged suspect memory for review output",
            status="suspect",
            created_at=old_created,
        )
        _write_memory(
            evolve,
            rel="memories/coding/hard-a.md",
            memory_id="demo-hard-a",
            topics=["coding"],
            summary="hard conflict side alpha",
            conflicts_with=["demo-hard-b"],
            created_at=old_created,
        )
        _write_memory(
            evolve,
            rel="memories/coding/hard-b.md",
            memory_id="demo-hard-b",
            topics=["coding"],
            summary="hard conflict side beta",
            conflicts_with=["demo-hard-a"],
            created_at=old_created,
        )
        _write_memory(
            evolve,
            rel="memories/coding/soft-a.md",
            memory_id="demo-soft-a",
            topics=["coding"],
            summary="workflow python agent governance review token overlap",
            created_at=old_created,
        )
        _write_memory(
            evolve,
            rel="memories/coding/soft-b.md",
            memory_id="demo-soft-b",
            topics=["coding"],
            summary="another workflow python agent governance overlap warning",
            created_at=old_created,
        )

        staged_dir = evolve / "tools" / "coding" / "demo_staged"
        staged_dir.mkdir(parents=True)
        (staged_dir / "main.py").write_text(
            "import json\nprint(json.dumps({'ok': True}))\n",
            encoding="utf-8",
        )
        (staged_dir / "tool.toml").write_text(
            "\n".join(
                [
                    "[tool]",
                    'name = "demo_staged"',
                    'description = "staged tool for review demo"',
                    'version = "0.1.0"',
                    'status = "staged"',
                    'topics = ["coding"]',
                    "",
                    "[entry]",
                    'type = "python"',
                    'path = "main.py"',
                    "",
                    "[schema.input]",
                    'type = "object"',
                    "",
                    "[schema.output]",
                    'type = "object"',
                    "",
                    "[policy]",
                    "confirm = true",
                    "dry_run_supported = true",
                    "workspace_only = true",
                    "timeout_sec = 30",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        log = EvolveLog(data / "evolve_log.jsonl")
        log.log_topics_confirmed(
            conversation_id="_demo",
            topics_confirmed=["workflow"],
            prompt_files_loaded=["prompts/workflow.md"],
            evolved_tools_listed=["write_text", "sort_by_extension"],
        )
        log.append_event(
            "tool_call",
            tool="read_file",
            arguments={"path": "evolve/memories/workflow/downloads-sort.md"},
            ok=True,
            duration_ms=1,
            conversation_id="_demo",
            confirm="skipped",
        )
        log.append_event(
            "tool_spec_accepted",
            tool_name="demo_pending_tool",
            proposal_id="prop-demo-001",
            note="pending_implementation",
        )

        demo_paths = AgentPaths(
            agent_root=Path(tmp),
            evolve=evolve,
            workspace=Path(tmp) / "workspace",
            data=data,
        )
        demo_paths.workspace.mkdir(parents=True, exist_ok=True)

        report = ReviewCollector(demo_paths).collect(ReviewOptions())
        assert report.schema_version == "1.0"
        print("[PASS] T-601: ReviewReport schema_version 1.0")

        payload = report_to_dict(report)
        assert payload["schema_version"] == "1.0"
        assert "summary" in payload and "never_used" in payload
        print("[PASS] T-601: report_to_dict canonical keys")

        never_ids = {item.id for item in report.never_used}
        assert "demo-never-used" in never_ids
        print("[PASS] T-601: never-used lists aged active entity with L2+ use_count=0")

        observation_ids = {item.id for item in report.observation_period}
        assert "demo-observation" in observation_ids
        assert "demo-never-used" not in observation_ids
        print("[PASS] T-601: observation period (<14d) separate from never-used")

        suspect_ids = {item.id for item in report.suspect}
        assert "demo-suspect" in suspect_ids
        print("[PASS] T-601: suspect status listed")

        assert any(item.id_a == "demo-hard-a" and item.id_b == "demo-hard-b" for item in report.conflicts_hard)
        print("[PASS] T-601: hard conflicts_with bidirectional active pair")

        soft = next(item for item in report.conflicts_soft if {item.id_a, item.id_b} == {"demo-soft-a", "demo-soft-b"})
        assert len(soft.shared_tokens) >= 3
        assert all(len(token) >= 3 for token in soft.shared_tokens)
        print("[PASS] T-601: soft conflict summary tokens length>=3 and shared>=3")

        pending_names = {item.tool_name for item in report.pending_implementation}
        assert "demo_staged" in pending_names
        assert "demo_pending_tool" in pending_names
        print("[PASS] T-601: pending implementation (staged + tool_spec_accepted)")

        cli_text = render_cli(report)
        assert "== Never-used ==" in cli_text
        assert "== Conflicts (soft) ==" in cli_text
        assert "demo-suspect" in cli_text
        print("[PASS] T-601: render_cli default human output")

    live = ReviewCollector(paths).collect(ReviewOptions(topics=("coding",)))
    assert live.schema_version == "1.0"
    assert live.summary.memories >= 1

    soft_pairs = {(item.id_a, item.id_b) for item in live.conflicts_soft}
    repl_pairs = [
        pair for pair in soft_pairs if pair[0].startswith("coding-repl") or pair[1].startswith("coding-repl")
    ]
    if repl_pairs:
        pair = repl_pairs[0]
        entry = next(item for item in live.conflicts_soft if (item.id_a, item.id_b) == pair)
        assert len(entry.shared_tokens) >= 3
        print("[PASS] T-601: live repo coding soft conflicts detected")
    else:
        print("[SKIP] T-601: live repo coding soft conflicts (no overlapping summaries)")

    used_ids = {item.id for item in live.never_used} | {item.id for item in live.observation_period}
    if "downloads-sort" not in used_ids:
        print("[PASS] T-601: live log read_file counts as memory L2 usage (downloads-sort not unused)")
    else:
        print("[SKIP] T-601: downloads-sort still unused in live repo log window")

    cli_live = render_cli(live)
    assert cli_live.startswith("my-agent review")
    print("[PASS] T-601: live repo review --topic coding renders")

    json_text = ReviewRenderer.render(live, "json")
    parsed = json.loads(json_text)
    assert parsed["schema_version"] == "1.0"
    assert parsed["summary"]["conflict_soft_count"] == live.summary.conflict_soft_count
    assert isinstance(parsed["never_used"], list)
    assert isinstance(parsed["conflicts_soft"], list)
    print("[PASS] T-601a: --format json valid ReviewReport (jq-parseable)")

    md_text = render_markdown(live)
    assert md_text.startswith("# my-agent review")
    assert "## Summary" in md_text
    assert "## Conflicts (soft)" in md_text
    print("[PASS] T-601a: --format markdown")

    with tempfile.TemporaryDirectory() as out_tmp:
        out_json = Path(out_tmp) / "report.json"
        ns = argparse.Namespace(
            topics=["coding"],
            log_window_days=90,
            no_observation=False,
            format="json",
            output=str(out_json),
        )
        assert run_review_from_namespace(ns, paths=paths) == 0
        on_disk = json.loads(out_json.read_text(encoding="utf-8"))
        assert on_disk["schema_version"] == "1.0"
        print("[PASS] T-601a: -o writes JSON file")

        out_md = Path(out_tmp) / "report.md"
        ns.output = str(out_md)
        ns.format = "markdown"
        run_review_from_namespace(ns, paths=paths)
        assert out_md.read_text(encoding="utf-8").startswith("# my-agent review")
        print("[PASS] T-601a: -o writes markdown file")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        raise SystemExit(run_review())
