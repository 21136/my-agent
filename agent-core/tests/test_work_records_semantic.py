import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from work_records_semantic import WorkRecordCandidate, WorkRecordSemanticRecognizer


def candidate(record_id="r1", title="deploy api", text="release notes", session_id="s1"):
    return WorkRecordCandidate(record_id=record_id, title=title, excerpt=text, session_id=session_id)


def batch(ids, evidence=None):
    import json
    return json.dumps({"status": "candidates" if ids else "none", "candidate_ids": ids,
                       "evidence": evidence or {rid: "synthetic relevance" for rid in ids}})


def final(status="match", ids=("r1",), evidence="synthetic reason"):
    import json
    return json.dumps({"status": status, "candidate_id": ids[0] if status == "match" else None,
                       "candidate_ids": list(ids), "evidence": evidence})


def test_returns_only_a_valid_candidate_id_and_limits_prompt_to_current_session_fields():
    seen = []
    responses = iter([batch(["r1"]), final()])
    result = WorkRecordSemanticRecognizer(lambda prompt: seen.append(prompt) or next(responses)).match("上线服务", [candidate()])
    assert result.status == "match" and result.candidate_id == "r1"
    assert all("上线服务" in prompt and "release notes" in prompt for prompt in seen)
    assert all('"session_id"' not in prompt for prompt in seen)


def test_empty_excerpt_is_allowed_and_not_augmented():
    seen = []
    responses = iter([batch([]), final("not_found", (), "No plausible record in complete search.")])
    result = WorkRecordSemanticRecognizer(lambda prompt: seen.append(prompt) or next(responses)).match("x", [candidate(text="")])
    assert result.status == "incomplete"
    assert '"excerpt": ""' in seen[0]


def test_rejects_model_generated_id_outside_batch():
    result = WorkRecordSemanticRecognizer(lambda _: batch(["invented"])).match("部署", [candidate()])
    assert result.status == "incomplete" and result.candidate_id is None


def test_rejects_extra_fields_and_malformed_json_as_incomplete():
    for response in ('{"status":"none","candidate_ids":[],"evidence":{},"query":"invented"}', 'not json'):
        result = WorkRecordSemanticRecognizer(lambda _, r=response: r).match("x", [candidate()])
        assert result.status == "incomplete" and result.candidate_id is None


def test_no_candidates_does_not_call_model():
    def forbidden(_): raise AssertionError("must not call LLM")
    assert WorkRecordSemanticRecognizer(forbidden).match("x", []).status == "not_found"


def test_mixed_sessions_are_rejected():
    called = False
    def llm(_):
        nonlocal called
        called = True
        return batch(["r1"])
    result = WorkRecordSemanticRecognizer(llm).match("x", [candidate("in", session_id="s1"), candidate("out", session_id="s2")])
    assert result.status == "uncertain" and not called


def test_annotation_is_current_session_only_and_explicit():
    rec = WorkRecordSemanticRecognizer(lambda prompt: batch(["r1"]) if "RECALL BATCH" in prompt else final())
    assert rec.match("x", [candidate()], annotate_current_session=True).scope == "current_session"
    assert rec.match("x", [candidate()]).scope is None


def test_over_call_budget_rejects_entire_search_without_model_call():
    called = False
    def llm(_):
        nonlocal called
        called = True
        return batch([])
    result = WorkRecordSemanticRecognizer(llm, max_candidates=1, max_calls=2).match("x", [candidate("a"), candidate("b")])
    assert result.status == "incomplete" and not called


def test_over_budget_text_is_not_silently_truncated():
    called = False
    def llm(_):
        nonlocal called
        called = True
        return batch([])
    result = WorkRecordSemanticRecognizer(llm, max_input_chars=10).match("x", [candidate()])
    assert result.status == "incomplete" and not called


def test_awaitable_llm_response_is_rejected_without_event_loop():
    async def llm(_):
        return batch(["r1"])
    result = WorkRecordSemanticRecognizer(llm).match("x", [candidate()])
    assert result.status == "incomplete" and result.candidate_id is None


def test_llm_failure_fails_closed_as_incomplete():
    def fail(_): raise RuntimeError("synthetic offline")
    result = WorkRecordSemanticRecognizer(fail).match("x", [candidate()])
    assert result.status == "incomplete" and result.candidate_id is None


def test_sync_llm_client_adapter_disables_tools_and_uses_json_response():
    from types import SimpleNamespace
    responses = iter([batch(["r1"]), final()])
    class SyncLLMClient:
        def __init__(self): self.kwargs = None
        def chat(self, messages, **kwargs):
            self.kwargs = kwargs
            assert messages[0]["role"] == "user"
            return SimpleNamespace(content=next(responses), tool_calls=[])
    client = SyncLLMClient()
    result = WorkRecordSemanticRecognizer.from_sync_llm_client(client, model="test-model").match("x", [candidate()])
    assert result.status == "match" and result.candidate_id == "r1"
    assert client.kwargs["tools"] == []
    assert client.kwargs["response_format"] == {"type": "json_object"}
    assert client.kwargs["model"] == "test-model"


