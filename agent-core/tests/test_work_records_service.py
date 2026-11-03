from __future__ import annotations

import sys
from pathlib import Path
import uuid

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_records_service import WorkRecordCaptureService, is_explicit_record_request


class FakeProvider:
    def __init__(self):
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return '{"action":"record","kind":"requirement","title":"合成原文：整理事项","requirement":"合成原文：整理事项","scope":"independent","item_id":null}'


def make_session(tmp_path):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    session_dir = paths.data / "sessions" / "synthetic-session"
    session_dir.mkdir(parents=True)
    paths.workspace.mkdir()
    paths.evolve.mkdir()
    session = Session("synthetic-session", session_dir, "", SessionMeta(), [], paths)
    return session


def test_capture_requires_receipted_external_and_local_confirmations(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    provider = FakeProvider()
    prompts = []
    receipts = iter([str(uuid.uuid4()), str(uuid.uuid4())])
    service = WorkRecordCaptureService(
        session=session,
        client=provider,
        confirm=lambda preview: (prompts.append(preview) or "yes", next(receipts)),
    )
    monkeypatch.setattr("work_records_service.get_registry", lambda *_a, **_k: type("R", (), {"get": lambda _self, _id: type("M", (), {"id":"0x567-flash", "vendor":"0x567", "provider_model":"gpt-6-luna", "max_input_tokens":96000})()})())

    result = service.capture("合成原文：整理事项", request_id="11111111-1111-4111-8111-111111111111")

    assert result.status == "recorded"
    stored = WorkRecordStore(session).list_items()[0]["requirements"][0]["requirement_id"]
    assert result.requirement_id == stored
    assert provider.calls and len(prompts) == 2
    assert "合成原文：整理事项" in prompts[0]
    assert "合成原文：整理事项" in prompts[1]
    assert "原文内容因此可能出现在保存字段中" in prompts[1]
    assert "原文不会落盘" not in prompts[1]
    assert str(session.session_dir / "work-records.v1.json") in prompts[1]
    assert len(WorkRecordStore(session).list_items()) == 1


@pytest.mark.parametrize("first_choice", ["no", "cancelled", "timeout"])
def test_outbound_rejection_or_cancel_never_calls_provider_or_writes(tmp_path, first_choice, monkeypatch):
    session = make_session(tmp_path)
    provider = FakeProvider()
    store = WorkRecordStore(session)
    prompts = []
    service = WorkRecordCaptureService(
        session=session,
        client=provider,
        confirm=lambda preview: (prompts.append(preview) or first_choice, str(uuid.uuid4())),
    )
    monkeypatch.setattr("work_records_service.get_registry", lambda *_a, **_k: pytest.fail("must not inspect model before consent"))

    result = service.capture("synthetic private text", request_id="22222222-2222-4222-8222-222222222222")

    assert result.status == "not_authorized"
    assert result.requirement_id is None
    assert provider.calls == []
    assert store.list_items() == []
    assert len(prompts) == 1


def test_storage_rejection_keeps_authorized_provider_call_local_only(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    provider = FakeProvider()
    store = WorkRecordStore(session)
    answers = iter([("yes", str(uuid.uuid4())), ("no", str(uuid.uuid4()))])
    service = WorkRecordCaptureService(session=session, client=provider, confirm=lambda _preview: next(answers))
    monkeypatch.setattr("work_records_service.get_registry", lambda *_a, **_k: type("R", (), {"get": lambda _self, _id: type("M", (), {"id":"0x567-flash", "vendor":"0x567", "provider_model":"gpt-6-luna", "max_input_tokens":96000})()})())

    result = service.capture("synthetic private text", request_id="55555555-5555-4555-8555-000000000091")

    assert result.status == "storage_not_authorized"
    assert result.requirement_id is None
    assert len(provider.calls) == 1
    assert store.list_items() == []





def test_existing_item_candidate_is_not_mutated_by_safe_first_entry(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    provider = FakeProvider()
    existing_store = WorkRecordStore(session)
    existing = existing_store.create_item(title="existing synthetic", scope={"kind": "independent"})
    provider.chat = lambda *_args, **_kwargs: (
        '{"action":"record","kind":"requirement","title":"continuation candidate",'
        '"requirement":"synthetic requirement","scope":"independent","item_id":"'
        + existing["item_id"] + '"}'
    )
    prompts = []
    service = WorkRecordCaptureService(
        session=session, client=provider,
        confirm=lambda preview: (prompts.append(preview) or "yes", str(uuid.uuid4())),
    )
    monkeypatch.setattr("work_records_service.get_registry", lambda *_a, **_k: type("R", (), {"get": lambda _self, _id: type("M", (), {"id":"0x567-flash", "vendor":"0x567", "provider_model":"gpt-6-luna", "max_input_tokens":96000})()})())

    result = service.capture("synthetic continuation", request_id="55555555-5555-4555-8555-000000000118")

    assert result.status == "recorded"
    assert len(prompts) == 2
    assert [req["text"] for req in existing_store.get_item(existing["item_id"])["requirements"]] == ["synthetic requirement"]


def test_outbound_confirmation_rechecks_snapshot_before_provider(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    provider = FakeProvider()
    store = WorkRecordStore(session)
    prompts = []
    def confirm(preview):
        prompts.append(preview)
        if len(prompts) == 1:
            store.create_item(title="concurrent synthetic", scope={"kind": "independent"})
        return "yes", str(uuid.uuid4())
    service = WorkRecordCaptureService(session=session, client=provider, confirm=confirm)
    monkeypatch.setattr("work_records_service.get_registry", lambda *_a, **_k: pytest.fail("stale snapshot must stop before provider config"))

    result = service.capture("synthetic text", request_id="55555555-5555-4555-8555-000000000138")

    assert result.status == "stale_context"
    assert provider.calls == []
    assert len(prompts) == 1


def test_session_binding_change_after_outbound_confirmation_fails_closed(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    session.meta.project_id = "project-before"
    provider = FakeProvider()
    prompts = []
    def confirm(preview):
        prompts.append(preview)
        session.meta.project_id = "project-after"
        return "yes", str(uuid.uuid4())
    service = WorkRecordCaptureService(session=session, client=provider, confirm=confirm)
    monkeypatch.setattr("work_records_service.get_registry", lambda *_a, **_k: pytest.fail("changed binding must stop before provider config"))

    result = service.capture("synthetic text", request_id="55555555-5555-4555-8555-000000000157")

    assert result.status == "stale_context"
    assert provider.calls == []
    assert len(prompts) == 1


def test_provider_side_snapshot_change_prevents_candidate_confirmation_and_write(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    provider = FakeProvider()
    store = WorkRecordStore(session)
    original_chat = provider.chat
    def chat(messages, **kwargs):
        response = original_chat(messages, **kwargs)
        store.create_item(title="concurrent synthetic", scope={"kind": "independent"})
        return response
    provider.chat = chat
    prompts = []
    service = WorkRecordCaptureService(
        session=session, client=provider,
        confirm=lambda preview: (prompts.append(preview) or "yes", str(uuid.uuid4())),
    )
    monkeypatch.setattr("work_records_service.get_registry", lambda *_a, **_k: type("R", (), {"get": lambda _self, _id: type("M", (), {"id":"0x567-flash", "vendor":"0x567", "provider_model":"gpt-6-luna", "max_input_tokens":96000})()})())

    result = service.capture("synthetic text", request_id="55555555-5555-4555-8555-000000000181")

    assert result.status == "stale_context"
    assert len(provider.calls) == 1
    assert len(prompts) == 1
    assert [item["title"] for item in store.list_items()] == ["concurrent synthetic"]


def test_second_confirmation_snapshot_change_prevents_service_write(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    provider = FakeProvider()
    store = WorkRecordStore(session)
    prompts = []
    def confirm(preview):
        prompts.append(preview)
        if len(prompts) == 2:
            store.create_item(title="concurrent synthetic", scope={"kind": "independent"})
        return "yes", str(uuid.uuid4())
    service = WorkRecordCaptureService(session=session, client=provider, confirm=confirm)
    monkeypatch.setattr("work_records_service.get_registry", lambda *_a, **_k: type("R", (), {"get": lambda _self, _id: type("M", (), {"id":"0x567-flash", "vendor":"0x567", "provider_model":"gpt-6-luna", "max_input_tokens":96000})()})())

    result = service.capture("synthetic text", request_id="55555555-5555-4555-8555-000000000202")

    assert result.status == "stale_context"
    assert len(prompts) == 2
    assert [item["title"] for item in store.list_items()] == ["concurrent synthetic"]


def test_atomic_store_write_failure_does_not_leave_empty_record(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    monkeypatch.setattr(store, "_publish", lambda *_a, **_k: (_ for _ in ()).throw(OSError("synthetic publish failure")))

    with pytest.raises(Exception):
        store.create_item_with_requirement(
            title="synthetic candidate", scope={"kind": "independent"},
            requirement="synthetic requirement", event_id="synthetic-event",
        )

    assert not (session.session_dir / "work-records.v1.json").exists()



def test_atomic_persistence_error_is_reported_as_uncertain_not_no_write(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    provider = FakeProvider()
    answers = iter([("yes", str(uuid.uuid4())), ("yes", str(uuid.uuid4()))])
    service = WorkRecordCaptureService(session=session, client=provider, confirm=lambda _preview: next(answers))
    monkeypatch.setattr("work_records_service.get_registry", lambda *_a, **_k: type("R", (), {"get": lambda _self, _id: type("M", (), {"id":"0x567-flash", "vendor":"0x567", "provider_model":"gpt-6-luna", "max_input_tokens":96000})()})())
    monkeypatch.setattr(service._store, "_publish", lambda *_a, **_k: (_ for _ in ()).throw(OSError("synthetic publish failure")))

    result = service.capture("合成原文：整理事项", request_id="55555555-5555-4555-8555-000000000232")

    assert result.status == "storage_uncertain"
    assert result.requirement_id is None
    assert "不要据此认定未写入" in result.message
    assert not (session.session_dir / "work-records.v1.json").exists()


def test_authorized_capture_appends_to_existing_item_after_disclosure(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    existing = store.create_item(title="existing synthetic", scope={"kind": "independent"})
    provider = FakeProvider()
    provider.chat = lambda *_args, **_kwargs: (
        '{"action":"record","kind":"requirement","title":"existing synthetic",'
        '"requirement":"append synthetic requirement","scope":"independent","item_id":"'
        + existing["item_id"] + '"}'
    )
    prompts = []
    service = WorkRecordCaptureService(
        session=session,
        client=provider,
        confirm=lambda preview: (prompts.append(preview) or "yes", str(uuid.uuid4())),
    )
    monkeypatch.setattr("work_records_service.get_registry", lambda *_a, **_k: type("R", (), {"get": lambda _self, _id: type("M", (), {"id":"0x567-flash", "vendor":"0x567", "provider_model":"gpt-6-luna", "max_input_tokens":96000})()})())

    result = service.capture("synthetic follow-up requirement", request_id="33333333-3333-4333-8333-333333333333")

    assert result.status == "recorded"
    assert result.item_id == existing["item_id"]
    assert isinstance(result.requirement_id, str) and result.requirement_id
    assert len(prompts) == 2
    assert "existing synthetic" in prompts[1]
    assert "append synthetic requirement" in prompts[1]
    item = store.get_item(existing["item_id"])
    assert [requirement["text"] for requirement in item["requirements"]] == ["append synthetic requirement"]
    assert item["requirements"][0]["requirement_id"] == result.requirement_id


def test_record_request_prefilter_only_routes_clear_one_shot_capture_actions():
    assert is_explicit_record_request("把这个要求记到工作记录里")
    assert is_explicit_record_request("请记录一下：下周交付合成报告")
    assert not is_explicit_record_request("请帮我整理一下这段话")
    assert not is_explicit_record_request("之前记过的项目要求是什么？")



def test_capture_requires_stable_request_id_before_any_consent_or_write(tmp_path):
    session = make_session(tmp_path)
    provider = FakeProvider()
    prompts = []
    service = WorkRecordCaptureService(session=session, client=provider,
        confirm=lambda preview: (prompts.append(preview) or "yes", str(uuid.uuid4())))

    result = service.capture("synthetic private text")

    assert result.status == "invalid_request"
    assert prompts == []
    assert provider.calls == []
    assert WorkRecordStore(session).list_items() == []


def test_same_request_replay_is_idempotent_and_changed_text_conflicts(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    provider = FakeProvider()
    service = WorkRecordCaptureService(session=session, client=provider,
        confirm=lambda _preview: ("yes", str(uuid.uuid4())))
    monkeypatch.setattr("work_records_service.get_registry", lambda *_a, **_k: type("R", (), {"get": lambda _self, _id: type("M", (), {"id":"0x567-flash", "vendor":"0x567", "provider_model":"gpt-6-luna", "max_input_tokens":96000})()})())
    request_id = "44444444-4444-4444-8444-444444444444"

    first = service.capture("synthetic stable text", request_id=request_id)
    replay = service.capture("synthetic stable text", request_id=request_id)
    changed = service.capture("different synthetic text", request_id=request_id)

    assert first.status == "recorded"
    assert replay.status == "already_recorded"
    assert first.requirement_id is not None
    assert replay.requirement_id == first.requirement_id
    assert changed.status == "request_conflict"
    assert "synthetic stable text" not in changed.message
    assert len(provider.calls) == 1
    assert changed.item_id is None and changed.requirement_id is None
    assert len(WorkRecordStore(session).list_items()) == 1
    persisted = WorkRecordStore(session).get_item(first.item_id)["requirements"]
    assert len(persisted) == 1
    assert persisted[0]["requirement_id"] == first.requirement_id



def test_same_request_replay_appends_only_once(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    existing = store.create_item(title="synthetic existing", scope={"kind": "independent"})
    provider = FakeProvider()
    provider.chat = lambda *_args, **_kwargs: (
        '{"action":"record","kind":"requirement","title":"synthetic existing",'
        '"requirement":"synthetic append","scope":"independent","item_id":"'
        + existing["item_id"] + '"}'
    )
    service = WorkRecordCaptureService(session=session, client=provider,
        confirm=lambda _preview: ("yes", str(uuid.uuid4())))
    monkeypatch.setattr("work_records_service.get_registry", lambda *_a, **_k: type("R", (), {"get": lambda _self, _id: type("M", (), {"id":"0x567-flash", "vendor":"0x567", "provider_model":"gpt-6-luna", "max_input_tokens":96000})()})())
    request_id = "66666666-6666-4666-8666-666666666666"

    first = service.capture("synthetic append", request_id=request_id)
    replay = service.capture("synthetic append", request_id=request_id)

    assert first.status == "recorded"
    assert replay.status == "already_recorded"
    assert first.requirement_id is not None
    assert replay.requirement_id == first.requirement_id
    assert len(store.get_item(existing["item_id"])["requirements"]) == 1

def test_storage_confirmation_preview_is_complete_and_uses_real_line_breaks(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    prompts = []
    service = WorkRecordCaptureService(
        session=session, client=FakeProvider(),
        confirm=lambda preview: (prompts.append(preview) or "yes", str(uuid.uuid4())),
    )
    monkeypatch.setattr("work_records_service.get_registry", lambda *_a, **_k: type("R", (), {
        "get": lambda _self, _id: type("M", (), {
            "id": "0x567-flash", "vendor": "0x567",
            "provider_model": "gpt-6-luna", "max_input_tokens": 96000,
        })()
    })())

    result = service.capture("合成原文：整理事项", request_id="77777777-7777-4777-8777-777777777777")

    assert result.status == "recorded"
    expected = (
        "工作记录本地落盘确认\n"
        "操作：新建事项并写入首条要求\n"
        "候选标题：合成原文：整理事项\n"
        "候选要求：合成原文：整理事项\n"
        "范围：{\"kind\": \"independent\"}\n"
        f"批准后保存上述候选字段到当前会话文件：{session.session_dir / 'work-records.v1.json'}\n"
        "重要：模型生成的标题或要求可能复制、改写或部分包含本次原文；批准后原文内容因此可能出现在保存字段中。"
        "以下候选内容即为拟保存内容；原文不会作为独立字段保存。\n"
        "这是本次单项写入确认；拒绝/取消/超时将不执行本次记录写入。"
    )
    assert len(prompts) == 2
    assert prompts[1] == expected
    assert "\\n" not in prompts[1]

def test_existing_item_append_rejects_concurrent_change_after_final_snapshot_check(tmp_path, monkeypatch):
    import threading

    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    existing = store.create_item(title="existing synthetic", scope={"kind": "independent"})
    provider = FakeProvider()
    provider.chat = lambda *_args, **_kwargs: (
        '{"action":"record","kind":"requirement","title":"existing synthetic",'
        '"requirement":"append candidate","scope":"independent","item_id":"'
        + existing["item_id"] + '"}'
    )
    service = WorkRecordCaptureService(
        session=session, client=provider,
        confirm=lambda _preview: ("yes", str(uuid.uuid4())),
    )
    monkeypatch.setattr("work_records_service.get_registry", lambda *_a, **_k: type("R", (), {
        "get": lambda _self, _id: type("M", (), {
            "id": "0x567-flash", "vendor": "0x567",
            "provider_model": "gpt-6-luna", "max_input_tokens": 96000,
        })()
    })())

    checked = threading.Event()
    changed = threading.Event()
    original_check = service._snapshot_unchanged
    calls = {"count": 0}

    def final_check_then_compete(bound_session, expected):
        unchanged = original_check(bound_session, expected)
        calls["count"] += 1
        if calls["count"] == 4:
            checked.set()
            assert changed.wait(3.0), "concurrent writer did not finish"
        return unchanged

    service._snapshot_unchanged = final_check_then_compete

    def concurrent_writer():
        assert checked.wait(3.0), "capture did not reach its final pre-write snapshot check"
        store.create_item(title="concurrent synthetic", scope={"kind": "independent"})
        changed.set()

    writer = threading.Thread(target=concurrent_writer, daemon=True)
    writer.start()
    result = service.capture("synthetic append candidate", request_id="88888888-8888-4888-8888-888888888888")
    writer.join(3.0)

    assert not writer.is_alive()
    assert result.status == "stale_context"
    assert [row["text"] for row in store.get_item(existing["item_id"])["requirements"]] == []
    assert [row["title"] for row in store.list_items()] == ["existing synthetic", "concurrent synthetic"]

@pytest.mark.parametrize("target_kind", ["append", "create"])
def test_capture_missing_persisted_requirement_identity_is_uncertain_and_exposes_none(
    tmp_path, monkeypatch, target_kind
):
    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    existing = None
    if target_kind == "append":
        existing = store.create_item(title="existing synthetic", scope={"kind": "independent"})
    provider = FakeProvider()
    if existing is not None:
        provider.chat = lambda *_args, **_kwargs: (
            '{"action":"record","kind":"requirement","title":"existing synthetic",'
            '"requirement":"append missing identity","scope":"independent","item_id":"'
            + existing["item_id"] + '"}'
        )
    service = WorkRecordCaptureService(
        session=session, client=provider,
        confirm=lambda _preview: ("yes", str(uuid.uuid4())),
    )
    monkeypatch.setattr("work_records_service.get_registry", lambda *_a, **_k: type("R", (), {
        "get": lambda _self, _id: type("M", (), {
            "id": "0x567-flash", "vendor": "0x567",
            "provider_model": "gpt-6-luna", "max_input_tokens": 96000,
        })()
    })())
    if target_kind == "append":
        monkeypatch.setattr(service._store, "add_requirement", lambda *_a, **_k: {})
    else:
        monkeypatch.setattr(service._store, "create_item_with_requirement", lambda **_k: {"item_id": "synthetic-item"})

    result = service.capture("synthetic requirement", request_id="99999999-9999-4999-8999-999999999999")

    assert result.status == "storage_uncertain"
    assert result.requirement_id is None

def test_track_run_preview_is_exact_local_only_disclosure_and_rejection_writes_nothing(tmp_path, monkeypatch):
    prompts_default = []
    prompts_tracked = []
    registry = type("R", (), {
        "get": lambda _self, _id: type("M", (), {
            "id": "0x567-flash", "vendor": "0x567",
            "provider_model": "gpt-6-luna", "max_input_tokens": 96000,
        })()
    })()
    monkeypatch.setattr("work_records_service.get_registry", lambda *_a, **_k: registry)

    default_session = make_session(tmp_path / "default")
    default_service = WorkRecordCaptureService(
        session=default_session, client=FakeProvider(),
        confirm=lambda preview: (prompts_default.append(preview) or ("yes" if len(prompts_default) == 1 else "no"), str(uuid.uuid4())),
    )
    default_result = default_service.capture(
        "synthetic tracked request", request_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    )

    tracked_session = make_session(tmp_path / "tracked")
    tracked_store = WorkRecordStore(tracked_session)
    tracked_service = WorkRecordCaptureService(
        session=tracked_session, client=FakeProvider(),
        confirm=lambda preview: (prompts_tracked.append(preview) or ("yes" if len(prompts_tracked) == 1 else "no"), str(uuid.uuid4())),
    )
    tracked_result = tracked_service.capture(
        "synthetic tracked request", request_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        track_run=True,
    )

    assert default_result.status == tracked_result.status == "storage_not_authorized"
    assert default_result.requirement_id is tracked_result.requirement_id is None
    assert len(prompts_default) == len(prompts_tracked) == 2
    assert prompts_tracked[0] == prompts_default[0], "track_run must not alter the outbound preview"
    expected_storage_preview = (
        "工作记录本地落盘确认\n"
        "操作：新建事项并写入首条要求\n"
        "候选标题：合成原文：整理事项\n"
        "候选要求：合成原文：整理事项\n"
        "范围：{\"kind\": \"independent\"}\n"
        f"批准后保存上述候选字段到当前会话文件：{tracked_session.session_dir / 'work-records.v1.json'}\n"
        "重要：模型生成的标题或要求可能复制、改写或部分包含本次原文；批准后原文内容因此可能出现在保存字段中。"
        "以下候选内容即为拟保存内容；原文不会作为独立字段保存。\n"
        "这是本次单项写入确认；拒绝/取消/超时将不执行本次记录写入。\n"
        "后续会另外写入与本条消息绑定的 run 身份、生命周期事件序号，以及可能存在的受限宿主回合结束类型"
        "（completed、cancelled、timeout、error、paused 或 unknown）；这些仅是宿主生命周期信息，"
        "不代表要求已完成或通过验证，要求结果仍为未知、未经验证。执行可能中断，且不会自动重做。\n"
        "还可能保存此 run 内已实际执行工具的宿主调用 ID、工具类型及返回 ok/非 ok；"
        "按工具返回后的记录顺序，最多保存前 20 条，并记录遗漏条目数；"
        "异常、后台等结果不明的调用也可能计入遗漏数。工具回执不会保存工具参数、路径、输出或模型文本，"
        "也不代表要求已完成或通过验证。"
    )
    assert prompts_tracked[1] == expected_storage_preview
    default_expected = expected_storage_preview.split("\n后续会另外写入", 1)[0].replace(
        str(tracked_session.session_dir), str(default_session.session_dir)
    )
    assert prompts_default[1] == default_expected
    assert tracked_store.list_items() == []
    assert WorkRecordStore(default_session).list_items() == []
    assert not (tracked_session.session_dir / "work-records.v1.json").exists()
    assert not (default_session.session_dir / "work-records.v1.json").exists()



@pytest.mark.parametrize("target_kind", ["create", "append"])
def test_competing_capture_instances_distinguish_commit_from_replay(tmp_path, monkeypatch, target_kind):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    existing = store.create_item(title="synthetic existing", scope={"kind": "independent"}) if target_kind == "append" else None
    registry = type("R", (), {"get": lambda _self, _id: type("M", (), {
        "id": "0x567-flash", "vendor": "0x567", "provider_model": "gpt-6-luna", "max_input_tokens": 96000,
    })()})()
    monkeypatch.setattr("work_records_service.get_registry", lambda *_a, **_k: registry)
    barrier = threading.Barrier(2, timeout=5)
    services = []
    for _ in range(2):
        provider = FakeProvider()
        if existing is not None:
            provider.chat = lambda *_a, **_k: (
                '{"action":"record","kind":"requirement","title":"synthetic existing",'
                '"requirement":"synthetic append","scope":"independent","item_id":"'
                + existing["item_id"] + '"}'
            )
        service = WorkRecordCaptureService(session=session, client=provider,
            confirm=lambda _preview: ("yes", str(uuid.uuid4())))
        original = service._snapshot_unchanged
        counter = [0]
        def check(bound, snapshot, original=original, counter=counter):
            result = original(bound, snapshot)
            counter[0] += 1
            if counter[0] == 4:
                barrier.wait()
            return result
        service._snapshot_unchanged = check
        services.append(service)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(s.capture, "synthetic race", request_id="aaaaaaaa-aaaa-4aaa-8aaa-111111111111") for s in services]
        results = [future.result(timeout=10) for future in futures]
    assert sorted(r.status for r in results) == ["already_recorded", "recorded"]
    assert len({(r.item_id, r.requirement_id) for r in results}) == 1
    replay = next(r for r in results if r.status == "already_recorded")
    assert "未再次写入" in replay.message
    assert len(store.list_items()) == 1
    assert len(store.list_items()[0]["requirements"]) == 1
