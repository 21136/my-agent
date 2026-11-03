from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from work_records_semantic import WorkRecordCandidate, WorkRecordSemanticRecognizer

OPT_IN_ENV = "MY_AGENT_LIVE_WORK_RECORD_SEMANTIC_RELIABILITY"
MODE_ENV = "MY_AGENT_LIVE_WORK_RECORD_SEMANTIC_RELIABILITY_MODE"
MODEL_ID = "0x567-flash"


@dataclass(frozen=True)
class EvalCase:
    label: str
    category: str
    question: str
    expected_status: str
    expected_ids: tuple[str, ...]


# Stable, fictional content only. No session files, AgentPaths data, or user history
# are used as candidate input. Semantic neighbors intentionally exercise confusion.
SYNTHETIC_CANDIDATES = (
    WorkRecordCandidate("r01", "海棠组交流会改期", "原定周二的讨论挪到周四下午，在虚构图书馆举行。", "synthetic-eval"),
    WorkRecordCandidate("r02", "松果组讨论改期", "原定周二的小组讨论改为周五上午，在模拟会议室举行。", "synthetic-eval"),
    WorkRecordCandidate("r03", "CSV字段映射校验", "核对虚构导出表的列名与字段映射。", "synthetic-eval"),
    WorkRecordCandidate("r04", "蓝桥读书会材料", "整理三篇虚构文章的主题和讨论问题。", "synthetic-eval"),
    WorkRecordCandidate("r05", "像素图标配色", "检查练习图标是否符合模拟色板规范。", "synthetic-eval"),
    WorkRecordCandidate("r06", "纸船物资盘点", "记录虚构活动物资的数量与缺项。", "synthetic-eval"),
    WorkRecordCandidate("r07", "云杉演示提纲", "准备模拟项目演示的幻灯片结构。", "synthetic-eval"),
    WorkRecordCandidate("r08", "星轨文档归档", "把练习文档按虚构主题分类存放。", "synthetic-eval"),
    WorkRecordCandidate("r09", "铜铃活动提醒", "为模拟读书活动设置提前一天的通知。", "synthetic-eval"),
    WorkRecordCandidate("r10", "白鹭练习预算", "汇总虚构练习项目的预算条目。", "synthetic-eval"),
    WorkRecordCandidate("r11", "珊瑚界面色板", "比较模拟界面的两组颜色组合。", "synthetic-eval"),
    WorkRecordCandidate("r12", "木槿园区路线", "规划虚构园区内几个地点间的步行顺序。", "synthetic-eval"),
    WorkRecordCandidate("r13", "琥珀表单字段", "检查模拟报名表是否遗漏必填项。", "synthetic-eval"),
    WorkRecordCandidate("r14", "蒲公英灵感备忘", "保存虚构创作练习中的几个点子。", "synthetic-eval"),
    WorkRecordCandidate("r15", "鲸歌音频标注", "给练习音频片段添加时间戳标签。", "synthetic-eval"),
    WorkRecordCandidate("r16", "青石标签整理", "合并模拟资料中重复的分类标签。", "synthetic-eval"),
    WorkRecordCandidate("r17", "银杏讲座提要", "提炼虚构讲座材料的章节要点。", "synthetic-eval"),
    WorkRecordCandidate("r18", "玻璃温室观察", "记录模拟植物叶片变化与观察日期。", "synthetic-eval"),
    WorkRecordCandidate("r19", "纸鸢页面排版", "校对练习页面的边距和段落间距。", "synthetic-eval"),
    WorkRecordCandidate("r20", "晨雾日志格式", "统一模拟日志的日期与标题格式。", "synthetic-eval"),
    WorkRecordCandidate("r21", "橘灯文具清单", "比较虚构采购清单中的文具数量。", "synthetic-eval"),
    WorkRecordCandidate("r22", "风筝例会安排", "记录模拟例会的时间和参会角色。", "synthetic-eval"),
    WorkRecordCandidate("r23", "远山资料来源", "整理练习报告引用的虚构资料出处。", "synthetic-eval"),
    WorkRecordCandidate("r24", "微风日程变更", "更新模拟日程中一项活动的开始时刻。", "synthetic-eval"),
    WorkRecordCandidate("r25", "琉璃文档封面", "为练习文档挑选简洁的虚构封面样式。", "synthetic-eval"),
)

