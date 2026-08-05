"""Phase 17 M0 checker subagent tests (T-1610–T-1614)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_client import LLMCancelledError, LLMResponse
from paths import AgentPaths
from session import Session, SessionMeta, utc_now_iso
from subagent import (
    CheckerTask,
    SubagentRunner,
    build_checker_tools,
    build_hard_checklist,
    checker_task_from_demo_record,
    find_auto_checker_target,
    format_checker_verdict_notice,
    format_subagent_overlay,
    merge_checklist_verdict,
    merge_checker_results,
    parse_checker_command,
    ChecklistItem,
)
from tools.executor import ExecutorSession, ScaffoldDemoRecord
from tools.logging import EVENT_SUBAGENT_RUN, EvolveLog, read_events
from tools.registry import ToolRegistry, parse_tool_manifest

from tests.isolation_helpers import make_temp_agent_paths


def _write_manifest(path: Path, *, name: str) -> None:
    path.write_text(
        f"""[tool]
name = "{name}"
description = "checker test tool"
version = "1.0.0"
status = "active"
topics = ["common"]

[entry]
type = "python"
path = "main.py"

[schema.input]
type = "object"

[schema.output]
type = "object"

[policy]
confirm = false
dry_run_supported = false
workspace_only = true
timeout_sec = 30
""",
        encoding="utf-8",
    )


class ParseCheckerCommandTests(unittest.TestCase):
    def test_acceptance_command(self) -> None:
        task = parse_checker_command("验收 write_text")
        self.assertIsNotNone(task)
        assert task is not None
        self.assertEqual(task.tool_name, "write_text")

    def test_check_command_with_reference(self) -> None:
        task = parse_checker_command("check npm_exec vs mvn_exec")
        self.assertIsNotNone(task)
        assert task is not None
        self.assertEqual(task.tool_name, "npm_exec")
        self.assertEqual(task.reference_tool, "mvn_exec")

    def test_non_command(self) -> None:
        self.assertIsNone(parse_checker_command("hello world"))


class VerdictMergeTests(unittest.TestCase):
    def test_fail_wins(self) -> None:
        items = [
            ChecklistItem("a", "pass", "ok"),
            ChecklistItem("b", "fail", "bad"),
        ]
        self.assertEqual(merge_checklist_verdict(items), "fail")

    def test_warn_without_fail(self) -> None:
        items = [
            ChecklistItem("a", "pass", "ok"),
            ChecklistItem("b", "warn", "skip"),
        ]
        self.assertEqual(merge_checklist_verdict(items), "warn")

    def test_hard_fail_overrides_llm_pass(self) -> None:
        hard = [ChecklistItem("demo_probe", "fail", "exit 1")]
        verdict, _ = merge_checker_results(
            hard,
            [],
            llm_verdict="pass",
            llm_summary="looks fine",
        )
        self.assertEqual(verdict, "fail")


class HardChecklistTests(unittest.TestCase):
    def test_missing_tool_fails(self) -> None:
        paths = AgentPaths.discover()
        items = build_hard_checklist(
            CheckerTask(tool_name="__no_such_tool__"),
            paths=paths,
        )
        self.assertTrue(any(item.status == "fail" for item in items))

    def test_write_text_passes_with_demo(self) -> None:
        paths = AgentPaths.discover()
        registry = ToolRegistry.load(paths)
        items = build_hard_checklist(
            CheckerTask(
                tool_name="write_text",
                demo_result={"attempted": True, "exit_code": 0},
            ),
            paths=paths,
            registry=registry,
        )
        self.assertTrue(any(item.id == "demo_probe" and item.status == "pass" for item in items))

    def test_broken_manifest_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evolve = Path(tmp)
            tool_dir = evolve / "tools" / "common" / "broken_chk"
            tool_dir.mkdir(parents=True)
            (tool_dir / "main.py").write_text("print('ok')\n", encoding="utf-8")
            (tool_dir / "tool.toml").write_text("not valid toml [[", encoding="utf-8")
            with self.assertRaises(Exception):
                parse_tool_manifest(tool_dir / "tool.toml", evolve_dir=evolve)


class CheckerTaskFromRecordTests(unittest.TestCase):
    def test_injects_demo_result(self) -> None:
        record = ScaffoldDemoRecord(
            tool_name="foo_tool",
            tool_dir="evolve/tools/common/foo_tool",
            demo_result={"attempted": True, "exit_code": 0, "stdout": "[PASS]"},
            auto_demo=True,
        )
        task = checker_task_from_demo_record(record)
        self.assertEqual(task.tool_name, "foo_tool")
        self.assertEqual(task.demo_result.get("exit_code"), 0)

    def test_find_auto_checker_target_prefers_auto_demo(self) -> None:
        session = ExecutorSession()
        session.segment_scaffold_tools["manual"] = ScaffoldDemoRecord(
            tool_name="manual",
            tool_dir="evolve/tools/common/manual",
            demo_result={"attempted": True, "exit_code": 0},
            auto_demo=False,
        )
        session.segment_scaffold_tools["auto"] = ScaffoldDemoRecord(
            tool_name="auto",
            tool_dir="evolve/tools/common/auto",
            demo_result={"attempted": True, "exit_code": 0},
            auto_demo=True,
        )
        target = find_auto_checker_target(session)
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.tool_name, "auto")

    def test_verdict_notice_labels(self) -> None:
        self.assertEqual(format_checker_verdict_notice("mvn_exec", "pass"), "验收 mvn_exec：通过")
        self.assertEqual(format_checker_verdict_notice("mvn_exec", "fail"), "验收 mvn_exec：失败")


class CompletionGateTests(unittest.TestCase):
    def test_blocks_scaffold_complete_claims_on_fail(self) -> None:
        from agent import apply_scaffold_completion_gate, claims_scaffold_complete

        text = "工具已沉淀完成，可以已验收使用。"
        self.assertTrue(claims_scaffold_complete(text))
        gated = apply_scaffold_completion_gate(text, "fail")
        self.assertNotIn("沉淀完成", gated)
        self.assertNotIn("已验收", gated)
        self.assertIn("〔验收未通过·已拦截〕", gated)

    def test_pass_allows_claims(self) -> None:
        from agent import apply_scaffold_completion_gate

        text = "工具已沉淀完成。"
        self.assertEqual(apply_scaffold_completion_gate(text, "pass"), text)


class CheckerRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self, copy_tool_dirs=("common/write_text",))
        session_dir = self.paths.data / "sessions" / "_checker_test"
        session_dir.mkdir(parents=True, exist_ok=True)
        self.session = Session(
            conversation_id="_checker_test",
            session_dir=session_dir,
            goal="checker unit test",
            meta=SessionMeta(
                topics=["coding"],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[],
            paths=self.paths,
        )
        self.session.save()
        self.msgs_before = len(self.session.messages)
        self.evolve_log = EvolveLog.for_agent(self.paths)

    def test_run_checker_mock_pass(self) -> None:
        read_args = json.dumps(
            {"path": "evolve/tools/common/write_text/tool.toml"},
            ensure_ascii=False,
        )

        class MockLLM:
            def __init__(self) -> None:
                self.responses = [
                    LLMResponse(
                        model="mock",
                        content=None,
                        tool_calls=[
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": read_args},
                            }
                        ],
                        finish_reason="tool_calls",
                        usage=None,
                        raw={},
                    ),
                    LLMResponse(
                        model="mock",
                        content="结构 OK。\nCHECKER_VERDICT: pass",
                        tool_calls=[],
                        finish_reason="stop",
                        usage=None,
                        raw={},
                    ),
                ]

            def chat(self, *_a, **_k) -> LLMResponse:
                return self.responses.pop(0)

        log_path = self.evolve_log.path
        before = len(read_events(log_path)) if log_path.is_file() else 0
        runner = SubagentRunner(paths=self.paths, evolve_log=self.evolve_log)
        result = runner.run_checker(
            CheckerTask(
                tool_name="write_text",
                demo_result={"attempted": True, "exit_code": 0},
            ),
            session=self.session,
            llm=MockLLM(),
            confirm_fn=lambda _p, _a: "y",
        )
        self.assertEqual(result.kind, "checker")
        self.assertEqual(result.verdict, "pass")
        overlay = format_subagent_overlay(result)
        self.assertIn("[子代理摘要 · checker]", overlay)
        self.assertIn("验收: PASS", overlay)
        self.assertEqual(len(self.session.messages), self.msgs_before)

        events = [
            e
            for e in read_events(log_path)[before:]
            if e.get("event") == EVENT_SUBAGENT_RUN
        ]
        self.assertTrue(events)
        self.assertEqual(events[-1].get("kind"), "checker")
        self.assertEqual(events[-1].get("verdict"), "pass")

    def test_checker_tools_subset(self) -> None:
        names = [t["function"]["name"] for t in build_checker_tools()]
        self.assertEqual(
            names,
            ["read_file", "list_dir", "glob_file_search", "grep"],
        )

    def test_cancel_event_aborts(self) -> None:
        cancel = threading.Event()
        cancel.set()

        class CancelLLM:
            def chat(self, *_a, **_k) -> LLMResponse:
                raise LLMCancelledError("cancelled")

            def set_cancel_event(self, _event: threading.Event) -> None:
                pass

        runner = SubagentRunner(paths=self.paths)
        with self.assertRaises(LLMCancelledError):
            runner.run_checker(
                CheckerTask(tool_name="write_text"),
                session=self.session,
                llm=CancelLLM(),
                cancel_event=cancel,
            )


class AutoCheckerSpawnTests(unittest.TestCase):
    def test_should_auto_spawn_requires_grow_scaffold(self) -> None:
        from agent import Agent
        from runtime_guards import checker_auto_on_scaffold

        paths = make_temp_agent_paths(self)
        session_dir = paths.data / "sessions" / "_checker_auto"
        session_dir.mkdir(parents=True, exist_ok=True)
        session = Session(
            conversation_id="_checker_auto",
            session_dir=session_dir,
            goal="auto checker",
            meta=SessionMeta(
                topics=["coding"],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S4",
                active_shell="grow",
            ),
            messages=[],
            paths=paths,
        )
        agent = Agent.create(session, confirm_fn=lambda _p, _a: "y")
        session.scaffold_tool_turn = True
        agent.executor.session.segment_scaffold_tools["demo"] = ScaffoldDemoRecord(
            tool_name="demo",
            tool_dir="evolve/tools/common/demo",
            demo_result={"attempted": True, "exit_code": 0},
            auto_demo=True,
        )

        prev = os.environ.get("CHECKER_AUTO_ON_SCAFFOLD")
        os.environ["CHECKER_AUTO_ON_SCAFFOLD"] = "1"
        try:
            self.assertTrue(checker_auto_on_scaffold())
            self.assertTrue(agent._should_auto_spawn_checker())
            session.meta.active_shell = "daily"
            self.assertFalse(agent._should_auto_spawn_checker())
        finally:
            if prev is None:
                os.environ.pop("CHECKER_AUTO_ON_SCAFFOLD", None)
            else:
                os.environ["CHECKER_AUTO_ON_SCAFFOLD"] = prev

    def test_finalize_applies_completion_gate(self) -> None:
        from agent import Agent

        paths = make_temp_agent_paths(self)
        session_dir = paths.data / "sessions" / "_checker_gate"
        session_dir.mkdir(parents=True, exist_ok=True)
        session = Session(
            conversation_id="_checker_gate",
            session_dir=session_dir,
            goal="gate",
            meta=SessionMeta(
                topics=["coding"],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[{"role": "assistant", "content": "工具已沉淀完成。"}],
            paths=paths,
        )
        agent = Agent.create(session, confirm_fn=lambda _p, _a: "y")

        class MockLLM:
            def chat(self, *_a, **_k) -> LLMResponse:
                return LLMResponse(
                    model="mock",
                    content="CHECKER_VERDICT: fail",
                    tool_calls=[],
                    finish_reason="stop",
                    usage=None,
                    raw={},
                )

            def set_cancel_event(self, _event: threading.Event) -> None:
                pass

        agent.llm = MockLLM()
        agent.executor.session.segment_scaffold_tools["gate_tool"] = ScaffoldDemoRecord(
            tool_name="gate_tool",
            tool_dir="evolve/tools/common/gate_tool",
            demo_result={"attempted": True, "exit_code": 1},
            auto_demo=True,
        )
        session.scaffold_tool_turn = True
        session.meta.active_shell = "grow"

        events: list[dict] = []
        agent.on_turn_event = events.append

        prev = os.environ.get("CHECKER_AUTO_ON_SCAFFOLD")
        os.environ["CHECKER_AUTO_ON_SCAFFOLD"] = "1"
        try:
            final_text, finish_reason, used, verdict, _rounds = agent._finalize_scaffold_checker(
                final_text="工具已沉淀完成。",
                finish_reason="completed",
            )
        finally:
            if prev is None:
                os.environ.pop("CHECKER_AUTO_ON_SCAFFOLD", None)
            else:
                os.environ["CHECKER_AUTO_ON_SCAFFOLD"] = prev

        self.assertTrue(used)
        self.assertEqual(verdict, "fail")
        self.assertNotIn("沉淀完成", final_text)
        self.assertTrue(any(e.get("type") == "checker.verdict" for e in events))
        self.assertEqual(session.messages[-1]["content"], final_text)


class ExternalSubagentPromptTests(unittest.TestCase):
    """IT-462 — evolve/subagents/*.md with inline fallback."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self, copy_tool_dirs=("common/write_text",))
        session_dir = self.paths.data / "sessions" / "_checker_ext"
        session_dir.mkdir(parents=True, exist_ok=True)
        self.session = Session(
            conversation_id="_checker_ext",
            session_dir=session_dir,
            goal="external prompt test",
            meta=SessionMeta(
                topics=["coding"],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[],
            paths=self.paths,
        )
        self.session.save()

    def test_checker_loads_external_prompt(self) -> None:
        marker = "UNIQUE_CHECKER_MARKER_462"
        sub_dir = self.paths.evolve / "subagents"
        sub_dir.mkdir(parents=True, exist_ok=True)
        (sub_dir / "checker_tool.md").write_text(
            f"你是 checker。\n{marker}\nCHECKER_VERDICT: pass",
            encoding="utf-8",
        )

        captured: list[str] = []

        class CaptureLLM:
            def chat(self, messages, **_k) -> LLMResponse:
                for msg in messages:
                    if msg.get("role") == "system":
                        captured.append(str(msg.get("content") or ""))
                return LLMResponse(
                    model="mock",
                    content=f"ok\nCHECKER_VERDICT: pass",
                    tool_calls=[],
                    finish_reason="stop",
                    usage=None,
                    raw={},
                )

        runner = SubagentRunner(paths=self.paths)
        result = runner.run_checker(
            CheckerTask(
                tool_name="write_text",
                demo_result={"attempted": True, "exit_code": 0},
            ),
            session=self.session,
            llm=CaptureLLM(),
            confirm_fn=lambda _p, _a: "y",
        )
        self.assertEqual(result.verdict, "pass")
        self.assertTrue(captured)
        self.assertIn(marker, captured[0])

    def test_checker_fallback_when_missing_file(self) -> None:
        sub_dir = self.paths.evolve / "subagents"
        if sub_dir.is_dir():
            for child in sub_dir.iterdir():
                child.unlink()

        captured: list[str] = []

        class CaptureLLM:
            def chat(self, messages, **_k) -> LLMResponse:
                for msg in messages:
                    if msg.get("role") == "system":
                        captured.append(str(msg.get("content") or ""))
                return LLMResponse(
                    model="mock",
                    content="CHECKER_VERDICT: pass",
                    tool_calls=[],
                    finish_reason="stop",
                    usage=None,
                    raw={},
                )

        runner = SubagentRunner(paths=self.paths)
        result = runner.run_checker(
            CheckerTask(
                tool_name="write_text",
                demo_result={"attempted": True, "exit_code": 0},
            ),
            session=self.session,
            llm=CaptureLLM(),
            confirm_fn=lambda _p, _a: "y",
        )
        self.assertEqual(result.verdict, "pass")
        self.assertTrue(captured)
        self.assertIn("checker 子代理", captured[0])
        self.assertIn("CHECKER_VERDICT", captured[0])


if __name__ == "__main__":
    unittest.main()
