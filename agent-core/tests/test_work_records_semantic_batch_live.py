from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from work_records_semantic import WorkRecordCandidate, WorkRecordSemanticRecognizer

LIVE_OPT_IN = "MY_AGENT_LIVE_SEMANTIC_BATCH"
MODEL_ID = "0x567-flash"
BATCH_SIZE = 20


def _synthetic_candidates(*, ambiguous: bool = False) -> list[WorkRecordCandidate]:
    """At least two batches; all identifiers/content are fictional and path-free."""
    candidates = [
        WorkRecordCandidate(
            f"synthetic-{index:02d}",
            f"虚构事项{index:02d}：{topic}",
            f"合成记录内容：{detail}。仅为定向语义检索实验素材。",
            "synthetic-session-batch-test",
        )
        for index, (topic, detail) in enumerate(
            [
                ("松果表格", "核验 CSV 列名映射"),
                ("蓝桥读书会", "整理虚构文章主题"),
                ("风筝排期", "调整周一例会时间"),
                ("像素图标", "检查图形色彩规范"),
                ("纸船清单", "盘点虚构物资数量"),
                ("云杉演示", "准备幻灯片结构"),
                ("星轨目录", "归类模拟文档"),
                ("铜铃提醒", "设置虚构活动通知"),
                ("白鹭预算", "汇总练习用金额"),
                ("珊瑚配色", "比较界面色板"),
                ("木槿路线", "规划虚构园区步行路线"),
                ("琥珀表单", "检查表单字段名称"),
                ("蒲公英备忘", "记录虚构灵感"),
                ("鲸歌音频", "标注练习音频片段"),
                ("青石标签", "整理模拟标签集合"),
                ("银杏讲座", "汇总虚构讲座提纲"),
                ("玻璃温室", "记录模拟植物观察"),
                ("纸鸢排版", "校对虚构页面边距"),
                ("晨雾日志", "整理练习日志格式"),
                ("橘灯采购", "比较模拟文具清单"),
                # The intended answer is deliberately in the third (tail) batch.
                ("海棠组交流会", "原定周二的讨论挪到周四下午，在虚构图书馆举行"),
                ("松果组活动", "讨论会按原计划在周二举行"),
                ("远山资料", "汇总虚构资料来源"),
                ("微风日程", "记录模拟日程变更"),
                ("琉璃封面", "挑选练习文档封面"),
            ]
        )
    ]
    if ambiguous:
        # A second semantically plausible record is intentionally placed in batch 1,
        # so the final decision must compare proposals across batches.
        candidates[1] = WorkRecordCandidate(
            "synthetic-cross-batch-near-match",
            "蓝桥组交流会调整",
            "原定周二的碰面改到周四下午，地点为虚构社区厅。",
            "synthetic-session-batch-test",
        )
    return candidates


def _tail_batch_match_response(prompt: str) -> str:
    payload = json.loads(prompt.split("\n", 1)[1])
    if "proposals" in payload:
        return json.dumps({
            "status": "match", "candidate_id": "synthetic-20",
            "candidate_ids": ["synthetic-20"],
            "evidence": "周二改到周四下午的交流会时间与问题吻合。",
        }, ensure_ascii=False)
    ids = [row["id"] for row in payload["candidates"]]
    if "synthetic-20" in ids:
        return json.dumps({"status": "candidates", "candidate_ids": ["synthetic-20"],
                           "evidence": {"synthetic-20": "改期内容吻合"}}, ensure_ascii=False)
    return json.dumps({"status": "none", "candidate_ids": [], "evidence": {}}, ensure_ascii=False)

def test_fake_llm_batch_harness_selects_only_tail_batch_id_and_sends_no_private_metadata():
    prompts: list[str] = []

    def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return _tail_batch_match_response(prompt)

    candidates = _synthetic_candidates()
    result = WorkRecordSemanticRecognizer(fake_llm, max_candidates=BATCH_SIZE).match(
        "那场碰面从原来的日子顺延到什么时候了？", candidates
    )

    assert (result.status, result.candidate_id, result.candidate_ids) == (
        "match", "synthetic-20", ("synthetic-20",)
    )
    assert len(candidates) >= 25 and len(prompts) == 3
    assert all("synthetic-session-batch-test" not in prompt for prompt in prompts)
    assert all(str(_AGENT_CORE) not in prompt and "conversation_id" not in prompt for prompt in prompts)
    assert all("tools" not in prompt.lower() for prompt in prompts)


