from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))
import multi_project_luna_intent as intent

spec = importlib.util.spec_from_file_location("synthetic_route_fixtures", CORE / "tests" / "test_multi_project_overview_agent_route.py")
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


def setup(tmp_path, monkeypatch, candidate, *, mapping=None, enabled=True, eligible=True):
    mapping = mapping if mapping is not None else {"alpha": "20261030-00000001", "beta": "20261030-00000002"}
    agent = fixtures.make_agent(tmp_path, mapping, enabled=enabled, confirm=Mock(return_value="y"))
    agent.multi_project_overview_recognizer = Mock(side_effect=candidate) if isinstance(candidate, Exception) else Mock(return_value=candidate)
    monkeypatch.setattr(intent, "is_eligible_overview_query", Mock(return_value=eligible), raising=False)
    single = Mock(return_value="synthetic single")
    multi = Mock(return_value="synthetic multi")
    module = ModuleType("work_records_overview")
    module.render_work_records_overview = single
    monkeypatch.setitem(sys.modules, "work_records_overview", module)
    fixtures.install_renderer(monkeypatch, multi)
    return agent, single, multi


@pytest.mark.parametrize("candidate", [intent.IntentCandidate("none"), intent.IntentCandidate("uncertain"), RuntimeError("private error"), None, {"intent": "all"}, intent.IntentCandidate("selected", ("forged",)), intent.IntentCandidate("all", ("alpha",))])
def test_uncertain_rejected_and_forged_candidate_stops_without_read_or_model(tmp_path, monkeypatch, candidate):
    agent, single, multi = setup(tmp_path, monkeypatch, candidate)
    result = agent.run_turn("请展示 alpha 项目进展概况")
    assert "未能安全确定" in result.assistant_text
    assert "private error" not in result.assistant_text
    assert not single.called and not multi.called and not agent.fallback_calls
    assert result.tool_rounds == 0


def test_semantic_all_uses_mapping_and_real_confirm_callback(tmp_path, monkeypatch):
    agent, single, multi = setup(tmp_path, monkeypatch, intent.IntentCandidate("all"))
    result = agent.run_turn("请概览各个项目的进度")
    assert multi.call_args.args == (agent.session, ["alpha", "beta"], agent.executor.confirm_fn)
    assert "synthetic multi" in result.assistant_text
    assert not single.called and not agent.fallback_calls


def test_semantic_selected_uses_only_exact_names_and_existing_confirmation(tmp_path, monkeypatch):
    agent, single, multi = setup(tmp_path, monkeypatch, intent.IntentCandidate("selected", ("beta", "alpha")))
    result = agent.run_turn("请概览 beta 与 alpha 项目的进度")
    assert multi.call_args.args == (agent.session, ["beta", "alpha"], agent.executor.confirm_fn)
    assert "synthetic multi" in result.assistant_text and not single.called


@pytest.mark.parametrize("names,text", [(('alpha', 'alpha'), "请概览 alpha 项目"),
    (("/alpha",), "请概览 /alpha 项目"), (("ALPHA",), "请概览 ALPHA 项目"),
    (("beta",), "请概览 alpha 项目"), (("unknown",), "请概览 unknown 项目"),
    (("alpha",)*7, "请概览 alpha 项目")])
def test_bad_selected_names_never_read_or_authorize(tmp_path, monkeypatch, names, text):
    agent, single, multi = setup(tmp_path, monkeypatch, intent.IntentCandidate("selected", names))
    result = agent.run_turn(text)
    assert not single.called and not multi.called and not agent.executor.confirm_fn.called
    assert not agent.fallback_calls and result.tool_rounds == 0


@pytest.mark.parametrize("text,mapping,expected", [
    ("看看当前整体工作进展", {"alpha": "20261030-00000001", "beta": "20261030-00000002"}, "multi"),
    ("看看当前整体工作进展", {"alpha": "20261030-00000001"}, "single"),
    ("看看本会话的工作进展", {"alpha": "20261030-00000001", "beta": "20261030-00000002"}, "single"),
])
def test_semantic_current_follows_current_scope_rules(tmp_path, monkeypatch, text, mapping, expected):
    agent, single, multi = setup(tmp_path, monkeypatch, intent.IntentCandidate("current"), mapping=mapping)
    result = agent.run_turn(text)
    assert (single.call_count, multi.call_count) == ((1, 0) if expected == "single" else (0, 1))
    assert not agent.fallback_calls and result.tool_rounds == 0


