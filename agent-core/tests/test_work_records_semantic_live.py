from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from work_records_semantic import WorkRecordCandidate, WorkRecordSemanticRecognizer

LIVE_OPT_IN = "MY_AGENT_LIVE_WORK_RECORD_SEMANTIC"
MODEL_ID = "0x567-flash"


def _candidates() -> list[WorkRecordCandidate]:
    # Entirely fictional records: no session files or user data are loaded.
    return [
        WorkRecordCandidate(
            "synthetic-meeting", "海棠组交流会改期", "原定周二的讨论挪到周四下午，在虚构图书馆举行。", "synthetic-session"
        ),
        WorkRecordCandidate(
            "synthetic-export", "松果项目导出校验", "检查 CSV 列名与虚构样例中的字段映射。", "synthetic-session"
        ),
        WorkRecordCandidate(
            "synthetic-reading", "整理远航读书会材料", "汇总三篇虚构文章的主题和讨论问题。", "synthetic-session"
        ),
    ]


def _ambiguous_candidates() -> list[WorkRecordCandidate]:
    return [
        WorkRecordCandidate("synthetic-a", "海棠组交流会改期", "小组碰面调整到周四。", "synthetic-session"),
        WorkRecordCandidate("synthetic-b", "松果组讨论改期", "讨论会也调整到周四。", "synthetic-session"),
    ]


class _FakeSemanticLLM:
    """Return protocol-compliant synthetic JSON for each recognizer phase."""

    def __init__(self, *, proposed_ids: tuple[str, ...], final_status: str, final_ids: tuple[str, ...] = ()) -> None:
        self.proposed_ids = proposed_ids
        self.final_status = final_status
        self.final_ids = final_ids
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        payload = json.loads(prompt.split("\n", 1)[1])
        if prompt.startswith("RECALL BATCH:"):
            available_ids = {candidate["id"] for candidate in payload["candidates"]}
            selected = [rid for rid in self.proposed_ids if rid in available_ids]
            evidence = {rid: "synthetic semantic evidence" for rid in selected}
            return json.dumps({
                "status": "candidates" if selected else "none",
                "candidate_ids": selected,
                "evidence": evidence,
            })
        if prompt.startswith("FINAL ARBITRATION:"):
            chosen = self.final_ids[0] if self.final_status == "match" and self.final_ids else None
            return json.dumps({
                "status": self.final_status,
                "candidate_id": chosen,
                "candidate_ids": list(self.final_ids),
                "evidence": "synthetic arbitration evidence",
            })
        raise AssertionError("recognizer prompt did not identify a supported protocol phase")


def test_fake_semantic_match_uses_meaning_not_shared_keywords():
    fake = _FakeSemanticLLM(proposed_ids=("synthetic-meeting",), final_status="match", final_ids=("synthetic-meeting",))
    result = WorkRecordSemanticRecognizer(fake).match("那场碰面从原来的日子顺延到什么时候了？", _candidates())
    assert (result.status, result.candidate_id) == ("match", "synthetic-meeting")
    assert "那场碰面" in fake.prompts[0]
    assert "周四下午" in fake.prompts[0]
    assert len(fake.prompts) == 2
    assert fake.prompts[0].startswith("RECALL BATCH:")
    assert fake.prompts[1].startswith("FINAL ARBITRATION:")


def test_fake_semantic_ambiguity_returns_ambiguous_without_guessing():
    candidate_ids = ("synthetic-a", "synthetic-b")
    fake = _FakeSemanticLLM(proposed_ids=candidate_ids, final_status="ambiguous", final_ids=candidate_ids)
    result = WorkRecordSemanticRecognizer(fake).match(
        "周四改期的是哪个小组的活动？", _ambiguous_candidates()
    )
    assert (result.status, result.candidate_ids) == ("ambiguous", candidate_ids)
    assert fake.prompts[0].startswith("RECALL BATCH:")
    assert fake.prompts[1].startswith("FINAL ARBITRATION:")


def test_fake_semantic_unrelated_question_without_proposals_is_incomplete():
    fake = _FakeSemanticLLM(proposed_ids=(), final_status="not_found")
    result = WorkRecordSemanticRecognizer(fake).match("火星探测器的太阳能板有多大？", _candidates())
    assert (result.status, result.candidate_id, result.candidate_ids) == ("incomplete", None, ())
    assert len(fake.prompts) == 1
    assert fake.prompts[0].startswith("RECALL BATCH:")


@pytest.mark.parametrize(
    ("case_name", "question", "candidates", "expected_status", "expected_candidate_id"),
    [
        pytest.param(
            "paraphrase_match",
            "那场碰面从原来的日子顺延到什么时候了？",
            _candidates(),
            "match",
            "synthetic-meeting",
            id="semantic-paraphrase-matches-target",
        ),
        pytest.param(
            "ambiguous_candidates",
            "周四改期的是哪个小组的活动？",
            _ambiguous_candidates(),
            "ambiguous",
            None,
            id="ambiguous-candidates-do-not-guess",
        ),
        pytest.param(
            "unrelated_request",
            "火星探测器的太阳能板有多大？",
            _candidates(),
            "incomplete",
            None,
            id="unrelated-request-incomplete-does-not-match",
        ),
    ],
)
def test_live_0x567_flash_semantic_cases_are_explicitly_opt_in(
    case_name: str,
    question: str,
    candidates: list[WorkRecordCandidate],
    expected_status: str,
    expected_candidate_id: str | None,
):
    if os.environ.get(LIVE_OPT_IN) != "1":
        pytest.skip(f"live 0x567-flash request is opt-in; set {LIVE_OPT_IN}=1")

    from llm_client import LLMClient, LLMResponse, load_config
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

    def live_text_only(prompt: str) -> str:
        response: LLMResponse = client.chat(
            [{"role": "user", "content": prompt}],
            model=MODEL_ID,
            temperature=0.0,
            tools=None,
            response_format={"type": "json_object"},
        )
        if response.tool_calls:
            return "{}"
        return response.content or ""

    result = WorkRecordSemanticRecognizer(live_text_only).match(question, candidates)
    actual_candidate = result.candidate_id
    if expected_status == "ambiguous":
        assert result.status == expected_status, f"synthetic live case {case_name!r} expected ambiguity, got {result!r}"
        return
    assert (result.status, actual_candidate) == (expected_status, expected_candidate_id), (
        f"synthetic live case {case_name!r} failed its strict expectation; "
        f"expected={(expected_status, expected_candidate_id)!r}, "
        f"actual={(result.status, result.candidate_id)!r}"
    )
    if case_name == "unrelated_request":
        assert result.candidate_ids == ()


