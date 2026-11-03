from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore


def make_session(root: Path, project_id: str = "") -> Session:
    conversation_id = "synthetic-session"
    session_dir = root / "data" / "sessions" / conversation_id
    session_dir.mkdir(parents=True)
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    return Session(conversation_id=conversation_id, session_dir=session_dir, goal="", meta=SessionMeta(project_id=project_id), messages=[], paths=paths)


class FakeAgent:
    def __init__(self, session):
        self.session = session
        self.calls = []
    def run_turn(self, text):
        self.calls.append(text)
        return type("Turn", (), {"assistant_text": "I have completed it", "tool_rounds": 0})()


def test_natural_request_records_requirement_but_agent_reply_stays_unverified(tmp_path):
    from work_records_experiment import WorkRecordExperimentController

    session = make_session(tmp_path, "project-1")
    store = WorkRecordStore(session)
    agent = FakeAgent(session)
    seen = []
    def recognizer(text, records):
        seen.append((text, records))
        return {"action": "record", "scope": {"kind": "project", "project_id": "project-1"}, "title": "synthetic login page", "requirement": "adjust synthetic button"}

    controller = WorkRecordExperimentController(
        agent=agent, store=store, recognizer=recognizer,
        idempotency_key_provider=lambda _text: "synthetic-experiment:login-adjustment",
    )
    result = controller.run_turn("please adjust the synthetic button")
    assert seen[0][0] == "please adjust the synthetic button"
    assert result.turn.assistant_text == "I have completed it"
    items = store.list_items()
    assert len(items) == 1
    assert items[0]["requirements"][0]["text"] == "adjust synthetic button"
    assert items[0]["runs"][0]["status"] == "unknown"
    assert items[0]["runs"][0]["verification"]["status"] == "not_recorded"
    assert agent.calls == ["please adjust the synthetic button"]


def test_recall_uses_recognizer_and_returns_restricted_current_session_answer(tmp_path):
    from work_records_experiment import WorkRecordExperimentController

    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    item = store.create_item(title="synthetic task", scope={"kind": "independent"})
    requirement = store.add_requirement(item["item_id"], "save synthetic requirement", event_id="req-1", idempotency_key="synthetic-fixture:req-1")
    store.record_result(item["item_id"], requirement["requirement_id"], run_id="run-1", result_id="result-1", status="unknown", summary="no verified outcome", verification={"status": "not_recorded", "summary": ""})
    agent = FakeAgent(session)
    def recognizer(text, records):
        assert text == "what was the synthetic task requirement?"
        assert len(records) == 1
        assert set(records[0]) == {"item_id", "title", "scope"}
        assert "runs" not in records[0] and "history" not in records[0]
        return {"action": "recall", "query": "synthetic task"}
    controller = WorkRecordExperimentController(agent=agent, store=store, recognizer=recognizer)
    result = controller.run_turn("what was the synthetic task requirement?")
    assert result.turn is None
    assert "save synthetic requirement" in result.reply
    assert "未核验" in result.reply
    assert agent.calls == []


def test_uncertain_recognition_does_not_create_or_recall(tmp_path):
    from work_records_experiment import WorkRecordExperimentController

    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    agent = FakeAgent(session)
    controller = WorkRecordExperimentController(agent=agent, store=store, recognizer=lambda text, records: {"action": "uncertain"})
    result = controller.run_turn("remember the earlier one")
    assert result.turn is None
    assert "请澄清" in result.reply
    assert store.list_items() == []
    assert agent.calls == []


