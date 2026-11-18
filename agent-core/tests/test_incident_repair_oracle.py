from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

import llm_client
from llm_client import LLMResponse
from session import Session, SessionError, create_new
from paths import AgentPaths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry


INCIDENT_MARKER = "INCIDENT_EXPLORE_PENDING_WRITE_CONFLICT"
_PENDING_CALL_ID = "incident-parent-explore-call"
_READ_FILE_CALL_ID = "incident-subagent-read-call"
_FILE_PATH = "workspace/incident-repro.txt"
_FILE_CONTENT = "the real explore read path completed\n"


class _ExploreReadClient:
    """Deterministic model boundary; the runner and read tool stay real."""

    def __init__(self) -> None:
        self.requests: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, Any]], **_kwargs: Any) -> LLMResponse:
        self.requests.append(copy.deepcopy(messages))
        if len(self.requests) == 1:
            return LLMResponse(
                model="fixture",
                content=None,
                tool_calls=[{
                    "id": _READ_FILE_CALL_ID,
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": _FILE_PATH}),
                    },
                }],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            )
        return LLMResponse(
            model="fixture",
            content="The file was read by the explore subagent.",
            tool_calls=[],
            finish_reason="stop",
            usage=None,
            raw={},
        )


def _recovered_pending_reply(messages_path: Path) -> bool:
    """Recognize the exact startup-style repair of our known pending call."""
    try:
        messages = [
            json.loads(line)
            for line in messages_path.read_text(encoding="utf-8").splitlines()
        ]
    except (OSError, json.JSONDecodeError):
        return False
    had_parent_call = any(
        message.get("role") == "assistant"
        and any(
            call.get("id") == _PENDING_CALL_ID
            for call in message.get("tool_calls", [])
            if isinstance(call, dict)
        )
        for message in messages
    )
    repaired_reply = any(
        message.get("role") == "tool"
        and message.get("tool_call_id") == _PENDING_CALL_ID
        and "session recovered" in str(message.get("content", ""))
        for message in messages
    )
    return had_parent_call and repaired_reply


