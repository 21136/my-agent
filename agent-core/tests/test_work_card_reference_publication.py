from __future__ import annotations

import sys
import uuid
from pathlib import Path
import pytest

_CORE = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_CORE))
sys.path.insert(0, str(_TESTS))

import server
from test_work_card_presentations import setup_case
from test_work_card_recall_agent import install_client, setup_case as setup_recall_case
from work_card_recall import RecallResolution


def test_confirmed_unique_agent_match_supplies_only_selected_identity(tmp_path, monkeypatch):
    agent, session, _store, ledger, selected = setup_recall_case(tmp_path, monkeypatch, count=2)
    records = ledger.list_cards()
    unselected = next(row["card_id"] for row in records if row["card_id"] != selected)
    install_client(monkeypatch, [selected], [])
    received = []
    run_id = str(uuid.uuid4())
    def prepare(bound_session, resolution):
        received.append((bound_session, resolution.status, tuple(row["card_id"] for row in resolution.records)))
        return server._prepare_confirmed_work_card_reference(
            bound_session, resolution, run_id, enabled=True,
        )
    agent.work_card_reference_intent_fn = prepare

    result = agent.run_turn("找回之前关于合成需求的工作卡", spawn_explore=False)

    assert result.finish_reason == "work_card_recall"
    assert type(result.work_card_reference_intent) is server.WorkCardReferenceIntent
    assert result.work_card_reference_intent.card_id == selected
    assert result.work_card_reference_intent.run_id == str(uuid.UUID(run_id))
    assert received == [(session, "match", (selected,))]
    assert unselected not in received[0][2]
    assert all(row["viewed_at"] is None for row in ledger.list_cards())


def test_server_reference_boundary_defaults_to_internal_intent_without_persisting(tmp_path):
    session, _store, ledger, card_id, _other = setup_case(tmp_path)
    resolution = RecallResolution("match", (ledger.get_card(card_id),))

    result = server._prepare_confirmed_work_card_reference(session, resolution, str(uuid.uuid4()))

    assert type(result) is server.WorkCardReferenceIntent
    assert not (session.session_dir / "work-card-presentations.v1.json").exists()
    assert all(message.get("role") != "work_card_presentation" for message in session.messages)


def test_enabled_boundary_returns_stable_internal_intent_without_persisting(tmp_path):
    session, _store, ledger, card_id, _other = setup_case(tmp_path)
    resolution = RecallResolution("match", (ledger.get_card(card_id),))
    run_id = str(uuid.uuid4())

    first = server._prepare_confirmed_work_card_reference(session, resolution, run_id, enabled=True)
    retry = server._prepare_confirmed_work_card_reference(session, resolution, run_id, enabled=True)

    assert type(first) is server.WorkCardReferenceIntent
    assert first == retry
    assert first.session_id == session.conversation_id and first.card_id == card_id
    assert first.presentation_id
    assert not (session.session_dir / "work-card-presentations.v1.json").exists()
    assert all(message.get("role") != "work_card_presentation" for message in session.messages)
    assert ledger.get_card(card_id)["viewed_at"] is None


def test_reference_boundary_rejects_foreign_session_and_non_match_resolution(tmp_path):
    session, _store, _ledger, card_id, _other = setup_case(tmp_path / "first")
    _foreign_session, _foreign_store, foreign_ledger, foreign_card, _ = setup_case(tmp_path / "foreign")
    run_id = str(uuid.uuid4())
    foreign = RecallResolution("match", (foreign_ledger.get_card(foreign_card),))
    non_match = RecallResolution("candidates", ({"card_id": card_id},))

    assert server._prepare_confirmed_work_card_reference(session, foreign, run_id, enabled=True) == "rejected"
    assert server._prepare_confirmed_work_card_reference(session, non_match, run_id, enabled=True) == "rejected"
    assert all(message.get("role") != "work_card_presentation" for message in session.messages)




