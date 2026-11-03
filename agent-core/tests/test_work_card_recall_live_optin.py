from __future__ import annotations

"""Synthetic-only, opt-in real confirmed work-card recall; no GUI/WS involved."""
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from work_card_ledger import LEDGER_FILENAME, WorkCardLedger
from work_record_cards import build_independent_work_record_card, build_project_work_record_cards
from work_records import WorkRecordStore
import work_card_recall as recall
import work_records_luna_recall as luna

OPT_IN = "MY_AGENT_LIVE_WORK_CARD_RECALL_OPTIN"
QUESTION = "Which card is about the synthetic Kestrel741 astronomical observations report?"
PROJECT_TITLE = "Synthetic Kestrel741 astronomy report"
PROJECT_REQUIREMENT = "Prepare a synthetic Kestrel741 report with two fictional astronomical observations."
INDEPENDENT_TITLE = "Synthetic Orion852 gardening note"
INDEPENDENT_REQUIREMENT = "Write a synthetic Orion852 note describing two imaginary garden flowers."


def _fixture(tmp_path):
    root = tmp_path / "synthetic-agent"
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    session_dir = paths.data / "sessions" / "20261101-aabbccdd"
    session_dir.mkdir(parents=True)
    session = Session("20261101-aabbccdd", session_dir, "synthetic only", SessionMeta(project_id="synthetic-project"), [], paths)
    session.save()
    store = WorkRecordStore(session)
    project = store.create_item(title=PROJECT_TITLE, scope={"kind": "project", "project_id": "synthetic-project"})
    store.add_requirement(project["item_id"], PROJECT_REQUIREMENT, event_id="synthetic-project-req")
    independent = store.create_item(title=INDEPENDENT_TITLE, scope={"kind": "independent"})
    store.add_requirement(independent["item_id"], INDEPENDENT_REQUIREMENT, event_id="synthetic-independent-req")
    ledger = WorkCardLedger(session, store)
    project_record = ledger.save_snapshot(str(uuid.uuid4()), build_project_work_record_cards(store, "synthetic-project"))
    independent_record = ledger.save_snapshot(str(uuid.uuid4()), build_independent_work_record_card(store, independent["item_id"]))
    assert project_record.status == independent_record.status == "saved"
    return ledger, project_record.card_id, independent_record.card_id


def _assert_synthetic_preview(prepared, ledger, project_id, independent_id, expected_endpoint, question=QUESTION):
    assert prepared.status == "ready"
    preview = json.loads(prepared.preview_json)
    assert set(preview) == {"model_id", "vendor", "endpoint", "session_id", "provider_payload"}
    assert preview["model_id"] == "0x567-flash" and preview["vendor"] == "0x567"
    assert preview["endpoint"] == expected_endpoint and preview["session_id"] == ledger._session.conversation_id
    body = preview["provider_payload"]
    assert set(body) == {"model", "messages", "temperature", "stream", "response_format"}
    assert body["model"] == "gpt-6-luna" and body["temperature"] == 0.0 and body["stream"] is False
    assert body["response_format"] == {"type": "json_object"}
    messages = body["messages"]
    assert len(messages) == 2 and messages[0] == {"role": "system", "content": luna._SYSTEM}
    assert messages[1]["role"] == "user" and set(messages[1]) == {"role", "content"}
    payload = json.loads(messages[1]["content"])
    assert set(payload) == {"question", "candidates"} and payload["question"] == question
    assert len(payload["candidates"]) == 2
    by_id = {row["id"]: row for row in payload["candidates"]}
    assert set(by_id) == {project_id, independent_id}
    assert by_id[project_id] == {"id": project_id, "title": PROJECT_TITLE,
        "excerpt": json.dumps({"scope": "project", "items": [{"title": PROJECT_TITLE, "requirements": [PROJECT_REQUIREMENT]}], "project_id": "synthetic-project"}, ensure_ascii=False, separators=(",", ":"))}
    assert by_id[independent_id] == {"id": independent_id, "title": INDEPENDENT_TITLE,
        "excerpt": json.dumps({"scope": "independent", "items": [{"title": INDEPENDENT_TITLE, "requirements": [INDEPENDENT_REQUIREMENT]}]}, ensure_ascii=False, separators=(",", ":"))}
    return preview