def test_live_explore_read_does_not_reopen_and_repair_parent_pending_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real subagent read must leave the active parent's persisted turn writable."""
    with tempfile.TemporaryDirectory(prefix="incident-explore-") as temp_root:
        root = Path(temp_root)
        (root / "workspace").mkdir()
        (root / "evolve").mkdir()
        (root / "evolve" / "_index.core.toml").write_text("", encoding="utf-8")
        monkeypatch.setenv("MY_AGENT_ROOT", str(root))
        (root / "data" / "sessions").mkdir(parents=True)
        paths = AgentPaths(
            agent_root=root,
            workspace=root / "workspace",
            data=root / "data",
            evolve=root / "evolve",
        )
        parent = create_new(paths, conversation_id="incident-explore-parent")
        (paths.agent_root / _FILE_PATH).parent.mkdir(parents=True, exist_ok=True)
        (paths.agent_root / _FILE_PATH).write_text(_FILE_CONTENT, encoding="utf-8")

        pending_call = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": _PENDING_CALL_ID,
                "type": "function",
                "function": {
                    "name": "explore",
                    "arguments": json.dumps({"task": f"Read {_FILE_PATH}"}),
                },
            }],
        }
        parent.append_message(pending_call)
        messages_path = parent.messages_path
        bytes_before_dispatch = messages_path.read_bytes()
        persisted_before = [
            json.loads(line) for line in bytes_before_dispatch.decode("utf-8").splitlines()
        ]
        assert persisted_before[-1] == pending_call

        fake_llm = _ExploreReadClient()
        monkeypatch.setattr(llm_client, "LLMClient", lambda: fake_llm)
        executor = ToolExecutor(
            registry=ToolRegistry.load(paths),
            session=ExecutorSession(session_dir=parent.session_dir),
            bound_session=parent,
        )

        explore_result = executor.run(
            "explore", {"task": f"Read {_FILE_PATH}", "max_rounds": 2}
        )
        assert explore_result.ok, (
            "NON_INCIDENT_EXPLORE_EXECUTION_FAILURE: "
            f"{explore_result.error or explore_result.data}"
        )
        assert len(fake_llm.requests) == 2, (
            "NON_INCIDENT_EXPLORE_EXECUTION_FAILURE: real SubagentRunner did not "
            "complete the read tool round and summary round"
        )
        subagent_tool_messages = [
            message
            for message in fake_llm.requests[1]
            if message.get("role") == "tool"
            and message.get("tool_call_id") == _READ_FILE_CALL_ID
        ]
        assert len(subagent_tool_messages) == 1, (
            "NON_INCIDENT_EXPLORE_EXECUTION_FAILURE: real ToolExecutor read_file "
            "result was not returned to SubagentRunner"
        )
        assert _FILE_CONTENT.strip() in str(subagent_tool_messages[0].get("content", "")), (
            "NON_INCIDENT_EXPLORE_EXECUTION_FAILURE: read_file did not return the "
            "fixture contents"
        )

        # The live parent still has its original pending assistant call. The old
        # Session.load inside explore repairs the on-disk copy while this call is
        # pending; that exact mutation is the incident contract, not an arbitrary
        # subagent or append error.
        disk_after_explore = messages_path.read_bytes()
        if disk_after_explore != bytes_before_dispatch:
            recovered_our_pending_call = _recovered_pending_reply(messages_path)
            if recovered_our_pending_call:
                pytest.fail(INCIDENT_MARKER)
            raise AssertionError(
                "NON_INCIDENT_SESSION_DISK_MUTATION: explore changed the parent "
                "session but did not recover this pending call"
            )

        # On the repair, no reopen or write occurs and the parent can append the
        # actual explore tool result.
        tool_result = {
            "role": "tool",
            "tool_call_id": _PENDING_CALL_ID,
            "content": json.dumps(explore_result.to_dict(), ensure_ascii=False),
        }
        try:
            parent.append_message(tool_result)
        except SessionError as exc:
            disk_changed = messages_path.read_bytes() != bytes_before_dispatch
            recovered_our_pending_call = _recovered_pending_reply(messages_path)
            if (
                str(exc) == "session messages changed or contain damaged records; reload before appending"
                and disk_changed
                and recovered_our_pending_call
            ):
                pytest.fail(INCIDENT_MARKER)
            raise AssertionError(
                "NON_INCIDENT_SESSION_APPEND_FAILURE: "
                f"{type(exc).__name__}: {exc}; disk_changed={disk_changed}; "
                f"recovered_our_pending_call={recovered_our_pending_call}"
            ) from exc

        assert messages_path.read_bytes() != bytes_before_dispatch
        persisted_after = [
            json.loads(line) for line in messages_path.read_text(encoding="utf-8").splitlines()
        ]
        assert persisted_after[-1] == tool_result
        reopened = Session.load(paths, parent.conversation_id)
        assert reopened.messages[-1] == tool_result



def test_genuine_orphan_is_still_repaired(tmp_path, monkeypatch):
    (tmp_path / "data" / "sessions").mkdir(parents=True)
    (tmp_path / "evolve").mkdir()
    (tmp_path / "evolve" / "_index.core.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("MY_AGENT_ROOT", str(tmp_path))
    (tmp_path / "workspace").mkdir()
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    session = create_new(paths, conversation_id="incident-crash-orphan")
    session.append_message({
        "role": "assistant", "content": None,
        "tool_calls": [{"id": _PENDING_CALL_ID, "type": "function", "function": {
            "name": "explore", "arguments": "{}",
        }}],
    })
    # No active executor uses this conversation; standard startup recovery must
    # still close a genuinely interrupted tool exchange.
    reopened = Session.load(paths, session.conversation_id)
    assert reopened.messages[-1]["role"] == "tool"
    assert reopened.messages[-1]["tool_call_id"] == _PENDING_CALL_ID
    assert _recovered_pending_reply(session.messages_path)