# 25 hand-authored cases: paraphrases, anaphora/pronouns, neighboring records,
# ambiguity (must abstain), and unrelated questions (must not retrieve anything).
CASES = (
    EvalCase("paraphrase_meeting", "near_synonym", "那场碰面从原来的日子顺延到什么时候了？我记得是在虚构图书馆。", "match", ("r01",)),
    EvalCase("anaphora_meeting", "pronoun", "它后来改到周几、什么时段？我记得地点是虚构图书馆。", "match", ("r01",)),
    EvalCase("neighboring_meeting", "near_synonym", "周二那次小组讨论改到了哪天？", "match", ("r02",)),
    EvalCase("csv_mapping", "near_synonym", "导出表的标题和字段对应关系检查了吗？", "match", ("r03",)),
    EvalCase("reading_topics", "near_synonym", "读书会要讨论的几篇文章梳理了什么？", "match", ("r04",)),
    EvalCase("icon_colors", "paraphrase", "练习用的小图形颜色是否符合规范？", "match", ("r05",)),
    EvalCase("inventory", "paraphrase", "活动用品还差哪些、各有多少？", "match", ("r06",)),
    EvalCase("slides", "pronoun", "那个演示准备按什么结构来讲？", "match", ("r07",)),
    EvalCase("archive", "near_synonym", "练习文件是按什么方式归类的？", "match", ("r08",)),
    EvalCase("reminder", "anaphora", "这项活动的通知提前多久发？", "match", ("r09",)),
    EvalCase("budget", "near_synonym", "模拟项目的费用汇总在哪里？", "match", ("r10",)),
    EvalCase("palette", "near_synonym", "界面那两套颜色方案比较得怎样？", "match", ("r11",)),
    EvalCase("route", "paraphrase", "园区里几个点之间怎么走比较合适？", "match", ("r12",)),
    EvalCase("form", "anaphora", "报名那张表有没有漏掉必须填写的内容？", "match", ("r13",)),
    EvalCase("ideas", "pronoun", "之前记下的创作点子有哪些？", "match", ("r14",)),
    EvalCase("audio", "near_synonym", "练习录音的片段标记和时间点整理了吗？", "match", ("r15",)),
    EvalCase("tags", "paraphrase", "资料分类里重复的标签处理了吗？", "match", ("r16",)),
    EvalCase("lecture", "near_synonym", "讲座内容的章节重点是什么？", "match", ("r17",)),
    EvalCase("plant", "pronoun", "那些叶子的变化按日期记录了吗？", "match", ("r18",)),
    EvalCase("layout", "paraphrase", "练习页的留白和段落距离校对了吗？", "match", ("r19",)),
    EvalCase("schedule_ambiguity", "ambiguous", "那场碰面从原来的日子顺延到什么时候了？", "ambiguous", ("r01", "r02")),
    EvalCase("colors_ambiguity", "ambiguous", "之前比较的两套颜色是哪两项工作？", "ambiguous", ("r05", "r11")),
    EvalCase("format_ambiguity", "ambiguous", "那个练习材料的格式整理指的是哪份？", "ambiguous", ("r08", "r20")),
    EvalCase("unrelated_astronomy", "unrelated", "火星探测器的太阳能板有多大？", "incomplete", ()),
    EvalCase("unrelated_cooking", "unrelated", "怎样烤制酸面包才能获得酥脆外壳？", "incomplete", ()),
)


SMALL_CASE_LABELS = frozenset({
    "paraphrase_meeting", "anaphora_meeting", "schedule_ambiguity", "unrelated_astronomy"
})


def _select_cases(mode: str) -> tuple[EvalCase, ...]:
    if mode == "full":
        return CASES
    if mode == "small":
        selected = tuple(case for case in CASES if case.label in SMALL_CASE_LABELS)
        if len(selected) != 4:
            raise AssertionError("small semantic evaluation must cover exactly four representative cases")
        return selected
    raise ValueError(f"evaluation mode must be small or full, got {mode!r}")


def test_small_mode_covers_four_representative_synthetic_cases():
    selected = _select_cases("small")
    assert len(selected) == 4
    assert {case.category for case in selected} == {"near_synonym", "pronoun", "ambiguous", "unrelated"}
    assert len(selected) * 2 * 2 == 16  # two orders, at most two calls per case


def test_meeting_cases_have_context_only_where_unique_match_is_expected():
    by_label = {case.label: case for case in CASES}
    r01, r02 = SYNTHETIC_CANDIDATES[:2]

    for label, cue in (("paraphrase_meeting", "碰面"), ("anaphora_meeting", "它")):
        case = by_label[label]
        assert case.expected_status == "match"
        assert case.expected_ids == ("r01",)
        assert cue in case.question
        assert "虚构图书馆" in case.question
    assert "虚构图书馆" in r01.excerpt
    assert "虚构图书馆" not in r02.excerpt
    assert "虚构图书馆" not in r01.title + r02.title

    no_context = by_label["schedule_ambiguity"]
    assert "虚构图书馆" not in no_context.question
    assert (no_context.expected_status, no_context.expected_ids) == ("ambiguous", ("r01", "r02"))


def test_full_mode_keeps_all_25_synthetic_cases():
    assert _select_cases("full") == CASES
    assert len(_select_cases("full")) == 25


def test_unknown_evaluation_mode_is_rejected_offline():
    with pytest.raises(ValueError, match="small or full"):
        _select_cases("unbounded")