def test_result_storage_failure_is_reported_without_rerunning_agent(tmp_path, monkeypatch):
    from work_records_experiment import WorkRecordExperimentController

    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    agent = FakeAgent(session)
    controller = WorkRecordExperimentController(
        agent=agent, store=store,
        recognizer=lambda text, records: {
            "action": "record", "scope": {"kind": "independent"},
            "title": "synthetic", "requirement": "synthetic requirement",
        },
        idempotency_key_provider=lambda _text: "synthetic-experiment:result-failure",
    )
    def fail(*args, **kwargs):
        raise OSError("synthetic storage failure")
    monkeypatch.setattr(store, "record_result", fail)
    result = controller.run_turn("do synthetic work")
    assert result.storage_error == "OSError"
    assert agent.calls == ["do synthetic work"]


def test_atomic_requirement_write_failure_leaves_no_partial_item_or_execute(tmp_path, monkeypatch):
    from work_records_experiment import WorkRecordExperimentController

    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    agent = FakeAgent(session)
    controller = WorkRecordExperimentController(
        agent=agent, store=store,
        recognizer=lambda text, records: {
            "action": "record", "scope": {"kind": "independent"},
            "title": "synthetic retained item", "requirement": "synthetic requirement",
        },
        idempotency_key_provider=lambda _text: "synthetic-experiment:atomic-write-failure",
    )
    def fail(*args, **kwargs):
        raise OSError("synthetic requirement write failure")
    monkeypatch.setattr(store, "create_item_with_requirement", fail)
    result = controller.run_turn("do synthetic work")
    items = store.list_items()
    assert result.turn is None
    assert result.item_id is None
    assert result.storage_error == "OSError"
    assert "未执行 Agent" in result.reply
    assert items == []
    assert agent.calls == []


def test_agent_exception_leaves_unknown_result_without_retry(tmp_path):
    from work_records_experiment import WorkRecordExperimentController

    class RaisingAgent(FakeAgent):
        def run_turn(self, text):
            self.calls.append(text)
            raise RuntimeError("synthetic interrupted turn")

    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    agent = RaisingAgent(session)
    controller = WorkRecordExperimentController(
        agent=agent, store=store,
        recognizer=lambda text, records: {
            "action": "record", "scope": {"kind": "independent"},
            "title": "synthetic", "requirement": "synthetic requirement",
        },
        idempotency_key_provider=lambda _text: "synthetic-experiment:agent-exception",
    )
    result = controller.run_turn("do synthetic work")
    items = store.list_items()
    assert result.turn is None
    assert result.agent_error == "RuntimeError"
    assert result.storage_error is None
    assert "待核实" in result.reply
    assert items[0]["runs"][0]["status"] == "unknown"
    assert items[0]["requirements"][0]["status"] == "unknown"
    assert agent.calls == ["do synthetic work"]



def test_semantic_recall_passes_only_current_session_candidate_fields_and_fetches_verified_id(tmp_path):
    from work_records_experiment import WorkRecordExperimentController

    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    item = store.create_item(title="synthetic deployment", scope={"kind": "independent"})
    requirement = store.add_requirement(item["item_id"], "restart synthetic service", event_id="semantic-req")
    store.record_result(item["item_id"], requirement["requirement_id"], run_id="run", result_id="result", status="unknown", summary="SENSITIVE_RESULT", verification={"status": "private"}, sources=[{"kind": "work_record", "id": "source-secret"}])
    agent = FakeAgent(session)

    class Matcher:
        def match(self, question, candidates):
            assert question == "what about restarting that service?"
            assert len(candidates) == 1
            assert candidates[0].record_id == item["item_id"]
            assert candidates[0].title == "synthetic deployment"
            excerpt = json.loads(candidates[0].excerpt)
            assert excerpt == {"title": "synthetic deployment", "requirements": ["restart synthetic service"]}
            assert candidates[0].session_id == session.conversation_id
            assert not hasattr(candidates[0], "runs") and not hasattr(candidates[0], "history")
            assert "SENSITIVE_RESULT" not in repr(candidates)
            assert "source-secret" not in repr(candidates)
            return type("Match", (), {"status": "match", "candidate_id": item["item_id"]})()

    controller = WorkRecordExperimentController(
        agent=agent, store=store, recognizer=lambda text, candidates: {"action": "recall", "query": "not used"},
        semantic_matcher=Matcher(),
    )
    result = controller.run_turn("what about restarting that service?")
    assert "restart synthetic service" in result.reply
    assert "SENSITIVE_RESULT" not in result.reply
    assert agent.calls == []


