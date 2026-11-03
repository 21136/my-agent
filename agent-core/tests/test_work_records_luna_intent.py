from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_TESTS = Path(__file__).resolve().parent
_CORE = _TESTS.parent
for _path in (_TESTS, _CORE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import work_records_luna_intent as luna


def install_registry(monkeypatch, entry=None):
    entry = entry or SimpleNamespace(
        id="0x567-flash", vendor="0x567", provider_model="gpt-6-luna", max_input_tokens=1_000_000
    )

    class Registry:
        def get(self, model_id):
            return entry if model_id == "0x567-flash" else None

    monkeypatch.setattr(luna, "get_registry", lambda *_args: Registry())


_ITEM_ID_UNSET = object()


def result(action, kind, *, title=None, requirement=None, scope=None, item_id=_ITEM_ID_UNSET):
    payload = {
        "action": action,
        "kind": kind,
        "title": title,
        "requirement": requirement,
        "scope": scope,
    }
    if item_id is not _ITEM_ID_UNSET:
        payload["item_id"] = item_id
    return json.dumps(payload, ensure_ascii=False)


def view():
    return [{"item_id": "item-1", "title": "整理测试", "scope": {"kind": "project", "project_id": "project-7"}}]


class FakeClient:
    paths = None

    def __init__(self, content):
        self.content = content
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return SimpleNamespace(content=self.content, tool_calls=[])


def test_requires_explicit_outbound_approval_before_any_external_request(monkeypatch):
    install_registry(monkeypatch)
    client = FakeClient(result("record", "requirement", title="测试", requirement="添加测试", scope="current_project"))
    recognizer = luna.WorkRecordsLunaIntent(client, current_project_id="project-7")

    decision = recognizer("请在项目里添加测试", view())

    assert decision["action"] == "uncertain"
    assert client.calls == []


def test_classifies_qa_recall_ambiguity_and_plain_execution_distinctly(monkeypatch):
    install_registry(monkeypatch)
    cases = (
        (result("none", "qa"), "qa", "none"),
        (result("recall", "recall"), "recall", "recall"),
        (result("uncertain", "ambiguous"), "ambiguous", "uncertain"),
        (result("none", "execution"), "execution", "none"),
    )
    for content, expected_kind, expected_action in cases:
        client = FakeClient(content)
        decision = luna.WorkRecordsLunaIntent(client, outbound_approved=True, current_project_id="project-7")("合成输入", view())
        assert decision["action"] == expected_action
        assert decision["kind"] == expected_kind
        assert len(client.calls) == 1


def test_only_one_clear_requirement_can_record_and_project_is_locally_bound(monkeypatch):
    install_registry(monkeypatch)
    client = FakeClient(result(
        "record", "requirement", title="补充单元测试", requirement="为解析器添加边界测试", scope="current_project"
    ))
    decision = luna.WorkRecordsLunaIntent(client, outbound_approved=True, current_project_id="project-7")(
        "在当前项目补充解析器边界测试", view()
    )

    assert decision == {
        "action": "record",
        "kind": "requirement",
        "title": "补充单元测试",
        "requirement": "为解析器添加边界测试",
        "scope": {"kind": "project", "project_id": "project-7"},
    }



def test_concrete_scoped_deliverable_is_recorded_without_a_record_verb(monkeypatch):
    install_registry(monkeypatch)
    client = FakeClient(result(
        "record", "requirement", title="本项目 CSV 导出功能",
        requirement="新增 CSV 导出按钮并生成文件；验收为字段完整且自动化测试通过",
        scope="current_project",
    ))
    recognizer = luna.WorkRecordsLunaIntent(client, outbound_approved=True, current_project_id="project-7")
    decision = recognizer(
        "在本项目新增 CSV 导出功能，交付导出按钮和 CSV 文件；验收：字段完整且自动化测试通过。",
        view(),
    )

    assert decision == {
        "action": "record",
        "kind": "requirement",
        "title": "本项目 CSV 导出功能",
        "requirement": "新增 CSV 导出按钮并生成文件；验收为字段完整且自动化测试通过",
        "scope": {"kind": "project", "project_id": "project-7"},
    }
    sent = json.loads(client.calls[0][0][1]["content"])
    assert sent["current_project_available"] is True
    system_prompt = client.calls[0][0][0]["content"].lower()
    assert "do not require the user to say" in system_prompt
    assert "deliverable" in system_prompt and "acceptance" in system_prompt
    assert "not a request to capture" not in system_prompt


def test_accepts_store_native_independent_scope_with_null_object_ref(monkeypatch):
    install_registry(monkeypatch)
    client = FakeClient(result("none", "qa"))
    recognizer = luna.WorkRecordsLunaIntent(client, outbound_approved=True)

    decision = recognizer("什么是单元测试？", [{
        "item_id": "item-independent",
        "title": "独立事项",
        "scope": {"kind": "independent", "object_ref": None},
    }])

    assert decision == {"action": "none", "kind": "qa", "title": None, "requirement": None, "scope": None}
    sent = json.loads(client.calls[0][0][1]["content"])
    assert sent["recognition_view"][0]["scope"] == {"kind": "independent"}
    assert "object_ref" not in client.calls[0][0][1]["content"]


def test_independent_requirement_stays_independent_and_project_id_is_not_sent(monkeypatch):
    install_registry(monkeypatch)
    client = FakeClient(result("record", "requirement", title="缴电费", requirement="周五前缴电费", scope="independent"))
    decision = luna.WorkRecordsLunaIntent(client, outbound_approved=True, current_project_id="project-7")(
        "周五前缴电费", view()
    )

    assert decision["scope"] == {"kind": "independent"}
    messages, kwargs = client.calls[0]
    assert "project-7" not in messages[1]["content"]
    assert kwargs == {
        "model": "0x567-flash",
        "tools": [],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }



def test_follow_up_to_existing_project_item_returns_its_exact_id(monkeypatch):
    install_registry(monkeypatch)
    client = FakeClient(result(
        "record", "requirement", title="继续完善解析器测试", requirement="增加错误输入覆盖；验收为新增用例通过",
        scope="current_project", item_id="item-1",
    ))
    decision = luna.WorkRecordsLunaIntent(client, outbound_approved=True, current_project_id="project-7")(
        "续写当前项目里的‘整理测试’事项：补充错误输入覆盖，新增用例全部通过作为验收。", view()
    )

    assert decision == {
        "action": "record", "kind": "requirement", "title": "继续完善解析器测试",
        "requirement": "增加错误输入覆盖；验收为新增用例通过",
        "scope": {"kind": "project", "project_id": "project-7"}, "item_id": "item-1",
    }
    sent = json.loads(client.calls[0][0][1]["content"])
    assert sent["recognition_view"][0]["item_id"] == "item-1"
    prompt = client.calls[0][0][0]["content"].lower()
    assert "item_id" in prompt and "continuation" in prompt


def test_new_independent_item_has_explicit_null_id(monkeypatch):
    install_registry(monkeypatch)
    client = FakeClient(result(
        "record", "requirement", title="独立事项", requirement="周五前提交合成报告；验收为文件齐全",
        scope="independent", item_id=None,
    ))
    decision = luna.WorkRecordsLunaIntent(client, outbound_approved=True)(
        "新增一个独立事项：周五前提交合成报告，验收为文件齐全。", []
    )

    assert decision == {
        "action": "record", "kind": "requirement", "title": "独立事项",
        "requirement": "周五前提交合成报告；验收为文件齐全",
        "scope": {"kind": "independent"}, "item_id": None,
    }


def test_rejects_unknown_or_cross_scope_item_ids(monkeypatch):
    install_registry(monkeypatch)
    invalid_outputs = (
        result("record", "requirement", title="续写", requirement="验收完成", scope="current_project", item_id="invented-id"),
        result("record", "requirement", title="续写", requirement="验收完成", scope="independent", item_id="item-1"),
        result("record", "requirement", title="续写", requirement="验收完成", scope="current_project", item_id=["item-1"]),
    )
    for content in invalid_outputs:
        client = FakeClient(content)
        decision = luna.WorkRecordsLunaIntent(client, outbound_approved=True, current_project_id="project-7")(
            "续写当前项目里的事项", view()
        )
        assert decision["action"] == "uncertain"
        assert decision.get("item_id") is None


def test_ambiguous_continuation_is_uncertain_and_duplicate_view_ids_fail_closed(monkeypatch):
    install_registry(monkeypatch)
    ambiguous = FakeClient(result("uncertain", "ambiguous", item_id=None))
    decision = luna.WorkRecordsLunaIntent(ambiguous, outbound_approved=True, current_project_id="project-7")(
        "续写之前的测试事项", view()
    )
    assert decision["action"] == "uncertain"
    assert decision["kind"] == "ambiguous"

    duplicate = FakeClient(result("none", "qa", item_id=None))
    duplicate_rows = view() + [{**view()[0], "title": "另一个事项"}]
    decision = luna.WorkRecordsLunaIntent(duplicate, outbound_approved=True, current_project_id="project-7")(
        "哪个事项？", duplicate_rows
    )
    assert decision["action"] == "uncertain"
    assert duplicate.calls == []


def test_rejects_malformed_or_cross_project_recognition_view_without_sending(monkeypatch):
    install_registry(monkeypatch)
    client = FakeClient(result("none", "qa"))
    recognizer = luna.WorkRecordsLunaIntent(client, outbound_approved=True, current_project_id="project-7")

    assert recognizer("问题", [{"item_id": "i", "title": "x", "scope": {"kind": "project", "project_id": "other"}}])["action"] == "uncertain"
    assert recognizer("问题", [{"item_id": "i", "title": "x", "scope": {"kind": "independent"}, "private": "secret"}])["action"] == "uncertain"
    assert recognizer("问题", [{"item_id": "i", "title": "x", "scope": {"kind": "independent", "object_ref": None, "private": "secret"}}])["action"] == "uncertain"
    assert client.calls == []


def test_strips_unrecognized_view_fields_and_sends_no_tools(monkeypatch):
    install_registry(monkeypatch)
    client = FakeClient(result("none", "qa"))
    recognizer = luna.WorkRecordsLunaIntent(client, outbound_approved=True)
    recognizer("合成问题", [{"item_id": "item-a", "title": "事项", "scope": {"kind": "independent"}}])

    messages, kwargs = client.calls[0]
    sent = json.loads(messages[1]["content"])
    assert set(sent) == {"text", "recognition_view", "current_project_available"}
    assert sent["current_project_available"] is False
    assert sent["recognition_view"] == [{"item_id": "item-a", "title": "事项", "scope": {"kind": "independent"}}]
    assert kwargs["tools"] == []
    assert "read-only" in messages[0]["content"].lower()


def test_strict_output_validation_and_duplicates_fail_closed(monkeypatch):
    install_registry(monkeypatch)
    invalid = (
        '{"action":"record","kind":"requirement","title":"A","requirement":"B","scope":"current_project","item_id":null,"extra":1}',
        '{"action":"record","action":"none","kind":"requirement","title":"A","requirement":"B","scope":"independent"}',
        result("record", "requirement", title="A", requirement="B", scope="invented-project"),
        result("record", "requirement", title="A", requirement="B", scope="current_project"),
        result("none", "requirement"),
        "```json\n{}\n```",
    )
    for content in invalid:
        client = FakeClient(content)
        decision = luna.WorkRecordsLunaIntent(client, outbound_approved=True)("合成输入", [])
        assert decision["action"] == "uncertain"


def test_registry_mismatch_async_response_and_provider_exception_fail_closed_without_leaks(monkeypatch):
    wrong = SimpleNamespace(id="0x567-flash", vendor="0x567", provider_model="other", max_input_tokens=100_000)
    install_registry(monkeypatch, wrong)
    client = FakeClient(result("none", "qa"))
    assert luna.WorkRecordsLunaIntent(client, outbound_approved=True, current_project_id="project-7")("合成输入", view())["action"] == "uncertain"
    assert client.calls == []

    install_registry(monkeypatch)

    class Broken:
        def chat(self, *_args, **_kwargs):
            raise RuntimeError("secret-token-private-prompt")

    decision = luna.WorkRecordsLunaIntent(Broken(), outbound_approved=True, current_project_id="project-7")("合成输入", view())
    assert decision["action"] == "uncertain"
    assert "secret-token-private-prompt" not in repr(decision)

    async def async_response():
        return "{}"

    async_client = SimpleNamespace(chat=lambda *_args, **_kwargs: async_response())
    decision = luna.WorkRecordsLunaIntent(async_client, outbound_approved=True, current_project_id="project-7")("合成输入", view())
    assert decision["action"] == "uncertain"


def test_no_project_binding_rejects_project_scoped_model_decision_but_allows_independent(monkeypatch):
    install_registry(monkeypatch)
    project = FakeClient(result("record", "requirement", title="事项", requirement="要求", scope="current_project"))
    assert luna.WorkRecordsLunaIntent(project, outbound_approved=True)("当前项目完成事项", [])["action"] == "uncertain"

    independent = FakeClient(result("record", "requirement", title="事项", requirement="要求", scope="independent"))
    decision = luna.WorkRecordsLunaIntent(independent, outbound_approved=True)("独立事项", [])["action"]
    assert decision == "record"



def test_live_summary_assertions_allow_paraphrase_but_require_all_synthetic_elements():
    decision = {
        "action": "record", "kind": "requirement", "scope": {"kind": "independent"}, "item_id": None,
        "title": "合成 S-17 测试文档",
        "requirement": "周五之前交付测试报告，并确保报告包含本次测试的结论和异常记录。",
    }
    _assert_live_summary(
        decision,
        scope={"kind": "independent"},
        item_id=None,
        evidence_groups=(
            ("本周五", "周五之前", "星期五", "friday"),
            ("S-17", "S17"),
            ("测试报告", "报告", "test report"),
            ("测试结论", "测试的结论", "测试结果", "报告结论", "测试发现", "test conclusion", "test results", "test findings"),
        ),
    )


@pytest.mark.parametrize(
    "requirement",
    (
        "交付 S-17 测试报告，并包含测试结论。",
        "周五之前整理测试报告，并包含测试结论。",
        "周五之前完成 S-17 测试内容，并包含测试结论。",
        "周五之前交付 S-17 测试报告。",
    ),
)
def test_live_summary_assertions_reject_missing_deadline_subject_deliverable_or_acceptance(requirement):
    decision = {
        "action": "record", "kind": "requirement", "scope": {"kind": "independent"}, "item_id": None,
        "title": "合成事项",
        "requirement": requirement,
    }
    with pytest.raises(AssertionError):
        _assert_live_summary(
            decision,
            scope={"kind": "independent"},
            item_id=None,
            evidence_groups=(
                ("本周五", "周五之前", "星期五", "friday"),
                ("S-17", "S17"),
                ("测试报告", "报告", "test report"),
                ("测试结论", "测试结果", "报告结论", "测试发现", "test conclusion", "test results", "test findings"),
            ),
        )



def _assert_live_summary(decision, *, scope, item_id, evidence_groups):
    """Require exact control fields plus every fixed synthetic semantic cue."""
    assert decision["action"] == "record"
    assert decision["kind"] == "requirement"
    assert decision["scope"] == scope
    assert decision["item_id"] == item_id
    assert isinstance(decision["title"], str) and decision["title"].strip()
    assert isinstance(decision["requirement"], str) and decision["requirement"].strip()
    summary = f"{decision['title']}\n{decision['requirement']}".casefold()
    for alternatives in evidence_groups:
        assert any(cue.casefold() in summary for cue in alternatives), (
            f"summary omitted required synthetic evidence group: {alternatives!r}"
        )


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    (
        ("action", "none"),
        ("kind", "execution"),
        ("scope", {"kind": "project", "project_id": "wrong-project"}),
        ("item_id", "invented-item-id"),
    ),
)
def test_live_summary_assertions_keep_action_kind_scope_and_id_strict(field, wrong_value):
    decision = {
        "action": "record", "kind": "requirement", "scope": {"kind": "independent"}, "item_id": None,
        "title": "合成 S-17 测试报告",
        "requirement": "周五前交付测试报告，报告包含测试结论。",
    }
    decision[field] = wrong_value
    with pytest.raises(AssertionError):
        _assert_live_summary(
            decision,
            scope={"kind": "independent"},
            item_id=None,
            evidence_groups=(("周五",), ("S-17",), ("报告",), ("测试结论",)),
        )


