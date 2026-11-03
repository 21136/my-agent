from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from agent import Agent
from paths import AgentPaths
from multi_project_work_records import _read_project_sessions as strict_project_sessions


class FakeSession:
    def __init__(self, paths):
        self.paths = paths
        self.messages = []
        self.turn_intent = None

    def ensure_messages_loaded(self):
        pass

    def append_message(self, message):
        self.messages.append(dict(message))


def make_agent(tmp_path, mapping=None, *, enabled=True, bound=True, confirm=None):
    root = tmp_path / "synthetic-agent"
    paths = AgentPaths(root, root / "workspace", root / "data", root / "evolve")
    paths.data.mkdir(parents=True)
    if mapping is not None:
        (paths.data / "state.json").write_text(json.dumps({"project_sessions": mapping}), encoding="utf-8")
    agent = object.__new__(Agent)
    agent.session = FakeSession(paths)
    agent.executor = SimpleNamespace(confirm_fn=confirm)
    agent.enable_work_record_recall = enabled
    agent.work_record_recall_controller = SimpleNamespace(_store=SimpleNamespace(
        _session=agent.session if bound else FakeSession(paths)))
    agent.enable_work_record_recording = False
    agent.work_record_recording_controller = None
    agent.fallback_calls = []
    agent._run_turn_without_work_record_recording = lambda text, **kwargs: (
        agent.fallback_calls.append(text) or SimpleNamespace(assistant_text="ordinary", tool_rounds=1))
    return agent


def install_renderer(monkeypatch, render):
    module = ModuleType("multi_project_work_records")
    module._read_project_sessions = strict_project_sessions
    if render is not None:
        module.render_selected_projects_overview = render
    monkeypatch.setitem(sys.modules, "multi_project_work_records", module)


def test_routes_trusted_ids_and_real_confirmation_to_local_renderer(tmp_path, monkeypatch):
    confirm = Mock(return_value="y")
    agent = make_agent(tmp_path, {"project-b": "session-b", "project-a": "session-a"}, confirm=confirm)
    def renderer(session, project_ids, confirm_fn):
        assert session is agent.session and project_ids == ["project-a", "project-b"]
        assert confirm_fn is confirm
        assert confirm_fn("SYNTHETIC exact scope project-a project-b", False) == "y"
        return "synthetic overview"
    render = Mock(side_effect=renderer)
    install_renderer(monkeypatch, render)
    result = agent.run_turn("请给我所有项目的工作概况")
    assert render.call_count == confirm.call_count == 1
    assert result.assistant_text.startswith("synthetic overview")
    assert "后续对话模型可能读取" in result.assistant_text
    assert result.tool_rounds == result.total_tool_rounds == 0
    assert not agent.fallback_calls and len(agent.session.messages) == 2


@pytest.mark.parametrize("choice", ["n", "cancel", "timeout"])
def test_rejection_passed_through_does_not_read_or_fallback(tmp_path, monkeypatch, choice):
    confirm = Mock(return_value=choice)
    agent = make_agent(tmp_path, {"project-a": "session-a"}, confirm=confirm)
    reads = []
    def renderer(_session, project_ids, confirm_fn):
        assert project_ids == ["project-a"]
        if confirm_fn("scope project-a", False) != "y":
            return "未获授权，未读取其他会话"
        reads.append("read")
        return "read"
    install_renderer(monkeypatch, renderer)
    result = agent.run_turn("全部项目的工作概况")
    assert confirm.call_count == 1 and not reads and not agent.fallback_calls
    assert "未获授权" in result.assistant_text


@pytest.mark.parametrize("text", [
    "不要查看所有项目的工作概况", "不是所有项目的工作概况",
    "请修改所有项目的工作概况", "请给我所有项目的工作概况然后修复代码",
    '他说“所有项目的工作概况”', "所有项目的工作概况 /tmp/secret", "列出项目",
])
def test_other_text_never_triggers_multi_project_route(tmp_path, monkeypatch, text):
    agent = make_agent(tmp_path, {"project-a": "session-a"}, confirm=Mock(return_value="y"))
    renderer = Mock(return_value="should not render")
    install_renderer(monkeypatch, renderer)
    agent.run_turn(text)
    assert renderer.call_count == 0


def test_disabled_does_not_intercept(tmp_path, monkeypatch):
    agent = make_agent(tmp_path, {"project-a": "session-a"}, enabled=False)
    render = Mock(return_value="not used")
    install_renderer(monkeypatch, render)
    assert agent.run_turn("所有项目的工作概况").assistant_text == "ordinary"
    assert render.call_count == 0


