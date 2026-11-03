from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_work_records_overview_api import _session
from work_records_overview_api import query_work_records_overview


def test_bounded_group_and_requirement_pages_cover_everything_and_prioritize_blocked(tmp_path):
    session, store = _session(tmp_path)
    for i in range(13):
        item=store.create_item(title=f'item-{i:02d}', scope={'kind':'project','project_id':'project-api'})
        for n in range(8 if i == 0 else 1):
            req=store.add_requirement(item['item_id'],f'requirement-{i}-{n}',event_id=f'e-{i}-{n}')
            if i == 0 and n == 7:
                store.begin_requirement_run(item['item_id'],req['requirement_id'],run_id='blocked-run',expected_current_run_id=None)
                store.record_result_if_current_run(item['item_id'],req['requirement_id'],run_id='blocked-run',result_id='blocked-result',status='blocked',summary='blocked',verification={'status':'not_verified'})
    first=query_work_records_overview(session, detail_paging=True)
    group=first['groups'][0]
    assert len(group['items']) <= 5 and group['items_page']['total'] == 13
    item=next(x for x in group['items'] if x['title']=='item-00')
    assert len(item['requirements']) <= 5 and item['requirements_page']['total']==8
    assert item['requirements'][0]['status']=='blocked' and group['status_counts']['blocked']==1
    assert item['current_results'][0]['requirement_id'] == item['requirements'][0]['requirement_id']
    assert first['detail_page'] is None
    rows=list(group['items']); cursor=group['items_page']['next_cursor']
    while cursor:
        page=query_work_records_overview(session,cursor=cursor,detail_paging=True)
        assert page['snapshot_id']==first['snapshot_id'] and page['detail_page']['kind']=='items'
        rows.extend(page['groups'][0]['items']); cursor=page['detail_page']['next_cursor']
    assert len(rows)==13 and len({x['item_id'] for x in rows})==13
    requirements=list(item['requirements']); cursor=item['requirements_page']['next_cursor']
    while cursor:
        page=query_work_records_overview(session,cursor=cursor,detail_paging=True)
        assert page['detail_page']['kind']=='requirements'
        requirements.extend(page['groups'][0]['items'][0]['requirements']); cursor=page['detail_page']['next_cursor']
    assert len(requirements)==8 and len({r['requirement_id'] for r in requirements})==8


def test_detail_cursor_expired_after_snapshot_change_or_wrong_session(tmp_path):
    session,store=_session(tmp_path)
    item=store.create_item(title='big',scope={'kind':'independent'})
    for i in range(7): store.add_requirement(item['item_id'],f'req-{i}',event_id=f'req-{i}')
    first=query_work_records_overview(session, detail_paging=True)
    cursor=first['groups'][0]['items'][0]['requirements_page']['next_cursor']
    assert query_work_records_overview(session, cursor=cursor)["reason"] == "detail_paging_required"
    other,_=_session(tmp_path,'20261030-a1abcd02')
    assert query_work_records_overview(other,cursor=cursor,detail_paging=True)['state']=='expired'
    store.add_requirement(item['item_id'],'changed',event_id='changed')
    assert query_work_records_overview(session,cursor=cursor,detail_paging=True)['state']=='expired'


def test_invalid_detail_cursor_cannot_select_another_or_invalid_page(tmp_path):
    import base64
    import json
    session, store = _session(tmp_path)
    item = store.create_item(title="many", scope={"kind": "independent"})
    for i in range(6):
        store.add_requirement(item["item_id"], f"req-{i}", event_id=f"req-{i}")
    first = query_work_records_overview(session, detail_paging=True)
    cursor = first["groups"][0]["items"][0]["requirements_page"]["next_cursor"]
    value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    for key, invalid in (("item_id", "unrelated"), ("offset", 1), ("offset", True)):
        altered = dict(value, **{key: invalid})
        encoded = base64.urlsafe_b64encode(json.dumps(altered).encode()).decode().rstrip("=")
        result = query_work_records_overview(session, cursor=encoded, detail_paging=True)
        assert result["state"] == "error" and result["groups"] == []