def test_fake_llm_batch_harness_keeps_cross_batch_ambiguity_unresolved():
    prompts: list[str] = []

    def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        payload = json.loads(prompt.split("\n", 1)[1])
        if "proposals" in payload:
            return json.dumps({
                "status": "ambiguous", "candidate_id": None,
                "candidate_ids": ["synthetic-cross-batch-near-match", "synthetic-20"],
                "evidence": "两个跨批候选都符合改期描述，无法唯一确定。",
            }, ensure_ascii=False)
        ids = [row["id"] for row in payload["candidates"]]
        selected = [item for item in ("synthetic-cross-batch-near-match", "synthetic-20") if item in ids]
        return json.dumps(
            {"status": "candidates", "candidate_ids": selected,
             "evidence": {item: "改期语义相似" for item in selected}}
            if selected else {"status": "none", "candidate_ids": [], "evidence": {}},
            ensure_ascii=False,
        )

    result = WorkRecordSemanticRecognizer(fake_llm, max_candidates=BATCH_SIZE).match(
        "周二改到周四的那场交流活动是哪一场？", _synthetic_candidates(ambiguous=True)
    )

    assert (result.status, result.candidate_id) == ("ambiguous", None), _live_call_diagnostics(calls)
    assert set(result.candidate_ids) == {"synthetic-cross-batch-near-match", "synthetic-20"}, _live_call_diagnostics(calls)
    assert len(prompts) == 3
    assert "synthetic-cross-batch-near-match" in prompts[-1] and "synthetic-20" in prompts[-1]


def _live_recognizer():
    from llm_client import LLMClient, load_config
    from llm_models import get_registry
    from llm_secrets import get_llm_secret
    from paths import AgentPaths

    paths = AgentPaths.discover(start=Path(__file__).resolve())
    model = get_registry(paths).get(MODEL_ID)
    if model is None or model.id != MODEL_ID:
        pytest.fail(f"sandbox model registry does not contain {MODEL_ID}")
    if model.api_key_env and not get_llm_secret(model.api_key_env, paths):
        pytest.skip("0x567 credential unavailable; no live request made")

    client = LLMClient(load_config(), paths=paths)
    calls: list[tuple[list[dict[str, Any]], dict[str, Any], str, bool]] = []

    class ObservedClient:
        def chat(self, messages, **kwargs):
            response = client.chat(messages, **kwargs)
            calls.append((messages, kwargs, response.content or "", bool(response.tool_calls)))
            return response

    matcher = WorkRecordSemanticRecognizer.from_sync_llm_client(
        ObservedClient(), model=MODEL_ID, max_candidates=BATCH_SIZE
    )
    return matcher, calls, paths


def _live_call_diagnostics(calls):
    diagnostics = []
    for messages, _kwargs, response_text, tool_calls in calls:
        payload = json.loads(messages[0]["content"].split("\n", 1)[1])
        diagnostics.append({
            "phase": "arbitration" if "proposals" in payload else f"batch-{payload.get('batch')}",
            "input_ids": [row["id"] for row in payload.get("candidates", [])],
            "proposed_ids": [row["id"] for row in payload.get("proposals", [])],
            "raw_model_response": response_text,
            "tool_calls": tool_calls,
        })
    return diagnostics


def _assert_live_calls_are_synthetic_and_tool_free(calls, paths):
    assert len(calls) == 3  # two candidate batches plus final arbitration
    for messages, kwargs, response_text, tool_calls in calls:
        assert kwargs["model"] == MODEL_ID
        assert kwargs["tools"] == []
        assert tool_calls is False
        assert kwargs["response_format"] == {"type": "json_object"}
        assert len(messages) == 1 and messages[0]["role"] == "user"
        prompt = messages[0]["content"]
        assert str(paths.agent_root) not in prompt
        assert "synthetic-session-batch-test" not in prompt
        assert "conversation_id" not in prompt
        assert "real data" not in prompt.lower()
        assert str(paths.agent_root) not in response_text
        assert "synthetic-session-batch-test" not in response_text


@pytest.mark.skipif(os.environ.get(LIVE_OPT_IN) != "1", reason=f"set {LIVE_OPT_IN}=1 to enable live model calls")
def test_live_0x567_flash_finds_synthetic_target_in_tail_batch():
    matcher, calls, paths = _live_recognizer()
    candidates = _synthetic_candidates()
    assert len(candidates) >= 25

    result = matcher.match("那场碰面从原来的日子顺延到什么时候了？", candidates)

    assert (result.status, result.candidate_id, result.candidate_ids) == (
        "match", "synthetic-20", ("synthetic-20",)
    ), _live_call_diagnostics(calls)
    _assert_live_calls_are_synthetic_and_tool_free(calls, paths)


@pytest.mark.skipif(os.environ.get(LIVE_OPT_IN) != "1", reason=f"set {LIVE_OPT_IN}=1 to enable live model calls")
def test_live_0x567_flash_does_not_choose_between_cross_batch_similar_candidates():
    matcher, calls, paths = _live_recognizer()

    result = matcher.match(
        "周二改到周四的那场交流活动是哪一场？", _synthetic_candidates(ambiguous=True)
    )

    assert result.status == "ambiguous"
    assert result.candidate_id is None
    assert set(result.candidate_ids) == {"synthetic-cross-batch-near-match", "synthetic-20"}
    _assert_live_calls_are_synthetic_and_tool_free(calls, paths)



