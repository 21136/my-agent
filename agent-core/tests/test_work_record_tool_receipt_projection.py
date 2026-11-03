from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from work_records_experiment import WorkRecordExperimentController

RUN = uuid.uuid4().hex
CALL = str(uuid.uuid4())


def render(receipts_marker=None, *, run_id=RUN, omitted=0, host=None):
    if host is None:
        host = {"sequence": 1, "finish_reason": "completed"}
        if receipts_marker is not None:
            host.update(host_tool_receipts=receipts_marker, omitted_count=omitted)
    return WorkRecordExperimentController._format_recall([{
        "title": "合成事项", "scope": {"kind": "independent"}, "status": "unknown",
        "requirements": [{"requirement_id": "req", "text": "合成要求", "status": "unknown"}],
        "runs": [{"run_id": run_id, "requirement_id": "req", "status": "unknown",
                  "verification": {"status": "not_verified", "host_execution": host}}],
    }])


def fact(**overrides):
    return {**{"host_run_id": RUN, "call_id": CALL, "tool": "read_file", "ok": True}, **overrides}


def test_valid_receipts_are_tool_results_not_verification_and_report_omissions():
    text = render([fact(), fact(call_id=str(uuid.uuid4()), tool="list_dir", ok=False)], omitted=3)
    assert "关联要求：合成要求" in text and "未核验" in text
    assert "宿主记录的工具返回" in text and "read_file" in text and "list_dir" in text
    assert "工具返回 ok" in text and "工具返回非 ok" in text
    assert "另有 3 条" in text and "不代表要求完成或验证通过" in text
    assert CALL in text


def test_unknown_tool_name_is_not_echoed():
    text = render([fact(tool="secret_injected_string")])
    assert "secret_injected_string" not in text
    assert "工具类型待核对" in text


@pytest.mark.parametrize("receipt,run_id,omitted", [
    (fact(host_run_id="SECRET"), RUN, 0),
    (fact(), "SECRET", 0),
    (fact(call_id="SECRET"), RUN, 0),
    (fact(ok=1), RUN, 0),
    (fact(tool="SECRET\nTEXT"), RUN, 0),
    (fact(output="SECRET"), RUN, 0),
    (fact(), RUN, -1),
    (fact(), RUN, True),
])
def test_damaged_receipts_fail_closed(receipt, run_id, omitted):
    text = render([receipt], run_id=run_id, omitted=omitted)
    assert "工具返回未能核对" in text
    assert "SECRET" not in text and "另有" not in text and "工具返回 ok" not in text


def test_oversized_and_duplicate_receipts_fail_closed_not_counted_as_omitted():
    for receipts in ([fact()] * 21, [fact(), fact()]):
        text = render(receipts, omitted=2)
        assert "工具返回未能核对" in text and "另有" not in text


def test_absent_legacy_field_says_nothing_about_tool_calls():
    text = render()
    assert "宿主回合事实" in text
    assert "工具返回" not in text and "没有调用" not in text


def test_partial_or_nonlist_receipts_fail_closed():
    for host in ({"host_tool_receipts": "SECRET", "omitted_count": 0},
                 {"host_tool_receipts": []}, {"omitted_count": 1}):
        text = render(host=host)
        assert "工具返回未能核对" in text and "SECRET" not in text


def test_two_requirements_and_eleven_runs_keep_association_and_history_omission():
    reqs = [{"requirement_id": "a", "text": "合成要求甲", "status": "unknown", "source_ref": None},
            {"requirement_id": "b", "text": "合成要求乙", "status": "unknown", "source_ref": {"kind": "synthetic"}}]
    runs = []
    for n in range(11):
        run_id = uuid.uuid4().hex
        runs.append({"run_id": run_id, "requirement_id": "a" if n % 2 == 0 else "b", "status": "unknown",
                     "result_summary": "PRIVATE_SUMMARY", "verification": {"status": "not_verified",
                     "host_execution": {"sequence": n + 1, "finish_reason": "completed",
                     "host_tool_receipts": [fact(host_run_id=run_id, call_id=str(uuid.uuid4()))], "omitted_count": 0}}})
    text = WorkRecordExperimentController._format_recall([{"title": "合成", "scope": {"kind": "independent"},
        "status": "unknown", "requirements": reqs, "runs": runs}])
    assert "还有 1 条更早回合记录未展开" in text
    assert text.count("宿主记录的工具返回（") == 10
    assert text.count("关联要求：合成要求甲") == 5 and text.count("关联要求：合成要求乙") == 5
    assert "无稳定来源关联" in text and "引用尚未核验" in text
    assert "PRIVATE_SUMMARY" not in text


def test_valid_twenty_records_and_empty_collection():
    entries = [fact(call_id=str(uuid.uuid4())) for _ in range(20)]
    assert render(entries, omitted=1).count("工具返回 ok") == 20
    assert "另有 1 条" in render(entries, omitted=1)
    assert "没有调用" not in render([])


@pytest.mark.parametrize("stored,receipt", [
    (RUN, str(uuid.UUID(hex=RUN))),
    (str(uuid.UUID(hex=RUN)), RUN),
    (RUN, RUN),
    (str(uuid.UUID(hex=RUN)), str(uuid.UUID(hex=RUN))),
])
def test_equivalent_lifecycle_and_host_run_id_forms_match(stored, receipt):
    text = render([fact(host_run_id=receipt)], run_id=stored)
    assert "宿主记录的工具返回（" in text
    assert "工具返回 ok" in text and CALL in text
    assert "工具返回未能核对" not in text


@pytest.mark.parametrize("stored,receipt", [
    (RUN, str(uuid.uuid4())),
    ("abcdefabcdef4abc8abcdefabcdefabc", "ABCDEFABCDEF4ABC8ABCDEFABCDEFABC"),
    ("ABCDEFABCDEF4ABC8ABCDEFABCDEFABC", "abcdefabcdef4abc8abcdefabcdefabc"),
    ("00000000000000000000000000000000", "00000000-0000-0000-0000-000000000000"),
    ("", ""),
])
def test_different_or_noncanonical_run_id_never_projects_receipts(stored, receipt):
    text = render([fact(host_run_id=receipt)], run_id=stored)
    assert "工具返回未能核对" in text
    assert "工具返回 ok" not in text and CALL not in text