def test_semantic_recall_rejects_forged_or_cross_session_id_before_get_item(tmp_path, monkeypatch):
    from work_records_experiment import WorkRecordExperimentController

    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    store.create_item(title="synthetic current", scope={"kind": "independent"})
    calls = []
    original_get = store.get_item
    monkeypatch.setattr(store, "get_item", lambda record_id: (calls.append(record_id), original_get(record_id))[1])
    agent = FakeAgent(session)
    controller = WorkRecordExperimentController(
        agent=agent, store=store, recognizer=lambda *_: {"action": "recall", "query": "ignored"},
        semantic_matcher=type("Matcher", (), {"match": lambda self, question, candidates: type("Match", (), {"status": "match", "candidate_id": "foreign-session-id"})()})(),
    )
    result = controller.run_turn("lookup another session")
    assert "回查线索" in result.reply
    assert calls == []
    assert agent.calls == []


def test_semantic_recall_ambiguity_and_matcher_failure_clarify_without_lookup(tmp_path, monkeypatch):
    from work_records_experiment import WorkRecordExperimentController

    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    store.create_item(title="synthetic", scope={"kind": "independent"})
    calls = []
    monkeypatch.setattr(store, "get_item", lambda record_id: calls.append(record_id))
    agent = FakeAgent(session)

    class Matcher:
        def __init__(self, result=None, fail=False):
            self.result, self.fail = result, fail
        def match(self, question, candidates):
            if self.fail:
                raise RuntimeError("synthetic matcher failure")
            return self.result

    for matcher in (
        Matcher(type("Match", (), {"status": "uncertain", "candidate_id": None})()),
        Matcher(type("Match", (), {"status": "not_found", "candidate_id": None})()),
        Matcher(type("Match", (), {"status": "needs_narrowing", "candidate_id": None})()),
        Matcher(fail=True),
    ):
        controller = WorkRecordExperimentController(
            agent=agent, store=store, recognizer=lambda *_: {"action": "recall", "query": "ignored"},
            semantic_matcher=matcher,
        )
        result = controller.run_turn("what happened to that prior item?")
        assert "回查" in result.reply
    assert calls == []
    assert agent.calls == []


def test_semantic_recall_delegates_more_than_twenty_candidates_to_matcher(tmp_path):
    from work_records_experiment import WorkRecordExperimentController

    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    items = [
        store.create_item(title=f"synthetic {index}", scope={"kind": "independent"})
        for index in range(25)
    ]
    agent = FakeAgent(session)

    class Matcher:
        called_with = None
        def match(self, question, candidates):
            self.called_with = list(candidates)
            return type("Match", (), {"status": "match", "candidate_id": items[-1]["item_id"]})()

    matcher = Matcher()
    controller = WorkRecordExperimentController(
        agent=agent, store=store, recognizer=lambda *_: {"action": "recall"},
        semantic_matcher=matcher,
    )
    result = controller.run_turn("find the last synthetic item")
    assert len(matcher.called_with) == 25
    assert matcher.called_with[-1].record_id == items[-1]["item_id"]
    assert items[-1]["title"] in result.reply
    assert "缩小当前会话的回查范围" not in result.reply
    assert agent.calls == []