def test_agent_run_publishes_public_card_over_bridge_and_restores_reference(tmp_path, monkeypatch):
    import asyncio
    from main import ConversationRepl
    from session import Session, session_history_event
    from work_card_ledger import WorkCardLedger
    from work_card_presentations import WorkCardPresentations
    from work_records import WorkRecordStore

    agent, session, store, ledger, selected = setup_recall_case(tmp_path, monkeypatch)
    install_client(monkeypatch, [selected], [])
    monkeypatch.setattr(server, "_WORK_CARD_REFERENCE_PUBLICATION_ENABLED", True)
    events = []
    bridge = server.WsBridge(emit=events.append, paths=session.paths)
    repl = ConversationRepl(
        paths=session.paths, session=session, agent=agent,
        input_fn=lambda _prompt: "n", output_fn=lambda _text: None,
        assistant_output_fn=bridge.emit_assistant,
    )
    server._bind_work_card_reference_intent(repl, bridge)

    async def run():
        await server._run_line(repl, bridge, "找回之前的工作卡", session.paths)
    asyncio.run(run())

    assistant = next(event for event in events if event.get("type") == "assistant.done")
    present = next(event for event in events if event.get("type") == "work-card.present")
    assert "Synthetic résumé requirement" in assistant["text"]
    assert present["session_id"] == session.conversation_id
    assert present["card_id"] == selected
    assert set(present["card"]) == {"card_id", "snapshot", "saved_at", "viewed_at"}
    assert present["card"]["card_id"] == selected
    assert "request_id" not in present["card"] and "snapshot_sha256" not in present["card"]
    assert "run_id" not in present
    assert present["presentation_id"]
    assert not any(
        event.get("type") == "turn.notice" and "待发布标记清理" in event.get("text", "")
        for event in events
    )
    history = session_history_event(Session.load(session.paths, session.conversation_id))["items"]
    reference = next(item for item in history if item.get("type") == "work_card_reference")
    assert reference["card_id"] == selected
    assert reference["presentation_id"] == present["presentation_id"]
    assert "run_id" not in reference
    stored_reference = next(message for message in session.messages
                            if message.get("role") == "work_card_presentation")
    run_event = next(event for event in events if event.get("type") == "execution.state")
    assert uuid.UUID(stored_reference["run_id"]).hex == run_event["run_id"]
    assert present["presentation_id"] == server._work_card_presentation_id(
        session.conversation_id, stored_reference["run_id"], selected,
    )
    presentation = WorkCardPresentations(session, store, ledger).get(present["presentation_id"])
    assert presentation.status == "found" and presentation.fact.acknowledged_at is None
    assert ledger.get_card(selected)["viewed_at"] is None

    # A client cannot request the server-only publish event.
    events.clear()
    asyncio.run(server.WsSessionHandler(session.paths)._handle_incoming({
        "type": "work-card.present", "session_id": session.conversation_id,
        "presentation_id": present["presentation_id"], "card_id": selected,
        "card": present["card"],
    }, repl, bridge))
    assert not any(event.get("type") == "work-card.present" for event in events)
    assert sum(message.get("role") == "work_card_presentation" for message in session.messages) == 1

    # A restored Session replays the durable pending reference with the same identity.
    restored = Session.load(session.paths, session.conversation_id)
    restored_repl = ConversationRepl.from_session(restored, paths=session.paths)
    restored_events = []
    restored_bridge = server.WsBridge(emit=restored_events.append, paths=session.paths)
    assert server._emit_pending_work_card_references(restored_repl, restored_bridge) == 1
    replay = next(event for event in restored_events if event.get("type") == "work-card.present")
    assert replay["presentation_id"] == present["presentation_id"]
    assert replay["card"] == present["card"]

    # The frontend's follow-up request uses the existing get protocol and public shape.
    asyncio.run(server.WsSessionHandler(session.paths)._handle_incoming(
        {"type": "work-card.get", "card_id": selected}, restored_repl, restored_bridge,
    ))
    fetched = next(event for event in restored_events if event.get("type") == "work-card.get.done")
    assert fetched["card"] == present["card"]