class ObservedConfirmedClient:
    """Always delegates to the actual LLMClient, recording only bounded metadata."""
    def __init__(self, delegate):
        self.delegate = delegate
        self.calls = 0
        self.response_model = None
        self.finish_reason = None
        self.error_type = None
        self.http_status = None

    def chat_confirmed_json(self, *args, **kwargs):
        if self.calls:
            raise RuntimeError("refuse retry")
        self.calls += 1
        try:
            response = self.delegate.chat_confirmed_json(*args, **kwargs)
        except Exception as exc:
            self.error_type = type(exc).__name__
            status = getattr(exc, "status_code", None)
            self.http_status = status if type(status) is int else None
            raise
        self.response_model = response.model if response.model == "gpt-6-luna" else "other"
        self.finish_reason = response.finish_reason if response.finish_reason in {"stop", "length", "tool_calls"} else "other"
        return response


def test_offline_fixture_prepares_exact_synthetic_preview_and_does_not_send(tmp_path):
    ledger, project_id, independent_id = _fixture(tmp_path)
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), QUESTION)
    if prepared.status != "ready":
        pytest.skip("temporary default provider profile is unavailable")
    url = json.loads(prepared.preview_json)["endpoint"]
    _assert_synthetic_preview(prepared, ledger, project_id, independent_id, url)
    before = (ledger._session_dir / LEDGER_FILENAME).read_bytes()
    class NeverCall:
        def chat_confirmed_json(self, *_args, **_kwargs):
            raise RuntimeError("synthetic no-send")
    result = recall.execute_recall(ledger, prepared, NeverCall(), lambda *_args: "n")
    assert result.status == "incomplete" and not result.records
    assert (ledger._session_dir / LEDGER_FILENAME).read_bytes() == before