@pytest.mark.parametrize("mapping", [None, {}, {"project-a": "session-a", "bad": None}])
def test_missing_or_invalid_index_fails_without_guessing(tmp_path, monkeypatch, mapping):
    agent = make_agent(tmp_path, mapping, confirm=Mock(return_value="y"))
    render = Mock(return_value="not used")
    install_renderer(monkeypatch, render)
    result = agent.run_turn("所有项目的工作概况")
    assert "未找到或无法核对项目会话索引" in result.assistant_text
    assert not agent.fallback_calls and not render.called


def test_over_six_never_truncates_and_offers_subset_example(tmp_path, monkeypatch):
    agent = make_agent(tmp_path, {f"project-{i}": f"session-{i}" for i in range(7)}, confirm=Mock(return_value="y"))
    render = Mock(return_value="not used")
    install_renderer(monkeypatch, render)
    result = agent.run_turn("所有项目的工作概况")
    assert "超过 6" in result.assistant_text and "查看项目 alpha、beta 的工作概况" in result.assistant_text
    assert not render.called and not agent.executor.confirm_fn.called and not agent.fallback_calls


@pytest.mark.parametrize("mode", ["unbound", "no_confirm", "missing_renderer", "exception"])
def test_fail_closed_no_llm_or_sensitive_error(tmp_path, monkeypatch, mode):
    agent = make_agent(tmp_path, {"project-a": "session-a"}, bound=mode != "unbound", confirm=None if mode == "no_confirm" else Mock(return_value="y"))
    render = Mock(side_effect=RuntimeError("SENSITIVE_ERROR")) if mode == "exception" else Mock(return_value="not used")
    install_renderer(monkeypatch, None if mode == "missing_renderer" else render)
    result = agent.run_turn("所有项目的工作概况")
    assert not agent.fallback_calls and "SENSITIVE_ERROR" not in result.assistant_text
    assert result.tool_rounds == 0 and len(agent.session.messages) == 2
    assert mode == "exception" or not render.called


def test_enumeration_exception_asks_to_verify_scope_without_model(tmp_path, monkeypatch):
    import project_switch
    agent = make_agent(tmp_path, {"project-a": "session-a"}, confirm=Mock(return_value="y"))
    render = Mock(return_value="unused")
    install_renderer(monkeypatch, render)
    monkeypatch.setattr(project_switch, "read_project_sessions", Mock(side_effect=OSError("PRIVATE_INDEX_ERROR")))
    result = agent.run_turn("所有项目的工作概况")
    assert "未找到或无法核对项目会话索引" in result.assistant_text
    assert "PRIVATE_INDEX_ERROR" not in result.assistant_text
    assert not render.called and not agent.fallback_calls


def test_exactly_five_projects_are_not_truncated(tmp_path, monkeypatch):
    mapping = {f"project-{i}": f"session-{i}" for i in range(5)}
    agent = make_agent(tmp_path, mapping, confirm=Mock(return_value="y"))
    render = Mock(return_value="synthetic overview")
    install_renderer(monkeypatch, render)
    agent.run_turn("所有项目的工作概况")
    assert render.call_args.args[1] == sorted(mapping)


def test_confirmation_exception_fails_closed(tmp_path, monkeypatch):
    confirm = Mock(side_effect=RuntimeError("PRIVATE_CONFIRM_ERROR"))
    agent = make_agent(tmp_path, {"project-a": "session-a"}, confirm=confirm)
    def renderer(_session, _ids, confirm_fn):
        confirm_fn("project-a", False)
        pytest.fail("read must not follow failed confirmation")
    install_renderer(monkeypatch, renderer)
    result = agent.run_turn("所有项目的工作概况")
    assert "PRIVATE_CONFIRM_ERROR" not in result.assistant_text
    assert not agent.fallback_calls and confirm.call_count == 1


def test_six_projects_are_accepted(tmp_path, monkeypatch):
    mapping = {f"project-{i}": f"session-{i}" for i in range(6)}
    confirm = Mock(return_value="y")
    agent = make_agent(tmp_path, mapping, confirm=confirm)
    render = Mock(return_value="synthetic overview")
    install_renderer(monkeypatch, render)
    result = agent.run_turn("全部项目的工作概况")
    assert render.call_args.args == (agent.session, sorted(mapping), confirm)
    assert not agent.fallback_calls and result.tool_rounds == 0