def _live_matcher() -> tuple[WorkRecordSemanticRecognizer, list[dict[str, Any]]]:
    from llm_client import LLMClient, load_config
    from llm_models import get_registry
    from llm_secrets import get_llm_secret
    from paths import AgentPaths

    paths = AgentPaths.discover(start=Path(__file__).resolve())
    model = get_registry(paths).get(MODEL_ID)
    if model is None or model.id != MODEL_ID:
        pytest.fail(f"configured model registry does not contain {MODEL_ID}")
    if model.api_key_env and not get_llm_secret(model.api_key_env, paths):
        pytest.skip("0x567 credential unavailable; no live request made")

    client = LLMClient(load_config(), paths=paths)
    observations: list[dict[str, Any]] = []

    class ObservedClient:
        def chat(self, messages, **kwargs):
            started = time.perf_counter()
            response = client.chat(messages, **kwargs)
            observations.append({
                "elapsed": time.perf_counter() - started,
                "tools_disabled": kwargs.get("tools") == [],
                "tool_calls": bool(getattr(response, "tool_calls", None)),
                "model": kwargs.get("model"),
                "message_count": len(messages),
                "role": messages[0].get("role") if messages else None,
            })
            return response

    matcher = WorkRecordSemanticRecognizer.from_sync_llm_client(
        ObservedClient(), model=MODEL_ID, max_candidates=len(SYNTHETIC_CANDIDATES), max_calls=2
    )
    return matcher, observations


@pytest.mark.skipif(os.environ.get(OPT_IN_ENV) != "1", reason=f"set {OPT_IN_ENV}=1 to make paid live calls")
def test_0x567_flash_semantic_recall_reliability_on_synthetic_cases_only():
    assert len(SYNTHETIC_CANDIDATES) == 25
    assert len(CASES) == 25
    mode = os.environ.get(MODE_ENV, "full").strip().lower()
    assert mode in {"small", "full"}, f"{MODE_ENV} must be small or full"
    selected_cases = _select_cases(mode)

    matcher, observations = _live_matcher()
    successes = 0
    eval_count = 0
    failures: list[str] = []
    started = time.perf_counter()

    for order_name, candidates in (
        ("forward", SYNTHETIC_CANDIDATES),
        ("reverse", tuple(reversed(SYNTHETIC_CANDIDATES))),
    ):
        for case in selected_cases:
            before = len(observations)
            result = matcher.match(case.question, candidates)
            per_case_requests = len(observations) - before
            per_case_elapsed = sum(row["elapsed"] for row in observations[before:])
            eval_count += 1
            expected_ids = case.expected_ids
            actual_ids = result.candidate_ids
            expected_result = (
                case.expected_status,
                expected_ids[0] if case.expected_status == "match" else None,
                set(expected_ids),
            )
            actual_result = (result.status, result.candidate_id, set(actual_ids))
            passed = actual_result == expected_result
            if passed:
                successes += 1
            else:
                failures.append(
                    f"{order_name}/{case.label} ({case.category}) expected "
                    f"{case.expected_status}:{expected_ids}, got {result.status}:{actual_ids}"
                )
            assert all(row["tools_disabled"] and not row["tool_calls"] for row in observations[before:]), (
                f"{order_name}/{case.label}: tool use must remain disabled"
            )
            assert all(
                row["model"] == MODEL_ID and row["message_count"] == 1 and row["role"] == "user"
                for row in observations[before:]
            ), f"{order_name}/{case.label}: unexpected request envelope"
            assert per_case_requests in (1, 2), (
                f"{order_name}/{case.label}: unexpected request count {per_case_requests}"
            )
            print(
                f"case={case.label} order={order_name} result={result.status} "
                f"expected={case.expected_status} passed={passed} requests={per_case_requests} "
                f"request_time={per_case_elapsed:.2f}s"
            )

    total_elapsed = time.perf_counter() - started
    request_count = len(observations)
    request_elapsed = sum(row["elapsed"] for row in observations)
    print(
        "semantic reliability: "
        f"mode={mode}, covered_cases={len(selected_cases)}/25, "
        f"evaluations={eval_count}/{2 * len(selected_cases)}, "
        f"success={successes}/{eval_count} ({successes / eval_count:.1%}), "
        f"requests={request_count}, request_time={request_elapsed:.2f}s, wall_time={total_elapsed:.2f}s"
    )
    expected_evaluations = 2 * len(selected_cases)
    assert eval_count == expected_evaluations
    if mode == "small":
        assert len(selected_cases) == 4 and eval_count == 8
        assert request_count <= 16, f"small mode exceeded 16 requests: {request_count}"
    else:
        assert len(selected_cases) == 25 and eval_count == 50
        assert request_count <= 100, f"full mode exceeded 100 requests: {request_count}"
    assert not failures, "strict semantic expectation failures: " + "; ".join(failures)
    assert successes == eval_count, f"strict semantic reliability expectation failed: {successes}/{eval_count}"