def test_uncertain_presentation_keeps_full_text_and_durable_retry_reference(tmp_path, monkeypatch):
    import asyncio
    from main import ConversationRepl
    from session import Session, session_history_event
    from work_card_presentations import PresentationResult, WorkCardPresentations

    agent, session, _store, ledger, selected = setup_recall_case(tmp_path, monkeypatch)
    install_client(monkeypatch, [selected], [])
    monkeypatch.setattr(server, "_WORK_CARD_REFERENCE_PUBLICATION_ENABLED", True)
    monkeypatch.setattr(WorkCardPresentations, "create", lambda *_a, **_k: PresentationResult("uncertain"))
    events = []
    bridge = server.WsBridge(emit=events.append, paths=session.paths)
    repl = ConversationRepl(
        paths=session.paths, session=session, agent=agent,
        input_fn=lambda _prompt: "n", output_fn=lambda _text: None,
        assistant_output_fn=bridge.emit_assistant,
    )
    server._bind_work_card_reference_intent(repl, bridge)

    async def run():
        await server._run_line(repl, bridge, "找回之前的工作卡", session.paths)
    asyncio.run(run())

    reply = next(event for event in events if event.get("type") == "assistant.done")
    assert "Synthetic résumé requirement" in reply["text"]
    assert any(event.get("type") == "turn.notice" and "无法确认" in event.get("text", "")
               and "完整文字回查结果已保留" in event.get("text", "") for event in events)
    assert not any(event.get("type") == "work-card.present" for event in events)
    loaded = Session.load(session.paths, session.conversation_id)
    reference = next(item for item in session_history_event(loaded)["items"]
                     if item.get("type") == "work_card_reference")
    assert reference["card_id"] == selected
    assert "不表示已送达" in reference["text"]
    assert ledger.get_card(selected)["viewed_at"] is None



def test_bridge_enqueue_error_preserves_reply_and_replays_same_reference(tmp_path, monkeypatch):
    import asyncio
    from main import ConversationRepl
    from session import Session, session_history_event

    agent, session, _store, ledger, selected = setup_recall_case(tmp_path, monkeypatch)
    install_client(monkeypatch, [selected], [])
    monkeypatch.setattr(server, "_WORK_CARD_REFERENCE_PUBLICATION_ENABLED", True)
    events = []
    def emit(event):
        if event.get("type") == "work-card.present":
            raise OSError("private socket failure")
        events.append(event)
    bridge = server.WsBridge(emit=emit, paths=session.paths)
    repl = ConversationRepl(
        paths=session.paths, session=session, agent=agent,
        input_fn=lambda _prompt: "n", output_fn=lambda _text: None,
        assistant_output_fn=bridge.emit_assistant,
    )
    server._bind_work_card_reference_intent(repl, bridge)

    async def run():
        await server._run_line(repl, bridge, "找回之前的工作卡", session.paths)
    asyncio.run(run())

    reply = next(event for event in events if event.get("type") == "assistant.done")
    assert "Synthetic résumé requirement" in reply["text"]
    assert any(event.get("type") == "turn.notice" and "无法确认" in event.get("text", "")
               and "完整文字回查结果已保留" in event.get("text", "") for event in events)
    loaded = Session.load(session.paths, session.conversation_id)
    reference = next(item for item in session_history_event(loaded)["items"]
                     if item.get("type") == "work_card_reference")
    assert reference["card_id"] == selected
    assert ledger.get_card(selected)["viewed_at"] is None

    restored_repl = ConversationRepl.from_session(loaded, paths=session.paths)
    replayed = []
    restored_bridge = server.WsBridge(emit=replayed.append, paths=session.paths)
    before_positions = [(row.get("presentation_id"), row.get("card_id")) for row in loaded.messages
                        if row.get("role") == "work_card_presentation"]
    assert server._emit_pending_work_card_references(restored_repl, restored_bridge) == 1
    event = next(row for row in replayed if row.get("type") == "work-card.present")
    assert event["presentation_id"] == reference["presentation_id"]
    after_positions = [(row.get("presentation_id"), row.get("card_id")) for row in loaded.messages
                       if row.get("role") == "work_card_presentation"]
    assert after_positions == before_positions