def test_explicit_subset_with_more_than_six_mapped_projects(tmp_path, monkeypatch):
    mapping = {f"project-{i}": f"session-{i}" for i in range(7)}
    mapping.update({"alpha": "synthetic-a", "beta": "synthetic-b"})
    confirm = Mock(return_value="n")
    agent = make_agent(tmp_path, mapping, confirm=confirm)
    def render(session, ids, confirm_fn):
        assert session is agent.session and ids == ["alpha", "beta"] and confirm_fn is confirm
        assert confirm_fn("synthetic alpha beta scope", False) == "n"
        return "未获授权，未读取其他会话"
    renderer = Mock(side_effect=render)
    install_renderer(monkeypatch, renderer)
    result = agent.run_turn("查看项目 alpha、beta 的工作概况")
    assert renderer.call_count == 1 and confirm.call_count == 1
    assert "未获授权" in result.assistant_text and not agent.fallback_calls


@pytest.mark.parametrize("text", [
    "查看项目 alpha、alpha 的工作概况", "查看项目 alpha、unknown 的工作概况",
    "查看项目 Alpha 的工作概况", "查看项目 alpha_beta 的工作概况",
    "查看项目 ../alpha 的工作概况", "查看项目 C:/alpha 的工作概况",
    "查看项目 alpha beta 的工作概况", "查看项目 的工作概况",
    "查看项目 alpha、beta、gamma、delta、epsilon、zeta、eta 的工作概况",
])
def test_invalid_subset_locally_requests_exact_names_without_reading(tmp_path, monkeypatch, text):
    mapping = {p: f"session-{p}" for p in ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta"]}
    confirm = Mock(return_value="y")
    agent = make_agent(tmp_path, mapping, confirm=confirm)
    renderer = Mock(return_value="must not read")
    install_renderer(monkeypatch, renderer)
    result = agent.run_turn(text)
    assert "准确" in result.assistant_text and "项目" in result.assistant_text
    assert not renderer.called and not confirm.called and not agent.fallback_calls


@pytest.mark.parametrize("text", [
    "不要查看项目 alpha、beta 的工作概况",
    "查看项目 alpha、beta 的工作概况然后执行代码",
    '他说“查看项目 alpha、beta 的工作概况”',
    "> 查看项目 alpha、beta 的工作概况",
])
def test_subset_never_intercepts_negation_quotes_or_mixed_execution(tmp_path, monkeypatch, text):
    agent = make_agent(tmp_path, {"alpha": "session-a", "beta": "session-b"}, confirm=Mock(return_value="y"))
    renderer = Mock(return_value="must not read")
    install_renderer(monkeypatch, renderer)
    agent.run_turn(text)
    assert not renderer.called and agent.fallback_calls == [text]


def test_real_backend_six_projects_and_current_independent(tmp_path):
    import importlib.util
    from work_records import WorkRecordStore

    spec = importlib.util.spec_from_file_location("synthetic_multi_project_fixture", _CORE / "tests" / "test_multi_project_overview_real_wiring.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    agent, current, _project_sessions, _model = fixture._make_fixture(tmp_path, project_count=6)
    store = WorkRecordStore(current)
    item = store.create_item(title="synthetic independent six", scope={"kind": "independent"})
    store.add_requirement(item["item_id"], "synthetic independent requirement", event_id="independent-event")
    result, events = fixture._run_with_real_bridge_confirmation(agent, choice="y")
    prompts = [event for event in events if event.get("type") == "confirm.request"]
    assert len(prompts) == 1
    assert "synthetic independent six" in result.assistant_text
    assert all(f"synthetic-project-{i}" in result.assistant_text for i in range(1, 7))
    assert result.tool_rounds == 0


def test_real_backend_explicit_subset_from_seven_projects(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("synthetic_subset_fixture", _CORE / "tests" / "test_multi_project_overview_real_wiring.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    agent, _current, _project_sessions, _model = fixture._make_fixture(tmp_path, project_count=7)
    original_run = agent.run_turn
    agent.run_turn = lambda _text: original_run("查看项目 synthetic-project-1、synthetic-project-3 的工作概况")
    result, events = fixture._run_with_real_bridge_confirmation(agent, choice="y")
    assert len([event for event in events if event.get("type") == "confirm.request"]) == 1
    assert "synthetic-project-1 recorded item" in result.assistant_text
    assert "synthetic-project-3 recorded item" in result.assistant_text
    assert "synthetic-project-2 recorded item" not in result.assistant_text
    assert result.tool_rounds == 0


@pytest.mark.parametrize("text", ["当前工作整体概况", "当前工作概况", "当前工作总览"])
def test_unqualified_current_overview_uses_multi_project_confirmation(tmp_path, monkeypatch, text):
    agent = make_agent(tmp_path, {"alpha": "20261030-00000001", "beta": "20261030-00000002"}, confirm=Mock(return_value="n"))
    multi = Mock(side_effect=lambda session, ids, confirm: (
        "已拒绝，未读取其他会话" if confirm("scope alpha beta", False) == "n" else "unexpected read"))
    single = Mock(return_value="single should not run")
    install_renderer(monkeypatch, multi)
    module = ModuleType("work_records_overview")
    module.render_work_records_overview = single
    monkeypatch.setitem(sys.modules, "work_records_overview", module)
    result = agent.run_turn(text)
    assert multi.call_count == 1 and multi.call_args.args[1] == ["alpha", "beta"]
    assert agent.executor.confirm_fn.call_count == 1
    assert "已拒绝" in result.assistant_text and not single.called and not agent.fallback_calls


@pytest.mark.parametrize("mapping", [None, {}, {"alpha": "20261030-00000001"}, {"alpha": "20261030-00000001", "broken": None}])
def test_unqualified_overview_with_fewer_or_unverified_projects_stays_explicitly_single_session(tmp_path, monkeypatch, mapping):
    agent = make_agent(tmp_path, mapping, confirm=Mock(return_value="y"))
    multi = Mock(return_value="should not run")
    install_renderer(monkeypatch, multi)
    module = ModuleType("work_records_overview")
    module.render_work_records_overview = Mock(return_value="synthetic local overview")
    monkeypatch.setitem(sys.modules, "work_records_overview", module)
    result = agent.run_turn("当前工作整体概况")
    assert "synthetic local overview" in result.assistant_text
    assert "仅覆盖当前会话" in result.assistant_text and "不代表所有项目" in result.assistant_text
    assert not multi.called and not agent.executor.confirm_fn.called and not agent.fallback_calls


@pytest.mark.parametrize("text", ["当前会话工作概况", "本会话的工作整体概况"])
def test_explicit_session_overview_never_switches_to_multi(tmp_path, monkeypatch, text):
    agent = make_agent(tmp_path, {"alpha": "20261030-00000001", "beta": "20261030-00000002"}, confirm=Mock(return_value="y"))
    multi = Mock(return_value="should not run")
    install_renderer(monkeypatch, multi)
    module = ModuleType("work_records_overview")
    module.render_work_records_overview = Mock(return_value="synthetic local overview")
    monkeypatch.setitem(sys.modules, "work_records_overview", module)
    result = agent.run_turn(text)
    assert "synthetic local overview" in result.assistant_text
    assert not multi.called and not agent.executor.confirm_fn.called and not agent.fallback_calls


def test_unqualified_overview_more_than_six_does_not_truncate(tmp_path, monkeypatch):
    agent = make_agent(tmp_path, {f"project-{i}": f"20261030-{i + 1:08x}" for i in range(7)}, confirm=Mock(return_value="y"))
    multi = Mock(return_value="should not run")
    install_renderer(monkeypatch, multi)
    result = agent.run_turn("当前工作概况")
    assert "超过 6" in result.assistant_text
    assert not multi.called and not agent.executor.confirm_fn.called and not agent.fallback_calls


@pytest.mark.parametrize("choice", ["y", "n"])
def test_real_unqualified_overview_uses_confirmation_and_rejection_does_not_read_other_sessions(tmp_path, monkeypatch, choice):
    import importlib.util
    from work_records import WorkRecordStore
    spec = importlib.util.spec_from_file_location("synthetic_unqualified_fixture", _CORE / "tests" / "test_multi_project_overview_real_wiring.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    agent, current, project_sessions, _model = fixture._make_fixture(tmp_path, project_count=2)
    store = WorkRecordStore(current)
    item = store.create_item(title="synthetic current independent", scope={"kind": "independent"})
    store.add_requirement(item["item_id"], "synthetic local requirement", event_id="local-event")
    targets = {session.conversation_id for session in project_sessions}
    observed = []
    original_read = WorkRecordStore._read
    def tracked_read(record_store):
        if record_store._conversation_id in targets:
            observed.append(record_store._conversation_id)
        return original_read(record_store)
    monkeypatch.setattr(WorkRecordStore, "_read", tracked_read)
    original_run = agent.run_turn
    agent.run_turn = lambda _text: original_run("当前工作整体概况")
    result, events = fixture._run_with_real_bridge_confirmation(agent, choice=choice)
    assert len([event for event in events if event.get("type") == "confirm.request"]) == 1
    if choice == "n":
        assert not observed and "未读取" in result.assistant_text
    else:
        assert set(observed) == targets
        assert "synthetic current independent" in result.assistant_text
        assert "synthetic-project-1 recorded item" in result.assistant_text
    assert result.tool_rounds == 0