def test_legacy_default_preserves_all_nested_rows(tmp_path):
    session, store = _session(tmp_path)
    for i in range(7):
        item = store.create_item(title=f"item-{i}", scope={"kind": "project", "project_id": "project-api"})
        for n in range(7):
            store.add_requirement(item["item_id"], f"req-{i}-{n}", event_id=f"e-{i}-{n}")
    group = query_work_records_overview(session)["groups"][0]
    assert len(group["items"]) == 7
    assert all(len(row["requirements"]) == 7 for row in group["items"])
    assert "items_page" not in group


def test_real_handler_capability_and_legacy_remains_complete(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from test_work_records_overview_server_protocol import _request, _response
    from server import WsBridge, WsSessionHandler
    async def run():
        session, store = _session(tmp_path)
        for i in range(6):
            item = store.create_item(title=f"item-{i}", scope={"kind": "project", "project_id": "project-api"})
            for n in range(6):
                store.add_requirement(item["item_id"], f"req-{i}-{n}", event_id=f"e-{i}-{n}")
        events = []
        bridge = WsBridge(emit=events.append, paths=session.paths)
        handler = WsSessionHandler(session.paths)
        repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
        request = _request(session)
        await handler._handle_incoming(request, repl, bridge)
        result = _response(events, request["request_id"])
        assert len(result["groups"][0]["items"]) == 6
        assert all(len(row["requirements"]) == 6 for row in result["groups"][0]["items"])
        capability = _request(session, detail_paging=True)
        await handler._handle_incoming(capability, repl, bridge)
        first = _response(events, capability["request_id"])
        assert first["state"] == "visible"
        group = first["groups"][0]
        assert len(group["items"]) == 5 and group["items_page"]["total"] == 6
        item = group["items"][0]
        assert len(item["requirements"]) == 5 and item["requirements_page"]["total"] == 6
        for kind, cursor in (("items", group["items_page"]["next_cursor"]),
                             ("requirements", item["requirements_page"]["next_cursor"])):
            request = _request(session, cursor=cursor, detail_paging=True)
            await handler._handle_incoming(request, repl, bridge)
            page = _response(events, request["request_id"])
            assert page["state"] == "visible" and page["detail_page"]["kind"] == kind
            assert page["snapshot_id"] == first["snapshot_id"]
            missing = _request(session, cursor=cursor)
            await handler._handle_incoming(missing, repl, bridge)
            assert _response(events, missing["request_id"])["reason"] == "detail_paging_required"
        cursor = group["items_page"]["next_cursor"]
        other, _ = _session(tmp_path, "20261030-a1abcd02")
        other_repl = SimpleNamespace(session=other, agent=SimpleNamespace(session=other))
        foreign_request = _request(other, cursor=cursor, detail_paging=True)
        await handler._handle_incoming(foreign_request, other_repl, bridge)
        assert _response(events, foreign_request["request_id"])["state"] == "expired"
        store.add_requirement(item["item_id"], "changed", event_id="change")
        stale = _request(session, cursor=cursor, detail_paging=True)
        await handler._handle_incoming(stale, repl, bridge)
        assert _response(events, stale["request_id"])["state"] == "expired"
        for value in (None, 1, "true", [], {}):
            bad = _request(session, detail_paging=value)
            await handler._handle_incoming(bad, repl, bridge)
            assert _response(events, bad["request_id"])["reason"] == "invalid_request"
        oversized = _request(session, cursor="x" * 4097, detail_paging=True)
        await handler._handle_incoming(oversized, repl, bridge)
        assert _response(events, oversized["request_id"])["reason"] == "invalid_request"
        legacy_false = _request(session, detail_paging=False)
        await handler._handle_incoming(legacy_false, repl, bridge)
        assert len(_response(events, legacy_false["request_id"])["groups"][0]["items"]) == 6
    asyncio.run(run())