def test_semantic_recall_incomplete_matching_cannot_claim_not_found_or_unique_match(tmp_path):
    from work_records_experiment import WorkRecordExperimentController

    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    item = store.create_item(title="synthetic candidate", scope={"kind": "independent"})
    agent = FakeAgent(session)

    for status, candidate_id in (("incomplete", None), ("not_found", None), ("match", item["item_id"])):
        matcher = type(
            "Matcher",
            (),
            {"match": lambda self, question, candidates, status=status, candidate_id=candidate_id: type(
                "Match", (), {"status": status, "candidate_id": candidate_id, "complete": False}
            )()},
        )()
        controller = WorkRecordExperimentController(
            agent=agent, store=store, recognizer=lambda *_: {"action": "recall"},
            semantic_matcher=matcher,
        )
        result = controller.run_turn("lookup synthetic candidate")
        assert result.reply == WorkRecordExperimentController._recall_clarification("needs_narrowing").reply
        assert "synthetic candidate" not in result.reply
        assert "\u6ca1\u67e5\u6e05" in result.reply
        assert "\u6ca1\u6709\u53d1\u751f" in result.reply
    assert agent.calls == []


def test_semantic_excerpt_policy_is_explicit_and_receives_all_synthetic_requirements(tmp_path):
    from work_records_experiment import WorkRecordExperimentController

    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    item = store.create_item(title="synthetic meeting rescheduled", scope={"kind": "independent"})
    store.add_requirement(item["item_id"], "originally Tuesday; moved to Thursday afternoon", event_id="req-one")
    store.add_requirement(item["item_id"], "fictional venue is the library", event_id="req-two")
    agent = FakeAgent(session)
    seen = {}

    def policy(title, requirements):
        seen["title"] = title
        seen["requirements"] = requirements
        return " | ".join(requirements)

    class Matcher:
        def match(self, question, candidates):
            seen["candidate"] = candidates[0]
            return type("Match", (), {"status": "match", "candidate_id": item["item_id"]})()

    controller = WorkRecordExperimentController(
        agent=agent, store=store, recognizer=lambda *_: {"action": "recall"},
        semantic_matcher=Matcher(), semantic_excerpt_policy=policy,
    )
    result = controller.run_turn("when was that meeting moved to?")
    assert seen["title"] == "synthetic meeting rescheduled"
    assert seen["requirements"] == ("originally Tuesday; moved to Thursday afternoon", "fictional venue is the library")
    assert seen["candidate"].excerpt == "originally Tuesday; moved to Thursday afternoon | fictional venue is the library"
    assert "Thursday afternoon" in result.reply


def test_semantic_not_found_and_ambiguity_prompt_only_for_recall_clues(tmp_path):
    from work_records_experiment import WorkRecordExperimentController

    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    store.create_item(title="synthetic A", scope={"kind": "independent"})
    agent = FakeAgent(session)

    for status in ("not_found", "uncertain"):
        matcher = type("Matcher", (), {"match": lambda self, question, candidates, status=status: type("Match", (), {"status": status, "candidate_id": None})()})()
        controller = WorkRecordExperimentController(
            agent=agent, store=store, recognizer=lambda *_: {"action": "recall"},
            semantic_matcher=matcher,
        )
        result = controller.run_turn("what about the prior synthetic item?")
        assert "回查" in result.reply
        assert "新建记录" in result.reply
        assert "\u6ca1\u6709\u53d1\u751f" in result.reply
        if status == "uncertain":
            assert "\u6ca1\u67e5\u6e05" in result.reply
    assert agent.calls == []



def test_default_json_semantic_excerpt_preserves_quoted_multiline_requirement(tmp_path):
    from work_records_experiment import WorkRecordExperimentController

    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    item = store.create_item(title='synthetic exact text', scope={'kind': 'independent'})
    text = 'quote: "keep"\nnext line\\folder'
    store.add_requirement(item['item_id'], text, event_id='escaped-requirement')
    seen = {}

    class Matcher:
        def match(self, _question, candidates):
            seen['excerpt'] = candidates[0].excerpt
            return type('Match', (), {'status': 'match', 'candidate_id': item['item_id']})()

    controller = WorkRecordExperimentController(
        agent=FakeAgent(session), store=store,
        recognizer=lambda *_: {'action': 'recall'}, semantic_matcher=Matcher(),
    )

    result = controller.run_turn('find the escaped synthetic requirement')

    assert json.loads(seen['excerpt']) == {'title': 'synthetic exact text', 'requirements': [text]}
    assert '缩小' not in result.reply
    assert 'quote&#58; &#34;keep&#34; next line&#92;folder' in result.reply


