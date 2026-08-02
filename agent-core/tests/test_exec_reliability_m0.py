"""EXEC-RELIABILITY M0: service success-claim gate + exec circuit (IT-160/161)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent, _is_retryable
from exec_reliability import (
    EXEC_CIRCUIT_NUDGE_MESSAGE,
    SERVICE_SUCCESS_REPLACEMENT,
    apply_service_success_gate,
    call_fingerprint,
    claims_service_success,
    clear_circuit_state,
    is_circuit_countable_failure,
    record_circuit_failure,
    run_service_postcondition_ok,
)
from llm_client import LLMResponse
from session import Session, SessionMeta, utc_now_iso
from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.schema import ToolErrorCode, tool_fail, tool_ok


class _MockLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def set_cancel_event(self, _event: Any) -> None:
        return None

    def chat(self, *args: Any, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        if not self._responses:
            raise AssertionError("no mock LLM responses left")
        return self._responses.pop(0)


def _run_service_envelope(*, ok: bool, ready: bool, alive: bool) -> str:
    return json.dumps(
        {
            "ok": ok,
            "tool": "run_evolved",
            "data": {
                "tool_name": "run_service",
                "action": "start",
                "ready": ready,
                "state": {"alive": alive, "ready_port": 3000, "status": "running"},
            },
            "error": None,
            "duration_ms": 1,
        },
        ensure_ascii=False,
    )


class ServiceClaimUnitTests(unittest.TestCase):
    def test_detects_start_claims(self) -> None:
        self.assertTrue(claims_service_success("前端已启动，可访问 http://localhost:3000"))
        self.assertTrue(claims_service_success("Vite is running on port 3000"))
        self.assertFalse(claims_service_success("还在装依赖，稍等"))

    def test_rewrites_when_postcondition_fail(self) -> None:
        text = "前端已启动，请打开 http://localhost:3000"
        gated = apply_service_success_gate(text, postcondition_ok=False)
        self.assertIn(SERVICE_SUCCESS_REPLACEMENT, gated)
        self.assertNotIn("已启动", gated)

    def test_keeps_claims_when_ok(self) -> None:
        text = "前端已启动"
        self.assertEqual(apply_service_success_gate(text, postcondition_ok=True), text)

    def test_postcondition_from_messages(self) -> None:
        dead = [{"role": "tool", "content": _run_service_envelope(ok=True, ready=False, alive=False)}]
        live = [{"role": "tool", "content": _run_service_envelope(ok=True, ready=True, alive=True)}]
        self.assertFalse(run_service_postcondition_ok(dead))
        self.assertTrue(run_service_postcondition_ok(live))
        # latest dead overrides earlier live
        self.assertFalse(run_service_postcondition_ok(live + dead))


class IT160SuccessClaimGateTests(unittest.TestCase):
    def test_it160_blocks_started_claim_when_service_dead(self) -> None:
        with temporary_agent_paths() as paths:
            start_args = json.dumps(
                {
                    "tool_name": "run_service",
                    "arguments": {
                        "action": "start",
                        "command": "npm run dev",
                        "working_dir": "workspace/demo/frontend",
                    },
                },
                ensure_ascii=False,
            )
            with_tools = LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "run_evolved", "arguments": start_args},
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            )
            final = LLMResponse(
                model="mock",
                content="前端已启动，可访问 http://localhost:3000",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            )
            session = Session(
                conversation_id="_it160",
                session_dir=paths.data / "sessions" / "_it160",
                goal="it160",
                meta=SessionMeta(
                    topics=[],
                    llm_model="mock",
                    updated_at=utc_now_iso(),
                    phase="S4",
                    turn_mode="agent",
                ),
                messages=[],
                paths=paths,
            )
            session.save()
            mock = _MockLLM(responses=[with_tools, final])
            agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")

            soft_fail = tool_ok(
                "run_evolved",
                {
                    "tool_name": "run_service",
                    "action": "start",
                    "ready": False,
                    "state": {"alive": False, "ready_port": 3000, "status": "exited"},
                    "warning": "started but ready criteria not met",
                },
            )
            with patch.object(agent.executor, "run", return_value=soft_fail):
                result = agent.run_turn("把前端拉起来", spawn_explore=False)

            self.assertIn(SERVICE_SUCCESS_REPLACEMENT, result.assistant_text)
            self.assertNotIn("已启动", result.assistant_text)
            last_assistant = [
                m for m in session.messages if m.get("role") == "assistant" and m.get("content")
            ][-1]
            self.assertIn(SERVICE_SUCCESS_REPLACEMENT, last_assistant["content"])


class CircuitUnitTests(unittest.TestCase):
    def test_fingerprint_stable(self) -> None:
        a = call_fingerprint(
            "run_evolved",
            {
                "tool_name": "run_command",
                "arguments": {"command": "npm run dev", "working_dir": "frontend"},
            },
        )
        b = call_fingerprint(
            "run_evolved",
            {
                "tool_name": "run_command",
                "arguments": {"command": "npm run dev", "working_dir": "frontend"},
            },
        )
        self.assertEqual(a, b)
        c = call_fingerprint(
            "run_evolved",
            {
                "tool_name": "run_command",
                "arguments": {"command": "npm run build", "working_dir": "frontend"},
            },
        )
        self.assertNotEqual(a, c)

    def test_soft_ready_false_counts(self) -> None:
        result = tool_ok(
            "run_evolved",
            {
                "tool_name": "run_service",
                "action": "start",
                "ready": False,
                "state": {"alive": False},
            },
        )
        self.assertTrue(is_circuit_countable_failure(result))

    def test_schema_validation_does_not_count(self) -> None:
        result = tool_fail(
            "run_evolved",
            ToolErrorCode.VALIDATION_ERROR,
            "missing command",
            details={"retry": True},
        )
        self.assertFalse(is_circuit_countable_failure(result))

    def test_opens_at_three(self) -> None:
        session = ExecutorSession()
        clear_circuit_state(session)
        fp = "run_evolved|run_command|command=npm run dev"
        self.assertFalse(record_circuit_failure(session, fp))
        self.assertFalse(record_circuit_failure(session, fp))
        self.assertTrue(record_circuit_failure(session, fp))
        self.assertIn(fp, session.circuit_open_fingerprints)


class IT161CircuitBreakerTests(unittest.TestCase):
    def test_it161_third_failure_opens_and_fourth_blocked(self) -> None:
        with temporary_agent_paths() as paths:
            args = {"path": "workspace"}
            fail = tool_fail(
                "list_dir",
                "execution_error",
                "command failed",
                details={"exit_code": 1},
            )
            session = ExecutorSession(session_dir=paths.data / "sessions" / "_it161")
            from tools.registry import ToolRegistry

            registry = ToolRegistry.load(paths)
            executor = ToolExecutor(
                registry=registry,
                session=session,
                confirm_fn=lambda _p, _a: "y",
            )
            executor.begin_execute_segment()

            call_count = {"n": 0}

            def _fake_execute(name: str, arguments: dict[str, Any], *, started: float):
                call_count["n"] += 1
                return fail

            with patch.object(executor, "_execute_builtin", side_effect=_fake_execute):
                for _ in range(3):
                    result = executor.run("list_dir", args)
                    self.assertFalse(result.ok)

                self.assertEqual(call_count["n"], 3)
                self.assertTrue(session.circuit_open_fingerprints)
                self.assertEqual(session.circuit_just_opened, call_fingerprint("list_dir", args))

                blocked = executor.run("list_dir", args)
                self.assertFalse(blocked.ok)
                assert blocked.error is not None
                self.assertEqual(blocked.error.details.get("guard_type"), "exec_circuit")
                self.assertFalse(_is_retryable(blocked))
                self.assertEqual(call_count["n"], 3)  # fourth did not execute

    def test_it161_nudge_injected_in_agent_loop(self) -> None:
        with temporary_agent_paths() as paths:
            list_args = json.dumps({"path": "workspace"}, ensure_ascii=False)

            def _tool_resp(cid: str) -> LLMResponse:
                return LLMResponse(
                    model="mock",
                    content=None,
                    tool_calls=[
                        {
                            "id": cid,
                            "type": "function",
                            "function": {"name": "list_dir", "arguments": list_args},
                        }
                    ],
                    finish_reason="tool_calls",
                    usage=None,
                    raw={},
                )

            responses = [_tool_resp(f"c{i}") for i in range(1, 4)]
            responses.append(
                LLMResponse(
                    model="mock",
                    content="依赖坏了，需要换招。",
                    tool_calls=[],
                    finish_reason="stop",
                    usage=None,
                    raw={},
                )
            )
            session = Session(
                conversation_id="_it161_nudge",
                session_dir=paths.data / "sessions" / "_it161_nudge",
                goal="it161",
                meta=SessionMeta(
                    topics=[],
                    llm_model="mock",
                    updated_at=utc_now_iso(),
                    phase="S4",
                    turn_mode="agent",
                ),
                messages=[],
                paths=paths,
            )
            session.save()
            mock = _MockLLM(responses=responses)
            agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")
            fail = tool_fail(
                "list_dir",
                "execution_error",
                "command failed",
                details={"exit_code": 1},
            )
            with patch.object(agent.executor, "_execute_builtin", return_value=fail):
                agent.run_turn("再列一次目录", spawn_explore=False)

            nudge = [
                m
                for m in session.messages
                if m.get("content") == EXEC_CIRCUIT_NUDGE_MESSAGE
            ]
            self.assertEqual(len(nudge), 1, session.messages[-12:])
            self.assertTrue(agent.executor.session.circuit_open_fingerprints)


if __name__ == "__main__":
    unittest.main()
