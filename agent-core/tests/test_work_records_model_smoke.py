from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_client import LLMClient, LLMResponse, load_config
from paths import AgentPaths
from session import create_new
from work_records import WorkRecordStore


PROJECT_ID = "synthetic-project-A"
PROJECT_TITLE = "项目A：检查导出流程"
RESUME_TITLE = "独立简历事项：整理技能摘要"
_FAKE_PATH = "C:/synthetic/private/projectA"
_FAKE_SECRET = "SYNTHETIC_SECRET_SENTINEL_DO_NOT_SEND"


def _saved_records(tmp_path: Path) -> list[dict[str, Any]]:
    """Create and then re-read only synthetic records below pytest's temp root."""
    root = tmp_path / "agent-root"
    root.mkdir()
    paths = AgentPaths(root, root / "workspace", root / "data", root / "evolve")
    session = create_new(paths, conversation_id="model-smoke-session")
    session.meta.project_id = PROJECT_ID
    store = WorkRecordStore(session)

    project = store.create_item(
        title=PROJECT_TITLE,
        scope={"kind": "project", "project_id": PROJECT_ID},
        source_refs=[{"kind": "project", "id": PROJECT_ID}],
    )
    project_req = store.add_requirement(
        project["item_id"], "核对导出流程是否运行", event_id="synthetic-project-req",
        source_ref={"kind": "project", "id": PROJECT_ID},
    )
    store.record_result(
        project["item_id"], project_req["requirement_id"],
        run_id="synthetic-project-run", result_id="synthetic-project-result",
        status="unknown", summary="测试尚未运行；没有实际执行导出流程。",
        verification={"status": "not_verified", "summary": "未运行测试"},
    )

    resume = store.create_item(
        title=RESUME_TITLE,
        scope={"kind": "independent"},
    )
    resume_req = store.add_requirement(
        resume["item_id"], "拟更新技能摘要，暂未编辑", event_id="synthetic-resume-req",
    )
    store.record_result(
        resume["item_id"], resume_req["requirement_id"],
        run_id="synthetic-resume-run", result_id="synthetic-resume-result",
        status="in_progress", summary="仅记录待办；简历未修改。",
        verification={"status": "not_verified", "summary": "未检查简历"},
    )
    return store.list_items()


def _summary_prompt(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    # Curate a minimal, path-free prompt: do not serialize source refs or raw records.
    lines = ["请用中文分别概括下列两条合成记录；标明事项归属、是否运行/完成，不要把独立简历事项归入项目A。"]
    for item in items:
        scope = item["scope"]["kind"]
        owner = "项目A" if scope == "project" and item["scope"].get("project_id") == PROJECT_ID else "独立事项"
        run = item["runs"][-1]
        lines.append(
            f"归属={owner}；事项={item['title']}；状态={run['status']}；"
            f"结果={run['result_summary']}；核验={run['verification']['status']}"
        )
    return [{"role": "user", "content": "\n".join(lines)}]


class _CapturingFakeClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.messages: list[dict[str, str]] | None = None
        self.kwargs: dict[str, Any] = {}

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        self.messages = messages
        self.kwargs = kwargs
        return LLMResponse(
            model="fake", content=self.response, tool_calls=[], finish_reason="stop",
            usage=None, raw={},
        )


def test_fake_client_prompt_uses_saved_records_without_paths_or_secrets(tmp_path):
    items = _saved_records(tmp_path)
    prompt = _summary_prompt(items)
    fake = _CapturingFakeClient("项目A测试未运行；独立简历事项尚未修改。")
    response = fake.chat(prompt, model="0x567-flash")

    assert response.content
    assert fake.messages == prompt
    assert fake.kwargs == {"model": "0x567-flash"}
    content = prompt[0]["content"]
    assert PROJECT_TITLE in content and RESUME_TITLE in content
    assert "未运行" in content
    assert _FAKE_PATH not in content
    assert _FAKE_SECRET not in content
    assert str(tmp_path) not in content
    assert all("source_refs" not in str(message) for message in prompt)


@pytest.mark.skipif(
    os.environ.get("MY_AGENT_LIVE_RECORDS_SMOKE") != "1",
    reason="live-model smoke is opt-in; set MY_AGENT_LIVE_RECORDS_SMOKE=1 explicitly",
)
def test_live_llm_summarizes_synthetic_saved_records_without_conflating_items(tmp_path):
    # This calls LLMClient directly from a test; it is not the Agent chat pipeline.
    items = _saved_records(tmp_path)
    messages = _summary_prompt(items)
    sandbox_paths = AgentPaths.discover(start=Path(__file__).resolve())
    response = LLMClient(load_config(), paths=sandbox_paths).chat(
        messages, model="0x567-flash", temperature=0.0
    )
    text = response.content or ""

    # Deliberately loose semantic checks: provider wording is not fixed.
    assert "项目A" in text
    assert "简历" in text
    assert any(marker in text for marker in ("未运行", "未执行", "尚未运行", "尚未执行"))
    assert any(marker in text for marker in ("独立", "不属于项目A", "不要混", "分别"))
    assert _FAKE_PATH not in text
    assert _FAKE_SECRET not in text