def test_old_999_references_remain_in_history_without_automatic_replay(tmp_path, monkeypatch):
    import asyncio
    from session import Session, session_history_event
    from main import ConversationRepl

    # Exercise the same 999-reference history window with two independently
    # addressable cards, including one whose earliest reference is outside it.
    agent, session, _store, ledger, card_id = setup_recall_case(tmp_path, monkeypatch, count=2)
    other_card_id = next(card["card_id"] for card in ledger.list_cards() if card["card_id"] != card_id)
    monkeypatch.setenv("MY_AGENT_SESSION_HISTORY_MAX_ITEMS", "200")
    references = []
    for index in range(999):
        run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"restore-window-run:{index}"))
        current_card = card_id if index < 799 else other_card_id
        presentation_id = server._work_card_presentation_id(session.conversation_id, run_id, current_card)
        references.append({
            "role": "work_card_presentation", "_work_card_reference": 1,
            "presentation_id": presentation_id, "card_id": current_card, "run_id": run_id,
        })
    session.messages.extend(references)
    session.save()

    # Reopen from disk as Desktop does, rather than checking only in-memory rows.
    restored = Session.load(session.paths, session.conversation_id, message_cap=0)
    history = session_history_event(restored)
    visible = [item for item in history["items"] if item.get("type") == "work_card_reference"]
    assert history["session_id"] == session.conversation_id
    assert history["total_items"] == 999 and history["truncated"] is True
    assert history["omitted_count"] == 799 and len(visible) == 200
    assert [(item["presentation_id"], item["card_id"]) for item in visible] == [
        (row["presentation_id"], row["card_id"]) for row in references[-200:]
    ]
    assert all("run_id" not in item for item in visible)
    restored.ensure_messages_loaded()
    assert restored.messages == references
    assert len({row["presentation_id"] for row in restored.messages}) == 999

    # A reference without a receipt-tracked presentation is not evidence of
    # non-delivery: neither the window nor all older rows should be pushed.
    events = []
    bridge = server.WsBridge(emit=events.append, paths=restored.paths)
    repl = ConversationRepl.from_session(restored, paths=restored.paths)
    assert server._emit_pending_work_card_references(repl, bridge) == 0
    assert not any(event.get("type") == "work-card.present" for event in events)
    assert restored.messages == references
    reread = Session.load(session.paths, session.conversation_id, message_cap=0)
    assert session_history_event(reread) == history

    # Both a card whose references fell outside the window and a visible one
    # remain available through the normal current-session read protocol.
    handler = server.WsSessionHandler(restored.paths)
    for requested_id in (card_id, other_card_id):
        asyncio.run(handler._handle_incoming({"type": "work-card.get", "card_id": requested_id}, repl, bridge))
        result = next(event for event in reversed(events) if event.get("type") == "work-card.get.done")
        assert result["status"] == "found" and result["card"]["card_id"] == requested_id
        assert result["card"]["snapshot"] == ledger.get_card(requested_id)["snapshot"]
        assert result["card"]["viewed_at"] is None
    assert not any(event.get("type") == "work-card.present" for event in events)
    assert restored.messages == references

    # A normal, explicitly requested Agent recall can find the older card
    # without knowing its card ID; the synthetic matcher is locally stubbed.
    calls = []
    install_client(monkeypatch, [card_id], calls)
    recall = agent.run_turn("找回之前的工作卡", spawn_explore=False)
    assert recall.finish_reason == "work_card_recall" and len(calls) == 1
    assert "Synthetic résumé requirement" in recall.assistant_text
    assert ledger.get_card(card_id)["viewed_at"] is None
    assert not any(event.get("type") == "work-card.present" for event in events)