def test_semantic_excerpt_policy_cannot_hide_unincluded_requirement_details(tmp_path):
    from work_records_experiment import WorkRecordExperimentController

    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    item = store.create_item(title="synthetic meeting", scope={"kind": "independent"})
    store.add_requirement(item["item_id"], "moved from Tuesday to Thursday", event_id="req-detail")
    agent = FakeAgent(session)
    class Matcher:
        called = False
        def match(self, question, candidates):
            self.called = True
            raise AssertionError("incomplete candidate excerpt must not be matched")
    matcher = Matcher()
    controller = WorkRecordExperimentController(
        agent=agent, store=store, recognizer=lambda *_: {"action": "recall"},
        semantic_matcher=matcher,
        semantic_excerpt_policy=lambda title, requirements: "meeting moved date unknown",
    )
    result = controller.run_turn("when was it moved to?")
    assert "回查" in result.reply and "缩小" in result.reply
    assert not matcher.called
    assert agent.calls == []


def test_semantic_recall_ambiguity_verifies_every_current_session_id_and_asks_user(tmp_path, monkeypatch):
    from work_records_experiment import WorkRecordExperimentController

    session = make_session(tmp_path, "project-1")
    store = WorkRecordStore(session)
    first = store.create_item(title="synthetic deployment", scope={"kind": "independent"})
    second = store.create_item(title="synthetic service restart", scope={"kind": "project", "project_id": "project-1"})
    store.add_requirement(first["item_id"], "PRIVATE first requirement", event_id="req-first")
    store.add_requirement(second["item_id"], "PRIVATE second requirement", event_id="req-second")
    looked_up = []
    original_get = store.get_item

    def tracked_get(record_id):
        looked_up.append(record_id)
        return original_get(record_id)

    monkeypatch.setattr(store, "get_item", tracked_get)
    agent = FakeAgent(session)
    matcher = type("Matcher", (), {
        "match": lambda self, question, candidates: type("Match", (), {
            "status": "ambiguous", "candidate_ids": (first["item_id"], second["item_id"]),
            "evidence": "PRIVATE model evidence",
        })(),
    })()
    controller = WorkRecordExperimentController(
        agent=agent, store=store, recognizer=lambda *_: {"action": "recall"}, semantic_matcher=matcher,
    )

    result = controller.run_turn("which synthetic task?")

    assert looked_up == [first["item_id"], second["item_id"]]
    assert "synthetic deployment" in result.reply
    assert "synthetic service restart" in result.reply
    assert "哪一项" in result.reply or "哪一条" in result.reply
    assert "PRIVATE" not in result.reply
    assert agent.calls == []


def test_semantic_recall_ambiguity_rejects_all_results_if_any_id_is_not_current(tmp_path, monkeypatch):
    from work_records_experiment import WorkRecordExperimentController

    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    item = store.create_item(title="synthetic current", scope={"kind": "independent"})
    looked_up = []
    original_get = store.get_item

    def tracked_get(record_id):
        looked_up.append(record_id)
        return original_get(record_id)

    monkeypatch.setattr(store, "get_item", tracked_get)
    agent = FakeAgent(session)
    matcher = type("Matcher", (), {
        "match": lambda self, question, candidates: type("Match", (), {
            "status": "ambiguous", "candidate_ids": (item["item_id"], "foreign-session-id"),
        })(),
    })()
    controller = WorkRecordExperimentController(
        agent=agent, store=store, recognizer=lambda *_: {"action": "recall"}, semantic_matcher=matcher,
    )

    result = controller.run_turn("what happened?")

    assert looked_up == []
    assert "\u6ca1\u67e5\u6e05" in result.reply, repr(result.reply.encode("unicode_escape"))
    assert "\u6ca1\u6709\u53d1\u751f" in result.reply, repr(result.reply.encode("unicode_escape"))
    assert "synthetic current" not in result.reply
    assert agent.calls == []


