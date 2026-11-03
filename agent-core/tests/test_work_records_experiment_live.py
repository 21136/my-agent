from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent
from llm_client import LLMClient, LLMResponse, load_config
from llm_models import get_registry
from llm_secrets import get_llm_secret
from paths import AgentPaths
from session import create_new
from work_records import WorkRecordStore
from work_records_experiment import WorkRecordExperimentController

LIVE_OPT_IN = "MY_AGENT_LIVE_WORK_RECORD_EXPERIMENT"
MODEL_ID = "0x567-flash"
PROJECT_ID = "synthetic-project-live"


def _paths(root: Path) -> AgentPaths:
    root.mkdir(parents=True, exist_ok=True)
    return AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")


def _recognizer_messages(text: str, project_id: str, items: list[dict[str, Any]]) -> list[dict[str, str]]:
    # Whitelist a tiny schema; never serialize Session, AgentPaths, source refs, or raw records.
    records = []
    for item in items:
        scope = item.get("scope", {})
        records.append({
            "scope": "project" if scope.get("kind") == "project" else "independent",
            "title": item.get("title", ""),
            "requirements": [r.get("text", "") for r in item.get("requirements", [])[:5]],
        })
    context = {
        "current_project_id": project_id,
        "current_project_name": "合成项目A" if project_id == PROJECT_ID else "",
        "existing_synthetic_records": records,
        "user_text": text,
    }
    return [
        {
            "role": "system",
            "content": (
                "你是工作记录意图识别器。仅根据 user_text 返回一个 JSON 对象，不要 Markdown。"
                "字段必须严格匹配动作：record={action,scope,title,requirement}; "
                "recall={action,query}; none 或 uncertain={action}。"
                "本测试中 current_project_name（合成项目A）就是当前项目，必须用 current_project_id 作为 project_id。"
                "scope 对项目记录必须是 {kind:project,project_id:当前项目ID}，独立事项是 {kind:independent}。"
                "recall 的 query 必须是现有记录标题或要求中的短原文连续片段，最多12个汉字；不要输出完整问句或改写。"
                "仅在事项与范围明确时 record；有歧义一律 uncertain，不得猜测。不得调用工具。"
            ),
        },
        {"role": "user", "content": json.dumps(context, ensure_ascii=False, separators=(",", ":"))},
    ]


def _strict_decision(content: str, project_id: str = PROJECT_ID) -> dict[str, Any]:
    """Accept exactly one JSON object with an action-specific closed schema."""
    try:
        value = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return {"action": "uncertain"}
    if not isinstance(value, dict) or not isinstance(value.get("action"), str):
        return {"action": "uncertain"}
    action = value["action"]
    expected = {
        "record": {"action", "scope", "title", "requirement"},
        "recall": {"action", "query"},
        "none": {"action"},
        "uncertain": {"action"},
    }.get(action)
    if expected is None or set(value) != expected:
        return {"action": "uncertain"}
    if action == "record":
        scope = value["scope"]
        if not isinstance(scope, dict) or scope.get("kind") not in {"project", "independent"}:
            return {"action": "uncertain"}
        if scope["kind"] == "project":
            if set(scope) != {"kind", "project_id"} or scope["project_id"] != project_id:
                return {"action": "uncertain"}
        elif set(scope) != {"kind"}:
            return {"action": "uncertain"}
        if not all(isinstance(value[k], str) and value[k].strip() for k in ("title", "requirement")):
            return {"action": "uncertain"}
    elif action == "recall" and (not isinstance(value["query"], str) or not value["query"].strip()):
        return {"action": "uncertain"}
    return value


def _decision_rejection_reason(content: str, project_id: str, decision: dict[str, Any]) -> str:
    if decision.get("action") != "uncertain":
        return "accepted"
    try:
        raw = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        raw = None
    if isinstance(raw, dict) and raw == {"action": "uncertain"}:
        return "model explicitly returned uncertain; strict parser accepted that fail-closed decision"
    try:
        value = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        return f"invalid JSON: {type(exc).__name__}"
    if not isinstance(value, dict):
        return "top-level JSON value is not an object"
    action = value.get("action")
    expected = {"record": {"action", "scope", "title", "requirement"}, "recall": {"action", "query"}, "none": {"action"}, "uncertain": {"action"}}.get(action)
    if expected is None:
        return f"unknown or missing action: {action!r}"
    if set(value) != expected:
        return f"keys do not exactly match {action} schema: got {sorted(value)}"
    if action == "record":
        scope = value.get("scope")
        if not isinstance(scope, dict) or scope.get("kind") not in {"project", "independent"}:
            return "record scope is missing or unsupported"
        if scope["kind"] == "project" and (set(scope) != {"kind", "project_id"} or scope.get("project_id") != project_id):
            return "project scope does not exactly match the synthetic current project"
        if scope["kind"] == "independent" and set(scope) != {"kind"}:
            return "independent scope contains unsupported fields"
        if not all(isinstance(value.get(k), str) and value[k].strip() for k in ("title", "requirement")):
            return "record title or requirement is empty or not a string"
    if action == "recall" and (not isinstance(value.get("query"), str) or not value["query"].strip()):
        return "recall query is empty or not a string"
    return "strict parser failed closed"