LIVE_INTENT_OPT_IN = "MY_AGENT_LIVE_WORK_RECORD_LUNA_INTENT"


@pytest.mark.skipif(
    os.environ.get(LIVE_INTENT_OPT_IN) != "1",
    reason=f"set {LIVE_INTENT_OPT_IN}=1 to send one synthetic-only request to 0x567 GPT-6 Luna",
)
def test_opt_in_live_luna_intent_uses_one_synthetic_case():
    """Opt-in only; never run by the default focused test command."""
    from llm_client import LLMClient
    from llm_models import get_registry

    client = LLMClient()
    entry = get_registry(client.paths).get("0x567-flash")
    assert entry is not None
    assert (entry.id, entry.vendor, entry.provider_model) == ("0x567-flash", "0x567", "gpt-6-luna")
    recognizer = luna.WorkRecordsLunaIntent(client, outbound_approved=True)
    decision = recognizer(
        "新增一个独立事项：本周五前整理合成任务 S-17 测试报告，验收为报告含测试结论。",
        [],
    )
    _assert_live_summary(
        decision,
        scope={"kind": "independent"},
        item_id=None,
        evidence_groups=(
            ("本周五", "周五前", "周五之前", "星期五", "friday"),
            ("S-17", "S17"),
            ("测试报告", "报告", "test report"),
            ("测试结论", "测试结果", "报告结论", "测试发现", "test conclusion", "test results", "test findings"),
        ),
    )