@pytest.mark.parametrize("enabled,eligible,text", [(False, True, "看看各个项目进度"), (True, False, "请执行代码"), (True, False, "普通聊天"), (True, False, "请查看 /secret 项目进度")])
def test_disabled_or_ineligible_does_not_intercept(tmp_path, monkeypatch, enabled, eligible, text):
    agent, single, multi = setup(tmp_path, monkeypatch, intent.IntentCandidate("all"), enabled=enabled, eligible=eligible)
    assert agent.run_turn(text).assistant_text == "ordinary"
    assert not agent.multi_project_overview_recognizer.called and not single.called and not multi.called


def test_exact_existing_routes_take_precedence(tmp_path, monkeypatch):
    agent, single, multi = setup(tmp_path, monkeypatch, intent.IntentCandidate("uncertain"))
    result = agent.run_turn("所有项目的工作概况")
    assert multi.called and not agent.multi_project_overview_recognizer.called
    assert result.tool_rounds == 0


def test_real_eligibility_gate_rejects_execution_and_sensitive_without_call(tmp_path, monkeypatch):
    agent = fixtures.make_agent(tmp_path, {"alpha": "20261030-00000001"}, confirm=Mock(return_value="y"))
    agent.multi_project_overview_recognizer = Mock(return_value=intent.IntentCandidate("all"))
    multi = Mock(return_value="should not read")
    fixtures.install_renderer(monkeypatch, multi)
    for text in ["请给所有项目概况然后执行代码", "查看 /private/secret 项目概况", "普通聊天"]:
        agent.run_turn(text)
    assert not agent.multi_project_overview_recognizer.called and not multi.called
    assert len(agent.fallback_calls) == 3


def test_exact_unbound_current_does_not_call_classifier(tmp_path, monkeypatch):
    agent, single, multi = setup(tmp_path, monkeypatch, intent.IntentCandidate("all"))
    agent.work_record_recall_controller._store._session = object()
    assert agent.run_turn("当前工作概况").assistant_text == "ordinary"
    assert not agent.multi_project_overview_recognizer.called and not single.called and not multi.called


def test_local_read_confirmation_rejection_after_classification(tmp_path, monkeypatch):
    agent, single, multi = setup(tmp_path, monkeypatch, intent.IntentCandidate("all"))
    agent.executor.confirm_fn.return_value = "n"
    reads = []
    def render(_session, ids, confirm):
        assert ids == ["alpha", "beta"]
        if confirm("scope alpha beta", False) != "y":
            return "已拒绝，未读取其他会话"
        reads.append(True)
        return "read"
    multi.side_effect = render
    result = agent.run_turn("请概览各个项目的进度")
    assert agent.multi_project_overview_recognizer.call_count == 1
    assert agent.executor.confirm_fn.call_count == 1 and not reads and not single.called
    assert "已拒绝" in result.assistant_text and not agent.fallback_calls


def test_changed_session_during_classifier_is_uncertain_without_read(tmp_path, monkeypatch):
    agent, single, multi = setup(tmp_path, monkeypatch, intent.IntentCandidate("all"))
    agent.multi_project_overview_recognizer.side_effect = lambda _text: (
        setattr(agent, "session", object()) or intent.IntentCandidate("all"))
    result = agent.run_turn("请概览各个项目的进度")
    assert "未能安全确定" in result.assistant_text
    assert not single.called and not multi.called and not agent.fallback_calls


@pytest.mark.parametrize("choice", ["y", "n"])
def test_semantic_classifier_routes_into_real_local_confirmation(tmp_path, monkeypatch, choice):
    from work_records import WorkRecordStore
    real_spec = importlib.util.spec_from_file_location("synthetic_luna_real_fixture", CORE / "tests" / "test_multi_project_overview_real_wiring.py")
    real_fixture = importlib.util.module_from_spec(real_spec)
    real_spec.loader.exec_module(real_fixture)
    agent, _session, project_sessions, model = real_fixture._make_fixture(tmp_path, project_count=2)
    agent.multi_project_overview_recognizer = Mock(return_value=intent.IntentCandidate("all"))
    targets = {session.conversation_id for session in project_sessions}
    reads = []
    original_read = WorkRecordStore._read
    def tracked(store):
        if store._conversation_id in targets:
            reads.append(store._conversation_id)
        return original_read(store)
    monkeypatch.setattr(WorkRecordStore, "_read", tracked)
    original_turn = agent.run_turn
    agent.run_turn = lambda _text: original_turn("请概览各个项目的进度")
    result, events = real_fixture._run_with_real_bridge_confirmation(agent, choice=choice)
    assert agent.multi_project_overview_recognizer.call_count == 1
    assert len([event for event in events if event.get("type") == "confirm.request"]) == 1
    assert not model.calls
    if choice == "n":
        assert not reads and "未读取" in result.assistant_text
    else:
        assert set(reads) == targets and "synthetic-project-1 recorded item" in result.assistant_text
    assert result.tool_rounds == 0