def _safe_live_output_for_failure(text: str) -> str:
    # Outputs are based on synthetic-only prompts; redact common credential/path-shaped values defensively.
    import re
    text = re.sub(r"(?i)(api[_-]?key|secret|token)(\s*[=:]\s*)[^\s,\"}]+", r"\1\2<REDACTED>", text)
    text = re.sub(r"[A-Za-z]:\\[^\s,\"]+", "<PATH-REDACTED>", text)
    return text[:1200]


class _LLMRecognizer:
    def __init__(self, client: Any, project_id: str) -> None:
        self.client = client
        self.project_id = project_id
        self.calls: list[list[dict[str, str]]] = []
        self.responses: list[dict[str, Any]] = []
        self.tool_options: list[Any] = []

    def __call__(self, text: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        messages = _recognizer_messages(text, self.project_id, items)
        self.calls.append(messages)
        self.tool_options.append(None)  # The call below explicitly sends tools=None.
        response = self.client.chat(
            messages, model=MODEL_ID, temperature=0.0, tools=None,
            response_format={"type": "json_object"},
        )
        if response.tool_calls:
            decision = {"action": "uncertain"}
            reason = "provider returned tool_calls despite tools=None"
        else:
            decision = _strict_decision(response.content or "", self.project_id)
            reason = _decision_rejection_reason(response.content or "", self.project_id, decision)
        self.responses.append({"content": response.content or "", "tool_calls": bool(response.tool_calls), "decision": decision, "reason": reason})
        return decision


class _FakeClient:
    def __init__(self, outputs: list[str] | str) -> None:
        self.outputs = [outputs] if isinstance(outputs, str) else list(outputs)
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        self.calls.append({"messages": messages, **kwargs})
        return LLMResponse(
            model="synthetic", content=self.outputs.pop(0), tool_calls=[],
            finish_reason="stop", usage=None, raw={},
        )


def test_recognizer_request_is_minimal_private_and_tools_disabled():
    sentinel_path = "SYNTHETIC_PRIVATE_PATH_SENTINEL"
    sentinel_secret = "SYNTHETIC_SECRET_SENTINEL"
    item = {
        "title": "合成事项", "scope": {"kind": "independent", "object_ref": sentinel_path},
        "requirements": [{"text": "核对合成字段", "source_ref": sentinel_path}],
        "source_refs": [sentinel_path], "storage_path": sentinel_path,
    }
    client = _FakeClient('{"action":"recall","query":"合成事项"}')
    recognizer = _LLMRecognizer(client, PROJECT_ID)
    assert recognizer("查一下合成事项", [item]) == {"action": "recall", "query": "合成事项"}
    call = client.calls[0]
    assert call["model"] == MODEL_ID
    assert call["tools"] is None
    assert call["response_format"] == {"type": "json_object"}
    outbound = json.dumps(call["messages"], ensure_ascii=False)
    assert sentinel_path not in outbound and sentinel_secret not in outbound
    assert '"object_ref"' not in outbound and '"source_ref"' not in outbound
    assert "合成事项" in outbound and "核对合成字段" in outbound


@pytest.mark.parametrize("content", [
    'prefix {"action":"record","scope":{"kind":"independent"},"title":"X","requirement":"Y"}',
    '{"action":"record","scope":{"kind":"independent"},"title":"X","requirement":"Y","extra":"z"}',
    '{"action":"record","scope":{"kind":"project","project_id":"wrong"},"title":"X","requirement":"Y"}',
    '{"action":"record","scope":{"kind":"independent","path":"x"},"title":"X","requirement":"Y"}',
])
def test_ambiguous_or_non_strict_structured_output_never_records(tmp_path: Path, content: str):
    paths = _paths(tmp_path / "agent")
    session = create_new(paths, conversation_id="synthetic-ambiguous")
    session.meta.project_id = PROJECT_ID
    store = WorkRecordStore(session)
    llm = _FakeClient(content)
    recognizer = _LLMRecognizer(llm, PROJECT_ID)
    agent_llm = _FakeClient("safe synthetic response")
    agent = Agent.create(session, llm=agent_llm, confirm_fn=Mock())
    # Deny tools at the Agent loop as a second, independent safety boundary.
    original_loop = agent._run_parent_tool_loop
    agent._run_parent_tool_loop = lambda **kw: original_loop(**{**kw, "tools": []})
    agent.executor.execute = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("tools disabled"))
    result = WorkRecordExperimentController(agent=agent, store=store, recognizer=recognizer).run_turn("也许记一下，范围不确定")
    assert result.turn is None  # Ambiguity must neither record nor invoke Agent.
    assert store.list_items() == []
    assert agent_llm.calls == []
    assert len(llm.calls) == 1