def test_controller_integrates_real_luna_recall_with_fake_registry_and_responses(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    import work_records_luna_recall as luna
    from work_records_semantic import SearchResult
    from work_records_experiment import WorkRecordExperimentController

    current_session = make_session(tmp_path, "current-project")
    store = WorkRecordStore(current_session)
    first = store.create_item(title="synthetic deployment", scope={"kind": "independent"})
    second = store.create_item(title="synthetic service restart", scope={"kind": "project", "project_id": "current-project"})
    foreign_session = make_session(tmp_path / "foreign-agent")
    foreign_store = WorkRecordStore(foreign_session)
    foreign = foreign_store.create_item(title="synthetic foreign record", scope={"kind": "independent"})

    class FakeRegistry:
        def get(self, model_id):
            assert model_id == "0x567-flash"
            return SimpleNamespace(
                id="0x567-flash", vendor="0x567", provider_model="gpt-6-luna",
                max_input_tokens=1_000_000,
            )

    monkeypatch.setattr(luna, "get_registry", lambda *_args: FakeRegistry())
    replies = [
        {"status": "match", "candidate_ids": [first["item_id"]]},
        {"status": "ambiguous", "candidate_ids": [second["item_id"], first["item_id"]]},
    ]
    calls = []

    class FakeClient:
        def chat(self, messages, **kwargs):
            calls.append((messages, kwargs))
            return SimpleNamespace(content=json.dumps(replies[len(calls) - 1]), tool_calls=[])

    matcher = luna.WorkRecordsLunaRecall(FakeClient())
    assert type(matcher) is luna.WorkRecordsLunaRecall
    matcher_results = []
    real_match = matcher.match

    def observe_real_match(question, candidates):
        result = real_match(question, candidates)
        matcher_results.append(result)
        return result

    matcher.match = observe_real_match
    agent = FakeAgent(current_session)
    controller = WorkRecordExperimentController(
        agent=agent, store=store, recognizer=lambda *_: {"action": "recall"},
        semantic_matcher=matcher,
    )

    matched = controller.run_turn("find the deployment")
    ambiguous = controller.run_turn("which service task?")

    assert isinstance(controller._semantic_matcher, luna.WorkRecordsLunaRecall)
    assert len(calls) == 2
    for (messages, kwargs) in calls:
        payload = json.loads(messages[1]["content"])
        assert {row["id"] for row in payload["candidates"]} == {first["item_id"], second["item_id"]}
        assert {row["title"] for row in payload["candidates"]} == {"synthetic deployment", "synthetic service restart"}
        assert all(set(row) == {"id", "title", "excerpt"} for row in payload["candidates"])
        assert "session_id" not in messages[1]["content"]
        assert foreign["item_id"] not in messages[1]["content"]
        assert "synthetic foreign record" not in messages[1]["content"]
        assert kwargs == {
            "model": "0x567-flash", "tools": [], "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }

    # This spy delegates every call to the real matcher and only records its return.
    assert len(matcher_results) == 2
    assert all(isinstance(result, SearchResult) for result in matcher_results)
    assert [(result.status, result.candidate_id, result.candidate_ids, result.scope) for result in matcher_results] == [
        ("match", first["item_id"], (first["item_id"],), "current_session"),
        ("ambiguous", None, (second["item_id"], first["item_id"]), "current_session"),
    ]
    assert matched.turn is None and "synthetic deployment" in matched.reply
    assert "synthetic service restart" in ambiguous.reply
    assert "synthetic deployment" in ambiguous.reply
    assert "哪一条" in ambiguous.reply
    assert agent.calls == []