@pytest.mark.skipif(
    os.environ.get(LIVE_INTENT_OPT_IN) != "1",
    reason=f"set {LIVE_INTENT_OPT_IN}=1 to send one synthetic continuation request to 0x567 GPT-6 Luna",
)
def test_opt_in_live_luna_intent_links_synthetic_continuation_to_exact_item():
    """One synthetic-only, explicitly opt-in live test; never run by default."""
    from llm_client import LLMClient
    from llm_models import get_registry

    client = LLMClient()
    entry = get_registry(client.paths).get("0x567-flash")
    assert entry is not None
    assert (entry.id, entry.vendor, entry.provider_model) == ("0x567-flash", "0x567", "gpt-6-luna")
    recognizer = luna.WorkRecordsLunaIntent(client, outbound_approved=True, current_project_id="synthetic-project-7")
    decision = recognizer(
        "续写当前项目里的‘合成 CSV 导出’事项：本周五前补充表头校验，验收为合成边界测试全部通过。",
        [{
            "item_id": "synthetic-item-csv-export",
            "title": "合成 CSV 导出",
            "scope": {"kind": "project", "project_id": "synthetic-project-7"},
        }],
    )
    _assert_live_summary(
        decision,
        scope={"kind": "project", "project_id": "synthetic-project-7"},
        item_id="synthetic-item-csv-export",
        evidence_groups=(
            ("本周五", "周五前", "周五之前", "星期五", "friday"),
            ("csv",),
            ("导出", "export"),
            ("表头", "header"),
            ("边界测试", "边界用例", "boundary test", "boundary case"),
            ("通过", "pass"),
        ),
    )