def test_my_work_real_classifier_and_real_local_confirmation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    real_spec = importlib.util.spec_from_file_location("synthetic_my_work_fixture", CORE / "tests" / "test_multi_project_overview_real_wiring.py")
    real_fixture = importlib.util.module_from_spec(real_spec)
    real_spec.loader.exec_module(real_fixture)
    agent, _session, _projects, main_model = real_fixture._make_fixture(tmp_path, project_count=2)
    entry = SimpleNamespace(id="0x567-flash", vendor="0x567", provider_model="gpt-6-luna", max_input_tokens=96000,
                            chat_completions_url=lambda: "https://api-cdn.0x567.com/v1/chat/completions")
    monkeypatch.setattr(intent, "get_registry", lambda *_args: SimpleNamespace(get=lambda _id: entry))
    outbound = []
    classifier_client = SimpleNamespace(chat=Mock(return_value=SimpleNamespace(content='{"intent":"current","project_names":[]}', tool_calls=[])))
    agent.multi_project_overview_recognizer = intent.MultiProjectLunaIntent(
        classifier_client, approve_outbound=lambda request: (outbound.append(request) or True))
    original_turn = agent.run_turn
    agent.run_turn = lambda _text: original_turn("看看我的工作整体进展？")
    result, events = real_fixture._run_with_real_bridge_confirmation(agent, choice="y")
    assert len(outbound) == 1 and outbound[0].user_text == "看看我的工作整体进展？"
    assert classifier_client.chat.call_count == 1 and not main_model.calls
    assert len([event for event in events if event.get("type") == "confirm.request"]) == 1
    assert "synthetic-project-1 recorded item" in result.assistant_text
    assert "synthetic-project-2 recorded item" in result.assistant_text
    assert result.tool_rounds == 0


@pytest.mark.parametrize("text", ["这个项目的进展", "当前项目的进度", "本项目的概况"])
def test_semantic_current_explicit_project_does_not_expand_to_all(tmp_path, monkeypatch, text):
    agent, single, multi = setup(tmp_path, monkeypatch, intent.IntentCandidate("current"))
    result = agent.run_turn(text)
    assert single.call_count == 1 and not multi.called
    assert not agent.executor.confirm_fn.called and not agent.fallback_calls
    assert result.tool_rounds == 0


@pytest.mark.parametrize("text", ["这个项目的进展", "当前项目的进度", "本项目的概况"])
def test_real_single_project_reference_does_not_confirm_or_read_other_sessions(tmp_path, monkeypatch, text):
    from work_records import WorkRecordStore
    real_spec = importlib.util.spec_from_file_location("synthetic_single_project_fixture", CORE / "tests" / "test_multi_project_overview_real_wiring.py")
    real_fixture = importlib.util.module_from_spec(real_spec)
    real_spec.loader.exec_module(real_fixture)
    agent, _session, project_sessions, model = real_fixture._make_fixture(tmp_path, project_count=2)
    agent.multi_project_overview_recognizer = Mock(return_value=intent.IntentCandidate("current"))
    agent.executor.confirm_fn = Mock(return_value="y")
    targets = {session.conversation_id for session in project_sessions}
    reads = []
    original_read = WorkRecordStore._read
    def tracked(store):
        if store._conversation_id in targets:
            reads.append(store._conversation_id)
        return original_read(store)
    monkeypatch.setattr(WorkRecordStore, "_read", tracked)
    result = agent.run_turn(text)
    assert agent.multi_project_overview_recognizer.call_count == 1
    assert not agent.executor.confirm_fn.called and not reads and not model.calls
    assert "当前会话" in result.assistant_text and result.tool_rounds == 0