def test_strict_parser_rejects_trailing_data_and_accepts_exact_actions():
    assert _strict_decision('{"action":"uncertain"} trailing') == {"action": "uncertain"}
    assert _strict_decision('{"action":"recall","query":" 合成项目 "}') == {"action": "recall", "query": " 合成项目 "}


def test_opt_in_live_recognizer_identifies_records_and_recall_using_synthetic_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    if os.environ.get(LIVE_OPT_IN) != "1":
        pytest.skip(f"live 0x567-flash request is opt-in; set {LIVE_OPT_IN}=1")

    sandbox_paths = AgentPaths.discover(start=Path(__file__).resolve())
    registry = get_registry(sandbox_paths)
    configured = registry.get(MODEL_ID)
    if configured is None or configured.id != MODEL_ID:
        pytest.fail(f"sandbox model registry does not contain explicit model {MODEL_ID}")
    if configured.api_key_env and not get_llm_secret(configured.api_key_env, sandbox_paths):
        pytest.skip("0x567 credential is unavailable from sandbox config; no live request made")

    paths = _paths(tmp_path / "temporary-agent-root")
    session = create_new(paths, conversation_id="synthetic-live-session")
    session.meta.project_id = PROJECT_ID
    session.meta.llm_model = MODEL_ID
    store = WorkRecordStore(session)

    # The real Agent runs against a deterministic, safe fake main model; only the restricted
    # recognizer below uses the configured live transport. Agent context never goes to network.
    main_model = _FakeClient("Synthetic response. No real work was performed.")
    agent = Agent.create(session, llm=main_model, confirm_fn=Mock())
    original_loop = agent._run_parent_tool_loop
    agent._run_parent_tool_loop = lambda **kw: original_loop(**{**kw, "tools": []})
    agent.executor.execute = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("tools disabled"))

    live_client = LLMClient(load_config(), paths=sandbox_paths)
    recognizer = _LLMRecognizer(live_client, PROJECT_ID)
    controller = WorkRecordExperimentController(
        agent=agent, store=store, recognizer=recognizer,
        # Stable caller identities belong to these synthetic opt-in submissions.
        idempotency_key_provider=lambda text: (
            "synthetic-live-experiment:project-a" if text.startswith("请把合成项目A")
            else "synthetic-live-experiment:independent-b" if text.startswith("另记一个独立事项")
            else None
        ),
    )
    # All prompts are fictional and contain only the exact requests plus whitelisted synthetic record fields.
    project_turn = controller.run_turn("请把合成项目A的导出字段核对事项记下来：检查合成字段名称。")
    assert project_turn.item_id, (
        "project recognition failed closed; "
        f"diagnostic={{{recognizer.responses[-1]['reason']}}}; "
        f"tool_calls={recognizer.responses[-1]['tool_calls']}; "
        f"model_output={_safe_live_output_for_failure(recognizer.responses[-1]['content'])!r}"
    )
    independent_turn = controller.run_turn("另记一个独立事项：整理虚构清单的三条分类。")
    assert independent_turn.item_id, (
        "independent recognition failed closed; "
        f"diagnostic={{{recognizer.responses[-1]['reason']}}}; "
        f"tool_calls={recognizer.responses[-1]['tool_calls']}; "
        f"model_output={_safe_live_output_for_failure(recognizer.responses[-1]['content'])!r}"
    )
    recall_turn = controller.run_turn("刚才记录的合成项目A导出字段事项是什么？")

    records = store.list_items()
    assert project_turn.item_id and independent_turn.item_id
    assert len(records) == 2
    assert records[0]["scope"] == {"kind": "project", "project_id": PROJECT_ID}
    assert records[1]["scope"]["kind"] == "independent"
    assert "合成项目A" in recall_turn.reply or "导出字段" in recall_turn.reply, (
        f"recall did not resolve to saved record; diagnostic={{{recognizer.responses[-1]['reason']}}}; "
        f"model_output={_safe_live_output_for_failure(recognizer.responses[-1]['content'])!r}; "
        f"parsed_decision={recognizer.responses[-1]['decision']!r}; reply={recall_turn.reply!r}"
    )
    assert len(recognizer.calls) == 3
    assert recognizer.tool_options == [None, None, None]
    assert all(not result["tool_calls"] for result in recognizer.responses)
    agent_user_texts = {
        message["content"]
        for call in main_model.calls
        for message in call["messages"]
        if message.get("role") == "user"
    }
    assert "请把合成项目A的导出字段核对事项记下来：检查合成字段名称。" in agent_user_texts
    assert "另记一个独立事项：整理虚构清单的三条分类。" in agent_user_texts
    assert "刚才记录的合成项目A导出字段事项是什么？" not in agent_user_texts
    # Main Agent is a deterministic fake; executor hard-denies execution and fake emits no tool calls.
    for call in recognizer.calls:
        serialized = json.dumps(call, ensure_ascii=False)
        assert str(tmp_path) not in serialized
        assert str(sandbox_paths.agent_root) not in serialized
        assert "api_key" not in serialized.lower() and "secret" not in serialized.lower()
    assert all("tool" not in message["role"] for message in session.messages)