@pytest.mark.skipif(os.getenv(OPT_IN) != "1", reason="explicit synthetic-only live opt-in")
def test_live_confirmed_json_single_work_card_recall(tmp_path, monkeypatch):
    import httpx
    from llm_client import LLMClient, load_config
    from llm_models import ModelRegistry
    provider_paths = AgentPaths.discover(start=CORE)
    registry = ModelRegistry.load(provider_paths)
    entry = registry.get("0x567-flash")
    if entry is None or (entry.id, entry.vendor, entry.provider_model) != ("0x567-flash", "0x567", "gpt-6-luna"):
        pytest.fail("configured 0x567 Luna profile unavailable", pytrace=False)
    endpoint = entry.chat_completions_url()
    url = urlsplit(endpoint)
    if (url.scheme != "https" or url.hostname != "api-cdn.0x567.com" or url.username or url.password
            or url.query or url.fragment or url.port not in (None, 443)):
        pytest.fail("configured endpoint outside permitted 0x567 endpoint", pytrace=False)
    if not entry.resolve_api_key(provider_paths):
        pytest.skip("configured credential unavailable; no network request")
    ledger, project_id, independent_id = _fixture(tmp_path)
    # Preparation uses the same real provider profile as the confirmed client;
    # only the registry lookup is injected, never a model response or transport.
    monkeypatch.setattr(luna, "get_registry", lambda _paths: registry)
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), QUESTION)
    preview = _assert_synthetic_preview(prepared, ledger, project_id, independent_id, endpoint)
    before = (ledger._session_dir / LEDGER_FILENAME).read_bytes()
    client = ObservedConfirmedClient(LLMClient(load_config(), paths=provider_paths))
    approvals = []
    posts = []
    expected_body = json.dumps(preview["provider_payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    original_post = httpx.Client.post
    def guarded_post(http_client, url, *args, **kwargs):
        if posts or url != endpoint or kwargs.get("content") != expected_body or kwargs.get("follow_redirects") is not False:
            raise RuntimeError("synthetic endpoint or frozen payload mismatch")
        posts.append(True)
        return original_post(http_client, url, *args, **kwargs)
    monkeypatch.setattr(httpx.Client, "post", guarded_post)
    def confirm(actual, allow_all):
        if approvals or actual != prepared.preview_json or allow_all is not False:
            return "n"
        # Whole frozen request was independently matched against synthetic fixture above.
        if json.loads(actual) != preview:
            return "n"
        approvals.append(True)
        return "y"
    result = recall.execute_recall(ledger, prepared, client, confirm)
    if (result.status != "match" or len(result.records) != 1 or result.records[0].get("card_id") != project_id
            or client.response_model != "gpt-6-luna" or client.finish_reason != "stop"):
        pytest.fail(f"live recall failed: status={result.status}, reason={result.reason}, "
                    f"response_model={client.response_model}, finish_reason={client.finish_reason}, "
                    f"exception_type={client.error_type}, http_status={client.http_status}, "
                    f"confirmed={len(approvals)}, requests={len(posts)}; raw response withheld", pytrace=False)
    assert client.calls == len(approvals) == len(posts) == 1
    assert all(row["viewed_at"] is None for row in ledger.list_cards())
    assert (ledger._session_dir / LEDGER_FILENAME).read_bytes() == before


AGENT_QUESTION = "请找回之前关于Kestrel741天文观测报告的工作卡"
_AGENT_SECRET_ENV = "MY_AGENT_SYNTHETIC_CARD_RECALL_SECRET"


def _configure_temp_profile(paths, endpoint, monkeypatch, secret):
    # Only nonsecret model metadata is written to the synthetic Session root.
    settings = {"models": [{"registryId": "0x567-flash", "modelId": "gpt-6-luna",
                            "vendor": "0x567", "baseUrl": endpoint,
                            "apiKeyEnv": _AGENT_SECRET_ENV, "maxInputTokens": 128000}]}
    config_file = paths.data / "llm_models.json"
    config_file.write_text(json.dumps(settings), encoding="utf-8")
    monkeypatch.setenv(_AGENT_SECRET_ENV, secret)


def _make_agent(ledger, client, confirm):
    from agent import Agent
    from work_records_experiment import WorkRecordExperimentController
    session, store = ledger._session, ledger._store
    agent = Agent.create(session, llm=client, enable_work_record_recall=True, confirm_fn=confirm)
    agent.work_record_recall_controller = WorkRecordExperimentController(
        agent=agent, store=store, recognizer=lambda *_a: {"action": "none"},
        semantic_matcher=luna.WorkRecordsLunaRecall(client))
    return agent


def test_agent_route_rejects_outbound_and_preserves_ledger_offline(tmp_path, monkeypatch):
    from llm_client import LLMClient, load_config
    import httpx
    ledger, project_id, independent_id = _fixture(tmp_path)
    endpoint = "https://api-cdn.0x567.com/v1/chat/completions"
    _configure_temp_profile(ledger._session.paths, endpoint, monkeypatch, "synthetic-fake-key")
    def forbid_post(*_a, **_kw):
        raise AssertionError("declined confirmation must not use network")
    monkeypatch.setattr(httpx.Client, "post", forbid_post)
    approvals = []
    def decline(preview, allow_all):
        from types import SimpleNamespace
        _assert_synthetic_preview(SimpleNamespace(status="ready", preview_json=preview), ledger,
                                  project_id, independent_id, endpoint, question=AGENT_QUESTION)
        assert allow_all is False
        approvals.append(True)
        return "n"
    agent = _make_agent(ledger, LLMClient(load_config(), paths=ledger._session.paths), decline)
    before = (ledger._session_dir / LEDGER_FILENAME).read_bytes()
    turn = agent.run_turn(AGENT_QUESTION)
    assert turn.finish_reason == "work_card_recall" and turn.tool_rounds == 0
    assert len(approvals) == 1 and "不能判断" in turn.assistant_text
    assert (ledger._session_dir / LEDGER_FILENAME).read_bytes() == before
    assert all(card["viewed_at"] is None for card in ledger.list_cards())


@pytest.mark.skipif(os.getenv(OPT_IN) != "1", reason="explicit synthetic-only live opt-in")
def test_live_agent_route_uses_confirmed_synthetic_ledger_and_no_ack(tmp_path, monkeypatch):
    import httpx
    import llm_client as client_module
    from llm_client import LLMClient, load_config
    from llm_models import ModelRegistry
    provider_paths = AgentPaths.discover(start=CORE)
    configured = ModelRegistry.load(provider_paths).get("0x567-flash")
    if configured is None or (configured.id, configured.vendor, configured.provider_model) != ("0x567-flash", "0x567", "gpt-6-luna"):
        pytest.fail("configured provider profile unavailable", pytrace=False)
    endpoint = configured.chat_completions_url()
    url = urlsplit(endpoint)
    if (url.scheme != "https" or url.hostname != "api-cdn.0x567.com" or url.username or url.password
            or url.query or url.fragment or url.port not in (None, 443)):
        pytest.fail("configured endpoint outside permitted 0x567 endpoint", pytrace=False)
    credential = configured.resolve_api_key(provider_paths)
    if not credential:
        pytest.skip("configured credential unavailable; no network request")
    ledger, project_id, independent_id = _fixture(tmp_path)
    _configure_temp_profile(ledger._session.paths, endpoint, monkeypatch, credential)
    del credential
    client = LLMClient(load_config(), paths=ledger._session.paths)
    approvals, posts, response_metadata = [], [], []
    expected = {}
    def confirm(preview, allow_all):
        from types import SimpleNamespace
        if approvals or allow_all is not False:
            return "n"
        data = _assert_synthetic_preview(SimpleNamespace(status="ready", preview_json=preview), ledger,
                                         project_id, independent_id, endpoint, question=AGENT_QUESTION)
        expected["body"] = json.dumps(data["provider_payload"], ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":"), allow_nan=False).encode("utf-8")
        approvals.append(True)
        return "y"
    original_post = httpx.Client.post
    def guarded_post(http_client, url, *args, **kwargs):
        if posts or not approvals or url != endpoint or kwargs.get("content") != expected.get("body") or kwargs.get("follow_redirects") is not False:
            raise RuntimeError("synthetic confirmed payload or endpoint rejected")
        posts.append(True)
        return original_post(http_client, url, *args, **kwargs)
    monkeypatch.setattr(httpx.Client, "post", guarded_post)
    original_parse = client_module._parse_completion
    def observe_parse(data, *, fallback_model):
        response = original_parse(data, fallback_model=fallback_model)
        response_metadata.append((response.model if response.model == "gpt-6-luna" else "other",
                                  response.finish_reason if response.finish_reason in {"stop", "length", "tool_calls"} else "other"))
        return response
    monkeypatch.setattr(client_module, "_parse_completion", observe_parse)
    agent = _make_agent(ledger, client, confirm)
    before = (ledger._session_dir / LEDGER_FILENAME).read_bytes()
    turn = agent.run_turn(AGENT_QUESTION)
    rendered = turn.assistant_text
    presentation_ok = ("【历史工作卡快照】" in rendered
                       and "卡片 ID：" + project_id in rendered
                       and PROJECT_TITLE in rendered
                       and all(field not in rendered for field in ('"snapshot":', '"request_id":', '"snapshot_sha256":')))
    if not (presentation_ok and turn.finish_reason == "work_card_recall" and len(approvals) == len(posts) == 1
            and response_metadata == [("gpt-6-luna", "stop")] and project_id in turn.assistant_text
            and independent_id not in turn.assistant_text):
        pytest.fail(f"agent route failed: finish={turn.finish_reason!r}, confirmations={len(approvals)}, "
                    f"requests={len(posts)}, response_metadata={response_metadata!r}, "
                    f"expected_candidate_present={project_id in rendered}, presentation_ok={presentation_ok}; response withheld", pytrace=False)
    assert turn.tool_rounds == turn.total_tool_rounds == 0
    assert (ledger._session_dir / LEDGER_FILENAME).read_bytes() == before
    assert all(row["viewed_at"] is None for row in ledger.list_cards())
    assert "不代表当前状态" in turn.assistant_text


@pytest.mark.skipif(os.getenv("MY_AGENT_LIVE_BATCH_WORK_CARD_RECALL_OPTIN") != "1",
                    reason="explicit synthetic-only batch live opt-in")
def test_live_batch_recall_two_synthetic_cards_one_confirmation(tmp_path, monkeypatch):
    import httpx
    import llm_client as client_module
    from llm_client import LLMClient, load_config
    from llm_models import ModelRegistry

    provider_paths = AgentPaths.discover(start=CORE)
    configured = ModelRegistry.load(provider_paths).get("0x567-flash")
    if configured is None or (configured.id, configured.vendor, configured.provider_model) != ("0x567-flash", "0x567", "gpt-6-luna"):
        pytest.fail("configured provider profile unavailable", pytrace=False)
    endpoint = configured.chat_completions_url()
    url = urlsplit(endpoint)
    if (url.scheme != "https" or url.hostname != "api-cdn.0x567.com" or url.username or url.password
            or url.query or url.fragment or url.port not in (None, 443)):
        pytest.fail("configured endpoint outside permitted 0x567 endpoint", pytrace=False)
    credential = configured.resolve_api_key(provider_paths)
    if not credential:
        pytest.skip("configured credential unavailable; no network request")
    ledger, project_id, independent_id = _fixture(tmp_path)
    _configure_temp_profile(ledger._session.paths, endpoint, monkeypatch, credential)
    del credential

    expected_rows = {
        project_id: {"id": project_id, "title": PROJECT_TITLE,
            "excerpt": json.dumps({"scope": "project", "items": [{"title": PROJECT_TITLE, "requirements": [PROJECT_REQUIREMENT]}]}, ensure_ascii=False, separators=(",", ":"))},
        independent_id: {"id": independent_id, "title": INDEPENDENT_TITLE,
            "excerpt": json.dumps({"scope": "independent", "items": [{"title": INDEPENDENT_TITLE, "requirements": [INDEPENDENT_REQUIREMENT]}]}, ensure_ascii=False, separators=(",", ":"))},
    }
    def canonical(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    # A single-candidate budget that cannot contain both rows forces exactly two batches.
    budget = max(len(canonical(recall._batch_payload(QUESTION, [row]))) for row in expected_rows.values())
    if budget >= len(canonical(recall._batch_payload(QUESTION, list(expected_rows.values())))):
        pytest.fail("cannot safely force two batches; no request sent", pytrace=False)
    prepared = recall.prepare_batch_recall(ledger, ledger.list_cards(), QUESTION, max_batch_bytes=budget)
    if prepared.status != "ready":
        pytest.fail(f"batch preflight unavailable: {prepared.reason}; no request sent", pytrace=False)
    preview = json.loads(prepared.preview_json)
    if (set(preview) != {"model_id", "vendor", "endpoint", "session_id", "provider_payloads"}
            or (preview["model_id"], preview["vendor"], preview["endpoint"], preview["session_id"]) !=
               ("0x567-flash", "0x567", endpoint, ledger._session.conversation_id)
            or type(preview["provider_payloads"]) is not list or len(preview["provider_payloads"]) != 2):
        pytest.fail("unexpected batch preview; no request sent", pytrace=False)
    seen = set()
    bodies = []
    for body in preview["provider_payloads"]:
        try:
            messages = body["messages"]
            content = json.loads(messages[1]["content"])
            rows = content["candidates"]
            row = rows[0]
            valid = (set(body) == {"model", "messages", "temperature", "stream", "response_format"}
                     and body["model"] == "gpt-6-luna" and body["temperature"] == 0.0
                     and body["stream"] is False and body["response_format"] == {"type": "json_object"}
                     and len(messages) == 2 and messages[0] == {"role": "system", "content": recall._BATCH_SYSTEM}
                     and set(messages[1]) == {"role", "content"} and messages[1]["role"] == "user"
                     and set(content) == {"question", "candidates"} and content["question"] == QUESTION
                     and len(rows) == 1 and row["id"] in expected_rows
                     and row == expected_rows[row["id"]] and row["id"] not in seen
                     and len(canonical(body)) <= budget)
            if not valid:
                raise ValueError("invalid synthetic body")
            seen.add(row["id"])
            bodies.append(canonical(body))
        except (KeyError, IndexError, TypeError, ValueError):
            pytest.fail("unexpected non-synthetic batch payload; no request sent", pytrace=False)
    if seen != set(expected_rows):
        pytest.fail("batch candidates incomplete; no request sent", pytrace=False)

    confirmations, posts, metadata = [], [], []
    def confirm(actual, allow_all):
        if confirmations or actual != prepared.preview_json or allow_all is not False:
            return "n"
        confirmations.append(True)
        return "y"
    original_post = httpx.Client.post
    def guarded_post(http_client, target, *args, **kwargs):
        i = len(posts)
        if (i >= 2 or len(confirmations) != 1 or target != endpoint
                or kwargs.get("content") != bodies[i] or kwargs.get("follow_redirects") is not False):
            raise RuntimeError("synthetic batch endpoint or payload rejected")
        posts.append(True)
        return original_post(http_client, target, *args, **kwargs)
    monkeypatch.setattr(httpx.Client, "post", guarded_post)
    original_parse = client_module._parse_completion
    def observe_parse(data, *, fallback_model):
        response = original_parse(data, fallback_model=fallback_model)
        metadata.append((response.model if response.model == "gpt-6-luna" else "other",
                         response.finish_reason if response.finish_reason in {"stop", "length", "tool_calls"} else "other"))
        return response
    monkeypatch.setattr(client_module, "_parse_completion", observe_parse)
    before = (ledger._session_dir / LEDGER_FILENAME).read_bytes()
    result = recall.execute_batch_recall(ledger, prepared, LLMClient(load_config(), paths=ledger._session.paths), confirm,
                                         host_binding_check=lambda: ledger._session_dir == ledger._session.session_dir)
    unchanged = (ledger._session_dir / LEDGER_FILENAME).read_bytes() == before
    no_ack = all(row["viewed_at"] is None for row in ledger.list_cards())
    selected = {record.get("card_id") for record in result.records}
    if not (result.status == "candidates" and result.reason == "semantic_completeness_not_guaranteed"
            and selected == {project_id} and len(result.records) == 1 and len(confirmations) == 1
            and len(posts) == 2 and metadata == [("gpt-6-luna", "stop")] * 2 and unchanged and no_ack):
        pytest.fail(f"batch result: status={result.status}, reason={result.reason}, target_match={selected == {project_id}}, "
                    f"confirmations={len(confirmations)}, requests={len(posts)}, metadata={metadata!r}, "
                    f"ledger_unchanged={unchanged}, no_ack={no_ack}; response withheld", pytrace=False)