def test_batches_collect_multiple_candidates_then_arbitrate_independently():
    calls = []
    responses = iter([
        '{"status":"candidates","candidate_ids":["r1","r2"],"evidence":{"r1":"matches deployment","r2":"mentions rollback"}}',
        '{"status":"candidates","candidate_ids":["r3"],"evidence":{"r3":"same service"}}',
        '{"status":"ambiguous","candidate_id":null,"candidate_ids":["r1","r3"],"evidence":"Both records match the service deployment; dates are needed to distinguish."}',
    ])
    def llm(prompt):
        calls.append(prompt)
        return next(responses)
    candidates = [candidate("r1"), candidate("r2"), candidate("r3"), candidate("r4")]
    result = WorkRecordSemanticRecognizer(llm, max_candidates=2).match("那次服务部署", candidates)
    assert result.status == "ambiguous"
    assert result.candidate_ids == ("r1", "r3")
    assert "dates are needed" in result.evidence
    assert len(calls) == 3
    assert "FINAL ARBITRATION" in calls[-1]
    assert '"id": "r1"' in calls[-1] and '"id": "r3"' in calls[-1]


def test_batch_rejects_invalid_id_even_if_other_ids_are_valid_and_never_arbitrates():
    calls = []
    responses = iter([
        '{"status":"candidates","candidate_ids":["r1","invented"],"evidence":{}}',
        '{"status":"candidates","candidate_ids":["r2"],"evidence":{}}',
    ])
    def llm(prompt):
        calls.append(prompt)
        return next(responses)
    result = WorkRecordSemanticRecognizer(llm, max_candidates=1).match("x", [candidate("r1"), candidate("r2")])
    assert result.status == "incomplete" and result.candidate_id is None
    assert len(calls) == 1


def test_any_batch_failure_is_incomplete_not_not_found_or_match():
    count = 0
    def llm(_):
        nonlocal count
        count += 1
        if count == 1:
            return '{"status":"candidates","candidate_ids":["r1"],"evidence":{"r1":"plausible"}}'
        raise RuntimeError("synthetic outage")
    result = WorkRecordSemanticRecognizer(llm, max_candidates=1).match("x", [candidate("r1"), candidate("r2")])
    assert result.status == "incomplete" and result.candidate_id is None


def test_batch_and_call_budgets_fail_explicitly_without_partial_match():
    calls = []
    def llm(prompt):
        calls.append(prompt)
        return '{"status":"candidates","candidate_ids":[],"evidence":{}}'
    result = WorkRecordSemanticRecognizer(llm, max_candidates=1, max_calls=2).match(
        "x", [candidate("a"), candidate("b"), candidate("c")]
    )
    assert result.status == "incomplete" and len(calls) == 0


def test_final_arbiter_must_only_select_from_current_session_proposals():
    responses = iter([
        '{"status":"candidates","candidate_ids":["r1"],"evidence":{"r1":"plausible"}}',
        '{"status":"match","candidate_id":"r2","candidate_ids":[],"evidence":"guess"}',
    ])
    result = WorkRecordSemanticRecognizer(lambda _: next(responses), max_candidates=2).match("x", [candidate("r1")])
    assert result.status == "incomplete" and result.candidate_id is None


def test_oversized_batch_returns_incomplete_without_model_call():
    called = False
    def llm(_):
        nonlocal called
        called = True
        return "{}"
    result = WorkRecordSemanticRecognizer(llm, max_input_chars=12).match("x", [candidate()])
    assert result.status == "incomplete" and not called




def test_llm_none_responses_are_not_proof_of_no_record():
    result = WorkRecordSemanticRecognizer(lambda _: batch([])).match("同义表达", [candidate()])
    assert result.status == "incomplete"
    assert result.candidate_id is None


def test_final_not_found_is_not_accepted_after_unproven_candidate_recall():
    responses = iter([batch(["r1"]), final("not_found", (), "No record.")])
    result = WorkRecordSemanticRecognizer(lambda _: next(responses)).match("那次部署", [candidate()])
    assert result.status == "incomplete"
    assert result.candidate_id is None


def test_batch_prompt_explicitly_requires_synonyms_pronouns_and_tail_scan():
    prompts = []
    def llm(prompt):
        prompts.append(prompt)
        return batch([])
    result = WorkRecordSemanticRecognizer(llm, max_candidates=1).match(
        "那个服务后来怎么处理的", [candidate("r1"), candidate("r2")]
    )
    assert result.status == "incomplete"
    assert len(prompts) == 2
    assert all("every candidate" in p.lower() and "last candidate" in p.lower() for p in prompts)
    assert all("synonym" in p.lower() and "pronoun" in p.lower() for p in prompts)


def test_999_candidate_simulation_fails_budget_before_any_model_call():
    called = False
    def llm(_):
        nonlocal called
        called = True
        return batch([])
    result = WorkRecordSemanticRecognizer(llm, max_candidates=2, max_calls=8).match(
        "large synthetic query", [candidate(f"r{i:04d}") for i in range(999)]
    )
    assert result.status == "incomplete"
    assert not called


def test_timeout_and_overload_failures_are_incomplete_without_partial_match():
    for error in (TimeoutError("synthetic timeout"), RuntimeError("synthetic overload/write failure")):
        result = WorkRecordSemanticRecognizer(lambda _, e=error: (_ for _ in ()).throw(e)).match(
            "x", [candidate()]
        )
        assert result.status == "incomplete"
        assert result.candidate_id is None


def test_candidate_order_does_not_change_budget_or_claim_no_match():
    candidates = [candidate("r1"), candidate("r2"), candidate("r3")]
    for ordered in (candidates, list(reversed(candidates))):
        result = WorkRecordSemanticRecognizer(lambda _: batch([]), max_candidates=1).match("x", ordered)
        assert result.status == "incomplete"