def test_live_controller_recalls_synthetic_store_record_semantically(tmp_path: Path):
    """Opt-in end-to-end semantic recall over a temporary synthetic session only."""
    if os.environ.get(LIVE_OPT_IN) != "1":
        pytest.skip(f"live 0x567-flash request is opt-in; set {LIVE_OPT_IN}=1")

    from llm_client import LLMClient, load_config
    from llm_models import get_registry
    from llm_secrets import get_llm_secret
    from paths import AgentPaths
    from session import create_new
    from work_records import WorkRecordStore
    from work_records_experiment import WorkRecordExperimentController

    paths = AgentPaths.discover(start=Path(__file__).resolve())
    model = get_registry(paths).get(MODEL_ID)
    if model is None or model.id != MODEL_ID:
        pytest.fail(f"sandbox model registry does not contain {MODEL_ID}")
    if model.api_key_env and not get_llm_secret(model.api_key_env, paths):
        pytest.skip("0x567 credential unavailable; no live request made")

    temp_root = tmp_path / "synthetic-agent"
    temp_root.mkdir()
    temp_paths = AgentPaths(
        agent_root=temp_root,
        workspace=temp_root / "workspace",
        data=temp_root / "data",
        evolve=temp_root / "evolve",
    )
    session = create_new(temp_paths, conversation_id="synthetic-semantic-recall-session")
    store = WorkRecordStore(session)
    stored = store.create_item(
        title="海棠组交流会改期",
        scope={"kind": "independent"},
    )
    store.add_requirement(
        stored["item_id"],
        "原定周二的讨论挪到周四下午，在虚构图书馆举行。",
        event_id="synthetic-semantic-recall-requirement",
    )

    class _NoAgent:
        def __init__(self, current_session):
            self.session = current_session

        def run_turn(self, _user_text):
            raise AssertionError("semantic recall must not invoke Agent")

    # This callback only selects the controller's recall branch. It does not
    # return a query, candidate, or semantic decision; those come from the real matcher.
    def route_recall_only(_user_text, _items):
        return {"action": "recall"}

    client = LLMClient(load_config(), paths=paths)

    class _ObservedClient:
        def __init__(self, delegate):
            self.delegate = delegate
            self.calls = []

        def chat(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            return self.delegate.chat(messages, **kwargs)

    observed_client = _ObservedClient(client)
    from work_records_semantic import WorkRecordSemanticRecognizer

    semantic_matcher = WorkRecordSemanticRecognizer.from_sync_llm_client(
        observed_client, model=MODEL_ID
    )
    controller = WorkRecordExperimentController(
        agent=_NoAgent(session),
        store=store,
        recognizer=route_recall_only,
        semantic_matcher=semantic_matcher,
        semantic_excerpt_policy=lambda _title, requirements: "\n".join(requirements),
    )

    # The query deliberately shares no title phrase; only meaning connects it to the record.
    result = controller.run_turn("那场碰面从原来的日子顺延到什么时候了？")
    assert result.reply.startswith("以下仅为当前会话已存工作记录")
    assert "海棠组交流会改期" in result.reply
    assert "周四下午" in result.reply
    assert result.item_id is None
    assert result.turn is None
    assert len(observed_client.calls) == 2
    outbound_prompts = []
    for messages, kwargs in observed_client.calls:
        assert kwargs["model"] == MODEL_ID
        assert kwargs["tools"] == []
        assert kwargs["response_format"] == {"type": "json_object"}
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        outbound = messages[0]["content"]
        assert str(temp_root) not in outbound
        assert str(paths.agent_root) not in outbound
        assert session.conversation_id not in outbound
        outbound_prompts.append(outbound)
    assert outbound_prompts[0].startswith("RECALL BATCH:")
    batch_payload = json.loads(outbound_prompts[0].split("\n", 1)[1])
    assert batch_payload == {
        "question": "那场碰面从原来的日子顺延到什么时候了？",
        "batch": 1,
        "candidates": [{
            "id": stored["item_id"],
            "title": "海棠组交流会改期",
            "excerpt": "原定周二的讨论挪到周四下午，在虚构图书馆举行。",
        }],
    }
    assert outbound_prompts[1].startswith("FINAL ARBITRATION:")
    arbitration_payload = json.loads(outbound_prompts[1].split("\n", 1)[1])
    assert arbitration_payload["question"] == "那场碰面从原来的日子顺延到什么时候了？"
    assert arbitration_payload["proposals"] == [{
        "id": stored["item_id"],
        "title": "海棠组交流会改期",
        "excerpt": "原定周二的讨论挪到周四下午，在虚构图书馆举行。",
        "batch_evidence": arbitration_payload["proposals"][0]["batch_evidence"],
    }]
    assert arbitration_payload["proposals"][0]["batch_evidence"]