def test_semantic_selected_resolves_exact_local_directory_names(tmp_path, monkeypatch):
    agent, single, multi = setup(
        tmp_path, monkeypatch,
        intent.IntentCandidate("selected", ("预算 项目", "模型实验")),
    )
    roots = {
        "alpha": tmp_path / "local-projects" / "预算 项目",
        "beta": tmp_path / "local-projects" / "模型实验",
    }
    for root in roots.values():
        root.mkdir(parents=True)
    _install_synthetic_external_bindings(agent, roots)

    result = agent.run_turn("请看预算 项目和模型实验的进展概况")

    assert multi.call_args.args == (agent.session, ["alpha", "beta"], agent.executor.confirm_fn)
    assert "synthetic multi" in result.assistant_text
    assert not single.called and not agent.fallback_calls


def test_semantic_selected_ambiguous_local_directory_name_asks_without_read(tmp_path, monkeypatch):
    agent, single, multi = setup(
        tmp_path, monkeypatch, intent.IntentCandidate("selected", ("共享 项目",)),
    )
    roots = {
        "alpha": tmp_path / "local-projects" / "alpha" / "共享 项目",
        "beta": tmp_path / "local-projects" / "beta" / "共享 项目",
    }
    for root in roots.values():
        root.mkdir(parents=True)
    _install_synthetic_external_bindings(agent, roots)

    result = agent.run_turn("请看共享 项目的进展概况")

    assert "请提供" in result.assistant_text and "唯一" in result.assistant_text
    assert not multi.called and not single.called and not agent.executor.confirm_fn.called
    assert not agent.fallback_calls and result.tool_rounds == 0



def _install_synthetic_external_bindings(agent, roots):
    """Use only temporary directories and the actual local external-binding reader."""
    state_path = agent.session.paths.data / "state.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["external_projects"] = {
        project_id: {
            "external_root": str(root),
            "logical_root": f"workspace/{project_id}",
        }
        for project_id, root in roots.items()
    }
    state_path.write_text(json.dumps(payload), encoding="utf-8")


def test_semantic_selected_rejects_ascii_identifier_prefix_from_other_project(tmp_path, monkeypatch):
    agent, _single, multi = setup(
        tmp_path, monkeypatch,
        intent.IntentCandidate("selected", ("alpha",)),
        mapping={"alpha": "20261030-00000001", "alpha2": "20261030-00000002"},
    )
    roots = {
        "alpha": tmp_path / "local-projects" / "alpha",
        "alpha2": tmp_path / "local-projects" / "alpha2",
    }
    for root in roots.values():
        root.mkdir(parents=True)
    _install_synthetic_external_bindings(agent, roots)

    result = agent.run_turn("请看alpha2的进展概况")

    assert "请提供" in result.assistant_text and "唯一" in result.assistant_text
    assert not multi.called and not agent.executor.confirm_fn.called
    assert not agent.fallback_calls and result.tool_rounds == 0


def test_semantic_selected_rejects_substring_of_another_unicode_project_name(tmp_path, monkeypatch):
    agent, _single, multi = setup(
        tmp_path, monkeypatch,
        intent.IntentCandidate("selected", ("预算",)),
        mapping={"budget": "20261030-00000001", "budget-plan": "20261030-00000002"},
    )
    roots = {
        "budget": tmp_path / "local-projects" / "预算",
        "budget-plan": tmp_path / "local-projects" / "预算管理",
    }
    for root in roots.values():
        root.mkdir(parents=True)
    _install_synthetic_external_bindings(agent, roots)

    result = agent.run_turn("请看预算管理的进展概况")

    assert "请提供" in result.assistant_text and "唯一" in result.assistant_text
    assert not multi.called and not agent.executor.confirm_fn.called
    assert not agent.fallback_calls and result.tool_rounds == 0


def test_semantic_selected_keeps_exact_ascii_id_when_prefix_project_also_exists(tmp_path, monkeypatch):
    agent, _single, multi = setup(
        tmp_path, monkeypatch,
        intent.IntentCandidate("selected", ("alpha",)),
        mapping={"alpha": "20261030-00000001", "alpha2": "20261030-00000002"},
    )
    roots = {
        "alpha": tmp_path / "local-projects" / "alpha",
        "alpha2": tmp_path / "local-projects" / "alpha2",
    }
    for root in roots.values():
        root.mkdir(parents=True)
    _install_synthetic_external_bindings(agent, roots)

    result = agent.run_turn("请看alpha的进展概况")

    assert multi.call_args.args == (agent.session, ["alpha"], agent.executor.confirm_fn)
    assert "synthetic multi" in result.assistant_text
    assert not agent.fallback_calls
