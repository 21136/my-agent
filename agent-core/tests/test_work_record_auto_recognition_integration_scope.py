from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
for _path in (_CORE, _TESTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from work_records import WorkRecordStore
from test_server_auto_recognition_ws_chain_focused import harness


def _decision(action, kind, *, title=None, requirement=None, scope=None, item_id=None):
    return json.dumps({
        "action": action, "kind": kind, "title": title,
        "requirement": requirement, "scope": scope, "item_id": item_id,
    }, ensure_ascii=False)


def _provider_for(responses):
    class Provider:
        paths = None

        def __init__(self):
            self.calls = []
            self.responses = dict(responses)

        def chat(self, messages, **_kwargs):
            payload = json.loads(messages[1]["content"])
            self.calls.append((messages, payload))
            return self.responses[payload["text"]]

    return Provider()


def test_desktop_wire_enable_to_approved_independent_record_and_same_session_overview(tmp_path, monkeypatch):
    text = "准备一个独立的合成验收笔记"
    title = "独立合成验收笔记"
    requirement = "写入两个部分；验收为两部分均存在"
    provider = _provider_for({text: _decision(
        "record", "requirement", title=title, requirement=requirement,
        scope="independent", item_id=None,
    )})
    session, _repl, _held, _handler, wait, begin, message, finish = harness(
        tmp_path, monkeypatch, provider,
    )
    session.meta.project_id = "synthetic-project-alpha"
    store = WorkRecordStore(session)
    project_item = store.create_item(
        title="Alpha synthetic project item",
        scope={"kind": "project", "project_id": "synthetic-project-alpha"},
    )
    store.add_requirement(project_item["item_id"], "Alpha existing requirement", event_id=str(uuid.uuid4()), idempotency_key=f"synthetic:{uuid.uuid4()}")
    independent_item = store.create_item(
        title="Existing standalone synthetic item", scope={"kind": "independent"},
    )

    async def run():
        ws, task = await begin()
        request_id = await message(ws, text)
        confirm = await wait(ws, lambda event: event.get("type") == "confirm.request"
                             and event.get("confirm_kind") == "work_record_post_turn")
        assert confirm["session_id"] == session.conversation_id
        assert request_id in confirm["preview"]
        assert title in confirm["preview"] and requirement in confirm["preview"]
        assert '"kind": "independent"' in confirm["preview"]
        assert "not_verified" in confirm["preview"] and "not_stored" in confirm["preview"]

        # Approval travels with the Desktop confirm.response wire shape.
        await ws.incoming.put({"type": "confirm.response", "request_id": confirm["request_id"], "choice": "y"})
        saved_event = await wait(ws, lambda event: event.get("type") == "work-record.auto-recognition.result"
                                 and event.get("request_id") == request_id)
        assert saved_event["session_id"] == session.conversation_id
        assert saved_event["status"] == "recorded"
        await wait(ws, lambda event: event.get("type") == "work-record.post-turn.result"
                    and event.get("request_id") == request_id)

        items = store.list_items()
        assert len(items) == 3
        saved = next(item for item in items if item["title"] == title)
        assert saved["scope"] == {"kind": "independent", "object_ref": None}
        assert [row["text"] for row in saved["requirements"]] == [requirement]
        assert len(saved["runs"]) == 1
        run_record = saved["runs"][0]
        assert run_record["status"] == "unknown"
        assert run_record["verification"]["status"] == "not_verified"
        assert run_record["verification"]["host_execution"]["sequence"] > 0
        assert uuid.UUID(run_record["run_id"])

        # The actual 0x567 provider fake saw only the current text and sanitized
        # current-session item identity/title/scope-kind view.
        assert len(provider.calls) == 1
        outbound = provider.calls[0][1]
        assert outbound["text"] == text
        assert outbound["recognition_view"] == [
            {"item_id": project_item["item_id"], "title": project_item["title"], "scope": {"kind": "project"}},
            {"item_id": independent_item["item_id"], "title": independent_item["title"], "scope": {"kind": "independent"}},
        ]
        assert "synthetic-project-alpha" not in json.dumps(outbound, ensure_ascii=False)

        overview_request_id = str(uuid.uuid4())
        await ws.incoming.put({
            "type": "work-records.overview.query", "session_id": session.conversation_id,
            "request_id": overview_request_id, "page_size": 20, "detail_paging": True,
        })
        overview = await wait(ws, lambda event: event.get("type") == "work-records.overview.result"
                              and event.get("request_id") == overview_request_id)
        assert overview["session_id"] == session.conversation_id
        overview_rows = [item for group in overview["groups"] for item in group["items"]]
        projected = next(item for item in overview_rows if item["title"] == title)
        assert projected["requirements"][0]["text"] == requirement
        assert projected["current_results"][0]["status"] == "unknown"
        await finish(ws, task)

    import asyncio
    asyncio.run(run())


def _existing_project_item(store, project_id, title="Alpha prior synthetic task"):
    item = store.create_item(title=title, scope={"kind": "project", "project_id": project_id})
    requirement = store.add_requirement(
        item["item_id"], "Original requirement", event_id=str(uuid.uuid4()),
        idempotency_key=f"synthetic-original:{uuid.uuid4()}",
    )
    original_run_id, original_result_id = str(uuid.uuid4()), str(uuid.uuid4())
    store.begin_requirement_run(
        item["item_id"], requirement["requirement_id"], run_id=original_run_id,
        expected_current_run_id=None,
    )
    store.record_result_if_current_run(
        item["item_id"], requirement["requirement_id"], run_id=original_run_id,
        result_id=original_result_id, status="completed", summary="Original synthetic result",
        verification={"status": "not_verified"},
    )
    return item


def test_same_project_existing_item_candidate_appends_exact_confirmed_target_and_run(tmp_path, monkeypatch):
    session_project = "synthetic-project-alpha"
    existing_title = "Alpha prior synthetic task"
    message_text = "继续 Alpha 的既有事项"
    provider = _provider_for({message_text: _decision(
        "record", "requirement", title="Alpha continuation candidate",
        requirement="继续添加一个合成检查", scope="current_project", item_id="placeholder-id",
    )})
    session, _repl, held, _handler, wait, begin, message, finish = harness(
        tmp_path, monkeypatch, provider,
    )
    session.meta.project_id = session_project
    store = WorkRecordStore(session)
    existing = _existing_project_item(store, session_project, existing_title)
    before = store.get_item(existing["item_id"])
    provider.responses[message_text] = _decision(
        "record", "requirement", title="Alpha continuation candidate",
        requirement="继续添加一个合成检查", scope="current_project", item_id=existing["item_id"],
    )

    async def run():
        ws, task = await begin()
        request_id = await message(ws, message_text)
        confirm = await wait(ws, lambda event: event.get("type") == "confirm.request"
                             and event.get("confirm_subtype") == "work_record_auto_candidate")
        terminal = held["bridge"].lifecycle.snapshot()
        fields = confirm["work_record_candidate"]
        assert fields == {
            "operation": "append_requirement", "item_id": existing["item_id"],
            "title": existing_title, "requirement": "继续添加一个合成检查",
            "scope": {"kind": "project", "project_id": session_project},
            "session_id": session.conversation_id, "request_id": request_id,
            "run_id": str(uuid.UUID(terminal.run_id)), "sequence": terminal.sequence,
            "terminal_state": "settled", "finish_reason": terminal.finish_reason,
        }
        assert existing["item_id"] in confirm["preview"]
        assert existing_title in confirm["preview"]
        assert "继续添加一个合成检查" in confirm["preview"]
        assert "Original synthetic result" in json.dumps(before, ensure_ascii=False)

        await ws.incoming.put({"type": "confirm.response", "request_id": confirm["request_id"], "choice": "y"})
        done = await wait(ws, lambda event: event.get("type") == "confirm.done"
                          and event.get("request_id") == confirm["request_id"])
        assert done["choice"] == "y" and done["work_record_candidate"] == fields
        result = await wait(ws, lambda event: event.get("type") == "work-record.auto-recognition.result"
                            and event.get("request_id") == request_id)
        assert result["status"] == "recorded"

        saved = store.get_item(existing["item_id"])
        assert saved["title"] == before["title"] and saved["scope"] == before["scope"]
        assert saved["requirements"][0] == before["requirements"][0]
        assert saved["requirements"][1]["text"] == fields["requirement"]
        assert len(saved["requirements"]) == len(before["requirements"]) + 1
        assert saved["runs"][0] == before["runs"][0]
        assert len(saved["runs"]) == len(before["runs"]) + 1
        appended_run = saved["runs"][-1]
        assert appended_run["requirement_id"] == saved["requirements"][1]["requirement_id"]
        assert appended_run["run_id"] == fields["run_id"]
        assert appended_run["status"] == "unknown"
        assert appended_run["verification"]["status"] == "not_verified"
        assert appended_run["verification"]["host_execution"]["sequence"] == fields["sequence"]
        assert saved["history"][:len(before["history"])] == before["history"]
        assert len(store.list_items()) == 1
        await finish(ws, task)

    import asyncio
    asyncio.run(run())


def test_same_project_append_rejection_preserves_original_requirements_and_result(tmp_path, monkeypatch):
    session_project = "synthetic-project-alpha"
    message_text = "继续 Alpha 的既有事项"
    provider = _provider_for({message_text: _decision(
        "record", "requirement", title="ignored model title", requirement="被拒绝的合成追加",
        scope="current_project", item_id="placeholder-id",
    )})
    session, _repl, _held, _handler, wait, begin, message, finish = harness(
        tmp_path, monkeypatch, provider,
    )
    session.meta.project_id = session_project
    store = WorkRecordStore(session)
    existing = _existing_project_item(store, session_project)
    before = store.get_item(existing["item_id"])
    provider.responses[message_text] = _decision(
        "record", "requirement", title="ignored model title", requirement="被拒绝的合成追加",
        scope="current_project", item_id=existing["item_id"],
    )

    async def run():
        ws, task = await begin()
        request_id = await message(ws, message_text)
        confirm = await wait(ws, lambda event: event.get("type") == "confirm.request"
                             and event.get("confirm_subtype") == "work_record_auto_candidate")
        assert confirm["work_record_candidate"]["item_id"] == existing["item_id"]
        await ws.incoming.put({"type": "confirm.response", "request_id": confirm["request_id"], "choice": "n"})
        done = await wait(ws, lambda event: event.get("type") == "confirm.done"
                          and event.get("request_id") == confirm["request_id"])
        assert done["choice"] == "n"
        result = await wait(ws, lambda event: event.get("type") == "work-record.auto-recognition.result"
                            and event.get("request_id") == request_id)
        assert result["status"] == "storage_not_authorized"
        assert store.get_item(existing["item_id"]) == before
        assert len(store.list_items()) == 1
        await finish(ws, task)

    import asyncio
    asyncio.run(run())


def test_foreign_or_wrong_scope_model_targets_are_rejected_without_confirmation(tmp_path, monkeypatch):
    session_project = "synthetic-project-alpha"
    text_foreign, text_wrong_scope = "foreign target", "wrong scope target"
    provider = _provider_for({
        text_foreign: _decision("record", "requirement", title="x", requirement="x",
                                scope="current_project", item_id="placeholder-foreign"),
        text_wrong_scope: _decision("record", "requirement", title="x", requirement="x",
                                    scope="current_project", item_id="placeholder-independent"),
    })
    session, _repl, _held, _handler, wait, begin, message, finish = harness(
        tmp_path, monkeypatch, provider,
    )
    session.meta.project_id = session_project
    store = WorkRecordStore(session)
    project_item = _existing_project_item(store, session_project, "Alpha protected item")
    independent_item = store.create_item(title="Standalone protected item", scope={"kind": "independent"})
    before = store.list_items()
    foreign_id = str(uuid.uuid4())
    provider.responses[text_foreign] = _decision(
        "record", "requirement", title="x", requirement="x",
        scope="current_project", item_id=foreign_id,
    )
    provider.responses[text_wrong_scope] = _decision(
        "record", "requirement", title="x", requirement="x",
        scope="current_project", item_id=independent_item["item_id"],
    )

    async def run():
        ws, task = await begin()
        for text in (text_foreign, text_wrong_scope):
            request_id = await message(ws, text)
            outcome = await wait(ws, lambda event: event.get("type") == "work-record.auto-recognition.result"
                                 and event.get("request_id") == request_id)
            assert outcome["status"] == "not_recorded"
        assert not [event for event in ws.events if event.get("type") == "confirm.request"
                    and event.get("confirm_subtype") == "work_record_auto_candidate"]
        assert store.list_items() == before
        assert store.get_item(project_item["item_id"]) == before[0]
        await finish(ws, task)

    import asyncio
    asyncio.run(run())

def test_other_project_intent_is_not_rebound_and_chat_none_does_not_write(tmp_path, monkeypatch):
    project_alpha = "synthetic-project-alpha"
    beta_text = "为 Beta 项目准备一份合成检查表"
    chat_text = "你好，闲聊一下"
    provider = _provider_for({
        beta_text: _decision("uncertain", "ambiguous"),
        chat_text: _decision("none", "qa"),
    })
    session, _repl, _held, _handler, wait, begin, message, finish = harness(
        tmp_path, monkeypatch, provider,
    )
    session.meta.project_id = project_alpha
    store = WorkRecordStore(session)
    alpha_item = store.create_item(
        title="Alpha only synthetic item", scope={"kind": "project", "project_id": project_alpha},
    )
    store.add_requirement(alpha_item["item_id"], "Alpha only requirement", event_id=str(uuid.uuid4()), idempotency_key=f"synthetic:{uuid.uuid4()}")
    original = store.list_items()

    async def run():
        ws, task = await begin()
        beta_request_id = await message(ws, beta_text)
        beta = await wait(ws, lambda event: event.get("type") == "work-record.auto-recognition.result"
                          and event.get("request_id") == beta_request_id)
        assert beta["status"] == "not_recorded"

        turn_end_count = sum(event.get("type") == "turn.end" for event in ws.events)
        chat_request_id = await message(ws, chat_text)
        chat_result = await wait(ws, lambda event: event.get("type") == "work-record.auto-recognition.result"
                                 and event.get("request_id") == chat_request_id)
        assert chat_result["status"] == "not_recorded"
        await wait(ws, lambda event: sum(row.get("type") == "turn.end" for row in ws.events) > turn_end_count)
        assert store.list_items() == original
        assert not [event for event in ws.events if event.get("type") == "confirm.request"
                    and event.get("confirm_kind") == "work_record_post_turn"]
        assert len(provider.calls) == 2
        for _messages, outbound in provider.calls:
            assert outbound["recognition_view"] == [{
                "item_id": alpha_item["item_id"], "title": alpha_item["title"],
                "scope": {"kind": "project"},
            }]
            assert "synthetic-project-alpha" not in json.dumps(outbound, ensure_ascii=False)
        await finish(ws, task)

    import asyncio
    asyncio.run(run())