def test_loopback_agent_recall_resends_only_requested_project_preserving_history(tmp_path, monkeypatch):
    """Real loopback WS and Agent, with an explicitly stubbed local matcher."""
    import asyncio
    import json
    import os
    from datetime import datetime
    from types import SimpleNamespace

    import websockets
    from websockets.asyncio.server import serve
    from agent import Agent
    from llm_client import LLMClient
    from main import ConversationRepl
    from test_work_card_project_scope import _add_task, _source_fixture
    from work_card_ledger import WorkCardLedger
    from work_card_presentations import WorkCardPresentations
    from work_card_project_scope import build_project_scoped_work_record_cards
    import work_records_luna_recall as luna

    session, store, item_id, _, _ = _source_fixture(tmp_path)
    root = session.paths.agent_root
    for directory in (session.paths.workspace, session.paths.data, session.paths.evolve):
        directory.mkdir(parents=True, exist_ok=True)
    (session.paths.evolve / '_index.core.toml').write_text('', encoding='utf-8')
    for name in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'MY_AGENT_ROOT'):
        monkeypatch.setenv(name, str(root))
    for name in tuple(os.environ):
        if any(marker in name.upper() for marker in ('API_KEY', 'TOKEN', 'SECRET', 'CREDENTIAL')):
            monkeypatch.delenv(name, raising=False)
    _add_task(store, item_id, 'synthetic-alpha', 'Alpha second requirement', 'alpha-second')
    session.save()
    ledger = WorkCardLedger(session, store)
    cards = {}
    for project in ('synthetic-alpha', 'synthetic-beta'):
        snapshot = build_project_scoped_work_record_cards(store, project)
        assert snapshot.completeness == 'complete'
        saved = ledger.save_project_scoped_snapshot(str(uuid.uuid4()), snapshot)
        assert saved.status == 'saved'
        cards[project] = saved.card_id

    # Both historical presentations were received before this connection; only B
    # was explicitly viewed. They must not be auto-replayed or changed by recall.
    presentations = WorkCardPresentations(session, store, ledger)
    old_ids = {}
    for project, card_id in cards.items():
        run_id = str(uuid.uuid4())
        presentation_id = server._work_card_presentation_id(session.conversation_id, run_id, card_id)
        assert presentations.create(presentation_id, card_id, run_id=run_id).status == 'created'
        session.append_sent_work_card_reference(presentation_id, card_id, run_id=run_id)
        assert presentations.mark_transport_offer(presentation_id, card_id).status == 'offered'
        assert presentations.mark_received(presentation_id, card_id, session.conversation_id, run_id).status == 'received'
        old_ids[project] = presentation_id
    assert presentations.acknowledge(old_ids['synthetic-beta'], cards['synthetic-beta'], explicit_user_action=True).status == 'acknowledged'
    assert ledger.acknowledge_viewed(cards['synthetic-beta']).status == 'acknowledged'
    original_cards = {project: ledger.get_card(card_id) for project, card_id in cards.items()}
    original_presentations = {project: presentations.get(pid).fact for project, pid in old_ids.items()}
    assert original_cards['synthetic-alpha']['viewed_at'] is None
    assert original_cards['synthetic-beta']['viewed_at'] is not None

    monkeypatch.setattr(luna, 'get_registry', lambda *_: SimpleNamespace(get=lambda _: SimpleNamespace(
        id='0x567-flash', vendor='0x567', provider_model='gpt-6-luna', max_input_tokens=1_000_000,
        chat_completions_url=lambda: 'https://example.invalid/v1/chat/completions')))
    client = LLMClient(paths=session.paths)
    agent = Agent.create(session, llm=client, confirm_fn=lambda *_: 'y', enable_work_record_recall=True)
    agent.work_record_recall_controller = SimpleNamespace(_store=store, _semantic_matcher=SimpleNamespace(_client=client))
    calls = []
    install_client(monkeypatch, [cards['synthetic-alpha']], calls)
    monkeypatch.setattr(server, 'resume_desktop_or_create', lambda paths: session)

    def build_repl(bound_session, paths, bridge, **_kwargs):
        assert bound_session is session and paths is session.paths
        repl = ConversationRepl(paths=paths, session=session, agent=agent,
            input_fn=lambda _prompt: 'n', output_fn=lambda _text: None, assistant_output_fn=bridge.emit_assistant)
        server._bind_work_card_reference_intent(repl, bridge)
        return repl

    monkeypatch.setattr(server, '_build_repl', build_repl)
    query = '\u627e\u56de\u4e4b\u524d\u5173\u4e8esynthetic-alpha\u7684\u5de5\u4f5c\u5361'

    async def exercise():
        handler = server.WsSessionHandler(session.paths)
        async with serve(handler.handle, '127.0.0.1', 0) as listener:
            port = listener.sockets[0].getsockname()[1]
            async with websockets.connect(f'ws://127.0.0.1:{port}', open_timeout=5, close_timeout=5) as ws:
                events = []
                while True:
                    event = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    events.append(event)
                    if event.get('type') == 'session.banner':
                        assert event['session_id'] == session.conversation_id
                        break
                await ws.send(json.dumps({'type': 'user.message', 'text': query}, ensure_ascii=False))
                while True:
                    event = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                    events.append(event)
                    if event.get('type') == 'turn.end':
                        assert event.get('ok') is True
                        break
                return events

    events = asyncio.run(exercise())
    offered = [event for event in events if event.get('type') == 'work-card.present']
    assert len(offered) == 1 and offered[0]['card_id'] == cards['synthetic-alpha'], (len(calls), [(e.get('type'), e.get('text'), e.get('message'), e.get('finish_reason')) for e in events if e.get('type') in ('assistant.done', 'turn.notice', 'turn.end', 'error')])
    event = offered[0]
    original_a = original_cards['synthetic-alpha']
    assert event['card'] == {key: original_a[key] for key in ('card_id', 'snapshot', 'saved_at', 'viewed_at')}
    snapshot = event['card']['snapshot']
    assert snapshot['kind'] == 'project_scoped' and snapshot['project_id'] == 'synthetic-alpha'
    assert snapshot['completeness'] == 'complete'
    assert {req['text'] for card in snapshot['cards'] for req in card['requirements']} == {
        'Alpha requirement', 'Alpha second requirement'}
    assert all(req['project_id'] == 'synthetic-alpha' for card in snapshot['cards'] for req in card['requirements'])
    assert event['card']['viewed_at'] is None
    assert len(calls) == 1 and query in json.dumps(calls[0][0], ensure_ascii=False)
    candidates = json.loads(calls[0][0][1]['content'])['candidates']
    assert {candidate['id']: json.loads(candidate['excerpt'])['project_id'] for candidate in candidates} == {
        cards[project]: project for project in cards}
    assert {project: ledger.get_card(card_id) for project, card_id in cards.items()} == original_cards
    assert {project: presentations.get(pid).fact for project, pid in old_ids.items()} == original_presentations
    new = presentations.get(event['presentation_id']).fact
    assert new is not None and event['presentation_id'] not in old_ids.values()
    assert new.card_id == cards['synthetic-alpha'] and new.acknowledged_at is None and new.result_views == ()
    assert new.received_at is None  # WS delivery alone is neither receipt nor a user view.
    assert datetime.fromisoformat(new.created_at.replace('Z', '+00:00')).tzinfo is not None
